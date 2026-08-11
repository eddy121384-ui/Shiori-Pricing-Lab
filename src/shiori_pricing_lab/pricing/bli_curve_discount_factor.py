"""BLI curve discount-factor resolver (docs/28 dependency chain).

Scope: a single narrow composition helper -- given a `BLICurvePoint`
collection, a `(currency, curve_purpose)` selector target, and a
target year fraction, return the discount factor for that target by
chaining the four already-reviewed helpers this dependency chain has
built, in order:

1. `pricing/bli_curve_selector.py::select_curve_points_by_purpose`
   (structural filter by `(currency, curve_purpose)`);
2. `pricing/bli_zero_curve_nodes.py::build_continuous_zero_curve_nodes`
   (rate-basis-gated, tenor-parsed, sorted zero-curve nodes);
3. `pricing/bli_zero_rate_interpolation.py::interpolate_continuous_zero_rate`
   (piecewise-linear interpolation on zero rates, in-range only);
4. `pricing/bli_discount_factor.py::continuous_discount_factor`
   (`exp(-zero_rate * year_fraction)`).

This module does not re-implement, duplicate, or re-validate any of
those four steps' own logic -- it composes them and lets their errors
propagate as-is. It does not parse tenors directly, does not read
`BLICurvePoint.rate`/`rate_basis` directly, does not implement
interpolation or discount-factor math itself, does not implement a
fallback-flag or structured-result contract, does not implement flat
extrapolation, and does not compute a forward clean price, PV, or
Black-76 value.

**``as_of_date`` (Issue #165 P1 follow-up):** forwarded, unmodified and
unvalidated by this module itself, to
`build_continuous_zero_curve_nodes`'s own `as_of_date` parameter -- see
that function's docstring for the exact fail-closed contract when a
selected row carries an explicit `maturity_date`. Optional, defaults to
`None`; every existing caller that never sets it keeps identical
behavior (a purely tenor-only curve never reads it).

**Live wiring (Issue #165 final live-wiring follow-up):** two separate
callers now pass `as_of_date=valuation_date` for their `OPTION_DISCOUNT_
CURVE` resolution -- `bli_pricing_engine.py::_price_bli_mvp_from_fields`
(the legacy `BLIMVPInputBundle` path's own composition; the standalone
OVME-aligned path is a **separate numeric composition** that does not
share `_price_bli_mvp_from_fields`, per that module's own docstring) and
`bli_standalone_option_pricing_inputs.py::_option_discount_factor_to_date`
(the standalone path's own, independent route to this same resolver).
Both use the same `valuation_date` each already uses to anchor every
other year-fraction it computes. `bli_forward_clean_price.py` (which
resolves `BOND_REFERENCE_CURVE`, not `OPTION_DISCOUNT_CURVE`) is
deliberately **not** wired here -- explicitly out of scope for Issue #165.
This module itself still does not change behavior for any curve whose
points are all tenor-only (`maturity_date is None`), which is every curve
this codebase has priced before Issue #165's Bloomberg ingestion.
"""

from __future__ import annotations

from collections.abc import Iterable

from shiori_pricing_lab.data.bli_snapshot import BLICurvePoint, BLICurvePurpose
from shiori_pricing_lab.pricing.bli_curve_selector import select_curve_points_by_purpose
from shiori_pricing_lab.pricing.bli_discount_factor import continuous_discount_factor
from shiori_pricing_lab.pricing.bli_zero_curve_nodes import build_continuous_zero_curve_nodes
from shiori_pricing_lab.pricing.bli_zero_rate_interpolation import (
    interpolate_continuous_zero_rate,
)
from shiori_pricing_lab.products.enums import Currency


def discount_factor_from_continuous_zero_curve(
    curve_points: Iterable[BLICurvePoint],
    *,
    currency: Currency | str,
    curve_purpose: BLICurvePurpose | str,
    target_year_fraction: float,
    as_of_date: str | None = None,
) -> float:
    """Return the discount factor at ``target_year_fraction`` for one curve.

    Composes, in order, `select_curve_points_by_purpose`,
    `build_continuous_zero_curve_nodes`,
    `interpolate_continuous_zero_rate`, and
    `continuous_discount_factor` -- nothing here re-validates or
    re-implements any of those steps; every error each one already
    raises (missing curve purpose/currency, a non-`BLICurvePoint`
    element, a non-`CONTINUOUS_ZERO_RATE` basis, an out-of-range
    target, an invalid target year fraction, a selected row with an
    explicit `maturity_date` but no `as_of_date`, ...) propagates
    unchanged. ``as_of_date`` is passed straight through to
    `build_continuous_zero_curve_nodes` -- see the module docstring.
    """

    selected_points = select_curve_points_by_purpose(
        curve_points,
        currency=currency,
        curve_purpose=curve_purpose,
    )
    nodes = build_continuous_zero_curve_nodes(selected_points, as_of_date=as_of_date)
    zero_rate = interpolate_continuous_zero_rate(nodes, target_year_fraction)
    return continuous_discount_factor(zero_rate, target_year_fraction)
