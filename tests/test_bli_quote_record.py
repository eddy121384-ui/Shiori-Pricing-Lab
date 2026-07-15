import ast
import inspect
from dataclasses import FrozenInstanceError, fields

import pytest

from shiori_pricing_lab.data.bli_benchmark_quote import (
    BLIBenchmarkQuote,
    BLIBenchmarkQuoteSide,
    BLIBenchmarkSourceType,
)
from shiori_pricing_lab.data.bli_mvp_input_bundle_fixtures import (
    SYNTHETIC_BLI_MVP_INPUT_BUNDLE,
)
from shiori_pricing_lab.data.bli_quote_record import BLIQuoteRecord
from shiori_pricing_lab.data.bli_snapshot import BLIVolatilityBasis
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
    PricingMessage,
    PricingResult,
    PricingStatus,
    PricingWarningCode,
)
from shiori_pricing_lab.products.enums import OptionType

QUOTE_ID = "123e4567-e89b-42d3-a456-426614174000"
BAD_UUIDS = [
    "123e4567-e89b-12d3-a456-426614174000",
    "123E4567-e89b-42d3-a456-426614174000",
    "123e4567e89b42d3a456426614174000",
]


def request(**overrides):
    params = dict(
        bond_option=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        resolved_bond_reference_data=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.resolved_bond_reference_data,
        valuation_date=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.valuation_date,
        market_data_snapshot=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.market_data_snapshot,
    )
    params.update(overrides)
    return BLIStandaloneBondOptionRequest(**params)


def pricing_result(**overrides):
    req = request()
    params = dict(
        product_id=req.bond_option.product_id,
        product_type=req.bond_option.product_type,
        valuation_date=req.valuation_date,
        result_currency=req.bond_option.currency.value,
        status=PricingStatus.SUCCESS,
        engine_name="SYNTHETIC_ENGINE",
        engine_version="1.0",
        method="BLACK76_PRICE_VOL",
        market_data_as_of=req.market_data_snapshot.as_of_timestamp,
        warnings=(),
        assumptions={"black76_pv_per_100": 4.5},
        pv=2250.0,
        diagnostics={},
    )
    params.update(overrides)
    return PricingResult(**params)


def benchmark_quote(**overrides):
    req = request()
    params = dict(
        benchmark_id="BM-0001",
        source_type=BLIBenchmarkSourceType.BLOOMBERG,
        source_system="SYNTHETIC_BENCHMARK_EXPORT",
        source_as_of=req.market_data_snapshot.as_of_timestamp,
        retrieved_at="2026-07-01T16:01:00Z",
        quote_side=BLIBenchmarkQuoteSide.MID,
        premium_per_100=4.5,
        total_premium=2250.0,
        currency=req.bond_option.currency,
        product_id=req.bond_option.product_id,
        snapshot_id=req.market_data_snapshot.snapshot_id,
        underlying_id=req.bond_option.underlying_isin,
        source_reference="synthetic benchmark fixture",
        notes="synthetic benchmark note",
    )
    params.update(overrides)
    return BLIBenchmarkQuote(**params)


def benchmark_comparison(**overrides):
    req = request()
    bench = benchmark_quote()
    pricing = pricing_result()
    params = dict(
        status=BLIBenchmarkComparisonStatus.PASS,
        reason=BLIBenchmarkComparisonReason.COMPARABLE,
        comparison_metric=BLIBenchmarkComparisonMetric.RELATIVE_RESIDUAL_PER_100,
        active_quote_side=bench.quote_side,
        pass_threshold=0.02,
        fail_threshold=0.05,
        near_zero_threshold_per_100=0.01,
        product_id=req.bond_option.product_id,
        snapshot_id=req.market_data_snapshot.snapshot_id,
        underlying_id=req.bond_option.underlying_isin,
        currency=req.bond_option.currency,
        valuation_date=req.valuation_date,
        market_data_as_of=req.market_data_snapshot.as_of_timestamp,
        benchmark_id=bench.benchmark_id,
        benchmark_source_as_of=bench.source_as_of,
        benchmark_retrieved_at=bench.retrieved_at,
        model_fair_premium_per_100=pricing.assumptions["black76_pv_per_100"],
        model_total_premium=pricing.pv,
        benchmark_premium_per_100=bench.premium_per_100,
        benchmark_total_premium=bench.total_premium,
        signed_residual_per_100=0.0,
        absolute_residual_per_100=0.0,
        relative_residual=0.0,
        alignment_note="synthetic comparable quote",
    )
    params.update(overrides)
    return BLIBenchmarkComparisonResult(**params)


def solver_result(**overrides):
    params = dict(
        status=BLIImpliedPriceVolSolverStatus.SUCCESS,
        reason=BLIImpliedPriceVolSolverReason.CONVERGED,
        option_type=OptionType.CALL,
        forward_clean_price=101.0,
        strike_clean_price=100.0,
        time_to_expiry=0.5,
        discount_factor=0.98,
        target_premium_per_100=4.5,
        lower_price_vol=0.000001,
        upper_price_vol=5.0,
        premium_tolerance_per_100=1e-8,
        price_vol_tolerance=1e-8,
        max_iterations=100,
        arbitrage_lower_bound_per_100=0.98,
        arbitrage_upper_bound_per_100=98.98,
        lower_bound_model_premium_per_100=0.98,
        upper_bound_model_premium_per_100=98.98,
        implied_price_vol=0.18,
        model_premium_per_100=4.5,
        premium_residual_per_100=0.0,
        iterations=12,
        final_bracket_lower_price_vol=0.179999,
        final_bracket_upper_price_vol=0.180001,
        diagnostic_note="synthetic solver convergence",
    )
    params.update(overrides)
    return BLIImpliedPriceVolSolverResult(**params)


def calibration_result(**overrides):
    req = request()
    bench = benchmark_quote()
    params = dict(
        status=BLIImpliedPriceVolCalibrationStatus.SUCCESS,
        reason=BLIImpliedPriceVolCalibrationReason.CALIBRATED,
        active_quote_side=bench.quote_side,
        pricing_engine_name="SYNTHETIC_ENGINE",
        pricing_engine_version="1.0",
        product_id=req.bond_option.product_id,
        snapshot_id=req.market_data_snapshot.snapshot_id,
        underlying_id=req.bond_option.underlying_isin,
        currency=req.bond_option.currency,
        valuation_date=req.valuation_date,
        market_data_as_of=req.market_data_snapshot.as_of_timestamp,
        option_type=req.bond_option.option_type,
        expiry_date=req.bond_option.expiry_date,
        request_notional=req.bond_option.notional,
        original_volatility=req.market_data_snapshot.volatility_input.volatility,
        original_volatility_basis=BLIVolatilityBasis.PRICE_VOL,
        benchmark_id=bench.benchmark_id,
        benchmark_source_type=bench.source_type,
        benchmark_source_system=bench.source_system,
        benchmark_source_as_of=bench.source_as_of,
        benchmark_retrieved_at=bench.retrieved_at,
        benchmark_quote_side=bench.quote_side,
        benchmark_premium_per_100=bench.premium_per_100,
        benchmark_total_premium=bench.total_premium,
        benchmark_source_reference=bench.source_reference,
        benchmark_notes=bench.notes,
        request_support_reasons=(),
        forward_clean_price=101.0,
        strike_clean_price=req.bond_option.strike_price,
        time_to_expiry=0.5,
        option_discount_factor=0.98,
        solver_result=solver_result(option_type=req.bond_option.option_type),
        resolution_error_type=None,
        resolution_error_message=None,
        diagnostic_note="synthetic calibration",
    )
    params.update(overrides)
    return BLIImpliedPriceVolCalibrationResult(**params)


def record(**overrides):
    params = dict(
        schema_version=1,
        quote_id=QUOTE_ID,
        quote_version=1,
        saved_at="2026-07-01T16:02:00Z",
        operator_id="synthetic.operator",
        request=request(),
        pricing_result=pricing_result(),
        benchmark_quote=benchmark_quote(),
        benchmark_comparison=benchmark_comparison(),
        calibration_result=calibration_result(),
        client_quote_premium_per_100=None,
        client_quote_total_premium=None,
        trader_adjustment_per_100=None,
        trader_adjustment_total=None,
        override_reason=None,
        exclusions=("manual-review-ok", "synthetic-fixture"),
    )
    params.update(overrides)
    return BLIQuoteRecord(**params)


def test_exact_16_field_contract_and_frozen():
    assert [field.name for field in fields(BLIQuoteRecord)] == [
        "schema_version",
        "quote_id",
        "quote_version",
        "saved_at",
        "operator_id",
        "request",
        "pricing_result",
        "benchmark_quote",
        "benchmark_comparison",
        "calibration_result",
        "client_quote_premium_per_100",
        "client_quote_total_premium",
        "trader_adjustment_per_100",
        "trader_adjustment_total",
        "override_reason",
        "exclusions",
    ]
    rec = record()
    with pytest.raises(FrozenInstanceError):
        rec.quote_version = 2


def test_valid_construction_with_existing_contracts_and_optional_fields():
    rec = record(client_quote_premium_per_100=4.5, client_quote_total_premium=2250.0)
    assert rec.request.bond_option.product_id == rec.pricing_result.product_id
    assert rec.calibration_result is not None
    assert rec.override_reason is None


@pytest.mark.parametrize("value", [True, 1.0, 2, "not-a-uuid", *BAD_UUIDS])
def test_uuid4_canonical_validation(value):
    with pytest.raises((TypeError, ValueError)):
        record(quote_id=value)


@pytest.mark.parametrize("value", [True, 0, -1, 1.0])
def test_quote_version_validation(value):
    with pytest.raises((TypeError, ValueError)):
        record(quote_version=value)


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-01",
        "2026-07-01T16:02Z",
        "2026-07-01T16:02:00+00:00",
        "2026-07-01T16:02:00.000Z",
        "2026-07-01T16:02:00z",
        "2026-07-01 16:02:00Z",
        "2026-02-30T16:02:00Z",
        "2026-07-01T25:61:61Z",
    ],
)
def test_saved_at_exact_real_utc_seconds(value):
    with pytest.raises(ValueError):
        record(saved_at=value)


def test_saved_at_stores_original_text_unchanged():
    rec = record(saved_at="2026-07-01T00:00:00Z")
    assert rec.saved_at == "2026-07-01T00:00:00Z"


@pytest.mark.parametrize("value", ["", " operator", "operator ", "   "])
def test_operator_id_must_be_non_blank_and_stripped(value):
    with pytest.raises(ValueError):
        record(operator_id=value)


def test_only_successful_pricing_result_allowed():
    with pytest.raises(ValueError):
        record(pricing_result=pricing_result(status=PricingStatus.FAILED))


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("pricing_result", pricing_result(product_id="OTHER")),
        ("benchmark_quote", benchmark_quote(snapshot_id="OTHER")),
        ("benchmark_comparison", benchmark_comparison(benchmark_id="OTHER")),
        ("calibration_result", calibration_result(benchmark_retrieved_at="2026-07-01T16:09:00Z")),
    ],
)
def test_alignment_mismatches_rejected(field, bad_value):
    with pytest.raises(ValueError):
        record(**{field: bad_value})


# --- Calibration direct-source provenance alignment (Issue #100 comment 4975917315) --


@pytest.mark.parametrize(
    "override",
    [
        {"pricing_engine_name": "OTHER_ENGINE"},
        {"pricing_engine_version": "9.9"},
        {"option_type": OptionType.PUT},
        {"expiry_date": "2099-01-01"},
        {"request_notional": 999999.0},
        {"original_volatility": 0.99},
        {"original_volatility_basis": BLIVolatilityBasis.YIELD_VOL},
    ],
    ids=[
        "pricing_engine_name",
        "pricing_engine_version",
        "option_type",
        "expiry_date",
        "request_notional",
        "original_volatility",
        "original_volatility_basis",
    ],
)
def test_calibration_direct_source_provenance_mismatch_rejected(override):
    # Each of these calibration fields is a verbatim copy of a value that
    # already has one authoritative source elsewhere in the record (the
    # stored request or the stored model PricingResult); a mismatch on any
    # one of the seven must be rejected on its own, deterministically.
    with pytest.raises(ValueError):
        record(calibration_result=calibration_result(**override))


def test_calibration_direct_source_provenance_aligned_record_round_trips():
    rec = record(calibration_result=calibration_result())
    assert rec.calibration_result.pricing_engine_name == rec.pricing_result.engine_name
    assert rec.calibration_result.pricing_engine_version == rec.pricing_result.engine_version
    assert rec.calibration_result.option_type is rec.request.bond_option.option_type
    assert rec.calibration_result.expiry_date == rec.request.bond_option.expiry_date
    assert rec.calibration_result.request_notional == rec.request.bond_option.notional
    assert (
        rec.calibration_result.original_volatility
        == rec.request.market_data_snapshot.volatility_input.volatility
    )
    assert (
        rec.calibration_result.original_volatility_basis
        is rec.request.market_data_snapshot.volatility_input.volatility_basis
    )


@pytest.mark.parametrize("field,value", [
    ("client_quote_premium_per_100", -0.01),
    ("client_quote_total_premium", float("nan")),
    ("trader_adjustment_per_100", float("inf")),
    ("trader_adjustment_total", True),
])
def test_optional_numeric_validation(field, value):
    with pytest.raises((TypeError, ValueError)):
        record(**{field: value})


_OPTIONAL_NUMERIC_FIELDS = [
    "client_quote_premium_per_100",
    "client_quote_total_premium",
    "trader_adjustment_per_100",
    "trader_adjustment_total",
]


@pytest.mark.parametrize("field", _OPTIONAL_NUMERIC_FIELDS)
def test_optional_numeric_fields_accept_exact_int_and_store_it_unchanged(field):
    # An exact builtin int must be accepted and returned unchanged -- never
    # coerced to float() -- for each of the four optional top-level quote
    # numeric fields.
    rec = record(**{field: 5, "override_reason": "manual override"})
    value = getattr(rec, field)
    assert value == 5
    assert type(value) is int


class _IntSubclass(int):
    pass


class _FloatSubclass(float):
    pass


@pytest.mark.parametrize("field", _OPTIONAL_NUMERIC_FIELDS)
@pytest.mark.parametrize(
    "bad_value", [_IntSubclass(5), _FloatSubclass(5.0)], ids=["int-subclass", "float-subclass"]
)
def test_optional_numeric_fields_reject_numeric_subclasses(field, bad_value):
    with pytest.raises(TypeError):
        record(**{field: bad_value, "override_reason": "manual override"})


def test_exclusions_are_ordered_stripped_strings_not_enums():
    rec = record(exclusions=("first", "second"))
    assert rec.exclusions == ("first", "second")
    with pytest.raises(ValueError):
        record(exclusions=(" first",))
    with pytest.raises(TypeError):
        record(exclusions=["first"])


@pytest.mark.parametrize(
    "overrides",
    [
        {"client_quote_premium_per_100": 4.6},
        {"client_quote_total_premium": 2250.1},
        {"trader_adjustment_per_100": 0.01},
        {"trader_adjustment_total": -1.0},
    ],
)
def test_override_reason_required_for_every_exact_branch(overrides):
    with pytest.raises(ValueError):
        record(**overrides)
    assert record(**overrides, override_reason="manual UTF-8 reason 雨").override_reason


def test_override_reason_forbidden_when_client_quote_equals_model_and_adjustment_zero():
    rec = record(
        client_quote_premium_per_100=4.5,
        client_quote_total_premium=2250.0,
        trader_adjustment_per_100=0.0,
        trader_adjustment_total=0.0,
    )
    assert rec.override_reason is None
    with pytest.raises(ValueError):
        record(override_reason="unneeded")


def test_no_forbidden_boundary_imports_or_calls():
    module = __import__("shiori_pricing_lab.data.bli_quote_record", fromlist=["x"])
    source = inspect.getsource(module)
    tree = ast.parse(source)
    forbidden_import_roots = {"os", "pathlib", "tempfile", "uuid", "getpass", "sqlite3"}
    imported = {
        alias.name.split(".")[0]
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert forbidden_import_roots.isdisjoint(imported)
    forbidden_names = {
        "date",
        "datetime",
        "uuid4",
        "price_bli_mvp",
        "calibrate_bli_implied_price_vol",
    }
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert forbidden_names.isdisjoint(called)


# --- Exact native object types (subclasses rejected) --------------------------


def _subclass_instance(obj):
    base_cls = type(obj)
    subclass = type(f"_{base_cls.__name__}Subclass", (base_cls,), {})
    kwargs = {f.name: getattr(obj, f.name) for f in fields(base_cls) if f.init}
    return subclass(**kwargs)


@pytest.mark.parametrize(
    "field_name,build_base",
    [
        ("request", request),
        ("pricing_result", pricing_result),
        ("benchmark_quote", benchmark_quote),
        ("benchmark_comparison", benchmark_comparison),
        ("calibration_result", calibration_result),
    ],
)
def test_subclass_of_native_object_rejected(field_name, build_base):
    subclassed = _subclass_instance(build_base())
    with pytest.raises(TypeError):
        record(**{field_name: subclassed})


def test_calibration_result_none_still_accepted_alongside_exact_type_check():
    rec = record(calibration_result=None)
    assert rec.calibration_result is None


# --- Required model premium anchors -------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"pv": None},
        {"pv": float("nan")},
        {"pv": float("inf")},
        {"pv": float("-inf")},
        {"pv": True},
        {"assumptions": {}},
        {"assumptions": {"black76_pv_per_100": float("nan")}},
        {"assumptions": {"black76_pv_per_100": float("inf")}},
        {"assumptions": {"black76_pv_per_100": True}},
    ],
)
def test_missing_or_invalid_model_anchor_rejected(overrides):
    with pytest.raises(ValueError):
        record(pricing_result=pricing_result(**overrides))


def test_missing_pv_anchor_fails_before_override_evaluation_even_with_client_quote():
    # A present client quote would otherwise trigger the override-invariant
    # equality check; the missing pv anchor must fail first and explicitly,
    # never silently comparing the client value against a missing anchor.
    bad_pricing = pricing_result(pv=None)
    with pytest.raises(ValueError, match="pricing_result.pv"):
        record(pricing_result=bad_pricing, client_quote_premium_per_100=4.5)


def test_missing_per_100_anchor_fails_before_override_evaluation_even_with_client_quote():
    bad_pricing = pricing_result(assumptions={})
    with pytest.raises(ValueError, match="black76_pv_per_100"):
        record(pricing_result=bad_pricing, client_quote_premium_per_100=4.5)


def test_bool_per_100_anchor_rejected_even_with_matching_client_total():
    bad_pricing = pricing_result(assumptions={"black76_pv_per_100": True})
    with pytest.raises(ValueError, match="black76_pv_per_100"):
        record(pricing_result=bad_pricing, client_quote_total_premium=2250.0)


# --- Successful-status policy ---------------------------------------------------


def test_success_with_warnings_pricing_result_accepted():
    warning_pricing = pricing_result(
        status=PricingStatus.SUCCESS_WITH_WARNINGS,
        warnings=(
            PricingMessage(
                code=PricingWarningCode.DATA_QUALITY,
                message="synthetic data-quality warning",
                detail={"note": "synthetic"},
            ),
        ),
    )
    rec = record(pricing_result=warning_pricing)
    assert rec.pricing_result.status is PricingStatus.SUCCESS_WITH_WARNINGS
    assert rec.pricing_result.warnings[0].message == "synthetic data-quality warning"
