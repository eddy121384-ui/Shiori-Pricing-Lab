"""Tests for `pricing/bli_zero_rate_interpolation.py`.

Covers piecewise-linear interpolation on continuously-compounded zero
rates (Annex A §A.10.2), exact node matches, flat extrapolation
(rate only, no fallback-flag object), single-node curves, input/node
validation, and the module-boundary checks confirming this slice stays
a narrow interpolation helper -- no tenor parsing, curve-purpose
selection, discount factor, forward price, or pricing logic.
"""

from __future__ import annotations

import inspect

import pytest

from shiori_pricing_lab.data.bli_snapshot import BLICurvePurpose
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.pricing import (
    bli_zero_rate_interpolation as bli_zero_rate_interpolation_module,
)
from shiori_pricing_lab.pricing.bli_curve_selector import select_curve_points_by_purpose
from shiori_pricing_lab.pricing.bli_zero_curve_nodes import (
    BLIContinuousZeroCurveNode,
    build_continuous_zero_curve_nodes,
)
from shiori_pricing_lab.pricing.bli_zero_rate_interpolation import (
    interpolate_continuous_zero_rate,
)
from shiori_pricing_lab.products.enums import Currency

_CURVE_POINTS = SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.curve_points


def _option_discount_nodes():
    selected = select_curve_points_by_purpose(
        _CURVE_POINTS,
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
    )
    return build_continuous_zero_curve_nodes(selected)


def _deposit_nodes():
    selected = select_curve_points_by_purpose(
        _CURVE_POINTS,
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.DEPOSIT_CURVE,
    )
    return build_continuous_zero_curve_nodes(selected)


# --- 1. Exact node match from fixture-derived nodes -------------------------


def test_exact_node_match_returns_node_rate():
    nodes = _option_discount_nodes()
    assert interpolate_continuous_zero_rate(nodes, 2.0) == pytest.approx(0.0341)
    assert interpolate_continuous_zero_rate(nodes, 5.0) == pytest.approx(0.0353)


# --- 2. Interpolation between fixture nodes ---------------------------------


def test_interpolation_between_fixture_nodes():
    nodes = _option_discount_nodes()
    expected = 0.0341 + (0.0353 - 0.0341) * (3.5 - 2.0) / (5.0 - 2.0)
    assert interpolate_continuous_zero_rate(nodes, 3.5) == pytest.approx(expected)


# --- 3. Sorting --------------------------------------------------------------


def test_reversed_node_order_gives_same_result():
    nodes = _option_discount_nodes()
    reversed_nodes = tuple(reversed(nodes))
    expected = interpolate_continuous_zero_rate(nodes, 3.5)
    assert interpolate_continuous_zero_rate(reversed_nodes, 3.5) == pytest.approx(expected)


# --- 4/5. Flat extrapolation -------------------------------------------------


def test_flat_extrapolation_below_range():
    nodes = _option_discount_nodes()
    assert interpolate_continuous_zero_rate(nodes, 1.0) == pytest.approx(0.0341)


def test_flat_extrapolation_above_range():
    nodes = _option_discount_nodes()
    assert interpolate_continuous_zero_rate(nodes, 10.0) == pytest.approx(0.0353)


# --- 6. Single-node behavior -------------------------------------------------


@pytest.mark.parametrize("target", [0.25, 1.0, 0.01])
def test_single_node_deposit_curve_returns_its_rate_for_any_target(target):
    nodes = _deposit_nodes()
    assert len(nodes) == 1
    assert interpolate_continuous_zero_rate(nodes, target) == pytest.approx(0.0350)


# --- 7. Empty nodes rejected --------------------------------------------------


def test_empty_nodes_raises_value_error():
    with pytest.raises(ValueError):
        interpolate_continuous_zero_rate((), 1.0)


# --- 8. Non-node element rejected --------------------------------------------


def test_non_node_element_raises_type_error():
    nodes = _option_discount_nodes()
    with pytest.raises(TypeError):
        interpolate_continuous_zero_rate((*nodes, object()), 3.0)


# --- 9. Invalid target rejected -----------------------------------------------


@pytest.mark.parametrize("target", [0.0, -1.0, float("nan"), float("inf"), float("-inf")])
def test_invalid_target_year_fraction_raises_value_error(target):
    nodes = _option_discount_nodes()
    with pytest.raises(ValueError):
        interpolate_continuous_zero_rate(nodes, target)


# --- 10. Invalid node year_fraction rejected ----------------------------------


@pytest.mark.parametrize("year_fraction", [0.0, -1.0, float("nan"), float("inf"), float("-inf")])
def test_invalid_node_year_fraction_raises_value_error(year_fraction):
    node = BLIContinuousZeroCurveNode(
        year_fraction=year_fraction,
        zero_rate=0.03,
        source_tenor="2Y",
        source_curve_id="TEST_CURVE",
    )
    with pytest.raises(ValueError):
        interpolate_continuous_zero_rate((node,), 1.0)


# --- 11. Invalid node zero_rate rejected --------------------------------------


@pytest.mark.parametrize("zero_rate", [float("nan"), float("inf"), float("-inf")])
def test_invalid_node_zero_rate_raises_value_error(zero_rate):
    node = BLIContinuousZeroCurveNode(
        year_fraction=2.0,
        zero_rate=zero_rate,
        source_tenor="2Y",
        source_curve_id="TEST_CURVE",
    )
    with pytest.raises(ValueError):
        interpolate_continuous_zero_rate((node,), 1.0)


# --- 12. Duplicate node year_fraction rejected --------------------------------


def test_duplicate_node_year_fraction_raises_value_error():
    node_a = BLIContinuousZeroCurveNode(
        year_fraction=1.0, zero_rate=0.01, source_tenor="12M", source_curve_id="CURVE_A"
    )
    node_b = BLIContinuousZeroCurveNode(
        year_fraction=1.0, zero_rate=0.02, source_tenor="1Y", source_curve_id="CURVE_A"
    )
    with pytest.raises(ValueError):
        interpolate_continuous_zero_rate((node_a, node_b), 1.0)


# --- 13. Signed zero rate preserved -------------------------------------------


def test_signed_zero_rate_preserved():
    node_a = BLIContinuousZeroCurveNode(
        year_fraction=1.0, zero_rate=-0.001, source_tenor="1Y", source_curve_id="CURVE_A"
    )
    node_b = BLIContinuousZeroCurveNode(
        year_fraction=2.0, zero_rate=0.001, source_tenor="2Y", source_curve_id="CURVE_A"
    )
    nodes = (node_a, node_b)
    assert interpolate_continuous_zero_rate(nodes, 1.0) == pytest.approx(-0.001)
    assert interpolate_continuous_zero_rate(nodes, 1.5) == pytest.approx(0.0)


# --- 14. Boundary / module tests ----------------------------------------------


def test_module_imports_continuous_zero_curve_node():
    assert hasattr(bli_zero_rate_interpolation_module, "BLIContinuousZeroCurveNode")


def test_module_does_not_import_upstream_bli_helpers_or_types():
    import_lines = [
        line
        for line in inspect.getsource(bli_zero_rate_interpolation_module).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    forbidden_substrings = (
        "BLICurvePoint",
        "BLICurveRateBasis",
        "build_continuous_zero_curve_nodes",
        "select_curve_points_by_purpose",
        "tenor_to_year_fraction",
        "pricing.curve",
        "RateCurve",
        "bli_mvp_input_bundle",
        "BLIMVPInputBundle",
        "bli_pricing_engine",
        "price_bli_mvp",
    )
    for line in import_lines:
        for forbidden in forbidden_substrings:
            assert forbidden not in line

    module_names = set(dir(bli_zero_rate_interpolation_module))
    for forbidden in forbidden_substrings:
        assert forbidden not in module_names


def test_module_defines_no_discount_factor_or_pricing_logic():
    module_names = set(dir(bli_zero_rate_interpolation_module))
    forbidden_names = {
        "discount_factor",
        "forward_price",
        "forward_clean_price",
        "compute_pv",
        "black76",
        "black_76",
        "convert_par_to_zero",
        "QuantLib",
    }
    assert module_names.isdisjoint(forbidden_names)
