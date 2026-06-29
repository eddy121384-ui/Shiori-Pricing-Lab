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
    with pytest.raises(ValueError, match="must be a YYYY-MM-DD date string"):
        _irs(maturity_date="07/01/2031")


def test_compact_date_rejected():
    # date.fromisoformat accepts "20260701" on 3.11+, but a product must store
    # the explicit dash-separated form.
    with pytest.raises(ValueError, match="must be a YYYY-MM-DD date string"):
        _irs(effective_date="20260701")


def test_iso_week_date_rejected():
    # ISO week dates (e.g. 2026-W27-3) are also accepted by fromisoformat and
    # must be rejected.
    with pytest.raises(ValueError, match="must be a YYYY-MM-DD date string"):
        _irs(effective_date="2026-W27-3")


def test_impossible_calendar_date_rejected():
    # Right shape, but not a real calendar date.
    with pytest.raises(ValueError, match="must be a YYYY-MM-DD date string"):
        _irs(maturity_date="2031-13-40")


def test_dates_round_trip_unchanged_through_asdict():
    irs = _irs(effective_date="2026-07-01", maturity_date="2031-07-01")
    data = asdict(irs)

    assert data["effective_date"] == "2026-07-01"
    assert data["maturity_date"] == "2031-07-01"


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


def _ois_floating(reset_frequency):
    """OIS floating leg helper that varies only the reset frequency."""

    return FloatingLeg(
        pay_receive=PayReceive.PAY,
        index=FloatingIndex.EUR_ESTR,
        spread=0.0,
        payment_frequency=Frequency.ANNUAL,
        day_count=DayCount.ACT_360,
        reset_frequency=reset_frequency,
        compounding_method=CompoundingMethod.DAILY_COMPOUNDED,
    )


@pytest.mark.parametrize("reset", [None, Frequency.DAILY])
def test_ois_allows_none_or_daily_reset(reset):
    ois = _ois(floating_leg=_ois_floating(reset))
    assert ois.floating_leg.reset_frequency is reset


@pytest.mark.parametrize(
    "reset",
    [Frequency.MONTHLY, Frequency.QUARTERLY, Frequency.SEMI_ANNUAL, Frequency.ANNUAL],
)
def test_ois_rejects_non_daily_reset(reset):
    with pytest.raises(ValueError, match="reset_frequency must be None or Frequency.DAILY"):
        _ois(floating_leg=_ois_floating(reset))


# --- product_type is fixed and non-overridable -------------------------------


def test_product_type_is_not_a_constructor_argument():
    # product_type is a fixed discriminator, not an init field.
    with pytest.raises(TypeError):
        _irs(product_type="OIS")
    with pytest.raises(TypeError):
        _ois(product_type="IRS")


def test_product_type_always_matches_the_class():
    assert _irs().product_type == "IRS"
    assert _ois().product_type == "OIS"
    assert asdict(_irs())["product_type"] == "IRS"
    assert asdict(_ois())["product_type"] == "OIS"


# --- enum-backed fields are coerced / validated at runtime -------------------


def test_valid_raw_strings_are_coerced_to_enum_members():
    # Raw strings that match an enum value are accepted and stored as members.
    irs = _irs(
        currency="GBP",
        business_day_convention="FOLLOWING",
        fixed_leg=FixedLeg(
            pay_receive="PAY",
            fixed_rate=0.03,
            payment_frequency="ANNUAL",
            day_count="ACT_365_FIXED",
        ),
        floating_leg=FloatingLeg(
            pay_receive="RECEIVE",
            index="GBP_SONIA",
            spread=0.0,
            payment_frequency="QUARTERLY",
            day_count="ACT_365_FIXED",
            reset_frequency="QUARTERLY",
        ),
    )

    assert irs.currency is Currency.GBP
    assert irs.business_day_convention is BusinessDayConvention.FOLLOWING
    assert irs.fixed_leg.pay_receive is PayReceive.PAY
    assert irs.floating_leg.index is FloatingIndex.GBP_SONIA
    assert irs.floating_leg.reset_frequency is Frequency.QUARTERLY


def test_invalid_currency_string_rejected():
    with pytest.raises(ValueError, match="currency must be one of"):
        _irs(currency="ZZZ")


def test_invalid_business_day_convention_string_rejected():
    with pytest.raises(ValueError, match="business_day_convention must be one of"):
        _irs(business_day_convention="ROLL_SOMEHOW")


def test_invalid_pay_receive_string_rejected():
    with pytest.raises(ValueError, match="pay_receive must be one of"):
        FixedLeg(
            pay_receive="BUY",
            fixed_rate=0.03,
            payment_frequency=Frequency.ANNUAL,
            day_count=DayCount.ACT_360,
        )


def test_invalid_payment_frequency_string_rejected():
    with pytest.raises(ValueError, match="payment_frequency must be one of"):
        FixedLeg(
            pay_receive=PayReceive.PAY,
            fixed_rate=0.03,
            payment_frequency="BIWEEKLY",
            day_count=DayCount.ACT_360,
        )


def test_invalid_day_count_string_rejected():
    with pytest.raises(ValueError, match="day_count must be one of"):
        FixedLeg(
            pay_receive=PayReceive.PAY,
            fixed_rate=0.03,
            payment_frequency=Frequency.ANNUAL,
            day_count="ACT_999",
        )


def test_invalid_floating_index_string_rejected():
    with pytest.raises(ValueError, match="index must be one of"):
        FloatingLeg(
            pay_receive=PayReceive.RECEIVE,
            index="USD_FAKE",
            spread=0.0,
            payment_frequency=Frequency.QUARTERLY,
            day_count=DayCount.ACT_360,
            reset_frequency=Frequency.QUARTERLY,
        )


def test_invalid_compounding_method_string_rejected():
    with pytest.raises(ValueError, match="compounding_method must be one of"):
        FloatingLeg(
            pay_receive=PayReceive.PAY,
            index=FloatingIndex.EUR_ESTR,
            spread=0.0,
            payment_frequency=Frequency.ANNUAL,
            day_count=DayCount.ACT_360,
            compounding_method="ROLLED_UP",
        )


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_enum_like_values_rejected(blank):
    with pytest.raises(ValueError, match="currency must not be blank"):
        _irs(currency=blank)
    with pytest.raises(ValueError, match="pay_receive must not be blank"):
        FixedLeg(
            pay_receive=blank,
            fixed_rate=0.03,
            payment_frequency=Frequency.ANNUAL,
            day_count=DayCount.ACT_360,
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
