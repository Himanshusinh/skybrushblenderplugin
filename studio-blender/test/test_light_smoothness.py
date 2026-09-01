"""Guards the fidelity of exported light programs.

A smooth colour animation in Blender must stay smooth once it has been
simplified and encoded as ``.skyc`` bytecode. These tests pin down the error
that the export pipeline is allowed to introduce.
"""

import math
import sys
from unittest.mock import MagicMock

import pytest

# Blender-only dependency pulled in by model.color
sys.modules.setdefault("mathutils", MagicMock())

from sbstudio.model.color import Color4D
from sbstudio.model.light_program import LightProgram
from sbstudio.model.skyc import encode_light_program_bytecode

DURATION = 12.0


def _reference(t: float) -> tuple[int, int, int]:
    """A hue sweep modulated by a slow sinusoidal brightness pulse."""
    hue = (t / DURATION) % 1.0
    brightness = 0.5 - 0.5 * math.cos(2 * math.pi * t / 3.0)
    sector = int(hue * 6) % 6
    f = hue * 6 - int(hue * 6)
    base = [
        (1, f, 0),
        (1 - f, 1, 0),
        (0, 1, f),
        (0, 1 - f, 1),
        (f, 0, 1),
        (1, 0, 1 - f),
    ][sector]
    return tuple(int(round(c * brightness * 255)) for c in base)


def _sample(fps: int) -> LightProgram:
    program = LightProgram()
    for k in range(int(DURATION * fps) + 1):
        t = k / fps
        program.append(Color4D(t, *_reference(t)))
    return program


def _read_varuint(buf: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return value, i
        shift += 7


def _decode(data: bytes) -> list[tuple[float, tuple[int, int, int], bool]]:
    """Turns bytecode back into the keypoints a player would interpolate."""
    points: list[tuple[float, tuple[int, int, int], bool]] = []
    i = 0
    t = 0.0
    current = (0, 0, 0)
    while i < len(data):
        op = data[i]
        i += 1
        if op == 0x00:  # END
            break
        if op == 0x02:  # SLEEP
            duration, i = _read_varuint(data, i)
            t += duration / 50.0
            points.append((t, current, False))
        elif op in (0x04, 0x08):  # SET_COLOR / FADE_TO_COLOR
            current = (data[i], data[i + 1], data[i + 2])
            i += 3
            duration, i = _read_varuint(data, i)
            t += duration / 50.0
            points.append((t, current, op == 0x08))
        else:
            raise AssertionError(f"unexpected opcode 0x{op:02x}")
    return points


def _playback(points, t: float) -> tuple[int, int, int]:
    """Emulates the player: fades interpolate, everything else holds."""
    if t <= points[0][0]:
        return points[0][1]
    for (t0, c0, _), (t1, c1, is_fade) in zip(points, points[1:]):
        if t0 <= t <= t1:
            if not is_fade or t1 <= t0:
                return c1 if t >= t1 else c0
            ratio = (t - t0) / (t1 - t0)
            return tuple(round(c0[k] + ratio * (c1[k] - c0[k])) for k in range(3))
    return points[-1][1]


def _interpolate(program: LightProgram, t: float) -> tuple[int, int, int]:
    """Linear interpolation of the raw samples handed to the exporter."""
    colors = program.colors
    if t <= colors[0].t:
        c = colors[0]
        return (c.r, c.g, c.b)
    for a, b in zip(colors, colors[1:]):
        if a.t <= t <= b.t:
            ratio = (t - a.t) / (b.t - a.t) if b.t > a.t else 0.0
            return (
                round(a.r + ratio * (b.r - a.r)),
                round(a.g + ratio * (b.g - a.g)),
                round(a.b + ratio * (b.b - a.b)),
            )
    c = colors[-1]
    return (c.r, c.g, c.b)


def _worst_error(fps: int, eps: float, *, against_samples: bool = False) -> float:
    """Largest per-channel difference between playback and the intended colour.

    With ``against_samples`` the baseline is the sampled polyline, which is what
    ``eps`` actually bounds; otherwise it is the underlying continuous animation,
    which additionally includes the error of sampling at ``fps``.
    """
    sampled = _sample(fps)
    program = sampled.simplify(eps=eps)
    points = _decode(encode_light_program_bytecode(program, end_time=DURATION))

    worst = 0.0
    for k in range(int(DURATION * 100)):
        t = k / 100
        expected = _interpolate(sampled, t) if against_samples else _reference(t)
        actual = _playback(points, t)
        worst = max(worst, max(abs(expected[j] - actual[j]) for j in range(3)))
    return worst


@pytest.mark.parametrize("eps", [2, 4, 8, 16])
def test_error_stays_within_requested_tolerance(eps: float):
    """The encoder must not lose more colour accuracy than ``eps`` allows.

    Sampled at 25 FPS so that every keypoint lands exactly on a 1/50 s bytecode
    tick; the single extra unit of slack is rounding to integer channels.
    """
    assert _worst_error(fps=25, eps=eps, against_samples=True) <= eps + 1


def test_tick_quantisation_cost_is_bounded():
    """Rates that do not divide 50 Hz shift keypoints by up to half a tick.

    That is inherent to the format, but it must stay small rather than compound.
    """
    aligned = _worst_error(fps=25, eps=4, against_samples=True)
    unaligned = _worst_error(fps=24, eps=4, against_samples=True)
    assert unaligned - aligned <= 4, (aligned, unaligned)


def test_default_tolerance_is_visually_smooth():
    """End to end, including sampling error, the default must stay unobtrusive."""
    assert _worst_error(fps=24, eps=4) <= 6


def test_finer_tolerance_never_degrades_quality():
    errors = [_worst_error(fps=24, eps=eps) for eps in (16, 8, 4, 2)]
    assert errors == sorted(errors, reverse=True), errors


def test_higher_sampling_rate_improves_fidelity():
    """Raising the light FPS must actually buy accuracy.

    It did not while the encoder pruned keypoints with its own heuristic.
    """
    coarse = _worst_error(fps=4, eps=2)
    fine = _worst_error(fps=30, eps=2)
    assert fine < coarse, (coarse, fine)


def test_smooth_ramp_is_encoded_as_fades_not_steps():
    """A gradual ramp must not be flattened into a few abrupt jumps."""
    program = _sample(24).simplify(eps=4)
    data = encode_light_program_bytecode(program, end_time=DURATION)
    points = _decode(data)

    fades = sum(1 for _, _, is_fade in points if is_fade)
    assert fades >= 40, f"only {fades} fades; keypoints are being over-pruned"


def test_program_starting_late_is_not_played_early():
    """Colours must appear when Blender shows them, not shifted to t=0."""
    program = LightProgram(
        [Color4D(2.0, 255, 0, 0), Color4D(3.0, 0, 0, 255)]
    )
    points = _decode(encode_light_program_bytecode(program, end_time=5.0))

    # The initial colour is held until its own timestamp, so the fade towards
    # blue must still finish at t=3.
    assert points[0][0] == pytest.approx(2.0)
    assert points[1][0] == pytest.approx(3.0)
    assert points[1][1] == (0, 0, 255)


def test_timestamps_do_not_drift_on_long_programs():
    """Durations are deltas of absolute ticks, so they must not accumulate error."""
    program = LightProgram(
        [Color4D(i * 0.013, i % 256, 0, 255 - i % 256) for i in range(4000)]
    )
    end = program.colors[-1].t
    points = _decode(encode_light_program_bytecode(program, end_time=end))
    assert abs(points[-1][0] - end) <= 0.02
