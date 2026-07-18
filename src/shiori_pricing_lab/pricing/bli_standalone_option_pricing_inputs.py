"""Focused OVME-aligned standalone bond-option pricing-input resolver (Issue #94).

Scope: one small, focused resolver that turns a
``BLIStandaloneBondOptionRequest`` into the exact set of already-resolved
Black-76 inputs the Eddy-approved Bloomberg (OVME) methodology needs for the
standalone European price-based cash-settled path (Issue #94 human
methodology approval, comment 5001749998; implementation authorization
5003670704):

1. explicit forward clean price (read from the snapshot's
   ``forward_clean_price_input`` -- never constructed from a spot price and a
   Bond Reference Curve, never a repo/financing derivation);
2. strike clean price (from the ``BondOption``);
3. accrued interest at the explicit bond forward settlement date, via the
   already-reviewed ``accrued_interest_per_100`` helper;
4. dirty forward / dirty strike (clean + that accrued interest);
5. fractional-timestamp ACT/ACT option time from the explicit pricing
   timestamp to the explicit expiry timestamp;
6. the effective reporting-date discount factor: the Option Discount Curve
   discount factor from pricing date to option settlement date, divided by
   the discount factor from pricing date to reporting date.

**A single focused resolver, not a framework.** It is shared by the
standalone fair-premium engine and available to the calibration safety gate,
so the same current methodology inputs are resolved in exactly one place. It
composes only already-reviewed helpers -- ``accrued_interest_per_100``
(``pricing/bli_quantlib_bond_adapter.py``),
``actual_actual_isda_year_fraction_between_datetimes``
(``pricing/bli_valuation_time.py``), and
``discount_factor_from_continuous_zero_curve``
(``pricing/bli_curve_discount_factor.py``) -- and adds no curve construction,
interpolation, bootstrap, extrapolation, repo/financing derivation, or
business-day/calendar rule of its own.

**Explicit dates and market inputs are authoritative.** ``reporting_date``,
``forward_settlement_date``, and ``option_settlement_date`` are taken exactly
as supplied on the request; none is derived from ``settlement_lag_days``,
Delivery Delay, weekends, holidays, or any calendar. The curve *coordinate*
for the reporting/settlement targets is ``(target_date - valuation_date).days
/ 365.0`` -- the existing continuous-zero-curve coordinate convention (the
same convention day-tenor curve nodes use), deliberately distinct from the
option's own ACT/ACT time. When ``reporting_date == valuation_date`` the
pricing-to-reporting discount factor is exactly ``1.0`` and no zero-tenor
curve interpolation is attempted.

This module creates no ``PricingResult`` and calls no pricing engine. It does
not read the system clock. Errors from the composed helpers propagate
unchanged (``ValueError`` for a curve out of range, a bad accrued-interest
date, etc.; ``BLIQuantLibNotAvailableError`` for a missing QuantLib install);
the caller decides how to map them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite

from shiori_pricing_lab.data._validation import _parse_iso_date
from shiori_pricing_lab.data.bli_snapshot import BLICurvePurpose
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.pricing.bli_curve_discount_factor import (
    discount_factor_from_continuous_zero_curve,
)
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import accrued_interest_per_100
from shiori_pricing_lab.pricing.bli_valuation_time import (
    actual_actual_isda_year_fraction_between_datetimes,
)


@dataclass(frozen=True)
class StandaloneOptionPricingInputs:
    """The already-resolved OVME-aligned Black-76 inputs for one standalone request.

    Every field is either read verbatim from the request or computed by an
    already-reviewed helper -- no value here is a fabricated fallback. The
    dirty values are the clean values plus the same
    ``accrued_interest_at_forward_settlement_per_100``.
    """

    forward_clean_price_per_100: float
    strike_clean_price_per_100: float
    accrued_interest_at_forward_settlement_per_100: float
    forward_dirty_price_per_100: float
    strike_dirty_price_per_100: float
    time_to_expiry_year_fraction: float
    pricing_to_reporting_discount_factor: float
    pricing_to_option_settlement_discount_factor: float
    effective_reporting_date_discount_factor: float


def _require_finite_positive(value: float, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    if not value > 0:
        raise ValueError(
            f"{field_name} must be positive, got {value!r} -- pricing is blocked rather "
            "than producing a fabricated option value"
        )
    return float(value)


def _option_discount_factor_to_date(
    request: BLIStandaloneBondOptionRequest, target_date: str, field_name: str
) -> float:
    """Return the Option Discount Curve DF from valuation date to ``target_date``.

    The curve coordinate is ``(target_date - valuation_date).days / 365.0``
    -- the existing continuous-zero-curve coordinate convention. A target on
    the valuation date itself gives ``1.0`` directly (no zero-tenor
    interpolation is attempted, matching Issue #94's explicit rule for the
    reporting date).
    """

    valuation = _parse_iso_date(request.valuation_date, "valuation_date")
    target = _parse_iso_date(target_date, field_name)
    days = (target - valuation).days
    if days == 0:
        return 1.0
    if days < 0:
        raise ValueError(
            f"{field_name} ({target_date!r}) must not be before valuation_date "
            f"({request.valuation_date!r})"
        )
    coordinate = days / 365.0
    return discount_factor_from_continuous_zero_curve(
        request.market_data_snapshot.curve_points,
        currency=request.bond_option.currency,
        curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
        target_year_fraction=coordinate,
    )


def resolve_standalone_option_pricing_inputs(
    request: BLIStandaloneBondOptionRequest,
) -> StandaloneOptionPricingInputs:
    """Resolve the OVME-aligned Black-76 inputs for ``request``.

    Composition order (Issue #94):

    1. read the explicit forward clean price from
       ``snapshot.forward_clean_price_input``;
    2. read the strike clean price from ``bond_option``;
    3. accrued interest at ``request.forward_settlement_date`` via
       ``accrued_interest_per_100``;
    4. dirty forward / dirty strike = clean + accrued interest;
    5. ACT/ACT option time from ``pricing_timestamp`` to ``expiry_timestamp``
       (the request already validated both as canonical offset-aware
       strings; parsed here with ``datetime.fromisoformat``, not
       re-validating that contract);
    6. Option Discount Curve DFs to reporting date and option settlement
       date, then the effective reporting-date DF ratio.

    Raises :class:`TypeError` for a non-``BLIStandaloneBondOptionRequest``.
    Propagates unchanged every error the composed helpers raise (curve out
    of range, invalid accrued-interest date, QuantLib unavailable, ...).
    Raises :class:`ValueError` if any resolved discount factor, or their
    ratio, is not finite and strictly positive -- signed rates are allowed,
    so a later DF is never required to be smaller than an earlier one.
    """

    if not isinstance(request, BLIStandaloneBondOptionRequest):
        raise TypeError(
            f"request must be a BLIStandaloneBondOptionRequest, got {type(request).__name__}"
        )

    snapshot = request.market_data_snapshot
    bond_option = request.bond_option

    forward_input = snapshot.forward_clean_price_input
    if forward_input is None:
        # Defensive: the request constructor already requires this. Never
        # fabricate a forward from a spot price / Bond Reference Curve.
        raise ValueError(
            "market_data_snapshot.forward_clean_price_input is required for the "
            "OVME-aligned standalone pricing path"
        )

    forward_clean_price = _require_finite_positive(
        forward_input.forward_clean_price_per_100, "forward_clean_price_per_100"
    )
    strike_clean_price = _require_finite_positive(
        bond_option.strike_price, "strike_clean_price_per_100"
    )

    accrued_interest = accrued_interest_per_100(
        request.resolved_bond_reference_data,
        as_of_date=request.forward_settlement_date,
    )
    forward_dirty_price = forward_clean_price + accrued_interest
    strike_dirty_price = strike_clean_price + accrued_interest

    pricing_timestamp = datetime.fromisoformat(request.pricing_timestamp)
    expiry_timestamp = datetime.fromisoformat(request.expiry_timestamp)
    time_to_expiry = actual_actual_isda_year_fraction_between_datetimes(
        pricing_timestamp, expiry_timestamp
    )

    pricing_to_reporting_df = _require_finite_positive(
        _option_discount_factor_to_date(request, request.reporting_date, "reporting_date"),
        "pricing_to_reporting_discount_factor",
    )
    pricing_to_option_settlement_df = _require_finite_positive(
        _option_discount_factor_to_date(
            request, request.option_settlement_date, "option_settlement_date"
        ),
        "pricing_to_option_settlement_discount_factor",
    )
    effective_df = _require_finite_positive(
        pricing_to_option_settlement_df / pricing_to_reporting_df,
        "effective_reporting_date_discount_factor",
    )

    return StandaloneOptionPricingInputs(
        forward_clean_price_per_100=forward_clean_price,
        strike_clean_price_per_100=strike_clean_price,
        accrued_interest_at_forward_settlement_per_100=accrued_interest,
        forward_dirty_price_per_100=forward_dirty_price,
        strike_dirty_price_per_100=strike_dirty_price,
        time_to_expiry_year_fraction=time_to_expiry,
        pricing_to_reporting_discount_factor=pricing_to_reporting_df,
        pricing_to_option_settlement_discount_factor=pricing_to_option_settlement_df,
        effective_reporting_date_discount_factor=effective_df,
    )
