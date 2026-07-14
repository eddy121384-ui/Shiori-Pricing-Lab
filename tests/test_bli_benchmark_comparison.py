"""Tests for ``compare_bli_benchmark`` (Issue #98 PR B deterministic comparison).

All fixtures here are clearly synthetic placeholder numbers -- picked only to
exercise the residual/threshold/mismatch-priority logic in isolation. None
of them are Bloomberg output, a golden case, market calibration, or UAT
evidence; Issue #94's sanitized Bloomberg golden case remains open and
unconsumed by this test file.
"""

from __future__ import annotations

import ast
import inspect
import math
from dataclasses import FrozenInstanceError, asdict

import pytest

from shiori_pricing_lab.data.bli_benchmark_quote import (
    BLIBenchmarkQuote,
    BLIBenchmarkQuoteSide,
    BLIBenchmarkSourceType,
)
from shiori_pricing_lab.data.bli_mvp_input_bundle_fixtures import (
    SYNTHETIC_BLI_MVP_INPUT_BUNDLE,
)
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.pricing import bli_benchmark_comparison as bli_benchmark_comparison_module
from shiori_pricing_lab.pricing.bli_benchmark_comparison import (
    BLIBenchmarkComparisonMetric,
    BLIBenchmarkComparisonReason,
    BLIBenchmarkComparisonResult,
    BLIBenchmarkComparisonStatus,
    compare_bli_benchmark,
)
from shiori_pricing_lab.pricing.result import PricingResult, PricingStatus
from shiori_pricing_lab.products.enums import Currency, PayReceive, TreasuryFTPQuoteSide

# --- Shared synthetic fixtures (mirrors tests/test_bli_standalone_option_request.py) --

_BOND_OPTION = SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option
_REFERENCE_DATA = SYNTHETIC_BLI_MVP_INPUT_BUNDLE.resolved_bond_reference_data
_VALUATION_DATE = SYNTHETIC_BLI_MVP_INPUT_BUNDLE.valuation_date
_SNAPSHOT = SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT

# product_id="BONDOPT-SYNTHETIC-0001", underlying_isin="XS0000000001",
# currency=USD, valuation_date="2026-07-01",
# snapshot_id="SYNTHETIC_BLI_SNAPSHOT_0001", as_of_timestamp="2026-07-01T16:00:00Z"


def _make_request(**overrides: object) -> BLIStandaloneBondOptionRequest:
    params = dict(
        bond_option=_BOND_OPTION,
        resolved_bond_reference_data=_REFERENCE_DATA,
        valuation_date=_VALUATION_DATE,
        market_data_snapshot=_SNAPSHOT,
    )
    params.update(overrides)
    return BLIStandaloneBondOptionRequest(**params)


def _make_pricing_result(**overrides: object) -> PricingResult:
    params = dict(
        product_id=_BOND_OPTION.product_id,
        product_type=_BOND_OPTION.product_type,
        valuation_date=_VALUATION_DATE,
        result_currency=_BOND_OPTION.currency.value,
        status=PricingStatus.SUCCESS,
        engine_name="TEST_ENGINE",
        engine_version="0.0.0",
        method="TEST_METHOD",
        market_data_as_of=_SNAPSHOT.as_of_timestamp,
        assumptions={"black76_pv_per_100": 4.5},
        pv=45000.0,
    )
    params.update(overrides)
    return PricingResult(**params)


def _make_benchmark(**overrides: object) -> BLIBenchmarkQuote:
    params = dict(
        benchmark_id="BM-0001",
        source_type=BLIBenchmarkSourceType.BLOOMBERG,
        source_system="BLOOMBERG",
        source_as_of="2026-07-01T12:00:00Z",
        retrieved_at="2026-07-01T13:00:00Z",
        quote_side=BLIBenchmarkQuoteSide.MID,
        premium_per_100=4.5,
        total_premium=45000.0,
        currency=Currency.USD,
        product_id=_BOND_OPTION.product_id,
        snapshot_id=_SNAPSHOT.snapshot_id,
        underlying_id=_BOND_OPTION.underlying_isin,
        source_reference="SANITIZED-BENCHMARK-REFERENCE-001",
    )
    params.update(overrides)
    return BLIBenchmarkQuote(**params)


_REQUEST = _make_request()


def _compare(**overrides: object) -> BLIBenchmarkComparisonResult:
    params = dict(
        pricing_result=_make_pricing_result(),
        request=_REQUEST,
        benchmark=_make_benchmark(),
        active_quote_side=BLIBenchmarkQuoteSide.MID,
    )
    params.update(overrides)
    return compare_bli_benchmark(**params)


# --- 1. Exact match / residual direction -------------------------------------


def test_exact_match_is_pass_with_zero_residuals():
    result = _compare()
    assert result.status is BLIBenchmarkComparisonStatus.PASS
    assert result.reason is BLIBenchmarkComparisonReason.COMPARABLE
    assert result.comparison_metric is BLIBenchmarkComparisonMetric.RELATIVE_RESIDUAL_PER_100
    assert result.signed_residual_per_100 == 0.0
    assert result.absolute_residual_per_100 == 0.0
    assert result.relative_residual == 0.0


def test_positive_signed_residual_when_model_above_benchmark():
    pricing_result = _make_pricing_result(assumptions={"black76_pv_per_100": 5.0})
    result = _compare(pricing_result=pricing_result, benchmark=_make_benchmark(premium_per_100=4.5))
    assert result.signed_residual_per_100 == pytest.approx(0.5)
    assert result.absolute_residual_per_100 == pytest.approx(0.5)


def test_negative_signed_residual_when_model_below_benchmark():
    pricing_result = _make_pricing_result(assumptions={"black76_pv_per_100": 4.0})
    result = _compare(pricing_result=pricing_result, benchmark=_make_benchmark(premium_per_100=4.5))
    assert result.signed_residual_per_100 == pytest.approx(-0.5)
    assert result.absolute_residual_per_100 == pytest.approx(0.5)


# --- 2. Threshold boundaries ---------------------------------------------------


def _compare_with_relative_residual(
    relative_residual: float, **overrides
) -> BLIBenchmarkComparisonResult:
    benchmark = _make_benchmark(premium_per_100=100.0)
    model_per_100 = 100.0 + relative_residual * 100.0
    pricing_result = _make_pricing_result(assumptions={"black76_pv_per_100": model_per_100})
    return _compare(pricing_result=pricing_result, benchmark=benchmark, **overrides)


def test_just_below_pass_threshold_is_pass():
    result = _compare_with_relative_residual(0.0199)
    assert result.status is BLIBenchmarkComparisonStatus.PASS


def test_exactly_two_percent_is_warning():
    result = _compare_with_relative_residual(0.02)
    assert result.status is BLIBenchmarkComparisonStatus.WARNING
    assert result.relative_residual == pytest.approx(0.02)


def test_exactly_five_percent_is_warning():
    result = _compare_with_relative_residual(0.05)
    assert result.status is BLIBenchmarkComparisonStatus.WARNING
    assert result.relative_residual == pytest.approx(0.05)


def test_above_five_percent_is_fail():
    result = _compare_with_relative_residual(0.0501)
    assert result.status is BLIBenchmarkComparisonStatus.FAIL


def test_caller_configured_thresholds_drive_status_and_are_stored():
    # 12% relative residual would FAIL under the 0.02/0.05 defaults, but
    # PASSes below and WARNs within a custom, wider 0.10/0.20 band.
    result = _compare_with_relative_residual(0.12, pass_threshold=0.10, fail_threshold=0.20)
    assert result.status is BLIBenchmarkComparisonStatus.WARNING
    assert result.pass_threshold == 0.10
    assert result.fail_threshold == 0.20


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pass_threshold": -0.01},
        {"pass_threshold": 0.05, "fail_threshold": 0.05},
        {"pass_threshold": 0.10, "fail_threshold": 0.05},
        {"pass_threshold": math.nan},
        {"fail_threshold": math.inf},
        {"pass_threshold": True},
        {"fail_threshold": False},
        {"near_zero_threshold_per_100": -0.01},
        {"near_zero_threshold_per_100": math.nan},
    ],
)
def test_invalid_threshold_configuration_rejected(kwargs):
    with pytest.raises(ValueError):
        _compare(**kwargs)


# --- 3. Near-zero denominator ---------------------------------------------------


def test_near_zero_benchmark_below_threshold_is_non_comparable():
    pricing_result = _make_pricing_result(assumptions={"black76_pv_per_100": 0.02})
    benchmark = _make_benchmark(premium_per_100=0.005)
    result = _compare(pricing_result=pricing_result, benchmark=benchmark)
    assert result.status is BLIBenchmarkComparisonStatus.NON_COMPARABLE
    assert result.reason is BLIBenchmarkComparisonReason.NEAR_ZERO_BENCHMARK
    assert result.signed_residual_per_100 == pytest.approx(0.015)
    assert result.absolute_residual_per_100 == pytest.approx(0.015)
    assert result.relative_residual is None


def test_near_zero_benchmark_exactly_at_threshold_is_non_comparable():
    pricing_result = _make_pricing_result(assumptions={"black76_pv_per_100": 0.02})
    benchmark = _make_benchmark(premium_per_100=0.01)
    result = _compare(pricing_result=pricing_result, benchmark=benchmark)
    assert result.status is BLIBenchmarkComparisonStatus.NON_COMPARABLE
    assert result.reason is BLIBenchmarkComparisonReason.NEAR_ZERO_BENCHMARK
    assert result.signed_residual_per_100 == pytest.approx(0.01)
    assert result.absolute_residual_per_100 == pytest.approx(0.01)
    assert result.relative_residual is None


def test_just_above_near_zero_threshold_computes_relative_residual_normally():
    pricing_result = _make_pricing_result(assumptions={"black76_pv_per_100": 0.0101})
    benchmark = _make_benchmark(premium_per_100=0.0101)
    result = _compare(pricing_result=pricing_result, benchmark=benchmark)
    assert result.status is BLIBenchmarkComparisonStatus.PASS
    assert result.reason is BLIBenchmarkComparisonReason.COMPARABLE
    assert result.relative_residual == pytest.approx(0.0)


# --- 4. Model result / premium eligibility --------------------------------------


def test_failed_pricing_result_is_model_result_not_success():
    pricing_result = _make_pricing_result(status=PricingStatus.FAILED, pv=None, assumptions={})
    result = _compare(pricing_result=pricing_result)
    assert result.status is BLIBenchmarkComparisonStatus.NON_COMPARABLE
    assert result.reason is BLIBenchmarkComparisonReason.MODEL_RESULT_NOT_SUCCESS
    assert result.signed_residual_per_100 is None
    assert result.absolute_residual_per_100 is None
    assert result.relative_residual is None
    assert result.model_fair_premium_per_100 is None
    assert result.model_total_premium is None


def test_success_with_warnings_is_eligible_for_comparison():
    pricing_result = _make_pricing_result(status=PricingStatus.SUCCESS_WITH_WARNINGS)
    result = _compare(pricing_result=pricing_result)
    assert result.status is BLIBenchmarkComparisonStatus.PASS


@pytest.mark.parametrize(
    "overrides",
    [
        {"pv": None},
        {"assumptions": {}},
        {"pv": True},
        {"pv": math.nan},
        {"pv": math.inf},
        {"assumptions": {"black76_pv_per_100": True}},
        {"assumptions": {"black76_pv_per_100": math.nan}},
        {"assumptions": {"black76_pv_per_100": math.inf}},
        {"assumptions": {"black76_pv_per_100": "4.5"}},
    ],
)
def test_missing_or_invalid_model_premium_is_model_premium_unavailable(overrides):
    pricing_result = _make_pricing_result(**overrides)
    result = _compare(pricing_result=pricing_result)
    assert result.status is BLIBenchmarkComparisonStatus.NON_COMPARABLE
    assert result.reason is BLIBenchmarkComparisonReason.MODEL_PREMIUM_UNAVAILABLE
    assert result.signed_residual_per_100 is None
    assert result.absolute_residual_per_100 is None
    assert result.relative_residual is None


def test_model_premium_unavailable_preserves_the_available_side():
    # pv is missing/invalid but black76_pv_per_100 is a valid number -- the
    # valid one is preserved, the invalid one is None.
    pricing_result = _make_pricing_result(pv=None)
    result = _compare(pricing_result=pricing_result)
    assert result.reason is BLIBenchmarkComparisonReason.MODEL_PREMIUM_UNAVAILABLE
    assert result.model_total_premium is None
    assert result.model_fair_premium_per_100 == pytest.approx(4.5)


# --- 5. Identity / currency / side / date mismatches ----------------------------


def test_product_id_mismatch():
    pricing_result = _make_pricing_result(product_id="OTHER-PRODUCT-ID")
    result = _compare(pricing_result=pricing_result)
    assert result.status is BLIBenchmarkComparisonStatus.NON_COMPARABLE
    assert result.reason is BLIBenchmarkComparisonReason.PRODUCT_ID_MISMATCH
    assert result.signed_residual_per_100 is None
    # Model premium was available -- still preserved on a mismatch result.
    assert result.model_fair_premium_per_100 == pytest.approx(4.5)


def test_snapshot_id_mismatch():
    result = _compare(benchmark=_make_benchmark(snapshot_id="OTHER-SNAPSHOT-ID"))
    assert result.status is BLIBenchmarkComparisonStatus.NON_COMPARABLE
    assert result.reason is BLIBenchmarkComparisonReason.SNAPSHOT_ID_MISMATCH


def test_underlying_id_mismatch():
    result = _compare(benchmark=_make_benchmark(underlying_id="XS9999999999"))
    assert result.status is BLIBenchmarkComparisonStatus.NON_COMPARABLE
    assert result.reason is BLIBenchmarkComparisonReason.UNDERLYING_ID_MISMATCH


def test_currency_mismatch():
    pricing_result = _make_pricing_result(result_currency="EUR")
    result = _compare(pricing_result=pricing_result)
    assert result.status is BLIBenchmarkComparisonStatus.NON_COMPARABLE
    assert result.reason is BLIBenchmarkComparisonReason.CURRENCY_MISMATCH


def test_quote_side_mismatch():
    result = _compare(
        benchmark=_make_benchmark(quote_side=BLIBenchmarkQuoteSide.BID),
        active_quote_side=BLIBenchmarkQuoteSide.OFFER,
    )
    assert result.status is BLIBenchmarkComparisonStatus.NON_COMPARABLE
    assert result.reason is BLIBenchmarkComparisonReason.QUOTE_SIDE_MISMATCH


def test_source_date_mismatch():
    result = _compare(benchmark=_make_benchmark(source_as_of="2026-07-02T09:00:00Z"))
    assert result.status is BLIBenchmarkComparisonStatus.NON_COMPARABLE
    assert result.reason is BLIBenchmarkComparisonReason.SOURCE_DATE_MISMATCH


def test_source_date_match_ignores_time_of_day():
    # Same calendar date, different time-of-day than the snapshot as-of --
    # calendar-date equality only, never full timestamp equality.
    result = _compare(benchmark=_make_benchmark(source_as_of="2026-07-01T23:59:00Z"))
    assert result.status is BLIBenchmarkComparisonStatus.PASS


def test_retrieved_at_difference_alone_does_not_change_comparability():
    result = _compare(benchmark=_make_benchmark(retrieved_at="2030-01-01T00:00:00Z"))
    assert result.status is BLIBenchmarkComparisonStatus.PASS
    assert result.benchmark_retrieved_at == "2030-01-01T00:00:00Z"


# --- 6. Mismatch priority ---------------------------------------------------------


def test_mismatch_priority_model_result_beats_everything():
    pricing_result = _make_pricing_result(
        status=PricingStatus.FAILED, pv=None, assumptions={}, product_id="OTHER-PRODUCT-ID"
    )
    result = _compare(
        pricing_result=pricing_result,
        benchmark=_make_benchmark(snapshot_id="OTHER-SNAPSHOT-ID"),
    )
    assert result.reason is BLIBenchmarkComparisonReason.MODEL_RESULT_NOT_SUCCESS


def test_mismatch_priority_product_id_beats_snapshot_and_underlying():
    pricing_result = _make_pricing_result(product_id="OTHER-PRODUCT-ID")
    result = _compare(
        pricing_result=pricing_result,
        benchmark=_make_benchmark(snapshot_id="OTHER-SNAPSHOT-ID", underlying_id="XS9999999999"),
    )
    assert result.reason is BLIBenchmarkComparisonReason.PRODUCT_ID_MISMATCH


def test_mismatch_priority_underlying_id_beats_currency_and_side():
    result = _compare(
        benchmark=_make_benchmark(
            underlying_id="XS9999999999", quote_side=BLIBenchmarkQuoteSide.BID
        ),
        active_quote_side=BLIBenchmarkQuoteSide.OFFER,
    )
    assert result.reason is BLIBenchmarkComparisonReason.UNDERLYING_ID_MISMATCH


def test_mismatch_priority_quote_side_beats_source_date():
    result = _compare(
        benchmark=_make_benchmark(
            quote_side=BLIBenchmarkQuoteSide.BID, source_as_of="2026-07-02T09:00:00Z"
        ),
        active_quote_side=BLIBenchmarkQuoteSide.OFFER,
    )
    assert result.reason is BLIBenchmarkComparisonReason.QUOTE_SIDE_MISMATCH


def test_mismatch_priority_source_date_beats_near_zero():
    pricing_result = _make_pricing_result(assumptions={"black76_pv_per_100": 0.02})
    result = _compare(
        pricing_result=pricing_result,
        benchmark=_make_benchmark(premium_per_100=0.005, source_as_of="2026-07-02T09:00:00Z"),
    )
    assert result.reason is BLIBenchmarkComparisonReason.SOURCE_DATE_MISMATCH


# --- 7. Total-premium isolation ----------------------------------------------------


def test_totals_are_preserved_but_do_not_affect_status():
    pricing_result = _make_pricing_result(pv=999_999_999.0)
    result = _compare(pricing_result=pricing_result, benchmark=_make_benchmark(total_premium=1.0))
    assert result.status is BLIBenchmarkComparisonStatus.PASS
    assert result.model_total_premium == 999_999_999.0
    assert result.benchmark_total_premium == 1.0


# --- 8. active_quote_side enum boundary --------------------------------------------


@pytest.mark.parametrize("side", list(BLIBenchmarkQuoteSide))
def test_native_active_quote_side_members_accepted(side):
    result = _compare(benchmark=_make_benchmark(quote_side=side), active_quote_side=side)
    assert result.active_quote_side is side


@pytest.mark.parametrize("raw", ["BID", "MID", "OFFER"])
def test_raw_string_active_quote_sides_accepted(raw):
    result = _compare(
        benchmark=_make_benchmark(quote_side=BLIBenchmarkQuoteSide(raw)), active_quote_side=raw
    )
    assert result.active_quote_side is BLIBenchmarkQuoteSide(raw)


@pytest.mark.parametrize("foreign_side", list(TreasuryFTPQuoteSide))
def test_foreign_treasury_ftp_active_quote_side_rejected(foreign_side):
    with pytest.raises(ValueError, match="active_quote_side"):
        _compare(active_quote_side=foreign_side)


def test_unrelated_foreign_enum_active_quote_side_rejected():
    with pytest.raises(ValueError, match="active_quote_side"):
        _compare(active_quote_side=PayReceive.PAY)


# --- 9. Input type validation -------------------------------------------------------


def test_wrong_pricing_result_type_raises_type_error():
    with pytest.raises(TypeError, match="pricing_result"):
        compare_bli_benchmark(
            pricing_result="not-a-pricing-result",
            request=_REQUEST,
            benchmark=_make_benchmark(),
            active_quote_side=BLIBenchmarkQuoteSide.MID,
        )


def test_wrong_request_type_raises_type_error():
    with pytest.raises(TypeError, match="request"):
        compare_bli_benchmark(
            pricing_result=_make_pricing_result(),
            request="not-a-request",
            benchmark=_make_benchmark(),
            active_quote_side=BLIBenchmarkQuoteSide.MID,
        )


def test_wrong_benchmark_type_raises_type_error():
    with pytest.raises(TypeError, match="benchmark"):
        compare_bli_benchmark(
            pricing_result=_make_pricing_result(),
            request=_REQUEST,
            benchmark="not-a-benchmark",
            active_quote_side=BLIBenchmarkQuoteSide.MID,
        )


# --- 10. Immutability / determinism / no mutation -----------------------------------


def test_result_is_frozen():
    result = _compare()
    with pytest.raises(FrozenInstanceError):
        result.status = BLIBenchmarkComparisonStatus.FAIL  # type: ignore[misc]


def test_asdict_is_deterministic():
    result = _compare()
    assert asdict(result) == asdict(result)
    second = _compare()
    assert asdict(result) == asdict(second)


def test_inputs_are_not_mutated():
    pricing_result = _make_pricing_result()
    request = _REQUEST
    benchmark = _make_benchmark()

    pricing_result_before = asdict(pricing_result)
    request_before = asdict(request)
    benchmark_before = asdict(benchmark)

    compare_bli_benchmark(
        pricing_result=pricing_result,
        request=request,
        benchmark=benchmark,
        active_quote_side=BLIBenchmarkQuoteSide.MID,
    )

    assert asdict(pricing_result) == pricing_result_before
    assert asdict(request) == request_before
    assert asdict(benchmark) == benchmark_before


def test_result_is_independent_object_not_a_pricing_result_or_request():
    result = _compare()
    assert not isinstance(result, PricingResult)
    assert not isinstance(result, BLIStandaloneBondOptionRequest)


# --- 11. Module boundary -------------------------------------------------------------


def test_module_imports_only_the_expected_dependencies():
    tree = ast.parse(inspect.getsource(bli_benchmark_comparison_module))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
    assert imported_names == {
        "__future__",
        "math",
        "dataclasses",
        "datetime",
        "enum",
        "shiori_pricing_lab.data._validation",
        "shiori_pricing_lab.data.bli_benchmark_quote",
        "shiori_pricing_lab.data.bli_standalone_option_request",
        "shiori_pricing_lab.pricing.result",
        "shiori_pricing_lab.products.enums",
    }


def test_module_defines_no_pricing_provider_ui_or_persistence_names():
    module_names = set(dir(bli_benchmark_comparison_module))
    forbidden_names = {
        "price_bli_mvp_standalone_option",
        "BLIMVPInputBundle",
        "BLIMarketDataSnapshot",
        "BondOption",
        "requests",
        "socket",
        "streamlit",
        "client_quote",
        "ClientQuote",
    }
    assert module_names.isdisjoint(forbidden_names)


def test_module_source_does_not_use_system_clock():
    source = inspect.getsource(bli_benchmark_comparison_module)
    assert "date.today(" not in source
    assert "datetime.now(" not in source
    assert "import datetime" not in source
    assert "from datetime import datetime" not in source
