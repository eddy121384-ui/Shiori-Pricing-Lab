"""Tests for the DepositLeg product schema (docs/18, BLI MVP Slice A).

Schema-construction and validation tests only. No pricing, market data,
Treasury FTP parser/ingestion, MarketDataSnapshot, or wrapper is involved
here — DepositLeg is a pure deal-term / rate-source-selector object
consumed later by a future BondLinkedStructuredProduct wrapper.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, fields

import pytest

from shiori_pricing_lab.products import (
    Currency,
    DepositLeg,
    DepositRateMode,
    PrincipalRepaymentRule,
    TreasuryFTPQuoteSide,
    TreasuryFTPRateSelector,
    TreasuryFTPTenor,
)


def _deposit_leg(**overrides) -> DepositLeg:
    """Build a synthetic, valid FIXED_RATE USD DepositLeg."""

    params = dict(
        deposit_leg_id="DEP-0001",
        deposit_notional=10_000_000.0,
        currency=Currency.USD,
        start_date="2026-07-01",
        maturity_date="2026-10-01",
        deposit_rate_mode=DepositRateMode.FIXED_RATE,
        principal_repayment_rule=PrincipalRepaymentRule.FULL_PRINCIPAL_AT_MATURITY,
        fixed_deposit_rate=0.035,
    )
    params.update(overrides)
    return DepositLeg(**params)


# --- Valid construction -------------------------------------------------


def test_valid_fixed_rate_deposit_leg():
    leg = _deposit_leg()
    assert leg.leg_type == "DEPOSIT_LEG"
    assert leg.fixed_deposit_rate == 0.035
    assert leg.ftp_rate_selector is None
    assert leg.manual_input_reference is None


@pytest.mark.parametrize("rate", [0.0, -0.001])
def test_fixed_rate_allows_zero_and_negative_rates(rate):
    leg = _deposit_leg(fixed_deposit_rate=rate)
    assert leg.fixed_deposit_rate == rate


def test_valid_treasury_ftp_reference_deposit_leg():
    selector = TreasuryFTPRateSelector(
        currency=Currency.USD,
        tenor=TreasuryFTPTenor.THREE_MONTH,
        quote_side=TreasuryFTPQuoteSide.MID,
    )
    leg = _deposit_leg(
        deposit_rate_mode=DepositRateMode.TREASURY_FTP_REFERENCE,
        fixed_deposit_rate=None,
        ftp_rate_selector=selector,
    )
    assert leg.ftp_rate_selector is selector
    assert leg.ftp_rate_selector.currency is Currency.USD
    assert leg.ftp_rate_selector.tenor is TreasuryFTPTenor.THREE_MONTH
    assert leg.ftp_rate_selector.quote_side is TreasuryFTPQuoteSide.MID
    assert leg.fixed_deposit_rate is None
    assert leg.manual_input_reference is None


def test_valid_treasury_ftp_reference_with_matching_top_level_tenor():
    selector = TreasuryFTPRateSelector(
        currency=Currency.USD,
        tenor=TreasuryFTPTenor.OVERNIGHT,
        quote_side=TreasuryFTPQuoteSide.BID,
    )
    leg = _deposit_leg(
        deposit_rate_mode=DepositRateMode.TREASURY_FTP_REFERENCE,
        fixed_deposit_rate=None,
        ftp_rate_selector=selector,
        tenor=TreasuryFTPTenor.OVERNIGHT,
    )
    assert leg.tenor is TreasuryFTPTenor.OVERNIGHT


def test_treasury_ftp_reference_selector_coerces_raw_strings():
    selector = TreasuryFTPRateSelector(currency="USD", tenor="O/N", quote_side="MID")
    assert selector.currency is Currency.USD
    assert selector.tenor is TreasuryFTPTenor.OVERNIGHT
    assert selector.quote_side is TreasuryFTPQuoteSide.MID


def test_valid_manual_verified_rate_deposit_leg():
    leg = _deposit_leg(
        deposit_rate_mode=DepositRateMode.MANUAL_VERIFIED_RATE,
        fixed_deposit_rate=None,
        manual_input_reference="MANUAL-INPUT-2026-07-01-USD-3M",
    )
    assert leg.manual_input_reference == "MANUAL-INPUT-2026-07-01-USD-3M"
    assert leg.fixed_deposit_rate is None
    assert leg.ftp_rate_selector is None


def test_enum_fields_coerced_from_raw_strings():
    leg = DepositLeg(
        deposit_leg_id="DEP-0002",
        deposit_notional=1_000_000.0,
        currency="EUR",
        start_date="2026-07-01",
        maturity_date="2026-08-01",
        deposit_rate_mode="FIXED_RATE",
        principal_repayment_rule="FULL_PRINCIPAL_AT_MATURITY",
        fixed_deposit_rate=0.01,
    )
    assert leg.currency is Currency.EUR
    assert leg.deposit_rate_mode is DepositRateMode.FIXED_RATE
    assert leg.principal_repayment_rule is PrincipalRepaymentRule.FULL_PRINCIPAL_AT_MATURITY


# --- asdict / JSON round-trip ------------------------------------------------


def test_deposit_leg_serializes_via_asdict():
    leg = _deposit_leg()
    data = asdict(leg)

    assert data["leg_type"] == "DEPOSIT_LEG"
    assert data["currency"] == "USD"
    assert data["deposit_rate_mode"] == "FIXED_RATE"
    assert data["principal_repayment_rule"] == "FULL_PRINCIPAL_AT_MATURITY"
    assert json.loads(json.dumps(data))["deposit_notional"] == 10_000_000.0


def test_treasury_ftp_reference_selector_serializes_via_asdict():
    selector = TreasuryFTPRateSelector(
        currency=Currency.USD,
        tenor=TreasuryFTPTenor.THREE_MONTH,
        quote_side=TreasuryFTPQuoteSide.MID,
    )
    leg = _deposit_leg(
        deposit_rate_mode=DepositRateMode.TREASURY_FTP_REFERENCE,
        fixed_deposit_rate=None,
        ftp_rate_selector=selector,
    )
    data = asdict(leg)
    assert data["ftp_rate_selector"] == {
        "currency": "USD",
        "tenor": "3M",
        "quote_side": "MID",
    }
    json.loads(json.dumps(data))


# --- General rejection --------------------------------------------------


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_deposit_leg_id_rejected(value):
    with pytest.raises(ValueError, match="deposit_leg_id must be a non-blank string"):
        _deposit_leg(deposit_leg_id=value)


@pytest.mark.parametrize(
    "notional", [0.0, -1.0, float("nan"), float("inf"), float("-inf"), True, "1000000"]
)
def test_invalid_deposit_notional_rejected(notional):
    with pytest.raises(ValueError):
        _deposit_leg(deposit_notional=notional)


def test_non_positive_notional_error_message():
    with pytest.raises(ValueError, match="deposit_notional must be positive"):
        _deposit_leg(deposit_notional=0.0)


def test_non_finite_notional_error_message():
    with pytest.raises(ValueError, match="deposit_notional must be a finite number"):
        _deposit_leg(deposit_notional=math.nan)


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("start_date", "not-a-date"),
        ("start_date", "20260701"),
        ("maturity_date", "2026-13-40"),
    ],
)
def test_invalid_date_format_rejected(field_name, value):
    with pytest.raises(ValueError, match="must be a YYYY-MM-DD date string"):
        _deposit_leg(**{field_name: value})


def test_maturity_before_start_rejected():
    with pytest.raises(ValueError, match="maturity_date .* must be after start_date"):
        _deposit_leg(start_date="2026-10-01", maturity_date="2026-07-01")


def test_maturity_equal_to_start_rejected():
    with pytest.raises(ValueError, match="maturity_date .* must be after start_date"):
        _deposit_leg(start_date="2026-07-01", maturity_date="2026-07-01")


@pytest.mark.parametrize(
    "field_name",
    ["currency", "deposit_rate_mode", "principal_repayment_rule", "tenor"],
)
def test_invalid_enum_string_rejected(field_name):
    with pytest.raises(ValueError, match="must be one of"):
        _deposit_leg(**{field_name: "NOT_A_REAL_VALUE"})


# --- FIXED_RATE mode rejection -----------------------------------------------


def test_fixed_rate_missing_rate_rejected():
    with pytest.raises(ValueError, match="fixed_deposit_rate is required"):
        _deposit_leg(fixed_deposit_rate=None)


def test_fixed_rate_rejects_ftp_rate_selector_present():
    selector = TreasuryFTPRateSelector(
        currency=Currency.USD, tenor=TreasuryFTPTenor.ONE_MONTH, quote_side=TreasuryFTPQuoteSide.MID
    )
    with pytest.raises(ValueError, match="ftp_rate_selector must be None"):
        _deposit_leg(ftp_rate_selector=selector)


def test_fixed_rate_rejects_manual_input_reference_present():
    with pytest.raises(ValueError, match="manual_input_reference must be None"):
        _deposit_leg(manual_input_reference="some-reference")


@pytest.mark.parametrize("rate", [float("nan"), float("inf"), float("-inf"), "0.035"])
def test_fixed_rate_rejects_non_finite_or_non_numeric_rate(rate):
    with pytest.raises(ValueError, match="fixed_deposit_rate must be a finite number"):
        _deposit_leg(fixed_deposit_rate=rate)


def test_fixed_rate_rejects_bool_rate():
    with pytest.raises(ValueError, match="fixed_deposit_rate must be a finite number"):
        _deposit_leg(fixed_deposit_rate=True)


# --- TREASURY_FTP_REFERENCE mode rejection -----------------------------------


def _ftp_reference_kwargs(**overrides):
    selector = TreasuryFTPRateSelector(
        currency=Currency.USD,
        tenor=TreasuryFTPTenor.THREE_MONTH,
        quote_side=TreasuryFTPQuoteSide.MID,
    )
    params = dict(
        deposit_rate_mode=DepositRateMode.TREASURY_FTP_REFERENCE,
        fixed_deposit_rate=None,
        ftp_rate_selector=selector,
    )
    params.update(overrides)
    return params


def test_treasury_ftp_reference_missing_selector_rejected():
    with pytest.raises(ValueError, match="ftp_rate_selector is required"):
        _deposit_leg(**_ftp_reference_kwargs(ftp_rate_selector=None))


def test_treasury_ftp_reference_rejects_fixed_deposit_rate_present():
    with pytest.raises(ValueError, match="fixed_deposit_rate must be None"):
        _deposit_leg(**_ftp_reference_kwargs(fixed_deposit_rate=0.02))


def test_treasury_ftp_reference_rejects_manual_input_reference_present():
    with pytest.raises(ValueError, match="manual_input_reference must be None"):
        _deposit_leg(**_ftp_reference_kwargs(manual_input_reference="some-reference"))


def test_treasury_ftp_reference_rejects_non_selector_type():
    with pytest.raises(TypeError, match="ftp_rate_selector must be a TreasuryFTPRateSelector"):
        _deposit_leg(**_ftp_reference_kwargs(ftp_rate_selector={"currency": "USD"}))


def test_treasury_ftp_reference_rejects_currency_mismatch():
    selector = TreasuryFTPRateSelector(
        currency=Currency.EUR,
        tenor=TreasuryFTPTenor.THREE_MONTH,
        quote_side=TreasuryFTPQuoteSide.MID,
    )
    with pytest.raises(ValueError, match="ftp_rate_selector.currency .* must match"):
        _deposit_leg(**_ftp_reference_kwargs(ftp_rate_selector=selector))


def test_treasury_ftp_reference_rejects_tenor_mismatch_with_top_level_tenor():
    selector = TreasuryFTPRateSelector(
        currency=Currency.USD,
        tenor=TreasuryFTPTenor.THREE_MONTH,
        quote_side=TreasuryFTPQuoteSide.MID,
    )
    with pytest.raises(ValueError, match="ftp_rate_selector.tenor .* must match"):
        _deposit_leg(
            **_ftp_reference_kwargs(ftp_rate_selector=selector, tenor=TreasuryFTPTenor.ONE_MONTH)
        )


@pytest.mark.parametrize("value", ["ON", "O_N", "o/n", "1WK", "12M", ""])
def test_treasury_ftp_reference_rejects_unsupported_tenor_spellings(value):
    with pytest.raises(ValueError):
        TreasuryFTPRateSelector(
            currency=Currency.USD, tenor=value, quote_side=TreasuryFTPQuoteSide.MID
        )


# --- MANUAL_VERIFIED_RATE mode rejection -------------------------------------


def _manual_verified_kwargs(**overrides):
    params = dict(
        deposit_rate_mode=DepositRateMode.MANUAL_VERIFIED_RATE,
        fixed_deposit_rate=None,
        manual_input_reference="MANUAL-INPUT-REF",
    )
    params.update(overrides)
    return params


def test_manual_verified_rate_missing_reference_rejected():
    with pytest.raises(ValueError, match="manual_input_reference is required"):
        _deposit_leg(**_manual_verified_kwargs(manual_input_reference=None))


@pytest.mark.parametrize("value", ["", "   "])
def test_manual_verified_rate_blank_reference_rejected(value):
    with pytest.raises(ValueError, match="manual_input_reference must be a non-blank string"):
        _deposit_leg(**_manual_verified_kwargs(manual_input_reference=value))


def test_manual_verified_rate_rejects_fixed_deposit_rate_present():
    with pytest.raises(ValueError, match="fixed_deposit_rate must be None"):
        _deposit_leg(**_manual_verified_kwargs(fixed_deposit_rate=0.02))


def test_manual_verified_rate_rejects_ftp_rate_selector_present():
    selector = TreasuryFTPRateSelector(
        currency=Currency.USD, tenor=TreasuryFTPTenor.ONE_MONTH, quote_side=TreasuryFTPQuoteSide.MID
    )
    with pytest.raises(ValueError, match="ftp_rate_selector must be None"):
        _deposit_leg(**_manual_verified_kwargs(ftp_rate_selector=selector))


# --- Boundary: no market-data / pricing-run fields on DepositLeg ------------


def test_deposit_leg_has_no_market_data_or_pricing_run_fields():
    field_names = {f.name for f in fields(DepositLeg)}
    forbidden = {
        "business_date",
        "as_of_timestamp",
        "source_file_name",
        "loaded_at",
        "rate_percent",
        "rate_decimal",
        "manual_verified_rate",
        "entered_by",
        "run_id",
        "day_count",
        "business_day_convention",
        "calendar",
    }
    assert field_names.isdisjoint(forbidden)


def test_treasury_ftp_rate_selector_has_no_dated_or_resolved_rate_fields():
    field_names = {f.name for f in fields(TreasuryFTPRateSelector)}
    forbidden = {
        "business_date",
        "as_of_timestamp",
        "source_file_name",
        "loaded_at",
        "rate_percent",
        "rate_decimal",
    }
    assert field_names.isdisjoint(forbidden)
