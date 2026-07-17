"""Tests for ``calibrate_bli_implied_price_vol`` (Issue #99 PR B wiring).

All fixtures are explicitly synthetic. None represents Bloomberg output,
market calibration evidence, a golden case, or UAT.

Two tests (``test_known_synthetic_call_recovery`` /
``test_known_synthetic_put_recovery``) exercise the real, unmocked
production F/T/DF composition (mirroring the reviewed pinned fixture in
``tests/test_bli_pricing_engine_standalone_option.py``) and require
QuantLib. Every other test mocks the three resolution helpers and/or the
solver (via ``unittest.mock.patch`` on the names as imported into the
calibration module) so the wiring logic -- gate order, call counts, error
mapping, provenance neutrality -- is proven independently of whether
QuantLib is installed, matching this repo's existing test-portability
convention.
"""

from __future__ import annotations

import ast
import inspect
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, asdict, replace
from unittest import mock

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
    BLIMarketDataStatus,
    BLIVolatilityBasis,
)
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.pricing import bli_implied_price_vol_calibration as calibration_module
from shiori_pricing_lab.pricing.bli_black76_price_option import (
    black76_price_option_pv_per_100,
)
from shiori_pricing_lab.pricing.bli_implied_price_vol_calibration import (
    BLIImpliedPriceVolCalibrationReason,
    BLIImpliedPriceVolCalibrationStatus,
    calibrate_bli_implied_price_vol,
)
from shiori_pricing_lab.pricing.bli_implied_price_vol_solver import (
    BLIImpliedPriceVolSolverReason,
    BLIImpliedPriceVolSolverResult,
    BLIImpliedPriceVolSolverStatus,
)
from shiori_pricing_lab.pricing.bli_pricing_engine import ENGINE_NAME, ENGINE_VERSION
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available
from shiori_pricing_lab.products.enums import (
    Currency,
    ExerciseStyle,
    OptionType,
    PayoffBasis,
    PayReceive,
    SettlementType,
    TreasuryFTPQuoteSide,
)

_MODULE = "shiori_pricing_lab.pricing.bli_implied_price_vol_calibration"
_requires_quantlib = pytest.mark.skipif(
    not is_quantlib_available(), reason="QuantLib is not installed in this environment"
)
_calibrate = calibrate_bli_implied_price_vol
_MID = BLIBenchmarkQuoteSide.MID

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
    bond_reference_nodes = tuple(
        BLICurvePoint(
            curve_id="TEST_LOCAL_BOND_REFERENCE_CURVE",
            curve_name="TEST_LOCAL_BOND_REFERENCE_CURVE",
            curve_purpose=BLICurvePurpose.BOND_REFERENCE_CURVE,
            tenor=tenor,
            rate=rate,
            **common,
        )
        for tenor, rate in (("1M", 0.030), ("1Y", 0.035))
    )
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
    return bond_reference_nodes + option_discount_nodes + deposit_nodes


def _supported_snapshot(**overrides):
    params = dict(
        snapshot_id="TEST_LOCAL_SUPPORTED_SNAPSHOT",
        source_system="TEST_LOCAL_CURVE",
        curve_points=_short_tenor_curve_points(
            SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option.currency
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


def _fake_solver_result(**overrides) -> BLIImpliedPriceVolSolverResult:
    params = dict(
        status=BLIImpliedPriceVolSolverStatus.SUCCESS,
        reason=BLIImpliedPriceVolSolverReason.CONVERGED,
        option_type=OptionType.CALL,
        forward_clean_price=101.0,
        strike_clean_price=99.5,
        time_to_expiry=0.5,
        discount_factor=0.98,
        target_premium_per_100=4.5,
        lower_price_vol=0.000001,
        upper_price_vol=5.0,
        premium_tolerance_per_100=1e-8,
        price_vol_tolerance=1e-8,
        max_iterations=100,
        arbitrage_lower_bound_per_100=0.98 * (101.0 - 99.5),
        arbitrage_upper_bound_per_100=0.98 * 101.0,
        lower_bound_model_premium_per_100=0.001,
        upper_bound_model_premium_per_100=90.0,
        implied_price_vol=0.15,
        model_premium_per_100=4.5,
        premium_residual_per_100=0.0,
        iterations=20,
        final_bracket_lower_price_vol=0.15,
        final_bracket_upper_price_vol=0.15,
        diagnostic_note="synthetic fake solver result for calibration wiring tests",
    )
    params.update(overrides)
    return BLIImpliedPriceVolSolverResult(**params)


# --- 1-3. Known synthetic recovery + repricing match (real composition) --------


@_requires_quantlib
def test_known_synthetic_call_recovery():
    request = _REQUEST
    bond_option = request.bond_option
    F = 101.22605288103159
    T = 0.2465753424657534
    DF = 0.9929452501091504
    sigma_true = 0.18
    target = black76_price_option_pv_per_100(
        forward_clean_price=F,
        strike_clean_price=bond_option.strike_price,
        price_volatility=sigma_true,
        time_to_expiry=T,
        discount_factor=DF,
        option_type=OptionType.CALL,
    )
    total_premium = target * bond_option.notional / 100.0
    benchmark = _make_benchmark(premium_per_100=target, total_premium=total_premium)

    result = _calibrate(request, benchmark, active_quote_side=_MID)

    assert result.status is BLIImpliedPriceVolCalibrationStatus.SUCCESS
    assert result.reason is BLIImpliedPriceVolCalibrationReason.CALIBRATED
    assert result.solver_result.status is BLIImpliedPriceVolSolverStatus.SUCCESS
    assert result.solver_result.implied_price_vol == pytest.approx(sigma_true, abs=1e-6)
    residual = abs(result.solver_result.model_premium_per_100 - target)
    assert residual <= result.solver_result.premium_tolerance_per_100
    assert result.forward_clean_price == F
    assert result.time_to_expiry == T
    assert result.option_discount_factor == DF
    assert result.pricing_engine_name == ENGINE_NAME
    assert result.pricing_engine_version == ENGINE_VERSION


@_requires_quantlib
def test_known_synthetic_put_recovery():
    put_option = replace(_REQUEST.bond_option, option_type=OptionType.PUT)
    request = replace(_REQUEST, bond_option=put_option)
    F = 101.22605288103159
    T = 0.2465753424657534
    DF = 0.9929452501091504
    sigma_true = 0.18
    target = black76_price_option_pv_per_100(
        forward_clean_price=F,
        strike_clean_price=put_option.strike_price,
        price_volatility=sigma_true,
        time_to_expiry=T,
        discount_factor=DF,
        option_type=OptionType.PUT,
    )
    total_premium = target * put_option.notional / 100.0
    benchmark = _make_benchmark(premium_per_100=target, total_premium=total_premium)

    result = _calibrate(request, benchmark, active_quote_side=_MID)

    assert result.status is BLIImpliedPriceVolCalibrationStatus.SUCCESS
    assert result.reason is BLIImpliedPriceVolCalibrationReason.CALIBRATED
    assert result.solver_result.implied_price_vol == pytest.approx(sigma_true, abs=1e-6)


# --- 4-6. Provenance neutrality (mocked F/T/DF, real solver) -------------------
#
# F/T/DF resolution is patched to return the exact same constants the target
# premium below was computed from, so the real solve_implied_price_vol still
# runs against genuine, consistent inputs -- these tests are portable
# without QuantLib, unlike the two real-composition recovery tests above.

_NEUTRALITY_F = 101.0
_NEUTRALITY_T = 0.5
_NEUTRALITY_DF = 0.98


@contextmanager
def _patched_resolution():
    with mock.patch(
        f"{_MODULE}.forward_clean_price_per_100", return_value=_NEUTRALITY_F
    ), mock.patch(
        f"{_MODULE}.year_fraction_to_expiry", return_value=_NEUTRALITY_T
    ), mock.patch(
        f"{_MODULE}.discount_factor_from_continuous_zero_curve", return_value=_NEUTRALITY_DF
    ):
        yield


def test_original_request_volatility_does_not_change_implied_vol():
    target = black76_price_option_pv_per_100(
        forward_clean_price=_NEUTRALITY_F,
        strike_clean_price=_REQUEST.bond_option.strike_price,
        price_volatility=0.18,
        time_to_expiry=_NEUTRALITY_T,
        discount_factor=_NEUTRALITY_DF,
        option_type=OptionType.CALL,
    )
    benchmark = _make_benchmark(premium_per_100=target)

    request_a = replace(
        _REQUEST,
        market_data_snapshot=replace(
            _REQUEST.market_data_snapshot,
            volatility_input=replace(
                _REQUEST.market_data_snapshot.volatility_input, volatility=0.05
            ),
        ),
    )
    request_b = replace(
        _REQUEST,
        market_data_snapshot=replace(
            _REQUEST.market_data_snapshot,
            volatility_input=replace(
                _REQUEST.market_data_snapshot.volatility_input, volatility=0.90
            ),
        ),
    )

    with _patched_resolution():
        result_a = _calibrate(request_a, benchmark, active_quote_side=_MID)
        result_b = _calibrate(request_b, benchmark, active_quote_side=_MID)

    assert result_a.solver_result.implied_price_vol == result_b.solver_result.implied_price_vol
    assert result_a.original_volatility == 0.05
    assert result_b.original_volatility == 0.90


def test_request_notional_does_not_change_implied_vol():
    target = black76_price_option_pv_per_100(
        forward_clean_price=_NEUTRALITY_F,
        strike_clean_price=_REQUEST.bond_option.strike_price,
        price_volatility=0.18,
        time_to_expiry=_NEUTRALITY_T,
        discount_factor=_NEUTRALITY_DF,
        option_type=OptionType.CALL,
    )
    benchmark = _make_benchmark(premium_per_100=target)

    request_a = replace(_REQUEST, bond_option=replace(_REQUEST.bond_option, notional=10.0))
    request_b = replace(_REQUEST, bond_option=replace(_REQUEST.bond_option, notional=10_000_000.0))

    with _patched_resolution():
        result_a = _calibrate(request_a, benchmark, active_quote_side=_MID)
        result_b = _calibrate(request_b, benchmark, active_quote_side=_MID)

    assert result_a.solver_result.implied_price_vol == result_b.solver_result.implied_price_vol
    assert result_a.request_notional == 10.0
    assert result_b.request_notional == 10_000_000.0


def test_benchmark_total_premium_does_not_change_implied_vol():
    target = black76_price_option_pv_per_100(
        forward_clean_price=_NEUTRALITY_F,
        strike_clean_price=_REQUEST.bond_option.strike_price,
        price_volatility=0.18,
        time_to_expiry=_NEUTRALITY_T,
        discount_factor=_NEUTRALITY_DF,
        option_type=OptionType.CALL,
    )
    benchmark_a = _make_benchmark(premium_per_100=target, total_premium=1.0)
    benchmark_b = _make_benchmark(premium_per_100=target, total_premium=999_999_999.0)

    with _patched_resolution():
        result_a = _calibrate(_REQUEST, benchmark_a, active_quote_side=_MID)
        result_b = _calibrate(_REQUEST, benchmark_b, active_quote_side=_MID)

    assert result_a.solver_result.implied_price_vol == result_b.solver_result.implied_price_vol
    assert result_a.benchmark_total_premium == 1.0
    assert result_b.benchmark_total_premium == 999_999_999.0


# --- 7-12. Alignment mismatches (gate short-circuits before resolution) ---------


def test_product_id_mismatch():
    benchmark = _make_benchmark(product_id="OTHER-PRODUCT-ID")
    result = _calibrate(_REQUEST, benchmark, active_quote_side=_MID)
    assert result.status is BLIImpliedPriceVolCalibrationStatus.FAILED
    assert result.reason is BLIImpliedPriceVolCalibrationReason.PRODUCT_ID_MISMATCH
    assert result.forward_clean_price is None
    assert result.solver_result is None


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


# --- 13. Mismatch priority --------------------------------------------------------


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


# --- 14-17. Unsupported request stops before resolution --------------------------


def test_unsupported_american_exercise_stops_before_resolution():
    american_option = replace(
        _REQUEST.bond_option,
        exercise_style=ExerciseStyle.AMERICAN,
        exercise_start_date="2026-06-01",
    )
    request = replace(_REQUEST, bond_option=american_option)
    with mock.patch(f"{_MODULE}.forward_clean_price_per_100") as mock_forward:
        result = _calibrate(
            request, _make_benchmark(), active_quote_side=_MID
        )
    assert result.status is BLIImpliedPriceVolCalibrationStatus.FAILED
    assert result.reason is BLIImpliedPriceVolCalibrationReason.REQUEST_NOT_SUPPORTED
    assert any("exercise_style" in reason for reason in result.request_support_reasons)
    assert result.forward_clean_price is None
    assert result.solver_result is None
    mock_forward.assert_not_called()


def test_unsupported_yield_payoff_stops_before_resolution():
    yield_option = replace(
        _REQUEST.bond_option, payoff_basis=PayoffBasis.YIELD, strike_price=None, strike_yield=0.035
    )
    request = replace(_REQUEST, bond_option=yield_option)
    with mock.patch(f"{_MODULE}.forward_clean_price_per_100") as mock_forward:
        result = _calibrate(
            request, _make_benchmark(), active_quote_side=_MID
        )
    assert result.reason is BLIImpliedPriceVolCalibrationReason.REQUEST_NOT_SUPPORTED
    assert any("payoff_basis" in reason for reason in result.request_support_reasons)
    mock_forward.assert_not_called()


def test_unsupported_physical_settlement_stops_before_resolution():
    physical_option = replace(_REQUEST.bond_option, settlement_type=SettlementType.PHYSICAL)
    request = replace(_REQUEST, bond_option=physical_option)
    with mock.patch(f"{_MODULE}.forward_clean_price_per_100") as mock_forward:
        result = _calibrate(
            request, _make_benchmark(), active_quote_side=_MID
        )
    assert result.reason is BLIImpliedPriceVolCalibrationReason.REQUEST_NOT_SUPPORTED
    assert any("settlement_type" in reason for reason in result.request_support_reasons)
    mock_forward.assert_not_called()


def test_unsupported_yield_vol_stops_before_resolution():
    yield_vol_snapshot = replace(
        _REQUEST.market_data_snapshot,
        volatility_input=replace(
            _REQUEST.market_data_snapshot.volatility_input,
            volatility_basis=BLIVolatilityBasis.YIELD_VOL,
        ),
    )
    request = replace(_REQUEST, market_data_snapshot=yield_vol_snapshot)
    with mock.patch(f"{_MODULE}.forward_clean_price_per_100") as mock_forward:
        result = _calibrate(
            request, _make_benchmark(), active_quote_side=_MID
        )
    assert result.reason is BLIImpliedPriceVolCalibrationReason.REQUEST_NOT_SUPPORTED
    assert any("volatility_basis" in reason for reason in result.request_support_reasons)
    mock_forward.assert_not_called()


# --- 18-19. Quote-side boundary ---------------------------------------------------


@pytest.mark.parametrize("side", [BLIBenchmarkQuoteSide.BID, "BID"])
def test_native_and_raw_quote_side_accepted(side):
    # Deliberately mismatch source_as_of so the run stops at the *next* gate
    # (SOURCE_DATE_MISMATCH) rather than QUOTE_SIDE_MISMATCH -- proving the
    # quote-side gate was passed, without needing QuantLib.
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


# --- 20. retrieved_at neutrality (mocked resolution, real solver) ---------------


def test_retrieved_at_difference_does_not_change_calibration():
    benchmark_a = _make_benchmark(premium_per_100=4.5, retrieved_at="2020-01-01T00:00:00Z")
    benchmark_b = _make_benchmark(premium_per_100=4.5, retrieved_at="2030-06-15T23:59:00Z")

    with mock.patch(f"{_MODULE}.forward_clean_price_per_100", return_value=101.0), mock.patch(
        f"{_MODULE}.year_fraction_to_expiry", return_value=0.5
    ), mock.patch(f"{_MODULE}.discount_factor_from_continuous_zero_curve", return_value=0.98):
        result_a = _calibrate(_REQUEST, benchmark_a, active_quote_side=_MID)
        result_b = _calibrate(_REQUEST, benchmark_b, active_quote_side=_MID)

    assert result_a.status == result_b.status
    assert result_a.reason == result_b.reason
    assert result_a.solver_result.implied_price_vol == result_b.solver_result.implied_price_vol
    assert result_a.benchmark_retrieved_at != result_b.benchmark_retrieved_at


# --- 21. Support guard called exactly once ---------------------------------------


def test_support_guard_called_exactly_once():
    # F/T/DF resolution and the solver are mocked so this test proves only
    # that the real support-guard wrapper is invoked exactly once -- it
    # does not depend on QuantLib or the real pricing composition.
    fake_solver_result = _fake_solver_result()
    with mock.patch(
        f"{_MODULE}.check_bli_mvp_standalone_option_required_inputs",
        wraps=calibration_module.check_bli_mvp_standalone_option_required_inputs,
    ) as mock_guard, mock.patch(
        f"{_MODULE}.forward_clean_price_per_100", return_value=101.0
    ), mock.patch(f"{_MODULE}.year_fraction_to_expiry", return_value=0.5), mock.patch(
        f"{_MODULE}.discount_factor_from_continuous_zero_curve", return_value=0.98
    ), mock.patch(f"{_MODULE}.solve_implied_price_vol", return_value=fake_solver_result):
        _calibrate(_REQUEST, _make_benchmark(), active_quote_side=_MID)
    assert mock_guard.call_count == 1


# --- 22-25. F/T/DF/solver call counts ---------------------------------------------


def test_forward_time_discount_and_solver_each_called_exactly_once():
    fake_solver_result = _fake_solver_result()
    with mock.patch(
        f"{_MODULE}.forward_clean_price_per_100", return_value=101.0
    ) as mock_forward, mock.patch(
        f"{_MODULE}.year_fraction_to_expiry", return_value=0.5
    ) as mock_time, mock.patch(
        f"{_MODULE}.discount_factor_from_continuous_zero_curve", return_value=0.98
    ) as mock_df, mock.patch(
        f"{_MODULE}.solve_implied_price_vol", return_value=fake_solver_result
    ) as mock_solver:
        result = _calibrate(
            _REQUEST, _make_benchmark(), active_quote_side=_MID
        )
    assert mock_forward.call_count == 1
    assert mock_time.call_count == 1
    assert mock_df.call_count == 1
    assert mock_solver.call_count == 1
    assert result.status is BLIImpliedPriceVolCalibrationStatus.SUCCESS
    assert result.reason is BLIImpliedPriceVolCalibrationReason.CALIBRATED
    assert result.solver_result is fake_solver_result


# --- 26-29. Individual F/T/DF resolution ValueError mapping ----------------------


def test_forward_clean_price_resolution_value_error_maps_to_input_resolution_failed():
    with mock.patch(
        f"{_MODULE}.forward_clean_price_per_100", side_effect=ValueError("forward boom")
    ):
        result = _calibrate(
            _REQUEST, _make_benchmark(), active_quote_side=_MID
        )
    assert result.status is BLIImpliedPriceVolCalibrationStatus.FAILED
    assert result.reason is BLIImpliedPriceVolCalibrationReason.INPUT_RESOLUTION_FAILED
    assert result.resolution_error_type == "ValueError"
    assert result.resolution_error_message == "forward boom"
    # Only strike_clean_price (a plain field read) is available before F fails.
    assert result.strike_clean_price == _REQUEST.bond_option.strike_price
    assert result.forward_clean_price is None
    assert result.time_to_expiry is None
    assert result.option_discount_factor is None
    assert result.solver_result is None


def test_time_to_expiry_resolution_value_error_maps_to_input_resolution_failed():
    with mock.patch(f"{_MODULE}.forward_clean_price_per_100", return_value=101.0), mock.patch(
        f"{_MODULE}.year_fraction_to_expiry", side_effect=ValueError("time boom")
    ):
        result = _calibrate(
            _REQUEST, _make_benchmark(), active_quote_side=_MID
        )
    assert result.reason is BLIImpliedPriceVolCalibrationReason.INPUT_RESOLUTION_FAILED
    assert result.resolution_error_type == "ValueError"
    assert result.resolution_error_message == "time boom"
    assert result.forward_clean_price == 101.0
    assert result.strike_clean_price == _REQUEST.bond_option.strike_price
    assert result.time_to_expiry is None
    assert result.option_discount_factor is None
    assert result.solver_result is None


def test_discount_factor_resolution_value_error_maps_to_input_resolution_failed():
    with mock.patch(f"{_MODULE}.forward_clean_price_per_100", return_value=101.0), mock.patch(
        f"{_MODULE}.year_fraction_to_expiry", return_value=0.5
    ), mock.patch(
        f"{_MODULE}.discount_factor_from_continuous_zero_curve", side_effect=ValueError("df boom")
    ):
        result = _calibrate(
            _REQUEST, _make_benchmark(), active_quote_side=_MID
        )
    assert result.reason is BLIImpliedPriceVolCalibrationReason.INPUT_RESOLUTION_FAILED
    assert result.resolution_error_type == "ValueError"
    assert result.resolution_error_message == "df boom"
    assert result.forward_clean_price == 101.0
    assert result.time_to_expiry == 0.5
    assert result.strike_clean_price == _REQUEST.bond_option.strike_price
    assert result.option_discount_factor is None
    assert result.solver_result is None


# --- 30. RuntimeError propagates (never caught, never relabeled) -----------------


def test_runtime_error_from_forward_resolution_propagates():
    with mock.patch(
        f"{_MODULE}.forward_clean_price_per_100", side_effect=RuntimeError("QuantLib not available")
    ):
        with pytest.raises(RuntimeError, match="QuantLib not available"):
            _calibrate(
                _REQUEST, _make_benchmark(), active_quote_side=_MID
            )


# --- 31. Solver configuration ValueError propagates unchanged --------------------


def test_solver_configuration_value_error_propagates_unchanged():
    with mock.patch(f"{_MODULE}.forward_clean_price_per_100", return_value=101.0), mock.patch(
        f"{_MODULE}.year_fraction_to_expiry", return_value=0.5
    ), mock.patch(f"{_MODULE}.discount_factor_from_continuous_zero_curve", return_value=0.98):
        with pytest.raises(ValueError, match="lower_price_vol"):
            _calibrate(
                _REQUEST,
                _make_benchmark(),
                active_quote_side=_MID,
                lower_price_vol=-1.0,
            )


# --- 32. Every solver economic failure maps to SOLVER_FAILED unchanged -----------


@pytest.mark.parametrize(
    "solver_reason",
    [
        BLIImpliedPriceVolSolverReason.BELOW_ARBITRAGE_LOWER_BOUND,
        BLIImpliedPriceVolSolverReason.AT_ARBITRAGE_LOWER_BOUND,
        BLIImpliedPriceVolSolverReason.AT_ARBITRAGE_UPPER_BOUND,
        BLIImpliedPriceVolSolverReason.ABOVE_ARBITRAGE_UPPER_BOUND,
        BLIImpliedPriceVolSolverReason.ROOT_NOT_BRACKETED,
        BLIImpliedPriceVolSolverReason.MAX_ITERATIONS_REACHED,
    ],
)
def test_every_solver_failure_reason_maps_to_solver_failed_unchanged(solver_reason):
    fake_solver_result = _fake_solver_result(
        status=BLIImpliedPriceVolSolverStatus.FAILED,
        reason=solver_reason,
        implied_price_vol=None,
        model_premium_per_100=None,
        premium_residual_per_100=None,
        iterations=0,
        final_bracket_lower_price_vol=None,
        final_bracket_upper_price_vol=None,
    )
    with mock.patch(f"{_MODULE}.forward_clean_price_per_100", return_value=101.0), mock.patch(
        f"{_MODULE}.year_fraction_to_expiry", return_value=0.5
    ), mock.patch(
        f"{_MODULE}.discount_factor_from_continuous_zero_curve", return_value=0.98
    ), mock.patch(f"{_MODULE}.solve_implied_price_vol", return_value=fake_solver_result):
        result = _calibrate(
            _REQUEST, _make_benchmark(), active_quote_side=_MID
        )
    assert result.status is BLIImpliedPriceVolCalibrationStatus.FAILED
    assert result.reason is BLIImpliedPriceVolCalibrationReason.SOLVER_FAILED
    assert result.solver_result is fake_solver_result
    assert result.solver_result.reason is solver_reason


# --- 33. No mutation of request / benchmark / snapshot ---------------------------


def test_request_benchmark_and_snapshot_are_not_mutated():
    request = _REQUEST
    benchmark = _make_benchmark()
    request_before = asdict(request)
    benchmark_before = asdict(benchmark)
    snapshot_before = asdict(request.market_data_snapshot)

    with mock.patch(f"{_MODULE}.forward_clean_price_per_100", return_value=101.0), mock.patch(
        f"{_MODULE}.year_fraction_to_expiry", return_value=0.5
    ), mock.patch(f"{_MODULE}.discount_factor_from_continuous_zero_curve", return_value=0.98):
        _calibrate(request, benchmark, active_quote_side=_MID)

    assert asdict(request) == request_before
    assert asdict(benchmark) == benchmark_before
    assert asdict(request.market_data_snapshot) == snapshot_before


# --- 34-35. Frozen result / deterministic asdict ----------------------------------


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


# --- 36. Public signature has no forbidden parameters -----------------------------


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


# --- 37. Module-boundary proof -----------------------------------------------------


def test_module_imports_only_the_expected_dependencies():
    tree = ast.parse(inspect.getsource(calibration_module))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
    assert imported_names == {
        "__future__",
        "dataclasses",
        "enum",
        "shiori_pricing_lab.data._validation",
        "shiori_pricing_lab.data.bli_benchmark_quote",
        "shiori_pricing_lab.data.bli_snapshot",
        "shiori_pricing_lab.data.bli_standalone_option_request",
        "shiori_pricing_lab.pricing.bli_curve_discount_factor",
        "shiori_pricing_lab.pricing.bli_forward_clean_price",
        "shiori_pricing_lab.pricing.bli_implied_price_vol_solver",
        "shiori_pricing_lab.pricing.bli_mvp_required_input_guard",
        "shiori_pricing_lab.pricing.bli_pricing_engine",
        "shiori_pricing_lab.pricing.bli_valuation_time",
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
    }
    assert module_names.isdisjoint(forbidden_names)


def test_module_source_does_not_use_system_clock():
    source = inspect.getsource(calibration_module)
    assert "date.today(" not in source
    assert "datetime.now(" not in source
    assert "import datetime" not in source


def test_module_defines_no_yield_vol_surface_sabr_or_scipy_names():
    # dir()-based: inspects the module's actual defined/imported names, not
    # prose in the docstring's non-goals list (which legitimately names
    # YIELD_VOL/SABR/American-exercise as things this module does NOT do).
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
