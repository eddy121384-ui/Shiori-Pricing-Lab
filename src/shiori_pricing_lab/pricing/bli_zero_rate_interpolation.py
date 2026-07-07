"""BLI continuous zero-rate interpolation (docs/28 dependency chain).

Scope: a single narrow helper -- given prepared
`BLIContinuousZeroCurveNode` values and a target year fraction, return
the continuously-compounded zero rate via piecewise-linear
interpolation on zero rates (Annex A §A.10.2), with flat extrapolation
outside the node range. This module consumes only
`BLIContinuousZeroCurveNode` values; it does not parse tenors, does not
select curve purpose, does not inspect `BLICurvePoint` or
`BLICurveRateBasis` at all, and does not convert par rates -- all of
that already happened upstream, in
`pricing/bli_curve_tenor.py`/`pricing/bli_curve_selector.py`/
`pricing/bli_zero_curve_nodes.py`, before a caller ever reaches this
helper.

**Flat extrapolation, rate only (Annex A §A.10.2):** Annex A pins flat
extrapolation outside the curve's range, "並標示 fallback flag" (and
flags the fallback). This module implements only the rate calculation
for that case -- no fallback-flag object, no audit output, no warning.
That is a separate, future concern, not part of this slice.

This module is not imported by, and does not change the behavior of,
`pricing/bli_pricing_engine.py::price_bli_mvp`.
"""

from __future__ import annotations

from collections.abc import Iterable
from math import isfinite

from shiori_pricing_lab.pricing.bli_zero_curve_nodes import BLIContinuousZeroCurveNode


def interpolate_continuous_zero_rate(
    nodes: Iterable[BLIContinuousZeroCurveNode],
    target_year_fraction: float,
) -> float:
    """Return the continuously-compounded zero rate at ``target_year_fraction``.

    Piecewise-linear interpolation on zero rates between the two
    adjacent nodes bracketing ``target_year_fraction`` (Annex A
    §A.10.2); an exact node match returns that node's ``zero_rate``
    directly; a target outside the node range returns the nearest
    endpoint's ``zero_rate`` (flat extrapolation, rate only -- no
    fallback-flag object). A single-node input returns that node's
    ``zero_rate`` for any valid target.

    Validates its own input even though callers are expected to already
    have used `build_continuous_zero_curve_nodes` to prepare ``nodes``:
    raises :class:`ValueError` for empty input, a non-finite or
    non-positive ``target_year_fraction``, a non-finite or non-positive
    node ``year_fraction``, a non-finite node ``zero_rate``, or
    duplicate node ``year_fraction`` values; raises :class:`TypeError`
    for a non-``BLIContinuousZeroCurveNode`` element. Signed zero rates
    are allowed; no non-negative constraint is imposed.
    """

    if not isfinite(target_year_fraction) or target_year_fraction <= 0:
        raise ValueError(
            f"target_year_fraction must be a finite, positive number, got "
            f"{target_year_fraction!r}"
        )

    points = tuple(nodes)
    if not points:
        raise ValueError("nodes must not be empty")

    seen_year_fractions: set[float] = set()
    for node in points:
        if not isinstance(node, BLIContinuousZeroCurveNode):
            raise TypeError(
                "nodes must contain only BLIContinuousZeroCurveNode instances, got "
                f"{type(node).__name__}"
            )
        if not isfinite(node.year_fraction) or node.year_fraction <= 0:
            raise ValueError(
                f"node.year_fraction must be a finite, positive number, got "
                f"{node.year_fraction!r}"
            )
        if not isfinite(node.zero_rate):
            raise ValueError(f"node.zero_rate must be a finite number, got {node.zero_rate!r}")
        if node.year_fraction in seen_year_fractions:
            raise ValueError(f"duplicate node.year_fraction {node.year_fraction!r}")
        seen_year_fractions.add(node.year_fraction)

    sorted_nodes = sorted(points, key=lambda node: node.year_fraction)

    for node in sorted_nodes:
        if node.year_fraction == target_year_fraction:
            return node.zero_rate

    if target_year_fraction < sorted_nodes[0].year_fraction:
        return sorted_nodes[0].zero_rate
    if target_year_fraction > sorted_nodes[-1].year_fraction:
        return sorted_nodes[-1].zero_rate

    for left, right in zip(sorted_nodes, sorted_nodes[1:], strict=False):
        if left.year_fraction < target_year_fraction < right.year_fraction:
            return left.zero_rate + (right.zero_rate - left.zero_rate) * (
                target_year_fraction - left.year_fraction
            ) / (right.year_fraction - left.year_fraction)

    # Unreachable: every target_year_fraction is either an exact match,
    # outside the range (handled above), or strictly between two
    # adjacent sorted nodes.
    raise AssertionError("unreachable: target_year_fraction not bracketed by any node pair")
