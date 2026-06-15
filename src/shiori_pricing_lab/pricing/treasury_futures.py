"""Treasury futures price / CTD yield conversion helpers.

The calculations here are deliberately explicit and lightweight. They are intended
for trader workflow checks, not official valuation.

Methodology anchor:
CME Treasury Analytics defines the futures implied yield as the yield-to-maturity
of the CTD cash security using:
    bond price = futures price * conversion factor + accrued coupon interest

This module treats the futures-to-cash conversion as a clean-price relationship
and then computes accrued interest separately inside the bond pricing routines.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Any


@dataclass(frozen=True)
class CTDInstrument:
    """Normalized CTD data used by the converter.

    Coupon and yield values are decimal rates: 0.0425 means 4.25%.
    Prices are per 100 par.
    """

    contract_code: str
    futures_symbol: str
    delivery_month: str
    ctd_cusip: str
    coupon_rate: float
    maturity_date: date
    conversion_factor: float
    last_delivery_date: date
    source: str = "manual"
    updated_at: str | None = None
    description: str | None = None


def parse_iso_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def parse_treasury_futures_price(value: str | int | float) -> float:
    """Parse CBOT Treasury futures price notation into decimal points.

    Supported examples:
    - ``110-16``  -> 110 + 16/32
    - ``110-16+`` -> 110 + 16.5/32
    - ``110-165`` -> 110 + 16.5/32
    - ``110-16.5`` -> 110 + 16.5/32
    - ``110.515625`` -> already-decimal price

    The compact ``110-165`` convention is interpreted as the last digit being
    tenths of a 32nd. This captures the common half-32nd display where ``5``
    means ``+``. For higher precision contracts, prefer the decimal minor form.
    """

    if isinstance(value, (int, float)):
        price = float(value)
        if not isfinite(price):
            raise ValueError("Price must be finite.")
        return price

    text = value.strip().replace("'", "-")
    if not text:
        raise ValueError("Price cannot be empty.")

    if "-" not in text:
        return float(text)

    whole_text, frac_text = text.split("-", 1)
    whole = int(whole_text)
    frac_text = frac_text.strip()

    plus = frac_text.endswith("+")
    if plus:
        frac_text = frac_text[:-1]

    if "." in frac_text:
        thirty_seconds = float(frac_text)
    elif len(frac_text) >= 3:
        thirty_seconds = int(frac_text[:-1]) + int(frac_text[-1]) / 10
    else:
        thirty_seconds = int(frac_text or "0")

    if plus:
        thirty_seconds += 0.5

    if thirty_seconds < 0 or thirty_seconds >= 32:
        raise ValueError("32nds component must be in [0, 32).")

    return whole + thirty_seconds / 32


def format_treasury_futures_price(price: float, tick_64ths: bool = True) -> str:
    """Format a decimal Treasury futures price as CBOT 32nds notation."""

    if not isfinite(price):
        raise ValueError("Price must be finite.")

    whole = int(price)
    frac = price - whole
    thirty_seconds = frac * 32

    if tick_64ths:
        half_ticks = round(thirty_seconds * 2)
        if half_ticks >= 64:
            whole += 1
            half_ticks -= 64
        main = half_ticks // 2
        half = half_ticks % 2
        return f"{whole}-{main:02d}{'+' if half else ''}"

    rounded = round(thirty_seconds)
    if rounded >= 32:
        whole += 1
        rounded -= 32
    return f"{whole}-{rounded:02d}"


def clean_price_from_futures(
    futures_price: str | int | float,
    conversion_factor: float,
    net_basis: float = 0.0,
) -> float:
    """Approximate CTD clean price from futures price and conversion factor.

    ``net_basis`` is added in price points per 100 par. Leave it at zero for the
    pure exchange conversion. Use a positive value when the cash bond trades
    richer than futures-implied price.
    """

    if conversion_factor <= 0:
        raise ValueError("Conversion factor must be positive.")

    return parse_treasury_futures_price(futures_price) * conversion_factor + net_basis


def futures_price_from_clean_price(
    clean_price: float,
    conversion_factor: float,
    net_basis: float = 0.0,
) -> float:
    if conversion_factor <= 0:
        raise ValueError("Conversion factor must be positive.")

    return (clean_price - net_basis) / conversion_factor


def _add_months(input_date: date, months: int) -> date:
    month_index = input_date.month - 1 + months
    year = input_date.year + month_index // 12
    month = month_index % 12 + 1

    days_by_month = [31, 29 if _is_leap_year(year) else 28, 31, 30, 31, 30,
                     31, 31, 30, 31, 30, 31]
    day = min(input_date.day, days_by_month[month - 1])
    return date(year, month, day)


def _is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def coupon_bounds(settlement_date: date, maturity_date: date, frequency: int = 2) -> tuple[date, date]:
    """Return previous and next coupon dates around settlement."""

    if settlement_date >= maturity_date:
        raise ValueError("Settlement date must be before maturity date.")
    if frequency != 2:
        raise NotImplementedError("Only semiannual coupons are currently supported.")

    months = 12 // frequency
    next_coupon = maturity_date
    while _add_months(next_coupon, -months) > settlement_date:
        next_coupon = _add_months(next_coupon, -months)

    previous_coupon = _add_months(next_coupon, -months)
    return previous_coupon, next_coupon


def cashflow_dates_after_settlement(
    settlement_date: date,
    maturity_date: date,
    frequency: int = 2,
) -> list[date]:
    if frequency != 2:
        raise NotImplementedError("Only semiannual coupons are currently supported.")

    previous_coupon, next_coupon = coupon_bounds(settlement_date, maturity_date, frequency)
    del previous_coupon

    dates: list[date] = []
    current = next_coupon
    months = 12 // frequency
    while current < maturity_date:
        dates.append(current)
        current = _add_months(current, months)
    dates.append(maturity_date)
    return dates


def accrued_interest(
    settlement_date: date,
    maturity_date: date,
    coupon_rate: float,
    frequency: int = 2,
    par: float = 100.0,
) -> float:
    """Actual/Actual coupon accrual approximation for US Treasury-style coupons."""

    previous_coupon, next_coupon = coupon_bounds(settlement_date, maturity_date, frequency)
    elapsed = (settlement_date - previous_coupon).days
    period = (next_coupon - previous_coupon).days
    coupon_cashflow = par * coupon_rate / frequency
    return coupon_cashflow * elapsed / period


def bond_clean_price_from_yield(
    yield_rate: float,
    settlement_date: date,
    maturity_date: date,
    coupon_rate: float,
    frequency: int = 2,
    par: float = 100.0,
) -> float:
    """Return clean price per 100 par from yield-to-maturity.

    Yield and coupon are decimal rates. Example: 4.25% is ``0.0425``.
    """

    if yield_rate <= -0.99:
        raise ValueError("Yield is too negative for periodic discounting.")

    previous_coupon, next_coupon = coupon_bounds(settlement_date, maturity_date, frequency)
    del previous_coupon

    coupon_dates = cashflow_dates_after_settlement(settlement_date, maturity_date, frequency)
    first_period_days = (next_coupon - settlement_date).days
    coupon_period_days = (next_coupon - _add_months(next_coupon, -(12 // frequency))).days
    first_exponent = first_period_days / coupon_period_days

    period_yield = yield_rate / frequency
    coupon_cashflow = par * coupon_rate / frequency

    dirty_price = 0.0
    for index, cashflow_date in enumerate(coupon_dates):
        exponent = first_exponent + index
        cashflow = coupon_cashflow
        if cashflow_date == maturity_date:
            cashflow += par
        dirty_price += cashflow / ((1 + period_yield) ** exponent)

    return dirty_price - accrued_interest(settlement_date, maturity_date, coupon_rate, frequency, par)


def yield_from_bond_clean_price(
    clean_price: float,
    settlement_date: date,
    maturity_date: date,
    coupon_rate: float,
    frequency: int = 2,
    par: float = 100.0,
    lower: float = -0.05,
    upper: float = 0.25,
    tolerance: float = 1e-10,
    max_iterations: int = 200,
) -> float:
    """Solve yield-to-maturity from clean price using bisection."""

    if clean_price <= 0:
        raise ValueError("Clean price must be positive.")

    def objective(rate: float) -> float:
        return bond_clean_price_from_yield(
            rate,
            settlement_date,
            maturity_date,
            coupon_rate,
            frequency,
            par,
        ) - clean_price

    low = lower
    high = upper
    low_value = objective(low)
    high_value = objective(high)

    # Expand the bracket if needed. This keeps the helper usable for distressed
    # or very rich prices without hiding the method.
    while low_value * high_value > 0 and high < 1.0:
        high += 0.25
        high_value = objective(high)

    if low_value * high_value > 0:
        raise ValueError("Could not bracket a yield solution for the supplied price.")

    for _ in range(max_iterations):
        mid = (low + high) / 2
        mid_value = objective(mid)

        if abs(mid_value) < tolerance:
            return mid

        if low_value * mid_value <= 0:
            high = mid
            high_value = mid_value
        else:
            low = mid
            low_value = mid_value

    return (low + high) / 2


def yield_from_futures_price(
    futures_price: str | int | float,
    conversion_factor: float,
    settlement_date: date,
    maturity_date: date,
    coupon_rate: float,
    net_basis: float = 0.0,
    frequency: int = 2,
    par: float = 100.0,
) -> float:
    clean_price = clean_price_from_futures(futures_price, conversion_factor, net_basis)
    return yield_from_bond_clean_price(
        clean_price,
        settlement_date,
        maturity_date,
        coupon_rate,
        frequency,
        par,
    )


def futures_price_from_target_yield(
    target_yield: float,
    conversion_factor: float,
    settlement_date: date,
    maturity_date: date,
    coupon_rate: float,
    net_basis: float = 0.0,
    frequency: int = 2,
    par: float = 100.0,
) -> float:
    clean_price = bond_clean_price_from_yield(
        target_yield,
        settlement_date,
        maturity_date,
        coupon_rate,
        frequency,
        par,
    )
    return futures_price_from_clean_price(clean_price, conversion_factor, net_basis)


def ctd_from_dict(payload: dict[str, Any]) -> CTDInstrument:
    """Create ``CTDInstrument`` from the normalized JSON cache schema."""

    return CTDInstrument(
        contract_code=str(payload["contract_code"]),
        futures_symbol=str(payload.get("futures_symbol", payload["contract_code"][:2])),
        delivery_month=str(payload.get("delivery_month", "")),
        ctd_cusip=str(payload.get("ctd_cusip", "")),
        coupon_rate=float(payload["coupon_rate"]),
        maturity_date=parse_iso_date(payload["maturity_date"]),
        conversion_factor=float(payload["conversion_factor"]),
        last_delivery_date=parse_iso_date(payload["last_delivery_date"]),
        source=str(payload.get("source", "manual")),
        updated_at=payload.get("updated_at"),
        description=payload.get("description"),
    )
