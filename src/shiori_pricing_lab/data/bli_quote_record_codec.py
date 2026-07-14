"""Strict codec for the canonical ``BLIQuoteRecord`` contract."""

from __future__ import annotations

import json
import math
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any

from shiori_pricing_lab.data.bli_benchmark_quote import (
    BLIBenchmarkQuote,
    BLIBenchmarkQuoteSide,
    BLIBenchmarkSourceType,
)
from shiori_pricing_lab.data.bli_quote_record import (
    BLI_QUOTE_RECORD_SCHEMA_VERSION,
    BLIQuoteRecord,
)
from shiori_pricing_lab.data.bli_snapshot import (
    BLIBondQuote,
    BLICreditSpreadInput,
    BLICreditSpreadTreatment,
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIDepositRateObservation,
    BLIMarketDataSnapshot,
    BLIMarketDataStatus,
    BLIQuoteBasis,
    BLIVolatilityBasis,
    BLIVolatilityInput,
)
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.pricing.bli_benchmark_comparison import (
    BLIBenchmarkComparisonMetric,
    BLIBenchmarkComparisonReason,
    BLIBenchmarkComparisonResult,
    BLIBenchmarkComparisonStatus,
)
from shiori_pricing_lab.pricing.bli_implied_price_vol_calibration import (
    BLIImpliedPriceVolCalibrationReason,
    BLIImpliedPriceVolCalibrationResult,
    BLIImpliedPriceVolCalibrationStatus,
)
from shiori_pricing_lab.pricing.bli_implied_price_vol_solver import (
    BLIImpliedPriceVolSolverReason,
    BLIImpliedPriceVolSolverResult,
    BLIImpliedPriceVolSolverStatus,
)
from shiori_pricing_lab.pricing.result import (
    PricingErrorCode,
    PricingMessage,
    PricingResult,
    PricingStatus,
    PricingWarningCode,
)
from shiori_pricing_lab.products.bond_option import BondOption
from shiori_pricing_lab.products.enums import (
    BondYieldConvention,
    BusinessDayConvention,
    Currency,
    DayCount,
    ExerciseStyle,
    Frequency,
    OptionType,
    PayoffBasis,
    Position,
    SettlementType,
    TreasuryFTPQuoteSide,
    TreasuryFTPTenor,
)
from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData
from shiori_pricing_lab.reference_data.enums import BondStatus, BondType

_CLASS_KEY = "__class__"
_TUPLE_KEY = "__tuple__"
_KNOWN_TAGGED_CLASSES = {
    "BLIStandaloneBondOptionRequest",
    "BondOption",
    "BondReferenceData",
    "BLIMarketDataSnapshot",
    "BLIBondQuote",
    "BLICurvePoint",
    "BLIDepositRateObservation",
    "BLIVolatilityInput",
    "BLICreditSpreadInput",
    "PricingMessage",
    "PricingResult",
    "BLIBenchmarkQuote",
    "BLIBenchmarkComparisonResult",
    "BLIImpliedPriceVolCalibrationResult",
    "BLIImpliedPriceVolSolverResult",
}


def _enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_enum_value(item) for item in value]
    if isinstance(value, list):
        return [_enum_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _enum_value(item) for key, item in value.items()}
    return value


def _encode_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _tagged(type(value), value)
    if isinstance(value, tuple):
        return {_TUPLE_KEY: [_encode_value(item) for item in value]}
    if isinstance(value, list):
        return [_encode_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _encode_value(item) for key, item in value.items()}
    return value


def _decode_value(value: Any) -> Any:
    if isinstance(value, dict):
        if _TUPLE_KEY in value:
            if set(value) != {_TUPLE_KEY} or not isinstance(value[_TUPLE_KEY], list):
                raise ValueError("malformed tuple tag payload")
            return tuple(_decode_value(item) for item in value[_TUPLE_KEY])
        if _CLASS_KEY in value:
            class_name = value[_CLASS_KEY]
            if class_name in _KNOWN_TAGGED_CLASSES:
                raise ValueError(f"unexpected tagged dataclass payload {class_name!r}")
            raise ValueError(f"unknown tagged class {class_name!r}")
        return {key: _decode_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_value(item) for item in value]
    return value


def _tagged(cls: type, obj: Any) -> dict[str, object]:
    payload = {_CLASS_KEY: cls.__name__}
    for field in fields(cls):
        payload[field.name] = _encode_value(getattr(obj, field.name))
    return payload


def _object(payload: object, name: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be an object")
    _reject_non_finite(payload, name)
    return payload


def _require_fields(payload: dict[str, Any], cls: type, name: str, *, tagged: bool = True) -> None:
    expected = {field.name for field in fields(cls)}
    if tagged:
        expected = expected | {_CLASS_KEY}
        if payload.get(_CLASS_KEY) != cls.__name__:
            raise ValueError(f"{name} must carry {_CLASS_KEY}={cls.__name__!r}")
    actual = set(payload)
    if actual != expected:
        raise ValueError(
            f"{name} fields mismatch: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _reject_non_finite(value: object, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path} contains non-finite number {value!r}")
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_non_finite(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_non_finite(item, f"{path}[{index}]")


def _tuple(payload: object, name: str, decode_item=lambda item: item) -> tuple[Any, ...]:
    if not isinstance(payload, dict) or set(payload) != {_TUPLE_KEY}:
        raise ValueError(f"{name} must be an explicit tuple tag")
    items = payload[_TUPLE_KEY]
    if not isinstance(items, list):
        raise ValueError(f"{name} tuple tag must contain an array")
    return tuple(decode_item(item) for item in items)


def _message(payload: object) -> PricingMessage:
    data = _object(payload, "PricingMessage")
    _require_fields(data, PricingMessage, "PricingMessage")
    code_value = data["code"]
    try:
        code = PricingErrorCode(code_value)
    except ValueError:
        code = PricingWarningCode(code_value)
    return PricingMessage(code=code, message=data["message"], detail=_decode_value(data["detail"]))


def _bond_option(payload: object) -> BondOption:
    data = _object(payload, "BondOption")
    _require_fields(data, BondOption, "BondOption")
    if data["product_type"] != "BOND_OPTION":
        raise ValueError("BondOption.product_type must be 'BOND_OPTION'")
    return BondOption(
        product_id=data["product_id"],
        underlying_isin=data["underlying_isin"],
        currency=Currency(data["currency"]),
        payoff_basis=PayoffBasis(data["payoff_basis"]),
        option_type=OptionType(data["option_type"]),
        exercise_style=ExerciseStyle(data["exercise_style"]),
        settlement_type=SettlementType(data["settlement_type"]),
        settlement_lag_days=data["settlement_lag_days"],
        expiry_date=data["expiry_date"],
        notional=data["notional"],
        position=Position(data["position"]),
        strike_price=data["strike_price"],
        strike_yield=data["strike_yield"],
        exercise_start_date=data["exercise_start_date"],
    )


def _bond_reference_data(payload: object) -> BondReferenceData:
    data = _object(payload, "BondReferenceData")
    _require_fields(data, BondReferenceData, "BondReferenceData")
    return BondReferenceData(
        isin=data["isin"],
        issuer=data["issuer"],
        currency=Currency(data["currency"]),
        coupon=data["coupon"],
        coupon_frequency=Frequency(data["coupon_frequency"]),
        maturity_date=data["maturity_date"],
        issue_date=data["issue_date"],
        day_count=DayCount(data["day_count"]),
        business_day_convention=BusinessDayConvention(data["business_day_convention"]),
        redemption_amount=data["redemption_amount"],
        callable_flag=data["callable_flag"],
        sinkable_flag=data["sinkable_flag"],
        bond_type=BondType(data["bond_type"]),
        yield_convention=BondYieldConvention(data["yield_convention"]),
        ex_dividend_days=data["ex_dividend_days"],
        first_coupon_date=data["first_coupon_date"],
        last_coupon_date=data["last_coupon_date"],
        status=BondStatus(data["status"]),
    )


def _bli_bond_quote(payload: object) -> BLIBondQuote:
    data = _object(payload, "BLIBondQuote")
    _require_fields(data, BLIBondQuote, "BLIBondQuote")
    return BLIBondQuote(
        isin=data["isin"],
        currency=Currency(data["currency"]),
        price_type=BLIQuoteBasis(data["price_type"]),
        quote_side=TreasuryFTPQuoteSide(data["quote_side"]),
        source_system=data["source_system"],
        status=BLIMarketDataStatus(data["status"]),
        clean_price_per_100=data["clean_price_per_100"],
        yield_value=data["yield_value"],
        accrued_interest_per_100=data["accrued_interest_per_100"],
    )


def _curve_point(payload: object) -> BLICurvePoint:
    data = _object(payload, "BLICurvePoint")
    _require_fields(data, BLICurvePoint, "BLICurvePoint")
    return BLICurvePoint(
        curve_id=data["curve_id"],
        curve_name=data["curve_name"],
        currency=Currency(data["currency"]),
        curve_purpose=BLICurvePurpose(data["curve_purpose"]),
        tenor=data["tenor"],
        rate=data["rate"],
        rate_basis=BLICurveRateBasis(data["rate_basis"]),
        source_system=data["source_system"],
        status=BLIMarketDataStatus(data["status"]),
    )


def _deposit_rate(payload: object) -> BLIDepositRateObservation:
    data = _object(payload, "BLIDepositRateObservation")
    _require_fields(data, BLIDepositRateObservation, "BLIDepositRateObservation")
    return BLIDepositRateObservation(
        currency=Currency(data["currency"]),
        tenor=TreasuryFTPTenor(data["tenor"]),
        quote_side=TreasuryFTPQuoteSide(data["quote_side"]),
        ftp_rate_percent_value=data["ftp_rate_percent_value"],
        ftp_rate_decimal_value=data["ftp_rate_decimal_value"],
        source_system=data["source_system"],
        status=BLIMarketDataStatus(data["status"]),
    )


def _volatility(payload: object) -> BLIVolatilityInput:
    data = _object(payload, "BLIVolatilityInput")
    _require_fields(data, BLIVolatilityInput, "BLIVolatilityInput")
    return BLIVolatilityInput(
        volatility=data["volatility"],
        volatility_basis=BLIVolatilityBasis(data["volatility_basis"]),
        source_system=data["source_system"],
        status=BLIMarketDataStatus(data["status"]),
        override_or_fallback_audit=data["override_or_fallback_audit"],
    )


def _credit_spread(payload: object) -> BLICreditSpreadInput:
    data = _object(payload, "BLICreditSpreadInput")
    _require_fields(data, BLICreditSpreadInput, "BLICreditSpreadInput")
    return BLICreditSpreadInput(
        spread_treatment=BLICreditSpreadTreatment(data["spread_treatment"]),
        source_system=data["source_system"],
        status=BLIMarketDataStatus(data["status"]),
        credit_spread=data["credit_spread"],
        credit_spread_basis=data["credit_spread_basis"],
        override_or_fallback_audit=data["override_or_fallback_audit"],
    )


def _snapshot(payload: object) -> BLIMarketDataSnapshot:
    data = _object(payload, "BLIMarketDataSnapshot")
    _require_fields(data, BLIMarketDataSnapshot, "BLIMarketDataSnapshot")
    return BLIMarketDataSnapshot(
        valuation_date=data["valuation_date"],
        as_of_timestamp=data["as_of_timestamp"],
        source_system=data["source_system"],
        snapshot_id=data["snapshot_id"],
        status=BLIMarketDataStatus(data["status"]),
        bond_quote=_bli_bond_quote(data["bond_quote"]),
        curve_points=_tuple(data["curve_points"], "curve_points", _curve_point),
        volatility_input=_volatility(data["volatility_input"]),
        credit_spread_input=_credit_spread(data["credit_spread_input"]),
        deposit_rate_observation=None
        if data["deposit_rate_observation"] is None
        else _deposit_rate(data["deposit_rate_observation"]),
    )


def _request(payload: object) -> BLIStandaloneBondOptionRequest:
    data = _object(payload, "BLIStandaloneBondOptionRequest")
    _require_fields(data, BLIStandaloneBondOptionRequest, "BLIStandaloneBondOptionRequest")
    return BLIStandaloneBondOptionRequest(
        bond_option=_bond_option(data["bond_option"]),
        resolved_bond_reference_data=_bond_reference_data(data["resolved_bond_reference_data"]),
        valuation_date=data["valuation_date"],
        market_data_snapshot=_snapshot(data["market_data_snapshot"]),
    )


def _pricing_result(payload: object) -> PricingResult:
    data = _object(payload, "PricingResult")
    _require_fields(data, PricingResult, "PricingResult")
    return PricingResult(
        product_id=data["product_id"],
        product_type=data["product_type"],
        valuation_date=data["valuation_date"],
        result_currency=data["result_currency"],
        status=PricingStatus(data["status"]),
        engine_name=data["engine_name"],
        engine_version=data["engine_version"],
        method=data["method"],
        market_data_as_of=data["market_data_as_of"],
        warnings=_tuple(data["warnings"], "warnings", _message),
        errors=_tuple(data["errors"], "errors", _message),
        assumptions=_decode_value(data["assumptions"]),
        pv=data["pv"],
        dv01=data["dv01"],
        cashflows=None if data["cashflows"] is None else _decode_value(data["cashflows"]),
        scenario_results=_decode_value(data["scenario_results"]),
        diagnostics=_decode_value(data["diagnostics"]),
    )


def _benchmark_quote(payload: object) -> BLIBenchmarkQuote:
    data = _object(payload, "BLIBenchmarkQuote")
    _require_fields(data, BLIBenchmarkQuote, "BLIBenchmarkQuote")
    return BLIBenchmarkQuote(
        benchmark_id=data["benchmark_id"],
        source_type=BLIBenchmarkSourceType(data["source_type"]),
        source_system=data["source_system"],
        source_as_of=data["source_as_of"],
        retrieved_at=data["retrieved_at"],
        quote_side=BLIBenchmarkQuoteSide(data["quote_side"]),
        premium_per_100=data["premium_per_100"],
        total_premium=data["total_premium"],
        currency=Currency(data["currency"]),
        product_id=data["product_id"],
        snapshot_id=data["snapshot_id"],
        underlying_id=data["underlying_id"],
        source_reference=data["source_reference"],
        notes=data["notes"],
    )


def _comparison(payload: object) -> BLIBenchmarkComparisonResult:
    data = _object(payload, "BLIBenchmarkComparisonResult")
    _require_fields(data, BLIBenchmarkComparisonResult, "BLIBenchmarkComparisonResult")
    return BLIBenchmarkComparisonResult(
        status=BLIBenchmarkComparisonStatus(data["status"]),
        reason=BLIBenchmarkComparisonReason(data["reason"]),
        comparison_metric=BLIBenchmarkComparisonMetric(data["comparison_metric"]),
        active_quote_side=BLIBenchmarkQuoteSide(data["active_quote_side"]),
        pass_threshold=data["pass_threshold"],
        fail_threshold=data["fail_threshold"],
        near_zero_threshold_per_100=data["near_zero_threshold_per_100"],
        product_id=data["product_id"],
        snapshot_id=data["snapshot_id"],
        underlying_id=data["underlying_id"],
        currency=Currency(data["currency"]),
        valuation_date=data["valuation_date"],
        market_data_as_of=data["market_data_as_of"],
        benchmark_id=data["benchmark_id"],
        benchmark_source_as_of=data["benchmark_source_as_of"],
        benchmark_retrieved_at=data["benchmark_retrieved_at"],
        model_fair_premium_per_100=data["model_fair_premium_per_100"],
        model_total_premium=data["model_total_premium"],
        benchmark_premium_per_100=data["benchmark_premium_per_100"],
        benchmark_total_premium=data["benchmark_total_premium"],
        signed_residual_per_100=data["signed_residual_per_100"],
        absolute_residual_per_100=data["absolute_residual_per_100"],
        relative_residual=data["relative_residual"],
        alignment_note=data["alignment_note"],
    )


def _solver(payload: object) -> BLIImpliedPriceVolSolverResult:
    data = _object(payload, "BLIImpliedPriceVolSolverResult")
    _require_fields(data, BLIImpliedPriceVolSolverResult, "BLIImpliedPriceVolSolverResult")
    return BLIImpliedPriceVolSolverResult(
        status=BLIImpliedPriceVolSolverStatus(data["status"]),
        reason=BLIImpliedPriceVolSolverReason(data["reason"]),
        option_type=OptionType(data["option_type"]),
        forward_clean_price=data["forward_clean_price"],
        strike_clean_price=data["strike_clean_price"],
        time_to_expiry=data["time_to_expiry"],
        discount_factor=data["discount_factor"],
        target_premium_per_100=data["target_premium_per_100"],
        lower_price_vol=data["lower_price_vol"],
        upper_price_vol=data["upper_price_vol"],
        premium_tolerance_per_100=data["premium_tolerance_per_100"],
        price_vol_tolerance=data["price_vol_tolerance"],
        max_iterations=data["max_iterations"],
        arbitrage_lower_bound_per_100=data["arbitrage_lower_bound_per_100"],
        arbitrage_upper_bound_per_100=data["arbitrage_upper_bound_per_100"],
        lower_bound_model_premium_per_100=data["lower_bound_model_premium_per_100"],
        upper_bound_model_premium_per_100=data["upper_bound_model_premium_per_100"],
        implied_price_vol=data["implied_price_vol"],
        model_premium_per_100=data["model_premium_per_100"],
        premium_residual_per_100=data["premium_residual_per_100"],
        iterations=data["iterations"],
        final_bracket_lower_price_vol=data["final_bracket_lower_price_vol"],
        final_bracket_upper_price_vol=data["final_bracket_upper_price_vol"],
        diagnostic_note=data["diagnostic_note"],
    )


def _calibration(payload: object) -> BLIImpliedPriceVolCalibrationResult | None:
    if payload is None:
        return None
    data = _object(payload, "BLIImpliedPriceVolCalibrationResult")
    _require_fields(
        data, BLIImpliedPriceVolCalibrationResult, "BLIImpliedPriceVolCalibrationResult"
    )
    return BLIImpliedPriceVolCalibrationResult(
        status=BLIImpliedPriceVolCalibrationStatus(data["status"]),
        reason=BLIImpliedPriceVolCalibrationReason(data["reason"]),
        active_quote_side=BLIBenchmarkQuoteSide(data["active_quote_side"]),
        pricing_engine_name=data["pricing_engine_name"],
        pricing_engine_version=data["pricing_engine_version"],
        product_id=data["product_id"],
        snapshot_id=data["snapshot_id"],
        underlying_id=data["underlying_id"],
        currency=Currency(data["currency"]),
        valuation_date=data["valuation_date"],
        market_data_as_of=data["market_data_as_of"],
        option_type=OptionType(data["option_type"]),
        expiry_date=data["expiry_date"],
        request_notional=data["request_notional"],
        original_volatility=data["original_volatility"],
        original_volatility_basis=BLIVolatilityBasis(data["original_volatility_basis"]),
        benchmark_id=data["benchmark_id"],
        benchmark_source_type=BLIBenchmarkSourceType(data["benchmark_source_type"]),
        benchmark_source_system=data["benchmark_source_system"],
        benchmark_source_as_of=data["benchmark_source_as_of"],
        benchmark_retrieved_at=data["benchmark_retrieved_at"],
        benchmark_quote_side=BLIBenchmarkQuoteSide(data["benchmark_quote_side"]),
        benchmark_premium_per_100=data["benchmark_premium_per_100"],
        benchmark_total_premium=data["benchmark_total_premium"],
        benchmark_source_reference=data["benchmark_source_reference"],
        benchmark_notes=data["benchmark_notes"],
        request_support_reasons=_tuple(data["request_support_reasons"], "request_support_reasons"),
        forward_clean_price=data["forward_clean_price"],
        strike_clean_price=data["strike_clean_price"],
        time_to_expiry=data["time_to_expiry"],
        option_discount_factor=data["option_discount_factor"],
        solver_result=None if data["solver_result"] is None else _solver(data["solver_result"]),
        resolution_error_type=data["resolution_error_type"],
        resolution_error_message=data["resolution_error_message"],
        diagnostic_note=data["diagnostic_note"],
    )


def quote_record_to_dict(record: BLIQuoteRecord) -> dict[str, object]:
    """Encode ``record`` to a strict JSON-ready typed dict."""

    if not isinstance(record, BLIQuoteRecord):
        raise TypeError("record must be a BLIQuoteRecord")
    payload: dict[str, object] = {
        "schema_version": record.schema_version,
        "quote_id": record.quote_id,
        "quote_version": record.quote_version,
        "saved_at": record.saved_at,
        "operator_id": record.operator_id,
        "request": _tagged(BLIStandaloneBondOptionRequest, record.request),
        "pricing_result": _tagged(PricingResult, record.pricing_result),
        "benchmark_quote": _tagged(BLIBenchmarkQuote, record.benchmark_quote),
        "benchmark_comparison": _tagged(
            BLIBenchmarkComparisonResult, record.benchmark_comparison
        ),
        "calibration_result": None
        if record.calibration_result is None
        else _tagged(BLIImpliedPriceVolCalibrationResult, record.calibration_result),
        "client_quote_premium_per_100": record.client_quote_premium_per_100,
        "client_quote_total_premium": record.client_quote_total_premium,
        "trader_adjustment_per_100": record.trader_adjustment_per_100,
        "trader_adjustment_total": record.trader_adjustment_total,
        "override_reason": record.override_reason,
        "exclusions": _encode_value(record.exclusions),
    }
    _reject_non_finite(payload, "BLIQuoteRecord")
    return payload


def quote_record_from_dict(payload: object) -> BLIQuoteRecord:
    """Decode a strict typed dict into ``BLIQuoteRecord`` by explicit field names."""

    data = _object(payload, "BLIQuoteRecord")
    _require_fields(data, BLIQuoteRecord, "BLIQuoteRecord", tagged=False)
    if data["schema_version"] != BLI_QUOTE_RECORD_SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {data['schema_version']!r}")
    return BLIQuoteRecord(
        schema_version=data["schema_version"],
        quote_id=data["quote_id"],
        quote_version=data["quote_version"],
        saved_at=data["saved_at"],
        operator_id=data["operator_id"],
        request=_request(data["request"]),
        pricing_result=_pricing_result(data["pricing_result"]),
        benchmark_quote=_benchmark_quote(data["benchmark_quote"]),
        benchmark_comparison=_comparison(data["benchmark_comparison"]),
        calibration_result=_calibration(data["calibration_result"]),
        client_quote_premium_per_100=data["client_quote_premium_per_100"],
        client_quote_total_premium=data["client_quote_total_premium"],
        trader_adjustment_per_100=data["trader_adjustment_per_100"],
        trader_adjustment_total=data["trader_adjustment_total"],
        override_reason=data["override_reason"],
        exclusions=_tuple(data["exclusions"], "exclusions"),
    )


def quote_record_to_json(record: BLIQuoteRecord) -> str:
    """Encode ``record`` as canonical JSON with one trailing newline."""

    return json.dumps(
        quote_record_to_dict(record),
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"


def quote_record_from_json(text: str) -> BLIQuoteRecord:
    """Decode canonical JSON text into ``BLIQuoteRecord``."""

    if not isinstance(text, str):
        raise TypeError("text must be str")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("top-level JSON must be an object")
    return quote_record_from_dict(payload)
