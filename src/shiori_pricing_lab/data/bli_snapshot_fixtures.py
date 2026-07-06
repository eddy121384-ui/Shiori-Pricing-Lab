"""Small, manually reviewed synthetic ``BLIMarketDataSnapshot`` fixture.

Per ``docs/23_bli_market_data_snapshot_schema_preflight.md`` §11.1: one
complete, eligible, positive BLI market-data path -- one valuation date,
one bond quote for a resolved-eligible ISIN reused from
``reference_data.fixtures.SYNTHETIC_BOND_FIXTURES``, one Bond Reference
Curve, one Option Discount Curve, one Deposit Curve, one separate
deposit-rate/FTP input (the Deposit Curve is the deposit leg's own
discounting input and is never a substitute for the FTP rate observation,
or vice versa), one explicit volatility input, and one explicit
credit-spread treatment.

**Explicitly not built here:** the MVP input bundle, a bundle builder, or a
pricing engine (docs/23 §13/§17). This module is a fixture only.
"""

from __future__ import annotations

from shiori_pricing_lab.data.bli_snapshot import (
    BLIBondQuote,
    BLICreditSpreadInput,
    BLICreditSpreadTreatment,
    BLICurvePoint,
    BLICurvePurpose,
    BLIDepositRateObservation,
    BLIMarketDataSnapshot,
    BLIMarketDataStatus,
    BLIQuoteBasis,
    BLIVolatilityBasis,
    BLIVolatilityInput,
)
from shiori_pricing_lab.products.enums import (
    Currency,
    TreasuryFTPQuoteSide,
    TreasuryFTPTenor,
)

# Reuses the existing eligible FIXED_COUPON_BULLET bond from
# reference_data.fixtures.SYNTHETIC_BOND_FIXTURES (docs/20/PR #58), rather
# than inventing a second synthetic ISIN for the same MVP fixture.
_SYNTHETIC_ELIGIBLE_ISIN = "XS0000000001"

_SYNTHETIC_VALUATION_DATE = "2026-07-01"

_SYNTHETIC_BOND_QUOTE = BLIBondQuote(
    isin=_SYNTHETIC_ELIGIBLE_ISIN,
    currency=Currency.USD,
    price_type=BLIQuoteBasis.PRICE,
    quote_side=TreasuryFTPQuoteSide.MID,
    source_system="SYNTHETIC_BOND_QUOTE_FEED",
    status=BLIMarketDataStatus.ACTIVE,
    clean_price_per_100=101.25,
    accrued_interest_per_100=0.42,
)

_SYNTHETIC_BOND_REFERENCE_CURVE = (
    BLICurvePoint(
        curve_id="USD_BOND_REFERENCE_CURVE",
        curve_name="USD Bond Reference Curve",
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.BOND_REFERENCE_CURVE,
        tenor="2Y",
        rate=0.0362,
        source_system="SYNTHETIC_CURVE_FEED",
        status=BLIMarketDataStatus.ACTIVE,
    ),
    BLICurvePoint(
        curve_id="USD_BOND_REFERENCE_CURVE",
        curve_name="USD Bond Reference Curve",
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.BOND_REFERENCE_CURVE,
        tenor="5Y",
        rate=0.0375,
        source_system="SYNTHETIC_CURVE_FEED",
        status=BLIMarketDataStatus.ACTIVE,
    ),
)

_SYNTHETIC_OPTION_DISCOUNT_CURVE = (
    BLICurvePoint(
        curve_id="USD_OPTION_DISCOUNT_CURVE",
        curve_name="USD Option Discount Curve",
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
        tenor="2Y",
        rate=0.0341,
        source_system="SYNTHETIC_CURVE_FEED",
        status=BLIMarketDataStatus.ACTIVE,
    ),
    BLICurvePoint(
        curve_id="USD_OPTION_DISCOUNT_CURVE",
        curve_name="USD Option Discount Curve",
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
        tenor="5Y",
        rate=0.0353,
        source_system="SYNTHETIC_CURVE_FEED",
        status=BLIMarketDataStatus.ACTIVE,
    ),
)

_SYNTHETIC_DEPOSIT_CURVE = (
    BLICurvePoint(
        curve_id="USD_DEPOSIT_CURVE",
        curve_name="USD Deposit Curve",
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.DEPOSIT_CURVE,
        tenor="3M",
        rate=0.0350,
        source_system="SYNTHETIC_CURVE_FEED",
        status=BLIMarketDataStatus.ACTIVE,
    ),
)

# A separate deposit-rate / FTP input, matching a TREASURY_FTP_REFERENCE-mode
# DepositLeg (docs/18 §4.2). This resolves the *rate*; it does not replace
# the Deposit Curve above, which is a separate discounting/funding input
# (docs/23 §11.1, Codex P2 review of PR #62).
_SYNTHETIC_DEPOSIT_RATE_OBSERVATION = BLIDepositRateObservation(
    currency=Currency.USD,
    tenor=TreasuryFTPTenor.THREE_MONTH,
    quote_side=TreasuryFTPQuoteSide.MID,
    ftp_rate_percent_value=3.5500,
    ftp_rate_decimal_value=0.0355,
    source_system="SYNTHETIC_TREASURY_FTP_FEED",
    status=BLIMarketDataStatus.ACTIVE,
)

_SYNTHETIC_VOLATILITY_INPUT = BLIVolatilityInput(
    volatility=0.18,
    volatility_basis=BLIVolatilityBasis.PRICE_VOL,
    source_system="SYNTHETIC_VOL_FEED",
    status=BLIMarketDataStatus.ACTIVE,
)

_SYNTHETIC_CREDIT_SPREAD_INPUT = BLICreditSpreadInput(
    spread_treatment=BLICreditSpreadTreatment.OBSERVED,
    source_system="SYNTHETIC_CREDIT_SPREAD_FEED",
    status=BLIMarketDataStatus.ACTIVE,
    credit_spread=0.0125,
    credit_spread_basis="BPS_OVER_USD_BOND_REFERENCE_CURVE",
)

SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT = BLIMarketDataSnapshot(
    valuation_date=_SYNTHETIC_VALUATION_DATE,
    as_of_timestamp="2026-07-01T16:00:00Z",
    source_system="SYNTHETIC_BLI_MARKET_DATA_FIXTURE",
    snapshot_id="SYNTHETIC_BLI_SNAPSHOT_0001",
    status=BLIMarketDataStatus.ACTIVE,
    bond_quote=_SYNTHETIC_BOND_QUOTE,
    curve_points=(
        _SYNTHETIC_BOND_REFERENCE_CURVE
        + _SYNTHETIC_OPTION_DISCOUNT_CURVE
        + _SYNTHETIC_DEPOSIT_CURVE
    ),
    volatility_input=_SYNTHETIC_VOLATILITY_INPUT,
    credit_spread_input=_SYNTHETIC_CREDIT_SPREAD_INPUT,
    deposit_rate_observation=_SYNTHETIC_DEPOSIT_RATE_OBSERVATION,
)
