"""BLI continuous zero-rate curve node preparation (docs/28 dependency chain).

Scope: a small, deterministic calculation-preparation helper that
converts already-selected `BLICurvePoint` rows into sorted, validated
continuous-zero-rate curve nodes. This is the step that sits between
`pricing/bli_curve_selector.py::select_curve_points_by_purpose` (pick
one curve's rows) and any future interpolation helper (use those rows'
rates as `x`/`y` pairs) -- it does not select by currency/curve purpose
itself, and it does not interpolate, extrapolate, or compute a discount
factor, forward clean price, or PV.

**Rate-basis gate:** every input `BLICurvePoint` must already have
`rate_basis is BLICurveRateBasis.CONTINUOUS_ZERO_RATE` (the required,
explicit contract landed in PR #75). A row carrying any other basis
(`PAR_RATE`, `SIMPLE_ZERO_RATE`, `SWAP_RATE`, `BOND_YIELD`,
`FUNDING_RATE`, `OTHER`) raises -- this module never converts a
non-continuous-zero rate into one, and never infers continuous-zero
status from `curve_purpose`, `curve_id`, `curve_name`, `source_system`,
or `tenor`.

**Tenor parsing:** delegates to the existing, reviewed
`pricing/bli_curve_tenor.py::tenor_to_year_fraction` -- this module does
not define, and does not use, any other tenor parser (not
`pricing/curve.py::tenor_to_years`, not a new one).

This module is not imported by, and does not change the behavior of,
`pricing/bli_pricing_engine.py::price_bli_mvp`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from shiori_pricing_lab.data.bli_snapshot import BLICurvePoint, BLICurveRateBasis
from shiori_pricing_lab.pricing.bli_curve_tenor import tenor_to_year_fraction


@dataclass(frozen=True)
class BLIContinuousZeroCurveNode:
    """One validated, tenor-parsed continuous-zero-rate curve node."""

    year_fraction: float
    zero_rate: float
    source_tenor: str
    source_curve_id: str


def build_continuous_zero_curve_nodes(
    curve_points: Iterable[BLICurvePoint],
) -> tuple[BLIContinuousZeroCurveNode, ...]:
    """Convert already-selected `BLICurvePoint` rows into sorted zero-curve nodes.

    Every row must already carry `rate_basis is
    BLICurveRateBasis.CONTINUOUS_ZERO_RATE` -- raises :class:`ValueError`
    naming the offending tenor, curve_id, and actual/expected basis
    otherwise. Raises :class:`TypeError` for a non-`BLICurvePoint`
    element, and :class:`ValueError` for empty input or duplicate parsed
    `year_fraction` values (e.g. `"12M"` and `"1Y"` both parsing to
    `1.0`). Returns nodes sorted by `year_fraction` ascending regardless
    of input order. Does not select by currency/curve_purpose, does not
    interpolate or extrapolate, and does not compute a discount factor,
    forward clean price, or PV -- callers must first narrow to one
    curve's rows themselves (e.g. via
    `select_curve_points_by_purpose`).
    """

    points = tuple(curve_points)
    if not points:
        raise ValueError("curve_points must not be empty")

    for point in points:
        if not isinstance(point, BLICurvePoint):
            raise TypeError(
                "curve_points must contain only BLICurvePoint instances, got "
                f"{type(point).__name__}"
            )
        if point.rate_basis is not BLICurveRateBasis.CONTINUOUS_ZERO_RATE:
            raise ValueError(
                f"curve_id={point.curve_id!r} tenor={point.tenor!r} has rate_basis="
                f"{point.rate_basis.value!r}, expected "
                f"{BLICurveRateBasis.CONTINUOUS_ZERO_RATE.value!r}"
            )

    nodes = [
        BLIContinuousZeroCurveNode(
            year_fraction=tenor_to_year_fraction(point.tenor),
            zero_rate=point.rate,
            source_tenor=point.tenor,
            source_curve_id=point.curve_id,
        )
        for point in points
    ]

    seen_year_fractions: dict[float, str] = {}
    for node in nodes:
        if node.year_fraction in seen_year_fractions:
            raise ValueError(
                f"duplicate parsed year_fraction {node.year_fraction!r} from tenors "
                f"{seen_year_fractions[node.year_fraction]!r} and {node.source_tenor!r}"
            )
        seen_year_fractions[node.year_fraction] = node.source_tenor

    return tuple(sorted(nodes, key=lambda node: node.year_fraction))
