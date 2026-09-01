"""Unit tests for the local point matching solver."""

from itertools import combinations, permutations

import numpy as np
import pytest
from sbstudio.math.matching import match_points_locally, solve_assignment_problem


def _brute_force_cost(cost):
    """Minimum total cost over every possible assignment, by exhaustion."""
    num_rows, num_cols = cost.shape
    return min(
        sum(cost[row][col] for row, col in enumerate(candidate))
        for candidate in permutations(range(num_cols), num_rows)
    )


class TestSolveAssignmentProblem:
    def test_rejects_more_rows_than_columns(self):
        with pytest.raises(ValueError):
            solve_assignment_problem(np.zeros((4, 3)))

    def test_single_cell(self):
        assert list(solve_assignment_problem(np.array([[7.0]]))) == [0]

    def test_picks_the_obvious_assignment(self):
        # Cheapest entry of each row sits on the anti-diagonal
        cost = np.array([[9.0, 9.0, 1.0], [9.0, 1.0, 9.0], [1.0, 9.0, 9.0]])
        assert list(solve_assignment_problem(cost)) == [2, 1, 0]

    @pytest.mark.parametrize("seed", range(25))
    def test_optimal_for_square_problems(self, seed):
        rng = np.random.default_rng(seed)
        size = int(rng.integers(1, 7))
        cost = rng.random((size, size)) * 10
        assignment = solve_assignment_problem(cost)
        total = cost[range(size), assignment].sum()
        assert total == pytest.approx(_brute_force_cost(cost))

    @pytest.mark.parametrize("seed", range(25))
    def test_optimal_for_rectangular_problems(self, seed):
        rng = np.random.default_rng(1000 + seed)
        num_rows = int(rng.integers(1, 6))
        num_cols = int(rng.integers(num_rows, num_rows + 4))
        cost = rng.random((num_rows, num_cols)) * 10
        assignment = solve_assignment_problem(cost)
        assert len(set(assignment)) == num_rows
        total = cost[range(num_rows), assignment].sum()
        assert total == pytest.approx(_brute_force_cost(cost))


class TestMatchPointsLocally:
    @pytest.mark.parametrize(
        ("num_source", "num_target"),
        [(5, 5), (3, 7), (9, 4), (1, 1), (0, 4), (6, 0)],
    )
    def test_mapping_shape_and_uniqueness(self, num_source, num_target):
        source = [(float(i), 0.0, 0.0) for i in range(num_source)]
        target = [(float(i), 1.0, 0.0) for i in range(num_target)]

        mapping, clearance = match_points_locally(source, target)
        matched = [item for item in mapping if item is not None]

        assert clearance is None
        assert len(mapping) == num_target
        assert len(matched) == min(num_source, num_target)
        assert len(set(matched)) == len(matched)
        assert all(0 <= item < num_source for item in matched)

    def test_identity_is_preferred_for_a_pure_translation(self):
        # Shifting every point by the same vector: each drone should keep its
        # own target rather than swapping with a neighbour
        source = [(float(i), 0.0, 0.0) for i in range(6)]
        target = [(float(i), 0.0, 10.0) for i in range(6)]

        mapping, _ = match_points_locally(source, target)

        assert mapping == list(range(6))

    def test_reversed_targets_are_matched_back_in_order(self):
        source = [(float(i), 0.0, 0.0) for i in range(5)]
        target = [(float(4 - i), 0.0, 5.0) for i in range(5)]

        mapping, _ = match_points_locally(source, target)

        assert mapping == [4, 3, 2, 1, 0]

    @pytest.mark.parametrize("seed", range(10))
    def test_transition_is_collision_free(self, seed):
        """The optimal assignment under squared distance must keep drones apart
        for the whole synchronized, linearly interpolated transition.
        """
        rng = np.random.default_rng(seed)
        count = 15
        source = rng.random((count, 3)) * 50
        target = rng.random((count, 3)) * 50

        mapping, _ = match_points_locally(
            [tuple(point) for point in source], [tuple(point) for point in target]
        )

        starts = np.array([source[item] for item in mapping])
        ends = np.array([target[index] for index in range(count)])

        for time in np.linspace(0.0, 1.0, 200):
            positions = (1.0 - time) * starts + time * ends
            for left, right in combinations(range(count), 2):
                distance = np.linalg.norm(positions[left] - positions[right])
                assert distance > 1e-6
