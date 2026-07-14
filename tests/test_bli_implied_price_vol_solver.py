"""Tests for ``solve_implied_price_vol`` (Issue #99 PR A bounded solver).

All fixtures are explicitly synthetic float inputs chosen only to exercise
the solver's arbitrage-bound, bracketing, tolerance, and iteration logic in
isolation. None represents Bloomberg output, a golden case, market
calibration evidence, or UAT -- Issue #94's sanitized Bloomberg golden case
remains open and gates only licensed-environment UAT evidence, not this
synthetic, deterministic solver.
"""

from __future__ import annotations

import ast
import inspect
import math
from dataclasses import FrozenInstanceError, asdict

import pytest

from shiori_pricing_lab.pricing import bli_implied_price_vol_solver as solver_module
from shiori_pricing_lab.pricing.bli_black76_price_option import (
    black76_price_option_pv_per_100,
)
from shiori_pricing_lab.pricing.bli_implied_price_vol_solver import (
    BLIImpliedPriceVolSolverReason,
    BLIImpliedPriceVolSolverResult,
    BLIImpliedPriceVolSolverStatus,
    solve_implied_price_vol,
)
from shiori_pricing_lab.products.enums import OptionType

# --- Shared synthetic fixtures -------------------------------------------------

_F = 100.0
_K = 95.0
_T = 0.5
_DF = 0.98


def _price(sigma: float, *, option_type: OptionType = OptionType.CALL, **overrides) -> float:
    params = dict(
        forward_clean_price=_F,
        strike_clean_price=_K,
        price_volatility=sigma,
        time_to_expiry=_T,
        discount_factor=_DF,
        option_type=option_type,
    )
    params.update(overrides)
    return black76_price_option_pv_per_100(**params)


# --- 1-2. Known synthetic recovery ----------------------------------------------


def test_known_synthetic_call_recovery():
    sigma_true = 0.20
    target = _price(sigma_true, option_type=OptionType.CALL)
    result = solve_implied_price_vol(
        forward_clean_price=_F,
        strike_clean_price=_K,
        time_to_expiry=_T,
        discount_factor=_DF,
        option_type=OptionType.CALL,
        target_premium_per_100=target,
    )
    assert result.status is BLIImpliedPriceVolSolverStatus.SUCCESS
    assert result.reason is BLIImpliedPriceVolSolverReason.CONVERGED
    assert result.implied_price_vol == pytest.approx(sigma_true, abs=1e-6)
    assert abs(result.model_premium_per_100 - target) <= result.premium_tolerance_per_100


def test_known_synthetic_put_recovery():
    sigma_true = 0.20
    target = _price(sigma_true, option_type=OptionType.PUT)
    result = solve_implied_price_vol(
        forward_clean_price=_F,
        strike_clean_price=_K,
        time_to_expiry=_T,
        discount_factor=_DF,
        option_type=OptionType.PUT,
        target_premium_per_100=target,
    )
    assert result.status is BLIImpliedPriceVolSolverStatus.SUCCESS
    assert result.reason is BLIImpliedPriceVolSolverReason.CONVERGED
    assert result.implied_price_vol == pytest.approx(sigma_true, abs=1e-6)
    assert abs(result.model_premium_per_100 - target) <= result.premium_tolerance_per_100


# --- 3. Defaults stored exactly ---------------------------------------------------


def test_defaults_stored_exactly():
    result = solve_implied_price_vol(
        forward_clean_price=_F,
        strike_clean_price=_K,
        time_to_expiry=_T,
        discount_factor=_DF,
        option_type=OptionType.CALL,
        target_premium_per_100=6.0,
    )
    assert result.lower_price_vol == 0.000001
    assert result.upper_price_vol == 5.0
    assert result.premium_tolerance_per_100 == 1e-8
    assert result.price_vol_tolerance == 1e-8
    assert result.max_iterations == 100


# --- 4-5. Configured lower/upper-vol root: CONVERGED, iterations=0 ---------------


def test_configured_lower_vol_root_converges_with_zero_iterations():
    # ATM (F == K): the theoretical arbitrage lower bound is exactly 0.0,
    # comfortably distinct from the (tiny but positive) model premium at
    # lower_price_vol, so the theoretical-bound checks never intercept this
    # case before the configured-bound boundary check runs.
    lower_vol = 0.000001
    target = _price(lower_vol, forward_clean_price=_F, strike_clean_price=_F)
    result = solve_implied_price_vol(
        forward_clean_price=_F,
        strike_clean_price=_F,
        time_to_expiry=_T,
        discount_factor=_DF,
        option_type=OptionType.CALL,
        target_premium_per_100=target,
    )
    assert result.status is BLIImpliedPriceVolSolverStatus.SUCCESS
    assert result.reason is BLIImpliedPriceVolSolverReason.CONVERGED
    assert result.implied_price_vol == lower_vol
    assert result.iterations == 0
    assert result.final_bracket_lower_price_vol == lower_vol
    assert result.final_bracket_upper_price_vol == lower_vol


def test_configured_upper_vol_root_converges_with_zero_iterations():
    upper_vol = 5.0
    target = _price(upper_vol, forward_clean_price=_F, strike_clean_price=_F)
    result = solve_implied_price_vol(
        forward_clean_price=_F,
        strike_clean_price=_F,
        time_to_expiry=_T,
        discount_factor=_DF,
        option_type=OptionType.CALL,
        target_premium_per_100=target,
    )
    assert result.status is BLIImpliedPriceVolSolverStatus.SUCCESS
    assert result.reason is BLIImpliedPriceVolSolverReason.CONVERGED
    assert result.implied_price_vol == upper_vol
    assert result.iterations == 0
    assert result.final_bracket_lower_price_vol == upper_vol
    assert result.final_bracket_upper_price_vol == upper_vol


# --- 6-9. Theoretical arbitrage bounds (ITM call: F=110, K=100) ------------------

_ITM_F = 110.0
_ITM_K = 100.0
_THEORETICAL_LOWER = _DF * max(_ITM_F - _ITM_K, 0.0)
_THEORETICAL_UPPER = _DF * _ITM_F


def _solve_itm(target: float, **overrides) -> BLIImpliedPriceVolSolverResult:
    params = dict(
        forward_clean_price=_ITM_F,
        strike_clean_price=_ITM_K,
        time_to_expiry=_T,
        discount_factor=_DF,
        option_type=OptionType.CALL,
        target_premium_per_100=target,
    )
    params.update(overrides)
    return solve_implied_price_vol(**params)


def test_below_arbitrage_lower_bound():
    result = _solve_itm(0.0)
    assert result.status is BLIImpliedPriceVolSolverStatus.FAILED
    assert result.reason is BLIImpliedPriceVolSolverReason.BELOW_ARBITRAGE_LOWER_BOUND
    assert result.implied_price_vol is None
    assert result.model_premium_per_100 is None
    assert result.premium_residual_per_100 is None
    assert result.lower_bound_model_premium_per_100 is None
    assert result.upper_bound_model_premium_per_100 is None
    assert result.final_bracket_lower_price_vol is None
    assert result.final_bracket_upper_price_vol is None
    assert result.iterations == 0
    assert result.arbitrage_lower_bound_per_100 == _THEORETICAL_LOWER
    assert result.diagnostic_note


def test_at_arbitrage_lower_bound():
    result = _solve_itm(_THEORETICAL_LOWER)
    assert result.status is BLIImpliedPriceVolSolverStatus.FAILED
    assert result.reason is BLIImpliedPriceVolSolverReason.AT_ARBITRAGE_LOWER_BOUND
    assert result.implied_price_vol is None
    assert result.iterations == 0


def test_at_arbitrage_upper_bound():
    result = _solve_itm(_THEORETICAL_UPPER)
    assert result.status is BLIImpliedPriceVolSolverStatus.FAILED
    assert result.reason is BLIImpliedPriceVolSolverReason.AT_ARBITRAGE_UPPER_BOUND
    assert result.implied_price_vol is None
    assert result.iterations == 0


def test_above_arbitrage_upper_bound():
    result = _solve_itm(_THEORETICAL_UPPER + 1.0)
    assert result.status is BLIImpliedPriceVolSolverStatus.FAILED
    assert result.reason is BLIImpliedPriceVolSolverReason.ABOVE_ARBITRAGE_UPPER_BOUND
    assert result.implied_price_vol is None
    assert result.iterations == 0


# --- 10. Zero target receives the applicable theoretical-bound reason -----------


def test_zero_target_receives_at_arbitrage_lower_bound_for_otm_call():
    # OTM call (K > F): theoretical_lower = DF * max(F - K, 0) == 0.0 exactly,
    # so a zero target lands exactly AT the lower bound, not below it.
    otm_f, otm_k = 90.0, 100.0
    theoretical_lower = _DF * max(otm_f - otm_k, 0.0)
    assert theoretical_lower == 0.0
    result = solve_implied_price_vol(
        forward_clean_price=otm_f,
        strike_clean_price=otm_k,
        time_to_expiry=_T,
        discount_factor=_DF,
        option_type=OptionType.CALL,
        target_premium_per_100=0.0,
    )
    assert result.status is BLIImpliedPriceVolSolverStatus.FAILED
    assert result.reason is BLIImpliedPriceVolSolverReason.AT_ARBITRAGE_LOWER_BOUND


# --- 11. Genuine ROOT_NOT_BRACKETED ----------------------------------------------


def test_root_not_bracketed_within_theoretical_but_outside_configured_bracket():
    # ATM call: theoretical bounds are [0, DF*F] = [0, 95.0]; target=50.0 is
    # strictly inside that range, but a narrow configured vol bracket
    # [0.01, 0.05] never reaches a model premium anywhere near 50.0.
    target = 50.0
    theoretical_upper = _DF * _F
    assert 0.0 < target < theoretical_upper
    result = solve_implied_price_vol(
        forward_clean_price=_F,
        strike_clean_price=_F,  # ATM: K == F
        time_to_expiry=_T,
        discount_factor=_DF,
        option_type=OptionType.CALL,
        target_premium_per_100=target,
        lower_price_vol=0.01,
        upper_price_vol=0.05,
    )
    assert result.status is BLIImpliedPriceVolSolverStatus.FAILED
    assert result.reason is BLIImpliedPriceVolSolverReason.ROOT_NOT_BRACKETED
    assert result.implied_price_vol is None
    assert result.premium_residual_per_100 is None
    assert result.lower_bound_model_premium_per_100 is not None
    assert result.upper_bound_model_premium_per_100 is not None
    assert result.iterations == 0
    assert result.final_bracket_lower_price_vol == 0.01
    assert result.final_bracket_upper_price_vol == 0.05


# --- Numeric-domain regression: finite, correctly-ordered bounds outside what --
# --- black76_price_option_pv_per_100 can evaluate must never leak a raw error --


def test_extreme_upper_price_vol_raises_value_error_not_overflow_error():
    with pytest.raises(ValueError, match="upper_price_vol") as excinfo:
        solve_implied_price_vol(
            forward_clean_price=_F,
            strike_clean_price=_K,
            time_to_expiry=_T,
            discount_factor=_DF,
            option_type=OptionType.CALL,
            target_premium_per_100=6.0,
            upper_price_vol=1e308,
        )
    assert not isinstance(excinfo.value, OverflowError)


def test_extreme_lower_price_vol_raises_value_error_not_overflow_error():
    with pytest.raises(ValueError, match="lower_price_vol") as excinfo:
        solve_implied_price_vol(
            forward_clean_price=_F,
            strike_clean_price=_K,
            time_to_expiry=_T,
            discount_factor=_DF,
            option_type=OptionType.CALL,
            target_premium_per_100=6.0,
            lower_price_vol=1e308,
            upper_price_vol=1.5e308,
        )
    assert not isinstance(excinfo.value, OverflowError)


def test_ordinary_configured_bounds_still_converge_normally():
    # Same fixture as test_known_synthetic_call_recovery -- proves the
    # numeric-domain guard does not change any existing outcome for
    # well-behaved, default-range configured bounds.
    sigma_true = 0.20
    target = _price(sigma_true, option_type=OptionType.CALL)
    result = solve_implied_price_vol(
        forward_clean_price=_F,
        strike_clean_price=_K,
        time_to_expiry=_T,
        discount_factor=_DF,
        option_type=OptionType.CALL,
        target_premium_per_100=target,
    )
    assert result.status is BLIImpliedPriceVolSolverStatus.SUCCESS
    assert result.reason is BLIImpliedPriceVolSolverReason.CONVERGED
    assert result.implied_price_vol == pytest.approx(sigma_true, abs=1e-6)


# --- Dyadic dual-tolerance-boundary fixture (shared by tests 12-14) -------------

_BRACKET_F = 100.0
_BRACKET_K = 100.0
_BRACKET_LOWER_VOL = 0.25
_BRACKET_UPPER_VOL = 0.75
_BRACKET_MIDPOINT_VOL = (_BRACKET_LOWER_VOL + _BRACKET_UPPER_VOL) / 2.0  # 0.5, exact
_BRACKET_MID_PREMIUM = black76_price_option_pv_per_100(
    forward_clean_price=_BRACKET_F,
    strike_clean_price=_BRACKET_K,
    price_volatility=_BRACKET_MIDPOINT_VOL,
    time_to_expiry=_T,
    discount_factor=_DF,
    option_type=OptionType.CALL,
)
_BRACKET_TARGET = _BRACKET_MID_PREMIUM - 0.01
_BRACKET_ITER1_RESIDUAL = _BRACKET_MID_PREMIUM - _BRACKET_TARGET


def _solve_bracket(**overrides) -> BLIImpliedPriceVolSolverResult:
    params = dict(
        forward_clean_price=_BRACKET_F,
        strike_clean_price=_BRACKET_K,
        time_to_expiry=_T,
        discount_factor=_DF,
        option_type=OptionType.CALL,
        target_premium_per_100=_BRACKET_TARGET,
        lower_price_vol=_BRACKET_LOWER_VOL,
        upper_price_vol=_BRACKET_UPPER_VOL,
    )
    params.update(overrides)
    return solve_implied_price_vol(**params)


# --- 12. Premium-tolerance boundary is inclusive ---------------------------------


def test_premium_tolerance_boundary_is_inclusive():
    # premium_tolerance_per_100 is set to the *exact* residual the solver
    # will itself compute at iteration 1 (self-referential capture, so the
    # comparison is bit-exact equality, not an approximation) -- a
    # comfortably loose price_vol_tolerance isolates the premium boundary.
    result = _solve_bracket(
        premium_tolerance_per_100=_BRACKET_ITER1_RESIDUAL,
        price_vol_tolerance=0.5,
    )
    assert result.status is BLIImpliedPriceVolSolverStatus.SUCCESS
    assert result.reason is BLIImpliedPriceVolSolverReason.CONVERGED
    assert result.iterations == 1
    assert abs(result.premium_residual_per_100) == result.premium_tolerance_per_100


# --- 13. Vol-tolerance boundary is inclusive -------------------------------------


def test_vol_tolerance_boundary_is_inclusive():
    # price_vol_tolerance is set to exactly the bracket width after one
    # bisection halving (0.5 / 2 == 0.25, exact in binary floating point);
    # a comfortably loose premium_tolerance isolates the vol boundary.
    result = _solve_bracket(premium_tolerance_per_100=1.0, price_vol_tolerance=0.25)
    assert result.status is BLIImpliedPriceVolSolverStatus.SUCCESS
    assert result.reason is BLIImpliedPriceVolSolverReason.CONVERGED
    assert result.iterations == 1
    width = result.final_bracket_upper_price_vol - result.final_bracket_lower_price_vol
    assert width == result.price_vol_tolerance


# --- 14. Convergence requires both tolerances, not either alone -----------------


def test_premium_tolerance_alone_is_not_sufficient():
    # Premium condition is satisfied (via self-referential capture) at
    # iteration 1, but price_vol_tolerance (0.1) is tighter than the
    # iteration-1 bracket width (0.25) -- the solver must not stop early.
    result = _solve_bracket(
        premium_tolerance_per_100=_BRACKET_ITER1_RESIDUAL,
        price_vol_tolerance=0.1,
        max_iterations=3,
    )
    assert result.iterations != 1
    assert result.status is BLIImpliedPriceVolSolverStatus.FAILED
    assert result.reason is BLIImpliedPriceVolSolverReason.MAX_ITERATIONS_REACHED
    assert result.iterations == 3


def test_vol_tolerance_alone_is_not_sufficient():
    # Vol-width condition is satisfied at iteration 1 (0.25 <= 0.3), but
    # premium_tolerance (0.001) is far tighter than the residual at every
    # iteration reached -- the solver must not stop early.
    result = _solve_bracket(
        premium_tolerance_per_100=0.001,
        price_vol_tolerance=0.3,
        max_iterations=3,
    )
    assert result.iterations != 1
    assert result.status is BLIImpliedPriceVolSolverStatus.FAILED
    assert result.reason is BLIImpliedPriceVolSolverReason.MAX_ITERATIONS_REACHED
    assert result.iterations == 3


# --- 15. MAX_ITERATIONS_REACHED preserves diagnostics ----------------------------


def test_max_iterations_reached_preserves_diagnostics():
    result = solve_implied_price_vol(
        forward_clean_price=_F,
        strike_clean_price=_K,
        time_to_expiry=_T,
        discount_factor=_DF,
        option_type=OptionType.CALL,
        target_premium_per_100=6.0,
        premium_tolerance_per_100=1e-15,
        price_vol_tolerance=1e-15,
        max_iterations=5,
    )
    assert result.status is BLIImpliedPriceVolSolverStatus.FAILED
    assert result.reason is BLIImpliedPriceVolSolverReason.MAX_ITERATIONS_REACHED
    assert result.iterations == 5
    assert result.implied_price_vol is not None
    assert result.model_premium_per_100 is not None
    assert result.premium_residual_per_100 is not None
    assert result.final_bracket_lower_price_vol is not None
    assert result.final_bracket_upper_price_vol is not None
    assert result.diagnostic_note


# --- 16. Repeat calls are deterministic ------------------------------------------


def test_repeat_calls_are_deterministic():
    kwargs = dict(
        forward_clean_price=_F,
        strike_clean_price=_K,
        time_to_expiry=_T,
        discount_factor=_DF,
        option_type=OptionType.CALL,
        target_premium_per_100=6.0,
    )
    first = solve_implied_price_vol(**kwargs)
    second = solve_implied_price_vol(**kwargs)
    assert asdict(first) == asdict(second)
    assert first.iterations == second.iterations
    assert first.final_bracket_lower_price_vol == second.final_bracket_lower_price_vol
    assert first.final_bracket_upper_price_vol == second.final_bracket_upper_price_vol


# --- 17. Negative target rejected -------------------------------------------------


def test_negative_target_premium_rejected():
    with pytest.raises(ValueError, match="target_premium_per_100"):
        solve_implied_price_vol(
            forward_clean_price=_F,
            strike_clean_price=_K,
            time_to_expiry=_T,
            discount_factor=_DF,
            option_type=OptionType.CALL,
            target_premium_per_100=-1.0,
        )


# --- 18. Contract-violation input rejection --------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"forward_clean_price": True},
        {"strike_clean_price": math.nan},
        {"time_to_expiry": math.inf},
        {"discount_factor": -math.inf},
        {"forward_clean_price": 0.0},
        {"strike_clean_price": -1.0},
        {"time_to_expiry": 0.0},
        {"discount_factor": 0.0},
        {"lower_price_vol": 0.0},
        {"lower_price_vol": -0.1},
        {"lower_price_vol": 5.0, "upper_price_vol": 5.0},
        {"lower_price_vol": 6.0, "upper_price_vol": 5.0},
        {"lower_price_vol": math.nan},
        {"upper_price_vol": math.inf},
        {"premium_tolerance_per_100": 0.0},
        {"premium_tolerance_per_100": -1e-8},
        {"price_vol_tolerance": 0.0},
        {"price_vol_tolerance": -1e-8},
        {"max_iterations": 0},
        {"max_iterations": -5},
        {"max_iterations": 100.0},
        {"max_iterations": True},
    ],
)
def test_invalid_configuration_rejected(overrides):
    kwargs = dict(
        forward_clean_price=_F,
        strike_clean_price=_K,
        time_to_expiry=_T,
        discount_factor=_DF,
        option_type=OptionType.CALL,
        target_premium_per_100=6.0,
    )
    kwargs.update(overrides)
    with pytest.raises((TypeError, ValueError)):
        solve_implied_price_vol(**kwargs)


# --- 19. Native OptionType members and raw strings accepted ----------------------


@pytest.mark.parametrize("option_type", [OptionType.CALL, OptionType.PUT, "CALL", "PUT"])
def test_option_type_accepts_native_members_and_raw_strings(option_type):
    result = solve_implied_price_vol(
        forward_clean_price=_F,
        strike_clean_price=_K,
        time_to_expiry=_T,
        discount_factor=_DF,
        option_type=option_type,
        target_premium_per_100=6.0,
    )
    assert result.option_type in (OptionType.CALL, OptionType.PUT)


def test_invalid_option_type_rejected():
    with pytest.raises(ValueError, match="option_type"):
        solve_implied_price_vol(
            forward_clean_price=_F,
            strike_clean_price=_K,
            time_to_expiry=_T,
            discount_factor=_DF,
            option_type="STRADDLE",
            target_premium_per_100=6.0,
        )


# --- 20. Frozen result / deterministic asdict ------------------------------------


def test_result_is_frozen():
    result = solve_implied_price_vol(
        forward_clean_price=_F,
        strike_clean_price=_K,
        time_to_expiry=_T,
        discount_factor=_DF,
        option_type=OptionType.CALL,
        target_premium_per_100=6.0,
    )
    with pytest.raises(FrozenInstanceError):
        result.status = BLIImpliedPriceVolSolverStatus.FAILED  # type: ignore[misc]


def test_asdict_is_deterministic():
    kwargs = dict(
        forward_clean_price=_F,
        strike_clean_price=_K,
        time_to_expiry=_T,
        discount_factor=_DF,
        option_type=OptionType.CALL,
        target_premium_per_100=6.0,
    )
    result = solve_implied_price_vol(**kwargs)
    assert asdict(result) == asdict(result)
    assert asdict(result) == asdict(solve_implied_price_vol(**kwargs))


# --- 21. Public signature has no forbidden parameters -----------------------------


def test_signature_has_no_request_benchmark_or_notional_parameters():
    signature = inspect.signature(solve_implied_price_vol)
    forbidden_names = {
        "request",
        "benchmark",
        "market_data_snapshot",
        "snapshot",
        "pricing_result",
        "notional",
        "total_premium",
        "target_total_premium",
        "initial_vol",
        "initial_price_vol",
        "guess",
    }
    assert forbidden_names.isdisjoint(signature.parameters.keys())


# --- 22. Module-boundary: import allowlist + forbidden names ---------------------


def test_module_imports_only_the_expected_dependencies():
    tree = ast.parse(inspect.getsource(solver_module))
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
        "enum",
        "shiori_pricing_lab.pricing.bli_black76_price_option",
        "shiori_pricing_lab.products.enums",
    }


def test_module_defines_no_request_benchmark_provider_or_ui_names():
    module_names = set(dir(solver_module))
    forbidden_names = {
        "BLIStandaloneBondOptionRequest",
        "BLIBenchmarkQuote",
        "PricingResult",
        "price_bli_mvp_standalone_option",
        "BLIMarketDataSnapshot",
        "requests",
        "socket",
        "scipy",
        "numpy",
        "streamlit",
        "ExerciseStyle",
        "SettlementType",
    }
    assert module_names.isdisjoint(forbidden_names)


def test_module_source_does_not_use_system_clock():
    source = inspect.getsource(solver_module)
    assert "date.today(" not in source
    assert "datetime.now(" not in source
    assert "import datetime" not in source


def test_module_source_has_no_scipy_or_numpy_reference():
    source = inspect.getsource(solver_module)
    assert "scipy" not in source.lower()
    assert "numpy" not in source.lower()


# --- 23. Solver reuses the Black-76 primitive, never reimplements it -------------


def test_module_calls_but_does_not_reimplement_black76():
    source = inspect.getsource(solver_module)
    assert "black76_price_option_pv_per_100(" in source
    # No re-derivation of the closed-form formula: no d1/d2/CDF/erf/normal
    # distribution math anywhere in this module.
    forbidden_tokens = ("erf(", "d1 =", "d2 =", "_standard_normal_cdf", "norm.cdf", "NormalDist")
    for token in forbidden_tokens:
        assert token not in source, f"unexpected token {token!r} found in module source"
