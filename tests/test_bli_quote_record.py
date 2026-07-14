from dataclasses import fields

import pytest

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
from shiori_pricing_lab.pricing.bli_benchmark_comparison import (
    BLIBenchmarkComparisonMetric,
    BLIBenchmarkComparisonReason,
    BLIBenchmarkComparisonResult,
    BLIBenchmarkComparisonStatus,
)
from shiori_pricing_lab.pricing.result import PricingResult, PricingStatus
from shiori_pricing_lab.products.enums import Currency


def pricing_result():
    return PricingResult(
        product_id="BLI-OPT-1",
        product_type="BOND_OPTION",
        valuation_date="2026-06-23",
        result_currency="USD",
        status=PricingStatus.SUCCESS,
        engine_name="engine",
        engine_version="1",
        method="BLACK76",
        market_data_as_of="2026-06-23T09:00:00Z",
        assumptions={"black76_pv_per_100": 1.25},
        pv=12500.0,
    )


def benchmark_quote():
    return BLIBenchmarkQuote(
        benchmark_id="BM-1",
        source_type=BLIBenchmarkSourceType.BLOOMBERG,
        source_system="SYNTHETIC_TEST",
        source_as_of="2026-06-23T09:00:00Z",
        retrieved_at="2026-06-23T09:01:00Z",
        quote_side=BLIBenchmarkQuoteSide.MID,
        premium_per_100=1.2,
        total_premium=12000.0,
        currency=Currency.USD,
        product_id="BLI-OPT-1",
        snapshot_id="SNAP-1",
        underlying_id="US0000000001",
        source_reference="synthetic-test-reference",
    )


def comparison_result():
    return BLIBenchmarkComparisonResult(
        status=BLIBenchmarkComparisonStatus.PASS,
        reason=BLIBenchmarkComparisonReason.COMPARABLE,
        comparison_metric=BLIBenchmarkComparisonMetric.RELATIVE_RESIDUAL_PER_100,
        active_quote_side=BLIBenchmarkQuoteSide.MID,
        pass_threshold=0.02,
        fail_threshold=0.05,
        near_zero_threshold_per_100=0.01,
        product_id="BLI-OPT-1",
        snapshot_id="SNAP-1",
        underlying_id="US0000000001",
        currency=Currency.USD,
        valuation_date="2026-06-23",
        market_data_as_of="2026-06-23T09:00:00Z",
        benchmark_id="BM-1",
        benchmark_source_as_of="2026-06-23T09:00:00Z",
        benchmark_retrieved_at="2026-06-23T09:01:00Z",
        model_fair_premium_per_100=1.25,
        model_total_premium=12500.0,
        benchmark_premium_per_100=1.2,
        benchmark_total_premium=12000.0,
        signed_residual_per_100=0.05,
        absolute_residual_per_100=0.05,
        relative_residual=0.041666666666666706,
        alignment_note="synthetic comparable test",
    )


def record(**overrides):
    kwargs = dict(
        schema_version=BLI_QUOTE_RECORD_SCHEMA_VERSION,
        quote_record_id="QR-1",
        product_id="BLI-OPT-1",
        snapshot_id="SNAP-1",
        underlying_id="US0000000001",
        currency=Currency.USD,
        valuation_date="2026-06-23",
        created_at="2026-06-23T09:02:00Z",
        created_by="unit-test-operator",
        pricing_result=pricing_result(),
        model_fair_value_per_100=1.25,
        model_total_value=12500.0,
        benchmark_quote=benchmark_quote(),
        comparison_result=comparison_result(),
        calibration_result=None,
        client_quote_per_100=1.3,
        client_total_quote=13000.0,
        trader_adjustment_per_100=0.05,
        trader_total_adjustment=500.0,
        override_applied=False,
        override_reason=None,
        override_note=None,
        exclusion_reasons=(),
        notes="synthetic unit test record",
    )
    kwargs.update(overrides)
    return BLIQuoteRecord(**kwargs)


def test_field_order_is_frozen():
    assert [f.name for f in fields(BLIQuoteRecord)] == [
        "schema_version", "quote_record_id", "product_id", "snapshot_id", "underlying_id",
        "currency", "valuation_date", "created_at", "created_by", "pricing_result",
        "model_fair_value_per_100", "model_total_value", "benchmark_quote",
        "comparison_result", "calibration_result", "client_quote_per_100",
        "client_total_quote", "trader_adjustment_per_100", "trader_total_adjustment",
        "override_applied", "override_reason", "override_note", "exclusion_reasons", "notes",
    ]


def test_successful_record_construction_preserves_separate_economics():
    rec = record()
    assert rec.model_fair_value_per_100 == 1.25
    assert rec.benchmark_quote.premium_per_100 == 1.2
    assert rec.client_quote_per_100 == 1.3
    assert rec.trader_adjustment_per_100 == 0.05


@pytest.mark.parametrize("field,value", [
    ("quote_record_id", ""), ("created_at", "not-a-date"), ("created_by", " "),
    ("model_fair_value_per_100", 1), ("client_total_quote", float("nan")),
    ("override_applied", 1), ("exclusion_reasons", []),
])
def test_invalid_native_contracts_are_rejected(field, value):
    with pytest.raises((TypeError, ValueError)):
        record(**{field: value})


def test_override_invariant():
    with pytest.raises(ValueError):
        record(override_applied=True, override_reason=None, override_note="manual")
    with pytest.raises(ValueError):
        record(override_applied=False, override_reason=BLIQuoteRecordOverrideReason.OTHER)
    rec = record(
        override_applied=True,
        override_reason=BLIQuoteRecordOverrideReason.MARKET_COLOR,
        override_note="manual synthetic adjustment",
        exclusion_reasons=(BLIQuoteRecordExclusionReason.MANUAL_REVIEW,),
    )
    assert rec.override_reason is BLIQuoteRecordOverrideReason.MARKET_COLOR
