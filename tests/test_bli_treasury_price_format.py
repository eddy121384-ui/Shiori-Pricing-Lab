"""Tests for `pricing/bli_treasury_price_format.py` (Issue #173/#174).

Same worked examples `test_ust_s490_repo_carry_forward_parity.py` already
pins for the tool's own re-export, checked directly against this shared
module -- the single source of truth both the CLI tool and the Workbench
server route now import.
"""

from __future__ import annotations

import pytest

from shiori_pricing_lab.pricing.bli_treasury_price_format import (
    TREASURY_32NDS_PER_POINT,
    format_price_as_treasury_fraction,
)


@pytest.mark.parametrize(
    "decimal_price, expected",
    [
        (98.5, "98-16"),
        (98.515625, "98-16+"),
        (99.1015625, "99-032"),
        (99.12109375, "99-037"),
        (100.0, "100-00"),
        (99.03125, "99-01"),
    ],
)
def test_a_price_renders_as_the_same_treasury_quote_the_page_already_parses(
    decimal_price, expected
):
    # The exact inverse of script.js::parseTreasuryQuote's own worked
    # examples (Issue #161).
    assert format_price_as_treasury_fraction(decimal_price) == expected


def test_an_arbitrary_decimal_is_rounded_to_the_nearest_eighth_of_a_32nd():
    assert format_price_as_treasury_fraction(98.5 + 0.0001) == "98-16"
    assert format_price_as_treasury_fraction(98.5 + 1 / 256) == "98-161"


def test_a_negative_price_keeps_its_sign_on_the_magnitude():
    assert format_price_as_treasury_fraction(-98.5) == "-98-16"


def test_the_32nds_constant_matches_the_conversion_used_for_ticks():
    assert TREASURY_32NDS_PER_POINT == 32
