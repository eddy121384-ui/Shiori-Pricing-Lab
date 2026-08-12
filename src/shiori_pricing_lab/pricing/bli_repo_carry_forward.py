"""Bloomberg FPA repo-carry forward primitive (Issue #173, prototype).

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

**Case A only, fail closed.** Issue #173's first validation is explicitly
the no-interim-coupon case. A coupon paid in ``(tS, tF]`` would make the
single-factor carry above wrong (the coupon's own reinvestment leg is not
modelled here), so this module refuses that case outright via
:class:`RepoCarryInterimCouponUnsupportedError` rather than returning a
silently wrong forward. Case B is a separate, later, separately-approved
slice.

**Composition, not reimplementation.** Accrued interest at both settlement
dates comes from the already-reviewed
``pricing/bli_quantlib_bond_adapter.accrued_interest_per_100``; the
interim-coupon check uses that same module's ``coupon_flows_before``. Date
parsing is ``data/_validation._parse_iso_date``. Every error those helpers
raise (irregular coupon grid, ex-dividend window, maturity cashflow in the
window, QuantLib not installed, ...) propagates unchanged.

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


class RepoCarryInterimCouponUnsupportedError(ValueError):
    """A coupon is paid in ``(spot settlement, forward settlement]``.

    Issue #173's first validation is the no-interim-coupon case (Case A)
    only. Raised instead of returning a forward whose single carry factor
    silently ignores the coupon and its reinvestment.
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
    this module's own named constants verbatim.
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

    Composes ``accrued_interest_per_100`` at both explicit settlement dates
    and applies the four lines of the FPA structure in the module docstring.
    Raises :class:`TypeError` for a ``bond`` the accrual adapter does not
    accept, :class:`ValueError` for a non-finite/non-positive spot clean
    price or a non-positive repo term, and
    :class:`RepoCarryInterimCouponUnsupportedError` when a coupon is paid in
    ``(spot_settlement_date, forward_settlement_date]`` (Case A only -- see
    the module docstring). Every other error propagates unchanged from the
    composed helpers.
    """

    spot_clean = _require_finite(spot_clean_price_per_100, "spot_clean_price_per_100")
    if not spot_clean > 0:
        raise ValueError(f"spot_clean_price_per_100 must be positive, got {spot_clean!r}")

    term_days = repo_term_days(spot_settlement_date, forward_settlement_date)
    term_year_fraction = term_days / REPO_DAY_COUNT_BASIS_DAYS

    interim_coupons = coupon_flows_before(
        bond,
        after_date=spot_settlement_date,
        on_or_before_date=forward_settlement_date,
    )
    if interim_coupons:
        raise RepoCarryInterimCouponUnsupportedError(
            f"{len(interim_coupons)} coupon(s) are paid in ({spot_settlement_date}, "
            f"{forward_settlement_date}] "
            f"(first on {interim_coupons[0].payment_date}) -- Issue #173's repo-carry "
            "forward prototype implements the no-interim-coupon case only; the "
            "coupon and its reinvestment leg are not modelled by this single carry factor"
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
