"""``BondReferenceData``: the underlying bond's static Bond Master terms.

Scope, per ``docs/20_bli_bond_reference_data_preflight.md``: this is the
underlying bond's stable, security-level static contractual terms --
identity, coupon mechanics, accrual/yield convention, redemption terms,
and the structural flags needed to determine plain-vanilla MVP pricing
eligibility. It is deliberately **not** part of ``shiori_pricing_lab.products``
(docs/20 §7's explicit "should the schema/future object itself care where
the data came from" framing, and this slice's package decision, recorded
below): a ``BondOption`` stores ``underlying_isin`` -- a reference, not
this data -- and a future pricing engine resolves that reference against a
``BondReferenceData`` fixture at pricing time, never at product-schema
construction time (docs/20 §8).

**Package decision (explicit, not silently defaulted):** Bond Reference
Data lives in its own top-level package,
``shiori_pricing_lab.reference_data``, sibling to ``products``, not inside
it. Reference data describes what the issuer promised (largely unchanged
for years); a product schema describes what was traded (frozen at deal
date). Putting Bond Master terms inside ``products`` would blur that
boundary and contradict docs/20 §2's "must not be embedded inside product
schemas" rule. No existing product schema (`BondOption`, `DepositLeg`,
`BondLinkedStructuredProduct`) is changed by this module.

This object validates only that its own fields are well-formed reference
data (docs/20 §10). **It does not decide MVP pricing eligibility** --
that is a separate, explicit check in
``shiori_pricing_lab.reference_data.eligibility.is_mvp_pricing_eligible``,
because a callable bond, a zero-coupon bond, or an ``OTHER``-yield-
convention bond are all valid *reference data* even though none of them
may be MVP-*pricing*-eligible (docs/20 §5).

It carries no market observation, valuation date, resolved rate, or
pricing output (docs/20 §3's full exclusion list) -- the same boundary
already enforced on ``DepositLeg`` and ``BondLinkedStructuredProduct``.
"""

from __future__ import annotations

from dataclasses import dataclass

from shiori_pricing_lab.products.enums import (
    BondYieldConvention,
    BusinessDayConvention,
    Currency,
    DayCount,
    Frequency,
    coerce_enum,
)
from shiori_pricing_lab.reference_data._validation import (
    _parse_iso_date,
    _require_finite_number,
    _require_non_blank,
)
from shiori_pricing_lab.reference_data.enums import BondStatus, BondType


@dataclass(frozen=True)
class BondReferenceData:
    """Bond Master reference data: static, security-level terms only.

    Every field here is required (per ``docs/20`` §4): the full Bond Master
    field set must be present and this schema does not downgrade any of them
    to optional, including ``first_coupon_date`` / ``last_coupon_date``
    (docs/20 §4/§10: a missing first/last coupon date must be rejected, not
    silently treated as "no stub").
    """

    isin: str
    issuer: str
    currency: Currency
    coupon: float
    coupon_frequency: Frequency
    maturity_date: str
    issue_date: str
    day_count: DayCount
    business_day_convention: BusinessDayConvention
    redemption_amount: float
    callable_flag: bool
    sinkable_flag: bool
    bond_type: BondType
    yield_convention: BondYieldConvention
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
        object.__setattr__(
            self,
            "business_day_convention",
            coerce_enum(
                self.business_day_convention, BusinessDayConvention, "business_day_convention"
            ),
        )
        object.__setattr__(self, "bond_type", coerce_enum(self.bond_type, BondType, "bond_type"))
        object.__setattr__(
            self,
            "yield_convention",
            coerce_enum(self.yield_convention, BondYieldConvention, "yield_convention"),
        )
        object.__setattr__(self, "status", coerce_enum(self.status, BondStatus, "status"))

        _require_non_blank(self.isin, "isin")
        _require_non_blank(self.issuer, "issuer")

        # coupon >= 0: negative coupons are rejected outright. coupon == 0 is
        # accepted here as valid reference data -- zero-coupon MVP-pricing
        # eligibility is a separate decision, see reference_data.eligibility
        # (docs/20 §4/§5/§10).
        _require_finite_number(self.coupon, "coupon")
        if self.coupon < 0:
            raise ValueError(f"coupon must be non-negative, got {self.coupon}")

        issue = _parse_iso_date(self.issue_date, "issue_date")
        maturity = _parse_iso_date(self.maturity_date, "maturity_date")
        if issue >= maturity:
            raise ValueError(
                f"issue_date ({self.issue_date}) must be before "
                f"maturity_date ({self.maturity_date})"
            )

        # first_coupon_date / last_coupon_date are required, non-null, strict
        # ISO dates (docs/20 §4/§10). This schema only records the dates; it
        # does not generate a coupon schedule or detect an irregular stub
        # from them -- see reference_data.eligibility and
        # reference_data.fixtures for how irregular-stub bonds are kept out
        # of the MVP pricing pool without a schedule engine.
        first_coupon = _parse_iso_date(self.first_coupon_date, "first_coupon_date")
        last_coupon = _parse_iso_date(self.last_coupon_date, "last_coupon_date")

        # Basic static date-order sanity (Codex P2 review of PR #58): this is
        # not schedule generation, stub detection, or business-day rolling --
        # it only rejects impossible static Bond Master records (a first
        # coupon on/before issue, a last coupon after maturity, or a first
        # coupon after the last one). The invariant is
        # issue_date < first_coupon_date <= last_coupon_date <= maturity_date;
        # a zero-coupon bond with first_coupon_date == last_coupon_date ==
        # maturity_date still satisfies it.
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

        _require_finite_number(self.redemption_amount, "redemption_amount")
        if not self.redemption_amount > 0:
            raise ValueError(f"redemption_amount must be positive, got {self.redemption_amount}")

        if not isinstance(self.callable_flag, bool):
            raise ValueError(f"callable_flag must be a bool, got {self.callable_flag!r}")
        if not isinstance(self.sinkable_flag, bool):
            raise ValueError(f"sinkable_flag must be a bool, got {self.sinkable_flag!r}")

        if isinstance(self.ex_dividend_days, bool) or not isinstance(self.ex_dividend_days, int):
            raise ValueError(
                f"ex_dividend_days must be a non-negative integer, got {self.ex_dividend_days!r}"
            )
        if self.ex_dividend_days < 0:
            raise ValueError(
                f"ex_dividend_days must be a non-negative integer, got {self.ex_dividend_days}"
            )
