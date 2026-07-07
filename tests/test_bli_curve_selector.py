"""Tests for `pricing/bli_curve_selector.py` (docs/27 implementation slice).

Covers the structural `(currency, curve_purpose)` filter decided in
`docs/27_bli_curve_interpolation_preflight.md` §4/§9.2, plus the
module-boundary checks that slice requires so this selector cannot
silently grow tenor-parsing/interpolation/discount-factor/zero-rate-
interpretation logic.
"""

from __future__ import annotations

import inspect

import pytest

from shiori_pricing_lab.data.bli_snapshot import BLICurvePoint, BLICurvePurpose, BLIMarketDataStatus
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.pricing import bli_curve_selector as bli_curve_selector_module
from shiori_pricing_lab.pricing.bli_curve_selector import select_curve_points_by_purpose
from shiori_pricing_lab.products.enums import Currency

_CURVE_POINTS = SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.curve_points

# --- 1. Happy paths using the existing fixture --------------------------


def test_selects_bond_reference_curve_points_in_order():
    selected = select_curve_points_by_purpose(
        _CURVE_POINTS,
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.BOND_REFERENCE_CURVE,
    )
    assert isinstance(selected, tuple)
    assert [point.tenor for point in selected] == [
        point.tenor
        for point in _CURVE_POINTS
        if point.curve_purpose is BLICurvePurpose.BOND_REFERENCE_CURVE
    ]
    assert selected
    for point in selected:
        assert point.currency is Currency.USD
        assert point.curve_purpose is BLICurvePurpose.BOND_REFERENCE_CURVE


def test_selects_option_discount_curve_points_in_order():
    selected = select_curve_points_by_purpose(
        _CURVE_POINTS,
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
    )
    assert isinstance(selected, tuple)
    assert [point.tenor for point in selected] == [
        point.tenor
        for point in _CURVE_POINTS
        if point.curve_purpose is BLICurvePurpose.OPTION_DISCOUNT_CURVE
    ]
    for point in selected:
        assert point.currency is Currency.USD
        assert point.curve_purpose is BLICurvePurpose.OPTION_DISCOUNT_CURVE


def test_selects_deposit_curve_point():
    selected = select_curve_points_by_purpose(
        _CURVE_POINTS,
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.DEPOSIT_CURVE,
    )
    assert isinstance(selected, tuple)
    assert len(selected) == 1
    assert selected[0].currency is Currency.USD
    assert selected[0].curve_purpose is BLICurvePurpose.DEPOSIT_CURVE


def test_input_order_is_preserved_even_when_reversed():
    reversed_points = tuple(reversed(_CURVE_POINTS))
    selected = select_curve_points_by_purpose(
        reversed_points,
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.BOND_REFERENCE_CURVE,
    )
    expected_order = [
        point.tenor
        for point in reversed_points
        if point.curve_purpose is BLICurvePurpose.BOND_REFERENCE_CURVE
    ]
    assert [point.tenor for point in selected] == expected_order


def test_accepts_string_currency_and_purpose_via_enum_coercion():
    selected = select_curve_points_by_purpose(
        _CURVE_POINTS,
        currency="USD",
        curve_purpose="DEPOSIT_CURVE",
    )
    assert selected[0].currency is Currency.USD


# --- 2. Missing selector cases -------------------------------------------


def test_absent_curve_purpose_raises_value_error_with_details():
    with pytest.raises(ValueError, match="FUNDING_CURVE"):
        select_curve_points_by_purpose(
            _CURVE_POINTS,
            currency=Currency.USD,
            curve_purpose=BLICurvePurpose.FUNDING_CURVE,
        )


def test_absent_currency_raises_value_error_with_details():
    with pytest.raises(ValueError, match="EUR"):
        select_curve_points_by_purpose(
            _CURVE_POINTS,
            currency=Currency.EUR,
            curve_purpose=BLICurvePurpose.DEPOSIT_CURVE,
        )


# --- 3. Type / boundary cases ---------------------------------------------


def test_empty_curve_points_raises_value_error_with_details():
    with pytest.raises(ValueError, match="USD") as exc_info:
        select_curve_points_by_purpose(
            (), currency=Currency.USD, curve_purpose=BLICurvePurpose.DEPOSIT_CURVE
        )
    assert "DEPOSIT_CURVE" in str(exc_info.value)


def test_non_curve_point_entry_raises_type_error():
    with pytest.raises(TypeError):
        select_curve_points_by_purpose(
            ("not-a-curve-point",),
            currency=Currency.USD,
            curve_purpose=BLICurvePurpose.DEPOSIT_CURVE,
        )


def test_invalid_currency_string_raises_value_error():
    with pytest.raises(ValueError):
        select_curve_points_by_purpose(
            _CURVE_POINTS, currency="NOT_A_CURRENCY", curve_purpose=BLICurvePurpose.DEPOSIT_CURVE
        )


def test_invalid_curve_purpose_string_raises_value_error():
    with pytest.raises(ValueError):
        select_curve_points_by_purpose(
            _CURVE_POINTS, currency=Currency.USD, curve_purpose="NOT_A_PURPOSE"
        )


# --- 4. Hard module boundary tests -----------------------------------------


def test_module_does_not_import_bli_curve_tenor():
    module_names = set(dir(bli_curve_selector_module))
    assert "tenor_to_year_fraction" not in module_names
    assert not hasattr(bli_curve_selector_module, "bli_curve_tenor")
    import_lines = [
        line
        for line in inspect.getsource(bli_curve_selector_module).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert not any("bli_curve_tenor" in line for line in import_lines)


def test_module_does_not_read_point_rate():
    source = inspect.getsource(bli_curve_selector_module)
    assert ".rate" not in source


def test_module_defines_no_interpolation_or_pricing_logic():
    module_names = set(dir(bli_curve_selector_module))
    forbidden_names = {
        "interpolate",
        "discount_factor",
        "zero_rate",
        "par_rate",
        "forward_price",
        "forward_clean_price",
        "black76",
        "black_76",
        "compute_pv",
        "QuantLib",
    }
    assert module_names.isdisjoint(forbidden_names)


def test_module_source_does_not_use_system_date():
    source = inspect.getsource(bli_curve_selector_module)
    assert "date.today(" not in source
    assert "datetime.now(" not in source
    assert "import datetime" not in source
    assert "from datetime" not in source


def test_selector_does_not_sort_by_tenor():
    unsorted_points = (
        BLICurvePoint(
            curve_id="TEST_CURVE",
            curve_name="Test Curve",
            currency=Currency.USD,
            curve_purpose=BLICurvePurpose.FUNDING_CURVE,
            tenor="5Y",
            rate=0.01,
            source_system="test",
            status=BLIMarketDataStatus.ACTIVE,
        ),
        BLICurvePoint(
            curve_id="TEST_CURVE",
            curve_name="Test Curve",
            currency=Currency.USD,
            curve_purpose=BLICurvePurpose.FUNDING_CURVE,
            tenor="1Y",
            rate=0.02,
            source_system="test",
            status=BLIMarketDataStatus.ACTIVE,
        ),
    )
    selected = select_curve_points_by_purpose(
        unsorted_points, currency=Currency.USD, curve_purpose=BLICurvePurpose.FUNDING_CURVE
    )
    assert [point.tenor for point in selected] == ["5Y", "1Y"]
