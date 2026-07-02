"""Vanilla rates product definitions (schema only).

This package holds machine-readable definitions of *what a trade is*. It is the
"Product Definition" piece of the Shiori spine
(``Product Definition + Valuation Context + Market Data Snapshot + Pricing
Engine = Valuation Result``).

Strict boundaries:

- No market data, curves, fixings, valuation date, or pricing output lives here.
- This package must not import the data, pricing, valuation, or AI layers.

Currently provides IRS, OIS, CCS, FX Swap, BondOption, and DepositLeg
(schema only). ``BondLinkedStructuredProduct`` is intentionally not
provided yet — see ``docs/15_bli_product_schema_preflight_issue_38.md`` §3
and ``docs/18_deposit_leg_schema_preflight.md``. ``DepositLeg`` is a leg
component consumed by that future wrapper, not a standalone product; it
carries no Treasury FTP business date, resolved rate, or manual-rate
provenance — see ``docs/18`` §4/§8.
"""

from __future__ import annotations

from shiori_pricing_lab.products.bond_option import BondOption
from shiori_pricing_lab.products.cross_currency import (
    CrossCurrencyLeg,
    CrossCurrencySwap,
)
from shiori_pricing_lab.products.deposit_leg import DepositLeg, TreasuryFTPRateSelector
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
    PrincipalRepaymentRule,
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
    "DepositLeg",
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
    "PrincipalRepaymentRule",
    "SettlementType",
    "TreasuryFTPQuoteSide",
    "TreasuryFTPRateSelector",
    "TreasuryFTPTenor",
]
