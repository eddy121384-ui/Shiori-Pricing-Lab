"""Tests for ``calibrate_bli_implied_price_vol`` (Issue #99 PR B wiring; Issue #P1-06
dirty-price OVME realignment).

All fixtures are explicitly synthetic. None represents Bloomberg output,
market calibration evidence, a golden case, or UAT -- the sanitized OVME
golden case (fixture reused, not duplicated, from
``tests/test_bli_ovme_golden_case.py``) exercises the full calibrate ->
reprice round trip in that file.

**Issue #P1-06: the methodology-alignment block is lifted.** Calibration no
longer returns ``METHODOLOGY_NOT_ALIGNED`` for a coherent request. After the
identical coherence gates (request support, identity, source date) and
solver-configuration validation, it resolves the OVME-aligned dirty
forward/strike, ACT/ACT option time, and effective reporting-date discount
factor via ``resolve_standalone_option_pricing_inputs`` (the same resolver
``price_bli_mvp_standalone_option`` itself uses) exactly once, then calls
the dirty-price ``solve_implied_dirty_price_vol`` exactly once against
``benchmark.premium_per_100``.

Several tests below monkeypatch ``resolve_standalone_option_pricing_inputs``
(imported into this module's own namespace) to isolate this module's own
gate/mapping/call-count logic from the resolver's QuantLib-backed internals
-- those are already covered elsewhere (``bli_pricing_engine`` /
``test_bli_ovme_golden_case.py``). Tests that need a genuine resolved dirty
F/K/T/DF (synthetic recovery, solver-failure mapping, provenance
neutrality) call the real resolver and are skipped when QuantLib is not
installed, mirroring the existing golden-case test convention.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import FrozenInstanceError, asdict, replace

import pytest

from shiori_pricing_lab.data.bli_benchmark_quote import (
    BLIBenchmarkQuote,
    BLIBenchmarkQuoteSide,
    BLIBenchmarkSourceType,
)
from shiori_pricing_lab.data.bli_mvp_input_bundle_fixtures import (
    SYNTHETIC_BLI_MVP_INPUT_BUNDLE,
)
from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIForwardCleanPriceInput,
    BLIMarketDataStatus,
    BLIVolatilityBasis,
)
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.pricing import bli_implied_price_vol_calibration as calibration_module
from shiori_pricing_lab.pricing.bli_black76_price_option import (
    black76_dirty_price_option_pv_per_100,
)
from shiori_pricing_lab.pricing.bli_implied_price_vol_calibration import (
    BLIImpliedPriceVolCalibrationReason,
    BLIImpliedPriceVolCalibrationStatus,
    calibrate_bli_implied_price_vol,
)
from shiori_pricing_lab.pricing.bli_implied_price_vol_solver import (
    BLIImpliedPriceVolSolverReason,
    BLIImpliedPriceVolSolverStatus,
)
from shiori_pricing_lab.pricing.bli_pricing_engine import (
    STANDALONE_ENGINE_NAME,
    STANDALONE_ENGINE_VERSION,
)
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available
from shiori_pricing_lab.pricing.bli_standalone_option_pricing_inputs import (
    StandaloneOptionPricingInputs,
    resolve_standalone_option_pricing_inputs,
)
from shiori_pricing_lab.products.enums import (
    Currency,
    ExerciseStyle,
    PayoffBasis,
    PayReceive,
    SettlementType,
    TreasuryFTPQuoteSide,
)

_calibrate = calibrate_bli_implied_price_vol
_MID = BLIBenchmarkQuoteSide.MID
_requires_quantlib = pytest.mark.skipif(
    not is_quantlib_available(), reason="QuantLib is not installed in this environment"
)

# Synthetic timing/date contract values (Issue #94 human methodology
# approval, comment 5001749998).
# SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option.expiry_date is
# "2026-09-29"; its valuation_date is "2026-07-01".
_PRICING_TIMESTAMP = "2026-07-01T16:00:00Z"
_EXPIRY_TIMESTAMP = "2026-09-29T16:00:00Z"
_REPORTING_DATE = "2026-07-01"
_FORWARD_SETTLEMENT_DATE = "2026-10-01"
_OPTION_SETTLEMENT_DATE = "2026-10-02"

# --- Shared synthetic fixtures (mirrors tests/test_bli_pricing_engine_standalone_option.py) --


def _short_tenor_curve_points(currency) -> tuple[BLICurvePoint, ...]:
    common = {
        "currency": currency,
        "rate_basis": BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
        "source_system": "TEST_LOCAL_CURVE",
        "status": BLIMarketDataStatus.ACTIVE,
    }
    option_discount_nodes = tuple(
        BLICurvePoint(
            curve_id="TEST_LOCAL_OPTION_DISCOUNT_CURVE",
            curve_name="TEST_LOCAL_OPTION_DISCOUNT_CURVE",
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            tenor=tenor,
            rate=rate,
            **common,
        )
        for tenor, rate in (("1M", 0.028), ("1Y", 0.032))
    )
    deposit_nodes = tuple(
        point
        for point in SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.curve_points
        if point.curve_purpose is BLICurvePurpose.DEPOSIT_CURVE
    )
    return option_discount_nodes + deposit_nodes


def _supported_snapshot(**overrides):
    params = dict(
        snapshot_id="TEST_LOCAL_SUPPORTED_SNAPSHOT",
        source_system="TEST_LOCAL_CURVE",
        curve_points=_short_tenor_curve_points(
            SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option.currency
        ),
        # Issue #94: standalone requests require an explicit forward clean
        # price input whose quote side matches the MID spot bond quote.
        forward_clean_price_input=BLIForwardCleanPriceInput(
            forward_clean_price_per_100=101.30,
            quote_side=TreasuryFTPQuoteSide.MID,
            source_system="TEST_LOCAL_FORWARD_FEED",
            status=BLIMarketDataStatus.ACTIVE,
        ),
    )
    params.update(overrides)
    return replace(SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT, **params)


def _supported_request(**overrides) -> BLIStandaloneBondOptionRequest:
    params = dict(
        bond_option=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        resolved_bond_reference_data=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.resolved_bond_reference_data,
        valuation_date=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.valuation_date,
        market_data_snapshot=_supported_snapshot(),
        pricing_timestamp=_PRICING_TIMESTAMP,
        expiry_timestamp=_EXPIRY_TIMESTAMP,
        reporting_date=_REPORTING_DATE,
        forward_settlement_date=_FORWARD_SETTLEMENT_DATE,
        option_settlement_date=_OPTION_SETTLEMENT_DATE,
    )
    params.update(overrides)
    return BLIStandaloneBondOptionRequest(**params)


_REQUEST = _supported_request()


def _make_benchmark(**overrides) -> BLIBenchmarkQuote:
    bond_option = _REQUEST.bond_option
    params = dict(
        benchmark_id="BM-0001",
        source_type=BLIBenchmarkSourceType.BLOOMBERG,
        source_system="BLOOMBERG",
        source_as_of=_REQUEST.market_data_snapshot.as_of_timestamp,
        retrieved_at="2026-07-14T09:00:00Z",
        quote_side=BLIBenchmarkQuoteSide.MID,
        premium_per_100=4.5,
        total_premium=2250.0,
        currency=bond_option.currency,
        product_id=bond_option.product_id,
        snapshot_id=_REQUEST.market_data_snapshot.snapshot_id,
        underlying_id=bond_option.underlying_isin,
        source_reference="SANITIZED-BENCHMARK-REFERENCE-001",
    )
    params.update(overrides)
    return BLIBenchmarkQuote(**params)


def _assert_gate_blocked(result, reason: BLIImpliedPriceVolCalibrationReason) -> None:
    assert result.status is BLIImpliedPriceVolCalibrationStatus.FAILED
    assert result.reason is reason
    # No resolution and no solver call happened: every resolved-input field
    # and the solver result stay None.
    assert result.solver_result is None
    assert result.forward_clean_price_per_100 is None
    assert result.strike_clean_price_per_100 is None
    assert result.accrued_interest_at_forward_settlement_per_100 is None
    assert result.forward_dirty_price_per_100 is None
    assert result.strike_dirty_price_per_100 is None
    assert result.time_to_expiry_year_fraction is None
    assert result.pricing_to_reporting_discount_factor is None
    assert result.pricing_to_option_settlement_discount_factor is None
    assert result.effective_reporting_date_discount_factor is None
    assert result.resolution_error_type is None
    assert result.resolution_error_message is None
    assert result.pricing_engine_name == STANDALONE_ENGINE_NAME
    assert result.pricing_engine_version == STANDALONE_ENGINE_VERSION


# --- 1. Identity/date coherence gates (unchanged priority and reasons) -------------


def test_product_id_mismatch():
    benchmark = _make_benchmark(product_id="OTHER-PRODUCT-ID")
    result = _calibrate(_REQUEST, benchmark, active_quote_side=_MID)
    _assert_gate_blocked(result, BLIImpliedPriceVolCalibrationReason.PRODUCT_ID_MISMATCH)


def test_snapshot_id_mismatch():
    benchmark = _make_benchmark(snapshot_id="OTHER-SNAPSHOT-ID")
    result = _calibrate(_REQUEST, benchmark, active_quote_side=_MID)
    assert result.reason is BLIImpliedPriceVolCalibrationReason.SNAPSHOT_ID_MISMATCH


def test_underlying_id_mismatch():
    benchmark = _make_benchmark(underlying_id="XS9999999999")
    result = _calibrate(_REQUEST, benchmark, active_quote_side=_MID)
    assert result.reason is BLIImpliedPriceVolCalibrationReason.UNDERLYING_ID_MISMATCH


def test_currency_mismatch():
    benchmark = _make_benchmark(currency=Currency.EUR)
    result = _calibrate(_REQUEST, benchmark, active_quote_side=_MID)
    assert result.reason is BLIImpliedPriceVolCalibrationReason.CURRENCY_MISMATCH


def test_quote_side_mismatch():
    benchmark = _make_benchmark(quote_side=BLIBenchmarkQuoteSide.BID)
    result = _calibrate(_REQUEST, benchmark, active_quote_side=BLIBenchmarkQuoteSide.OFFER)
    assert result.reason is BLIImpliedPriceVolCalibrationReason.QUOTE_SIDE_MISMATCH


def test_source_date_mismatch():
    benchmark = _make_benchmark(source_as_of="2026-08-01T09:00:00Z")
    result = _calibrate(_REQUEST, benchmark, active_quote_side=_MID)
    assert result.reason is BLIImpliedPriceVolCalibrationReason.SOURCE_DATE_MISMATCH


def test_mismatch_priority_product_id_beats_snapshot_and_underlying():
    benchmark = _make_benchmark(
        product_id="OTHER-PRODUCT-ID", snapshot_id="OTHER-SNAPSHOT-ID", underlying_id="XS9999999999"
    )
    result = _calibrate(_REQUEST, benchmark, active_quote_side=_MID)
    assert result.reason is BLIImpliedPriceVolCalibrationReason.PRODUCT_ID_MISMATCH


def test_mismatch_priority_currency_beats_quote_side_and_date():
    benchmark = _make_benchmark(
        currency=Currency.EUR,
        quote_side=BLIBenchmarkQuoteSide.BID,
        source_as_of="2026-08-01T09:00:00Z",
    )
    result = _calibrate(_REQUEST, benchmark, active_quote_side=BLIBenchmarkQuoteSide.OFFER)
    assert result.reason is BLIImpliedPriceVolCalibrationReason.CURRENCY_MISMATCH


def test_mismatch_priority_source_date_beats_solver_config_and_resolution():
    # SOURCE_DATE_MISMATCH (gate 7) must still short-circuit before solver-
    # configuration validation / resolution (gate 8) -- a malformed config
    # paired with a date mismatch yields the mismatch reason, not ValueError.
    benchmark = _make_benchmark(source_as_of="2026-08-01T09:00:00Z")
    result = _calibrate(_REQUEST, benchmark, active_quote_side=_MID, lower_price_vol=-1.0)
    assert result.reason is BLIImpliedPriceVolCalibrationReason.SOURCE_DATE_MISMATCH


# --- 2. Unsupported request stops at REQUEST_NOT_SUPPORTED (gate 1) ---------------


def test_mismatch_priority_request_not_supported_beats_everything():
    american_option = replace(
        _REQUEST.bond_option,
        exercise_style=ExerciseStyle.AMERICAN,
        exercise_start_date="2026-06-01",
    )
    request = replace(_REQUEST, bond_option=american_option)
    benchmark = _make_benchmark(product_id="OTHER-PRODUCT-ID")
    result = _calibrate(request, benchmark, active_quote_side=_MID)
    assert result.reason is BLIImpliedPriceVolCalibrationReason.REQUEST_NOT_SUPPORTED


def test_unsupported_american_exercise_stops_at_request_not_supported():
    american_option = replace(
        _REQUEST.bond_option,
        exercise_style=ExerciseStyle.AMERICAN,
        exercise_start_date="2026-06-01",
    )
    request = replace(_REQUEST, bond_option=american_option)
    result = _calibrate(request, _make_benchmark(), active_quote_side=_MID)
    _assert_gate_blocked(result, BLIImpliedPriceVolCalibrationReason.REQUEST_NOT_SUPPORTED)
    assert any("exercise_style" in reason for reason in result.request_support_reasons)


def test_unsupported_yield_payoff_stops_at_request_not_supported():
    yield_option = replace(
        _REQUEST.bond_option, payoff_basis=PayoffBasis.YIELD, strike_price=None, strike_yield=0.035
    )
    request = replace(_REQUEST, bond_option=yield_option)
    result = _calibrate(request, _make_benchmark(), active_quote_side=_MID)
    assert result.reason is BLIImpliedPriceVolCalibrationReason.REQUEST_NOT_SUPPORTED
    assert any("payoff_basis" in reason for reason in result.request_support_reasons)


def test_unsupported_physical_settlement_stops_at_request_not_supported():
    physical_option = replace(_REQUEST.bond_option, settlement_type=SettlementType.PHYSICAL)
    request = replace(_REQUEST, bond_option=physical_option)
    result = _calibrate(request, _make_benchmark(), active_quote_side=_MID)
    assert result.reason is BLIImpliedPriceVolCalibrationReason.REQUEST_NOT_SUPPORTED
    assert any("settlement_type" in reason for reason in result.request_support_reasons)


def test_unsupported_yield_vol_stops_at_request_not_supported():
    yield_vol_snapshot = replace(
        _REQUEST.market_data_snapshot,
        volatility_input=replace(
            _REQUEST.market_data_snapshot.volatility_input,
            volatility_basis=BLIVolatilityBasis.YIELD_VOL,
        ),
    )
    request = replace(_REQUEST, market_data_snapshot=yield_vol_snapshot)
    result = _calibrate(request, _make_benchmark(), active_quote_side=_MID)
    assert result.reason is BLIImpliedPriceVolCalibrationReason.REQUEST_NOT_SUPPORTED
    assert any("volatility_basis" in reason for reason in result.request_support_reasons)


def test_unsupported_request_wins_before_solver_config_validation():
    american_option = replace(
        _REQUEST.bond_option,
        exercise_style=ExerciseStyle.AMERICAN,
        exercise_start_date="2026-06-01",
    )
    request = replace(_REQUEST, bond_option=american_option)
    result = _calibrate(request, _make_benchmark(), active_quote_side=_MID, max_iterations=0)
    assert result.reason is BLIImpliedPriceVolCalibrationReason.REQUEST_NOT_SUPPORTED


# --- 3. Quote-side boundary ---------------------------------------------------------


@pytest.mark.parametrize("side", [BLIBenchmarkQuoteSide.BID, "BID"])
def test_native_and_raw_quote_side_accepted(side):
    # Deliberately mismatch source_as_of so the run stops at the *next* gate
    # (SOURCE_DATE_MISMATCH) rather than QUOTE_SIDE_MISMATCH -- proving the
    # quote-side gate was passed.
    benchmark = _make_benchmark(
        quote_side=BLIBenchmarkQuoteSide.BID, source_as_of="2026-08-01T09:00:00Z"
    )
    result = _calibrate(_REQUEST, benchmark, active_quote_side=side)
    assert result.reason is BLIImpliedPriceVolCalibrationReason.SOURCE_DATE_MISMATCH
    assert result.active_quote_side is BLIBenchmarkQuoteSide.BID


@pytest.mark.parametrize("foreign_side", list(TreasuryFTPQuoteSide))
def test_foreign_treasury_ftp_quote_side_rejected(foreign_side):
    with pytest.raises(ValueError, match="active_quote_side"):
        _calibrate(_REQUEST, _make_benchmark(), active_quote_side=foreign_side)


def test_unrelated_foreign_enum_quote_side_rejected():
    with pytest.raises(ValueError, match="active_quote_side"):
        _calibrate(_REQUEST, _make_benchmark(), active_quote_side=PayReceive.PAY)


# --- 4. Solver-configuration validation (still runs before resolution/solve) -------


@pytest.mark.parametrize(
    "config",
    [
        {"lower_price_vol": -1.0},
        {"lower_price_vol": 0.0},
        {"lower_price_vol": float("nan")},
        {"lower_price_vol": float("inf")},
        {"upper_price_vol": float("nan")},
        {"upper_price_vol": float("-inf")},
        {"lower_price_vol": 0.5, "upper_price_vol": 0.5},  # upper <= lower
        {"lower_price_vol": 0.5, "upper_price_vol": 0.4},
        {"premium_tolerance_per_100": 0.0},
        {"premium_tolerance_per_100": -1e-8},
        {"premium_tolerance_per_100": float("nan")},
        {"price_vol_tolerance": 0.0},
        {"price_vol_tolerance": -1e-8},
        {"price_vol_tolerance": float("inf")},
        {"max_iterations": 0},
        {"max_iterations": -5},
        {"max_iterations": True},  # bool rejected as a non-int
        {"max_iterations": 1.5},  # non-int rejected
    ],
)
def test_coherent_request_with_malformed_solver_config_raises_value_error(config):
    with pytest.raises(ValueError):
        _calibrate(_REQUEST, _make_benchmark(), active_quote_side=_MID, **config)


def test_coherence_mismatch_wins_before_solver_config_validation():
    benchmark = _make_benchmark(product_id="OTHER-PRODUCT-ID")
    result = _calibrate(_REQUEST, benchmark, active_quote_side=_MID, lower_price_vol=-1.0)
    assert result.reason is BLIImpliedPriceVolCalibrationReason.PRODUCT_ID_MISMATCH


# --- 5. Resolver error mapping (monkeypatched: independent of QuantLib) ------------


def test_resolver_value_error_maps_to_input_resolution_failed(monkeypatch):
    def _raise_value_error(request):
        raise ValueError("curve target year fraction is out of the curve's node range")

    monkeypatch.setattr(
        calibration_module, "resolve_standalone_option_pricing_inputs", _raise_value_error
    )
    result = _calibrate(_REQUEST, _make_benchmark(), active_quote_side=_MID)
    assert result.status is BLIImpliedPriceVolCalibrationStatus.FAILED
    assert result.reason is BLIImpliedPriceVolCalibrationReason.INPUT_RESOLUTION_FAILED
    assert result.resolution_error_type == "ValueError"
    assert "out of the curve's node range" in result.resolution_error_message
    assert result.solver_result is None
    assert result.forward_dirty_price_per_100 is None


def test_resolver_runtime_error_propagates_unchanged(monkeypatch):
    def _raise_runtime_error(request):
        raise RuntimeError("QuantLib is not installed in this environment")

    monkeypatch.setattr(
        calibration_module, "resolve_standalone_option_pricing_inputs", _raise_runtime_error
    )
    with pytest.raises(RuntimeError, match="QuantLib"):
        _calibrate(_REQUEST, _make_benchmark(), active_quote_side=_MID)


def test_resolver_and_solver_are_each_called_exactly_once(monkeypatch):
    fake_inputs = StandaloneOptionPricingInputs(
        forward_clean_price_per_100=101.30,
        strike_clean_price_per_100=99.50,
        accrued_interest_at_forward_settlement_per_100=0.95,
        forward_dirty_price_per_100=102.25,
        strike_dirty_price_per_100=100.45,
        time_to_expiry_year_fraction=0.25,
        pricing_to_reporting_discount_factor=1.0,
        pricing_to_option_settlement_discount_factor=0.99,
        effective_reporting_date_discount_factor=0.99,
    )
    call_counts = {"resolve": 0, "solve": 0}
    real_solve = calibration_module.solve_implied_dirty_price_vol

    def _counting_resolve(request):
        call_counts["resolve"] += 1
        return fake_inputs

    def _counting_solve(**kwargs):
        call_counts["solve"] += 1
        return real_solve(**kwargs)

    monkeypatch.setattr(
        calibration_module, "resolve_standalone_option_pricing_inputs", _counting_resolve
    )
    monkeypatch.setattr(calibration_module, "solve_implied_dirty_price_vol", _counting_solve)

    result = _calibrate(_REQUEST, _make_benchmark(premium_per_100=2.0), active_quote_side=_MID)

    assert call_counts["resolve"] == 1
    assert call_counts["solve"] == 1
    assert result.forward_dirty_price_per_100 == fake_inputs.forward_dirty_price_per_100
    assert result.strike_dirty_price_per_100 == fake_inputs.strike_dirty_price_per_100
    assert result.time_to_expiry_year_fraction == fake_inputs.time_to_expiry_year_fraction
    assert (
        result.effective_reporting_date_discount_factor
        == fake_inputs.effective_reporting_date_discount_factor
    )


def test_successful_solve_maps_to_success_calibrated(monkeypatch):
    fake_inputs = StandaloneOptionPricingInputs(
        forward_clean_price_per_100=101.30,
        strike_clean_price_per_100=99.50,
        accrued_interest_at_forward_settlement_per_100=0.95,
        forward_dirty_price_per_100=102.25,
        strike_dirty_price_per_100=100.45,
        time_to_expiry_year_fraction=0.25,
        pricing_to_reporting_discount_factor=1.0,
        pricing_to_option_settlement_discount_factor=0.99,
        effective_reporting_date_discount_factor=0.99,
    )
    monkeypatch.setattr(
        calibration_module,
        "resolve_standalone_option_pricing_inputs",
        lambda request: fake_inputs,
    )
    sigma_true = 0.06
    target = black76_dirty_price_option_pv_per_100(
        forward_dirty_price=fake_inputs.forward_dirty_price_per_100,
        strike_dirty_price=fake_inputs.strike_dirty_price_per_100,
        price_volatility=sigma_true,
        time_to_expiry=fake_inputs.time_to_expiry_year_fraction,
        discount_factor=fake_inputs.effective_reporting_date_discount_factor,
        option_type=_REQUEST.bond_option.option_type,
    )
    result = _calibrate(_REQUEST, _make_benchmark(premium_per_100=target), active_quote_side=_MID)
    assert result.status is BLIImpliedPriceVolCalibrationStatus.SUCCESS
    assert result.reason is BLIImpliedPriceVolCalibrationReason.CALIBRATED
    assert result.solver_result.status is BLIImpliedPriceVolSolverStatus.SUCCESS
    assert result.solver_result.implied_price_vol == pytest.approx(sigma_true, abs=1e-6)


def test_economic_solver_failure_maps_to_solver_failed(monkeypatch):
    fake_inputs = StandaloneOptionPricingInputs(
        forward_clean_price_per_100=101.30,
        strike_clean_price_per_100=99.50,
        accrued_interest_at_forward_settlement_per_100=0.95,
        forward_dirty_price_per_100=102.25,
        strike_dirty_price_per_100=100.45,
        time_to_expiry_year_fraction=0.25,
        pricing_to_reporting_discount_factor=1.0,
        pricing_to_option_settlement_discount_factor=0.99,
        effective_reporting_date_discount_factor=0.99,
    )
    monkeypatch.setattr(
        calibration_module,
        "resolve_standalone_option_pricing_inputs",
        lambda request: fake_inputs,
    )
    # forward (102.25) > strike (100.45): a CALL's theoretical arbitrage
    # lower bound is strictly positive, so a target of 0.0 is below it.
    result = _calibrate(_REQUEST, _make_benchmark(premium_per_100=0.0), active_quote_side=_MID)
    assert result.status is BLIImpliedPriceVolCalibrationStatus.FAILED
    assert result.reason is BLIImpliedPriceVolCalibrationReason.SOLVER_FAILED
    assert result.solver_result is not None
    assert result.solver_result.status is BLIImpliedPriceVolSolverStatus.FAILED
    assert result.solver_result.reason is BLIImpliedPriceVolSolverReason.BELOW_ARBITRAGE_LOWER_BOUND
    # Resolution succeeded (this is a solver failure, not a resolution failure).
    assert result.forward_dirty_price_per_100 == fake_inputs.forward_dirty_price_per_100
    assert result.resolution_error_type is None


# --- 6. Genuine end-to-end recovery (real resolver; requires QuantLib) -------------


@_requires_quantlib
def test_coherent_request_calibrates_and_recovers_synthetic_vol():
    inputs = resolve_standalone_option_pricing_inputs(_REQUEST)
    sigma_true = 0.05
    target = black76_dirty_price_option_pv_per_100(
        forward_dirty_price=inputs.forward_dirty_price_per_100,
        strike_dirty_price=inputs.strike_dirty_price_per_100,
        price_volatility=sigma_true,
        time_to_expiry=inputs.time_to_expiry_year_fraction,
        discount_factor=inputs.effective_reporting_date_discount_factor,
        option_type=_REQUEST.bond_option.option_type,
    )
    result = _calibrate(_REQUEST, _make_benchmark(premium_per_100=target), active_quote_side=_MID)
    assert result.status is BLIImpliedPriceVolCalibrationStatus.SUCCESS
    assert result.reason is BLIImpliedPriceVolCalibrationReason.CALIBRATED
    assert result.solver_result.implied_price_vol == pytest.approx(sigma_true, abs=1e-6)
    assert result.forward_dirty_price_per_100 == pytest.approx(inputs.forward_dirty_price_per_100)
    assert result.strike_dirty_price_per_100 == pytest.approx(inputs.strike_dirty_price_per_100)
    assert result.time_to_expiry_year_fraction == pytest.approx(
        inputs.time_to_expiry_year_fraction
    )
    assert result.effective_reporting_date_discount_factor == pytest.approx(
        inputs.effective_reporting_date_discount_factor
    )
    assert result.forward_clean_price_per_100 == pytest.approx(
        inputs.forward_clean_price_per_100
    )


@_requires_quantlib
def test_provenance_fields_do_not_affect_implied_vol():
    inputs = resolve_standalone_option_pricing_inputs(_REQUEST)
    sigma_true = 0.07
    target = black76_dirty_price_option_pv_per_100(
        forward_dirty_price=inputs.forward_dirty_price_per_100,
        strike_dirty_price=inputs.strike_dirty_price_per_100,
        price_volatility=sigma_true,
        time_to_expiry=inputs.time_to_expiry_year_fraction,
        discount_factor=inputs.effective_reporting_date_discount_factor,
        option_type=_REQUEST.bond_option.option_type,
    )
    baseline = _calibrate(_REQUEST, _make_benchmark(premium_per_100=target), active_quote_side=_MID)
    assert baseline.status is BLIImpliedPriceVolCalibrationStatus.SUCCESS
    baseline_vol = baseline.solver_result.implied_price_vol

    for volatility in (0.01, 0.99):
        request = replace(
            _REQUEST,
            market_data_snapshot=replace(
                _REQUEST.market_data_snapshot,
                volatility_input=replace(
                    _REQUEST.market_data_snapshot.volatility_input, volatility=volatility
                ),
            ),
        )
        result = _calibrate(
            request, _make_benchmark(premium_per_100=target), active_quote_side=_MID
        )
        assert result.solver_result.implied_price_vol == pytest.approx(baseline_vol, abs=1e-9)

    for notional in (10.0, 10_000_000.0):
        request = replace(_REQUEST, bond_option=replace(_REQUEST.bond_option, notional=notional))
        result = _calibrate(
            request, _make_benchmark(premium_per_100=target), active_quote_side=_MID
        )
        assert result.solver_result.implied_price_vol == pytest.approx(baseline_vol, abs=1e-9)

    for total_premium in (1.0, 999_999.0):
        result = _calibrate(
            _REQUEST,
            _make_benchmark(premium_per_100=target, total_premium=total_premium),
            active_quote_side=_MID,
        )
        assert result.solver_result.implied_price_vol == pytest.approx(baseline_vol, abs=1e-9)


@_requires_quantlib
def test_retrieved_at_difference_does_not_affect_calibration():
    inputs = resolve_standalone_option_pricing_inputs(_REQUEST)
    target = black76_dirty_price_option_pv_per_100(
        forward_dirty_price=inputs.forward_dirty_price_per_100,
        strike_dirty_price=inputs.strike_dirty_price_per_100,
        price_volatility=0.04,
        time_to_expiry=inputs.time_to_expiry_year_fraction,
        discount_factor=inputs.effective_reporting_date_discount_factor,
        option_type=_REQUEST.bond_option.option_type,
    )
    benchmark_a = _make_benchmark(premium_per_100=target, retrieved_at="2020-01-01T00:00:00Z")
    benchmark_b = _make_benchmark(premium_per_100=target, retrieved_at="2030-06-15T23:59:00Z")
    result_a = _calibrate(_REQUEST, benchmark_a, active_quote_side=_MID)
    result_b = _calibrate(_REQUEST, benchmark_b, active_quote_side=_MID)
    assert result_a.status is BLIImpliedPriceVolCalibrationStatus.SUCCESS
    assert result_b.status is BLIImpliedPriceVolCalibrationStatus.SUCCESS
    assert result_a.solver_result.implied_price_vol == pytest.approx(
        result_b.solver_result.implied_price_vol, abs=1e-12
    )
    assert result_a.benchmark_retrieved_at != result_b.benchmark_retrieved_at


# --- 7. No mutation of request / benchmark / snapshot ------------------------------


def test_request_benchmark_and_snapshot_are_not_mutated():
    request = _REQUEST
    benchmark = _make_benchmark()
    request_before = asdict(request)
    benchmark_before = asdict(benchmark)
    snapshot_before = asdict(request.market_data_snapshot)

    _calibrate(request, benchmark, active_quote_side=_MID)

    assert asdict(request) == request_before
    assert asdict(benchmark) == benchmark_before
    assert asdict(request.market_data_snapshot) == snapshot_before


# --- 8. Frozen result / deterministic asdict ---------------------------------------


def test_result_is_frozen():
    result = _calibrate(
        _REQUEST, _make_benchmark(product_id="OTHER-PRODUCT-ID"), active_quote_side=_MID
    )
    with pytest.raises(FrozenInstanceError):
        result.status = BLIImpliedPriceVolCalibrationStatus.SUCCESS  # type: ignore[misc]


def test_asdict_is_deterministic():
    benchmark = _make_benchmark(product_id="OTHER-PRODUCT-ID")
    first = _calibrate(_REQUEST, benchmark, active_quote_side=_MID)
    second = _calibrate(_REQUEST, benchmark, active_quote_side=_MID)
    assert asdict(first) == asdict(second)


# --- 9. Public signature has no forbidden parameters -------------------------------


def test_signature_has_no_pricing_result_comparison_or_forbidden_parameters():
    signature = inspect.signature(calibrate_bli_implied_price_vol)
    forbidden_names = {
        "pricing_result",
        "comparison_result",
        "provider",
        "initial_vol",
        "initial_price_vol",
        "guess",
        "notional_target",
        "target_total_premium",
        "target_notional_premium",
    }
    assert forbidden_names.isdisjoint(signature.parameters.keys())


# --- 10. Module-boundary proof ------------------------------------------------------


def test_module_imports_only_the_expected_dependencies():
    tree = ast.parse(inspect.getsource(calibration_module))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
    # Issue #P1-06: the resolver replaces the legacy F/T/DF helpers
    # (bli_forward_clean_price, bli_valuation_time, bli_curve_discount_factor)
    # -- none of those three modules is imported here.
    assert imported_names == {
        "__future__",
        "dataclasses",
        "enum",
        "math",
        "shiori_pricing_lab.data._validation",
        "shiori_pricing_lab.data.bli_benchmark_quote",
        "shiori_pricing_lab.data.bli_snapshot",
        "shiori_pricing_lab.data.bli_standalone_option_request",
        "shiori_pricing_lab.pricing.bli_implied_price_vol_solver",
        "shiori_pricing_lab.pricing.bli_mvp_required_input_guard",
        "shiori_pricing_lab.pricing.bli_pricing_engine",
        "shiori_pricing_lab.pricing.bli_standalone_option_pricing_inputs",
        "shiori_pricing_lab.products.enums",
    }


def test_module_defines_no_pricing_result_comparison_provider_or_ui_names():
    module_names = set(dir(calibration_module))
    forbidden_names = {
        "PricingResult",
        "compare_bli_benchmark",
        "BLIBenchmarkComparisonResult",
        "price_bli_mvp",
        "price_bli_mvp_standalone_option",
        "requests",
        "socket",
        "streamlit",
        "ExerciseStyle",
        "SettlementType",
        "PayoffBasis",
        # Legacy clean-price/ACT-365F F/T/DF helpers the OVME-aligned
        # resolver replaces -- must not be imported directly here.
        "forward_clean_price_per_100",
        "year_fraction_to_expiry",
        "discount_factor_from_continuous_zero_curve",
    }
    assert module_names.isdisjoint(forbidden_names)


def test_module_source_does_not_use_system_clock():
    source = inspect.getsource(calibration_module)
    assert "date.today(" not in source
    assert "datetime.now(" not in source
    assert "import datetime" not in source


def test_module_defines_no_yield_vol_surface_sabr_or_scipy_names():
    module_names = set(dir(calibration_module))
    forbidden_names = {
        "YIELD_VOL",
        "SABR",
        "scipy",
        "numpy",
        "VolatilitySurface",
        "yield_vol_to_price_vol",
    }
    assert module_names.isdisjoint(forbidden_names)


def test_module_calls_solve_implied_dirty_price_vol_not_the_clean_wrapper():
    # Issue #P1-06: calibration must solve on the dirty basis -- the clean
    # solve_implied_price_vol is never called by this module.
    source = inspect.getsource(calibration_module)
    assert "solve_implied_dirty_price_vol(" in source
    assert "solve_implied_price_vol(" not in source
