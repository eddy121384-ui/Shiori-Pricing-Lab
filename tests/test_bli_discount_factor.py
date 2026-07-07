"""Tests for `pricing/bli_discount_factor.py`.

Covers the deterministic `exp(-zero_rate * year_fraction)` discount
factor (Annex A §A.10.2's continuous-compounding convention), its
input validation, and the module-boundary checks confirming this slice
stays a tiny, standalone formula -- no curve selection, interpolation,
forward price, PV, or pricing-engine wiring.
"""

from __future__ import annotations

import inspect
from math import exp

import pytest

from shiori_pricing_lab.pricing import bli_discount_factor as bli_discount_factor_module
from shiori_pricing_lab.pricing.bli_discount_factor import continuous_discount_factor

# --- 1-4. Deterministic cases ------------------------------------------------


def test_positive_rate_case():
    assert continuous_discount_factor(0.035, 2.0) == pytest.approx(exp(-0.035 * 2.0))


def test_zero_rate_case():
    assert continuous_discount_factor(0.0, 5.0) == pytest.approx(1.0)


def test_negative_rate_case():
    assert continuous_discount_factor(-0.001, 2.0) == pytest.approx(exp(0.002))


def test_very_short_year_fraction_case():
    assert continuous_discount_factor(0.035, 0.25) == pytest.approx(exp(-0.035 * 0.25))


# --- 5. Reject invalid zero rates --------------------------------------------


@pytest.mark.parametrize("zero_rate", [float("nan"), float("inf"), float("-inf")])
def test_invalid_zero_rate_raises_value_error(zero_rate):
    with pytest.raises(ValueError):
        continuous_discount_factor(zero_rate, 1.0)


# --- 6. Reject invalid year fractions -----------------------------------------


@pytest.mark.parametrize(
    "year_fraction", [0.0, -1.0, float("nan"), float("inf"), float("-inf")]
)
def test_invalid_year_fraction_raises_value_error(year_fraction):
    with pytest.raises(ValueError):
        continuous_discount_factor(0.035, year_fraction)


# --- 7. Boundary tests ---------------------------------------------------


def test_module_does_not_import_upstream_bli_curve_or_pricing_types():
    import_lines = [
        line
        for line in inspect.getsource(bli_discount_factor_module).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    forbidden_substrings = (
        "bli_curve_selector",
        "select_curve_points_by_purpose",
        "bli_zero_rate_interpolation",
        "interpolate_continuous_zero_rate",
        "BLIContinuousZeroCurveNode",
        "BLICurvePoint",
        "bli_mvp_input_bundle",
        "BLIMVPInputBundle",
        "bli_pricing_engine",
        "price_bli_mvp",
    )
    for line in import_lines:
        for forbidden in forbidden_substrings:
            assert forbidden not in line

    module_names = set(dir(bli_discount_factor_module))
    for forbidden in forbidden_substrings:
        assert forbidden not in module_names


def test_module_defines_no_pv_or_forward_price_or_black76_logic():
    module_names = set(dir(bli_discount_factor_module))
    forbidden_names = {
        "compute_pv",
        "forward_price",
        "forward_clean_price",
        "black76",
        "black_76",
        "interpolate",
        "select_curve_points_by_purpose",
        "QuantLib",
    }
    assert module_names.isdisjoint(forbidden_names)
