"""Strict typed-dict and canonical JSON codec for ``BLIQuoteRecord``."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, fields, is_dataclass
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
    BLIQuoteRecordExclusionReason,
    BLIQuoteRecordOverrideReason,
)
from shiori_pricing_lab.data.bli_snapshot import BLIVolatilityBasis
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
from shiori_pricing_lab.products.enums import Currency, OptionType


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


def _dataclass_to_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not is_dataclass(value):
        raise TypeError(f"expected dataclass instance, got {type(value).__name__}")
    return _enum_value(asdict(value))


def _require_object(payload: Any, name: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be an object")
    return payload


def _require_fields(payload: dict[str, Any], cls: type, name: str) -> None:
    expected = tuple(field.name for field in fields(cls))
    actual = tuple(payload.keys())
    if actual != expected:
        missing = [field for field in expected if field not in payload]
        unknown = [field for field in actual if field not in expected]
        if missing or unknown:
            raise ValueError(f"{name} fields mismatch: missing={missing}, unknown={unknown}")
        return


def _require_finite_json_numbers(value: Any, path: str = "payload") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path} contains non-finite number {value!r}")
    if isinstance(value, dict):
        for key, item in value.items():
            _require_finite_json_numbers(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _require_finite_json_numbers(item, f"{path}[{index}]")


def _decode_pricing_message(payload: Any) -> PricingMessage:
    payload = _require_object(payload, "PricingMessage")
    _require_fields(payload, PricingMessage, "PricingMessage")
    code_value = payload["code"]
    try:
        code = PricingErrorCode(code_value)
    except ValueError:
        code = PricingWarningCode(code_value)
    return PricingMessage(code=code, message=payload["message"], detail=payload["detail"])


def _decode_tuple(payload: Any, name: str, decoder=lambda item: item) -> tuple[Any, ...]:
    if not isinstance(payload, list):
        raise ValueError(f"{name} must be a JSON array")
    return tuple(decoder(item) for item in payload)


def _decode_pricing_result(payload: Any) -> PricingResult:
    payload = _require_object(payload, "PricingResult")
    _require_fields(payload, PricingResult, "PricingResult")
    return PricingResult(
        product_id=payload["product_id"],
        product_type=payload["product_type"],
        valuation_date=payload["valuation_date"],
        result_currency=payload["result_currency"],
        status=PricingStatus(payload["status"]),
        engine_name=payload["engine_name"],
        engine_version=payload["engine_version"],
        method=payload["method"],
        market_data_as_of=payload["market_data_as_of"],
        warnings=_decode_tuple(payload["warnings"], "warnings", _decode_pricing_message),
        errors=_decode_tuple(payload["errors"], "errors", _decode_pricing_message),
        assumptions=payload["assumptions"],
        pv=payload["pv"],
        dv01=payload["dv01"],
        cashflows=None if payload["cashflows"] is None else tuple(payload["cashflows"]),
        scenario_results=payload["scenario_results"],
        diagnostics=payload["diagnostics"],
    )


def _decode_benchmark_quote(payload: Any) -> BLIBenchmarkQuote:
    payload = _require_object(payload, "BLIBenchmarkQuote")
    _require_fields(payload, BLIBenchmarkQuote, "BLIBenchmarkQuote")
    return BLIBenchmarkQuote(
        benchmark_id=payload["benchmark_id"],
        source_type=BLIBenchmarkSourceType(payload["source_type"]),
        source_system=payload["source_system"],
        source_as_of=payload["source_as_of"],
        retrieved_at=payload["retrieved_at"],
        quote_side=BLIBenchmarkQuoteSide(payload["quote_side"]),
        premium_per_100=payload["premium_per_100"],
        total_premium=payload["total_premium"],
        currency=Currency(payload["currency"]),
        product_id=payload["product_id"],
        snapshot_id=payload["snapshot_id"],
        underlying_id=payload["underlying_id"],
        source_reference=payload["source_reference"],
        notes=payload["notes"],
    )


def _decode_comparison(payload: Any) -> BLIBenchmarkComparisonResult:
    payload = _require_object(payload, "BLIBenchmarkComparisonResult")
    _require_fields(payload, BLIBenchmarkComparisonResult, "BLIBenchmarkComparisonResult")
    return BLIBenchmarkComparisonResult(
        status=BLIBenchmarkComparisonStatus(payload["status"]),
        reason=BLIBenchmarkComparisonReason(payload["reason"]),
        comparison_metric=BLIBenchmarkComparisonMetric(payload["comparison_metric"]),
        active_quote_side=BLIBenchmarkQuoteSide(payload["active_quote_side"]),
        pass_threshold=payload["pass_threshold"],
        fail_threshold=payload["fail_threshold"],
        near_zero_threshold_per_100=payload["near_zero_threshold_per_100"],
        product_id=payload["product_id"],
        snapshot_id=payload["snapshot_id"],
        underlying_id=payload["underlying_id"],
        currency=Currency(payload["currency"]),
        valuation_date=payload["valuation_date"],
        market_data_as_of=payload["market_data_as_of"],
        benchmark_id=payload["benchmark_id"],
        benchmark_source_as_of=payload["benchmark_source_as_of"],
        benchmark_retrieved_at=payload["benchmark_retrieved_at"],
        model_fair_premium_per_100=payload["model_fair_premium_per_100"],
        model_total_premium=payload["model_total_premium"],
        benchmark_premium_per_100=payload["benchmark_premium_per_100"],
        benchmark_total_premium=payload["benchmark_total_premium"],
        signed_residual_per_100=payload["signed_residual_per_100"],
        absolute_residual_per_100=payload["absolute_residual_per_100"],
        relative_residual=payload["relative_residual"],
        alignment_note=payload["alignment_note"],
    )


def _decode_solver(payload: Any) -> BLIImpliedPriceVolSolverResult:
    payload = _require_object(payload, "BLIImpliedPriceVolSolverResult")
    _require_fields(payload, BLIImpliedPriceVolSolverResult, "BLIImpliedPriceVolSolverResult")
    return BLIImpliedPriceVolSolverResult(
        status=BLIImpliedPriceVolSolverStatus(payload["status"]),
        reason=BLIImpliedPriceVolSolverReason(payload["reason"]),
        option_type=OptionType(payload["option_type"]),
        **{k: payload[k] for k in tuple(payload.keys())[3:]},
    )


def _decode_calibration(payload: Any) -> BLIImpliedPriceVolCalibrationResult | None:
    if payload is None:
        return None
    payload = _require_object(payload, "BLIImpliedPriceVolCalibrationResult")
    _require_fields(
        payload, BLIImpliedPriceVolCalibrationResult, "BLIImpliedPriceVolCalibrationResult"
    )
    return BLIImpliedPriceVolCalibrationResult(
        status=BLIImpliedPriceVolCalibrationStatus(payload["status"]),
        reason=BLIImpliedPriceVolCalibrationReason(payload["reason"]),
        active_quote_side=BLIBenchmarkQuoteSide(payload["active_quote_side"]),
        pricing_engine_name=payload["pricing_engine_name"],
        pricing_engine_version=payload["pricing_engine_version"],
        product_id=payload["product_id"],
        snapshot_id=payload["snapshot_id"],
        underlying_id=payload["underlying_id"],
        currency=Currency(payload["currency"]),
        valuation_date=payload["valuation_date"],
        market_data_as_of=payload["market_data_as_of"],
        option_type=OptionType(payload["option_type"]),
        expiry_date=payload["expiry_date"],
        request_notional=payload["request_notional"],
        original_volatility=payload["original_volatility"],
        original_volatility_basis=BLIVolatilityBasis(payload["original_volatility_basis"]),
        benchmark_id=payload["benchmark_id"],
        benchmark_source_type=BLIBenchmarkSourceType(payload["benchmark_source_type"]),
        benchmark_source_system=payload["benchmark_source_system"],
        benchmark_source_as_of=payload["benchmark_source_as_of"],
        benchmark_retrieved_at=payload["benchmark_retrieved_at"],
        benchmark_quote_side=BLIBenchmarkQuoteSide(payload["benchmark_quote_side"]),
        benchmark_premium_per_100=payload["benchmark_premium_per_100"],
        benchmark_total_premium=payload["benchmark_total_premium"],
        benchmark_source_reference=payload["benchmark_source_reference"],
        benchmark_notes=payload["benchmark_notes"],
        request_support_reasons=tuple(payload["request_support_reasons"]),
        forward_clean_price=payload["forward_clean_price"],
        strike_clean_price=payload["strike_clean_price"],
        time_to_expiry=payload["time_to_expiry"],
        option_discount_factor=payload["option_discount_factor"],
        solver_result=_decode_solver(payload["solver_result"])
        if payload["solver_result"] is not None
        else None,
        resolution_error_type=payload["resolution_error_type"],
        resolution_error_message=payload["resolution_error_message"],
        diagnostic_note=payload["diagnostic_note"],
    )


def bli_quote_record_to_typed_dict(record: BLIQuoteRecord) -> dict[str, Any]:
    if not isinstance(record, BLIQuoteRecord):
        raise TypeError("record must be a BLIQuoteRecord")
    payload = {
        "schema_version": record.schema_version,
        "quote_record_id": record.quote_record_id,
        "product_id": record.product_id,
        "snapshot_id": record.snapshot_id,
        "underlying_id": record.underlying_id,
        "currency": record.currency.value,
        "valuation_date": record.valuation_date,
        "created_at": record.created_at,
        "created_by": record.created_by,
        "pricing_result": _dataclass_to_dict(record.pricing_result),
        "model_fair_value_per_100": record.model_fair_value_per_100,
        "model_total_value": record.model_total_value,
        "benchmark_quote": _dataclass_to_dict(record.benchmark_quote),
        "comparison_result": _dataclass_to_dict(record.comparison_result),
        "calibration_result": _dataclass_to_dict(record.calibration_result),
        "client_quote_per_100": record.client_quote_per_100,
        "client_total_quote": record.client_total_quote,
        "trader_adjustment_per_100": record.trader_adjustment_per_100,
        "trader_total_adjustment": record.trader_total_adjustment,
        "override_applied": record.override_applied,
        "override_reason": None
        if record.override_reason is None
        else record.override_reason.value,
        "override_note": record.override_note,
        "exclusion_reasons": [item.value for item in record.exclusion_reasons],
        "notes": record.notes,
    }
    _require_finite_json_numbers(payload)
    return payload


def bli_quote_record_from_typed_dict(
    payload: dict[str, Any], *, _enforce_order: bool = True
) -> BLIQuoteRecord:
    payload = _require_object(payload, "BLIQuoteRecord")
    if _enforce_order:
        _require_fields(payload, BLIQuoteRecord, "BLIQuoteRecord")
    else:
        expected = {field.name for field in fields(BLIQuoteRecord)}
        actual = set(payload)
        if actual != expected:
            raise ValueError(
                "BLIQuoteRecord fields mismatch: "
                f"missing={sorted(expected - actual)}, "
                f"unknown={sorted(actual - expected)}"
            )
    _require_finite_json_numbers(payload)
    if payload["schema_version"] != BLI_QUOTE_RECORD_SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {payload['schema_version']!r}")
    exclusions = _decode_tuple(
        payload["exclusion_reasons"],
        "exclusion_reasons",
        lambda item: BLIQuoteRecordExclusionReason(item),
    )
    return BLIQuoteRecord(
        schema_version=payload["schema_version"],
        quote_record_id=payload["quote_record_id"],
        product_id=payload["product_id"],
        snapshot_id=payload["snapshot_id"],
        underlying_id=payload["underlying_id"],
        currency=Currency(payload["currency"]),
        valuation_date=payload["valuation_date"],
        created_at=payload["created_at"],
        created_by=payload["created_by"],
        pricing_result=_decode_pricing_result(payload["pricing_result"]),
        model_fair_value_per_100=payload["model_fair_value_per_100"],
        model_total_value=payload["model_total_value"],
        benchmark_quote=_decode_benchmark_quote(payload["benchmark_quote"]),
        comparison_result=_decode_comparison(payload["comparison_result"]),
        calibration_result=_decode_calibration(payload["calibration_result"]),
        client_quote_per_100=payload["client_quote_per_100"],
        client_total_quote=payload["client_total_quote"],
        trader_adjustment_per_100=payload["trader_adjustment_per_100"],
        trader_total_adjustment=payload["trader_total_adjustment"],
        override_applied=payload["override_applied"],
        override_reason=None
        if payload["override_reason"] is None
        else BLIQuoteRecordOverrideReason(payload["override_reason"]),
        override_note=payload["override_note"],
        exclusion_reasons=exclusions,
        notes=payload["notes"],
    )


def bli_quote_record_to_canonical_json(record: BLIQuoteRecord) -> str:
    return json.dumps(
        bli_quote_record_to_typed_dict(record),
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"


def bli_quote_record_from_canonical_json(payload: str) -> BLIQuoteRecord:
    if not isinstance(payload, str):
        raise TypeError("payload must be str")
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON: {exc.msg}") from exc
    if not isinstance(decoded, dict):
        raise ValueError("top-level JSON must be an object")
    return bli_quote_record_from_typed_dict(decoded, _enforce_order=False)
