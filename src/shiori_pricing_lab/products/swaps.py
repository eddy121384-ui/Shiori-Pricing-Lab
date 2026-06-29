"""Vanilla single-currency swap product definitions: IRS and OIS.

A product definition answers only one question: *what is the trade?* It records
the contractual terms and nothing about the market or the valuation. There are
deliberately no curves, fixings, discount factors, valuation date, PV, or DV01
here — those belong to a future pricing engine that will consume a product
definition together with a separate ``ValuationContext``.

The design is inspired by how standard rates documentation (ISDA / FpML / CDM
style) separates a trade into legs with explicit conventions, but it is a small,
purpose-built schema, not a copy or implementation of any of those standards.

Validation is intentionally schema-level only: it checks that the recorded terms
are internally coherent (positive notional, sensible date order, opposite leg
directions, non-blank identifiers). It does not parse calendars or compute
schedules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from shiori_pricing_lab.products.enums import (
    BusinessDayConvention,
    CompoundingMethod,
    Currency,
)
from shiori_pricing_lab.products.legs import FixedLeg, FloatingLeg


def _require_non_blank(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")


def _parse_iso_date(value: str, field_name: str) -> date:
    """Parse an explicit ISO date string (YYYY-MM-DD).

    ``date.fromisoformat`` is pure parsing, not the system clock, so this does
    not smuggle a valuation date or "today" into a product definition. It is
    used only to validate format and ordering of the schedule dates.
    """

    _require_non_blank(value, field_name)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date (YYYY-MM-DD): {value!r}") from exc


def _validate_common(
    product_id: str,
    effective_date: str,
    maturity_date: str,
    notional: float,
    fixed_leg: FixedLeg,
    floating_leg: FloatingLeg,
) -> None:
    """Validation shared by IRS and OIS."""

    _require_non_blank(product_id, "product_id")

    effective = _parse_iso_date(effective_date, "effective_date")
    maturity = _parse_iso_date(maturity_date, "maturity_date")
    if maturity <= effective:
        raise ValueError(
            f"maturity_date ({maturity_date}) must be after effective_date ({effective_date})"
        )

    if not notional > 0:
        raise ValueError(f"notional must be positive, got {notional}")

    # In a vanilla swap one leg is paid and the other received.
    if fixed_leg.pay_receive is floating_leg.pay_receive:
        raise ValueError(
            "fixed_leg and floating_leg must have opposite pay/receive directions"
        )


@dataclass(frozen=True)
class InterestRateSwap:
    """Vanilla fixed-vs-floating interest rate swap (single currency).

    The fixed/floating direction of the trade is read from the legs: a payer
    swap has ``fixed_leg.pay_receive == PayReceive.PAY``. Both legs share the
    swap-level ``notional`` and ``currency``.

    The floating leg must reference a term index reset schedule, so
    ``floating_leg.reset_frequency`` is required for an IRS.
    """

    product_id: str
    effective_date: str
    maturity_date: str
    currency: Currency
    notional: float
    fixed_leg: FixedLeg
    floating_leg: FloatingLeg
    business_day_convention: BusinessDayConvention
    product_type: str = "IRS"

    def __post_init__(self) -> None:
        _validate_common(
            self.product_id,
            self.effective_date,
            self.maturity_date,
            self.notional,
            self.fixed_leg,
            self.floating_leg,
        )
        if self.floating_leg.reset_frequency is None:
            raise ValueError("IRS floating_leg.reset_frequency is required")

    @property
    def pay_receive_fixed(self):
        """Direction of the fixed leg (the conventional 'side' of the swap)."""

        return self.fixed_leg.pay_receive


@dataclass(frozen=True)
class OvernightIndexedSwap:
    """Vanilla fixed-vs-overnight-index swap (single currency).

    The floating leg references an overnight index whose daily fixings are
    combined over each coupon period, so ``floating_leg.compounding_method`` must
    be a real compounding method (``DAILY_COMPOUNDED`` or ``AVERAGED``), not
    ``NONE``. The daily reset is implicit, so ``reset_frequency`` is optional.
    """

    product_id: str
    effective_date: str
    maturity_date: str
    currency: Currency
    notional: float
    fixed_leg: FixedLeg
    floating_leg: FloatingLeg
    business_day_convention: BusinessDayConvention
    product_type: str = "OIS"

    def __post_init__(self) -> None:
        _validate_common(
            self.product_id,
            self.effective_date,
            self.maturity_date,
            self.notional,
            self.fixed_leg,
            self.floating_leg,
        )
        if self.floating_leg.compounding_method is CompoundingMethod.NONE:
            raise ValueError(
                "OIS floating_leg.compounding_method must be DAILY_COMPOUNDED or AVERAGED"
            )

    @property
    def pay_receive_fixed(self):
        """Direction of the fixed leg (the conventional 'side' of the swap)."""

        return self.fixed_leg.pay_receive
