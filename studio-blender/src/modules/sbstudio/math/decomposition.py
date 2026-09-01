"""Offline replacement for the point decomposition / landing services of
Skybrush Studio Server.

When drones sit closer to each other than the safety distance, they cannot all
take off or land at the same time -- the ones underneath would fly through the
downwash of the ones above. The fix is to split them into groups ("layers") such
that within a single group every pair of drones is at least ``min_distance``
apart, and then handle the groups one after the other.

Finding the smallest number of such groups is graph colouring, which is
NP-hard, so this uses the same greedy strategy the server exposes as its
``"greedy"`` method: walk the points in order and drop each one into the first
group where it does not conflict with anything already there.
"""

from typing import Sequence

from numpy import asarray

from sbstudio.model.types import Coordinate3D

__all__ = ("decompose_points_locally", "plan_landing_locally")


def decompose_points_locally(
    points: Sequence[Coordinate3D],
    *,
    min_distance: float,
) -> list[int]:
    """Splits points into groups so that points within a group are far enough
    from each other.

    This is a drop-in replacement for `SkybrushStudioAPI.decompose_points()`
    and `SkybrushStudioAPI.plan_takeoff()` and returns its result in the same
    shape.

    Parameters:
        points: the coordinates of the drones
        min_distance: the minimum distance that two drones in the same group
            must keep from each other

    Returns:
        a list holding the index of the group each point was assigned to
    """
    num_points = len(points)
    if not num_points:
        return []

    coordinates = asarray(points, dtype=float)
    squared_min_distance = min_distance * min_distance

    groups: list[int] = [0] * num_points
    members: list[list[int]] = []

    for index in range(num_points):
        for group_index, group_members in enumerate(members):
            differences = coordinates[group_members] - coordinates[index]
            squared_distances = (differences * differences).sum(axis=-1)
            if (squared_distances >= squared_min_distance).all():
                group_members.append(index)
                groups[index] = group_index
                break
        else:
            # Conflicts with every existing group, so it needs a new one
            groups[index] = len(members)
            members.append([index])

    return groups


def plan_landing_locally(
    points: Sequence[Coordinate3D],
    *,
    min_distance: float,
    velocity: float,
    target_altitude: float = 0,
    spindown_time: float = 5,
) -> tuple[list[float], list[float]]:
    """Plans staggered landing start times and durations without the server.

    Drop-in replacement for `SkybrushStudioAPI.plan_landing()`.

    Drones whose landing positions are closer than ``min_distance`` are split
    into groups. Group 0 lands first; each later group waits until the previous
    group has finished descending and its motors have spun down.

    Parameters:
        points: current coordinates of the drones
        min_distance: minimum distance to keep while motors are running
        velocity: average vertical landing speed in m/s
        target_altitude: altitude to land to, in meters
        spindown_time: seconds to wait after a group lands before the next
            group may start

    Returns:
        ``(start_times, durations)`` in seconds for each drone
    """
    if velocity <= 0:
        raise ValueError("landing velocity must be positive")

    if not points:
        return [], []

    groups = decompose_points_locally(points, min_distance=min_distance)
    durations = [max(0.0, (point[2] - target_altitude) / velocity) for point in points]

    num_groups = max(groups) + 1 if groups else 0
    max_duration_per_group = [0.0] * num_groups
    for index, group in enumerate(groups):
        if durations[index] > max_duration_per_group[group]:
            max_duration_per_group[group] = durations[index]

    group_start_times = [0.0] * num_groups
    for group in range(1, num_groups):
        group_start_times[group] = (
            group_start_times[group - 1]
            + max_duration_per_group[group - 1]
            + max(0.0, spindown_time)
        )

    start_times = [group_start_times[group] for group in groups]
    return start_times, durations
