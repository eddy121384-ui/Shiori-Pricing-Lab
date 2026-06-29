"""FX swap product definition: schema only.

An FX swap is a pair of currency exchanges: a *near* leg and a *far* leg in the
opposite direction. It is not an interest-accrual product, so it does **not**
reuse ``FixedLeg`` / ``FloatingLeg`` — it is a small, flat schema of its own.

The near and far exchange rates are **frozen trade terms agreed at execution**,
not live market data: ``near_rate`` and ``far_rate`` live on the product and are
never sourced from a curve or snapshot. Forward points are intentionally not
stored separately — they are ``far_rate - near_rate`` (scaled by a pair-specific
pip convention), so storing them would duplicate, and could contradict, the
explicit rates.

The trade owner does one action on the base currency on the near date
(``near_action``: buy or sell); the far leg is the opposite by construction, so
only the near-leg direction is recorded. A single matched ``base_notional``
applies to both legs; the quote-currency amounts are derivable (base * rate) and
are not stored, again to avoid a contradictory second source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shiori_pricing_lab.products._validation import _parse_iso_date, _require_non_blank
from shiori_pricing_lab.products.enums import (
    BusinessDayConvention,
    BuySell,
    Currency,
    coerce_enum,
)


@dataclass(frozen=True)
class FXSwap:
    """FX swap: near and far exchange of two currencies at agreed rates.

    ``base_currency`` / ``quote_currency`` form the pair (rates are quoted as
    units of quote per unit of base). ``base_notional`` is the matched base
    principal exchanged on both legs. ``near_action`` is what the owner does to
    the base currency on the near date; the far leg reverses it.
    """

    product_id: str
    base_currency: Currency
    quote_currency: Currency
    near_date: str
    far_date: str
    base_notional: float
    near_action: BuySell
    near_rate: float
    far_rate: float
    business_day_convention: BusinessDayConvention
    # Fixed discriminator: not a constructor argument, so a caller cannot build
    # an FX-swap-shaped trade that serializes as anything other than "FX_SWAP".
    product_type: str = field(init=False, default="FX_SWAP")

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "base_currency", coerce_enum(self.base_currency, Currency, "base_currency")
        )
        object.__setattr__(
            self, "quote_currency", coerce_enum(self.quote_currency, Currency, "quote_currency")
        )
        object.__setattr__(
            self, "near_action", coerce_enum(self.near_action, BuySell, "near_action")
        )
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

        if self.base_currency is self.quote_currency:
            raise ValueError(
                "base_currency and quote_currency must differ "
                f"(both are {self.base_currency.value})"
            )

        near = _parse_iso_date(self.near_date, "near_date")
        far = _parse_iso_date(self.far_date, "far_date")
        if far <= near:
            raise ValueError(
                f"far_date ({self.far_date}) must be after near_date ({self.near_date})"
            )

        if not self.base_notional > 0:
            raise ValueError(f"base_notional must be positive, got {self.base_notional}")

        # FX rates are strictly positive (unlike interest rates, which may trade
        # through zero). Both the agreed near and far rates must be > 0.
        if not self.near_rate > 0:
            raise ValueError(f"near_rate must be positive, got {self.near_rate}")
        if not self.far_rate > 0:
            raise ValueError(f"far_rate must be positive, got {self.far_rate}")
