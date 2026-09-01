"""Offline replacement for the point matching service of Skybrush Studio Server.

A transition between two formations is an assignment problem: every drone has to
be sent to exactly one target point, and we want the assignment that minimizes
the total *squared* distance travelled.

Squared distance is the important part. Assume the drones all leave and arrive
together and interpolate linearly, so drone i sits at ``(1 - t) * a_i + t * b_i``.
Drones i and j collide iff ``(1 - t) * (a_i - a_j) + t * (b_i - b_j) == 0`` for
some t in [0, 1], which requires ``b_i - b_j`` to point opposite to
``a_i - a_j``. Swapping the two targets changes the total squared cost by
``-2 * (a_i - a_j) . (b_i - b_j)``, and that is strictly positive precisely when
the two vectors are anti-parallel -- so a colliding pair can always be improved
upon, and an optimal assignment therefore contains none.

Note that this buys collision-freedom, not non-crossing paths: two trajectories
may still cross on the ground plane, they just never reach the crossing point at
the same moment. Minimizing plain (non-squared) distance is what avoids
crossings, and it does *not* imply the collision-free property we want here.

The solver is the Jonker-Volgenant shortest augmenting path algorithm, i.e. the
same O(n^3) method behind `scipy.optimize.linear_sum_assignment`. It is written
against NumPy alone because Blender ships NumPy but not SciPy.
"""

from typing import Sequence

from numpy import (
    argmin,
    asarray,
    bool_,
    empty,
    full,
    inf,
    int_,
    ndarray,
    where,
    zeros,
)

from sbstudio.model.types import Coordinate3D

__all__ = ("match_points_locally", "solve_assignment_problem")


def solve_assignment_problem(cost: ndarray) -> ndarray:
    """Solves the rectangular linear sum assignment problem.

    Parameters:
        cost: an ``(n, m)`` cost matrix with ``n <= m``

    Returns:
        an integer array of length ``n`` in which the i-th item is the index of
        the column assigned to row i
    """
    num_rows, num_cols = cost.shape
    if num_rows > num_cols:
        raise ValueError("cost matrix must not have more rows than columns")

    # Dual potentials. Column-indexed arrays carry one extra slot at index 0
    # that stands for the virtual column each augmenting path starts from.
    u = zeros(num_rows + 1)
    v = zeros(num_cols + 1)

    # predecessor[j] is the column visited right before column j on the current
    # augmenting path; owner[j] is the 1-based row currently assigned to column
    # j, or 0 if the column is still free
    predecessor = zeros(num_cols + 1, dtype=int_)
    owner = zeros(num_cols + 1, dtype=int_)

    for row in range(1, num_rows + 1):
        owner[0] = row
        col = 0
        min_reduced = full(num_cols + 1, inf)
        visited = zeros(num_cols + 1, dtype=bool_)

        # Grow an alternating path from the current row until it reaches a
        # column that nobody is assigned to yet
        while True:
            visited[col] = True
            current_row = owner[col]
            free = ~visited[1:]

            # Reduced costs of the still unvisited columns for the row that
            # currently owns the column we stepped onto
            reduced = cost[current_row - 1] - u[current_row] - v[1:]
            improved = free & (reduced < min_reduced[1:])
            min_reduced[1:][improved] = reduced[improved]
            predecessor[1:][improved] = col

            # Cheapest unvisited column decides where the path continues
            candidates = where(free, min_reduced[1:], inf)
            best = int(argmin(candidates))
            delta = candidates[best]
            next_col = best + 1

            # Shift the potentials so that the chosen edge becomes tight
            u[owner[visited]] += delta
            v[visited] -= delta
            min_reduced[1:][free] -= delta

            col = next_col
            if owner[col] == 0:
                break

        # Walk the path backwards, flipping the assignment along it
        while col:
            previous = predecessor[col]
            owner[col] = owner[previous]
            col = previous

    result = empty(num_rows, dtype=int_)
    for column in range(1, num_cols + 1):
        if owner[column]:
            result[owner[column] - 1] = column - 1

    return result


def match_points_locally(
    source: Sequence[Coordinate3D],
    target: Sequence[Coordinate3D],
) -> tuple[list[int | None], float | None]:
    """Matches a set of source points to a set of target points without
    contacting Skybrush Studio Server.

    This is a drop-in replacement for `SkybrushStudioAPI.match_points()` and
    returns its result in the same shape.

    Parameters:
        source: coordinates of the drones at the start of the transition
        target: coordinates of the points the drones should move to

    Returns:
        a pair whose first item maps each *target* index to the index of the
        source point assigned to it (or ``None`` when the target was left
        unmatched), and whose second item is the minimum clearance during the
        transition -- always ``None`` here, which the caller treats as "not
        calculated"
    """
    num_source, num_target = len(source), len(target)
    if not num_source or not num_target:
        return [None] * num_target, None

    source_array = asarray(source, dtype=float)
    target_array = asarray(target, dtype=float)

    # Squared Euclidean distance between every source and every target point
    differences = source_array[:, None, :] - target_array[None, :, :]
    cost = (differences * differences).sum(axis=-1)

    mapping: list[int | None] = [None] * num_target

    if num_source <= num_target:
        # Every drone gets a target; the surplus targets stay unmatched
        assignment = solve_assignment_problem(cost)
        for source_index, target_index in enumerate(assignment):
            mapping[int(target_index)] = source_index
    else:
        # Every target gets a drone; the surplus drones stay where they are
        assignment = solve_assignment_problem(cost.T)
        for target_index, source_index in enumerate(assignment):
            mapping[target_index] = int(source_index)

    return mapping, None
