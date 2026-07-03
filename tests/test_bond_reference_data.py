"""Tests for BondReferenceData / Bond Master (docs/20 BLI MVP Slice B).

Schema-construction, validation, and MVP-eligibility tests only. No
pricing, cash-flow generation, market data, MarketDataSnapshot, or
valuation date is involved -- BondReferenceData is a pure reference-data
object, and MVP pricing eligibility is a separate, explicit check
(``reference_data.eligibility.is_mvp_pricing_eligible``), not part of
construction validation.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from shiori_pricing_lab.products.enums import (
    BondYieldConvention,
    BusinessDayConvention,
    Currency,
    DayCount,
    Frequency,
)
from shiori_pricing_lab.reference_data import (
    BondReferenceData,
    BondStatus,
    BondType,
    is_mvp_pricing_eligible,
)
from shiori_pricing_lab.reference_data.fixtures import SYNTHETIC_BOND_FIXTURES


def _bond(**overrides) -> BondReferenceData:
    """Build a synthetic, valid, MVP-pricing-eligible fixed-coupon bullet bond."""

    params = dict(
        isin="XS1234567890",
        issuer="Synthetic Reference Issuer",
        currency=Currency.USD,
        coupon=0.04,
        coupon_frequency=Frequency.SEMI_ANNUAL,
        maturity_date="2031-05-01",
        issue_date="2025-05-01",
        day_count=DayCount.ACT_ACT_ISDA,
        business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
        redemption_amount=100.0,
        callable_flag=False,
        sinkable_flag=False,
        bond_type=BondType.FIXED_COUPON_BULLET,
        yield_convention=BondYieldConvention.SEMI_ANNUAL_COMPOUND,
        ex_dividend_days=1,
        first_coupon_date="2025-11-01",
        last_coupon_date="2030-11-01",
        status=BondStatus.ACTIVE,
    )
    params.update(overrides)
    return BondReferenceData(**params)


# --- Valid construction -------------------------------------------------


def test_valid_plain_vanilla_bond_constructs():
    bond = _bond()
    assert bond.isin == "XS1234567890"
    assert bond.currency is Currency.USD
    assert bond.bond_type is BondType.FIXED_COUPON_BULLET
    assert bond.status is BondStatus.ACTIVE


def test_valid_plain_vanilla_bond_is_mvp_pricing_eligible():
    result = is_mvp_pricing_eligible(_bond())
    assert result.eligible is True
    assert result.reasons == ()


# --- isin / issuer --------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_isin_rejected(value):
    with pytest.raises(ValueError, match="isin must be a non-blank string"):
        _bond(isin=value)


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_issuer_rejected(value):
    with pytest.raises(ValueError, match="issuer must be a non-blank string"):
        _bond(issuer=value)


# --- coupon: negative rejected, zero accepted as reference data ----------


def test_negative_coupon_rejected():
    with pytest.raises(ValueError, match="coupon must be non-negative"):
        _bond(coupon=-0.01)


def test_zero_coupon_accepted_as_valid_reference_data():
    bond = _bond(coupon=0.0, first_coupon_date="2031-05-01", last_coupon_date="2031-05-01")
    assert bond.coupon == 0.0


def test_zero_coupon_is_not_mvp_pricing_eligible():
    bond = _bond(coupon=0.0, first_coupon_date="2031-05-01", last_coupon_date="2031-05-01")
    result = is_mvp_pricing_eligible(bond)
    assert result.eligible is False
    assert any("zero-coupon" in reason for reason in result.reasons)


# --- issue_date / maturity_date -------------------------------------------


def test_issue_date_not_before_maturity_date_rejected():
    with pytest.raises(ValueError, match="issue_date .* must be before maturity_date"):
        _bond(issue_date="2031-05-01", maturity_date="2031-05-01")


def test_invalid_date_format_rejected():
    with pytest.raises(ValueError, match="maturity_date must be a YYYY-MM-DD date string"):
        _bond(maturity_date="20310501")


# --- first_coupon_date / last_coupon_date: required, non-null, strict ISO -


def test_missing_first_coupon_date_rejected():
    with pytest.raises(TypeError):
        params = {
            "isin": "XS1234567890",
            "issuer": "Synthetic Reference Issuer",
            "currency": Currency.USD,
            "coupon": 0.04,
            "coupon_frequency": Frequency.SEMI_ANNUAL,
            "maturity_date": "2031-05-01",
            "issue_date": "2025-05-01",
            "day_count": DayCount.ACT_ACT_ISDA,
            "business_day_convention": BusinessDayConvention.MODIFIED_FOLLOWING,
            "redemption_amount": 100.0,
            "callable_flag": False,
            "sinkable_flag": False,
            "bond_type": BondType.FIXED_COUPON_BULLET,
            "yield_convention": BondYieldConvention.SEMI_ANNUAL_COMPOUND,
            "ex_dividend_days": 1,
            "last_coupon_date": "2030-11-01",
            "status": BondStatus.ACTIVE,
        }
        BondReferenceData(**params)


def test_none_first_coupon_date_rejected():
    with pytest.raises(ValueError, match="first_coupon_date must be a non-blank string"):
        _bond(first_coupon_date=None)


def test_none_last_coupon_date_rejected():
    with pytest.raises(ValueError, match="last_coupon_date must be a non-blank string"):
        _bond(last_coupon_date=None)


def test_invalid_first_coupon_date_format_rejected():
    with pytest.raises(ValueError, match="first_coupon_date must be a YYYY-MM-DD date string"):
        _bond(first_coupon_date="2025-W27-3")


def test_invalid_last_coupon_date_format_rejected():
    with pytest.raises(ValueError, match="last_coupon_date must be a YYYY-MM-DD date string"):
        _bond(last_coupon_date="not-a-date")


# --- redemption_amount -----------------------------------------------------


def test_non_positive_redemption_amount_rejected():
    with pytest.raises(ValueError, match="redemption_amount must be positive"):
        _bond(redemption_amount=0.0)


# --- callable_flag / sinkable_flag: real bool, not truthy ------------------


@pytest.mark.parametrize("value", [1, 0, "true", None])
def test_callable_flag_non_bool_rejected(value):
    with pytest.raises(ValueError, match="callable_flag must be a bool"):
        _bond(callable_flag=value)


@pytest.mark.parametrize("value", [1, 0, "true", None])
def test_sinkable_flag_non_bool_rejected(value):
    with pytest.raises(ValueError, match="sinkable_flag must be a bool"):
        _bond(sinkable_flag=value)


def test_callable_bond_is_valid_reference_data_but_ineligible():
    bond = _bond(callable_flag=True)
    assert bond.callable_flag is True
    result = is_mvp_pricing_eligible(bond)
    assert result.eligible is False
    assert any("callable" in reason for reason in result.reasons)


def test_sinkable_bond_is_valid_reference_data_but_ineligible():
    bond = _bond(sinkable_flag=True)
    assert bond.sinkable_flag is True
    result = is_mvp_pricing_eligible(bond)
    assert result.eligible is False
    assert any("sinkable" in reason for reason in result.reasons)


# --- ex_dividend_days: non-negative int, not bool ---------------------------


@pytest.mark.parametrize("value", [-1, True, False, 1.5, "1"])
def test_ex_dividend_days_invalid_rejected(value):
    with pytest.raises(ValueError, match="ex_dividend_days must be a non-negative integer"):
        _bond(ex_dividend_days=value)


def test_ex_dividend_days_zero_accepted():
    bond = _bond(ex_dividend_days=0)
    assert bond.ex_dividend_days == 0


# --- enum coercion -----------------------------------------------------------


def test_invalid_currency_rejected():
    with pytest.raises(ValueError, match="currency must be one of"):
        _bond(currency="NOT_A_CCY")


def test_invalid_coupon_frequency_rejected():
    with pytest.raises(ValueError, match="coupon_frequency must be one of"):
        _bond(coupon_frequency="FORTNIGHTLY")


def test_invalid_day_count_rejected():
    with pytest.raises(ValueError, match="day_count must be one of"):
        _bond(day_count="ACT_365")


def test_invalid_business_day_convention_rejected():
    with pytest.raises(ValueError, match="business_day_convention must be one of"):
        _bond(business_day_convention="NEAREST")


def test_invalid_bond_type_rejected():
    with pytest.raises(ValueError, match="bond_type must be one of"):
        _bond(bond_type="MORTGAGE_BACKED")


def test_invalid_yield_convention_rejected():
    with pytest.raises(ValueError, match="yield_convention must be one of"):
        _bond(yield_convention="CONTINUOUS_COMPOUND")


def test_invalid_status_rejected():
    with pytest.raises(ValueError, match="status must be one of"):
        _bond(status="PENDING")


def test_string_enum_values_coerce():
    bond = _bond(
        currency="USD",
        coupon_frequency="SEMI_ANNUAL",
        day_count="ACT_ACT_ISDA",
        business_day_convention="MODIFIED_FOLLOWING",
        bond_type="FIXED_COUPON_BULLET",
        yield_convention="SEMI_ANNUAL_COMPOUND",
        status="ACTIVE",
    )
    assert bond.currency is Currency.USD
    assert bond.bond_type is BondType.FIXED_COUPON_BULLET


# --- yield_convention OTHER: valid reference data, ineligible ---------------


def test_other_yield_convention_is_valid_reference_data_but_ineligible():
    bond = _bond(yield_convention=BondYieldConvention.OTHER)
    assert bond.yield_convention is BondYieldConvention.OTHER
    result = is_mvp_pricing_eligible(bond)
    assert result.eligible is False
    assert any("yield_convention OTHER" in reason for reason in result.reasons)


# --- bond_type exclusion mechanism: floating/amortizing/etc ----------------


@pytest.mark.parametrize(
    "bond_type",
    [
        BondType.FLOATING_RATE_NOTE,
        BondType.AMORTIZING,
        BondType.CONVERTIBLE,
        BondType.INFLATION_LINKED,
        BondType.PERPETUAL,
        BondType.STRUCTURED_NOTE,
    ],
)
def test_non_vanilla_bond_type_is_valid_reference_data_but_ineligible(bond_type):
    bond = _bond(bond_type=bond_type)
    assert bond.bond_type is bond_type
    result = is_mvp_pricing_eligible(bond)
    assert result.eligible is False
    assert any("not MVP-pricing-eligible" in reason for reason in result.reasons)


# --- multiple simultaneous ineligibility reasons ----------------------------


def test_multiple_ineligibility_reasons_all_reported():
    bond = _bond(callable_flag=True, coupon=0.0, first_coupon_date="2031-05-01",
                 last_coupon_date="2031-05-01")
    result = is_mvp_pricing_eligible(bond)
    assert result.eligible is False
    assert len(result.reasons) == 2


# --- Irregular first/last coupon stub handling: fixture-level, by construction


def test_fixture_bonds_have_regular_no_stub_coupon_dates_by_construction():
    """No runtime irregular-stub detection exists (docs/20 §5/§10) -- this
    repo has no schedule engine, so this slice relies on the MVP fixture
    being manually reviewed and limited to regular-coupon, no-stub bonds
    by construction instead. This test documents that construction
    decision by asserting every fixture bond's first/last coupon dates
    fall strictly between issue_date and maturity_date (a necessary, not
    sufficient, condition for "no stub", verified manually at fixture
    authoring time).
    """

    for bond in SYNTHETIC_BOND_FIXTURES:
        from datetime import date

        issue = date.fromisoformat(bond.issue_date)
        maturity = date.fromisoformat(bond.maturity_date)
        first_coupon = date.fromisoformat(bond.first_coupon_date)
        last_coupon = date.fromisoformat(bond.last_coupon_date)
        assert issue < first_coupon <= maturity
        assert issue < last_coupon <= maturity
        assert first_coupon <= last_coupon


# --- Boundary: no market-data or pricing-output field -----------------------


def test_bond_reference_data_has_no_market_data_or_pricing_fields():
    field_names = {f.name for f in fields(BondReferenceData)}
    forbidden = {
        "business_date",
        "valuation_date",
        "as_of_timestamp",
        "clean_price",
        "dirty_price",
        "yield",
        "spread",
        "volatility",
        "curve",
        "discount_rate",
        "funding_rate",
        "source_file_name",
        "loaded_at",
        "run_id",
        "pv",
        "option_premium",
        "customer_return",
        "bank_margin",
        "pricing_result",
    }
    assert field_names.isdisjoint(forbidden)


# --- Fixture: deterministic, importable, and covers eligible + ineligible --


def test_fixture_is_deterministic_and_nonempty():
    assert len(SYNTHETIC_BOND_FIXTURES) >= 1
    assert all(isinstance(bond, BondReferenceData) for bond in SYNTHETIC_BOND_FIXTURES)


def test_fixture_contains_at_least_one_eligible_and_one_ineligible_bond():
    results = [is_mvp_pricing_eligible(bond) for bond in SYNTHETIC_BOND_FIXTURES]
    assert any(result.eligible for result in results)
    assert any(not result.eligible for result in results)


# --- Export -------------------------------------------------------------


def test_bond_reference_data_importable_from_reference_data_package():
    from shiori_pricing_lab.reference_data import BondReferenceData as Imported

    assert Imported is BondReferenceData


def test_bond_reference_data_not_exported_from_products_package():
    import shiori_pricing_lab.products as products

    assert not hasattr(products, "BondReferenceData")
