"""MVP plain-vanilla pricing eligibility for ``BondReferenceData`` (docs/20 §5).

Deliberately separate from ``BondReferenceData.__post_init__``: a bond can
be perfectly valid *reference data* (docs/20 §4/§10) while still being
out of scope for the MVP *pricing* pool -- a callable bond, a zero-coupon
bond, and an ``OTHER``-yield-convention bond are all constructible, but
none of them may be priced by the MVP slice. This module is the single
place that decides "may the MVP price this bond," so a future pricing
engine has one function to call rather than re-deriving the rule.

No pricing, cash-flow generation, or schedule engine is added here. This
resolves several open items ``docs/20`` explicitly left to the
implementation slice:

- **Floating-rate / amortizing / convertible / inflation-linked /
  perpetual / structured-note exclusion** (docs/20 §5): resolved via
  option (a) -- ``bond_type`` is the exclusion signal; only
  ``BondType.FIXED_COUPON_BULLET`` is eligible.
- **Zero-coupon MVP-pricing eligibility** (docs/20 §5): resolved as
  **valid-but-ineligible** -- ``coupon == 0`` bonds construct successfully
  as reference data but are declared out of MVP pricing scope for this
  first implementation (the stricter, "also acceptable" choice docs/20 §5
  offers). This is a deliberately conservative default, not a claim that
  zero-coupon bonds could never be priced by a future MVP slice.
- **Irregular first/last coupon stub handling** (docs/20 §5/§10): this
  module does **not** attempt to detect an irregular stub from
  ``first_coupon_date`` / ``last_coupon_date`` -- doing so correctly
  requires real schedule logic (accounting for stub conventions, business
  day rolling, etc.) that this repo does not have, and a wrong heuristic
  is worse than none (a false "regular" verdict would silently admit a
  stub bond into the pricing pool, exactly what docs/20 forbids). Instead,
  per docs/20 §5/§10's explicitly allowed fallback, the MVP fixture
  (``reference_data.fixtures``) is manually reviewed and limited to
  regular-coupon, no-stub bonds by construction; any known stub/irregular
  bond is out of MVP pricing scope by construction, not by runtime
  detection.
"""

from __future__ import annotations

from dataclasses import dataclass

from shiori_pricing_lab.products.enums import BondYieldConvention
from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData
from shiori_pricing_lab.reference_data.enums import BondType


@dataclass(frozen=True)
class EligibilityResult:
    """Outcome of an MVP plain-vanilla pricing eligibility check.

    ``reasons`` is empty when ``eligible`` is ``True``, and otherwise lists
    every rule that failed (a bond can fail more than one rule at once,
    e.g. callable *and* zero-coupon).
    """

    eligible: bool
    reasons: tuple[str, ...]


def is_mvp_pricing_eligible(bond: BondReferenceData) -> EligibilityResult:
    """Return whether ``bond`` may be priced by the MVP BLI pricing pool.

    This is a pure function of the bond's own reference data -- it does
    not look at market data, a valuation date, or any other bond, and it
    never mutates ``bond``.
    """

    reasons: list[str] = []

    if bond.callable_flag:
        reasons.append("callable bonds are not MVP-pricing-eligible")
    if bond.sinkable_flag:
        reasons.append("sinkable bonds are not MVP-pricing-eligible")
    if bond.yield_convention is BondYieldConvention.OTHER:
        reasons.append(
            "yield_convention OTHER is not MVP-pricing-eligible by default "
            "(docs/14 F-08 compounding-frequency gap is unresolved)"
        )
    if bond.bond_type is not BondType.FIXED_COUPON_BULLET:
        reasons.append(
            f"bond_type {bond.bond_type.value} is not MVP-pricing-eligible "
            "(only FIXED_COUPON_BULLET is eligible)"
        )
    if bond.coupon == 0:
        reasons.append(
            "zero-coupon bonds are valid reference data but are not "
            "MVP-pricing-eligible for this implementation slice (docs/20 §5)"
        )

    return EligibilityResult(eligible=not reasons, reasons=tuple(reasons))
