"""Deterministic regular schedule helpers for pricing engines.

This helper deliberately does not know about business-day calendars, holidays,
or the system date. It only generates clean, unadjusted regular periods and
raises when a stub would be required.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from shiori_pricing_lab.products.enums import Frequency


@dataclass(frozen=True)
class SchedulePeriod:
    """One unadjusted coupon/accrual period."""

    start_date: date
    end_date: date


_FREQUENCY_MONTHS = {
    Frequency.MONTHLY: 1,
    Frequency.QUARTERLY: 3,
    Frequency.SEMI_ANNUAL: 6,
    Frequency.ANNUAL: 12,
}


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    try:
        return date(year, month, value.day)
    except ValueError as exc:
        raise ValueError(
            "regular schedule generation does not support month-end date adjustment"
        ) from exc


def generate_regular_schedule(
    *,
    effective_date: date,
    maturity_date: date,
    frequency: Frequency,
) -> tuple[SchedulePeriod, ...]:
    """Generate unadjusted regular periods or raise for unsupported/stub schedules."""

    if frequency not in _FREQUENCY_MONTHS:
        raise ValueError(f"unsupported payment frequency for regular schedule: {frequency}")
    if maturity_date <= effective_date:
        raise ValueError("maturity_date must be after effective_date")

    months = _FREQUENCY_MONTHS[frequency]
    periods: list[SchedulePeriod] = []
    start = effective_date
    while start < maturity_date:
        end = _add_months(start, months)
        if end > maturity_date:
            raise ValueError(
                "effective_date to maturity_date does not divide into clean regular periods"
            )
        periods.append(SchedulePeriod(start_date=start, end_date=end))
        start = end

    if start != maturity_date:
        raise ValueError(
            "effective_date to maturity_date does not divide into clean regular periods"
        )
    return tuple(periods)
