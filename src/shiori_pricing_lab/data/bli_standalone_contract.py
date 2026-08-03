"""Standalone-route deal and resolved-bond contracts (Issue #146).

These frozen types contain exactly the fields the standalone browser pricing
route validates or consumes. They deliberately omit four legacy fields that
are not inputs to this route:

- ``BondOption.settlement_lag_days`` -- explicit forward and option
  settlement dates are authoritative;
- ``BondReferenceData.business_day_convention`` -- the reviewed coupon
  adapter uses unadjusted dates and a ``NullCalendar``;
- ``BondReferenceData.redemption_amount`` -- principal/redemption is not
  part of the reviewed accrued-interest slice;
- ``BondReferenceData.yield_convention`` -- standalone price-based pricing
  performs no yield conversion.

The general ``BondOption`` and ``BondReferenceData`` contracts remain
unchanged for legacy bundle and structured-product paths. No placeholder or
inferred value is manufactured to bridge the two shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shiori_pricing_lab.products._validation import _parse_iso_date, _require_non_blank
from shiori_pricing_lab.products.bond_option import BondOption, _require_finite_number
from shiori_pricing_lab.products.enums import (
    Currency,
    DayCount,
    ExerciseStyle,
    Frequency,
    OptionType,
    PayoffBasis,
    Position,
    SettlementType,
    coerce_enum,
)
from shiori_pricing_lab.reference_data._validation import (
    _parse_iso_date as _parse_reference_iso_date,
)
from shiori_pricing_lab.reference_data._validation import (
    _require_finite_number as _require_reference_finite_number,
)
from shiori_pricing_lab.reference_data._validation import (
    _require_non_blank as _require_reference_non_blank,
)
from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData
from shiori_pricing_lab.reference_data.eligibility import EligibilityResult
from shiori_pricing_lab.reference_data.enums import BondStatus, BondType


@dataclass(frozen=True)
class BLIStandaloneBondOptionTerms:
    """General bond-option terms needed by the standalone route, without lag."""

    product_id: str
    underlying_isin: str
    currency: Currency
    payoff_basis: PayoffBasis
    option_type: OptionType
    exercise_style: ExerciseStyle
    settlement_type: SettlementType
    expiry_date: str
    notional: float
    position: Position
    strike_price: float | None = None
    strike_yield: float | None = None
    exercise_start_date: str | None = None
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

        expiry = _parse_iso_date(self.expiry_date, "expiry_date")
        if self.exercise_style is ExerciseStyle.EUROPEAN:
            if self.exercise_start_date is not None:
                raise ValueError(
                    "exercise_start_date must be None when exercise_style is EUROPEAN"
                )
        else:
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
        else:
            if self.strike_price is not None:
                raise ValueError("strike_price must be None when payoff_basis is YIELD")
            if self.strike_yield is None:
                raise ValueError("strike_yield is required when payoff_basis is YIELD")
            _require_finite_number(self.strike_yield, "strike_yield")

    @classmethod
    def from_bond_option(cls, bond_option: BondOption) -> BLIStandaloneBondOptionTerms:
        """Drop the legacy lag field without reading or deriving from it."""

        if not isinstance(bond_option, BondOption):
            raise TypeError("bond_option must be a BondOption")
        return cls(
            product_id=bond_option.product_id,
            underlying_isin=bond_option.underlying_isin,
            currency=bond_option.currency,
            payoff_basis=bond_option.payoff_basis,
            option_type=bond_option.option_type,
            exercise_style=bond_option.exercise_style,
            settlement_type=bond_option.settlement_type,
            expiry_date=bond_option.expiry_date,
            notional=bond_option.notional,
            position=bond_option.position,
            strike_price=bond_option.strike_price,
            strike_yield=bond_option.strike_yield,
            exercise_start_date=bond_option.exercise_start_date,
        )


@dataclass(frozen=True)
class BLIStandaloneBondReferenceData:
    """Resolved bond fields needed for standalone safety and accrued interest."""

    isin: str
    issuer: str
    currency: Currency
    coupon: float
    coupon_frequency: Frequency
    maturity_date: str
    issue_date: str
    day_count: DayCount
    callable_flag: bool
    sinkable_flag: bool
    bond_type: BondType
    ex_dividend_days: int
    first_coupon_date: str
    last_coupon_date: str
    status: BondStatus

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", coerce_enum(self.currency, Currency, "currency"))
        object.__setattr__(
            self,
            "coupon_frequency",
            coerce_enum(self.coupon_frequency, Frequency, "coupon_frequency"),
        )
        object.__setattr__(self, "day_count", coerce_enum(self.day_count, DayCount, "day_count"))
        object.__setattr__(self, "bond_type", coerce_enum(self.bond_type, BondType, "bond_type"))
        object.__setattr__(self, "status", coerce_enum(self.status, BondStatus, "status"))

        _require_reference_non_blank(self.isin, "isin")
        _require_reference_non_blank(self.issuer, "issuer")
        _require_reference_finite_number(self.coupon, "coupon")
        if self.coupon < 0:
            raise ValueError(f"coupon must be non-negative, got {self.coupon}")

        issue = _parse_reference_iso_date(self.issue_date, "issue_date")
        maturity = _parse_reference_iso_date(self.maturity_date, "maturity_date")
        if issue >= maturity:
            raise ValueError(
                f"issue_date ({self.issue_date}) must be before "
                f"maturity_date ({self.maturity_date})"
            )
        first_coupon = _parse_reference_iso_date(
            self.first_coupon_date, "first_coupon_date"
        )
        last_coupon = _parse_reference_iso_date(self.last_coupon_date, "last_coupon_date")
        if first_coupon <= issue:
            raise ValueError(
                f"first_coupon_date ({self.first_coupon_date}) must be after "
                f"issue_date ({self.issue_date})"
            )
        if last_coupon > maturity:
            raise ValueError(
                f"last_coupon_date ({self.last_coupon_date}) must be on or "
                f"before maturity_date ({self.maturity_date})"
            )
        if first_coupon > last_coupon:
            raise ValueError(
                f"first_coupon_date ({self.first_coupon_date}) must be on or "
                f"before last_coupon_date ({self.last_coupon_date})"
            )

        if not isinstance(self.callable_flag, bool):
            raise ValueError(f"callable_flag must be a bool, got {self.callable_flag!r}")
        if not isinstance(self.sinkable_flag, bool):
            raise ValueError(f"sinkable_flag must be a bool, got {self.sinkable_flag!r}")
        if isinstance(self.ex_dividend_days, bool) or not isinstance(
            self.ex_dividend_days, int
        ):
            raise ValueError(
                "ex_dividend_days must be a non-negative integer, "
                f"got {self.ex_dividend_days!r}"
            )
        if self.ex_dividend_days < 0:
            raise ValueError(
                "ex_dividend_days must be a non-negative integer, "
                f"got {self.ex_dividend_days}"
            )

    @classmethod
    def from_bond_reference_data(
        cls, bond: BondReferenceData
    ) -> BLIStandaloneBondReferenceData:
        """Drop the three legacy-only fields without reading their values."""

        if not isinstance(bond, BondReferenceData):
            raise TypeError("bond must be a BondReferenceData")
        return cls(
            isin=bond.isin,
            issuer=bond.issuer,
            currency=bond.currency,
            coupon=bond.coupon,
            coupon_frequency=bond.coupon_frequency,
            maturity_date=bond.maturity_date,
            issue_date=bond.issue_date,
            day_count=bond.day_count,
            callable_flag=bond.callable_flag,
            sinkable_flag=bond.sinkable_flag,
            bond_type=bond.bond_type,
            ex_dividend_days=bond.ex_dividend_days,
            first_coupon_date=bond.first_coupon_date,
            last_coupon_date=bond.last_coupon_date,
            status=bond.status,
        )


StandaloneBondOption = BondOption | BLIStandaloneBondOptionTerms
StandaloneBondReferenceData = BondReferenceData | BLIStandaloneBondReferenceData


def is_standalone_bond_reference_data_eligible(
    bond: StandaloneBondReferenceData,
) -> EligibilityResult:
    """Apply the standalone route's structural safety gates.

    This intentionally mirrors generic MVP eligibility except for
    ``yield_convention``, which the price-based standalone route neither
    consumes nor carries.
    """

    if not isinstance(bond, (BondReferenceData, BLIStandaloneBondReferenceData)):
        raise TypeError(
            "bond must be a BondReferenceData or BLIStandaloneBondReferenceData"
        )

    reasons: list[str] = []
    if bond.callable_flag:
        reasons.append("callable bonds are not standalone-pricing-eligible")
    if bond.sinkable_flag:
        reasons.append("sinkable bonds are not standalone-pricing-eligible")
    if bond.bond_type is not BondType.FIXED_COUPON_BULLET:
        reasons.append(
            f"bond_type {bond.bond_type.value} is not standalone-pricing-eligible "
            "(only FIXED_COUPON_BULLET is eligible)"
        )
    if bond.coupon == 0:
        reasons.append(
            "zero-coupon bonds are valid reference data but are not "
            "standalone-pricing-eligible for this implementation"
        )
    if bond.status is not BondStatus.ACTIVE:
        reasons.append(f"status {bond.status.value} is not standalone-pricing-eligible")
    return EligibilityResult(eligible=not reasons, reasons=tuple(reasons))
