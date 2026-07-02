"""BondOption product definition: schema only (Issue #38, partial).

Scope, per ``docs/15_bli_product_schema_preflight_issue_38.md`` §2: ``BondOption``
records the pure contractual deal terms of a bond option — what was traded, not
the underlying bond's static reference data (Bond Master), not market data, and
not a pricing output. It does not carry ``day_count``, ``yield_convention``, or
``compounding_frequency``: those describe the underlying bond's accrual/yield
convention and belong to a future Bond Master schema, resolved at pricing time
(``docs/14`` §3.2, F-08).

``underlying_isin`` is a reference to the bond, not the bond's data itself —
this schema does not fetch or embed Bond Master fields.

``BondLinkedStructuredProduct`` (the deposit-linked wrapper) is intentionally
not implemented here; see ``docs/15`` §3 for why it must stay deferred.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from shiori_pricing_lab.products._validation import _parse_iso_date, _require_non_blank
from shiori_pricing_lab.products.enums import (
    Currency,
    ExerciseStyle,
    OptionType,
    PayoffBasis,
    Position,
    SettlementType,
    coerce_enum,
)


def _require_finite_number(value: object, field_name: str) -> None:
    """Reject anything that is not a real, finite ``int``/``float``.

    ``bool`` is a ``int`` subclass in Python, so it is excluded explicitly.
    This only checks "is this a usable real number" — it does not impose a
    sign; callers apply their own positivity rules on top where needed.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")


@dataclass(frozen=True)
class BondOption:
    """Bond option: pure deal terms only.

    ``payoff_basis`` fixes whether the strike is expressed in price or yield
    space; exactly one of ``strike_price`` / ``strike_yield`` must be set,
    matching ``payoff_basis`` (§2.3). ``exercise_start_date`` is only
    meaningful for ``ExerciseStyle.AMERICAN`` and must be ``None`` for
    ``ExerciseStyle.EUROPEAN``. ``settlement_lag_days`` is recorded as a plain
    trader-adjustable integer, not resolved against a calendar here.
    """

    product_id: str
    underlying_isin: str
    currency: Currency
    payoff_basis: PayoffBasis
    option_type: OptionType
    exercise_style: ExerciseStyle
    settlement_type: SettlementType
    settlement_lag_days: int
    expiry_date: str
    notional: float
    position: Position
    strike_price: float | None = None
    strike_yield: float | None = None
    exercise_start_date: str | None = None
    # Fixed discriminator: not a constructor argument, so a caller cannot build
    # a BondOption-shaped trade that serializes as anything other than
    # "BOND_OPTION".
    product_type: str = field(init=False, default="BOND_OPTION")

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", coerce_enum(self.currency, Currency, "currency"))
        object.__setattr__(
            self, "payoff_basis", coerce_enum(self.payoff_basis, PayoffBasis, "payoff_basis")
        )
        object.__setattr__(
            self, "option_type", coerce_enum(self.option_type, OptionType, "option_type")
        )
        object.__setattr__(
            self,
            "exercise_style",
            coerce_enum(self.exercise_style, ExerciseStyle, "exercise_style"),
        )
        object.__setattr__(
            self,
            "settlement_type",
            coerce_enum(self.settlement_type, SettlementType, "settlement_type"),
        )
        object.__setattr__(self, "position", coerce_enum(self.position, Position, "position"))

        _require_non_blank(self.product_id, "product_id")
        _require_non_blank(self.underlying_isin, "underlying_isin")

        _require_finite_number(self.notional, "notional")
        if not self.notional > 0:
            raise ValueError(f"notional must be positive, got {self.notional}")

        if isinstance(self.settlement_lag_days, bool) or not isinstance(
            self.settlement_lag_days, int
        ):
            raise ValueError(
                f"settlement_lag_days must be a non-negative integer, got {self.settlement_lag_days!r}"
            )
        if self.settlement_lag_days < 0:
            raise ValueError(
                f"settlement_lag_days must be a non-negative integer, got {self.settlement_lag_days}"
            )

        expiry = _parse_iso_date(self.expiry_date, "expiry_date")

        if self.exercise_style is ExerciseStyle.EUROPEAN:
            if self.exercise_start_date is not None:
                raise ValueError(
                    "exercise_start_date must be None when exercise_style is EUROPEAN"
                )
        else:  # ExerciseStyle.AMERICAN
            if self.exercise_start_date is None:
                raise ValueError(
                    "exercise_start_date is required when exercise_style is AMERICAN"
                )
            exercise_start = _parse_iso_date(self.exercise_start_date, "exercise_start_date")
            if exercise_start >= expiry:
                raise ValueError(
                    "exercise_start_date "
                    f"({self.exercise_start_date}) must be strictly before "
                    f"expiry_date ({self.expiry_date})"
                )

        if self.payoff_basis is PayoffBasis.PRICE:
            if self.strike_yield is not None:
                raise ValueError("strike_yield must be None when payoff_basis is PRICE")
            if self.strike_price is None:
                raise ValueError("strike_price is required when payoff_basis is PRICE")
            _require_finite_number(self.strike_price, "strike_price")
            if not self.strike_price > 0:
                raise ValueError(f"strike_price must be positive, got {self.strike_price}")
        else:  # PayoffBasis.YIELD
            if self.strike_price is not None:
                raise ValueError("strike_price must be None when payoff_basis is YIELD")
            if self.strike_yield is None:
                raise ValueError("strike_yield is required when payoff_basis is YIELD")
            # Zero and negative yields are legitimate (unlike a price), so only
            # a finiteness/type check applies here, deliberately no sign check.
            _require_finite_number(self.strike_yield, "strike_yield")
