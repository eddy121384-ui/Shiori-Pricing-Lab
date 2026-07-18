"""Deterministic anonymized OVME golden case (Issue #94, section J).

One sanitized synthetic golden case proving the standalone European
price-based cash-settled fair-premium path reproduces the Eddy-approved
Bloomberg (OVME) methodology end to end: an explicit forward clean price,
dirty forward / dirty strike in Black-76, fractional-timestamp ACT/ACT option
time, and an Option Discount Curve discount factor to option settlement
divided by the factor to the reporting date.

**Anonymized. Not a Bloomberg record.** No real ISIN/CUSIP, issuer name,
security description, screenshot, licensed raw record, or vendor source string
appears here -- only placeholder identifiers
(``SANITIZED_OVME_GOLDEN_OPTION`` / ``SANITIZED_OVME_GOLDEN_BOND`` / source
``SANITIZED_LICENSED_TERMINAL_OBSERVATION``). This verifies one sanitized case
only; it does not claim universal Bloomberg replication.

The two Option Discount Curve targets are expressed as exact day-tenor
continuous-zero nodes (Issue #94 day-tenor support): 3D to the reporting date
(2026-07-20, 3 days after valuation) and 94D to the option settlement date
(2026-10-19, 94 days after valuation).
"""

from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from shiori_pricing_lab.data.bli_benchmark_quote import (
    BLIBenchmarkQuote,
    BLIBenchmarkQuoteSide,
    BLIBenchmarkSourceType,
)
from shiori_pricing_lab.data.bli_snapshot import (
    BLIBondQuote,
    BLICreditSpreadInput,
    BLICreditSpreadTreatment,
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIForwardCleanPriceInput,
    BLIMarketDataSnapshot,
    BLIMarketDataStatus,
    BLIQuoteBasis,
    BLIVolatilityBasis,
    BLIVolatilityInput,
)
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.pricing.bli_black76_price_option import (
    black76_price_option_pv_per_100,
)
from shiori_pricing_lab.pricing.bli_implied_price_vol_calibration import (
    BLIImpliedPriceVolCalibrationReason,
    BLIImpliedPriceVolCalibrationStatus,
    calibrate_bli_implied_price_vol,
)
from shiori_pricing_lab.pricing.bli_pricing_engine import price_bli_mvp_standalone_option
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available
from shiori_pricing_lab.pricing.bli_valuation_time import year_fraction_to_expiry
from shiori_pricing_lab.pricing.result import PricingStatus
from shiori_pricing_lab.products.bond_option import BondOption
from shiori_pricing_lab.products.enums import (
    BusinessDayConvention,
    Currency,
    ExerciseStyle,
    Frequency,
    OptionType,
    PayoffBasis,
    Position,
    SettlementType,
    TreasuryFTPQuoteSide,
)
from shiori_pricing_lab.reference_data.bond_reference_data import (
    BondReferenceData,
    BondStatus,
    BondType,
    BondYieldConvention,
    DayCount,
)

_requires_quantlib = pytest.mark.skipif(
    not is_quantlib_available(), reason="QuantLib is not installed in this environment"
)

_SOURCE = "SANITIZED_LICENSED_TERMINAL_OBSERVATION"

# --- Approved golden inputs (Issue #94 section J) ----------------------------

_CLEAN_STRIKE = 99.44921875
_CLEAN_FORWARD = 99.3671875
_PRICE_VOL = 0.03314
_NOTIONAL = 1000.0

# --- Approved expected intermediates + outputs -------------------------------

_EXPECTED_OPTION_DAYS = 90.64027777777778
_EXPECTED_ACT_ACT = 0.2483295281582953
_EXPECTED_ACCRUED = 1.2442255434782605
_EXPECTED_DIRTY_FORWARD = 100.61141304347827
_EXPECTED_DIRTY_STRIKE = 100.69344429347827
_EXPECTED_DF_REPORTING = 0.9996982422463937
_EXPECTED_DF_OPTION_SETTLEMENT = 0.9869442900526999
_EXPECTED_EFFECTIVE_DF = 0.987242198040646
_EXPECTED_PREMIUM_PER_100 = 0.6149706611572777
_EXPECTED_TOTAL_PREMIUM = 6.149706611572777
_EXPECTED_PREMIUM_OVER_STRIKE_PCT = 0.6183765633224522

# Bloomberg display bucket for 00-19 5/8 (32nds), section J.
_DISPLAY_BUCKET_LOW = 0.611328125
_DISPLAY_BUCKET_HIGH = 0.615234375


def _golden_request() -> BLIStandaloneBondOptionRequest:
    bond = BondReferenceData(
        isin="SANITIZED_OVME_GOLDEN_BOND",
        issuer="SANITIZED OVME GOLDEN ISSUER",
        currency=Currency.USD,
        coupon=0.04125,
        coupon_frequency=Frequency.SEMI_ANNUAL,
        maturity_date="2031-06-30",
        issue_date="2026-06-30",
        day_count=DayCount.ACT_ACT_ISDA,
        business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
        redemption_amount=100.0,
        callable_flag=False,
        sinkable_flag=False,
        bond_type=BondType.FIXED_COUPON_BULLET,
        yield_convention=BondYieldConvention.SEMI_ANNUAL_COMPOUND,
        ex_dividend_days=0,
        first_coupon_date="2026-12-31",
        last_coupon_date="2030-12-31",
        status=BondStatus.ACTIVE,
    )
    option = BondOption(
        product_id="SANITIZED_OVME_GOLDEN_OPTION",
        underlying_isin="SANITIZED_OVME_GOLDEN_BOND",
        currency=Currency.USD,
        payoff_basis=PayoffBasis.PRICE,
        option_type=OptionType.CALL,
        exercise_style=ExerciseStyle.EUROPEAN,
        settlement_type=SettlementType.CASH,
        settlement_lag_days=2,
        expiry_date="2026-10-16",
        notional=_NOTIONAL,
        position=Position.BUY,
        strike_price=_CLEAN_STRIKE,
    )
    common = dict(
        currency=Currency.USD,
        rate_basis=BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
        source_system=_SOURCE,
        status=BLIMarketDataStatus.ACTIVE,
    )
    option_discount_curve = (
        BLICurvePoint(
            curve_id="SANITIZED_OVME_OPTION_DISCOUNT_CURVE",
            curve_name="SANITIZED_OVME_OPTION_DISCOUNT_CURVE",
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            tenor="3D",
            rate=0.03671940048267599,
            **common,
        ),
        BLICurvePoint(
            curve_id="SANITIZED_OVME_OPTION_DISCOUNT_CURVE",
            curve_name="SANITIZED_OVME_OPTION_DISCOUNT_CURVE",
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            tenor="94D",
            rate=0.05102888269044335,
            **common,
        ),
    )
    snapshot = BLIMarketDataSnapshot(
        valuation_date="2026-07-17",
        as_of_timestamp="2026-07-17",
        source_system=_SOURCE,
        snapshot_id="SANITIZED_OVME_GOLDEN_SNAPSHOT",
        status=BLIMarketDataStatus.ACTIVE,
        bond_quote=BLIBondQuote(
            isin="SANITIZED_OVME_GOLDEN_BOND",
            currency=Currency.USD,
            price_type=BLIQuoteBasis.PRICE,
            quote_side=TreasuryFTPQuoteSide.MID,
            source_system=_SOURCE,
            status=BLIMarketDataStatus.ACTIVE,
            clean_price_per_100=_CLEAN_FORWARD,
            accrued_interest_per_100=0.20,
        ),
        forward_clean_price_input=BLIForwardCleanPriceInput(
            forward_clean_price_per_100=_CLEAN_FORWARD,
            quote_side=TreasuryFTPQuoteSide.MID,
            source_system=_SOURCE,
            status=BLIMarketDataStatus.ACTIVE,
        ),
        curve_points=option_discount_curve,
        volatility_input=BLIVolatilityInput(
            volatility=_PRICE_VOL,
            volatility_basis=BLIVolatilityBasis.PRICE_VOL,
            source_system=_SOURCE,
            status=BLIMarketDataStatus.ACTIVE,
        ),
        credit_spread_input=BLICreditSpreadInput(
            spread_treatment=BLICreditSpreadTreatment.OBSERVED,
            source_system=_SOURCE,
            status=BLIMarketDataStatus.ACTIVE,
            credit_spread=0.0125,
            credit_spread_basis="BPS_OVER_USD_BOND_REFERENCE_CURVE",
        ),
        deposit_rate_observation=None,
    )
    return BLIStandaloneBondOptionRequest(
        bond_option=option,
        resolved_bond_reference_data=bond,
        valuation_date="2026-07-17",
        market_data_snapshot=snapshot,
        pricing_timestamp="2026-07-17T13:58:00+08:00",
        expiry_timestamp="2026-10-16T05:20:00+08:00",
        reporting_date="2026-07-20",
        forward_settlement_date="2026-10-19",
        option_settlement_date="2026-10-19",
    )


@_requires_quantlib
def test_ovme_golden_case_reproduces_every_intermediate_and_output():
    result = price_bli_mvp_standalone_option(_golden_request())

    assert result.status is PricingStatus.SUCCESS
    a = result.assumptions

    # Deterministic intermediates (tight tolerance: ACT/365F, clean-price
    # Black, or expiry-only discounting would each miss these).
    assert a["time_to_expiry_year_fraction"] == pytest.approx(_EXPECTED_ACT_ACT, abs=1e-12)
    assert a["accrued_interest_at_forward_settlement_per_100"] == pytest.approx(
        _EXPECTED_ACCRUED, abs=1e-12
    )
    assert a["forward_dirty_price_per_100"] == pytest.approx(_EXPECTED_DIRTY_FORWARD, abs=1e-12)
    assert a["strike_dirty_price_per_100"] == pytest.approx(_EXPECTED_DIRTY_STRIKE, abs=1e-12)
    assert a["pricing_to_reporting_discount_factor"] == pytest.approx(
        _EXPECTED_DF_REPORTING, abs=1e-12
    )
    assert a["pricing_to_option_settlement_discount_factor"] == pytest.approx(
        _EXPECTED_DF_OPTION_SETTLEMENT, abs=1e-12
    )
    assert a["effective_reporting_date_discount_factor"] == pytest.approx(
        _EXPECTED_EFFECTIVE_DF, abs=1e-12
    )

    # Outputs.
    assert a["black76_pv_per_100"] == pytest.approx(_EXPECTED_PREMIUM_PER_100, abs=1e-12)
    assert result.pv == pytest.approx(_EXPECTED_TOTAL_PREMIUM, abs=1e-9)
    assert round(result.pv, 2) == 6.15
    assert a["black76_pv_per_100"] / _CLEAN_STRIKE * 100 == pytest.approx(
        _EXPECTED_PREMIUM_OVER_STRIKE_PCT, abs=1e-9
    )

    # Premium per 100 lies inside the Bloomberg display bucket for 00-19 5/8.
    assert _DISPLAY_BUCKET_LOW <= a["black76_pv_per_100"] <= _DISPLAY_BUCKET_HIGH


@_requires_quantlib
def test_ovme_golden_case_exact_option_days():
    # The fractional-timestamp ACT/ACT time derives from exactly
    # 90.64027777777778 elapsed days -- an ACT/365F date-only convention would
    # instead use whole days and land outside the display bucket (below).
    from datetime import datetime

    request = _golden_request()
    pricing = datetime.fromisoformat(request.pricing_timestamp)
    expiry = datetime.fromisoformat(request.expiry_timestamp)
    assert (expiry - pricing).total_seconds() / 86400.0 == pytest.approx(
        _EXPECTED_OPTION_DAYS, abs=1e-9
    )


@_requires_quantlib
def test_ovme_golden_case_rejects_legacy_clean_act365_expiry_methodology():
    # Guard against a tolerance wide enough that the legacy composition would
    # also pass: pricing the SAME case with clean forward/strike, ACT/365F
    # option time, and the same effective DF yields a premium OUTSIDE the
    # display bucket -- the dirty/ACT-ACT methodology is what lands inside it.
    legacy_time = year_fraction_to_expiry("2026-07-17", "2026-10-16")
    legacy_premium = black76_price_option_pv_per_100(
        forward_clean_price=_CLEAN_FORWARD,
        strike_clean_price=_CLEAN_STRIKE,
        price_volatility=_PRICE_VOL,
        time_to_expiry=legacy_time,
        discount_factor=_EXPECTED_EFFECTIVE_DF,
        option_type=OptionType.CALL,
    )
    assert not (_DISPLAY_BUCKET_LOW <= legacy_premium <= _DISPLAY_BUCKET_HIGH)
    assert abs(legacy_premium - _EXPECTED_PREMIUM_PER_100) > 1e-3


# --- Implied PRICE_VOL calibration round trip (Issue #P1-06) -----------------


def _golden_benchmark() -> BLIBenchmarkQuote:
    request = _golden_request()
    return BLIBenchmarkQuote(
        benchmark_id="SANITIZED_OVME_GOLDEN_BENCHMARK",
        source_type=BLIBenchmarkSourceType.BLOOMBERG,
        source_system=_SOURCE,
        source_as_of=request.market_data_snapshot.as_of_timestamp,
        retrieved_at="2026-07-17T14:00:00Z",
        quote_side=BLIBenchmarkQuoteSide.MID,
        premium_per_100=_EXPECTED_PREMIUM_PER_100,
        total_premium=_EXPECTED_TOTAL_PREMIUM,
        currency=request.bond_option.currency,
        product_id=request.bond_option.product_id,
        snapshot_id=request.market_data_snapshot.snapshot_id,
        underlying_id=request.bond_option.underlying_isin,
        source_reference="SANITIZED-OVME-GOLDEN-BENCHMARK-REFERENCE",
    )


@_requires_quantlib
def test_ovme_golden_case_implied_price_vol_calibration_recovers_and_reprices():
    # Proves calibration and the formal standalone pricing path are
    # consistent end to end: calibrating against the golden benchmark
    # premium recovers ~0.03314, and repricing a request that differs from
    # the original only by that implied volatility -- through the same
    # price_bli_mvp_standalone_option formal path, not a self-referential
    # dirty-solver repricing -- reproduces the golden premium.
    request = _golden_request()
    benchmark = _golden_benchmark()
    request_before = asdict(request)
    benchmark_before = asdict(benchmark)
    snapshot_before = asdict(request.market_data_snapshot)

    calibration = calibrate_bli_implied_price_vol(
        request, benchmark, active_quote_side=BLIBenchmarkQuoteSide.MID
    )

    assert calibration.status is BLIImpliedPriceVolCalibrationStatus.SUCCESS
    assert calibration.reason is BLIImpliedPriceVolCalibrationReason.CALIBRATED
    implied_vol = calibration.solver_result.implied_price_vol
    assert implied_vol == pytest.approx(_PRICE_VOL, abs=1e-4)

    # request/benchmark/snapshot were never mutated by calibration.
    assert asdict(request) == request_before
    assert asdict(benchmark) == benchmark_before
    assert asdict(request.market_data_snapshot) == snapshot_before

    # New immutable request copy: only the volatility input differs.
    repriced_request = replace(
        request,
        market_data_snapshot=replace(
            request.market_data_snapshot,
            volatility_input=replace(
                request.market_data_snapshot.volatility_input, volatility=implied_vol
            ),
        ),
    )
    assert repriced_request.market_data_snapshot.volatility_input.volatility != _PRICE_VOL
    # The original request/snapshot/volatility are still untouched.
    assert asdict(request) == request_before
    assert request.market_data_snapshot.volatility_input.volatility == _PRICE_VOL

    pricing_result = price_bli_mvp_standalone_option(repriced_request)
    assert pricing_result.status is PricingStatus.SUCCESS
    repriced_premium_per_100 = pricing_result.assumptions["black76_pv_per_100"]
    assert repriced_premium_per_100 == pytest.approx(_EXPECTED_PREMIUM_PER_100, abs=1e-6)
