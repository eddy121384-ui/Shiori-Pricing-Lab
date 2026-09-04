"""Tests for `pricing/treasury_futures_contract.py` (Issue #190).

The quote-notation half of the desk's futures <-> CTD implied-yield utility.
Issue #190's explicit rejection of PR #9's one generic 32nds parser is what
most of this file is about: each contract's minimum tick and sub-32nd digit
alphabet is pinned here as a literal, so a change to the derivation rule in
the module cannot silently move ZT/ZF/ZN/ZB's quote conventions.
"""

from __future__ import annotations

import pytest

from shiori_pricing_lab.pricing.treasury_futures_contract import (
    SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES,
    TreasuryFuturesContractError,
    TreasuryFuturesQuoteError,
    format_futures_quote,
    get_contract,
    minimum_tick,
    parse_futures_quote,
    round_to_tick,
)


def test_the_four_mvp_contracts_are_supported() -> None:
    assert SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES == ("ZT", "ZF", "ZN", "ZB")


@pytest.mark.parametrize(
    "code, expected_tick",
    [
        ("ZT", 1 / 256),  # one eighth of a 32nd
        ("ZF", 1 / 128),  # one quarter of a 32nd
        ("ZN", 1 / 64),  # one half of a 32nd
        ("ZB", 1 / 32),  # one 32nd
    ],
)
def test_each_contract_has_its_own_published_minimum_tick(code, expected_tick) -> None:
    # Issue #190's RED requirement: these are four different increments, and
    # one generic 1/64 rule is wrong for three of them.
    assert minimum_tick(code) == expected_tick


@pytest.mark.parametrize(
    "code, expected_digits",
    [
        ("ZT", ["0", "1", "2", "3", "5", "6", "7", "8"]),
        ("ZF", ["0", "2", "5", "7"]),
        ("ZN", ["0", "5"]),
        ("ZB", ["0"]),
    ],
)
def test_the_sub_32nd_digit_alphabet_is_the_exchange_display_alphabet(
    code, expected_digits
) -> None:
    # The digit is the leading decimal digit of the fraction of a 32nd, not a
    # count of eighths -- ZF's "7" for three quarters is the case that decides
    # it, and ZT consequently never displays a "4" or a "9".
    assert sorted(get_contract(code).sub_32nd_digits) == expected_digits


@pytest.mark.parametrize(
    "code, raw, expected",
    [
        # ZT -- eighths of a 32nd
        ("ZT", "102-16", 102 + 16 / 32),
        ("ZT", "102-161", 102 + 16.125 / 32),
        ("ZT", "102-163", 102 + 16.375 / 32),
        ("ZT", "102-165", 102 + 16.5 / 32),
        ("ZT", "102-166", 102 + 16.625 / 32),
        ("ZT", "102-168", 102 + 16.875 / 32),
        ("ZT", "102-16+", 102 + 16.5 / 32),
        # ZF -- quarters of a 32nd
        ("ZF", "108-15", 108 + 15 / 32),
        ("ZF", "108-152", 108 + 15.25 / 32),
        ("ZF", "108-155", 108 + 15.5 / 32),
        ("ZF", "108-157", 108 + 15.75 / 32),
        # ZN -- halves of a 32nd
        ("ZN", "112-16", 112 + 16 / 32),
        ("ZN", "112-165", 112 + 16.5 / 32),
        ("ZN", "112-16+", 112 + 16.5 / 32),
        ("ZN", "112'165", 112 + 16.5 / 32),
        # ZB -- whole 32nds
        ("ZB", "118-16", 118 + 16 / 32),
        ("ZB", "118-160", 118 + 16 / 32),
        ("ZB", "118-31", 118 + 31 / 32),
    ],
)
def test_a_valid_exchange_quote_parses_to_its_decimal_price(code, raw, expected) -> None:
    assert parse_futures_quote(code, raw).decimal_price == pytest.approx(expected)


@pytest.mark.parametrize(
    "code, price, expected",
    [
        ("ZT", 102 + 16.625 / 32, "102-16 5/8"),
        ("ZT", 102.5, "102-16"),
        ("ZF", 108 + 15.75 / 32, "108-15 3/4"),
        ("ZN", 112 + 16.5 / 32, "112-16 1/2"),
        ("ZN", 112.5, "112-16"),
        ("ZB", 118.5, "118-16"),
        ("ZB", 119.0, "119-00"),
    ],
)
def test_a_decimal_price_formats_back_to_its_exchange_quote(code, price, expected) -> None:
    assert format_futures_quote(code, price) == expected


# Bloomberg-style notation parsing tests
@pytest.mark.parametrize(
    "code, raw, expected",
    [
        # ZT -- eighths of a 32nd (Bloomberg style)
        ("ZT", "102-16 1/8", 102 + 16.125 / 32),
        ("ZT", "102-16 1/4", 102 + 16.25 / 32),
        ("ZT", "102-16 3/8", 102 + 16.375 / 32),
        ("ZT", "102-16 1/2", 102 + 16.5 / 32),
        ("ZT", "102-16 5/8", 102 + 16.625 / 32),
        ("ZT", "102-16 3/4", 102 + 16.75 / 32),
        ("ZT", "102-16 7/8", 102 + 16.875 / 32),
        # ZT -- Unicode fractions
        ("ZT", "102-16 ⅛", 102 + 16.125 / 32),
        ("ZT", "102-16 ¼", 102 + 16.25 / 32),
        ("ZT", "102-16 ⅜", 102 + 16.375 / 32),
        ("ZT", "102-16 ½", 102 + 16.5 / 32),
        ("ZT", "102-16 ⅝", 102 + 16.625 / 32),
        ("ZT", "102-16 ¾", 102 + 16.75 / 32),
        ("ZT", "102-16 ⅞", 102 + 16.875 / 32),
        # ZF -- quarters of a 32nd
        ("ZF", "108-15 1/4", 108 + 15.25 / 32),
        ("ZF", "108-15 1/2", 108 + 15.5 / 32),
        ("ZF", "108-15 3/4", 108 + 15.75 / 32),
        ("ZF", "108-15 ¼", 108 + 15.25 / 32),
        ("ZF", "108-15 ½", 108 + 15.5 / 32),
        ("ZF", "108-15 ¾", 108 + 15.75 / 32),
        # ZN -- halves of a 32nd
        ("ZN", "112-16 1/2", 112 + 16.5 / 32),
        ("ZN", "112-16 ½", 112 + 16.5 / 32),
        # ZB -- whole 32nds only (no fractions)
        ("ZB", "118-16", 118 + 16 / 32),
    ],
)
def test_bloomberg_style_quote_parses_to_correct_decimal(code, raw, expected) -> None:
    assert parse_futures_quote(code, raw).decimal_price == pytest.approx(expected)


# Test that internal shorthand and Bloomberg notation produce the same decimal prices
@pytest.mark.parametrize(
    "code, shorthand, bloomberg, expected",
    [
        ("ZT", "102-161", "102-16 1/8", 102 + 16.125 / 32),
        ("ZT", "102-162", "102-16 1/4", 102 + 16.25 / 32),
        ("ZT", "102-163", "102-16 3/8", 102 + 16.375 / 32),
        ("ZT", "102-165", "102-16 1/2", 102 + 16.5 / 32),
        ("ZT", "102-166", "102-16 5/8", 102 + 16.625 / 32),
        ("ZT", "102-167", "102-16 3/4", 102 + 16.75 / 32),
        ("ZT", "102-168", "102-16 7/8", 102 + 16.875 / 32),
        ("ZT", "102-16+", "102-16 1/2", 102 + 16.5 / 32),
        ("ZF", "108-152", "108-15 1/4", 108 + 15.25 / 32),
        ("ZF", "108-155", "108-15 1/2", 108 + 15.5 / 32),
        ("ZF", "108-157", "108-15 3/4", 108 + 15.75 / 32),
        ("ZF", "108-15+", "108-15 1/2", 108 + 15.5 / 32),
        ("ZN", "112-165", "112-16 1/2", 112 + 16.5 / 32),
        ("ZN", "112-16+", "112-16 1/2", 112 + 16.5 / 32),
    ],
)
def test_shorthand_and_bloomberg_notation_are_equivalent(
    code, shorthand, bloomberg, expected
) -> None:
    shorthand_result = parse_futures_quote(code, shorthand)
    bloomberg_result = parse_futures_quote(code, bloomberg)
    assert shorthand_result.decimal_price == pytest.approx(expected)
    assert bloomberg_result.decimal_price == pytest.approx(expected)
    # Both should format to the same Bloomberg-style output
    lhs = format_futures_quote(code, shorthand_result.decimal_price)
    rhs = format_futures_quote(code, bloomberg_result.decimal_price)
    assert lhs == rhs


# Test invalid Bloomberg fractions are rejected
@pytest.mark.parametrize(
    "code, raw",
    [
        ("ZT", "102-16 1/3"),  # 1/3 not a valid eighth fraction
        ("ZB", "118-16 1/2"),  # ZB doesn't have fractions
        ("ZN", "112-16 1/4"),  # ZN doesn't have quarters
        ("ZF", "108-15 1/8"),  # ZF doesn't have eighths
    ],
)
def test_invalid_bloomberg_fraction_is_rejected(code, raw) -> None:
    with pytest.raises(TreasuryFuturesQuoteError):
        parse_futures_quote(code, raw)


@pytest.mark.parametrize("code", SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES)
def test_parse_and_format_are_inverses_across_every_tick_of_a_point(code) -> None:
    contract = get_contract(code)
    for tick in range(contract.ticks_per_point):
        price = 110.0 + tick / contract.ticks_per_point
        quote = format_futures_quote(code, price)
        assert parse_futures_quote(code, quote).decimal_price == pytest.approx(price)


@pytest.mark.parametrize(
    "code, raw",
    [
        # A half 32nd is not a tick on ZB.
        ("ZB", "118-16+"),
        ("ZB", "118-165"),
        # A quarter is not a tick on ZN, an eighth is not a tick on ZF.
        ("ZN", "112-162"),
        ("ZF", "108-151"),
        # 4 and 9 are never displayed on any Treasury futures contract.
        ("ZT", "102-164"),
        ("ZT", "102-169"),
        # 32nds must be 00-31, and the component is exactly two digits.
        ("ZN", "112-32"),
        ("ZN", "112-99"),
        ("ZN", "112-1"),
        ("ZN", "112-1655"),
        # Not a quote at all.
        ("ZN", "abc"),
        ("ZN", ""),
        ("ZN", "   "),
        ("ZN", "-16"),
    ],
)
def test_an_invalid_quote_for_this_contract_is_rejected(code, raw) -> None:
    with pytest.raises(TreasuryFuturesQuoteError):
        parse_futures_quote(code, raw)


@pytest.mark.parametrize("bad_price", [0, -1, -110.5, float("inf"), float("nan")])
def test_a_non_positive_or_non_finite_price_is_rejected(bad_price) -> None:
    with pytest.raises(TreasuryFuturesQuoteError):
        parse_futures_quote("ZN", bad_price)


def test_a_boolean_is_never_read_as_a_price() -> None:
    with pytest.raises(TreasuryFuturesQuoteError):
        parse_futures_quote("ZN", True)


def test_an_off_tick_decimal_is_kept_exactly_and_flagged_not_rounded_away() -> None:
    # The desk's whole use case is hypothetical levels, so an arbitrary
    # decimal must survive into the calculation untouched -- while still
    # showing the trader what would actually trade.
    quote = parse_futures_quote("ZN", 112.5137)
    assert quote.decimal_price == 112.5137
    assert quote.on_tick is False
    assert quote.exchange_price == pytest.approx(112.515625)
    assert quote.exchange_quote == "112-16 1/2"


def test_an_on_tick_decimal_is_reported_as_on_tick() -> None:
    quote = parse_futures_quote("ZN", 112.515625)
    assert quote.on_tick is True
    assert quote.exchange_price == quote.decimal_price


def test_rounding_to_a_tick_uses_the_contracts_own_increment() -> None:
    assert round_to_tick("ZB", 118.5137) == pytest.approx(118.5)
    assert round_to_tick("ZN", 118.5137) == pytest.approx(118.515625)
    assert round_to_tick("ZT", 118.5137) == pytest.approx(118.515625)
    assert round_to_tick("ZT", 118.5127) == pytest.approx(118.51171875)


def test_a_half_tick_residual_always_rounds_the_same_direction() -> None:
    # Exactly half a ZB tick above 118-16. Round-half-up, never banker's, so
    # the answer is reproducible by hand.
    assert round_to_tick("ZB", 118.5 + 1 / 64) == pytest.approx(118.53125)


@pytest.mark.parametrize("code", ["", "  ", "ZQ", "TY", None, 10])
def test_an_unsupported_contract_code_is_rejected(code) -> None:
    with pytest.raises(TreasuryFuturesContractError):
        get_contract(code)
