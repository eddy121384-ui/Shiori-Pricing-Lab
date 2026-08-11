"""Tests for `pricing/bli_zero_curve_nodes.py`.

Covers the continuous zero-rate curve node preparation helper: the
rate-basis gate, tenor parsing via the existing BLI tenor parser,
input/duplicate validation, sorting, and the module-boundary checks
confirming this slice stays a pure calculation-preparation step, not
interpolation, a discount factor, or a wired pricing path.
"""

from __future__ import annotations

import inspect

import pytest

from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIMarketDataStatus,
)
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.pricing import bli_zero_curve_nodes as bli_zero_curve_nodes_module
from shiori_pricing_lab.pricing.bli_curve_selector import select_curve_points_by_purpose
from shiori_pricing_lab.pricing.bli_zero_curve_nodes import (
    BLIContinuousZeroCurveNode,
    build_continuous_zero_curve_nodes,
)
from shiori_pricing_lab.products.enums import Currency

_CURVE_POINTS = SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.curve_points


def _point(**overrides) -> BLICurvePoint:
    params = dict(
        curve_id="TEST_CURVE",
        curve_name="Test Curve",
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.FUNDING_CURVE,
        tenor="2Y",
        rate=0.03,
        rate_basis=BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
        source_system="test",
        status=BLIMarketDataStatus.ACTIVE,
    )
    params.update(overrides)
    return BLICurvePoint(**params)


# --- 1. Happy path from synthetic fixture -----------------------------------


def test_option_discount_curve_nodes_from_fixture():
    selected = select_curve_points_by_purpose(
        _CURVE_POINTS,
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
    )
    nodes = build_continuous_zero_curve_nodes(selected)

    assert isinstance(nodes, tuple)
    assert all(isinstance(node, BLIContinuousZeroCurveNode) for node in nodes)
    assert [(node.year_fraction, node.zero_rate) for node in nodes] == [
        (2.0, 0.0341),
        (5.0, 0.0353),
    ]


# --- 2. Sorting --------------------------------------------------------------


def test_nodes_are_sorted_regardless_of_input_order():
    selected = select_curve_points_by_purpose(
        _CURVE_POINTS,
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
    )
    nodes = build_continuous_zero_curve_nodes(tuple(reversed(selected)))
    assert [node.year_fraction for node in nodes] == sorted(node.year_fraction for node in nodes)
    assert [(node.year_fraction, node.zero_rate) for node in nodes] == [
        (2.0, 0.0341),
        (5.0, 0.0353),
    ]


# --- 3. Deposit single-node curve --------------------------------------------


def test_deposit_curve_single_node_no_interpolation():
    selected = select_curve_points_by_purpose(
        _CURVE_POINTS,
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.DEPOSIT_CURVE,
    )
    nodes = build_continuous_zero_curve_nodes(selected)
    assert len(nodes) == 1
    assert nodes[0].year_fraction == pytest.approx(0.25)
    assert nodes[0].zero_rate == pytest.approx(0.0350)


# --- 4. Empty input rejected --------------------------------------------------


def test_empty_input_raises_value_error():
    with pytest.raises(ValueError):
        build_continuous_zero_curve_nodes(())


# --- 5. Non-BLICurvePoint rejected --------------------------------------------


def test_non_curve_point_element_raises_type_error():
    with pytest.raises(TypeError):
        build_continuous_zero_curve_nodes((_point(), object()))


# --- 6. Non-continuous rate basis rejected -----------------------------------


def test_par_rate_basis_raises_value_error_naming_both_bases():
    par_point = _point(rate_basis=BLICurveRateBasis.PAR_RATE)
    with pytest.raises(ValueError, match="PAR_RATE") as exc_info:
        build_continuous_zero_curve_nodes((par_point,))
    assert "CONTINUOUS_ZERO_RATE" in str(exc_info.value)


@pytest.mark.parametrize(
    "basis",
    [
        BLICurveRateBasis.SIMPLE_ZERO_RATE,
        BLICurveRateBasis.SWAP_RATE,
        BLICurveRateBasis.BOND_YIELD,
        BLICurveRateBasis.FUNDING_RATE,
        BLICurveRateBasis.OTHER,
    ],
)
def test_every_non_continuous_rate_basis_raises(basis):
    with pytest.raises(ValueError):
        build_continuous_zero_curve_nodes((_point(rate_basis=basis),))


# --- 7. Raw string-coerced continuous basis works ----------------------------


def test_raw_string_continuous_zero_rate_basis_works():
    point = _point(rate_basis="CONTINUOUS_ZERO_RATE")
    nodes = build_continuous_zero_curve_nodes((point,))
    assert len(nodes) == 1
    assert nodes[0].zero_rate == pytest.approx(0.03)


# --- 8. Duplicate parsed year fraction rejected ------------------------------


def test_duplicate_parsed_year_fraction_raises():
    point_12m = _point(curve_id="CURVE_A", tenor="12M", rate=0.01)
    point_1y = _point(curve_id="CURVE_A", tenor="1Y", rate=0.02)
    with pytest.raises(ValueError, match="1.0"):
        build_continuous_zero_curve_nodes((point_12m, point_1y))


# --- 9. Signed rate preserved -------------------------------------------------


def test_signed_negative_rate_is_preserved():
    point = _point(rate=-0.001)
    nodes = build_continuous_zero_curve_nodes((point,))
    assert nodes[0].zero_rate == -0.001


# --- 10. Boundary tests --------------------------------------------------


def test_module_imports_tenor_to_year_fraction():
    assert hasattr(bli_zero_curve_nodes_module, "tenor_to_year_fraction")


def test_module_does_not_import_generic_curve_helpers():
    import_lines = [
        line
        for line in inspect.getsource(bli_zero_curve_nodes_module).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert not any("pricing.curve" in line for line in import_lines)
    assert not any(
        line.strip() == "from shiori_pricing_lab.pricing import curve" for line in import_lines
    )
    module_names = set(dir(bli_zero_curve_nodes_module))
    assert "RateCurve" not in module_names
    assert "tenor_to_years" not in module_names


def test_module_does_not_import_bundle_or_pricing_engine():
    import_lines = [
        line
        for line in inspect.getsource(bli_zero_curve_nodes_module).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert not any("bli_mvp_input_bundle" in line for line in import_lines)
    assert not any("bli_pricing_engine" in line for line in import_lines)
    module_names = set(dir(bli_zero_curve_nodes_module))
    assert "BLIMVPInputBundle" not in module_names
    assert "price_bli_mvp" not in module_names


# --- 11. Explicit maturity_date node coordinates (Issue #165 P1 follow-up) ----


def test_explicit_date_node_uses_the_actual_date_not_the_nominal_tenor_fraction():
    # tenor="2Y" would normally parse to exactly 2.0 (tenor_to_year_fraction);
    # an explicit maturity_date 731 days after as_of_date (2028-08-01 from
    # 2026-08-01) is a leap-spanning ACT/365F fraction close to, but not
    # exactly, 2.0 -- proving the tenor label is never consulted once
    # maturity_date is present.
    point = _point(tenor="2Y", maturity_date="2028-08-01")
    nodes = build_continuous_zero_curve_nodes((point,), as_of_date="2026-08-01")

    assert nodes[0].year_fraction != 2.0
    assert nodes[0].year_fraction == pytest.approx(731 / 365.0)
    assert nodes[0].source_maturity_date == "2028-08-01"


def test_legacy_tenor_only_node_is_unaffected_by_as_of_date_being_supplied():
    point = _point(tenor="2Y")  # maturity_date defaults to None

    without_as_of_date = build_continuous_zero_curve_nodes((point,))
    with_unused_as_of_date = build_continuous_zero_curve_nodes((point,), as_of_date="2026-08-01")

    assert without_as_of_date[0].year_fraction == 2.0
    assert with_unused_as_of_date[0].year_fraction == 2.0
    assert without_as_of_date[0].source_maturity_date is None
    assert with_unused_as_of_date[0].source_maturity_date is None


def test_mixed_curve_only_the_explicit_date_row_uses_a_date_coordinate():
    tenor_only = _point(curve_id="MIXED", tenor="1Y", rate=0.02)
    explicit_date = _point(curve_id="MIXED", tenor="5Y", rate=0.03, maturity_date="2031-08-01")

    nodes = build_continuous_zero_curve_nodes((tenor_only, explicit_date), as_of_date="2026-08-01")

    by_tenor = {node.source_tenor: node for node in nodes}
    assert by_tenor["1Y"].year_fraction == 1.0
    assert by_tenor["1Y"].source_maturity_date is None
    assert by_tenor["5Y"].year_fraction == pytest.approx((365 * 5 + 1) / 365.0)
    assert by_tenor["5Y"].source_maturity_date == "2031-08-01"


def test_explicit_maturity_date_without_as_of_date_fails_closed():
    point = _point(tenor="2Y", maturity_date="2028-08-01")

    with pytest.raises(ValueError, match="no as_of_date was supplied") as exc_info:
        build_continuous_zero_curve_nodes((point,))

    message = str(exc_info.value)
    assert "TEST_CURVE" in message
    assert "2Y" in message
    assert "2028-08-01" in message


def test_explicit_maturity_date_never_silently_falls_back_to_tenor():
    # Confirms the fail-closed path never quietly computes a
    # tenor-derived year_fraction as a fallback before raising.
    point = _point(tenor="2Y", maturity_date="2028-08-01")

    with pytest.raises(ValueError):
        build_continuous_zero_curve_nodes((point,))


def test_duplicate_explicit_dates_across_curve_points_fail_closed():
    point_a = _point(curve_id="CURVE_A", tenor="2Y", maturity_date="2028-08-01")
    point_b = _point(curve_id="CURVE_A", tenor="3Y", maturity_date="2028-08-01")

    with pytest.raises(ValueError, match="duplicate parsed year_fraction"):
        build_continuous_zero_curve_nodes((point_a, point_b), as_of_date="2026-08-01")


def test_maturity_date_on_or_before_as_of_date_fails_closed():
    point = _point(tenor="1Y", maturity_date="2026-08-01")

    with pytest.raises(ValueError, match="strictly after"):
        build_continuous_zero_curve_nodes((point,), as_of_date="2026-08-01")


def test_maturity_date_before_as_of_date_fails_closed():
    point = _point(tenor="1Y", maturity_date="2025-08-01")

    with pytest.raises(ValueError, match="strictly after"):
        build_continuous_zero_curve_nodes((point,), as_of_date="2026-08-01")


def test_as_of_date_is_never_required_when_no_point_has_a_maturity_date():
    # Every curve this codebase has used until now is exactly this case --
    # confirms nothing about the new parameter forces it on legacy callers.
    nodes = build_continuous_zero_curve_nodes((_point(), _point(tenor="5Y")))
    assert len(nodes) == 2


def test_module_defines_no_interpolation_or_pv_shaped_names():
    module_names = set(dir(bli_zero_curve_nodes_module))
    forbidden_names = {
        "interpolate",
        "interpolate_curve",
        "discount_factor",
        "forward_price",
        "forward_clean_price",
        "compute_pv",
        "black76",
        "black_76",
        "select_curve_points_by_purpose",
    }
    assert module_names.isdisjoint(forbidden_names)
