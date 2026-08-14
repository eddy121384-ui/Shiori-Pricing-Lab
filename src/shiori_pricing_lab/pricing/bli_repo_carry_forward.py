"""Bloomberg FPA repo-carry forward primitive (Issue #173/#175, prototype).

Scope: one deterministic primitive that rebuilds a UST forward clean price
from a spot quote and an explicit repo/carry funding rate, exactly as
Bloomberg's own FPA Help documents the forward structure::

    Settlement Invoice Amount x (1 + (Repo Rate x Term) / Day Count)
        = Termination Invoice Amount

    ((Termination Invoice Amount - Termination Accrued Interest) / Face Amount) x 100
        = Forward Price

Expressed per 100 face -- the unit every price in this repository already
uses -- that is exactly::

    Spot Dirty(tS)    = Spot Clean(tS) + AI(tS)
    Carry Factor      = 1 + repo_rate_decimal x (days(tS, tF) / 360)
    Forward Dirty(tF) = Spot Dirty(tS) x Carry Factor
    Forward Clean(tF) = Forward Dirty(tF) - AI(tF)

**Case A only.** A coupon paid in ``(tS, tF]`` would need a further term
per coupon, and this module refuses that case outright -- see the RED note
below for why, and for what would be needed to lift it.

**This module derives no funding rate of its own.** ``repo_rate_decimal``
is a caller input; where it comes from (a Bloomberg Curve #490 / S490
transformation, a trader entry, a test fixture) is deliberately not this
module's concern. Issue #173's S490 funding source lives in its own
separate, swappable module,
``pricing/bli_s490_funding_resolver.py`` -- so the funding transformation
can be replaced without touching this formula, and this formula can be
tested without any curve at all.

**Explicit, traceable conventions (Issue #173 requirement).** Both
conventions this formula depends on are named constants in this module,
carried verbatim onto every returned result, and never inferred:

- ``REPO_DAY_COUNT_CONVENTION = "ACT/360"`` -- the repo term basis, the
  convention Bloomberg's OVME F screen displays (``Day Count = ACT/360``)
  for the S490 repo source Issue #173 targets. The term is actual calendar
  days between the two explicit settlement dates over 360; no business-day
  calendar, holiday rule, or settlement-lag derivation exists anywhere in
  this module.
- ``REPO_COMPOUNDING_CONVENTION = "SIMPLE"`` -- the single ``1 + r x t``
  accrual the FPA Help formula above states literally. **This is a labeled
  Issue #173 prototype assumption, not a proven match**: the same OVME F
  screen also displays ``Comp Method = Scientific``, whose exact meaning
  for a sub-year repo horizon is not proven by any evidence this
  repository holds. Issue #173's own instruction for exactly this
  situation is to implement the documented FPA structure explicitly, label
  the uncertainty, and let OVME F parity decide -- never to introduce an
  unexplained adjustment to force agreement. Nothing in this module
  adjusts, calibrates, or tunes anything.

**RED, unresolved: the interim coupon's payment date (Issue #175).** Issue
#175 asked whether this repo-carry structure still reconstructs OVME F when
a coupon falls inside the repo term. Extending the FPA structure to do so is
straightforward -- each coupon reduces the termination amount, carried from
its own payment date -- but it needs the date on which the coupon cash is
**actually received**, and this repository cannot supply one:

``coupon_flows_before`` returns the bond's **unadjusted** schedule dates
(``bli_quantlib_bond_adapter`` generates them with ``ql.NullCalendar()``, by
design, and no business-day calendar exists anywhere here). That is correct
and already approved for **accrued interest**, where accrual conventionally
stops on the nominal date. It is *not* established for **cash receipt**: a
US Treasury coupon scheduled on a non-business day is paid on the next
business day, so the nominal date and the receipt date are different dates
serving different purposes, and only the first use is covered by existing
approval.

Two conventions are therefore in play -- (A) the unadjusted scheduled date,
(B) the actual next-business-day payment date -- and the choice is not
cosmetic:

- it changes *how long* each coupon is reinvested: small, ~4.2e-04 per 100
  (~0.013 of a 32nd) for two days on a 2.00 coupon at 3.77%; and
- it changes *whether the coupon is in the window at all* when ``tF`` falls
  between the two dates: on a 2.00 coupon, ~64 ticks.

No first-party evidence in this repository says which date Bloomberg's FPA
forward uses, and resolving it needs a US government securities holiday
calendar that Issue #175 did not authorise. **So this module does not
choose, and does not compute.** Every coupon scheduled in ``(tS, tF]``
raises :class:`RepoCarryInterimCouponPaymentDateUnresolvedError` -- weekday
coupons included, because a weekday market holiday is equally not a payment
date and is equally undetectable without that calendar. A partial
(weekend-only) guard was tried and rejected: it left the dangerous half of
the exposure in place while implying that the dates which passed had been
validated.

The reinvestment *rate* would be a second, much smaller question if this one
were settled (reinvest at the same term repo rate, which a zero ``Repo
Spread`` implies; the alternatives -- an Annex A SS A.5.2 style per-coupon
discount-factor ratio, or no reinvestment at all -- differ by ~0.0006 and
~0.07 of a 32nd respectively, all below OVME F's own quarter-tick display
granularity). It is recorded here only so the sizing is not lost; nothing in
this module implements, selects between, or tunes any of them.

**Composition, not reimplementation.** Accrued interest at both settlement
dates comes from the already-reviewed
``pricing/bli_quantlib_bond_adapter.accrued_interest_per_100``; the interim
coupon dates and amounts come from that same module's
``coupon_flows_before`` -- this module builds no second coupon schedule and
computes no coupon amount of its own. Date parsing is
``data/_validation._parse_iso_date``. Every error those helpers raise
(irregular coupon grid, ex-dividend window, maturity cashflow in the window,
QuantLib not installed, ...) propagates unchanged.

**Not in this module.** No Black-76, no option discounting, no volatility,
no curve construction, interpolation, or bootstrap, no repo-rate
derivation, no OVME screen parsing, no settlement-date/calendar
derivation, and no wiring into the existing standalone explicit-forward
override path (``BLIForwardCleanPriceInput``), which is untouched by Issue
#173.

**No system clock:** every date is an explicit caller-supplied ISO date
string.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from shiori_pricing_lab.data._validation import _parse_iso_date
from shiori_pricing_lab.data.bli_standalone_contract import StandaloneBondReferenceData
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import (
    accrued_interest_per_100,
    coupon_flows_before,
)

# The two repo conventions this formula depends on, named once and carried
# verbatim onto every result -- see the module docstring for why SIMPLE is a
# labeled prototype assumption rather than a proven OVME match.
REPO_DAY_COUNT_CONVENTION = "ACT/360"
REPO_DAY_COUNT_BASIS_DAYS = 360.0
REPO_COMPOUNDING_CONVENTION = "SIMPLE"


class RepoCarryInterimCouponPaymentDateUnresolvedError(ValueError):
    """A coupon falls in ``(tS, tF]`` and its actual payment date is unknown.

    ``coupon_flows_before`` returns unadjusted ``NullCalendar`` schedule
    dates. Using one as a *cash-receipt* date -- which a reinvestment leg
    requires -- is an unresolved convention in this repository, and it can
    change whether the coupon is in ``(tS, tF]`` at all, not merely how long
    it is reinvested. Raised for **every** interim coupon, not only ones
    whose scheduled date is obviously not a business day: see the module
    docstring's RED note for why a partial guard was the wrong answer.
    """


def _require_finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    return float(value)


def repo_term_days(spot_settlement_date: str, forward_settlement_date: str) -> int:
    """Return actual calendar days from ``spot`` to ``forward`` settlement.

    Both arguments are explicit ISO ``YYYY-MM-DD`` strings; neither is
    derived from a settlement lag, calendar, or business-day rule.
    ``forward_settlement_date`` must be strictly after
    ``spot_settlement_date`` -- a zero or negative repo term is not a
    forward and raises ``ValueError`` rather than producing a carry factor
    of exactly 1.0 or less.
    """

    spot = _parse_iso_date(spot_settlement_date, "spot_settlement_date")
    forward = _parse_iso_date(forward_settlement_date, "forward_settlement_date")
    if forward <= spot:
        raise ValueError(
            f"forward_settlement_date ({forward_settlement_date!r}) must be strictly after "
            f"spot_settlement_date ({spot_settlement_date!r})"
        )
    return (forward - spot).days


def repo_term_year_fraction(spot_settlement_date: str, forward_settlement_date: str) -> float:
    """Return the ACT/360 repo term fraction between the two settlement dates.

    ``repo_term_days(...) / 360`` -- the ``REPO_DAY_COUNT_CONVENTION``
    named above and nothing else. This is deliberately *not* the ACT/365F
    curve-internal coordinate the zero-curve helpers use: the repo term and
    the curve coordinate are two different conventions and Issue #173
    requires both to stay explicit and separately traceable.
    """

    return repo_term_days(spot_settlement_date, forward_settlement_date) / REPO_DAY_COUNT_BASIS_DAYS


def carry_factor_from_simple_repo_rate(
    *, repo_rate_decimal: float, repo_term_year_fraction: float
) -> float:
    """Return ``1 + repo_rate_decimal * repo_term_year_fraction``.

    The FPA Help ``(1 + (Repo Rate x Term) / Day Count)`` factor, with the
    term already expressed as an ACT/360 fraction by
    :func:`repo_term_year_fraction`. ``repo_rate_decimal`` is a decimal
    fraction (``0.0377`` for 3.77%), never a percent. A resulting factor
    that is not strictly positive raises ``ValueError`` rather than
    producing a non-positive forward invoice amount.
    """

    rate = _require_finite(repo_rate_decimal, "repo_rate_decimal")
    term = _require_finite(repo_term_year_fraction, "repo_term_year_fraction")
    factor = 1.0 + rate * term
    if not factor > 0:
        raise ValueError(
            f"carry factor must be positive, got {factor!r} from repo_rate_decimal={rate!r} "
            f"and repo_term_year_fraction={term!r}"
        )
    return factor


@dataclass(frozen=True)
class RepoCarryForward:
    """Every traceable step of one FPA repo-carry forward calculation.

    Issue #173 requires the spot clean -> spot dirty -> carry -> forward
    dirty -> forward clean transition to be individually inspectable, so
    every intermediate value is a field here rather than a discarded local.
    ``repo_day_count_convention`` / ``repo_compounding_convention`` carry
    this module's own named constants verbatim. There are no interim-coupon
    fields: a horizon containing a coupon never produces a result at all
    (see the module docstring's RED note).
    """

    spot_settlement_date: str
    forward_settlement_date: str
    spot_clean_price_per_100: float
    accrued_interest_at_spot_settlement_per_100: float
    spot_dirty_price_per_100: float
    repo_rate_decimal: float
    repo_day_count_convention: str
    repo_compounding_convention: str
    repo_term_days: int
    repo_term_year_fraction: float
    carry_factor: float
    forward_dirty_price_per_100: float
    accrued_interest_at_forward_settlement_per_100: float
    forward_clean_price_per_100: float


def repo_carry_forward_clean_price(
    *,
    bond: StandaloneBondReferenceData,
    spot_clean_price_per_100: float,
    spot_settlement_date: str,
    forward_settlement_date: str,
    repo_rate_decimal: float,
) -> RepoCarryForward:
    """Return the FPA repo-carry forward for ``bond`` at ``forward_settlement_date``.

    Composes ``coupon_flows_before`` over ``(spot_settlement_date,
    forward_settlement_date]`` and ``accrued_interest_per_100`` at both
    explicit settlement dates, then applies the FPA structure in the module
    docstring.

    **Case A only.** Every coupon scheduled in ``(spot_settlement_date,
    forward_settlement_date]`` raises
    :class:`RepoCarryInterimCouponPaymentDateUnresolvedError` -- weekday
    coupons included -- because the date this repository holds for a coupon
    is an unadjusted schedule date rather than a cash-receipt date (the
    module docstring's RED note). No Case B forward is produced by any
    caller, by any route.

    Raises :class:`TypeError` for a ``bond`` the accrual adapter does not
    accept, and :class:`ValueError` for a non-finite/non-positive spot clean
    price or a non-positive repo term. Every other error propagates
    unchanged from the composed helpers -- notably
    ``BLIBondMaturityCashflowUnsupportedError`` when the window reaches the
    bond's maturity date, which ``coupon_flows_before`` raises before the
    interim-coupon refusal above.
    """

    spot_clean = _require_finite(spot_clean_price_per_100, "spot_clean_price_per_100")
    if not spot_clean > 0:
        raise ValueError(f"spot_clean_price_per_100 must be positive, got {spot_clean!r}")

    term_days = repo_term_days(spot_settlement_date, forward_settlement_date)
    term_year_fraction = term_days / REPO_DAY_COUNT_BASIS_DAYS

    scheduled_interim_coupons = coupon_flows_before(
        bond,
        after_date=spot_settlement_date,
        on_or_before_date=forward_settlement_date,
    )
    if scheduled_interim_coupons:
        raise RepoCarryInterimCouponPaymentDateUnresolvedError(
            f"{len(scheduled_interim_coupons)} coupon(s) are scheduled in "
            f"({spot_settlement_date}, {forward_settlement_date}] on "
            + ", ".join(flow.payment_date for flow in scheduled_interim_coupons)
            + " -- those are the bond's *unadjusted* schedule dates (NullCalendar), not "
            "the dates the coupon cash is actually received. This repository holds no "
            "business-day calendar to resolve the actual payment dates, and no "
            "first-party evidence says which date Bloomberg's FPA forward reinvests "
            "from. The choice changes how long each coupon is reinvested and -- when the "
            "forward settlement date falls between a coupon's scheduled and actual "
            "payment date -- whether that coupon is subtracted at all. That is an "
            "unresolved methodology decision (Issue #175 RED), not a value to assume. "
            "Extending the FPA structure to carry the coupon is straightforward; "
            "the blocked input is the date, not the arithmetic"
        )
    accrued_at_spot = accrued_interest_per_100(bond, as_of_date=spot_settlement_date)
    accrued_at_forward = accrued_interest_per_100(bond, as_of_date=forward_settlement_date)

    spot_dirty = spot_clean + accrued_at_spot
    carry_factor = carry_factor_from_simple_repo_rate(
        repo_rate_decimal=repo_rate_decimal,
        repo_term_year_fraction=term_year_fraction,
    )
    forward_dirty = spot_dirty * carry_factor
    forward_clean = forward_dirty - accrued_at_forward

    return RepoCarryForward(
        spot_settlement_date=spot_settlement_date,
        forward_settlement_date=forward_settlement_date,
        spot_clean_price_per_100=spot_clean,
        accrued_interest_at_spot_settlement_per_100=accrued_at_spot,
        spot_dirty_price_per_100=spot_dirty,
        repo_rate_decimal=float(repo_rate_decimal),
        repo_day_count_convention=REPO_DAY_COUNT_CONVENTION,
        repo_compounding_convention=REPO_COMPOUNDING_CONVENTION,
        repo_term_days=term_days,
        repo_term_year_fraction=term_year_fraction,
        carry_factor=carry_factor,
        forward_dirty_price_per_100=forward_dirty,
        accrued_interest_at_forward_settlement_per_100=accrued_at_forward,
        forward_clean_price_per_100=forward_clean,
    )
