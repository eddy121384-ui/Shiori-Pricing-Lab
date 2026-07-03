"""Vanilla rates product definitions (schema only).

This package holds machine-readable definitions of *what a trade is*. It is the
"Product Definition" piece of the Shiori spine
(``Product Definition + Valuation Context + Market Data Snapshot + Pricing
Engine = Valuation Result``).

Strict boundaries:

- No market data, curves, fixings, valuation date, or pricing output lives here.
- This package must not import the data, pricing, valuation, or AI layers.

Currently provides IRS, OIS, CCS, FX Swap, BondOption, DepositLeg, and
BondLinkedStructuredProduct (schema only). ``DepositLeg`` and ``BondOption``
are components consumed by ``BondLinkedStructuredProduct``, not standalone
products in their own right — see ``docs/18_deposit_leg_schema_preflight.md``
and ``docs/19_bli_wrapper_schema_preflight.md``. The wrapper owns the
relationship between its two components (currency consistency, a
derived-only ``participation_ratio``, CASH-settlement and
effective-settlement-date guardrails) and carries no Treasury FTP business
date, resolved rate, manual-rate provenance, or any pricing output — see
``docs/19`` §3/§9.
"""

from __future__ import annotations

from shiori_pricing_lab.products.bond_linked_structured_product import (
    BondLinkedStructuredProduct,
)
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
    "BondLinkedStructuredProduct",
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
