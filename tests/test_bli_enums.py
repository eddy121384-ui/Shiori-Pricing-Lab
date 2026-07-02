"""Tests for the BLI controlled-vocabulary preflight (Issue #37).

Scope: enum-level construction and validation only. No product schema, no
market-data snapshot, and no pricing engine is exercised here — this mirrors
the "controlled vocabulary only" boundary of the PR (docs/14 §4.1, F-16/A-14).
"""

from __future__ import annotations

import pytest

from shiori_pricing_lab.pricing.result import PricingErrorCode, PricingMessage
from shiori_pricing_lab.products import (
    BondYieldConvention,
    Currency,
    ExerciseStyle,
    OptionType,
    PayoffBasis,
    Position,
    SettlementType,
)
from shiori_pricing_lab.products.enums import coerce_enum

# --- New BLI currencies (docs/14 §3.2 / F-16, Annex A/B NZ/KR/HK/SG) ---------


@pytest.mark.parametrize(
    "code,member",
    [
        ("NZD", Currency.NZD),
        ("KRW", Currency.KRW),
        ("HKD", Currency.HKD),
        ("SGD", Currency.SGD),
    ],
)
def test_new_bli_currencies_accepted(code, member):
    assert Currency(code) is member
    assert coerce_enum(code, Currency, "currency") is member


# --- BLI product enums (docs/14 §4.1) ----------------------------------------


@pytest.mark.parametrize(
    "enum_cls,members",
    [
        (PayoffBasis, ["PRICE", "YIELD"]),
        (OptionType, ["CALL", "PUT"]),
        (ExerciseStyle, ["EUROPEAN", "AMERICAN"]),
        (SettlementType, ["CASH", "PHYSICAL"]),
        (Position, ["BUY", "SELL"]),
    ],
)
def test_bli_product_enum_values_accepted(enum_cls, members):
    for value in members:
        assert enum_cls(value).value == value
        assert coerce_enum(value, enum_cls, "field") is enum_cls(value)


# --- BondYieldConvention (docs/14 §3.2, F-08) --------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "SEMI_ANNUAL_COMPOUND",
        "ANNUAL_COMPOUND",
        "SIMPLE_YIELD",
        "JAPANESE_COMPOUND",
        "OTHER",
    ],
)
def test_bond_yield_convention_values_accepted(value):
    assert BondYieldConvention(value).value == value
    assert coerce_enum(value, BondYieldConvention, "yield_convention") is BondYieldConvention(value)


# --- Unknown values still fail explicitly, never silently coerced -----------


@pytest.mark.parametrize(
    "enum_cls,field_name",
    [
        (Currency, "currency"),
        (PayoffBasis, "payoff_basis"),
        (OptionType, "option_type"),
        (ExerciseStyle, "exercise_style"),
        (SettlementType, "settlement_type"),
        (Position, "position"),
        (BondYieldConvention, "yield_convention"),
    ],
)
def test_unknown_enum_string_rejected_through_coerce_enum(enum_cls, field_name):
    with pytest.raises(ValueError, match=f"{field_name} must be one of"):
        coerce_enum("NOT_A_REAL_VALUE", enum_cls, field_name)


def test_act_365_variants_are_not_added_to_day_count():
    # Deliberately deferred (see PR body "day-count naming decision"): ACT_365,
    # ACT_365F, ACT_ACT, and ACT_ACT_ICMA are not added by this PR. Asserting
    # their absence here keeps the deferral honest against silent drift.
    from shiori_pricing_lab.products import DayCount

    existing = {member.value for member in DayCount}
    assert existing == {"ACT_360", "ACT_365_FIXED", "THIRTY_360", "ACT_ACT_ISDA"}


# --- PricingErrorCode.MISSING_REFERENCE_DATA (docs/14 §3.2, F-08/F-16) -------


def test_pricing_message_accepts_missing_reference_data_member():
    msg = PricingMessage(code=PricingErrorCode.MISSING_REFERENCE_DATA, message="x")
    assert msg.code is PricingErrorCode.MISSING_REFERENCE_DATA


def test_pricing_message_coerces_missing_reference_data_raw_string():
    msg = PricingMessage(code="MISSING_REFERENCE_DATA", message="x")
    assert msg.code is PricingErrorCode.MISSING_REFERENCE_DATA


def test_pricing_message_still_rejects_unknown_code_string():
    with pytest.raises(ValueError, match="known error or warning code"):
        PricingMessage(code="NOT_A_REAL_CODE", message="x")
