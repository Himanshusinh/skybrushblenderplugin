"""Guards the acceleration profile of the takeoff maneuver and the safety check
that reports it.

A constant-velocity climb reaches and leaves its cruise velocity within a single
frame, which shows up as an acceleration of tens of m/s/s -- something no drone
can fly. These tests pin down both that the takeoff velocity profile keeps the
acceleration bounded, and that the safety check reports the acceleration
faithfully instead of inventing spikes of its own.
"""

import pytest
from sbstudio.math.derivatives import estimate_second_derivative
from sbstudio.math.transition_timing import (
    MIN_JERK_PEAK_ACCELERATION,
    MIN_JERK_PEAK_VELOCITY,
    minimum_jerk,
)
from sbstudio.utils import LRUCache

FPS = 25
"""Frame rate of the fake scene. Chosen so that one frame is exactly 40 ms."""

MAX_STEP = 2
"""Same frame spacing limit as the one the safety check task asks for."""

CACHE_CAPACITY = 9
"""Same capacity as the position cache of the safety check task."""


def _snapshot(altitude: float):
    return {"drone": (0.0, 0.0, altitude)}


def _cache(altitude_at, frames):
    """Fills a position cache by visiting the given frames in the given order."""
    cache = LRUCache(CACHE_CAPACITY)
    for frame in frames:
        cache[frame] = _snapshot(altitude_at(frame))
    return cache


def _vertical_acceleration(altitude_at, frame: int, *, visited=None):
    """Runs the estimator the way the safety check task does."""
    visited = range(frame - 4, frame + 2) if visited is None else visited
    accelerations, known = estimate_second_derivative(
        _snapshot(altitude_at(frame)),
        _cache(altitude_at, visited),
        index=frame,
        step_size=1.0 / FPS,
        max_step=MAX_STEP,
    )
    return accelerations["drone"][2], known


def _climb_then_hold(frame: int) -> float:
    """A constant-velocity climb of 0.1 m per frame that stops at frame 30."""
    return 0.1 * min(frame, 30)


def test_constant_climb_reports_no_acceleration():
    acceleration, known = _vertical_acceleration(_climb_then_hold, 20)

    assert known
    assert acceleration == pytest.approx(0.0, abs=1e-9)


def test_uniform_acceleration_is_recovered():
    expected = 3.0

    def altitude_at(frame: int) -> float:
        return 0.5 * expected * (frame / FPS) ** 2

    acceleration, known = _vertical_acceleration(altitude_at, 28)

    assert known
    assert acceleration == pytest.approx(expected, rel=1e-9)


def test_velocity_step_is_reported_as_a_large_acceleration():
    # The climb loses 0.1 m/frame, i.e. 2.5 m/s, within a single frame
    acceleration, known = _vertical_acceleration(_climb_then_hold, 30)

    assert known
    assert acceleration == pytest.approx(-2.5 * FPS, rel=1e-9)


def test_result_does_not_depend_on_the_order_the_frames_were_visited():
    """The estimate must describe the trajectory, not the user's scrubbing.

    Deriving accelerations from cached velocity estimates used to make the
    answer depend on which frames happened to be in the cache and whether they
    sat before or after the current one.
    """
    forwards = [26, 27, 28, 29, 30, 31]
    backwards = list(reversed(forwards))
    shuffled = [31, 27, 30, 26, 29, 28]

    results = [
        _vertical_acceleration(_climb_then_hold, 30, visited=order)
        for order in (forwards, backwards, shuffled)
    ]

    assert len(set(results)) == 1


def test_smooth_trajectory_never_reports_a_spike_while_stepping_through_it():
    """No frame of a smooth climb may look like a violent maneuver."""
    height, seconds = 6.0, 4.0
    duration = int(seconds * FPS)

    def altitude_at(frame: int) -> float:
        return height * minimum_jerk(_clamp(frame / duration))

    bound = SMOOTH_PEAK_ACCELERATION_FACTOR * height / seconds**2
    for frame in range(6, duration + 20):
        acceleration, known = _vertical_acceleration(altitude_at, frame)
        assert known
        assert abs(acceleration) <= bound * 1.01, f"spike at frame {frame}"


def test_missing_neighbouring_frames_are_reported_as_unknown():
    """An unknown acceleration must not masquerade as a zero one."""
    cache = LRUCache(CACHE_CAPACITY)
    cache[100] = _snapshot(5.0)

    accelerations, known = estimate_second_derivative(
        _snapshot(5.0), cache, index=500, step_size=1.0 / FPS, max_step=MAX_STEP
    )

    assert not known
    assert accelerations["drone"] == (0.0, 0.0, 0.0)


#############################################################################
# The velocity profile of the takeoff itself


SMOOTH_PEAK_VELOCITY_FACTOR = MIN_JERK_PEAK_VELOCITY
SMOOTH_PEAK_ACCELERATION_FACTOR = MIN_JERK_PEAK_ACCELERATION
"""Copies of the factors that ``takeoff.py`` uses to predict the peaks of a
smooth maneuver. ``test_smooth_transition_peaks_match_the_predicted_factors``
checks them against the minimum-jerk polynomial the influence curve tracks.
"""


def _clamp(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def _influence(profile: str, x: float) -> float:
    return x if profile == "LINEAR" else minimum_jerk(x)


def test_smooth_transition_peaks_match_the_predicted_factors():
    dx = 1e-5
    samples = [i / 1000 for i in range(1001)]
    slopes = [(minimum_jerk(x + dx) - minimum_jerk(x - dx)) / (2 * dx) for x in samples]
    curvatures = [
        (minimum_jerk(x + dx) - 2 * minimum_jerk(x) + minimum_jerk(x - dx)) / dx**2
        for x in samples
    ]

    assert max(slopes) == pytest.approx(SMOOTH_PEAK_VELOCITY_FACTOR, rel=1e-4)
    assert max(abs(c) for c in curvatures) == pytest.approx(
        SMOOTH_PEAK_ACCELERATION_FACTOR, rel=1e-3
    )
    # The whole point of the quintic: acceleration is zero at both ends, so a
    # drone that was hovering does not jump to its peak acceleration in one
    # frame the way a cubic smoothstep would.
    assert curvatures[0] == pytest.approx(0.0, abs=1e-3)
    assert curvatures[-1] == pytest.approx(0.0, abs=1e-3)


@pytest.mark.parametrize("profile", ["LINEAR", "SMOOTH"])
def test_takeoff_profile_decides_whether_the_acceleration_is_flyable(profile):
    """A takeoff climbing 6 m at 1.5 m/s average, as the defaults ask for."""
    height, average_velocity = 6.0, 1.5
    seconds = height / average_velocity
    duration = int(seconds * FPS)

    def altitude_at(frame: int) -> float:
        return height * _influence(profile, _clamp(frame / duration))

    peak = max(
        abs(_vertical_acceleration(altitude_at, frame)[0])
        for frame in range(6, duration + 10)
    )

    smooth_bound = SMOOTH_PEAK_ACCELERATION_FACTOR * height / seconds**2
    if profile == "LINEAR":
        # Reaching cruise velocity within one frame; the shorter the frame, the
        # worse it gets, which is why this cannot be flown at all
        assert peak == pytest.approx(average_velocity * FPS, rel=1e-9)
        assert peak > 10 * smooth_bound
    else:
        assert peak <= smooth_bound * 1.01
