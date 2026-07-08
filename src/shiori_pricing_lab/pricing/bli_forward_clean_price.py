"""BLI forward clean price primitive (Issue #42, MVP-6 under #82).

Scope: compute the forward clean price per 100 for the bond leg embedded
in a `BLIMVPInputBundle`, per Annex A §A.5.2 exactly:

```text
Spot Dirty Price            = Spot Clean Price + AI(valuation_date)

PV(coupons before expiry)   = Sum Coupon_i * DF_bond_reference(valuation_date, coupon_date_i)
                               for coupon_date_i in (valuation_date, expiry_date]

Forward Dirty Price(expiry) = [Spot Dirty Price - PV(coupons before expiry)]
                               / DF_bond_reference(valuation_date, expiry_date)

Forward Clean Price(expiry) = Forward Dirty Price(expiry) - AI(expiry_date)
```

Per §A.5.3, both the coupon-PV step and the forward-discount step use the
**Bond Reference Curve** only -- never the Option Discount Curve. This
module hardcodes `BLICurvePurpose.BOND_REFERENCE_CURVE` internally and
does **not** expose `curve_purpose` as a caller parameter, so a caller
cannot accidentally mix the two curves Annex A says must not be mixed.

**No pricing math beyond this one formula is added here.** No Black-76,
no option premium, no Greeks, no volatility conversion, no
yield-to-price/price-to-yield conversion, no American exercise logic, no
physical delivery, no principal/redemption implementation. This module is
not imported by, and does not change the behavior of,
`pricing/bli_pricing_engine.py::price_bli_mvp` -- wiring this primitive
(and #41's required-input guard) into the engine is #44's job, not this
one.

**Composition, not reimplementation:** every building block below is an
already-reviewed helper from a prior slice, reused as-is and never
re-validated or re-implemented:

- coupon cashflows in `(valuation_date, expiry_date]`:
  `pricing.bli_quantlib_bond_adapter.coupon_flows_before` (#81);
- accrued interest at an explicit date:
  `pricing.bli_quantlib_bond_adapter.accrued_interest_per_100` (#81);
- ACT/365F year fraction to a target date:
  `pricing.bli_valuation_time.year_fraction_to_expiry` (docs/26) -- per
  Annex A §A.10.2 ("Day count: ACT/365F unified for curve-internal
  calculations"), this is reused for both each coupon date and the
  expiry date, independent of the bond's own `day_count` (which governs
  only accrual math inside `accrued_interest_per_100`, a separate
  concern);
- discount factor at a target year fraction:
  `pricing.bli_curve_discount_factor.discount_factor_from_continuous_zero_curve`
  (docs/28).

The only new logic in this module is the sum-of-discounted-coupons loop
and the four lines of §A.5.2 arithmetic (`_forward_clean_price_from_components`)
-- everything else is composition. No fallback, no silent default, and no
fabricated pricing number is ever produced: every missing/invalid input
(missing clean price, missing required curve, an out-of-range discount
factor, an unsupported maturity/principal boundary, an irregular coupon
grid, an ex-dividend-window date, ...) raises an already-reviewed
exception from the composed helpers, propagated unchanged.

**No system clock:** every date this module touches is an explicit
caller-supplied ISO date string (from `BondReferenceData`,
`BLIMVPInputBundle`, or a function argument) -- the current wall-clock
date/time is never read.
"""

from __future__ import annotations

from collections.abc import Iterable
from math import isfinite

from shiori_pricing_lab.data.bli_mvp_input_bundle import BLIMVPInputBundle
from shiori_pricing_lab.data.bli_snapshot import BLICurvePoint, BLICurvePurpose
from shiori_pricing_lab.pricing.bli_curve_discount_factor import (
    discount_factor_from_continuous_zero_curve,
)
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import (
    accrued_interest_per_100,
    coupon_flows_before,
)
from shiori_pricing_lab.pricing.bli_valuation_time import year_fraction_to_expiry
from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData

# Annex A SS A.5.3: PV(coupons before expiry) and the forward-discount step
# both use the Bond Reference Curve only -- never the Option Discount
# Curve. Hardcoded, not a caller parameter, so this rule cannot be
# accidentally violated by a caller passing the wrong curve_purpose.
_FORWARD_CLEAN_PRICE_CURVE_PURPOSE = BLICurvePurpose.BOND_REFERENCE_CURVE


def _require_finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    if not isfinite(value):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    return float(value)


def _forward_clean_price_from_components(
    *,
    spot_clean_price: float,
    accrued_interest_at_valuation: float,
    coupon_pv: float,
    expiry_discount_factor: float,
    accrued_interest_at_expiry: float,
) -> float:
    """Pure Annex A SS A.5.2 arithmetic -- no QuantLib, no curve lookups.

    Private, intentionally tiny: it encodes only the four lines of the
    formula (spot dirty, subtract coupon PV, divide by the expiry
    discount factor, subtract accrued interest at expiry) over
    already-resolved plain numbers. This is not a second public API --
    every real caller goes through `forward_clean_price_per_100` /
    `forward_clean_price_per_100_for_bundle`, which resolve these five
    numbers via the reviewed composition described in the module
    docstring. Kept separate purely so the Red-zone formula itself is
    testable without QuantLib being installed.
    """

    spot_clean_price = _require_finite_number(spot_clean_price, "spot_clean_price")
    accrued_interest_at_valuation = _require_finite_number(
        accrued_interest_at_valuation, "accrued_interest_at_valuation"
    )
    coupon_pv = _require_finite_number(coupon_pv, "coupon_pv")
    expiry_discount_factor = _require_finite_number(
        expiry_discount_factor, "expiry_discount_factor"
    )
    accrued_interest_at_expiry = _require_finite_number(
        accrued_interest_at_expiry, "accrued_interest_at_expiry"
    )

    if not expiry_discount_factor > 0:
        raise ValueError(
            "expiry_discount_factor must be positive, got "
            f"{expiry_discount_factor!r} -- a zero or negative discount factor "
            "cannot be divided into and never produces a pricing number"
        )

    spot_dirty_price = spot_clean_price + accrued_interest_at_valuation
    forward_dirty_price = (spot_dirty_price - coupon_pv) / expiry_discount_factor
    return forward_dirty_price - accrued_interest_at_expiry


def forward_clean_price_per_100(
    *,
    bond: BondReferenceData,
    spot_clean_price: float,
    valuation_date: str,
    expiry_date: str,
    curve_points: Iterable[BLICurvePoint],
) -> float:
    """Return the forward clean price per 100 for ``bond`` at ``expiry_date``.

    Composes `coupon_flows_before`, `accrued_interest_per_100`,
    `year_fraction_to_expiry`, and
    `discount_factor_from_continuous_zero_curve` exactly as Annex A
    §A.5.2 specifies (see module docstring), then applies the pure
    arithmetic in `_forward_clean_price_from_components`. Raises
    `TypeError` for a non-``BondReferenceData`` ``bond``; propagates
    every other error unchanged from the composed helpers (missing
    curve, out-of-range discount factor, irregular coupon grid,
    unsupported maturity/principal boundary, ex-dividend-window date,
    ...). ``curve_points`` is read only for ``bond.currency`` /
    ``BLICurvePurpose.BOND_REFERENCE_CURVE`` rows -- no other currency or
    curve purpose is ever selected.
    """

    if not isinstance(bond, BondReferenceData):
        raise TypeError(f"bond must be a BondReferenceData, got {type(bond).__name__}")

    spot_clean_price = _require_finite_number(spot_clean_price, "spot_clean_price")
    if not spot_clean_price > 0:
        raise ValueError(f"spot_clean_price must be positive, got {spot_clean_price!r}")

    curve_points = tuple(curve_points)

    accrued_interest_at_valuation = accrued_interest_per_100(bond, as_of_date=valuation_date)

    coupon_flows = coupon_flows_before(
        bond, after_date=valuation_date, on_or_before_date=expiry_date
    )
    coupon_pv = 0.0
    for flow in coupon_flows:
        coupon_year_fraction = year_fraction_to_expiry(valuation_date, flow.payment_date)
        coupon_discount_factor = discount_factor_from_continuous_zero_curve(
            curve_points,
            currency=bond.currency,
            curve_purpose=_FORWARD_CLEAN_PRICE_CURVE_PURPOSE,
            target_year_fraction=coupon_year_fraction,
        )
        coupon_pv += flow.amount_per_100 * coupon_discount_factor

    expiry_year_fraction = year_fraction_to_expiry(valuation_date, expiry_date)
    expiry_discount_factor = discount_factor_from_continuous_zero_curve(
        curve_points,
        currency=bond.currency,
        curve_purpose=_FORWARD_CLEAN_PRICE_CURVE_PURPOSE,
        target_year_fraction=expiry_year_fraction,
    )

    accrued_interest_at_expiry = accrued_interest_per_100(bond, as_of_date=expiry_date)

    return _forward_clean_price_from_components(
        spot_clean_price=spot_clean_price,
        accrued_interest_at_valuation=accrued_interest_at_valuation,
        coupon_pv=coupon_pv,
        expiry_discount_factor=expiry_discount_factor,
        accrued_interest_at_expiry=accrued_interest_at_expiry,
    )


def forward_clean_price_per_100_for_bundle(bundle: BLIMVPInputBundle) -> float:
    """Return the forward clean price per 100 for ``bundle``'s embedded bond option.

    Thin bundle-reading convenience wrapper (mirrors
    `pricing.bli_valuation_time.year_fraction_to_bond_option_expiry`'s own
    precedent): reads `bundle.resolved_bond_reference_data`,
    `bundle.market_data_snapshot.bond_quote.clean_price_per_100`,
    `bundle.valuation_date`, `bundle.product.bond_option.expiry_date`, and
    `bundle.market_data_snapshot.curve_points`, then delegates to
    `forward_clean_price_per_100`. Does **not** call #41's
    `check_bli_mvp_required_inputs` -- matching every other module in this
    dependency chain, that pre-check is #44's job, not this primitive's.
    Raises `TypeError` for a non-``BLIMVPInputBundle`` argument, and
    `ValueError` if `clean_price_per_100` is absent (a yield-only bond
    quote is not usable by this MVP slice).
    """

    if not isinstance(bundle, BLIMVPInputBundle):
        raise TypeError(f"bundle must be a BLIMVPInputBundle, got {type(bundle).__name__}")

    spot_clean_price = bundle.market_data_snapshot.bond_quote.clean_price_per_100
    if spot_clean_price is None:
        raise ValueError(
            "market_data_snapshot.bond_quote.clean_price_per_100 must be present "
            "(a yield-only bond quote is not usable by this MVP slice)"
        )

    return forward_clean_price_per_100(
        bond=bundle.resolved_bond_reference_data,
        spot_clean_price=spot_clean_price,
        valuation_date=bundle.valuation_date,
        expiry_date=bundle.product.bond_option.expiry_date,
        curve_points=bundle.market_data_snapshot.curve_points,
    )
