"""Unit tests for the local point decomposition solver."""

from itertools import combinations
from math import dist

import numpy as np
import pytest
from sbstudio.math.decomposition import (
    decompose_points_locally,
    plan_landing_locally,
)


def _grid(rows, columns, spacing):
    xs, ys = np.mgrid[0:columns, 0:rows]
    xs = (xs.ravel() - (columns - 1) / 2) * spacing
    ys = (ys.ravel() - (rows - 1) / 2) * spacing
    return [(float(x), float(y), 0.0) for x, y in zip(xs, ys)]


def _assert_groups_are_safe(points, groups, min_distance):
    for left, right in combinations(range(len(points)), 2):
        if groups[left] == groups[right]:
            assert dist(points[left], points[right]) >= min_distance - 1e-9


class TestDecomposePointsLocally:
    def test_empty_input(self):
        assert decompose_points_locally([], min_distance=3.0) == []

    def test_single_point_needs_one_group(self):
        assert decompose_points_locally([(0.0, 0.0, 0.0)], min_distance=3.0) == [0]

    def test_sparse_points_share_a_single_group(self):
        points = _grid(6, 6, 5.0)
        groups = decompose_points_locally(points, min_distance=3.0)
        assert set(groups) == {0}

    def test_coincident_points_land_in_separate_groups(self):
        points = [(0.0, 0.0, 0.0)] * 4
        groups = decompose_points_locally(points, min_distance=1.0)
        assert sorted(groups) == [0, 1, 2, 3]

    @pytest.mark.parametrize(
        ("spacing", "min_distance", "expected_groups"),
        [
            (3.0, 3.0, 1),  # exactly at the limit, one layer is enough
            (1.5, 3.0, 4),  # every second point in each axis -> 2x2
            (1.0, 3.0, 9),  # every third point in each axis -> 3x3
        ],
    )
    def test_regular_grid_splits_into_the_expected_sublattice(
        self, spacing, min_distance, expected_groups
    ):
        points = _grid(12, 12, spacing)
        groups = decompose_points_locally(points, min_distance=min_distance)
        assert max(groups) + 1 == expected_groups
        _assert_groups_are_safe(points, groups, min_distance)

    @pytest.mark.parametrize("seed", range(8))
    def test_groups_always_respect_the_minimum_distance(self, seed):
        rng = np.random.default_rng(seed)
        points = [tuple(map(float, point)) for point in rng.random((80, 3)) * 30]
        min_distance = 6.0

        groups = decompose_points_locally(points, min_distance=min_distance)

        assert len(groups) == len(points)
        assert min(groups) == 0
        _assert_groups_are_safe(points, groups, min_distance)


class TestPlanLandingLocally:
    def test_sparse_points_all_start_together(self):
        points = [(0.0, 0.0, 10.0), (5.0, 0.0, 10.0), (0.0, 5.0, 8.0)]
        starts, durations = plan_landing_locally(
            points, min_distance=3.0, velocity=2.0, target_altitude=0.0
        )
        assert starts == [0.0, 0.0, 0.0]
        assert durations == [5.0, 5.0, 4.0]

    def test_dense_points_are_staggered_by_group(self):
        # 2x2 grid at 1.5 m spacing needs 4 groups when min_distance is 3 m
        points = [
            (0.0, 0.0, 6.0),
            (1.5, 0.0, 6.0),
            (0.0, 1.5, 6.0),
            (1.5, 1.5, 6.0),
        ]
        starts, durations = plan_landing_locally(
            points,
            min_distance=3.0,
            velocity=2.0,
            target_altitude=0.0,
            spindown_time=5.0,
        )
        assert durations == [3.0, 3.0, 3.0, 3.0]
        # Four separate groups: each waits for previous descent + spindown
        assert sorted(set(starts)) == [0.0, 8.0, 16.0, 24.0]
        assert len(set(starts)) == 4

    def test_scales_past_community_server_limit(self):
        points = [(float(i % 10), float(i // 10), 10.0) for i in range(100)]
        starts, durations = plan_landing_locally(
            points, min_distance=3.0, velocity=1.0, target_altitude=0.0
        )
        assert len(starts) == 100
        assert len(durations) == 100
        assert all(d == 10.0 for d in durations)
