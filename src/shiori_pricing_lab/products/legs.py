"""Swap leg definitions.

A leg describes the contractual terms of one side of a vanilla swap. Legs carry
no notional or currency of their own: in a single-currency vanilla swap both
legs share the swap-level ``notional`` and ``currency``, so those live on the
swap to avoid duplicated (and potentially contradictory) state.

Legs are plain frozen dataclasses. They hold deal terms only — no market data,
no curves, no valuation date, no pricing output. Cross-field validation that
spans both legs (for example, that the paying and receiving directions are
opposite) is performed by the owning swap, not here.
"""

from __future__ import annotations

from dataclasses import dataclass

from shiori_pricing_lab.products.enums import (
    CompoundingMethod,
    DayCount,
    FloatingIndex,
    Frequency,
    PayReceive,
    coerce_enum,
)


@dataclass(frozen=True)
class FixedLeg:
    """Fixed-rate leg of a swap.

    ``fixed_rate`` is a decimal rate (4.25% = 0.0425) and may be zero or
    negative; rates legitimately trade through zero.
    """

    pay_receive: PayReceive
    fixed_rate: float
    payment_frequency: Frequency
    day_count: DayCount

    def __post_init__(self) -> None:
        # Coerce/validate enum-backed fields: type hints are not enforced at
        # runtime, so a raw string must be turned into a real enum member here.
        object.__setattr__(
            self, "pay_receive", coerce_enum(self.pay_receive, PayReceive, "pay_receive")
        )
        object.__setattr__(
            self,
            "payment_frequency",
            coerce_enum(self.payment_frequency, Frequency, "payment_frequency"),
        )
        object.__setattr__(
            self, "day_count", coerce_enum(self.day_count, DayCount, "day_count")
        )


@dataclass(frozen=True)
class FloatingLeg:
    """Floating-rate leg of a swap.

    Serves both IRS (term index, e.g. ``EUR_EURIBOR_3M``) and OIS (overnight
    index compounded daily) floating legs:

    - For a term IRS leg, set ``reset_frequency`` and leave
      ``compounding_method`` as ``NONE``.
    - For an OIS leg, the reset is daily by nature; ``compounding_method`` should
      be ``DAILY_COMPOUNDED`` or ``AVERAGED`` and ``reset_frequency`` may be left
      ``None`` (or set to ``Frequency.DAILY``).

    ``spread`` is a decimal spread over the index and may be positive, zero, or
    negative.
    """

    pay_receive: PayReceive
    index: FloatingIndex
    spread: float
    payment_frequency: Frequency
    day_count: DayCount
    reset_frequency: Frequency | None = None
    compounding_method: CompoundingMethod = CompoundingMethod.NONE

    def __post_init__(self) -> None:
        # Coerce/validate enum-backed fields. ``reset_frequency`` stays optional:
        # ``None`` means "no explicit reset" (e.g. an OIS daily reset), so only a
        # supplied value is coerced.
        object.__setattr__(
            self, "pay_receive", coerce_enum(self.pay_receive, PayReceive, "pay_receive")
        )
        object.__setattr__(self, "index", coerce_enum(self.index, FloatingIndex, "index"))
        object.__setattr__(
            self,
            "payment_frequency",
            coerce_enum(self.payment_frequency, Frequency, "payment_frequency"),
        )
        object.__setattr__(
            self, "day_count", coerce_enum(self.day_count, DayCount, "day_count")
        )
        if self.reset_frequency is not None:
            object.__setattr__(
                self,
                "reset_frequency",
                coerce_enum(self.reset_frequency, Frequency, "reset_frequency"),
            )
        object.__setattr__(
            self,
            "compounding_method",
            coerce_enum(self.compounding_method, CompoundingMethod, "compounding_method"),
        )
