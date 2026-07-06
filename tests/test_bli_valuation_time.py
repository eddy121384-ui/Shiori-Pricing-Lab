"""Tests for `pricing/bli_valuation_time.py` (docs/26 implementation slice).

Covers Annex A §A.2.2/§A.2.4's ACT/365F time-to-expiry definition and its
"T > 0, else blocked" rule, plus the module-boundary checks docs/26 §7
requires so this slice cannot silently grow curve/discount/forward-price/
yield-conversion/volatility/QuantLib-shaped logic.
"""

from __future__ import annotations

import inspect

import pytest

from shiori_pricing_lab.data.bli_mvp_input_bundle_fixtures import SYNTHETIC_BLI_MVP_INPUT_BUNDLE
from shiori_pricing_lab.pricing import bli_valuation_time as bli_valuation_time_module
from shiori_pricing_lab.pricing.bli_valuation_time import (
    year_fraction_to_bond_option_expiry,
    year_fraction_to_expiry,
)

# --- 1. ACT/365F happy path --------------------------------------------------


def test_one_day_future_expiry_returns_one_over_365():
    assert year_fraction_to_expiry("2026-07-06", "2026-07-07") == pytest.approx(1 / 365.0)


def test_one_year_future_expiry_with_365_day_span_returns_one():
    # 2026-07-06 -> 2027-07-06 is a 365-day span (no leap day in between).
    assert year_fraction_to_expiry("2026-07-06", "2027-07-06") == pytest.approx(1.0)


def test_leap_year_spanning_pair_still_uses_days_over_365():
    # 2027-07-06 -> 2028-07-06 spans the 2028-02-29 leap day, so the span is
    # 366 days -- ACT/365F still divides by 365.0, not 366.0.
    days = (
        __import__("datetime").date(2028, 7, 6) - __import__("datetime").date(2027, 7, 6)
    ).days
    assert days == 366
    assert year_fraction_to_expiry("2027-07-06", "2028-07-06") == pytest.approx(366 / 365.0)


# --- 2. Blocking rule (Annex A §A.2.4: T > 0, else blocked) -----------------


def test_same_day_expiry_raises_value_error():
    with pytest.raises(ValueError):
        year_fraction_to_expiry("2026-07-06", "2026-07-06")


def test_expiry_before_valuation_date_raises_value_error():
    with pytest.raises(ValueError):
        year_fraction_to_expiry("2026-07-06", "2026-07-05")


# --- 3. Malformed input -------------------------------------------------


@pytest.mark.parametrize(
    "valuation_date,expiry_date",
    [
        ("2026/07/06", "2026-07-07"),
        ("2026-07-06", "07-07-2026"),
        ("not-a-date", "2026-07-07"),
        ("2026-07-06", "not-a-date"),
        ("2026-13-01", "2026-07-07"),
    ],
)
def test_malformed_date_strings_raise(valuation_date, expiry_date):
    with pytest.raises(ValueError):
        year_fraction_to_expiry(valuation_date, expiry_date)


# --- 4. No system-date use ---------------------------------------------


def test_module_source_does_not_use_system_date():
    source = inspect.getsource(bli_valuation_time_module)
    assert "date.today(" not in source
    assert "datetime.now(" not in source


# --- 5. Module boundary: no curve/discount/forward-price/vol logic ----------


def test_module_defines_no_curve_or_pricing_logic():
    module_names = set(dir(bli_valuation_time_module))
    forbidden_names = {
        "compute_pv",
        "interpolate",
        "interpolate_curve",
        "discount_factor",
        "forward_price",
        "forward_clean_price",
        "convert_yield_to_price",
        "convert_price_to_yield",
        "black76",
        "black_76",
        "volatility",
        "generate_cashflows",
        "build_schedule",
        "QuantLib",
    }
    assert module_names.isdisjoint(forbidden_names)


# --- 6. Bundle convenience function --------------------------------------


def test_year_fraction_to_bond_option_expiry_matches_direct_call():
    bundle = SYNTHETIC_BLI_MVP_INPUT_BUNDLE
    expected = year_fraction_to_expiry(
        bundle.valuation_date, bundle.product.bond_option.expiry_date
    )
    assert year_fraction_to_bond_option_expiry(bundle) == pytest.approx(expected)
    assert expected > 0.0


def test_year_fraction_to_bond_option_expiry_does_not_mutate_bundle():
    bundle = SYNTHETIC_BLI_MVP_INPUT_BUNDLE
    valuation_date_before = bundle.valuation_date
    expiry_date_before = bundle.product.bond_option.expiry_date
    year_fraction_to_bond_option_expiry(bundle)
    assert bundle.valuation_date == valuation_date_before
    assert bundle.product.bond_option.expiry_date == expiry_date_before
