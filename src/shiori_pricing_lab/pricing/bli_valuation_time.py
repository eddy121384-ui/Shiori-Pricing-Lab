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
for BLI. ``year_fraction_to_expiry`` and its bundle wrapper are left
exactly as-is: they continue to serve the legacy bundle pricing path
unchanged.

The OVME-aligned standalone path (Issue #94) needs a *different* option
time: fractional-timestamp ACT/ACT (ISDA) from an explicit pricing
timestamp to an explicit expiry timestamp. That is a separate pure
helper, :func:`actual_actual_isda_year_fraction_between_datetimes`,
added below -- it does not change, and is not called by, the date-only
``year_fraction_to_expiry`` the bundle path uses.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from shiori_pricing_lab.data.bli_mvp_input_bundle import BLIMVPInputBundle

_ISO_DATE_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _is_leap_year(year: int) -> int:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def actual_actual_isda_year_fraction_between_datetimes(
    start: datetime, end: datetime
) -> float:
    """Return the ACT/ACT (ISDA) year fraction between two aware datetimes.

    Fractional-timestamp option time for the OVME-aligned standalone path
    (Issue #94 human methodology approval, comment 5001749998): unlike the
    date-only :func:`year_fraction_to_expiry` (which stays exactly as-is for
    the legacy bundle path), this uses the exact elapsed seconds between two
    timezone-aware instants and splits the interval at calendar-year
    boundaries per ACT/ACT ISDA.

    Both ``start`` and ``end`` must be timezone-aware ``datetime`` values
    (a naive value raises); ``end`` must be strictly after ``start``.
    ``end`` is first converted into ``start``'s timezone, and the interval
    is then split at each ``1 January 00:00`` boundary *in that timezone*.
    Each segment contributes ``segment_seconds / 86400 / days_in_that_year``,
    where ``days_in_that_year`` is 366 for a leap year and 365 otherwise.
    Never reads the system clock; adds no third-party dependency.
    """

    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise ValueError("start and end must both be datetime instances")
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be timezone-aware (an explicit UTC offset)")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("end must be timezone-aware (an explicit UTC offset)")

    # Compare and segment in start's timezone so calendar-year boundaries
    # are evaluated consistently in one frame (an aware comparison itself is
    # instant-based, but the year-boundary split must use one local frame).
    end_in_start_tz = end.astimezone(start.tzinfo)
    if end_in_start_tz <= start:
        raise ValueError("end must be strictly after start")

    total = 0.0
    cursor = start
    while cursor < end_in_start_tz:
        next_year_boundary = datetime(cursor.year + 1, 1, 1, tzinfo=cursor.tzinfo)
        segment_end = min(end_in_start_tz, next_year_boundary)
        segment_seconds = (segment_end - cursor).total_seconds()
        days_in_year = 366.0 if _is_leap_year(cursor.year) else 365.0
        total += segment_seconds / 86400.0 / days_in_year
        cursor = segment_end
    return total


def _parse_iso_date(value: str, field_name: str) -> date:
    # date.fromisoformat also accepts ISO basic ("20260706") and ISO week-date
    # ("2026-W28-1") forms on Python 3.11 -- this slice's contract is strict
    # YYYY-MM-DD only, so the shape is checked before calendar validation.
    if not isinstance(value, str) or not _ISO_DATE_SHAPE.fullmatch(value):
        raise ValueError(
            f"{field_name} must be an ISO date string (YYYY-MM-DD), got {value!r}"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
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
