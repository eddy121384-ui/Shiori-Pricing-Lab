"""Tests for the vanilla rates product schemas (IRS / OIS).

These are schema-construction and validation tests only. No pricing, market
data, or valuation date is involved; products are pure deal-term objects.
"""

from dataclasses import asdict

import pytest

from shiori_pricing_lab.products import (
    BusinessDayConvention,
    CompoundingMethod,
    Currency,
    DayCount,
    FixedLeg,
    FloatingIndex,
    FloatingLeg,
    Frequency,
    InterestRateSwap,
    OvernightIndexedSwap,
    PayReceive,
)


def _irs(**overrides) -> InterestRateSwap:
    """Build a synthetic, valid USD IRS (pay fixed / receive floating)."""

    params = dict(
        product_id="IRS-0001",
        effective_date="2026-07-01",
        maturity_date="2031-07-01",
        currency=Currency.USD,
        notional=10_000_000.0,
        fixed_leg=FixedLeg(
            pay_receive=PayReceive.PAY,
            fixed_rate=0.0375,
            payment_frequency=Frequency.SEMI_ANNUAL,
            day_count=DayCount.THIRTY_360,
        ),
        floating_leg=FloatingLeg(
            pay_receive=PayReceive.RECEIVE,
            index=FloatingIndex.USD_SOFR_TERM_3M,
            spread=0.0010,
            payment_frequency=Frequency.QUARTERLY,
            day_count=DayCount.ACT_360,
            reset_frequency=Frequency.QUARTERLY,
        ),
        business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
    )
    params.update(overrides)
    return InterestRateSwap(**params)


def _ois(**overrides) -> OvernightIndexedSwap:
    """Build a synthetic, valid EUR OIS (receive fixed / pay floating)."""

    params = dict(
        product_id="OIS-0001",
        effective_date="2026-07-01",
        maturity_date="2027-07-01",
        currency=Currency.EUR,
        notional=25_000_000.0,
        fixed_leg=FixedLeg(
            pay_receive=PayReceive.RECEIVE,
            fixed_rate=0.0285,
            payment_frequency=Frequency.ANNUAL,
            day_count=DayCount.ACT_360,
        ),
        floating_leg=FloatingLeg(
            pay_receive=PayReceive.PAY,
            index=FloatingIndex.EUR_ESTR,
            spread=0.0,
            payment_frequency=Frequency.ANNUAL,
            day_count=DayCount.ACT_360,
            compounding_method=CompoundingMethod.DAILY_COMPOUNDED,
        ),
        business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
    )
    params.update(overrides)
    return OvernightIndexedSwap(**params)


# --- Valid construction ------------------------------------------------------


def test_valid_irs_construction():
    irs = _irs()

    assert irs.product_type == "IRS"
    assert irs.currency is Currency.USD
    assert irs.notional == 10_000_000.0
    assert irs.pay_receive_fixed is PayReceive.PAY
    assert irs.fixed_leg.pay_receive.opposite() is irs.floating_leg.pay_receive


def test_valid_ois_construction():
    ois = _ois()

    assert ois.product_type == "OIS"
    assert ois.currency is Currency.EUR
    assert ois.pay_receive_fixed is PayReceive.RECEIVE
    assert ois.floating_leg.compounding_method is CompoundingMethod.DAILY_COMPOUNDED


def test_fixed_rate_and_spread_may_be_zero_or_negative():
    # Rates legitimately trade through zero; the schema must allow it.
    irs = _irs(
        fixed_leg=FixedLeg(
            pay_receive=PayReceive.PAY,
            fixed_rate=-0.0005,
            payment_frequency=Frequency.SEMI_ANNUAL,
            day_count=DayCount.THIRTY_360,
        ),
        floating_leg=FloatingLeg(
            pay_receive=PayReceive.RECEIVE,
            index=FloatingIndex.USD_SOFR_TERM_3M,
            spread=-0.0025,
            payment_frequency=Frequency.QUARTERLY,
            day_count=DayCount.ACT_360,
            reset_frequency=Frequency.QUARTERLY,
        ),
    )

    assert irs.fixed_leg.fixed_rate == -0.0005
    assert irs.floating_leg.spread == -0.0025


# --- Validation rejections ---------------------------------------------------


@pytest.mark.parametrize("bad_notional", [0.0, -1.0, -10_000_000.0])
def test_invalid_notional_rejected(bad_notional):
    with pytest.raises(ValueError, match="notional must be positive"):
        _irs(notional=bad_notional)


def test_invalid_date_order_rejected():
    with pytest.raises(ValueError, match="must be after effective_date"):
        _irs(effective_date="2031-07-01", maturity_date="2026-07-01")


def test_equal_dates_rejected():
    with pytest.raises(ValueError, match="must be after effective_date"):
        _irs(effective_date="2026-07-01", maturity_date="2026-07-01")


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_product_id_rejected(blank):
    with pytest.raises(ValueError, match="product_id must be a non-blank string"):
        _irs(product_id=blank)


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_effective_date_rejected(blank):
    with pytest.raises(ValueError, match="effective_date must be a non-blank string"):
        _irs(effective_date=blank)


def test_non_iso_date_rejected():
    with pytest.raises(ValueError, match="must be an ISO date"):
        _irs(maturity_date="07/01/2031")


def test_same_direction_legs_rejected():
    with pytest.raises(ValueError, match="opposite pay/receive directions"):
        _irs(
            floating_leg=FloatingLeg(
                pay_receive=PayReceive.PAY,  # same as fixed leg -> invalid
                index=FloatingIndex.USD_SOFR_TERM_3M,
                spread=0.0010,
                payment_frequency=Frequency.QUARTERLY,
                day_count=DayCount.ACT_360,
                reset_frequency=Frequency.QUARTERLY,
            )
        )


def test_irs_requires_floating_reset_frequency():
    with pytest.raises(ValueError, match="reset_frequency is required"):
        _irs(
            floating_leg=FloatingLeg(
                pay_receive=PayReceive.RECEIVE,
                index=FloatingIndex.USD_SOFR_TERM_3M,
                spread=0.0010,
                payment_frequency=Frequency.QUARTERLY,
                day_count=DayCount.ACT_360,
                reset_frequency=None,
            )
        )


def test_ois_requires_real_compounding_method():
    with pytest.raises(ValueError, match="compounding_method must be"):
        _ois(
            floating_leg=FloatingLeg(
                pay_receive=PayReceive.PAY,
                index=FloatingIndex.EUR_ESTR,
                spread=0.0,
                payment_frequency=Frequency.ANNUAL,
                day_count=DayCount.ACT_360,
                compounding_method=CompoundingMethod.NONE,
            )
        )


# --- Serialization -----------------------------------------------------------


def test_irs_serializes_via_asdict():
    irs = _irs()
    data = asdict(irs)

    assert data["product_id"] == "IRS-0001"
    assert data["product_type"] == "IRS"
    # str-valued enums serialize to plain strings, so the dict is JSON-friendly.
    assert data["currency"] == "USD"
    assert data["fixed_leg"]["pay_receive"] == "PAY"
    assert data["floating_leg"]["index"] == "USD_SOFR_TERM_3M"

    import json

    # Round-trips through JSON without a custom encoder.
    assert json.loads(json.dumps(data))["notional"] == 10_000_000.0


def test_ois_serializes_via_asdict():
    ois = _ois()
    data = asdict(ois)

    assert data["product_type"] == "OIS"
    assert data["floating_leg"]["compounding_method"] == "DAILY_COMPOUNDED"


# --- Layering / boundary guard ----------------------------------------------


def test_products_do_not_import_data_pricing_or_valuation():
    import importlib
    import sys

    forbidden_prefixes = (
        "shiori_pricing_lab.data",
        "shiori_pricing_lab.pricing",
        "shiori_pricing_lab.valuation",
    )

    # Drop the products package and the forbidden layers, then import products
    # fresh and confirm none of the forbidden layers got pulled in.
    for name in list(sys.modules):
        if name.startswith("shiori_pricing_lab.products") or name.startswith(
            forbidden_prefixes
        ):
            del sys.modules[name]

    importlib.import_module("shiori_pricing_lab.products")

    assert not any(name.startswith(forbidden_prefixes) for name in sys.modules)
