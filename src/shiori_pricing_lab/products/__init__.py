"""Vanilla rates product definitions (schema only).

This package holds machine-readable definitions of *what a trade is*. It is the
"Product Definition" piece of the Shiori spine
(``Product Definition + Valuation Context + Market Data Snapshot + Pricing
Engine = Valuation Result``).

Strict boundaries:

- No market data, curves, fixings, valuation date, or pricing output lives here.
- This package must not import the data, pricing, valuation, or AI layers.

Currently provides IRS, OIS, CCS, and FX Swap (schema only).
"""

from __future__ import annotations

from shiori_pricing_lab.products.cross_currency import (
    CrossCurrencyLeg,
    CrossCurrencySwap,
)
from shiori_pricing_lab.products.enums import (
    BusinessDayConvention,
    BuySell,
    CompoundingMethod,
    Currency,
    DayCount,
    FloatingIndex,
    Frequency,
    PayReceive,
)
from shiori_pricing_lab.products.fx_swap import FXSwap
from shiori_pricing_lab.products.legs import FixedLeg, FloatingLeg
from shiori_pricing_lab.products.swaps import InterestRateSwap, OvernightIndexedSwap

__all__ = [
    "BusinessDayConvention",
    "BuySell",
    "CompoundingMethod",
    "CrossCurrencyLeg",
    "CrossCurrencySwap",
    "Currency",
    "DayCount",
    "FXSwap",
    "FixedLeg",
    "FloatingIndex",
    "FloatingLeg",
    "Frequency",
    "InterestRateSwap",
    "OvernightIndexedSwap",
    "PayReceive",
]
