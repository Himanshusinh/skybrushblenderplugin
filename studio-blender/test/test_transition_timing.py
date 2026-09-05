"""Guards the duration planner for formation-to-formation transitions.

A hop that is timed from index-aligned distances, or from average velocity
alone, leaves the drones with a gap that cannot absorb the real longest
assignment. The planner must size the gap from the matched displacements and
from the peak of the velocity profile, otherwise the overlay reports a jump
from 0 to tens of m/s² at the first frame of the transition.
"""

from math import hypot, sqrt

import pytest
from sbstudio.math.transition_timing import (
    MIN_JERK_PEAK_ACCELERATION,
    MIN_JERK_PEAK_VELOCITY,
    displacements_for_assignment,
    peak_acceleration_for_transition,
    required_transition_duration,
    wanted_transition_profile,
)


def test_assignment_uses_the_matched_pairing_not_the_index():
    source = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0)]
    target = [(10.0, 0.0, 0.0), (0.0, 0.0, 0.0)]
    # Target 0 comes from source 1, target 1 from source 0: both hop 0 m
    match = [1, 0]

    hops = displacements_for_assignment(source, target, match)

    assert hops == [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0)]


def test_short_gap_for_a_long_hop_is_rejected():
    """117 m in 5 s is the 28 m/s² readout from a C_maa → Mahadev-sized hop."""
    hop = [(80.0, 0.0, 51.0)]
    distance = hypot(80.0, 51.0)

    seconds = required_transition_duration(
        hop,
        max_velocity_xy=10.0,
        max_velocity_z_up=4.0,
        max_velocity_z_down=2.0,
        max_acceleration=4.0,
        profile="SMOOTH",
    )

    assert seconds >= sqrt(MIN_JERK_PEAK_ACCELERATION * distance / 4.0)
    assert peak_acceleration_for_transition(hop, seconds) <= 4.0 * 1.001
    assert peak_acceleration_for_transition(hop, 5.0) == pytest.approx(
        MIN_JERK_PEAK_ACCELERATION * distance / 25.0, rel=1e-9
    )
    assert peak_acceleration_for_transition(hop, 5.0) > 20.0


def test_horizontal_velocity_cap_can_dominate_the_duration():
    hop = [(100.0, 0.0, 0.0)]

    seconds = required_transition_duration(
        hop,
        max_velocity_xy=8.0,
        max_velocity_z_up=4.0,
        max_velocity_z_down=2.0,
        max_acceleration=20.0,
        profile="SMOOTH",
    )

    assert seconds == pytest.approx(MIN_JERK_PEAK_VELOCITY * 100.0 / 8.0, rel=1e-9)


def test_linear_profile_is_not_saved_by_the_acceleration_cap():
    hop = [(50.0, 0.0, 0.0)]

    seconds = required_transition_duration(
        hop,
        max_velocity_xy=10.0,
        max_velocity_z_up=4.0,
        max_velocity_z_down=2.0,
        max_acceleration=0.1,
        profile="LINEAR",
    )

    # Constant velocity: duration comes from the speed, not from 0.1 m/s²
    assert seconds == pytest.approx(5.0, rel=1e-9)
    assert peak_acceleration_for_transition(hop, seconds, profile="LINEAR") == float(
        "inf"
    )


def test_show_moves_are_forced_onto_a_rest_to_rest_profile():
    assert (
        wanted_transition_profile(
            previous_purpose="SHOW",
            previous_profile="LINEAR",
            purpose="SHOW",
        )
        == "SMOOTH"
    )
    assert (
        wanted_transition_profile(
            previous_purpose="TAKEOFF",
            previous_profile="LINEAR",
            purpose="SHOW",
        )
        == "SMOOTH_FROM_RIGHT"
    )
    assert (
        wanted_transition_profile(
            previous_purpose="TAKEOFF",
            previous_profile="SMOOTH",
            purpose="TAKEOFF",
        )
        is None
    )


def test_empty_transition_still_lasts_a_second():
    seconds = required_transition_duration(
        [],
        max_velocity_xy=10.0,
        max_velocity_z_up=2.0,
        max_velocity_z_down=2.0,
        max_acceleration=4.0,
    )

    assert seconds == 1.0
