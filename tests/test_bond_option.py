"""Tests for the BondOption product schema (Issue #38, partial).

Schema-construction and validation tests only. No pricing, market data,
Bond Master, ingestion, or valuation date is involved — BondOption is a pure
deal-term object (docs/15_bli_product_schema_preflight_issue_38.md §2).
"""

from __future__ import annotations

import pytest

from shiori_pricing_lab.products import (
    BondOption,
    Currency,
    ExerciseStyle,
    OptionType,
    PayoffBasis,
    Position,
    SettlementType,
)


def _bond_option(**overrides) -> BondOption:
    """Build a synthetic, valid European price-based BondOption."""

    params = dict(
        product_id="BONDOPT-0001",
        underlying_isin="US912828ZZ11",
        currency=Currency.USD,
        payoff_basis=PayoffBasis.PRICE,
        option_type=OptionType.CALL,
        exercise_style=ExerciseStyle.EUROPEAN,
        settlement_type=SettlementType.CASH,
        settlement_lag_days=2,
        expiry_date="2026-12-01",
        notional=10_000_000.0,
        position=Position.BUY,
        strike_price=99.5,
    )
    params.update(overrides)
    return BondOption(**params)


# --- Valid construction -------------------------------------------------


def test_valid_european_price_based_bond_option():
    opt = _bond_option()
    assert opt.product_type == "BOND_OPTION"
    assert opt.strike_price == 99.5
    assert opt.strike_yield is None
    assert opt.exercise_start_date is None


def test_valid_yield_based_bond_option():
    opt = _bond_option(payoff_basis=PayoffBasis.YIELD, strike_price=None, strike_yield=0.035)
    assert opt.strike_yield == 0.035
    assert opt.strike_price is None


def test_valid_yield_based_bond_option_allows_zero_or_negative_yield():
    opt = _bond_option(payoff_basis=PayoffBasis.YIELD, strike_price=None, strike_yield=-0.001)
    assert opt.strike_yield == -0.001

    opt_zero = _bond_option(payoff_basis=PayoffBasis.YIELD, strike_price=None, strike_yield=0.0)
    assert opt_zero.strike_yield == 0.0


def test_valid_american_option_with_exercise_start_date_before_expiry():
    opt = _bond_option(
        exercise_style=ExerciseStyle.AMERICAN,
        exercise_start_date="2026-06-01",
        expiry_date="2026-12-01",
    )
    assert opt.exercise_start_date == "2026-06-01"


def test_valid_physical_settlement_bond_option():
    opt = _bond_option(settlement_type=SettlementType.PHYSICAL)
    assert opt.settlement_type is SettlementType.PHYSICAL


def test_enum_fields_coerced_from_raw_strings():
    opt = BondOption(
        product_id="BONDOPT-0002",
        underlying_isin="US912828ZZ11",
        currency="USD",
        payoff_basis="PRICE",
        option_type="PUT",
        exercise_style="EUROPEAN",
        settlement_type="CASH",
        settlement_lag_days=1,
        expiry_date="2026-12-01",
        notional=1_000_000.0,
        position="SELL",
        strike_price=100.0,
    )
    assert opt.currency is Currency.USD
    assert opt.option_type is OptionType.PUT
    assert opt.position is Position.SELL


# --- Exercise-style / exercise-start-date validation ---------------------


def test_european_option_rejects_exercise_start_date():
    with pytest.raises(ValueError, match="exercise_start_date must be None"):
        _bond_option(exercise_style=ExerciseStyle.EUROPEAN, exercise_start_date="2026-06-01")


def test_american_option_requires_exercise_start_date():
    with pytest.raises(ValueError, match="exercise_start_date is required"):
        _bond_option(exercise_style=ExerciseStyle.AMERICAN, exercise_start_date=None)


def test_american_option_rejects_exercise_start_date_on_or_after_expiry():
    with pytest.raises(ValueError, match="must be strictly before"):
        _bond_option(
            exercise_style=ExerciseStyle.AMERICAN,
            exercise_start_date="2026-12-01",
            expiry_date="2026-12-01",
        )


# --- Payoff-basis / strike cross-field validation -------------------------


def test_price_payoff_without_strike_price_rejected():
    with pytest.raises(ValueError, match="strike_price is required"):
        _bond_option(payoff_basis=PayoffBasis.PRICE, strike_price=None)


def test_price_payoff_with_both_strike_price_and_strike_yield_rejected():
    with pytest.raises(ValueError, match="strike_yield must be None"):
        _bond_option(payoff_basis=PayoffBasis.PRICE, strike_price=99.5, strike_yield=0.03)


def test_price_payoff_rejects_non_positive_strike_price():
    with pytest.raises(ValueError, match="strike_price must be positive"):
        _bond_option(payoff_basis=PayoffBasis.PRICE, strike_price=0.0)


def test_yield_payoff_without_strike_yield_rejected():
    with pytest.raises(ValueError, match="strike_yield is required"):
        _bond_option(payoff_basis=PayoffBasis.YIELD, strike_price=None, strike_yield=None)


def test_yield_payoff_with_both_strike_yield_and_strike_price_rejected():
    with pytest.raises(ValueError, match="strike_price must be None"):
        _bond_option(payoff_basis=PayoffBasis.YIELD, strike_price=99.5, strike_yield=0.03)


@pytest.mark.parametrize(
    "strike_yield",
    ["abc", float("nan"), float("inf"), float("-inf"), True],
)
def test_yield_payoff_rejects_non_finite_or_non_numeric_strike_yield(strike_yield):
    with pytest.raises(ValueError, match="strike_yield must be a finite number"):
        _bond_option(payoff_basis=PayoffBasis.YIELD, strike_price=None, strike_yield=strike_yield)


# --- notional / settlement_lag_days ---------------------------------------


@pytest.mark.parametrize("notional", [0.0, -1.0, -1_000_000.0])
def test_non_positive_notional_rejected(notional):
    with pytest.raises(ValueError, match="notional must be positive"):
        _bond_option(notional=notional)


@pytest.mark.parametrize("lag", [-1, -5])
def test_negative_settlement_lag_days_rejected(lag):
    with pytest.raises(ValueError, match="settlement_lag_days must be a non-negative integer"):
        _bond_option(settlement_lag_days=lag)


def test_non_integer_settlement_lag_days_rejected():
    with pytest.raises(ValueError, match="settlement_lag_days must be a non-negative integer"):
        _bond_option(settlement_lag_days=2.5)


# --- Enum rejection ---------------------------------------------------------


@pytest.mark.parametrize(
    "field_name",
    ["currency", "payoff_basis", "option_type", "exercise_style", "settlement_type", "position"],
)
def test_invalid_enum_string_rejected(field_name):
    with pytest.raises(ValueError, match="must be one of"):
        _bond_option(**{field_name: "NOT_A_REAL_VALUE"})


# --- product_id / underlying_isin -------------------------------------------


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_product_id_rejected(value):
    with pytest.raises(ValueError, match="product_id must be a non-blank string"):
        _bond_option(product_id=value)


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_underlying_isin_rejected(value):
    with pytest.raises(ValueError, match="underlying_isin must be a non-blank string"):
        _bond_option(underlying_isin=value)


# --- Forbidden fields: Bond Master / market data / pricing / deposit-leg ----
#
# BondOption is a plain frozen dataclass with an explicit field list (no
# **kwargs), so passing any unknown keyword argument already raises
# TypeError at construction time. This asserts that behaviour for the
# specific forbidden fields listed in docs/15 §2.2 / §4, so the guard is not
# silently lost if the schema is ever refactored to accept **kwargs.


@pytest.mark.parametrize(
    "field_name",
    [
        "coupon",
        "coupon_frequency",
        "bond_maturity_date",
        "bond_issue_date",
        "day_count",
        "business_day_convention",
        "yield_convention",
        "compounding_frequency",
        "accrued_interest",
        "clean_price",
        "dirty_price",
        "bond_yield",
        "volatility",
        "credit_spread",
        "curve_id",
        "discount_curve_id",
        "bond_reference_curve_id",
        "funding_curve_id",
        "market_data_snapshot_id",
        "pricing_result",
        "greeks",
        "pv",
        "premium",
        "deposit_rate",
        "deposit_yield",
        "principal_repayment_rule",
        "participation_ratio",
        "deposit_notional",
    ],
)
def test_forbidden_field_not_accepted_as_constructor_argument(field_name):
    with pytest.raises(TypeError):
        _bond_option(**{field_name: object()})
