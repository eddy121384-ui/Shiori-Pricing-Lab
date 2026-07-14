"""Canonical BLI quote-record contract (Issue #100 PR A).

This module is a RED-zone schema boundary only.  It records already-computed
and already-observed economic values for one quote workflow; it does not price,
compare, calibrate, persist, fetch data, generate ids, use a system clock, or
reconcile economics.  Model fair value, benchmark quote, client quote, and
trader adjustment are intentionally separate fields.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from shiori_pricing_lab.data._validation import _parse_as_of_calendar_date, _require_non_blank
from shiori_pricing_lab.data.bli_benchmark_quote import BLIBenchmarkQuote
from shiori_pricing_lab.pricing.bli_benchmark_comparison import BLIBenchmarkComparisonResult
from shiori_pricing_lab.pricing.bli_implied_price_vol_calibration import (
    BLIImpliedPriceVolCalibrationResult,
)
from shiori_pricing_lab.pricing.result import PricingResult
from shiori_pricing_lab.products.enums import Currency, coerce_enum

BLI_QUOTE_RECORD_SCHEMA_VERSION = 1


class BLIQuoteRecordOverrideReason(StrEnum):
    """Controlled reason for a trader override of the model/client quote."""

    BENCHMARK_OVERRIDE = "BENCHMARK_OVERRIDE"
    CLIENT_REQUEST = "CLIENT_REQUEST"
    MARKET_COLOR = "MARKET_COLOR"
    RISK_MANAGEMENT = "RISK_MANAGEMENT"
    OTHER = "OTHER"


class BLIQuoteRecordExclusionReason(StrEnum):
    """Controlled reason a record is excluded from downstream analysis."""

    STALE_BENCHMARK = "STALE_BENCHMARK"
    INVALID_MARKET_DATA = "INVALID_MARKET_DATA"
    UNSUPPORTED_REQUEST = "UNSUPPORTED_REQUEST"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    OTHER = "OTHER"


def _require_exact_type(value: object, typ: type, field_name: str) -> None:
    if type(value) is not typ:  # noqa: E721 - exact native contract, not subclass polymorphism.
        raise TypeError(f"{field_name} must be {typ.__name__}, got {type(value).__name__}")


def _require_finite_float(value: object, field_name: str, *, non_negative: bool = False) -> float:
    _require_exact_type(value, float, field_name)
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite, got {value!r}")
    if non_negative and value < 0.0:
        raise ValueError(f"{field_name} must be non-negative, got {value!r}")
    return value


def _require_optional_finite_float(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    return _require_finite_float(value, field_name)


def _require_bool(value: object, field_name: str) -> bool:
    _require_exact_type(value, bool, field_name)
    return value


def _require_tuple_of_str(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be a tuple of strings")
    for item in value:
        _require_exact_type(item, str, f"{field_name} item")
        _require_non_blank(item, f"{field_name} item")
    return value


@dataclass(frozen=True)
class BLIQuoteRecord:
    """Frozen canonical quote record for one BLI quote workflow.

    The dataclass deliberately validates native Python types strictly.  Numeric
    fields are accepted only as finite ``float`` objects: ``int``, ``bool``,
    ``NaN``, and infinities are rejected so the codec can prove a stable JSON
    contract without silent coercion.
    """

    schema_version: int
    quote_record_id: str
    product_id: str
    snapshot_id: str
    underlying_id: str
    currency: Currency
    valuation_date: str
    created_at: str
    created_by: str
    pricing_result: PricingResult
    model_fair_value_per_100: float
    model_total_value: float
    benchmark_quote: BLIBenchmarkQuote
    comparison_result: BLIBenchmarkComparisonResult
    calibration_result: BLIImpliedPriceVolCalibrationResult | None
    client_quote_per_100: float
    client_total_quote: float
    trader_adjustment_per_100: float
    trader_total_adjustment: float
    override_applied: bool
    override_reason: BLIQuoteRecordOverrideReason | None
    override_note: str | None
    exclusion_reasons: tuple[BLIQuoteRecordExclusionReason, ...]
    notes: str | None = None

    def __post_init__(self) -> None:
        _require_exact_type(self.schema_version, int, "schema_version")
        if self.schema_version != BLI_QUOTE_RECORD_SCHEMA_VERSION:
            raise ValueError(
                "schema_version must be "
                f"{BLI_QUOTE_RECORD_SCHEMA_VERSION}, got {self.schema_version!r}"
            )

        for field_name in (
            "quote_record_id",
            "product_id",
            "snapshot_id",
            "underlying_id",
            "valuation_date",
            "created_at",
            "created_by",
        ):
            value = getattr(self, field_name)
            _require_exact_type(value, str, field_name)
            _require_non_blank(value, field_name)

        _parse_as_of_calendar_date(self.valuation_date, "valuation_date")
        _parse_as_of_calendar_date(self.created_at, "created_at")
        object.__setattr__(self, "currency", coerce_enum(self.currency, Currency, "currency"))

        if not isinstance(self.pricing_result, PricingResult):
            raise TypeError("pricing_result must be a PricingResult")
        if not isinstance(self.benchmark_quote, BLIBenchmarkQuote):
            raise TypeError("benchmark_quote must be a BLIBenchmarkQuote")
        if not isinstance(self.comparison_result, BLIBenchmarkComparisonResult):
            raise TypeError("comparison_result must be a BLIBenchmarkComparisonResult")
        if self.calibration_result is not None and not isinstance(
            self.calibration_result, BLIImpliedPriceVolCalibrationResult
        ):
            raise TypeError(
                "calibration_result must be None or a BLIImpliedPriceVolCalibrationResult"
            )

        for field_name in (
            "model_fair_value_per_100",
            "model_total_value",
            "client_quote_per_100",
            "client_total_quote",
            "trader_adjustment_per_100",
            "trader_total_adjustment",
        ):
            _require_finite_float(getattr(self, field_name), field_name)

        object.__setattr__(
            self, "override_applied", _require_bool(self.override_applied, "override_applied")
        )
        if self.override_reason is not None:
            object.__setattr__(
                self,
                "override_reason",
                coerce_enum(self.override_reason, BLIQuoteRecordOverrideReason, "override_reason"),
            )
        if self.override_note is not None:
            _require_exact_type(self.override_note, str, "override_note")
            _require_non_blank(self.override_note, "override_note")
        if self.override_applied:
            if self.override_reason is None or self.override_note is None:
                raise ValueError(
                    "override_applied=True requires override_reason and override_note"
                )
        elif self.override_reason is not None or self.override_note is not None:
            raise ValueError(
                "override_applied=False requires override_reason and override_note to be None"
            )

        if not isinstance(self.exclusion_reasons, tuple):
            raise TypeError("exclusion_reasons must be a tuple")
        coerced_exclusions = tuple(
            coerce_enum(item, BLIQuoteRecordExclusionReason, "exclusion_reasons item")
            for item in self.exclusion_reasons
        )
        object.__setattr__(self, "exclusion_reasons", coerced_exclusions)

        if self.notes is not None:
            _require_exact_type(self.notes, str, "notes")
            _require_non_blank(self.notes, "notes")

        if self.product_id != self.benchmark_quote.product_id:
            raise ValueError("product_id must match benchmark_quote.product_id")
        if self.product_id != self.pricing_result.product_id:
            raise ValueError("product_id must match pricing_result.product_id")
        if self.snapshot_id != self.benchmark_quote.snapshot_id:
            raise ValueError("snapshot_id must match benchmark_quote.snapshot_id")
        if self.underlying_id != self.benchmark_quote.underlying_id:
            raise ValueError("underlying_id must match benchmark_quote.underlying_id")
        if self.currency is not self.benchmark_quote.currency:
            raise ValueError("currency must match benchmark_quote.currency")
        if self.currency.value != self.pricing_result.result_currency:
            raise ValueError("currency must match pricing_result.result_currency")
