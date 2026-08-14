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
                                            x (days(coupon_date_i, tF) / 360))
    Forward Clean(tF) = Forward Dirty(tF) - AI(tF)

where ``coupon_date_i`` runs over every coupon paid in ``(tS, tF]``. Case A
(no such coupon) is the empty sum, so the two cases are one formula and one
code path, not two -- see "Interim coupons" below.

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

**Interim coupons (Issue #175, Case B).** Issue #173's first validation was
the no-interim-coupon case, and this module originally refused any coupon in
``(tS, tF]`` outright. It no longer does: a coupon paid inside the repo term
is carried to ``tF`` and subtracted from the termination amount, per
``INTERIM_COUPON_TREATMENT`` below.

- ``INTERIM_COUPON_TREATMENT =
  "INTERIM_COUPON_REINVESTED_AT_REPO_RATE_TO_FORWARD_SETTLEMENT__SIMPLE_ACT360__PROTOTYPE"``
  -- the holder of a repo-financed long position receives ``Coupon_i`` on
  its payment date, reinvests it at the same funding rate to ``tF``, and
  that accumulated value reduces what is owed at termination. The
  reinvestment factor is the *same* ``1 + r x t`` FPA factor applied over
  each coupon's own shorter ACT/360 term ``days(coupon_date_i, tF) / 360``;
  a coupon paid exactly on ``tF`` therefore has a term of zero days, a
  factor of exactly ``1.0``, and reduces the termination amount by its face
  amount alone.

**Why this treatment, and exactly how far the supporting precedent goes.**
Bloomberg's FPA Help text quoted above states the single-factor invoice
carry and is silent on interim coupons, so it does not settle this on its
own. The nearest supporting precedent is this repository's own
already-approved forward construction, Annex A SS A.5.2 in
``pricing/bli_forward_clean_price.py``, which subtracts the PV of every
in-window coupon from spot dirty and then divides by the terminal discount
factor. It supports the *shape* of the sum above -- an in-window coupon
reduces the termination value, carried from its own payment date -- and
nothing more. **It is not an exact algebraic equivalence, and this module
does not claim one** (Codex P2 review of PR #176, which was correct):

- Under *continuous* compounding of one flat rate the two coincide exactly,
  because ``exp(r x tF) / exp(r x t_i) == exp(r x (tF - t_i))``.
- Under the ``SIMPLE`` convention this module actually uses they do not.
  Annex A's ratio is ``(1 + r x T) / (1 + r x t_i)``; this module's factor is
  ``1 + r x (T - t_i)``. They differ by the second-order cross term
  ``r^2 x t_i x (T - t_i) / (1 + r x t_i)`` -- this module's coupon value is
  always the slightly larger of the two at a positive rate, so its forward
  clean price is always the slightly lower one. The size is pinned by a
  deterministic test rather than described: at Issue #175's own acceptance
  horizon it is ~1.9e-05 per 100, ~0.0006 of a 32nd.
- On a *non-flat* curve they diverge for a second, independent reason:
  Annex A discounts each coupon at the curve's own factor for that coupon
  date, whereas this module applies one term repo rate to every leg and
  never evaluates a curve at all.

That second difference is deliberate and is the reason this module is not
"Annex A restated". An FPA repo-carry forward *is* a single-repo-rate
structure -- one ``Repo Rate`` for the term, ``Repo Spread = 0 bp`` -- so
reinvesting an interim coupon at that same term rate is the reading internal
to the structure being modelled. Evaluating a curve per coupon date would be
the Annex A discounting construction instead, would require a new S490
transformation, and would break this module's own rule that it takes a
scalar rate and never touches a curve. Neither is chosen here on parity
evidence, because at these horizons parity cannot distinguish them.

**So the reinvestment rate is a labeled prototype assumption**, exposed
rather than argued away: every per-coupon term and factor is on the result
(:class:`RepoCarryInterimCoupon`), so both alternative readings -- Annex A's
DF ratio, or no reinvestment at all (the degenerate zero-rate case) -- are
recoverable from the trace by inspection, without this module implementing,
selecting between, or tuning anything. Nothing here is calibrated to an
observed OVME number.

**Boundaries this module still refuses.** The coupon window is exactly
``(tS, tF]`` -- half-open at ``tS``, because a coupon paid on the spot
settlement date is not received by the forward buyer and the spot dirty
price on that date already carries zero accrued interest. The composed
``coupon_flows_before`` continues to refuse a window reaching
``maturity_date`` (coupon-at-maturity combines with principal redemption,
which that adapter slice does not implement), and a resulting non-positive
forward dirty price raises rather than producing a forward clean price from
it.

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

# How a coupon paid inside the repo term reaches the forward date -- named
# once and carried verbatim onto every result, including Case A results
# (where it is the treatment that would have applied, over an empty set of
# coupons). See the module docstring for the evidence behind it and for what
# part of it remains an Issue #175 prototype assumption.
INTERIM_COUPON_TREATMENT = (
    "INTERIM_COUPON_REINVESTED_AT_REPO_RATE_TO_FORWARD_SETTLEMENT__SIMPLE_ACT360__PROTOTYPE"
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
    """One coupon paid in ``(tS, tF]``, carried to the forward date.

    ``payment_date``/``amount_per_100`` are the composed
    ``coupon_flows_before`` flow's own values, echoed verbatim -- this
    module never recomputes a coupon amount. The remaining fields are this
    coupon's own reinvestment leg under ``INTERIM_COUPON_TREATMENT``:
    ``reinvestment_term_days`` actual calendar days from the payment date to
    the forward settlement date (zero for a coupon paid on ``tF``),
    ``reinvestment_factor`` the ``1 + r x t`` factor over that ACT/360 term,
    and ``forward_value_per_100`` their product -- the amount by which this
    one coupon reduces the termination invoice amount.
    """

    payment_date: str
    amount_per_100: float
    reinvestment_term_days: int
    reinvestment_term_year_fraction: float
    reinvestment_factor: float
    forward_value_per_100: float


def reinvest_interim_coupon_to_forward_settlement(
    *,
    payment_date: str,
    amount_per_100: float,
    forward_settlement_date: str,
    repo_rate_decimal: float,
) -> RepoCarryInterimCoupon:
    """Carry one interim coupon from ``payment_date`` to ``forward_settlement_date``.

    Applies ``INTERIM_COUPON_TREATMENT``: the same
    :func:`carry_factor_from_simple_repo_rate` FPA factor the whole position
    is carried by, over this coupon's own shorter ACT/360 term. A payment
    date equal to the forward settlement date is the zero-term case
    (factor exactly ``1.0``), not an error -- a coupon paid on ``tF`` is
    still received, it simply earns nothing afterwards. A payment date
    *after* ``forward_settlement_date`` raises ``ValueError``: such a coupon
    is not inside the repo term at all and never reaches this function from
    :func:`repo_carry_forward_clean_price`, whose window is ``(tS, tF]``.
    """

    amount = _require_finite(amount_per_100, "amount_per_100")
    payment = _parse_iso_date(payment_date, "payment_date")
    forward = _parse_iso_date(forward_settlement_date, "forward_settlement_date")
    if payment > forward:
        raise ValueError(
            f"payment_date ({payment_date!r}) must not be after forward_settlement_date "
            f"({forward_settlement_date!r}) -- a coupon paid after the forward settlement "
            "date is not an interim coupon of this repo term"
        )

    term_days = (forward - payment).days
    term_year_fraction = term_days / REPO_DAY_COUNT_BASIS_DAYS
    reinvestment_factor = carry_factor_from_simple_repo_rate(
        repo_rate_decimal=repo_rate_decimal,
        repo_term_year_fraction=term_year_fraction,
    )
    return RepoCarryInterimCoupon(
        payment_date=payment_date,
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

    ``interim_coupons`` is every coupon paid in ``(tS, tF]``, in payment-date
    order, each with its own reinvestment leg; it is empty for Case A, where
    ``interim_coupon_forward_value_per_100`` is then exactly ``0.0`` and
    ``forward_dirty_price_per_100`` is unchanged from the single-factor
    carry.
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
    docstring -- including one reinvestment leg per interim coupon, so a
    coupon inside the repo term is carried rather than refused (Issue #175,
    Case B). A horizon with no such coupon is the same code path over an
    empty coupon set and is arithmetically unchanged from Case A.

    Raises :class:`TypeError` for a ``bond`` the accrual adapter does not
    accept, and :class:`ValueError` for a non-finite/non-positive spot clean
    price, a non-positive repo term, or a forward dirty price that is not
    positive after the interim coupons are subtracted. Every other error
    propagates unchanged from the composed helpers -- notably
    ``BLIBondMaturityCashflowUnsupportedError`` when the window reaches the
    bond's maturity date.
    """

    spot_clean = _require_finite(spot_clean_price_per_100, "spot_clean_price_per_100")
    if not spot_clean > 0:
        raise ValueError(f"spot_clean_price_per_100 must be positive, got {spot_clean!r}")

    term_days = repo_term_days(spot_settlement_date, forward_settlement_date)
    term_year_fraction = term_days / REPO_DAY_COUNT_BASIS_DAYS

    interim_coupons = tuple(
        reinvest_interim_coupon_to_forward_settlement(
            payment_date=flow.payment_date,
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
