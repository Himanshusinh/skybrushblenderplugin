"""Finite-difference estimation of derivatives of sampled 3D vectors.

Used by the safety check to work out how fast the drones are accelerating from
the positions it recorded in the frames the user has visited.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from sbstudio.model.types import Coordinate3D

__all__ = ("estimate_second_derivative",)


VectorDict = dict[str, Coordinate3D]
"""Mapping from names to the value of some 3D quantity for each of them."""

_ZERO: Coordinate3D = (0.0, 0.0, 0.0)


def _gather(
    sample: VectorDict,
    cache: Mapping[int, VectorDict],
    *,
    index: int,
    offsets: Sequence[int],
) -> list[VectorDict] | None:
    """Gathers the samples at the given offsets from the given index, taking the
    zero offset from ``sample`` and the rest from the cache.

    Returns:
        the samples in the order of the offsets, or ``None`` if the cache does
        not hold all of them
    """
    result: list[VectorDict] = []

    for offset in offsets:
        if offset == 0:
            result.append(sample)
        else:
            try:
                result.append(cache[index + offset])
            except KeyError:
                return None

    return result


def _second_difference(
    samples: Sequence[VectorDict], *, names: Iterable[str], step_size: float
) -> VectorDict | None:
    """Calculates the second difference of three samples that are ``step_size``
    apart from each other.

    Returns:
        the second difference for each of the given names, or ``None`` if any of
        the samples is missing one of them
    """
    before, middle, after = samples
    scale = 1.0 / (step_size * step_size)

    result: VectorDict = {}
    for name in names:
        prev, curr, next = before.get(name), middle.get(name), after.get(name)
        if prev is None or curr is None or next is None:
            return None

        result[name] = (
            (prev[0] - 2.0 * curr[0] + next[0]) * scale,
            (prev[1] - 2.0 * curr[1] + next[1]) * scale,
            (prev[2] - 2.0 * curr[2] + next[2]) * scale,
        )

    return result


def estimate_second_derivative(
    sample: VectorDict,
    cache: Mapping[int, VectorDict],
    *,
    index: int,
    step_size: float,
    max_step: int = 2,
) -> tuple[VectorDict, bool]:
    """Estimates the second derivative of a quantity at the given index as a
    second difference over evenly spaced indices.

    Differentiating cached estimates of the *first* derivative instead would not
    be reliable: each of those is measured over whatever gap happens to be
    available in the cache at the time, and sometimes against a later index
    rather than an earlier one, so the difference of two of them can report a
    violent second derivative for a curve that is in fact perfectly smooth.

    Parameters:
        sample: the values at the given index
        cache: the values at the indices visited earlier, keyed by index. It
            does not matter whether it also holds ``index`` itself
        index: the index to estimate at
        step_size: the distance between two consecutive indices, in the unit
            that the result should be expressed in
        max_step: largest index spacing to accept. A wider spacing averages the
            result over a longer stretch of the curve, which masks short spikes,
            so keep this small

    Returns:
        the estimates, and whether they could be estimated at all. An all-zero
        result reported as not estimated means "unknown", not "zero".
    """
    for step in range(1, max_step + 1):
        # The centred stencil comes first because it is both the most accurate
        # and the only one that describes the given index rather than a
        # neighbouring one. The one-sided stencils are what remains while the
        # user steps through the timeline in one direction and the indices ahead
        # have not been visited yet.
        for offsets in ((-step, 0, step), (-2 * step, -step, 0), (0, step, 2 * step)):
            samples = _gather(sample, cache, index=index, offsets=offsets)
            if samples is None:
                continue

            result = _second_difference(
                samples, names=sample, step_size=step * step_size
            )
            if result is not None:
                return result, True

    return dict.fromkeys(sample, _ZERO), False
