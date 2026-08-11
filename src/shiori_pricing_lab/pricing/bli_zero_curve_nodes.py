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

**Node coordinate: explicit date when available, tenor label otherwise
(Issue #165 P1 follow-up).** Each row's own `maturity_date` (see
`data/bli_snapshot.py::BLICurvePoint`) decides which coordinate this row
uses, per row, independently:

- `maturity_date is None` (every curve row that existed before Issue
  #165, and any future row with no source-reported date): unchanged --
  delegates to the existing, reviewed
  `pricing/bli_curve_tenor.py::tenor_to_year_fraction` exactly as before.
  This path's behavior is untouched by this slice; `as_of_date` is never
  read or required for these rows.
- `maturity_date is not None` (e.g. a Bloomberg-sourced row where the
  source system reported a real calendar date): the row's own
  `year_fraction` is computed from that date, via the existing, reviewed
  `pricing/bli_valuation_time.py::year_fraction_to_expiry(as_of_date,
  point.maturity_date)` -- the same ACT/365F day-count function, and the
  same reference-date parameter name and role, already used for every
  other year-fraction this pricing chain computes (the option's own
  `time_to_expiry` in `bli_pricing_engine.py`, and both the coupon and
  expiry discount-factor targets in `bli_forward_clean_price.py` --
  all anchored to the same `valuation_date`). Reusing this function
  rather than writing new date arithmetic also means an explicit-date
  row's `year_fraction` can never be negative or zero without raising --
  `year_fraction_to_expiry` already rejects a target that is not
  strictly after the reference date (Annex A §A.2.4's `T > 0` rule,
  restated here as the explicit-date analogue of the existing
  `BLIContinuousZeroCurveNode.year_fraction` positivity requirement
  enforced downstream in `bli_zero_rate_interpolation.py`).

**No silent fallback (Issue #165 P1 follow-up, required by the task
itself).** If *any* row in `curve_points` carries a `maturity_date` but
the caller did not supply `as_of_date`, this function raises
:class:`ValueError` immediately, naming the offending row's `curve_id`/
`tenor`/`maturity_date` -- it never falls back to `tenor_to_year_fraction`
for that row, never defaults `as_of_date` to the current system date or
any other invented value, and never silently drops the row. `as_of_date` itself is
validated the same way `year_fraction_to_expiry` already validates its
own `valuation_date` argument (a strict `YYYY-MM-DD` string) -- this
function performs no separate validation of it beyond what that reused
call already does.

**Duplicate/non-positive explicit-date coordinates fail closed the same
way tenor-derived ones already did.** Two rows (from any source, mixed
tenor-only and explicit-date alike) that resolve to the same
`year_fraction` -- whether both are tenor labels, both are the same
calendar date, or one of each happens to coincide -- raise the same
existing duplicate-`year_fraction` error below. A `maturity_date` that
is not strictly after `as_of_date` raises via `year_fraction_to_expiry`
itself, before this function's own duplicate check ever runs.

**Tenor parsing:** delegates to the existing, reviewed
`pricing/bli_curve_tenor.py::tenor_to_year_fraction` -- this module does
not define, and does not use, any other tenor parser (not
`pricing/curve.py::tenor_to_years`, not a new one).

**Interpolation methodology, Black-76, and the overall pricing formula
are unchanged by this slice** -- only how a node's own `year_fraction`
coordinate is computed changed, and only for rows that opt in by
carrying an explicit `maturity_date`.

This module is not imported by, and does not change the behavior of,
`pricing/bli_pricing_engine.py::price_bli_mvp` -- `price_bli_mvp` (and
`bli_forward_clean_price.py`/`bli_standalone_option_pricing_inputs.py`,
the chain's other two live callers) do not yet pass `as_of_date` through
to `discount_factor_from_continuous_zero_curve`, so today's live pricing
paths still process only tenor-only curve points, exactly as before this
slice. Wiring `valuation_date` into those call sites so an explicit-date
Bloomberg curve point is reachable end-to-end through live pricing is a
separate, not-yet-authorized follow-up.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from shiori_pricing_lab.data.bli_snapshot import BLICurvePoint, BLICurveRateBasis
from shiori_pricing_lab.pricing.bli_curve_tenor import tenor_to_year_fraction
from shiori_pricing_lab.pricing.bli_valuation_time import year_fraction_to_expiry


@dataclass(frozen=True)
class BLIContinuousZeroCurveNode:
    """One validated, coordinate-resolved continuous-zero-rate curve node.

    ``source_maturity_date`` is ``None`` when this node's ``year_fraction``
    came from ``source_tenor`` via ``tenor_to_year_fraction`` (every node
    before Issue #165, and any future tenor-only row); it carries the
    source ``BLICurvePoint.maturity_date`` string when ``year_fraction``
    instead came from that explicit date via ``year_fraction_to_expiry``.
    """

    year_fraction: float
    zero_rate: float
    source_tenor: str
    source_curve_id: str
    source_maturity_date: str | None = None


def build_continuous_zero_curve_nodes(
    curve_points: Iterable[BLICurvePoint],
    *,
    as_of_date: str | None = None,
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
    curve's rows themselves (e.g. via `select_curve_points_by_purpose`).

    ``as_of_date`` (Issue #165 P1 follow-up) is the curve's own
    valuation/as-of reference date, required *only* if at least one row
    in ``curve_points`` carries an explicit ``maturity_date`` -- see the
    module docstring's "No silent fallback" section for the exact
    fail-closed contract. Rows with ``maturity_date is None`` never read
    ``as_of_date`` at all; a caller preparing a purely tenor-only curve
    (every curve this codebase has used until now) can omit it and gets
    identical behavior to before this parameter existed.
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
        if point.maturity_date is not None and as_of_date is None:
            raise ValueError(
                f"curve_id={point.curve_id!r} tenor={point.tenor!r} declares an explicit "
                f"maturity_date={point.maturity_date!r} but no as_of_date was supplied to "
                "compute its time coordinate -- pass the curve's own valuation/as-of date "
                "explicitly; it is never silently derived, defaulted, or reconstructed from "
                "tenor"
            )

    nodes = [
        BLIContinuousZeroCurveNode(
            year_fraction=(
                year_fraction_to_expiry(as_of_date, point.maturity_date)
                if point.maturity_date is not None
                else tenor_to_year_fraction(point.tenor)
            ),
            zero_rate=point.rate,
            source_tenor=point.tenor,
            source_curve_id=point.curve_id,
            source_maturity_date=point.maturity_date,
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
