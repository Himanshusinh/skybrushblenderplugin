from base64 import b64encode
from collections.abc import Sequence
from itertools import chain
from operator import attrgetter
from typing import Self

from numpy import arange, array, interp

from .point import Point3D, Point4D

__all__ = ("Trajectory",)


class Trajectory:
    """Simplest representation of a causal trajectory in space and time.

    Positions between given Point4D elements are assumed to be
    linearly interpolated both in space and time.
    """

    def __init__(self, points: Sequence[Point4D] = []):
        self.points = sorted(points, key=attrgetter("t"))

    @property
    def first_point(self) -> Point4D | None:
        return self.points[0] if self.points else None

    @property
    def first_time(self) -> float | None:
        return self.points[0].t if self.points else None

    @property
    def last_point(self) -> Point4D | None:
        return self.points[-1] if self.points else None

    @property
    def last_time(self) -> float | None:
        return self.points[-1].t if self.points else None

    def append(self, point: Point4D) -> None:
        """Add a point to the end of the trajectory."""
        if self.points and self.points[-1].t >= point.t:
            raise ValueError("New point must come after existing trajectory in time")
        self.points.append(point)

    def as_dict(self, ndigits: int = 3, *, version: int = 2):
        """Create a Skybrush-compatible dictionary representation of this
        instance.

        Parameters:
            ndigits: round floats to this precision
            version: version of the representation to generate

        Return:
            dictionary of this instance, to be converted to JSON later

        """
        if version == 0:
            # Deprecated representation that contains the points in a list of
            # length-4 lists
            # Use version 2 instead, which does the same but in a faster and
            # smaller binary representation
            return {
                "points": [
                    [
                        round(point.t, ndigits=ndigits),
                        round(point.x, ndigits=ndigits),
                        round(point.y, ndigits=ndigits),
                        round(point.z, ndigits=ndigits),
                    ]
                    for point in self.points
                ],
                "version": 0,
            }
        elif version == 1:
            # Standard representation
            return {
                "points": [
                    [
                        round(point.t, ndigits=ndigits),
                        [
                            round(point.x, ndigits=ndigits),
                            round(point.y, ndigits=ndigits),
                            round(point.z, ndigits=ndigits),
                        ],
                        [],
                    ]
                    for point in self.points
                ],
                "version": 1,
            }
        elif version == 2:
            # Representation similar to version 0 but in a binary form for
            # reducing bandwidth usage and increasing render speed
            # TODO: use numpy arrays in Trajectory already for additional speedup
            floats = array(
                list(chain.from_iterable(point.as_tuple() for point in self.points)),
                dtype="<f4",
            )
            return {
                "points": b64encode(floats.tobytes()).decode("ascii"),
                "version": 2,
            }
        else:
            raise ValueError(f"Unknown version {version} for trajectory representation")

    @property
    def duration(self) -> float:
        """Returns the duration of the trajectory in seconds."""

        if len(self.points) < 2:
            return 0.0

        return self.points[-1].t - self.points[0].t

    def resample_in_place(self, fps: float) -> Self:
        """Resamples the trajectory to the given FPS value in-place.

        Parameters:
            fps: the new fps value to resample the trajectories to, in [1/s]
        """

        if not self.points:
            return self
        assert self.first_time is not None
        assert self.last_time is not None

        source_times = [p.t for p in self.points]
        target_times = arange(self.first_time, self.last_time, 1 / fps)

        resampled_x = interp(target_times, source_times, [p.x for p in self.points])
        resampled_y = interp(target_times, source_times, [p.y for p in self.points])
        resampled_z = interp(target_times, source_times, [p.z for p in self.points])

        self.points = [
            Point4D(t, x, y, z)
            for t, x, y, z in zip(target_times, resampled_x, resampled_y, resampled_z)
        ]

        return self

    def offset_in_place(self, offset: Point3D) -> Self:
        """Offsets all points of the trajectory in-place.

        Parameters:
            offset: the offset to add to each point in the trajectory.
        """
        for point in self.points:
            point.x += offset.x
            point.y += offset.y
            point.z += offset.z
        return self

    def shift_time_in_place(self, delta: float) -> Self:
        """Shifts all timestamp of the trajectory in-place.

        Parameters:
            delta: the time delta to add to the timestamp of each point in the
                trajectory.
        """
        for point in self.points:
            point.t += delta
        return self

    def simplify_in_place(self, eps: float = 0.05) -> Self:
        """Simplifies the trajectory in-place by removing points that are
        linearly interpolatable within spatial tolerance eps (in meters).
        """
        if not self.points or len(self.points) < 3:
            return self

        from sbstudio.utils import simplify_path

        def traj_dist_func(pts, start, end):
            dt = end.t - start.t
            if dt <= 0:
                return [0.0] * len(pts)
            res = []
            for p in pts:
                ratio = (p.t - start.t) / dt
                ix = start.x + ratio * (end.x - start.x)
                iy = start.y + ratio * (end.y - start.y)
                iz = start.z + ratio * (end.z - start.z)
                d = ((p.x - ix) ** 2 + (p.y - iy) ** 2 + (p.z - iz) ** 2) ** 0.5
                res.append(d)
            return res

        def traj_eq_func(p1, p2):
            return (
                abs(p1.x - p2.x) <= 0.001
                and abs(p1.y - p2.y) <= 0.001
                and abs(p1.z - p2.z) <= 0.001
            )

        self.points = simplify_path(
            list(self.points),
            eps=eps,
            distance_func=traj_dist_func,
            eq_func=traj_eq_func,
        )
        return self
