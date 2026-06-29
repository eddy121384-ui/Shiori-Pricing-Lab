"""Vanilla rates product definitions (schema only).

This package holds machine-readable definitions of *what a trade is*. It is the
"Product Definition" piece of the Shiori spine
(``Product Definition + Valuation Context + Market Data Snapshot + Pricing
Engine = Valuation Result``).

Strict boundaries:

- No market data, curves, fixings, valuation date, or pricing output lives here.
- This package must not import the data, pricing, valuation, or AI layers.

Currently provides IRS and OIS. CCS and FX Swap are intentionally deferred to a
later PR (see ``docs/04_product_definition_schema.md``).
"""

from __future__ import annotations

from shiori_pricing_lab.products.enums import (
    BusinessDayConvention,
    CompoundingMethod,
    Currency,
    DayCount,
    FloatingIndex,
    Frequency,
    PayReceive,
)
from shiori_pricing_lab.products.legs import FixedLeg, FloatingLeg
from shiori_pricing_lab.products.swaps import InterestRateSwap, OvernightIndexedSwap

__all__ = [
    "BusinessDayConvention",
    "CompoundingMethod",
    "Currency",
    "DayCount",
    "FixedLeg",
    "FloatingIndex",
    "FloatingLeg",
    "Frequency",
    "InterestRateSwap",
    "OvernightIndexedSwap",
    "PayReceive",
]
