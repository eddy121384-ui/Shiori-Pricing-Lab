"""BLI time-to-expiry ACT/365F year-fraction utility (docs/26 implementation slice).

Scope, per `docs/26_bli_first_valuation_slice_preflight.md` §5/§7: a single
pure date-arithmetic function plus a tiny bundle-reading convenience
wrapper. **No curve, discount-factor, forward-price, coupon-schedule,
accrued-interest, volatility, yield-conversion, Black-76, PV, or Greeks
logic is added here.** This module is not imported by, and does not
change the behavior of, `pricing/bli_pricing_engine.py::price_bli_mvp`.

Annex A §A.2.2 defines `T` as ACT/365F from the valuation date to the
option expiry date; §A.2.4 requires `T > 0`, i.e. pricing is blocked for
a same-day or already-expired option. This mirrors the existing
`pricing/irs_engine.py::_year_fraction`'s `ACT_365_FIXED` behavior
(`days / 365.0`), the reviewed precedent this slice mechanically adapts
for BLI.
"""

from __future__ import annotations

from datetime import date

from shiori_pricing_lab.data.bli_mvp_input_bundle import BLIMVPInputBundle


def _parse_iso_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} must be an ISO date string (YYYY-MM-DD), got {value!r}"
        ) from exc


def year_fraction_to_expiry(valuation_date: str, expiry_date: str) -> float:
    """Return the ACT/365F year fraction from ``valuation_date`` to ``expiry_date``.

    Both arguments are ISO ``YYYY-MM-DD`` date strings. Raises
    :class:`ValueError` if either string is not a valid ISO date, or if
    ``expiry_date`` is not strictly after ``valuation_date`` (Annex A
    §A.2.4: `T > 0`, otherwise pricing is blocked -- same-day expiry and
    already-expired options both raise rather than returning `0.0` or a
    negative value).
    """

    parsed_valuation_date = _parse_iso_date(valuation_date, "valuation_date")
    parsed_expiry_date = _parse_iso_date(expiry_date, "expiry_date")

    if parsed_expiry_date <= parsed_valuation_date:
        raise ValueError(
            f"expiry_date ({expiry_date!r}) must be strictly after "
            f"valuation_date ({valuation_date!r})"
        )

    days = (parsed_expiry_date - parsed_valuation_date).days
    return days / 365.0


def year_fraction_to_bond_option_expiry(bundle: BLIMVPInputBundle) -> float:
    """Return the ACT/365F year fraction to ``bundle``'s embedded bond option expiry.

    Reads only ``bundle.valuation_date`` and
    ``bundle.product.bond_option.expiry_date``. Does not mutate ``bundle``,
    call any pricing engine logic, or read market data, curves,
    volatility, credit spread, bond reference data, or deposit data.
    """

    return year_fraction_to_expiry(bundle.valuation_date, bundle.product.bond_option.expiry_date)
