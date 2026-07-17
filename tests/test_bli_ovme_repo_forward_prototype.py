"""Tests for `pricing/bli_ovme_repo_forward_prototype.py` (Issue #94 diagnostic).

**RED PROTOTYPE, not production.** These tests prove the isolated diagnostic
formula and its explicit boundaries only -- they do not validate, wire, or
authorize any production pricing path. No real Bloomberg screenshot, export,
or market value is committed here; every input is either a plain synthetic
number or the already-reviewed synthetic bond fixture reused as-is from
`reference_data.fixtures` / `test_bli_forward_clean_price.py`.
"""

from __future__ import annotations

from datetime import date

import pytest

from shiori_pricing_lab.pricing import bli_ovme_repo_forward_prototype as module
from shiori_pricing_lab.pricing.bli_forward_clean_price import (
    _forward_clean_price_from_components,
)
from shiori_pricing_lab.pricing.bli_ovme_repo_forward_prototype import (
    ovme_flat_repo_forward_clean_price_no_coupon_per_100,
)
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import (
    accrued_interest_per_100,
    is_quantlib_available,
)
from shiori_pricing_lab.reference_data.fixtures import SYNTHETIC_BOND_FIXTURES

_QUANTLIB_AVAILABLE = is_quantlib_available()
_requires_quantlib = pytest.mark.skipif(
    not _QUANTLIB_AVAILABLE, reason="QuantLib is not installed in this environment"
)

# Same eligible FIXED_COUPON_BULLET bond (XS0000000001) already reviewed and
# reused as-is by test_bli_forward_clean_price.py -- not duplicated here.
_ELIGIBLE_BOND = SYNTHETIC_BOND_FIXTURES[0]

# Same no-coupon window already proven coupon-free in
# test_bli_forward_clean_price.py::test_zero_coupon_before_expiry_matches_recomputed_annex_a_formula
# (next coupon for this bond is 2026-12-15).
_SPOT_SETTLEMENT_DATE = "2026-07-01"
_FORWARD_SETTLEMENT_DATE = "2026-09-29"
_SPOT_CLEAN_PRICE = 99.46484375
_REPO_RATE = 0.04725


# --- 1. Matches an independently computed ACT/365 semi-annual repo formula ---


@_requires_quantlib
def test_matches_independently_computed_act_365_semiannual_repo_formula():
    actual_days = (
        date.fromisoformat(_FORWARD_SETTLEMENT_DATE) - date.fromisoformat(_SPOT_SETTLEMENT_DATE)
    ).days
    t = actual_days / 365.0
    expected_discount_factor_repo = (1.0 + _REPO_RATE / 2.0) ** (-2.0 * t)

    ai_spot = accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date=_SPOT_SETTLEMENT_DATE)
    ai_forward = accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date=_FORWARD_SETTLEMENT_DATE)
    expected = _forward_clean_price_from_components(
        spot_clean_price=_SPOT_CLEAN_PRICE,
        accrued_interest_at_valuation=ai_spot,
        coupon_pv=0.0,
        expiry_discount_factor=expected_discount_factor_repo,
        accrued_interest_at_expiry=ai_forward,
    )

    result = ovme_flat_repo_forward_clean_price_no_coupon_per_100(
        bond=_ELIGIBLE_BOND,
        spot_clean_price=_SPOT_CLEAN_PRICE,
        spot_settlement_date=_SPOT_SETTLEMENT_DATE,
        forward_settlement_date=_FORWARD_SETTLEMENT_DATE,
        repo_rate=_REPO_RATE,
    )

    assert result == pytest.approx(expected)


# --- 2. Higher repo rate produces a higher forward clean price --------------


@_requires_quantlib
def test_higher_repo_rate_produces_higher_forward_clean_price():
    lower = ovme_flat_repo_forward_clean_price_no_coupon_per_100(
        bond=_ELIGIBLE_BOND,
        spot_clean_price=_SPOT_CLEAN_PRICE,
        spot_settlement_date=_SPOT_SETTLEMENT_DATE,
        forward_settlement_date=_FORWARD_SETTLEMENT_DATE,
        repo_rate=0.02,
    )
    higher = ovme_flat_repo_forward_clean_price_no_coupon_per_100(
        bond=_ELIGIBLE_BOND,
        spot_clean_price=_SPOT_CLEAN_PRICE,
        spot_settlement_date=_SPOT_SETTLEMENT_DATE,
        forward_settlement_date=_FORWARD_SETTLEMENT_DATE,
        repo_rate=0.08,
    )
    # Dividing the same spot-dirty numerator by a smaller DF_repo (higher
    # repo_rate -> smaller DF_repo) yields a larger forward_dirty, and hence
    # a larger forward_clean, all else equal.
    assert higher > lower


# --- 3. Intervening coupon is rejected explicitly ----------------------------


@_requires_quantlib
def test_intervening_coupon_is_rejected_explicitly():
    # Window pushed past the 2026-12-15 coupon -- same window already proven
    # to contain exactly that one coupon in test_bli_forward_clean_price.py.
    with pytest.raises(ValueError, match="does not support intervening coupons"):
        ovme_flat_repo_forward_clean_price_no_coupon_per_100(
            bond=_ELIGIBLE_BOND,
            spot_clean_price=_SPOT_CLEAN_PRICE,
            spot_settlement_date=_SPOT_SETTLEMENT_DATE,
            forward_settlement_date="2027-01-20",
            repo_rate=_REPO_RATE,
        )


# --- 4. Invalid or reversed dates fail ---------------------------------------


@_requires_quantlib
def test_reversed_dates_fail():
    with pytest.raises(ValueError, match="must be strictly after"):
        ovme_flat_repo_forward_clean_price_no_coupon_per_100(
            bond=_ELIGIBLE_BOND,
            spot_clean_price=_SPOT_CLEAN_PRICE,
            spot_settlement_date=_FORWARD_SETTLEMENT_DATE,
            forward_settlement_date=_SPOT_SETTLEMENT_DATE,
            repo_rate=_REPO_RATE,
        )


@_requires_quantlib
def test_equal_dates_fail():
    with pytest.raises(ValueError, match="must be strictly after"):
        ovme_flat_repo_forward_clean_price_no_coupon_per_100(
            bond=_ELIGIBLE_BOND,
            spot_clean_price=_SPOT_CLEAN_PRICE,
            spot_settlement_date=_SPOT_SETTLEMENT_DATE,
            forward_settlement_date=_SPOT_SETTLEMENT_DATE,
            repo_rate=_REPO_RATE,
        )


def test_malformed_date_fails():
    with pytest.raises(ValueError, match="spot_settlement_date"):
        ovme_flat_repo_forward_clean_price_no_coupon_per_100(
            bond=_ELIGIBLE_BOND,
            spot_clean_price=_SPOT_CLEAN_PRICE,
            spot_settlement_date="2026/07/01",
            forward_settlement_date=_FORWARD_SETTLEMENT_DATE,
            repo_rate=_REPO_RATE,
        )


# --- 5. Invalid/non-finite repo rate and spot price fail ---------------------


@pytest.mark.parametrize("bad_repo_rate", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_repo_rate_fails(bad_repo_rate):
    with pytest.raises(ValueError, match="repo_rate"):
        ovme_flat_repo_forward_clean_price_no_coupon_per_100(
            bond=_ELIGIBLE_BOND,
            spot_clean_price=_SPOT_CLEAN_PRICE,
            spot_settlement_date=_SPOT_SETTLEMENT_DATE,
            forward_settlement_date=_FORWARD_SETTLEMENT_DATE,
            repo_rate=bad_repo_rate,
        )


def test_repo_rate_implying_non_positive_compounding_base_fails():
    # 1 + (-2.5) / 2 = -0.25 <= 0 -- a non-integer power of a negative base
    # silently yields a complex number in Python rather than raising, so
    # this must be rejected explicitly before the formula is evaluated.
    with pytest.raises(ValueError, match="non-positive semi-annual compounding base"):
        ovme_flat_repo_forward_clean_price_no_coupon_per_100(
            bond=_ELIGIBLE_BOND,
            spot_clean_price=_SPOT_CLEAN_PRICE,
            spot_settlement_date=_SPOT_SETTLEMENT_DATE,
            forward_settlement_date=_FORWARD_SETTLEMENT_DATE,
            repo_rate=-2.5,
        )


@pytest.mark.parametrize("bad_spot_price", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_spot_clean_price_fails(bad_spot_price):
    with pytest.raises(ValueError, match="spot_clean_price"):
        ovme_flat_repo_forward_clean_price_no_coupon_per_100(
            bond=_ELIGIBLE_BOND,
            spot_clean_price=bad_spot_price,
            spot_settlement_date=_SPOT_SETTLEMENT_DATE,
            forward_settlement_date=_FORWARD_SETTLEMENT_DATE,
            repo_rate=_REPO_RATE,
        )


def test_wrong_bond_type_raises_type_error():
    with pytest.raises(TypeError, match="BondReferenceData"):
        ovme_flat_repo_forward_clean_price_no_coupon_per_100(
            bond="not-a-bond",
            spot_clean_price=_SPOT_CLEAN_PRICE,
            spot_settlement_date=_SPOT_SETTLEMENT_DATE,
            forward_settlement_date=_FORWARD_SETTLEMENT_DATE,
            repo_rate=_REPO_RATE,
        )


# --- 6. Isolation: no Black-76 / engine / request / builder / UI import -----


def test_module_does_not_import_forbidden_modules():
    import sys

    forbidden_module_names = {
        "shiori_pricing_lab.pricing.bli_pricing_engine",
        "shiori_pricing_lab.pricing.bli_black76_price_option",
        "shiori_pricing_lab.data.bli_standalone_option_request",
        "shiori_pricing_lab.data.bli_standalone_option_request_builder",
        "shiori_pricing_lab.app.standalone_option_workbench",
        "shiori_pricing_lab.app.standalone_option_ui",
        "shiori_pricing_lab.app.streamlit_app",
    }
    module_globals = set(vars(module))
    for forbidden in forbidden_module_names:
        short_name = forbidden.rsplit(".", 1)[-1]
        assert short_name not in module_globals
    assert module.__name__ in sys.modules  # sanity: module actually loaded


def test_module_defines_no_black76_or_engine_wiring_names():
    module_names = set(dir(module))
    forbidden_names = {
        "black76_price_option_pv_per_100",
        "price_bli_mvp",
        "PricingResult",
        "register_engine",
        "PricingEngineRegistry",
    }
    assert module_names.isdisjoint(forbidden_names)


def test_module_reads_no_system_clock():
    import inspect

    source = inspect.getsource(module)
    assert "date.today()" not in source
    assert "datetime.now()" not in source
    assert "utcnow()" not in source


# --- 7. No existing module imports this prototype ---------------------------


def test_no_existing_module_imports_this_prototype():
    import pathlib

    repo_src = pathlib.Path(__file__).resolve().parent.parent / "src"
    this_file = pathlib.Path(module.__file__).resolve()
    offending = []
    for path in repo_src.rglob("*.py"):
        if path.resolve() == this_file:
            continue
        text = path.read_text(encoding="utf-8")
        if "bli_ovme_repo_forward_prototype" in text:
            offending.append(str(path))
    assert offending == []
