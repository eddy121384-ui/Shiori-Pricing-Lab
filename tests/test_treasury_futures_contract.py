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


def test_the_supported_contracts_are_the_mvp_four_plus_uxy_and_wn() -> None:
    assert SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES == ("ZT", "ZF", "ZN", "ZB", "UXY", "WN")


@pytest.mark.parametrize(
    "code, expected_tick",
    [
        ("ZT", 1 / 256),  # one eighth of a 32nd
        ("ZF", 1 / 128),  # one quarter of a 32nd
        ("ZN", 1 / 64),  # one half of a 32nd
        ("ZB", 1 / 32),  # one 32nd
        ("UXY", 1 / 64),  # one half of a 32nd, same grid as ZN
        ("WN", 1 / 32),  # one 32nd, same grid as ZB
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
        ("UXY", ["0", "5"]),
        ("WN", ["0"]),
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
        # UXY -- halves of a 32nd, same grid as ZN
        ("UXY", "104-08", 104 + 8 / 32),
        ("UXY", "104-085", 104 + 8.5 / 32),
        ("UXY", "104-08+", 104 + 8.5 / 32),
        # WN -- whole 32nds, same grid as ZB
        ("WN", "132-24", 132 + 24 / 32),
        ("WN", "132-240", 132 + 24 / 32),
        ("WN", "132-31", 132 + 31 / 32),
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
        ("UXY", 104 + 8.5 / 32, "104-08 1/2"),
        ("UXY", 104.25, "104-08"),
        ("WN", 132.75, "132-24"),
        ("WN", 133.0, "133-00"),
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
        # UXY -- halves of a 32nd
        ("UXY", "104-08 1/2", 104 + 8.5 / 32),
        ("UXY", "104-08 \u00bd", 104 + 8.5 / 32),
        # WN -- whole 32nds only (no fractions)
        ("WN", "132-24", 132 + 24 / 32),
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
        ("UXY", "104-085", "104-08 1/2", 104 + 8.5 / 32),
        ("UXY", "104-08+", "104-08 1/2", 104 + 8.5 / 32),
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
        ("UXY", "104-08 1/4"),  # UXY doesn't have quarters either
        ("WN", "132-24 1/2"),  # WN doesn't have fractions either
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


@ pytest.mark.parametrize(
    "code, raw",
    [
        # A half 32nd is not a tick on ZB.
        ("ZB", "118-16+"),
        ("ZB", "118-165"),
        # Nor on WN, which shares ZB's whole-32nd grid.
        ("WN", "132-24+"),
        ("WN", "132-245"),
        # A quarter is not a tick on UXY either, which shares ZN's grid.
        ("UXY", "104-082"),
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


# Codex P2 regression: fraction tick validity must use exact integer arithmetic,
# not float rounding. Test the high-precision counterexample where float
# arithmetic could produce false positives/negatives.
def test_fraction_tick_validity_uses_exact_integer_arithmetic() -> None:
    """Codex P2: exact integer arithmetic for fraction tick divisibility.
    
    The fraction 5/8 on ZT (8 ticks/32nd): 5*8=40, 40%8=0 -> 5 ticks exact.
    The fraction 1/3 on ZT: 1*8=8, 8%3=2 != 0 -> not an exact tick, REJECTED.
    
    Float arithmetic: (5/8)*8 = 5.0 -> is_integer() = True (correct)
    Float arithmetic: (1/3)*8 = 2.666... -> is_integer() = False (correct)
    
    But edge cases with floating point can be problematic:
    - Very large numerators/denominators
    - Fractions that are mathematically exact but float-imprecise
    
    Using integer arithmetic (product % denominator == 0) is exact.
    """
    # Valid fractions that should parse correctly (exact divisibility)
    assert parse_futures_quote("ZT", "102-16 1/8").decimal_price == pytest.approx(102 + 16.125 / 32)
    assert parse_futures_quote("ZT", "102-16 5/8").decimal_price == pytest.approx(102 + 16.625 / 32)
    assert parse_futures_quote("ZF", "108-15 1/4").decimal_price == pytest.approx(108 + 15.25 / 32)
    assert parse_futures_quote("ZF", "108-15 3/4").decimal_price == pytest.approx(108 + 15.75 / 32)
    assert parse_futures_quote("ZN", "112-16 1/2").decimal_price == pytest.approx(112 + 16.5 / 32)


def test_fraction_not_on_tick_grid_is_rejected_exact_integer_check() -> None:
    """Codex P2: fractions not exactly representable on the tick grid are rejected.
    
    These are fractions where numerator * ticks_per_32nd is NOT divisible by denominator.
    This uses exact integer arithmetic (product % denominator != 0), not float.is_integer().
    """
    # ZT: 8 ticks per 32nd. Fractions with denominator not dividing 8*numerator
    with pytest.raises(TreasuryFuturesQuoteError):
        parse_futures_quote("ZT", "102-16 1/3")   # 1*8=8, 8%3=2 != 0
    with pytest.raises(TreasuryFuturesQuoteError):
        parse_futures_quote("ZT", "102-16 1/5")   # 1*8=8, 8%5=3 != 0
    with pytest.raises(TreasuryFuturesQuoteError):
        parse_futures_quote("ZT", "102-16 2/5")   # 2*8=16, 16%5=1 != 0
    
    # ZF: 4 ticks per 32nd
    with pytest.raises(TreasuryFuturesQuoteError):
        parse_futures_quote("ZF", "108-15 1/3")   # 1*4=4, 4%3=1 != 0
    with pytest.raises(TreasuryFuturesQuoteError):
        parse_futures_quote("ZF", "108-15 1/8")   # 1*4=4, 4%8=4 != 0
    
    # ZN: 2 ticks per 32nd
    with pytest.raises(TreasuryFuturesQuoteError):
        parse_futures_quote("ZN", "112-16 1/3")   # 1*2=2, 2%3=2 != 0
    with pytest.raises(TreasuryFuturesQuoteError):
        parse_futures_quote("ZN", "112-16 1/4")   # 1*2=2, 2%4=2 != 0


def test_high_precision_fraction_counterexample() -> None:
    """Codex P2: High-precision counterexample where float arithmetic fails.
    
    Consider a fraction like 123456789/987654321 on a contract with 8 ticks/32nd.
    Float: (123456789/987654321) * 8 = 1.000000008... might incorrectly round.
    Integer: 123456789 * 8 = 987654312, 987654312 % 987654321 != 0 -> REJECTED (correct).
    
    This test uses fractions that are mathematically NOT on the tick grid but
    could be misclassified by float.is_integer() due to floating-point precision.
    """
    # Large numbers where float precision could be an issue
    # 1/3 is the classic case: float(1/3)*8 = 2.6666666666666665
    # is_integer() correctly returns False, but we test integer arithmetic explicitly
    
    # Edge case: fraction that equals exactly an integer in decimal but 
    # not an exact multiple of the tick grid
    # E.g., on ZN (2 ticks/32nd), 2/3 of a 32nd = 1.333... ticks
    with pytest.raises(TreasuryFuturesQuoteError):
        parse_futures_quote("ZN", "112-16 2/3")
    
    # Another edge case: very close to a tick but not exact
    # 3/2 = 1.5 -> on ZF (4 ticks/32nd): 1.5*4 = 6 ticks -> exact!
    # But 3/2 is not a standard fraction
    # Actually 3/2 of a 32nd = 1.5 32nds = 1 32nd + 16 ticks = way out of range
    
    # Test that 1/6 on ZT is rejected (1*8=8, 8%6=2 != 0)
    with pytest.raises(TreasuryFuturesQuoteError):
        parse_futures_quote("ZT", "102-16 1/6")
    
    # Test that 5/6 on ZT is rejected (5*8=40, 40%6=4 != 0)
    with pytest.raises(TreasuryFuturesQuoteError):
        parse_futures_quote("ZT", "102-16 5/6")


def test_all_valid_fractions_are_exactly_representable_as_integers() -> None:
    """Verify every valid fraction for each contract maps to an exact integer tick count.
    
    This is a completeness test: for each contract, every fraction in the 
    _TICK_FRACTION_TO_DESK mapping must satisfy exact integer divisibility.
    """
    from shiori_pricing_lab.pricing.treasury_futures_contract import _TICK_FRACTION_TO_DESK
    
    for (ticks_per_32nd, sub_ticks), fraction_str in _TICK_FRACTION_TO_DESK.items():
        num_str, den_str = fraction_str.split("/")
        numerator = int(num_str)
        denominator = int(den_str)
        
        # Exact integer check
        product = numerator * ticks_per_32nd
        assert product % denominator == 0, (
            f"Fraction {fraction_str} for {ticks_per_32nd} ticks/32nd "
            f"fails exact integer divisibility: {product} % {denominator} = {product % denominator}"
        )
        sub_ticks_calc = product // denominator
        assert sub_ticks_calc == sub_ticks, (
            f"Fraction {fraction_str} gives {sub_ticks_calc} ticks, expected {sub_ticks}"
        )


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
