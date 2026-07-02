"""Tests for the DepositLeg / Treasury FTP controlled vocabulary (docs/18).

Scope: enum-level construction and validation only, mirroring
tests/test_bli_enums.py's boundary. No DepositLeg schema, Treasury FTP
parser, ingestion, or market-data code is exercised here.
"""

from __future__ import annotations

import pytest

from shiori_pricing_lab.products import (
    DepositRateMode,
    Frequency,
    TreasuryFTPQuoteSide,
    TreasuryFTPTenor,
)
from shiori_pricing_lab.products.enums import coerce_enum

# --- DepositRateMode ---------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["FIXED_RATE", "TREASURY_FTP_REFERENCE", "MANUAL_VERIFIED_RATE"],
)
def test_deposit_rate_mode_canonical_values_accepted(value):
    assert DepositRateMode(value).value == value
    assert coerce_enum(value, DepositRateMode, "deposit_rate_mode") is DepositRateMode(value)


@pytest.mark.parametrize(
    "value",
    ["fixed_rate", "FIXED", "TREASURY_FTP", "MANUAL", "AUTO", "NOT_A_REAL_MODE"],
)
def test_deposit_rate_mode_unsupported_values_rejected(value):
    with pytest.raises(ValueError, match="deposit_rate_mode must be one of"):
        coerce_enum(value, DepositRateMode, "deposit_rate_mode")


def test_deposit_rate_mode_blank_string_rejected():
    with pytest.raises(ValueError, match="deposit_rate_mode must not be blank"):
        coerce_enum("", DepositRateMode, "deposit_rate_mode")


# --- TreasuryFTPQuoteSide -----------------------------------------------------


@pytest.mark.parametrize("value", ["BID", "MID", "OFFER"])
def test_treasury_ftp_quote_side_canonical_values_accepted(value):
    assert TreasuryFTPQuoteSide(value).value == value
    assert (
        coerce_enum(value, TreasuryFTPQuoteSide, "quote_side")
        is TreasuryFTPQuoteSide(value)
    )


def test_treasury_ftp_quote_side_default_is_mid():
    # Vocabulary only: this asserts MID exists as a member, not that any
    # code path defaults to it (docs/18 §5 leaves the default-selection
    # mechanism to a future slice).
    assert TreasuryFTPQuoteSide.MID.value == "MID"


@pytest.mark.parametrize("value", ["ASK", "OF", "MIDDLE", "bid", "mid", "offer"])
def test_treasury_ftp_quote_side_unsupported_values_rejected(value):
    with pytest.raises(ValueError, match="quote_side must be one of"):
        coerce_enum(value, TreasuryFTPQuoteSide, "quote_side")


def test_treasury_ftp_quote_side_blank_string_rejected():
    with pytest.raises(ValueError, match="quote_side must not be blank"):
        coerce_enum("", TreasuryFTPQuoteSide, "quote_side")


# --- TreasuryFTPTenor ---------------------------------------------------------


@pytest.mark.parametrize(
    "value,member",
    [
        ("O/N", TreasuryFTPTenor.OVERNIGHT),
        ("1W", TreasuryFTPTenor.ONE_WEEK),
        ("2W", TreasuryFTPTenor.TWO_WEEK),
        ("3W", TreasuryFTPTenor.THREE_WEEK),
        ("1M", TreasuryFTPTenor.ONE_MONTH),
        ("2M", TreasuryFTPTenor.TWO_MONTH),
        ("3M", TreasuryFTPTenor.THREE_MONTH),
        ("6M", TreasuryFTPTenor.SIX_MONTH),
        ("9M", TreasuryFTPTenor.NINE_MONTH),
        ("1Y", TreasuryFTPTenor.ONE_YEAR),
        ("2Y", TreasuryFTPTenor.TWO_YEAR),
        ("3Y", TreasuryFTPTenor.THREE_YEAR),
        ("DEMAND_SAVINGS", TreasuryFTPTenor.DEMAND_SAVINGS),
    ],
)
def test_treasury_ftp_tenor_canonical_values_accepted(value, member):
    assert TreasuryFTPTenor(value) is member
    assert coerce_enum(value, TreasuryFTPTenor, "tenor") is member


@pytest.mark.parametrize(
    "value",
    [
        "ON",  # missing the slash
        "O_N",  # underscore instead of slash
        "o/n",  # lowercase
        "1WK",  # non-canonical weekly suffix
        "1週",  # non-canonical / non-ASCII label
        "12M",  # not in the observed sheet vocabulary
        "2M ",  # trailing whitespace
        " 1M",  # leading whitespace
    ],
)
def test_treasury_ftp_tenor_unsupported_or_ambiguous_values_rejected(value):
    with pytest.raises(ValueError, match="tenor must be one of"):
        coerce_enum(value, TreasuryFTPTenor, "tenor")


def test_treasury_ftp_tenor_blank_string_rejected():
    with pytest.raises(ValueError, match="tenor must not be blank"):
        coerce_enum("", TreasuryFTPTenor, "tenor")


def test_treasury_ftp_tenor_rejects_none_via_enum_constructor():
    # coerce_enum's blank-string guard only applies to str input; None is
    # neither an existing member nor a valid raw string, so the underlying
    # StrEnum lookup raises ValueError, which coerce_enum re-raises with
    # the standard "must be one of" message.
    with pytest.raises(ValueError, match="tenor must be one of"):
        coerce_enum(None, TreasuryFTPTenor, "tenor")


def test_treasury_ftp_tenor_is_not_the_frequency_enum():
    # Frequency is a payment/reset period vocabulary and must not be reused
    # as FTP tenor vocabulary (docs/18 §2.3) -- assert the two enums are
    # distinct classes with non-overlapping member sets.
    assert TreasuryFTPTenor is not Frequency
    frequency_values = {member.value for member in Frequency}
    tenor_values = {member.value for member in TreasuryFTPTenor}
    assert frequency_values.isdisjoint(tenor_values)


def test_treasury_ftp_tenor_member_names_are_valid_python_identifiers():
    for member in TreasuryFTPTenor:
        assert member.name.isidentifier()
