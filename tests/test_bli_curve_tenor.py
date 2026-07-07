"""Tests for `pricing/bli_curve_tenor.py` (docs/27 implementation slice).

Covers the strict `<int>M`/`<int>Y` tenor-label contract decided in
`docs/27_bli_curve_interpolation_preflight.md` §5/§9, plus the
module-boundary checks §9's Codex review checklist requires so this
slice cannot silently grow curve-selection/interpolation/discount-
factor/zero-rate-interpretation logic.
"""

from __future__ import annotations

import inspect

import pytest

from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.pricing import bli_curve_tenor as bli_curve_tenor_module
from shiori_pricing_lab.pricing.bli_curve_tenor import tenor_to_year_fraction

# --- 1. Happy path -----------------------------------------------------


@pytest.mark.parametrize(
    "tenor,expected",
    [
        ("1M", 1 / 12.0),
        ("3M", 0.25),
        ("6M", 0.5),
        ("1Y", 1.0),
        ("2Y", 2.0),
        ("5Y", 5.0),
        ("10Y", 10.0),
    ],
)
def test_valid_tenor_labels(tenor, expected):
    assert tenor_to_year_fraction(tenor) == pytest.approx(expected)


# --- 2. Existing BLI fixture coverage -----------------------------------


def test_covers_every_tenor_label_in_existing_bli_fixture():
    tenors = {point.tenor for point in SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.curve_points}
    assert tenors == {"3M", "2Y", "5Y"}
    for tenor in tenors:
        assert tenor_to_year_fraction(tenor) > 0.0


# --- 3. Rejection cases --------------------------------------------------


@pytest.mark.parametrize(
    "tenor",
    [
        "1W",
        "2W",
        "3W",
        "O/N",
        "24",
        "2 years",
        "0M",
        "0Y",
        "-1M",
        "-1Y",
        " 3M",
        "3M ",
        "3m",
        "2y",
        "",
        None,
    ],
)
def test_unsupported_or_malformed_tenor_raises(tenor):
    with pytest.raises(ValueError):
        tenor_to_year_fraction(tenor)


def test_error_message_includes_offending_tenor():
    with pytest.raises(ValueError, match="2 years"):
        tenor_to_year_fraction("2 years")


# --- 4. Module boundary --------------------------------------------------


def test_module_source_does_not_use_system_date():
    source = inspect.getsource(bli_curve_tenor_module)
    assert "date.today(" not in source
    assert "datetime.now(" not in source
    assert "import datetime" not in source
    assert "from datetime" not in source


def test_module_does_not_import_bli_data_or_product_types():
    module_names = set(dir(bli_curve_tenor_module))
    forbidden_names = {
        "BLICurvePoint",
        "BLIMarketDataSnapshot",
        "BLIMVPInputBundle",
    }
    assert module_names.isdisjoint(forbidden_names)


def test_module_defines_no_curve_selection_or_pricing_logic():
    module_names = set(dir(bli_curve_tenor_module))
    forbidden_names = {
        "select_curve_points",
        "interpolate",
        "interpolate_curve",
        "discount_factor",
        "forward_price",
        "forward_clean_price",
        "zero_rate",
        "par_rate",
        "convert_yield_to_price",
        "convert_price_to_yield",
        "black76",
        "black_76",
        "compute_pv",
        "QuantLib",
    }
    assert module_names.isdisjoint(forbidden_names)
