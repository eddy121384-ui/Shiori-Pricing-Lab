"""Cross-currency swap (CCS) product definition: schema only.

A CCS exchanges interest (and usually principal) in two *different* currencies.
Unlike the single-currency IRS / OIS — where both legs share one swap-level
notional and currency — each CCS leg carries its **own** currency and notional.
That is the one structural difference from the vanilla swaps, and it lives in a
small wrapper:

    CrossCurrencyLeg(currency, notional, leg)

where ``leg`` reuses the existing ``FixedLeg`` or ``FloatingLeg`` unchanged.
Reusing the legs keeps fixed/float, float/float (basis), and fixed/fixed CCS all
expressible without new leg types, and a floating-leg basis spread is just the
existing ``FloatingLeg.spread`` — there is no separate ``basis_spread`` field.

Like the other product definitions this records *what the trade is* only. There
are no curves, fixings, FX rates, valuation date, PV, or DV01 here — those
belong to a future pricing engine. The implied FX rate (notional_1 / notional_2)
is intentionally *not* stored, to avoid a contradictory third source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shiori_pricing_lab.products._validation import _parse_iso_date, _require_non_blank
from shiori_pricing_lab.products.enums import (
    BusinessDayConvention,
    Currency,
    coerce_enum,
)
from shiori_pricing_lab.products.legs import FixedLeg, FloatingLeg


@dataclass(frozen=True)
class CrossCurrencyLeg:
    """One leg of a cross-currency swap: a currency + notional + interest terms.

    The interest terms are a reused ``FixedLeg`` or ``FloatingLeg``. Currency and
    notional are per-leg here (not swap-level) because the two legs of a CCS are
    in different currencies with their own notionals. The leg's own
    ``pay_receive`` and conventions come from the wrapped ``FixedLeg`` /
    ``FloatingLeg`` and are validated by that object.
    """

    currency: Currency
    notional: float
    leg: FixedLeg | FloatingLeg

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", coerce_enum(self.currency, Currency, "currency"))
        if not isinstance(self.leg, (FixedLeg, FloatingLeg)):
            raise TypeError("CrossCurrencyLeg.leg must be a FixedLeg or FloatingLeg")
        if not self.notional > 0:
            raise ValueError(f"notional must be positive, got {self.notional}")

    @property
    def pay_receive(self):
        """Direction of this leg, read from the wrapped interest leg."""

        return self.leg.pay_receive


@dataclass(frozen=True)
class CrossCurrencySwap:
    """Cross-currency swap (two currencies, two notionals).

    ``leg_1`` and ``leg_2`` must be in different currencies and have opposite
    pay/receive directions (one paid away, one received). ``initial_exchange``
    and ``final_exchange`` record whether notionals are exchanged at start and at
    maturity; they are required, explicit booleans (no silent default) because
    not every CCS exchanges principal.
    """

    product_id: str
    effective_date: str
    maturity_date: str
    leg_1: CrossCurrencyLeg
    leg_2: CrossCurrencyLeg
    initial_exchange: bool
    final_exchange: bool
    business_day_convention: BusinessDayConvention
    # Fixed discriminator: not a constructor argument, so a caller cannot build
    # a CCS-shaped trade that serializes as anything other than "CCS".
    product_type: str = field(init=False, default="CCS")

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "business_day_convention",
            coerce_enum(
                self.business_day_convention,
                BusinessDayConvention,
                "business_day_convention",
            ),
        )

        _require_non_blank(self.product_id, "product_id")

        effective = _parse_iso_date(self.effective_date, "effective_date")
        maturity = _parse_iso_date(self.maturity_date, "maturity_date")
        if maturity <= effective:
            raise ValueError(
                f"maturity_date ({self.maturity_date}) must be after "
                f"effective_date ({self.effective_date})"
            )

        if not isinstance(self.leg_1, CrossCurrencyLeg) or not isinstance(
            self.leg_2, CrossCurrencyLeg
        ):
            raise TypeError("leg_1 and leg_2 must be CrossCurrencyLeg instances")

        # The defining property of a CCS: the two legs are in different
        # currencies. Same-currency would just be a single-currency swap.
        if self.leg_1.currency is self.leg_2.currency:
            raise ValueError(
                "leg_1 and leg_2 must be in different currencies "
                f"(both are {self.leg_1.currency.value})"
            )

        # In a swap one leg is paid and the other received.
        if self.leg_1.pay_receive is self.leg_2.pay_receive:
            raise ValueError("leg_1 and leg_2 must have opposite pay/receive directions")

        # Exchange flags must be real booleans, not truthy values, so an
        # exchange decision is always explicit and unambiguous.
        if not isinstance(self.initial_exchange, bool):
            raise TypeError("initial_exchange must be a bool")
        if not isinstance(self.final_exchange, bool):
            raise TypeError("final_exchange must be a bool")
