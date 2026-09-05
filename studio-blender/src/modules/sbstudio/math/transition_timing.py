"""How long a straight-line transition must last, and how hard it accelerates.

A drone that interpolates between two points as ``p(t) = a + (b - a) * s(t/T)``
has velocity ``(b - a) / T * s'(τ)`` and acceleration ``(b - a) / T² * s''(τ)``.
The peak of each is therefore the peak of the unit curve, scaled by distance
and duration. These functions turn that relationship around: given the
distances the drones must cover and the velocity / acceleration they are
allowed, they return the shortest duration that stays inside those limits.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import hypot, inf, sqrt

from sbstudio.model.types import Coordinate3D

__all__ = (
    "CUBIC_SMOOTH_PEAK_ACCELERATION",
    "CUBIC_SMOOTH_PEAK_VELOCITY",
    "MIN_JERK_PEAK_ACCELERATION",
    "MIN_JERK_PEAK_VELOCITY",
    "displacements_for_assignment",
    "minimum_jerk",
    "minimum_jerk_derivative",
    "peak_acceleration_for_transition",
    "required_transition_duration",
    "wanted_transition_profile",
)


Displacement = tuple[float, float, float]


CUBIC_SMOOTH_PEAK_VELOCITY = 1.5
"""Peak of the first derivative of ``3τ² - 2τ³``."""

CUBIC_SMOOTH_PEAK_ACCELERATION = 6.0
"""Peak of the second derivative of ``3τ² - 2τ³``, reached at both ends."""

MIN_JERK_PEAK_VELOCITY = 1.875
"""Peak of the first derivative of the unit minimum-jerk polynomial, at τ=1/2."""

MIN_JERK_PEAK_ACCELERATION = 10.0 * sqrt(3.0) / 3.0
"""Peak of the second derivative of the unit minimum-jerk polynomial.

The second derivative is zero at both ends (the drones start and stop at rest
without an instantaneous jerk) and reaches this value near τ ≈ 0.21 and 0.79.
"""

_PROFILE_PEAK_FACTORS: dict[str, tuple[float, float | None]] = {
    # (peak |s'|, peak |s''|). A missing acceleration factor means the profile
    # reaches its cruise velocity in a single frame, so the acceleration is
    # limited only by the frame rate.
    "LINEAR": (1.0, None),
    "SMOOTH": (MIN_JERK_PEAK_VELOCITY, MIN_JERK_PEAK_ACCELERATION),
    "SMOOTH_FROM_LEFT": (CUBIC_SMOOTH_PEAK_VELOCITY, CUBIC_SMOOTH_PEAK_ACCELERATION),
    "SMOOTH_FROM_RIGHT": (CUBIC_SMOOTH_PEAK_VELOCITY, CUBIC_SMOOTH_PEAK_ACCELERATION),
}

_MINIMUM_DURATION = 1.0
"""Never plan a transition shorter than this, even for a tiny hop.

A one-frame hop is what produces the "0 to tens of m/s² in a microsecond"
readout: the whole velocity change is concentrated in a single sample.
"""


def minimum_jerk(tau: float) -> float:
    """Unit minimum-jerk polynomial: 0 at τ=0, 1 at τ=1, with zero first and
    second derivatives at both ends.
    """
    tau = min(max(tau, 0.0), 1.0)
    return ((6.0 * tau - 15.0) * tau + 10.0) * tau * tau * tau


def minimum_jerk_derivative(tau: float) -> float:
    """First derivative of :func:`minimum_jerk` with respect to τ."""
    tau = min(max(tau, 0.0), 1.0)
    rest = 1.0 - tau
    return 30.0 * tau * tau * rest * rest


def displacements_for_assignment(
    source: Sequence[Coordinate3D],
    target: Sequence[Coordinate3D],
    target_to_source: Sequence[int | None],
) -> list[Displacement]:
    """Converts an assignment of target indices to source indices into the
    displacement each matched pair has to fly.
    """
    result: list[Displacement] = []
    for target_index, source_index in enumerate(target_to_source):
        if source_index is None:
            continue
        if source_index >= len(source) or target_index >= len(target):
            continue
        sx, sy, sz = source[source_index]
        tx, ty, tz = target[target_index]
        result.append((tx - sx, ty - sy, tz - sz))
    return result


def _peak_factors(profile: str) -> tuple[float, float | None]:
    return _PROFILE_PEAK_FACTORS.get(profile, _PROFILE_PEAK_FACTORS["SMOOTH"])


def required_transition_duration(
    displacements: Sequence[Displacement],
    *,
    max_velocity_xy: float,
    max_velocity_z_up: float,
    max_velocity_z_down: float,
    max_acceleration: float,
    profile: str = "SMOOTH",
) -> float:
    """Shortest duration, in seconds, of a rest-to-rest transition that stays
    inside the given velocity and acceleration limits.

    Parameters:
        displacements: ``(dx, dy, dz)`` of each drone that takes part
        max_velocity_xy: allowed horizontal speed
        max_velocity_z_up: allowed climb rate
        max_velocity_z_down: allowed descent rate
        max_acceleration: allowed acceleration magnitude
        profile: storyboard velocity profile name

    Returns:
        duration in seconds, never shorter than one second
    """
    if not displacements:
        return _MINIMUM_DURATION

    v_factor, a_factor = _peak_factors(profile)

    max_xy = 0.0
    max_z_up = 0.0
    max_z_down = 0.0
    max_distance = 0.0
    for dx, dy, dz in displacements:
        max_xy = max(max_xy, hypot(dx, dy))
        max_distance = max(max_distance, hypot(dx, dy, dz))
        if dz > 0.0:
            max_z_up = max(max_z_up, dz)
        else:
            max_z_down = max(max_z_down, -dz)

    seconds = _MINIMUM_DURATION
    if max_velocity_xy > 0.0 and max_xy > 0.0:
        seconds = max(seconds, v_factor * max_xy / max_velocity_xy)
    if max_velocity_z_up > 0.0 and max_z_up > 0.0:
        seconds = max(seconds, v_factor * max_z_up / max_velocity_z_up)
    if max_velocity_z_down > 0.0 and max_z_down > 0.0:
        seconds = max(seconds, v_factor * max_z_down / max_velocity_z_down)
    if a_factor is not None and max_acceleration > 0.0 and max_distance > 0.0:
        seconds = max(seconds, sqrt(a_factor * max_distance / max_acceleration))

    return seconds


def wanted_transition_profile(
    *,
    previous_purpose: str,
    previous_profile: str,
    purpose: str,
) -> str | None:
    """Profile a storyboard entry should use so neighbouring moves stay
    velocity-continuous and rest-to-rest wherever both sides hover.

    Returns ``None`` when the existing profile must be left alone (the takeoff
    operator owns that curve).
    """
    if purpose == "TAKEOFF":
        return None

    if previous_purpose == "TAKEOFF" and previous_profile in (
        "LINEAR",
        "SMOOTH_FROM_LEFT",
    ):
        return "SMOOTH_FROM_RIGHT"

    return "SMOOTH"


def peak_acceleration_for_transition(
    displacements: Sequence[Displacement],
    duration: float,
    *,
    profile: str = "SMOOTH",
) -> float:
    """Largest acceleration the given transition would produce, in m/s².

    Returns ``inf`` when the profile has no finite acceleration bound (a
    constant-velocity hop) or when the duration is not positive.
    """
    if duration <= 0.0:
        return inf

    _v_factor, a_factor = _peak_factors(profile)
    if a_factor is None:
        return inf
    if not displacements:
        return 0.0

    max_distance = max(hypot(dx, dy, dz) for dx, dy, dz in displacements)
    return a_factor * max_distance / (duration * duration)
