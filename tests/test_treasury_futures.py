from datetime import date

import pytest

from shiori_pricing_lab.pricing.treasury_futures import (
    bond_clean_price_from_yield,
    clean_price_from_futures,
    format_treasury_futures_price,
    futures_price_from_target_yield,
    parse_treasury_futures_price,
    yield_from_bond_clean_price,
    yield_from_futures_price,
)


def test_parse_treasury_futures_price_formats():
    assert parse_treasury_futures_price("110-16") == pytest.approx(110.5)
    assert parse_treasury_futures_price("110-16+") == pytest.approx(110.515625)
    assert parse_treasury_futures_price("110-165") == pytest.approx(110.515625)
    assert parse_treasury_futures_price("110-16.5") == pytest.approx(110.515625)
    assert parse_treasury_futures_price(110.5) == pytest.approx(110.5)


def test_format_treasury_futures_price():
    assert format_treasury_futures_price(110.5) == "110-16"
    assert format_treasury_futures_price(110.515625) == "110-16+"


def test_clean_price_from_futures():
    assert clean_price_from_futures("110-16", 0.9012) == pytest.approx(99.5826)


def test_bond_yield_roundtrip():
    settlement = date(2026, 6, 30)
    maturity = date(2034, 5, 15)
    coupon = 0.04
    target_yield = 0.0435

    clean_price = bond_clean_price_from_yield(target_yield, settlement, maturity, coupon)
    solved_yield = yield_from_bond_clean_price(clean_price, settlement, maturity, coupon)

    assert solved_yield == pytest.approx(target_yield, abs=1e-8)


def test_futures_yield_roundtrip():
    settlement = date(2026, 6, 30)
    maturity = date(2034, 5, 15)
    coupon = 0.04
    conversion_factor = 0.9012
    target_yield = 0.0435

    futures_price = futures_price_from_target_yield(
        target_yield,
        conversion_factor,
        settlement,
        maturity,
        coupon,
    )
    solved_yield = yield_from_futures_price(
        futures_price,
        conversion_factor,
        settlement,
        maturity,
        coupon,
    )

    assert solved_yield == pytest.approx(target_yield, abs=1e-8)
