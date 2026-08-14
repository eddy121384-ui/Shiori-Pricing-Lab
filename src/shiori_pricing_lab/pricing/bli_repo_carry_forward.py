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
                        - SUM_i Coupon_i x (1 + repo_rate_decimal
                                            x (days(pay_i, tF) / 360))
    Forward Clean(tF) = Forward Dirty(tF) - AI(tF)

where ``i`` runs over every coupon whose **scheduled** date falls in
``(tS, tF]`` and ``pay_i`` is the date that coupon's cash is **actually
paid** -- see "Interim coupons" below. Case A (no such coupon) is the empty
sum, so both cases are one formula and one code path.

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

**Interim coupons (Issue #175, Case B).** A coupon received inside the repo
term reduces what is owed at termination, carried from its own payment date
to ``tF`` by the *same* ``1 + r x t`` FPA factor the whole position is
carried by, over that coupon's own ACT/360 term. Two named constants govern
it, both carried verbatim onto every result:

- ``INTERIM_COUPON_TREATMENT`` -- reinvested at the repo rate from the
  coupon's actual payment date to ``tF``, simple, ACT/360.
- the payment date itself comes from
  ``pricing/bli_ust_coupon_payment_date.resolve_ust_coupon_payment_date``,
  which carries its own ``UST_COUPON_PAYMENT_CALENDAR`` /
  ``UST_COUPON_PAYMENT_ROLL_CONVENTION``. This module resolves no calendar
  and rolls no date itself.

**Scheduled date selects; payment date carries.** These are two different
dates doing two different jobs, and conflating them was Issue #175's whole
blocker:

- *Which* coupons are interim is decided by the **scheduled** date, because
  entitlement follows the coupon's own record date: a forward buyer settling
  at ``tS`` is entitled to the coupons scheduled in ``(tS, tF]``, and to no
  others. This is exactly the window ``coupon_flows_before`` already returns,
  unchanged.
- *When each coupon's cash arrives*, and therefore how long it is
  reinvested, is decided by the **payment** date. Under Eddy's Issue #175
  decision (convention B) a scheduled date that is not a Federal Reserve
  business day rolls forward to the next one, with no additional coupon
  interest -- so the amount is untouched and only the term moves.

A consequence worth stating plainly: when a coupon is scheduled at or very
near ``tF``, its payment date can fall **after** ``tF``. The holder is still
entitled to it (its scheduled date is in the window), but the cash arrives a
few days late, so its reinvestment term is *negative* and the same
``1 + r x t`` factor becomes a small discount rather than an accrual. That
is deliberate and is the economically correct answer: dropping such a coupon
instead would mis-state the forward by the coupon's whole face amount --
around 64 ticks on a 2.00 coupon -- which is precisely the cliff Issue #175
identified. A coupon scheduled exactly on ``tF`` and paid on ``tF`` has a
zero term and a factor of exactly ``1.0``.

**What is a labeled assumption and what is not.** The payment-date
convention is Eddy's explicit decision, not an inference. The *reinvestment
rate* remains a labeled prototype assumption: this module reinvests at the
same term repo rate it funds at, which a zero ``Repo Spread`` implies. The
alternatives -- an Annex A SS A.5.2 style per-coupon discount-factor ratio,
or no reinvestment at all -- differ by roughly 0.0006 and 0.07 of a 32nd
respectively, both below OVME F's own quarter-tick display granularity, so
parity cannot decide between them. Every per-coupon term and factor is on
the result, so either alternative is recoverable from the trace by
inspection. Nothing here is calibrated to an observed OVME number.

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
from shiori_pricing_lab.pricing.bli_ust_coupon_payment_date import (
    resolve_ust_coupon_payment_date,
)

# The two repo conventions this formula depends on, named once and carried
# verbatim onto every result -- see the module docstring for why SIMPLE is a
# labeled prototype assumption rather than a proven OVME match.
REPO_DAY_COUNT_CONVENTION = "ACT/360"
REPO_DAY_COUNT_BASIS_DAYS = 360.0
REPO_COMPOUNDING_CONVENTION = "SIMPLE"


# How a coupon received inside the repo term reaches the forward date --
# named once and carried verbatim onto every result, Case A results included
# (where it applies over an empty coupon set). See the module docstring for
# what is Eddy's decision here and what remains a labeled assumption.
INTERIM_COUPON_TREATMENT = (
    "INTERIM_COUPON_REINVESTED_AT_REPO_RATE_FROM_ACTUAL_PAYMENT_DATE__SIMPLE_ACT360__PROTOTYPE"
)


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
class RepoCarryInterimCoupon:
    """One coupon scheduled in ``(tS, tF]``, carried to the forward date.

    ``scheduled_payment_date``/``amount_per_100`` are the composed
    ``coupon_flows_before`` flow's own values, echoed verbatim -- this module
    recomputes no coupon amount, and the roll never changes one (Eddy's
    "no additional coupon interest"). ``payment_date``, ``payment_roll_days``,
    ``payment_calendar`` and ``payment_roll_convention`` come from
    ``bli_ust_coupon_payment_date`` verbatim.

    ``reinvestment_term_days`` is actual calendar days from the *payment*
    date to the forward settlement date. It is **negative** when a coupon
    scheduled on or before ``tF`` is paid after it -- see the module
    docstring -- in which case ``reinvestment_factor`` is a small discount
    below ``1.0`` rather than an accrual above it.
    """

    scheduled_payment_date: str
    payment_date: str
    payment_roll_days: int
    payment_calendar: str
    payment_roll_convention: str
    amount_per_100: float
    reinvestment_term_days: int
    reinvestment_term_year_fraction: float
    reinvestment_factor: float
    forward_value_per_100: float


def _carry_interim_coupon(
    *,
    scheduled_payment_date: str,
    amount_per_100: float,
    forward_settlement_date: str,
    repo_rate_decimal: float,
) -> RepoCarryInterimCoupon:
    """Carry one interim coupon from its actual payment date to ``tF``.

    Private: the public surface of this module is
    :func:`repo_carry_forward_clean_price` alone, so there is no second,
    directly callable coupon-pricing entry point (Codex P1 review of PR
    #176). Composes ``resolve_ust_coupon_payment_date`` for the date and
    :func:`carry_factor_from_simple_repo_rate` for the factor; the only
    arithmetic here is the ACT/360 term and the amount x factor product.
    """

    amount = _require_finite(amount_per_100, "amount_per_100")
    payment = resolve_ust_coupon_payment_date(scheduled_payment_date)
    term_days = (
        _parse_iso_date(forward_settlement_date, "forward_settlement_date")
        - _parse_iso_date(payment.payment_date, "payment_date")
    ).days
    term_year_fraction = term_days / REPO_DAY_COUNT_BASIS_DAYS
    reinvestment_factor = carry_factor_from_simple_repo_rate(
        repo_rate_decimal=repo_rate_decimal,
        repo_term_year_fraction=term_year_fraction,
    )
    return RepoCarryInterimCoupon(
        scheduled_payment_date=payment.scheduled_payment_date,
        payment_date=payment.payment_date,
        payment_roll_days=payment.roll_days,
        payment_calendar=payment.payment_calendar,
        payment_roll_convention=payment.roll_convention,
        amount_per_100=amount,
        reinvestment_term_days=term_days,
        reinvestment_term_year_fraction=term_year_fraction,
        reinvestment_factor=reinvestment_factor,
        forward_value_per_100=amount * reinvestment_factor,
    )


@dataclass(frozen=True)
class RepoCarryForward:
    """Every traceable step of one FPA repo-carry forward calculation.

    Issue #173 requires the spot clean -> spot dirty -> carry -> forward
    dirty -> forward clean transition to be individually inspectable, so
    every intermediate value is a field here rather than a discarded local.
    ``repo_day_count_convention`` / ``repo_compounding_convention`` /
    ``interim_coupon_treatment`` carry this module's own named constants
    verbatim.

    ``interim_coupons`` is every coupon scheduled in ``(tS, tF]``, in
    scheduled-date order, each with its own payment date and reinvestment
    leg. It is empty for Case A, where
    ``interim_coupon_forward_value_per_100`` is then exactly ``0.0`` and
    ``forward_dirty_price_per_100`` equals ``carried_spot_dirty_price_per_100``
    unchanged.
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
    carried_spot_dirty_price_per_100: float
    interim_coupon_treatment: str
    interim_coupons: tuple[RepoCarryInterimCoupon, ...]
    interim_coupon_forward_value_per_100: float
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

    Every coupon scheduled in ``(spot_settlement_date,
    forward_settlement_date]`` is carried to ``tF`` from its own **actual
    payment date** and subtracted (Issue #175, Case B -- see the module
    docstring for what selects a coupon and what carries it). A horizon with
    no such coupon is the same code path over an empty coupon set and is
    arithmetically identical to Case A.

    Raises :class:`TypeError` for a ``bond`` the accrual adapter does not
    accept, and :class:`ValueError` for a non-finite/non-positive spot clean
    price, a non-positive repo term, a coupon whose reinvestment factor is
    not positive, or a forward dirty price that is not positive after the
    interim coupons are subtracted. Every other error propagates unchanged
    from the composed helpers -- notably
    ``BLIBondMaturityCashflowUnsupportedError`` when the window reaches the
    bond's maturity date, and
    ``BLICouponPaymentCalendarUnavailableError`` when QuantLib is absent and
    a coupon's payment date therefore cannot be resolved.
    """

    spot_clean = _require_finite(spot_clean_price_per_100, "spot_clean_price_per_100")
    if not spot_clean > 0:
        raise ValueError(f"spot_clean_price_per_100 must be positive, got {spot_clean!r}")

    term_days = repo_term_days(spot_settlement_date, forward_settlement_date)
    term_year_fraction = term_days / REPO_DAY_COUNT_BASIS_DAYS

    interim_coupons = tuple(
        _carry_interim_coupon(
            scheduled_payment_date=flow.payment_date,
            amount_per_100=flow.amount_per_100,
            forward_settlement_date=forward_settlement_date,
            repo_rate_decimal=repo_rate_decimal,
        )
        for flow in coupon_flows_before(
            bond,
            after_date=spot_settlement_date,
            on_or_before_date=forward_settlement_date,
        )
    )
    interim_coupon_forward_value = sum(
        (coupon.forward_value_per_100 for coupon in interim_coupons), 0.0
    )

    accrued_at_spot = accrued_interest_per_100(bond, as_of_date=spot_settlement_date)
    accrued_at_forward = accrued_interest_per_100(bond, as_of_date=forward_settlement_date)

    spot_dirty = spot_clean + accrued_at_spot
    carry_factor = carry_factor_from_simple_repo_rate(
        repo_rate_decimal=repo_rate_decimal,
        repo_term_year_fraction=term_year_fraction,
    )
    carried_spot_dirty = spot_dirty * carry_factor
    forward_dirty = carried_spot_dirty - interim_coupon_forward_value
    if not forward_dirty > 0:
        raise ValueError(
            f"forward dirty price must be positive, got {forward_dirty!r} from carried spot "
            f"dirty {carried_spot_dirty!r} less {len(interim_coupons)} interim coupon(s) "
            f"worth {interim_coupon_forward_value!r} at {forward_settlement_date!r} -- no "
            "forward clean price is produced from a non-positive termination amount"
        )
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
        carried_spot_dirty_price_per_100=carried_spot_dirty,
        interim_coupon_treatment=INTERIM_COUPON_TREATMENT,
        interim_coupons=interim_coupons,
        interim_coupon_forward_value_per_100=interim_coupon_forward_value,
        forward_dirty_price_per_100=forward_dirty,
        accrued_interest_at_forward_settlement_per_100=accrued_at_forward,
        forward_clean_price_per_100=forward_clean,
    )
