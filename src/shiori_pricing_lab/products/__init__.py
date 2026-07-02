"""Vanilla rates product definitions (schema only).

This package holds machine-readable definitions of *what a trade is*. It is the
"Product Definition" piece of the Shiori spine
(``Product Definition + Valuation Context + Market Data Snapshot + Pricing
Engine = Valuation Result``).

Strict boundaries:

- No market data, curves, fixings, valuation date, or pricing output lives here.
- This package must not import the data, pricing, valuation, or AI layers.

Currently provides IRS, OIS, CCS, FX Swap, and BondOption (schema only).
``BondLinkedStructuredProduct`` is intentionally not provided yet — see
``docs/15_bli_product_schema_preflight_issue_38.md`` §3. ``DepositRateMode``,
``TreasuryFTPQuoteSide``, and ``TreasuryFTPTenor`` are controlled-vocabulary
foundations for a future ``DepositLeg`` schema — see
``docs/18_deposit_leg_schema_preflight.md``; no ``DepositLeg`` schema exists
yet.
"""

from __future__ import annotations

from shiori_pricing_lab.products.bond_option import BondOption
from shiori_pricing_lab.products.cross_currency import (
    CrossCurrencyLeg,
    CrossCurrencySwap,
)
from shiori_pricing_lab.products.enums import (
    BondYieldConvention,
    BusinessDayConvention,
    BuySell,
    CompoundingMethod,
    Currency,
    DayCount,
    DepositRateMode,
    ExerciseStyle,
    FloatingIndex,
    Frequency,
    OptionType,
    PayoffBasis,
    PayReceive,
    Position,
    SettlementType,
    TreasuryFTPQuoteSide,
    TreasuryFTPTenor,
)
from shiori_pricing_lab.products.fx_swap import FXSwap
from shiori_pricing_lab.products.legs import FixedLeg, FloatingLeg
from shiori_pricing_lab.products.swaps import InterestRateSwap, OvernightIndexedSwap

__all__ = [
    "BondOption",
    "BondYieldConvention",
    "BusinessDayConvention",
    "BuySell",
    "CompoundingMethod",
    "CrossCurrencyLeg",
    "CrossCurrencySwap",
    "Currency",
    "DayCount",
    "DepositRateMode",
    "ExerciseStyle",
    "FXSwap",
    "FixedLeg",
    "FloatingIndex",
    "FloatingLeg",
    "Frequency",
    "InterestRateSwap",
    "OptionType",
    "OvernightIndexedSwap",
    "PayReceive",
    "PayoffBasis",
    "Position",
    "SettlementType",
    "TreasuryFTPQuoteSide",
    "TreasuryFTPTenor",
]
