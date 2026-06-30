"""Minimal USD-only IRS reference pricing engine.

Scope: a narrow deterministic MVP for synthetic USD fixed-vs-floating IRS.
It uses one ``RateCurve`` as both discount and forecast curve, no bootstrapping,
no calendars, no business-day adjustment, no historical fixings, and no DV01.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from shiori_pricing_lab.pricing.curve import RateCurve
from shiori_pricing_lab.pricing.engine import ENGINE_CONTRACT_VERSION
from shiori_pricing_lab.pricing.result import (
    PricingErrorCode,
    PricingMessage,
    PricingResult,
    PricingStatus,
    PricingWarningCode,
)
from shiori_pricing_lab.pricing.schedule import SchedulePeriod, generate_regular_schedule
from shiori_pricing_lab.products.enums import Currency, DayCount, PayReceive

if TYPE_CHECKING:
    from shiori_pricing_lab.data.snapshot import MarketDataSnapshot
    from shiori_pricing_lab.products.swaps import InterestRateSwap
    from shiori_pricing_lab.valuation.context import ValuationContext

ENGINE_NAME = "usd_irs_reference_engine"
ENGINE_VERSION = ENGINE_CONTRACT_VERSION
METHOD = "single_curve_simple_discount_forecast"


class IRSReferenceEngine:
    """Reference engine for the first supported IRS product slice."""

    def price(
        self,
        product: InterestRateSwap,
        valuation_context: ValuationContext,
        market_snapshot: MarketDataSnapshot,
    ) -> PricingResult:
        if product.currency is not Currency.USD:
            return _failed(
                product,
                valuation_context,
                market_snapshot,
                PricingErrorCode.INVALID_PRODUCT,
                "IRS reference engine supports USD products only",
                {"unsupported_currency": product.currency.value},
            )

        try:
            valuation_date = date.fromisoformat(str(valuation_context.valuation_date))
            effective = date.fromisoformat(product.effective_date)
            maturity = date.fromisoformat(product.maturity_date)
        except ValueError as exc:
            return _failed(
                product,
                valuation_context,
                market_snapshot,
                PricingErrorCode.INVALID_PRODUCT,
                f"invalid IRS date: {exc}",
            )

        is_forward_starting = valuation_date < effective

        if valuation_date > effective:
            return _failed(
                product,
                valuation_context,
                market_snapshot,
                PricingErrorCode.INVALID_PRODUCT,
                "IRS reference engine does not support in-progress swaps or historical fixings",
                {"valuation_date": str(valuation_date), "effective_date": product.effective_date},
            )

        unsupported_day_counts = [
            leg_name
            for leg_name, day_count in (
                ("fixed_leg", product.fixed_leg.day_count),
                ("floating_leg", product.floating_leg.day_count),
            )
            if day_count not in (DayCount.ACT_360, DayCount.ACT_365_FIXED)
        ]
        if unsupported_day_counts:
            return _failed(
                product,
                valuation_context,
                market_snapshot,
                PricingErrorCode.INVALID_PRODUCT,
                "unsupported day count for IRS reference engine",
                {"legs": unsupported_day_counts},
            )

        try:
            fixed_schedule = generate_regular_schedule(
                effective_date=effective,
                maturity_date=maturity,
                frequency=product.fixed_leg.payment_frequency,
            )
            floating_schedule = generate_regular_schedule(
                effective_date=effective,
                maturity_date=maturity,
                frequency=product.floating_leg.payment_frequency,
            )
        except ValueError as exc:
            return _failed(
                product,
                valuation_context,
                market_snapshot,
                PricingErrorCode.INVALID_PRODUCT,
                str(exc),
            )

        try:
            curve = RateCurve.from_snapshot(market_snapshot)
            curve_points = _usable_curve_points(curve)
        except (ValueError, KeyError, TypeError) as exc:
            return _failed(
                product,
                valuation_context,
                market_snapshot,
                PricingErrorCode.MISSING_MARKET_DATA,
                f"cannot build usable IRS curve from market snapshot: {exc}",
            )

        fixed_pv = _leg_pv(
            schedule=fixed_schedule,
            valuation_date=valuation_date,
            notional=product.notional,
            pay_receive=product.fixed_leg.pay_receive,
            day_count=product.fixed_leg.day_count,
            curve_points=curve_points,
            fixed_rate=product.fixed_leg.fixed_rate,
        )
        floating_pv = _leg_pv(
            schedule=floating_schedule,
            valuation_date=valuation_date,
            notional=product.notional,
            pay_receive=product.floating_leg.pay_receive,
            day_count=product.floating_leg.day_count,
            curve_points=curve_points,
            spread=product.floating_leg.spread,
        )
        pv = fixed_pv + floating_pv

        warnings = ()
        if is_forward_starting:
            warnings = (
                PricingMessage(
                    code=PricingWarningCode.FORWARD_STARTING,
                    message="valuation_date is before IRS effective_date",
                    detail={
                        "valuation_date": str(valuation_date),
                        "effective_date": product.effective_date,
                    },
                ),
            )

        return PricingResult(
            product_id=product.product_id,
            product_type=product.product_type,
            valuation_date=valuation_context.valuation_date,
            result_currency=valuation_context.reporting_currency,
            status=PricingStatus.SUCCESS_WITH_WARNINGS if warnings else PricingStatus.SUCCESS,
            engine_name=ENGINE_NAME,
            engine_version=ENGINE_VERSION,
            method=METHOD,
            market_data_as_of=market_snapshot.valuation_date,
            warnings=warnings,
            assumptions={
                "product_currency": product.currency.value,
                "curve_usage": "single curve used for both discount and forecast",
                "discount_factor_formula": "1 / (1 + zero_rate * year_fraction)",
                "zero_rate_interpolation": "linear in tenor year fractions; flat extrapolation",
                "forecast_rate_formula": "(df_start / df_end - 1) / accrual_fraction",
                "business_day_adjustment_applied": False,
                "calendar_applied": False,
                "bootstrapping_applied": False,
                "cashflows_returned": False,
            },
            pv=pv,
            dv01=None,
            cashflows=None,
            diagnostics={
                "fixed_leg_pv": fixed_pv,
                "floating_leg_pv": floating_pv,
                "fixed_period_count": len(fixed_schedule),
                "floating_period_count": len(floating_schedule),
            },
        )


def _failed(product, valuation_context, market_snapshot, code, message, detail=None):
    return PricingResult(
        product_id=product.product_id,
        product_type=product.product_type,
        valuation_date=valuation_context.valuation_date,
        result_currency=valuation_context.reporting_currency,
        status=PricingStatus.FAILED,
        engine_name=ENGINE_NAME,
        engine_version=ENGINE_VERSION,
        method=METHOD,
        market_data_as_of=market_snapshot.valuation_date,
        errors=(PricingMessage(code=code, message=message, detail=detail or {}),),
    )


def _usable_curve_points(curve: RateCurve) -> tuple[tuple[float, float], ...]:
    frame = curve.to_frame()
    required = {"years", "value"}
    if not required.issubset(frame.columns):
        raise ValueError("curve is missing years/value columns")
    cleaned = frame.dropna(subset=["years", "value"])
    if cleaned.empty:
        raise ValueError("curve has no tenor points with supported year fractions")
    return tuple((float(row.years), float(row.value)) for row in cleaned.itertuples())


def _leg_pv(
    *,
    schedule: tuple[SchedulePeriod, ...],
    valuation_date: date,
    notional: float,
    pay_receive: PayReceive,
    day_count: DayCount,
    curve_points: tuple[tuple[float, float], ...],
    fixed_rate: float | None = None,
    spread: float = 0.0,
) -> float:
    sign = 1.0 if pay_receive is PayReceive.RECEIVE else -1.0
    pv = 0.0
    for period in schedule:
        accrual = _year_fraction(period.start_date, period.end_date, day_count)
        payment_time = _year_fraction(valuation_date, period.end_date, DayCount.ACT_365_FIXED)
        df_pay = _discount_factor(payment_time, curve_points)
        if fixed_rate is None:
            start_time = max(
                _year_fraction(valuation_date, period.start_date, DayCount.ACT_365_FIXED),
                0.0,
            )
            end_time = _year_fraction(
                valuation_date, period.end_date, DayCount.ACT_365_FIXED
            )
            forward = (
                _discount_factor(start_time, curve_points)
                / _discount_factor(end_time, curve_points)
                - 1.0
            ) / accrual
            cashflow_rate = forward + spread
        else:
            cashflow_rate = fixed_rate
        pv += sign * notional * cashflow_rate * accrual * df_pay
    return pv


def _year_fraction(start: date, end: date, day_count: DayCount) -> float:
    days = (end - start).days
    if day_count is DayCount.ACT_360:
        return days / 360.0
    if day_count is DayCount.ACT_365_FIXED:
        return days / 365.0
    raise ValueError(f"unsupported day count: {day_count}")


def _discount_factor(years: float, curve_points: tuple[tuple[float, float], ...]) -> float:
    rate = _zero_rate(years, curve_points)
    return 1.0 / (1.0 + rate * years)


def _zero_rate(years: float, curve_points: tuple[tuple[float, float], ...]) -> float:
    if years <= curve_points[0][0]:
        return curve_points[0][1]
    for (left_years, left_rate), (right_years, right_rate) in zip(
        curve_points, curve_points[1:], strict=False
    ):
        if years <= right_years:
            weight = (years - left_years) / (right_years - left_years)
            return left_rate + weight * (right_rate - left_rate)
    return curve_points[-1][1]
