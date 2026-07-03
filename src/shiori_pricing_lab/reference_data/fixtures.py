"""Small, manually reviewed synthetic Bond Reference Data fixture (docs/20 §7).

Per ``docs/20`` §7, Bond Reference Data may be supplied for MVP as a
small, manually reviewed fixture -- a fixed set of hand-written records
checked into the repo, not a live-loaded file. This module is exactly
that: a deterministic tuple of synthetic bonds, nothing else.

**Explicitly not in this module:** a generic file import, a parser, a
Bloomberg/API connector, screenshot capture, or any lookup/resolution
helper (docs/20 §11 defers designing a lookup interface to a future
pricing-engine slice). Consumers filter or search ``SYNTHETIC_BOND_FIXTURES``
directly.

Every bond below is invented and clearly synthetic (``issuer`` and
``isin`` are placeholder values, not real securities), matching
`AGENTS.md` rule 6 and `docs/09`'s "synthetic data only" invariant.

**Irregular-stub handling by construction (docs/20 §5/§10):** none of
these fixture bonds has an irregular first/last coupon period --
``first_coupon_date`` is exactly one ``coupon_frequency`` period after
``issue_date`` and ``last_coupon_date`` is exactly one ``coupon_frequency``
period before ``maturity_date`` for every coupon-bearing bond, by manual
construction, not by runtime schedule detection (this repo has no
schedule engine, and a wrong heuristic would be worse than none -- see
``reference_data.eligibility`` module docstring). The zero-coupon bond
carries no real coupon schedule; its ``first_coupon_date`` /
``last_coupon_date`` are both set to its ``maturity_date``, since Annex B
§B.5 requires both fields even where a coupon schedule is not
economically meaningful.
"""

from __future__ import annotations

from shiori_pricing_lab.products.enums import (
    BondYieldConvention,
    BusinessDayConvention,
    Currency,
    DayCount,
    Frequency,
)
from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData
from shiori_pricing_lab.reference_data.enums import BondStatus, BondType

# A plain-vanilla, fixed-coupon, bullet bond -- MVP-pricing-eligible.
_SYNTHETIC_VANILLA_BULLET = BondReferenceData(
    isin="XS0000000001",
    issuer="Synthetic Test Issuer A",
    currency=Currency.USD,
    coupon=0.0325,
    coupon_frequency=Frequency.SEMI_ANNUAL,
    maturity_date="2030-06-15",
    issue_date="2025-06-15",
    day_count=DayCount.ACT_ACT_ISDA,
    business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
    redemption_amount=100.0,
    callable_flag=False,
    sinkable_flag=False,
    bond_type=BondType.FIXED_COUPON_BULLET,
    yield_convention=BondYieldConvention.SEMI_ANNUAL_COMPOUND,
    ex_dividend_days=1,
    first_coupon_date="2025-12-15",
    last_coupon_date="2029-12-15",
    status=BondStatus.ACTIVE,
)

# A zero-coupon bullet bond -- valid reference data, but declared
# not MVP-pricing-eligible by this implementation slice (docs/20 §5).
_SYNTHETIC_ZERO_COUPON_BULLET = BondReferenceData(
    isin="XS0000000002",
    issuer="Synthetic Test Issuer B",
    currency=Currency.USD,
    coupon=0.0,
    coupon_frequency=Frequency.ANNUAL,
    maturity_date="2028-03-01",
    issue_date="2025-03-01",
    day_count=DayCount.ACT_365_FIXED,
    business_day_convention=BusinessDayConvention.FOLLOWING,
    redemption_amount=100.0,
    callable_flag=False,
    sinkable_flag=False,
    bond_type=BondType.FIXED_COUPON_BULLET,
    yield_convention=BondYieldConvention.SIMPLE_YIELD,
    ex_dividend_days=0,
    first_coupon_date="2028-03-01",
    last_coupon_date="2028-03-01",
    status=BondStatus.ACTIVE,
)

# A callable bond -- valid reference data, ineligible for MVP pricing.
_SYNTHETIC_CALLABLE_BOND = BondReferenceData(
    isin="XS0000000003",
    issuer="Synthetic Test Issuer C",
    currency=Currency.USD,
    coupon=0.045,
    coupon_frequency=Frequency.SEMI_ANNUAL,
    maturity_date="2032-01-10",
    issue_date="2025-01-10",
    day_count=DayCount.THIRTY_360,
    business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
    redemption_amount=100.0,
    callable_flag=True,
    sinkable_flag=False,
    bond_type=BondType.FIXED_COUPON_BULLET,
    yield_convention=BondYieldConvention.SEMI_ANNUAL_COMPOUND,
    ex_dividend_days=1,
    first_coupon_date="2025-07-10",
    last_coupon_date="2031-07-10",
    status=BondStatus.ACTIVE,
)

# A floating-rate note -- valid reference data, ineligible for MVP pricing
# via the bond_type exclusion mechanism (docs/20 §5 option (a)).
_SYNTHETIC_FLOATING_RATE_NOTE = BondReferenceData(
    isin="XS0000000004",
    issuer="Synthetic Test Issuer D",
    currency=Currency.USD,
    coupon=0.0,
    coupon_frequency=Frequency.QUARTERLY,
    maturity_date="2029-09-20",
    issue_date="2025-09-20",
    day_count=DayCount.ACT_360,
    business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
    redemption_amount=100.0,
    callable_flag=False,
    sinkable_flag=False,
    bond_type=BondType.FLOATING_RATE_NOTE,
    yield_convention=BondYieldConvention.SIMPLE_YIELD,
    ex_dividend_days=0,
    first_coupon_date="2025-12-20",
    last_coupon_date="2029-06-20",
    status=BondStatus.ACTIVE,
)

SYNTHETIC_BOND_FIXTURES: tuple[BondReferenceData, ...] = (
    _SYNTHETIC_VANILLA_BULLET,
    _SYNTHETIC_ZERO_COUPON_BULLET,
    _SYNTHETIC_CALLABLE_BOND,
    _SYNTHETIC_FLOATING_RATE_NOTE,
)
