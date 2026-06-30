"""Tests for the minimal USD-only IRS reference engine (Issue #27)."""

from dataclasses import asdict
from types import SimpleNamespace

import pandas as pd
import pytest

from shiori_pricing_lab.data.snapshot import MarketDataSnapshot
from shiori_pricing_lab.pricing import PricingErrorCode, PricingStatus, price
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
from shiori_pricing_lab.valuation.context import ValuationContext

_VALUATION_DATE = "2026-06-29"


def _snapshot(rows=None):
    if rows is None:
        rows = [
            {
                "date": _VALUATION_DATE,
                "ticker": "USD_SYN_6M",
                "tenor": "6M",
                "value": 0.04,
                "data_type": "zero_rate",
                "source": "synthetic",
            },
            {
                "date": _VALUATION_DATE,
                "ticker": "USD_SYN_1Y",
                "tenor": "1Y",
                "value": 0.042,
                "data_type": "zero_rate",
                "source": "synthetic",
            },
        ]
    return MarketDataSnapshot.from_rates_points(
        pd.DataFrame(rows), valuation_date=_VALUATION_DATE, source="synthetic"
    )


def _ctx(snapshot):
    return ValuationContext.from_snapshot(snapshot, reporting_currency="USD")


def _irs(**overrides):
    params = dict(
        product_id="IRS-USD-1Y",
        effective_date="2026-07-01",
        maturity_date="2027-07-01",
        currency=Currency.USD,
        notional=1_000_000.0,
        fixed_leg=FixedLeg(
            pay_receive=PayReceive.PAY,
            fixed_rate=0.04,
            payment_frequency=Frequency.SEMI_ANNUAL,
            day_count=DayCount.ACT_365_FIXED,
        ),
        floating_leg=FloatingLeg(
            pay_receive=PayReceive.RECEIVE,
            index=FloatingIndex.USD_SOFR_TERM_3M,
            spread=0.0,
            payment_frequency=Frequency.QUARTERLY,
            day_count=DayCount.ACT_360,
            reset_frequency=Frequency.QUARTERLY,
        ),
        business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
    )
    params.update(overrides)
    return InterestRateSwap(**params)


def test_usd_irs_happy_path_returns_pinned_deterministic_pv():
    snap = _snapshot()
    result = price(_irs(), _ctx(snap), snap)

    assert result.status is PricingStatus.SUCCESS
    assert result.pv == pytest.approx(1506.7928142153469, abs=1e-9)
    assert result.dv01 is None
    assert result.cashflows is None
    assert result.assumptions["curve_usage"] == "single curve used for both discount and forecast"
    assert result.assumptions["business_day_adjustment_applied"] is False
    assert result.diagnostics["fixed_period_count"] == 2
    assert result.diagnostics["floating_period_count"] == 4


def test_usd_irs_repeated_runs_produce_same_pv():
    snap = _snapshot()
    product = _irs()
    ctx = _ctx(snap)

    first = price(product, ctx, snap)
    second = price(product, ctx, snap)

    assert first.status is PricingStatus.SUCCESS
    assert second.status is PricingStatus.SUCCESS
    assert first.pv == second.pv


def test_unsupported_product_still_returns_unsupported_product():
    snap = _snapshot()
    product = OvernightIndexedSwap(
        product_id="OIS-1",
        effective_date="2026-07-01",
        maturity_date="2027-07-01",
        currency=Currency.USD,
        notional=1_000_000.0,
        fixed_leg=FixedLeg(
            pay_receive=PayReceive.RECEIVE,
            fixed_rate=0.04,
            payment_frequency=Frequency.ANNUAL,
            day_count=DayCount.ACT_360,
        ),
        floating_leg=FloatingLeg(
            pay_receive=PayReceive.PAY,
            index=FloatingIndex.USD_SOFR,
            spread=0.0,
            payment_frequency=Frequency.ANNUAL,
            day_count=DayCount.ACT_360,
            reset_frequency=Frequency.DAILY,
            compounding_method=CompoundingMethod.DAILY_COMPOUNDED,
        ),
        business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
    )

    result = price(product, _ctx(snap), snap)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.UNSUPPORTED_PRODUCT
    assert result.pv is None


@pytest.mark.parametrize(
    "snapshot",
    [
        SimpleNamespace(valuation_date=_VALUATION_DATE, rates_points=pd.DataFrame()),
        SimpleNamespace(
            valuation_date=_VALUATION_DATE,
            rates_points=pd.DataFrame(
                [
                    {
                        "date": _VALUATION_DATE,
                        "ticker": "BAD",
                        "tenor": "BAD_TENOR",
                        "value": 0.04,
                        "data_type": "zero_rate",
                        "source": "synthetic",
                    }
                ]
            ),
        ),
    ],
)
def test_missing_empty_unusable_market_data_returns_failed(snapshot):
    ctx = SimpleNamespace(
        valuation_date=_VALUATION_DATE,
        reporting_currency="USD",
        market_snapshot=snapshot,
    )

    result = price(_irs(), ctx, snapshot)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.MISSING_MARKET_DATA
    assert result.pv is None


def test_non_usd_irs_fails_before_curve_construction():
    snapshot = SimpleNamespace(
        valuation_date=_VALUATION_DATE,
        rates_points=property(lambda _: pytest.fail("curve should not be built")),
    )
    ctx = SimpleNamespace(
        valuation_date=_VALUATION_DATE,
        reporting_currency="USD",
        market_snapshot=snapshot,
    )

    result = price(_irs(currency=Currency.EUR), ctx, snapshot)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.INVALID_PRODUCT
    assert result.errors[0].detail == {"currency": "EUR"}
    assert result.pv is None


def test_unsupported_day_count_returns_structured_failed():
    snap = _snapshot()
    product = _irs(
        fixed_leg=FixedLeg(
            pay_receive=PayReceive.PAY,
            fixed_rate=0.04,
            payment_frequency=Frequency.SEMI_ANNUAL,
            day_count=DayCount.THIRTY_360,
        )
    )

    result = price(product, _ctx(snap), snap)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.INVALID_PRODUCT
    assert result.pv is None


def test_unsupported_frequency_returns_structured_failed():
    snap = _snapshot()
    product = _irs(
        floating_leg=FloatingLeg(
            pay_receive=PayReceive.RECEIVE,
            index=FloatingIndex.USD_SOFR_TERM_3M,
            spread=0.0,
            payment_frequency=Frequency.DAILY,
            day_count=DayCount.ACT_360,
            reset_frequency=Frequency.DAILY,
        )
    )

    result = price(product, _ctx(snap), snap)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.INVALID_PRODUCT
    assert result.pv is None


def test_non_clean_schedule_returns_structured_failed():
    snap = _snapshot()
    result = price(_irs(maturity_date="2027-08-01"), _ctx(snap), snap)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.INVALID_PRODUCT
    assert result.pv is None


def test_pricing_inputs_are_not_mutated():
    snap = _snapshot()
    ctx = _ctx(snap)
    product = _irs()
    before_product = asdict(product)
    before_ctx_metadata = dict(ctx.metadata)
    before_ctx_model_settings = dict(ctx.model_settings)
    before_snapshot = snap.rates_points.copy(deep=True)

    result = price(product, ctx, snap)

    assert result.status is PricingStatus.SUCCESS
    assert asdict(product) == before_product
    assert ctx.market_snapshot is snap
    assert ctx.metadata == before_ctx_metadata
    assert ctx.model_settings == before_ctx_model_settings
    pd.testing.assert_frame_equal(snap.rates_points, before_snapshot)


def test_failed_paths_never_return_fake_zero_pv():
    snap = _snapshot()
    failures = [
        price(_irs(currency=Currency.EUR), _ctx(snap), snap),
        price(_irs(maturity_date="2027-08-01"), _ctx(snap), snap),
    ]

    assert all(result.status is PricingStatus.FAILED for result in failures)
    assert all(result.pv is None for result in failures)


def test_pricing_modules_have_no_provider_csv_bloomberg_or_web_imports():
    from pathlib import Path

    import shiori_pricing_lab.pricing as pricing_pkg

    pricing_dir = Path(pricing_pkg.__file__).parent
    forbidden_fragments = (
        "shiori_pricing_lab.data.providers",
        "ManualMarketDataProvider",
        "CSVMarketDataProvider",
        "read_csv",
        "bloomberg",
        "requests",
        "urllib",
    )
    for path in sorted(pricing_dir.glob("*.py")):
        text = path.read_text()
        for fragment in forbidden_fragments:
            assert fragment not in text, f"{path.name} contains forbidden import/use {fragment}"
