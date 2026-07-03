"""Tests for the BondLinkedStructuredProduct wrapper schema (docs/19).

Schema-construction and validation tests only. No pricing, payoff
calculation, market data, or Treasury FTP parser/ingestion is involved --
the wrapper only binds one DepositLeg and one BondOption and validates the
relationship between them.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from shiori_pricing_lab.products import (
    BondLinkedStructuredProduct,
    BondOption,
    Currency,
    DepositLeg,
    DepositRateMode,
    ExerciseStyle,
    OptionType,
    PayoffBasis,
    Position,
    PrincipalRepaymentRule,
    SettlementType,
)


def _bond_option(**overrides) -> BondOption:
    """Build a synthetic, valid European CASH-settled USD BondOption.

    Default expiry (2026-09-29) + settlement_lag_days (2) = effective
    settlement 2026-10-01, which lands exactly on the default DepositLeg
    maturity below -- an "equal to maturity" case that must be accepted.
    """

    params = dict(
        product_id="BONDOPT-0001",
        underlying_isin="US912828ZZ11",
        currency=Currency.USD,
        payoff_basis=PayoffBasis.PRICE,
        option_type=OptionType.CALL,
        exercise_style=ExerciseStyle.EUROPEAN,
        settlement_type=SettlementType.CASH,
        settlement_lag_days=2,
        expiry_date="2026-09-29",
        notional=50.0,
        position=Position.BUY,
        strike_price=99.5,
    )
    params.update(overrides)
    return BondOption(**params)


def _deposit_leg(**overrides) -> DepositLeg:
    """Build a synthetic, valid FIXED_RATE USD DepositLeg."""

    params = dict(
        deposit_leg_id="DEP-0001",
        deposit_notional=100.0,
        currency=Currency.USD,
        start_date="2026-07-01",
        maturity_date="2026-10-01",
        deposit_rate_mode=DepositRateMode.FIXED_RATE,
        principal_repayment_rule=PrincipalRepaymentRule.FULL_PRINCIPAL_AT_MATURITY,
        fixed_deposit_rate=0.03,
    )
    params.update(overrides)
    return DepositLeg(**params)


def _wrapper(**overrides) -> BondLinkedStructuredProduct:
    deposit_leg = overrides.pop("deposit_leg", None) or _deposit_leg()
    bond_option = overrides.pop("bond_option", None) or _bond_option()
    params = dict(
        product_id="BLI-0001",
        deposit_leg=deposit_leg,
        bond_option=bond_option,
    )
    params.update(overrides)
    return BondLinkedStructuredProduct(**params)


# --- Valid construction -------------------------------------------------


def test_valid_wrapper_construction():
    deposit_leg = _deposit_leg()
    bond_option = _bond_option()
    wrapper = _wrapper(deposit_leg=deposit_leg, bond_option=bond_option)

    assert wrapper.product_type == "BOND_LINKED_STRUCTURED_PRODUCT"
    assert wrapper.deposit_leg is deposit_leg
    assert wrapper.bond_option is bond_option
    assert wrapper.participation_ratio == bond_option.notional / deposit_leg.deposit_notional


def test_effective_settlement_date_equal_to_maturity_accepted():
    # expiry 2026-09-29 + settlement_lag_days 2 = 2026-10-01, which equals
    # the default deposit_leg.maturity_date exactly.
    wrapper = _wrapper(
        bond_option=_bond_option(expiry_date="2026-09-29", settlement_lag_days=2),
        deposit_leg=_deposit_leg(maturity_date="2026-10-01"),
    )
    assert wrapper.bond_option.expiry_date == "2026-09-29"


def test_expiry_equal_to_deposit_start_accepted():
    wrapper = _wrapper(
        deposit_leg=_deposit_leg(start_date="2026-07-01"),
        bond_option=_bond_option(expiry_date="2026-07-01", settlement_lag_days=0),
    )
    assert wrapper.bond_option.expiry_date == "2026-07-01"


# --- Rejections: product_id / component types -------------------------------


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_product_id_rejected(value):
    with pytest.raises(ValueError, match="product_id must be a non-blank string"):
        _wrapper(product_id=value)


def test_deposit_leg_wrong_type_rejected():
    with pytest.raises(TypeError, match="deposit_leg must be a DepositLeg"):
        _wrapper(deposit_leg=object())


def test_bond_option_wrong_type_rejected():
    with pytest.raises(TypeError, match="bond_option must be a BondOption"):
        _wrapper(bond_option=object())


def test_deposit_leg_dict_rejected():
    with pytest.raises(TypeError, match="deposit_leg must be a DepositLeg"):
        _wrapper(deposit_leg={"deposit_notional": 100.0})


# --- Rejections: currency mismatch ------------------------------------------


def test_currency_mismatch_rejected():
    with pytest.raises(ValueError, match="deposit_leg.currency .* must match bond_option.currency"):
        _wrapper(bond_option=_bond_option(currency=Currency.EUR))


# --- Rejections: settlement type ---------------------------------------------


def test_bond_option_physical_settlement_is_valid_standalone():
    # BondOption itself remains general and allows PHYSICAL settlement.
    option = _bond_option(settlement_type=SettlementType.PHYSICAL)
    assert option.settlement_type is SettlementType.PHYSICAL


def test_wrapper_rejects_physical_settlement():
    physical_option = _bond_option(settlement_type=SettlementType.PHYSICAL)
    with pytest.raises(ValueError, match="bond_option.settlement_type must be CASH"):
        _wrapper(bond_option=physical_option)


# --- principal repayment rule ------------------------------------------------


def test_wrapper_accepts_full_principal_at_maturity():
    # PrincipalRepaymentRule currently defines only one member
    # (FULL_PRINCIPAL_AT_MATURITY), so DepositLeg's own coerce_enum layer
    # already rejects any other value before the wrapper ever sees it --
    # this test documents that the wrapper's own explicit re-check does
    # not reject the one legal value.
    wrapper = _wrapper()
    assert (
        wrapper.deposit_leg.principal_repayment_rule
        is PrincipalRepaymentRule.FULL_PRINCIPAL_AT_MATURITY
    )


# --- Rejections: date / settlement guardrails --------------------------------


def test_expiry_before_deposit_start_rejected():
    with pytest.raises(ValueError, match="bond_option.expiry_date .* must be on or after"):
        _wrapper(
            deposit_leg=_deposit_leg(start_date="2026-07-01"),
            bond_option=_bond_option(expiry_date="2026-06-30", settlement_lag_days=0),
        )


def test_effective_settlement_date_after_maturity_rejected():
    # deposit maturity = 2026-10-01, expiry = 2026-10-01,
    # settlement_lag_days = 2 -> effective settlement 2026-10-03 > maturity.
    with pytest.raises(ValueError, match="effective settlement date"):
        _wrapper(
            deposit_leg=_deposit_leg(maturity_date="2026-10-01"),
            bond_option=_bond_option(expiry_date="2026-10-01", settlement_lag_days=2),
        )


def test_bare_expiry_check_is_insufficient_settlement_lag_pushes_past_maturity():
    # expiry <= maturity holds (2026-09-30 <= 2026-10-01), but
    # expiry + settlement_lag_days (5) = 2026-10-05 > maturity, so the
    # wrapper must still reject -- a bare expiry_date <= maturity_date
    # check alone would have wrongly accepted this.
    with pytest.raises(ValueError, match="effective settlement date"):
        _wrapper(
            deposit_leg=_deposit_leg(maturity_date="2026-10-01"),
            bond_option=_bond_option(expiry_date="2026-09-30", settlement_lag_days=5),
        )


# --- participation_ratio: derived-only, not a constructor field -------------


def test_participation_ratio_not_accepted_as_constructor_argument():
    with pytest.raises(TypeError):
        _wrapper(participation_ratio=0.5)


def test_participation_ratio_derived_correctly():
    wrapper = _wrapper(
        deposit_leg=_deposit_leg(deposit_notional=100.0),
        bond_option=_bond_option(notional=50.0),
    )
    assert wrapper.participation_ratio == 0.5


# --- Boundary: no duplicated / market-data / pricing fields on wrapper ------


def test_wrapper_has_no_duplicated_or_market_data_or_pricing_fields():
    field_names = {f.name for f in fields(BondLinkedStructuredProduct)}
    forbidden = {
        "deposit_notional",
        "bond_option_notional",
        "fixed_deposit_rate",
        "ftp_rate_selector",
        "manual_input_reference",
        "strike_price",
        "strike_yield",
        "participation_ratio",
        "business_date",
        "as_of_timestamp",
        "source_file_name",
        "loaded_at",
        "rate_percent",
        "rate_decimal",
        "manual_verified_rate",
        "bond_clean_price",
        "bond_yield",
        "volatility",
        "curve",
        "spread",
        "pricing_result",
        "pv",
        "option_premium",
        "customer_return",
        "bank_margin",
        "payoff_amount",
        "deposit_accrual",
    }
    assert field_names.isdisjoint(forbidden)


# --- Export ------------------------------------------------------------------


def test_bond_linked_structured_product_importable_from_products_package():
    from shiori_pricing_lab.products import BondLinkedStructuredProduct as Imported

    assert Imported is BondLinkedStructuredProduct
