"""Tests for `pricing/bli_curve_discount_factor.py`.

Covers `discount_factor_from_continuous_zero_curve`'s composition of
the four already-reviewed helpers this dependency chain has built
(selector, node preparation, interpolation, discount factor), asserting
it produces the same deterministic result as calling them by hand and
that every propagated failure mode (missing selector match, non-
continuous rate basis, out-of-range target, invalid target) still
surfaces correctly, plus the module-boundary checks confirming this
slice stays a pure composition helper -- no direct tenor parsing, no
direct rate/rate_basis inspection, no interpolation/discount-factor
math of its own, no forward price/PV/Black-76, and no pricing-engine
wiring.
"""

from __future__ import annotations

import inspect
from math import exp

import pytest

from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIMarketDataStatus,
)
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.pricing import (
    bli_curve_discount_factor as bli_curve_discount_factor_module,
)
from shiori_pricing_lab.pricing.bli_curve_discount_factor import (
    discount_factor_from_continuous_zero_curve,
)
from shiori_pricing_lab.products.enums import Currency

_CURVE_POINTS = SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.curve_points


# --- 1. Option discount curve in-range interpolated case ---------------------


def test_option_discount_curve_interpolated_target():
    expected_rate = 0.0341 + (0.0353 - 0.0341) * (3.5 - 2.0) / (5.0 - 2.0)
    expected_df = exp(-expected_rate * 3.5)
    result = discount_factor_from_continuous_zero_curve(
        _CURVE_POINTS,
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
        target_year_fraction=3.5,
    )
    assert result == pytest.approx(expected_df)


# --- 2. Option discount curve exact node case --------------------------------


def test_option_discount_curve_exact_node_target():
    result = discount_factor_from_continuous_zero_curve(
        _CURVE_POINTS,
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
        target_year_fraction=2.0,
    )
    assert result == pytest.approx(exp(-0.0341 * 2.0))


# --- 3. Reversed input order still works -------------------------------------


def test_reversed_curve_points_order_gives_same_result():
    reversed_points = tuple(reversed(_CURVE_POINTS))
    normal_result = discount_factor_from_continuous_zero_curve(
        _CURVE_POINTS,
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
        target_year_fraction=3.5,
    )
    reversed_result = discount_factor_from_continuous_zero_curve(
        reversed_points,
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
        target_year_fraction=3.5,
    )
    assert reversed_result == pytest.approx(normal_result)


# --- 4. Deposit curve exact single-node case ---------------------------------


def test_deposit_curve_exact_single_node_target():
    result = discount_factor_from_continuous_zero_curve(
        _CURVE_POINTS,
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.DEPOSIT_CURVE,
        target_year_fraction=0.25,
    )
    assert result == pytest.approx(exp(-0.0350 * 0.25))


# --- 5. Out-of-range option discount target rejected -------------------------


def test_out_of_range_option_discount_target_raises_value_error():
    with pytest.raises(ValueError, match="extrapolation"):
        discount_factor_from_continuous_zero_curve(
            _CURVE_POINTS,
            currency=Currency.USD,
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            target_year_fraction=10.0,
        )


# --- 6. Single-node deposit non-exact target rejected ------------------------


def test_single_node_deposit_non_exact_target_raises_value_error():
    with pytest.raises(ValueError, match="extrapolation"):
        discount_factor_from_continuous_zero_curve(
            _CURVE_POINTS,
            currency=Currency.USD,
            curve_purpose=BLICurvePurpose.DEPOSIT_CURVE,
            target_year_fraction=1.0,
        )


# --- 7. Missing curve purpose rejected ---------------------------------------


def test_missing_curve_purpose_raises_value_error():
    with pytest.raises(ValueError):
        discount_factor_from_continuous_zero_curve(
            _CURVE_POINTS,
            currency=Currency.USD,
            curve_purpose=BLICurvePurpose.FUNDING_CURVE,
            target_year_fraction=1.0,
        )


# --- 8. Missing currency rejected ---------------------------------------------


def test_missing_currency_raises_value_error():
    with pytest.raises(ValueError):
        discount_factor_from_continuous_zero_curve(
            _CURVE_POINTS,
            currency=Currency.EUR,
            curve_purpose=BLICurvePurpose.DEPOSIT_CURVE,
            target_year_fraction=1.0,
        )


# --- 9. Non-continuous rate basis rejected ------------------------------------


def test_non_continuous_rate_basis_raises_value_error_mentioning_expected_basis():
    par_points = (
        BLICurvePoint(
            curve_id="TEST_PAR_CURVE",
            curve_name="Test Par Curve",
            currency=Currency.USD,
            curve_purpose=BLICurvePurpose.FUNDING_CURVE,
            tenor="2Y",
            rate=0.03,
            rate_basis=BLICurveRateBasis.PAR_RATE,
            source_system="test",
            status=BLIMarketDataStatus.ACTIVE,
        ),
        BLICurvePoint(
            curve_id="TEST_PAR_CURVE",
            curve_name="Test Par Curve",
            currency=Currency.USD,
            curve_purpose=BLICurvePurpose.FUNDING_CURVE,
            tenor="5Y",
            rate=0.032,
            rate_basis=BLICurveRateBasis.PAR_RATE,
            source_system="test",
            status=BLIMarketDataStatus.ACTIVE,
        ),
    )
    with pytest.raises(ValueError, match="CONTINUOUS_ZERO_RATE"):
        discount_factor_from_continuous_zero_curve(
            par_points,
            currency=Currency.USD,
            curve_purpose=BLICurvePurpose.FUNDING_CURVE,
            target_year_fraction=3.0,
        )


# --- 10. Invalid target rejected -----------------------------------------------


@pytest.mark.parametrize("target", [0.0, -1.0, float("nan"), float("inf"), float("-inf")])
def test_invalid_target_year_fraction_raises_value_error(target):
    with pytest.raises(ValueError):
        discount_factor_from_continuous_zero_curve(
            _CURVE_POINTS,
            currency=Currency.USD,
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            target_year_fraction=target,
        )


# --- 11. Boundary / module tests ----------------------------------------------


def test_module_uses_the_four_expected_helper_functions():
    source = inspect.getsource(bli_curve_discount_factor_module)
    for helper in (
        "select_curve_points_by_purpose",
        "build_continuous_zero_curve_nodes",
        "interpolate_continuous_zero_rate",
        "continuous_discount_factor",
    ):
        assert helper in source


def test_module_does_not_import_bundle_pricing_engine_or_result_types():
    import_lines = [
        line
        for line in inspect.getsource(bli_curve_discount_factor_module).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    forbidden_substrings = (
        "bli_mvp_input_bundle",
        "BLIMVPInputBundle",
        "bli_pricing_engine",
        "price_bli_mvp",
        "PricingResult",
        "PricingStatus",
        "PricingErrorCode",
        "QuantLib",
    )
    for line in import_lines:
        for forbidden in forbidden_substrings:
            assert forbidden not in line

    module_names = set(dir(bli_curve_discount_factor_module))
    for forbidden in forbidden_substrings:
        assert forbidden not in module_names


def test_module_defines_no_own_interpolation_discount_factor_or_pricing_logic():
    module_names = set(dir(bli_curve_discount_factor_module))
    forbidden_names = {
        "forward_price",
        "forward_clean_price",
        "compute_pv",
        "black76",
        "black_76",
        "tenor_to_year_fraction",
    }
    assert module_names.isdisjoint(forbidden_names)
