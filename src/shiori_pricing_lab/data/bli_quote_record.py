"""Canonical BLI quote-record contract (Issue #100 PR A).

This module is RED-zone schema/validation only.  It stores one explicit BLI
quote record assembled from already-reviewed request, pricing, benchmark,
comparison, and optional calibration objects.  It does not generate IDs, use a
system clock, fetch data, persist records, replay pricing, invoke calibration, or
derive/reconcile per-100 and total amounts.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime

from shiori_pricing_lab.data.bli_benchmark_quote import BLIBenchmarkQuote
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.pricing.bli_benchmark_comparison import (
    BLIBenchmarkComparisonResult,
)
from shiori_pricing_lab.pricing.bli_implied_price_vol_calibration import (
    BLIImpliedPriceVolCalibrationResult,
)
from shiori_pricing_lab.pricing.result import PricingResult

BLI_QUOTE_RECORD_SCHEMA_VERSION = 1

_UUID4_CANONICAL_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_SAVED_AT_UTC_SECONDS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
)


def _require_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an int, got {value!r}")
    return value


def _require_non_blank_stripped(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a str, got {type(value).__name__}")
    if not value:
        raise ValueError(f"{field_name} must be non-blank")
    if value.strip() != value:
        raise ValueError(f"{field_name} must already be stripped")
    if not value.strip():
        raise ValueError(f"{field_name} must be non-blank")
    return value


def _require_optional_finite_number(
    value: object, field_name: str, *, non_negative: bool
) -> int | float | None:
    """Require ``None`` or an exact, finite ``int``/``float``, unchanged.

    Membership is by exact type (``type(value) in (int, float)``), not
    ``isinstance`` -- a ``bool`` (an ``int`` subclass) and any other numeric
    subclass or custom numeric type are rejected, never silently accepted.
    The original value is returned as-is: an ``int`` is never coerced to
    ``float`` (or vice versa), so the four optional top-level quote numeric
    fields (client premiums, trader adjustments) preserve exactly the
    builtin type the caller supplied.

    ``math.isfinite`` is called only for an exact ``float``. An exact
    builtin ``int`` is mathematically finite by construction and needs no
    finiteness check -- calling ``math.isfinite`` on one anyway would
    implicitly convert it to ``float`` first, and a huge int outside
    float range (e.g. ``10**1000``) would raise ``OverflowError`` instead
    of being accepted, contradicting the exact-int-accepted-unchanged rule.
    """

    if value is None:
        return None
    if type(value) not in (int, float):
        raise TypeError(f"{field_name} must be a finite number or None, got {value!r}")
    if type(value) is float and not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite, got {value!r}")
    if non_negative and value < 0:
        raise ValueError(f"{field_name} must be non-negative, got {value!r}")
    return value


def _require_saved_at(value: object) -> str:
    saved_at = _require_non_blank_stripped(value, "saved_at")
    if not _SAVED_AT_UTC_SECONDS_RE.fullmatch(saved_at):
        raise ValueError("saved_at must match YYYY-MM-DDTHH:MM:SSZ")
    try:
        datetime.strptime(saved_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError("saved_at must be a real UTC RFC3339 seconds timestamp") from exc
    return saved_at


def _require_exclusions(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError("exclusions must be a tuple[str, ...]")
    for item in value:
        _require_non_blank_stripped(item, "exclusions item")
    return value


def _require_model_number(value: object, field_name: str) -> None:
    """Require a finite, non-bool number; accepted-type policy unchanged.

    ``math.isfinite`` is called only for a ``float`` -- an ``int`` (already
    accepted by the type check above, with ``bool`` excluded) is
    mathematically finite by construction and needs no finiteness check.
    Calling ``math.isfinite`` on a huge ``int`` (e.g. ``10**1000``) would
    implicitly convert it to ``float`` first and raise ``OverflowError``
    instead of accepting it, contradicting the already-approved rule that
    an exact builtin int is accepted unchanged.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite, got {value!r}")


def _require_model_anchors(pricing_result: PricingResult) -> None:
    """Require ``pv`` and ``assumptions['black76_pv_per_100']`` to be usable anchors.

    Called before any comparison-alignment or override evaluation reads
    either value, so a missing/non-finite/bool anchor fails explicitly
    instead of silently comparing against ``None`` (which would make a
    missing anchor look like "always different," accidentally satisfying an
    override/mismatch check rather than failing it outright). Neither value
    is derived, reconciled, coerced, or mutated here -- only validated.
    """

    _require_model_number(pricing_result.pv, "pricing_result.pv")
    if "black76_pv_per_100" not in pricing_result.assumptions:
        raise ValueError(
            "pricing_result.assumptions must contain 'black76_pv_per_100'"
        )
    _require_model_number(
        pricing_result.assumptions["black76_pv_per_100"],
        "pricing_result.assumptions['black76_pv_per_100']",
    )


def _model_per_100(pricing_result: PricingResult) -> float:
    return pricing_result.assumptions["black76_pv_per_100"]


@dataclass(frozen=True)
class BLIQuoteRecord:
    """Frozen canonical BLI quote record with the authorized 16-field shape."""

    schema_version: int
    quote_id: str
    quote_version: int
    saved_at: str
    operator_id: str
    request: BLIStandaloneBondOptionRequest
    pricing_result: PricingResult
    benchmark_quote: BLIBenchmarkQuote
    benchmark_comparison: BLIBenchmarkComparisonResult
    calibration_result: BLIImpliedPriceVolCalibrationResult | None
    client_quote_premium_per_100: float | None
    client_quote_total_premium: float | None
    trader_adjustment_per_100: float | None
    trader_adjustment_total: float | None
    override_reason: str | None
    exclusions: tuple[str, ...]

    def __post_init__(self) -> None:
        schema_version = _require_int(self.schema_version, "schema_version")
        if schema_version != BLI_QUOTE_RECORD_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {BLI_QUOTE_RECORD_SCHEMA_VERSION}, "
                f"got {schema_version!r}"
            )

        quote_id = _require_non_blank_stripped(self.quote_id, "quote_id")
        if not _UUID4_CANONICAL_RE.fullmatch(quote_id):
            raise ValueError("quote_id must be canonical lowercase UUID4 text")

        quote_version = _require_int(self.quote_version, "quote_version")
        if quote_version <= 0:
            raise ValueError(f"quote_version must be positive, got {quote_version!r}")

        _require_saved_at(self.saved_at)
        _require_non_blank_stripped(self.operator_id, "operator_id")

        if type(self.request) is not BLIStandaloneBondOptionRequest:
            raise TypeError("request must be exactly a BLIStandaloneBondOptionRequest")
        if type(self.pricing_result) is not PricingResult:
            raise TypeError("pricing_result must be exactly a PricingResult")
        if type(self.benchmark_quote) is not BLIBenchmarkQuote:
            raise TypeError("benchmark_quote must be exactly a BLIBenchmarkQuote")
        if type(self.benchmark_comparison) is not BLIBenchmarkComparisonResult:
            raise TypeError("benchmark_comparison must be exactly a BLIBenchmarkComparisonResult")
        if self.calibration_result is not None and (
            type(self.calibration_result) is not BLIImpliedPriceVolCalibrationResult
        ):
            raise TypeError(
                "calibration_result must be None or exactly a "
                "BLIImpliedPriceVolCalibrationResult"
            )

        if not self.pricing_result.is_success:
            raise ValueError("pricing_result must be SUCCESS or SUCCESS_WITH_WARNINGS")

        _require_model_anchors(self.pricing_result)

        object.__setattr__(
            self,
            "client_quote_premium_per_100",
            _require_optional_finite_number(
                self.client_quote_premium_per_100,
                "client_quote_premium_per_100",
                non_negative=True,
            ),
        )
        object.__setattr__(
            self,
            "client_quote_total_premium",
            _require_optional_finite_number(
                self.client_quote_total_premium,
                "client_quote_total_premium",
                non_negative=True,
            ),
        )
        object.__setattr__(
            self,
            "trader_adjustment_per_100",
            _require_optional_finite_number(
                self.trader_adjustment_per_100,
                "trader_adjustment_per_100",
                non_negative=False,
            ),
        )
        object.__setattr__(
            self,
            "trader_adjustment_total",
            _require_optional_finite_number(
                self.trader_adjustment_total,
                "trader_adjustment_total",
                non_negative=False,
            ),
        )

        if self.override_reason is not None:
            _require_non_blank_stripped(self.override_reason, "override_reason")
        object.__setattr__(self, "exclusions", _require_exclusions(self.exclusions))

        self._validate_alignment()
        self._validate_override_invariant()

    def _validate_alignment(self) -> None:
        request = self.request
        pricing = self.pricing_result
        benchmark = self.benchmark_quote
        comparison = self.benchmark_comparison
        calibration = self.calibration_result

        product_id = request.bond_option.product_id
        snapshot_id = request.market_data_snapshot.snapshot_id
        underlying_id = request.bond_option.underlying_isin
        currency = request.bond_option.currency
        valuation_date = request.valuation_date
        market_data_as_of = request.market_data_snapshot.as_of_timestamp

        if pricing.product_id != product_id:
            raise ValueError("pricing_result.product_id must match request.bond_option.product_id")
        if pricing.result_currency != currency.value:
            raise ValueError("pricing_result.result_currency must match request currency")
        if pricing.valuation_date != valuation_date:
            raise ValueError("pricing_result.valuation_date must match request.valuation_date")
        if pricing.market_data_as_of != market_data_as_of:
            raise ValueError(
                "pricing_result.market_data_as_of must match request market-data as-of"
            )

        if benchmark.product_id != product_id:
            raise ValueError("benchmark_quote.product_id must match request.bond_option.product_id")
        if benchmark.snapshot_id != snapshot_id:
            raise ValueError("benchmark_quote.snapshot_id must match request snapshot_id")
        if benchmark.underlying_id != underlying_id:
            raise ValueError("benchmark_quote.underlying_id must match request underlying ISIN")
        if benchmark.currency is not currency:
            raise ValueError("benchmark_quote.currency must match request currency")

        if comparison.product_id != product_id:
            raise ValueError("benchmark_comparison.product_id must match request product_id")
        if comparison.snapshot_id != snapshot_id:
            raise ValueError("benchmark_comparison.snapshot_id must match request snapshot_id")
        if comparison.underlying_id != underlying_id:
            raise ValueError(
                "benchmark_comparison.underlying_id must match request underlying ISIN"
            )
        if comparison.currency is not currency:
            raise ValueError("benchmark_comparison.currency must match request currency")
        if comparison.valuation_date != valuation_date:
            raise ValueError(
                "benchmark_comparison.valuation_date must match request valuation_date"
            )
        if comparison.market_data_as_of != market_data_as_of:
            raise ValueError(
                "benchmark_comparison.market_data_as_of must match request market-data as-of"
            )
        if comparison.benchmark_id != benchmark.benchmark_id:
            raise ValueError("benchmark_comparison.benchmark_id must match benchmark_quote")
        if comparison.benchmark_source_as_of != benchmark.source_as_of:
            raise ValueError(
                "benchmark_comparison benchmark_source_as_of must match benchmark_quote"
            )
        if comparison.benchmark_retrieved_at != benchmark.retrieved_at:
            raise ValueError(
                "benchmark_comparison benchmark_retrieved_at must match benchmark_quote"
            )
        if comparison.active_quote_side is not benchmark.quote_side:
            raise ValueError(
                "benchmark_comparison.active_quote_side must match benchmark quote_side"
            )
        if comparison.benchmark_premium_per_100 != benchmark.premium_per_100:
            raise ValueError("benchmark_comparison benchmark per-100 premium must match quote")
        if comparison.benchmark_total_premium != benchmark.total_premium:
            raise ValueError("benchmark_comparison benchmark total premium must match quote")
        if comparison.model_fair_premium_per_100 != _model_per_100(pricing):
            raise ValueError("benchmark_comparison model per-100 premium must match pricing_result")
        if comparison.model_total_premium != pricing.pv:
            raise ValueError("benchmark_comparison model total premium must match pricing_result")

        if calibration is not None:
            if calibration.product_id != product_id:
                raise ValueError("calibration_result.product_id must match request product_id")
            if calibration.snapshot_id != snapshot_id:
                raise ValueError("calibration_result.snapshot_id must match request snapshot_id")
            if calibration.underlying_id != underlying_id:
                raise ValueError(
                    "calibration_result.underlying_id must match request underlying ISIN"
                )
            if calibration.currency is not currency:
                raise ValueError("calibration_result.currency must match request currency")
            if calibration.valuation_date != valuation_date:
                raise ValueError(
                    "calibration_result.valuation_date must match request valuation_date"
                )
            if calibration.market_data_as_of != market_data_as_of:
                raise ValueError("calibration_result.market_data_as_of must match request as-of")
            if calibration.active_quote_side is not benchmark.quote_side:
                raise ValueError(
                    "calibration_result.active_quote_side must match benchmark quote_side"
                )
            if calibration.benchmark_id != benchmark.benchmark_id:
                raise ValueError("calibration_result.benchmark_id must match benchmark_quote")
            if calibration.benchmark_source_type is not benchmark.source_type:
                raise ValueError(
                    "calibration_result benchmark source_type must match benchmark_quote"
                )
            if calibration.benchmark_source_system != benchmark.source_system:
                raise ValueError("calibration_result source_system must match benchmark_quote")
            if calibration.benchmark_source_as_of != benchmark.source_as_of:
                raise ValueError("calibration_result source_as_of must match benchmark_quote")
            if calibration.benchmark_retrieved_at != benchmark.retrieved_at:
                raise ValueError("calibration_result retrieved_at must match benchmark_quote")
            if calibration.benchmark_quote_side is not benchmark.quote_side:
                raise ValueError("calibration_result quote_side must match benchmark_quote")
            if calibration.benchmark_premium_per_100 != benchmark.premium_per_100:
                raise ValueError("calibration_result benchmark per-100 premium must match quote")
            if calibration.benchmark_total_premium != benchmark.total_premium:
                raise ValueError("calibration_result benchmark total premium must match quote")
            if calibration.benchmark_source_reference != benchmark.source_reference:
                raise ValueError("calibration_result source_reference must match benchmark_quote")
            if calibration.benchmark_notes != benchmark.notes:
                raise ValueError("calibration_result notes must match benchmark_quote")

            # Direct-source calibration provenance anchors (Issue #100 comment
            # 4975917315): each of these calibration fields is a verbatim copy
            # of a value that already has one authoritative source elsewhere in
            # the record (the stored request or the stored model PricingResult).
            # Reject on mismatch; never derive, reconcile, coerce, or mutate
            # either side. This is deliberately limited to direct-source
            # anchors -- it does not add speculative solver-internal equality
            # rules for calibration_result.solver_result.
            if calibration.pricing_engine_name != pricing.engine_name:
                raise ValueError(
                    "calibration_result.pricing_engine_name must match pricing_result.engine_name"
                )
            if calibration.pricing_engine_version != pricing.engine_version:
                raise ValueError(
                    "calibration_result.pricing_engine_version must match "
                    "pricing_result.engine_version"
                )
            if calibration.option_type is not request.bond_option.option_type:
                raise ValueError(
                    "calibration_result.option_type must match request.bond_option.option_type"
                )
            if calibration.expiry_date != request.bond_option.expiry_date:
                raise ValueError(
                    "calibration_result.expiry_date must match request.bond_option.expiry_date"
                )
            if calibration.request_notional != request.bond_option.notional:
                raise ValueError(
                    "calibration_result.request_notional must match request.bond_option.notional"
                )
            if (
                calibration.original_volatility
                != request.market_data_snapshot.volatility_input.volatility
            ):
                raise ValueError(
                    "calibration_result.original_volatility must match "
                    "request.market_data_snapshot.volatility_input.volatility"
                )
            if (
                calibration.original_volatility_basis
                is not request.market_data_snapshot.volatility_input.volatility_basis
            ):
                raise ValueError(
                    "calibration_result.original_volatility_basis must match "
                    "request.market_data_snapshot.volatility_input.volatility_basis"
                )

    def _validate_override_invariant(self) -> None:
        requires_override = False
        model_per_100 = _model_per_100(self.pricing_result)
        model_total = self.pricing_result.pv

        if self.client_quote_premium_per_100 is not None:
            requires_override = self.client_quote_premium_per_100 != model_per_100
        if self.client_quote_total_premium is not None:
            requires_override = requires_override or self.client_quote_total_premium != model_total
        if self.trader_adjustment_per_100 is not None:
            requires_override = requires_override or self.trader_adjustment_per_100 != 0.0
        if self.trader_adjustment_total is not None:
            requires_override = requires_override or self.trader_adjustment_total != 0.0

        if requires_override and self.override_reason is None:
            raise ValueError("override_reason is required by the exact override invariant")
        if not requires_override and self.override_reason is not None:
            raise ValueError(
                "override_reason must be None when no override invariant branch applies"
            )
