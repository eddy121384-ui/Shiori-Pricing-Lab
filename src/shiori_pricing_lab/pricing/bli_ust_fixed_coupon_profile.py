"""Narrow UST fixed-coupon bullet profile for the standalone route's eight
non-market Advanced technical fields (Issue #157, parent milestone #143).

Scope: decide, deterministically, what the standalone browser route should
pre-fill for the eight fields that currently block Price until a trader types
them one by one -- ``day_count``, ``bond_type``, ``ex_dividend_days``,
``last_coupon_date``, ``status``, ``reporting_date``,
``forward_settlement_date`` and ``option_settlement_date`` -- **and, just as
importantly, when it must refuse to.**

**This module prices nothing.** It computes no forward, no volatility, no
discount factor, no curve node, no Black-76 value and no Greek, and it changes
no existing pricing, discounting, accrual or repo-carry methodology. It reads
no system clock and makes no Bloomberg call: every input is an explicit
caller-supplied value taken from one already-completed Bloomberg lookup plus
the run's own valuation/expiry dates.

**Field-level resolution (Issue #161).** The first revision of this module
resolved all eight fields or none of them: one failing condition emptied
``fields`` entirely. Real-workstation UAT on ``US91282CMC28`` showed why that
is wrong -- a single display-only description string (``MTY_TYP = NORMAL``
rather than ``AT MATURITY``) wiped out seven fields that were perfectly well
known, and left a trader typing a backend contract by hand for an ordinary
Treasury note.

The gate is now split in two, and the difference is exactly the difference a
trader can act on:

- **Product/schedule admission** (:func:`_product_rejection_reasons` plus the
  coupon-grid check) -- callable, sinkable, non-USD, zero/non-fixed coupon,
  non-semi-annual, already matured, or a coupon grid the reviewed adapter
  cannot carry. These fail closed for the *whole* profile: ``supported`` is
  ``False``, ``fields`` is empty, and **no Advanced edit can repair the
  ticket**, so the browser must not offer one.
- **Per-field resolution** -- every remaining field is resolved on its own.
  A field whose own inputs are missing or contradictory comes back in
  ``unresolved_fields`` (tier :data:`PROVENANCE_BLOCKED`) *while every other
  field keeps its value*. These blockers are genuinely repairable by a trader
  override in Advanced.

**Four-tier provenance (Issue #157), plus an explicit blocked tier.** Every
value carries the tier it came from, and there is no unlabelled silent
default:

1. ``BLOOMBERG_AUTO`` -- a *typed* Bond Master value Bloomberg actually
   returned. Only the three fields that are genuine ``BondReferenceData``
   destination fields in the Bloomberg bond-quote loader's own
   ``_BOND_MASTER_FIELD_MAP`` (``day_count``, ``bond_type``,
   ``last_coupon_date``) are ever read this way, and today none of them is
   mapped, so this tier yields nothing until Eddy confirms a mnemonic for
   one of them. The display-only description strings (``DAY_CNT_DES`` /
   ``MTY_TYP`` / ``CALC_TYP_DES``, carried as ``bond_master_raw``) are
   **never** coerced into a typed value here -- see the product-shape gate
   below for the only, strictly-narrowing use they have. This tier does not
   depend on ``convention_profile`` at all: a confirmed Bloomberg value is
   used regardless of which profile is selected.
2. ``SHIORI_DERIVED`` -- mechanically derived from already-loaded bond terms,
   the valuation date, the expiry date and existing reviewed logic:
   ``last_coupon_date`` (the reviewed coupon-schedule adapter),
   ``reporting_date`` (the run's own valuation date) and the two settlement
   dates (the QuantLib U.S. government-bond calendar). These derivations are
   mechanical, not a profile-owned constant -- but they only run once the
   product-shape gate below has passed for the selected profile.
3. ``UST_PROFILE_DEFAULT`` -- the narrow profile constants this issue
   approves: ACT/ACT day count, fixed-coupon bullet bond type, zero
   ex-dividend days, and the ACTIVE status. **This is the one tier that is
   actually owned by, and named after, the selected convention profile** --
   see "Convention Profile" below.
4. ``TRADER_OVERRIDE`` -- owned by the caller, not by this module. Nothing
   here ever overwrites a value the trader has taken over; the browser keeps
   the override set and simply does not re-apply an overridden path.
5. ``BLOCKED`` -- this one field has no safe data or method on this run, and
   is reported in ``unresolved_fields`` with its own reason rather than
   guessed. It never removes a value from any other field.

**Convention Profile: a browser-state input, never a server-computed
identity claim (Issue #157 P1-1 correction, second revision).** This
module's first revision tried to gate admission on *proving* the bond is
issued by the U.S. Treasury (first via the ISIN's ``US`` country prefix,
then via the CUSIP's ``912`` issuer-number block). Both were withdrawn on
Eddy's explicit product-direction correction: Shiori's long-term direction
is not Treasury-only, and this module must never claim to have verified an
issuer's identity -- there is no ``identity_verified`` field, no issuer
classification, and no such claim anywhere in this module's output.

Instead, ``convention_profile`` is a **required, explicit parameter** of
:func:`resolve_ust_advanced_field_profile` -- it must be supplied by the
caller (ultimately, browser state) on every call, and it names *which
convention profile is currently selected*, not what Shiori has deduced
about the bond. This PR's scope supports exactly one value,
``"UST"`` (:data:`CONVENTION_PROFILE_UST`), reserved in
:data:`_SUPPORTED_CONVENTION_PROFILES` as the interface point a future
``US_CORPORATE`` / ``GILT`` / ``CUSTOM`` selector will extend without
changing this contract shape. A missing, blank, or unrecognized
``convention_profile`` raises ``ValueError`` immediately -- Shiori never
silently falls back to ``"UST"`` for an unspecified or unknown selection.
The resolved profile's own :attr:`BLIUstAdvancedFieldProfile.convention_profile`
echoes the validated selection back, so the browser can render
``Convention Profile: UST`` from the response it just received rather than
from a value it independently assumes.

Only the ``UST_PROFILE_DEFAULT`` tier is owned by the selected profile: its
four constants (day count, bond type, ex-dividend days, status) are this
profile's own conventions, and a different selected profile would supply
different constants for them. ``BLOOMBERG_AUTO`` and ``SHIORI_DERIVED``
values are not profile ownership claims -- they are, respectively, a
confirmed external fact and a mechanical derivation from the bond's own
already-loaded terms -- but both still only run once the product-shape gate
below has passed for whichever profile was selected, since resolving them
against a bond the profile does not fit at all would mean inventing values
for something outside scope.

**Product-shape gate: fail-closed, and never an identity claim.** The
profile is applied only to a bond that passes *every* condition in
:func:`_product_rejection_reasons`: non-USD, callable, sinkable,
zero/non-numeric coupon, not semi-annual, or already matured. Anything that
fails gets no profile at all and is reported with its reasons, so an
unsupported instrument stops clearly instead of being silently given UST
conventions. None of these conditions is, or was ever intended as, proof of
who issued the bond -- they test only whether this bond's own terms fit the
shape UST conventions assume (a fixed, positive, semi-annual coupon on a
bond that has not yet matured). A USD, non-callable, non-sinkable,
semi-annual fixed-coupon bond that is not actually a Treasury still passes
this gate when ``"UST"`` is the selected profile -- that is the explicit,
accepted design (Issue #157 review discussion), not an oversight: the
profile is applied because it is selected, not because Shiori believes it
has identified the issuer.

**The three display-only description strings are evidence, never admission
(Issue #161 correction).** The first revision made ``DAY_CNT_DES =
ACT/ACT``, ``MTY_TYP = AT MATURITY`` and ``CALC_TYP_DES = STREET
CONVENTION`` *necessary* conditions for the whole profile. Bloomberg
workstation UAT on ``US91282CMC28`` -- an ordinary 4.5% 12/31/31 Treasury
note -- returned ``MTY_TYP = NORMAL``, so that string equality was rejecting
real USTs. ``MTY_TYP`` and ``CALC_TYP_DES`` are therefore no longer read
here at all: nothing in this module has established what their values mean
semantically, and an unproven string must not decide a product's fate in
either direction.

``DAY_CNT_DES`` keeps exactly one, strictly-narrowing, **field-level** role:
when it is present and reads something other than :data:`UST_PROFILE_DAY_COUNT_EVIDENCE`
(``"ACT/ACT"``), Bloomberg's own description contradicts the day count this
profile would otherwise supply, so ``day_count`` alone comes back ``BLOCKED``
for the trader to set. The seven other fields are unaffected. This is still
the opposite of the mapping #145 forbids: a matching string never *produces*
a typed value (the value comes from the approved profile constant), a
missing string blocks nothing, and a contradicting string can only withhold
the one field it is evidence about.

**Irregular schedules fail closed.** The current typed pricing adapter accepts
only one internally consistent regular coupon grid. An irregular or stubbed
grid therefore rejects the whole profile: editing ``last_coupon_date`` cannot
repair the underlying issue/maturity/first-coupon grid that the adapter also
validates. That is a genuine product/schedule refusal, not a field-level
blocker, and the browser must not offer an Advanced route out of it.

**Missing schedule dates block one field, not the ticket.** When an
individual Bloomberg schedule date is absent or malformed there is no grid
to call irregular -- there is simply nothing to derive ``last_coupon_date``
from. That is a field-level ``BLOCKED``, and the trader can supply the date
in Advanced. The same applies to ``status`` and ``reporting_date`` when the
maturity or valuation date they depend on is unusable: those fields block,
and the rest of the profile is still filled.

**Day count correction (post-review, superseding the PR's first revision).**
The first revision of this module used ``DayCount.ACT_ACT_ISDA`` for
"ACT/ACT", reasoning that it was the enum's only ACT/ACT member. That was
wrong and has been withdrawn: QuantLib's own ``ActualActual::ISDA`` and
``ActualActual::Bond`` (the ISMA/bond-basis convention) are genuinely
different day-count rules, not a naming difference -- ISDA prorates against
the calendar year(s) a period spans, while Bond/ISMA (the actual US
Treasury coupon-accrual convention) prorates strictly within the
bracketing coupon period, so the two produce different accrued-interest
results over the same period. Using ISDA here would have silently
mispriced accrued interest, and therefore dirty price and everything
downstream of it, for every UST this profile applies to.

The correction: a new, genuinely distinct ``DayCount.ACT_ACT_BOND`` member
(``products/enums.py``) is what this profile now uses, mapped by the
reviewed adapter to ``ql.ActualActual(ql.ActualActual.Bond)`` with the
bracketing coupon period passed as QuantLib's explicit reference period
(see ``bli_quantlib_bond_adapter.py``'s ``_day_counter`` and
``accrued_interest_per_100``). ``ACT_ACT_ISDA``'s own mapping and every
existing caller of it are completely unchanged -- this adds a member, it
does not alias, rename, or repurpose the existing one.

**Names that differ from Issue #157's prose (reported deliberately).**

- *Last coupon date.* The issue describes deriving "the previous coupon date
  relative to the valuation date". The field that actually exists on
  ``BLIStandaloneBondReferenceData`` is ``last_coupon_date``, and the reviewed
  adapter requires it to equal the **final scheduled coupon date before
  maturity** (the coupon grid's second-to-last date) -- a run whose
  ``last_coupon_date`` is a mid-life "previous coupon" is rejected outright by
  ``_check_regular_schedule``. The existing, reviewed meaning is used, via
  ``derive_last_coupon_date`` in the coupon-schedule adapter itself, so no
  second schedule generator exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import (
    BLIBondScheduleError,
    BLIQuantLibNotAvailableError,
    derive_last_coupon_date,
)
from shiori_pricing_lab.products.enums import Currency, DayCount, Frequency
from shiori_pricing_lab.reference_data._validation import _parse_iso_date
from shiori_pricing_lab.reference_data.enums import BondStatus, BondType

try:
    import QuantLib as ql
except ImportError:  # QuantLib is optional -- pyproject.toml [project.optional-dependencies].quant
    ql = None


# --- Provenance tiers (Issue #157) --------------------------------------------

PROVENANCE_BLOOMBERG_AUTO = "BLOOMBERG_AUTO"
PROVENANCE_SHIORI_DERIVED = "SHIORI_DERIVED"
PROVENANCE_UST_PROFILE_DEFAULT = "UST_PROFILE_DEFAULT"
PROVENANCE_TRADER_OVERRIDE = "TRADER_OVERRIDE"
# Not a value tier: the explicit label for a field this run refused to fill.
PROVENANCE_BLOCKED = "BLOCKED"

PROVENANCE_TIERS = (
    PROVENANCE_BLOOMBERG_AUTO,
    PROVENANCE_SHIORI_DERIVED,
    PROVENANCE_UST_PROFILE_DEFAULT,
    PROVENANCE_TRADER_OVERRIDE,
)

# --- Convention profile selection (Issue #157 P1-1 correction) ---------------
#
# The browser-state input naming which convention profile is selected -- see
# the module docstring's "Convention Profile" section. This PR's scope
# supports exactly one value; the tuple is the reserved extension point for a
# future profile selector, not a switch statement built ahead of need.
CONVENTION_PROFILE_UST = "UST"
_SUPPORTED_CONVENTION_PROFILES = (CONVENTION_PROFILE_UST,)

# --- The eight field paths, spelled exactly as they sit on a standalone case --

PATH_DAY_COUNT = "bond_reference_data_universe.0.day_count"
PATH_BOND_TYPE = "bond_reference_data_universe.0.bond_type"
PATH_EX_DIVIDEND_DAYS = "bond_reference_data_universe.0.ex_dividend_days"
PATH_LAST_COUPON_DATE = "bond_reference_data_universe.0.last_coupon_date"
PATH_STATUS = "bond_reference_data_universe.0.status"
PATH_REPORTING_DATE = "reporting_date"
PATH_FORWARD_SETTLEMENT_DATE = "forward_settlement_date"
PATH_OPTION_SETTLEMENT_DATE = "option_settlement_date"

UST_ADVANCED_FIELD_PATHS = (
    PATH_DAY_COUNT,
    PATH_BOND_TYPE,
    PATH_EX_DIVIDEND_DAYS,
    PATH_LAST_COUPON_DATE,
    PATH_STATUS,
    PATH_REPORTING_DATE,
    PATH_FORWARD_SETTLEMENT_DATE,
    PATH_OPTION_SETTLEMENT_DATE,
)

# The two paths that need an expiry date, so they are recomputed whenever
# expiry changes (for every path the trader has not taken over).
EXPIRY_DEPENDENT_FIELD_PATHS = (PATH_FORWARD_SETTLEMENT_DATE, PATH_OPTION_SETTLEMENT_DATE)

# --- The approved narrow profile ----------------------------------------------
#
# Existing typed members only -- this module adds no enum value anywhere.
# Owned by the "UST" convention profile specifically (see the module
# docstring): a future profile would supply its own constants here, not
# these ones.
UST_PROFILE_DAY_COUNT = DayCount.ACT_ACT_BOND
UST_PROFILE_BOND_TYPE = BondType.FIXED_COUPON_BULLET
UST_PROFILE_EX_DIVIDEND_DAYS = 0
UST_PROFILE_STATUS = BondStatus.ACTIVE
UST_PROFILE_CURRENCY = Currency.USD
UST_PROFILE_COUPON_FREQUENCY = Frequency.SEMI_ANNUAL
UST_PROFILE_SETTLEMENT_BUSINESS_DAYS = 1

# The one display-only Bloomberg description string still read here, and only
# to *withhold* the single field it is evidence about (Issue #161). A present
# value reading anything else contradicts UST_PROFILE_DAY_COUNT, so day_count
# alone comes back BLOCKED; a missing value blocks nothing; a matching value
# is never mapped into a typed enum (see the module docstring). MTY_TYP and
# CALC_TYP_DES are not read at all -- real USTs return MTY_TYP = NORMAL.
UST_PROFILE_DAY_COUNT_EVIDENCE = "ACT/ACT"

# Bond Master keys that are genuine BondReferenceData destination fields, so a
# non-null value there is a typed Bloomberg value rather than a description
# string. ex_dividend_days and status are deliberately absent: they are not
# destination fields at all, so no Bloomberg tier exists for them.
_BLOOMBERG_TYPED_FIELD_BY_PATH = {
    PATH_DAY_COUNT: "day_count",
    PATH_BOND_TYPE: "bond_type",
    PATH_LAST_COUPON_DATE: "last_coupon_date",
}


@dataclass(frozen=True)
class BLIUstProfileField:
    """One resolved field: where it goes, what it is, and which tier it came from."""

    path: str
    value: object
    provenance: str


@dataclass(frozen=True)
class BLIUstUnresolvedField:
    """One in-scope Advanced field this run could not resolve, and why.

    Distinct from ``pending_field_paths`` (waiting on an input that simply
    has not been supplied yet, e.g. expiry): this is a field whose own
    inputs are missing or contradictory, so a real per-field check refused
    to guess it (Issue #161). Every other field is still resolved, and the
    trader *can* repair this one with an Advanced override -- which is
    exactly what distinguishes it from a ``rejection_reasons`` entry.
    """

    path: str
    reason: str


@dataclass(frozen=True)
class BLIUstAdvancedFieldProfile:
    """The full result of one profile resolution.

    ``convention_profile`` echoes the validated, caller-selected profile
    this result was resolved against (see the module docstring) -- never a
    value this module invented or deduced.

    ``supported`` is the product/schedule admission decision. When it is
    ``False``, ``fields`` is empty and ``rejection_reasons`` says exactly
    why: this bond cannot be completed on the current pricing path at all,
    and **no Advanced override repairs it** (Issue #161 -- the browser must
    not offer a "Go to this input" exit for these). It is a claim about
    whether this bond's *shape and schedule* fit the selected profile and
    the reviewed adapter, never a claim about who issued it.

    When ``supported`` is ``True``, ``fields`` may still be partial:

    - ``pending_field_paths`` names fields that are in scope but cannot be
      resolved yet because the run has no expiry date, so they are absent
      rather than guessed;
    - ``unresolved_fields`` names fields whose own inputs are missing or
      contradictory (tier :data:`PROVENANCE_BLOCKED`). One blocked field
      never withdraws another field's resolved value, and a trader override
      in Advanced is a genuine route past it.
    """

    supported: bool
    convention_profile: str
    rejection_reasons: tuple[str, ...]
    fields: tuple[BLIUstProfileField, ...]
    pending_field_paths: tuple[str, ...]
    unresolved_fields: tuple[BLIUstUnresolvedField, ...]


def _require_quantlib() -> None:
    if ql is None:
        raise BLIQuantLibNotAvailableError(
            "QuantLib is not installed -- install the optional 'quant' dependency group "
            '(pip install "shiori-pricing-lab[quant]") to use this function'
        )


def ust_government_bond_calendar() -> ql.Calendar:
    """Return the existing QuantLib U.S. government-bond calendar.

    Reused verbatim: this module writes no holiday table of its own, partial
    or otherwise, and defines no calendar framework. ``ql.UnitedStates`` with
    the ``GovernmentBond`` market is QuantLib's own reviewed U.S. government
    securities calendar.
    """

    _require_quantlib()
    return ql.UnitedStates(ql.UnitedStates.GovernmentBond)


def advance_ust_government_bond_business_days(value: date, business_days: int) -> date:
    """Return ``value`` advanced by ``business_days`` U.S. government-bond business days.

    ``business_days`` must be positive. QuantLib's own ``Calendar.advance``
    performs the roll, so a start date that is itself a weekend or holiday is
    handled by QuantLib's rules rather than by any arithmetic here.
    """

    if isinstance(business_days, bool) or not isinstance(business_days, int):
        raise ValueError(f"business_days must be a positive integer, got {business_days!r}")
    if business_days <= 0:
        raise ValueError(f"business_days must be a positive integer, got {business_days}")
    calendar = ust_government_bond_calendar()
    advanced = calendar.advance(ql.Date(value.day, value.month, value.year), business_days, ql.Days)
    return date(advanced.year(), advanced.month(), advanced.dayOfMonth())


def _optional_iso_date(value: object, field_name: str) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _parse_iso_date(value, field_name)
    except ValueError:
        return None


def _require_supported_convention_profile(convention_profile: object) -> str:
    """Validate the caller-selected convention profile, or raise ``ValueError``.

    ``convention_profile`` is required browser-state input, never a value
    this module defaults or infers (see the module docstring's "Convention
    Profile" section). A missing, blank, or unrecognized selection is a
    caller-contract violation -- reported the same way the server route
    already reports a malformed request body -- and is never silently
    treated as ``"UST"``.
    """

    if not isinstance(convention_profile, str) or not convention_profile.strip():
        raise ValueError(
            "convention_profile is required and must name the selected convention "
            f"profile (one of {_SUPPORTED_CONVENTION_PROFILES!r}); got "
            f"{convention_profile!r} -- Shiori never silently falls back to a default "
            "profile for a missing or blank selection"
        )
    if convention_profile not in _SUPPORTED_CONVENTION_PROFILES:
        raise ValueError(
            f"convention_profile {convention_profile!r} is not one of the profiles this "
            f"route supports ({_SUPPORTED_CONVENTION_PROFILES!r}); Shiori never silently "
            "falls back to a default profile for an unrecognized selection"
        )
    return convention_profile


def _product_rejection_reasons(
    *,
    currency: object,
    bond_master: dict,
    valuation_date: object,
) -> tuple[str, ...]:
    """Return every reason this bond's *product shape* is outside the profile.

    An empty tuple means admitted. Every reason is collected rather than
    short-circuited, so a trader sees the whole picture at once instead of
    fixing one blocker only to meet the next. This checks the bond's own
    terms only -- currency, coupon shape, callable/sinkable flags, maturity
    -- and makes no claim about who issued it (see the module docstring's
    "Product-shape gate" section; the withdrawn ISIN/CUSIP issuer-identity
    checks are not here, and neither are the withdrawn description-string
    equalities, Issue #161).

    Every reason here is one **no Advanced override can repair**. Anything a
    trader could genuinely fix by typing a value is a field-level
    ``BLOCKED`` instead, resolved per field below.

    Coupon-grid *regularity* is checked separately, immediately after this
    function's reasons come back empty, by :func:`resolve_ust_advanced_field_profile`
    itself: an irregular, stubbed, or Bloomberg-confirmed-mismatched grid
    rejects the whole profile there (see the module docstring's "Irregular
    schedules fail closed" section), not just ``last_coupon_date``. A
    schedule date that is simply *absent* is not a rejection at all -- there
    is no grid to call irregular, so only ``last_coupon_date`` blocks.
    """

    reasons: list[str] = []

    if currency != UST_PROFILE_CURRENCY.value:
        reasons.append(
            f"currency {currency!r} is not {UST_PROFILE_CURRENCY.value} -- the UST "
            "fixed-coupon bullet profile covers USD-denominated bonds only"
        )

    if bond_master.get("callable_flag") is not False:
        reasons.append(
            f"callable_flag is {bond_master.get('callable_flag')!r}; the profile covers "
            "explicitly non-callable bonds only"
        )
    if bond_master.get("sinkable_flag") is not False:
        reasons.append(
            f"sinkable_flag is {bond_master.get('sinkable_flag')!r}; the profile covers "
            "explicitly non-sinkable bonds only"
        )

    coupon = bond_master.get("coupon")
    if isinstance(coupon, bool) or not isinstance(coupon, (int, float)):
        reasons.append(f"coupon is {coupon!r}; a fixed coupon is required")
    elif coupon <= 0:
        reasons.append(
            f"coupon is {coupon!r}; zero-coupon bonds are outside the fixed-coupon profile"
        )

    coupon_frequency = bond_master.get("coupon_frequency")
    if coupon_frequency != UST_PROFILE_COUPON_FREQUENCY.value:
        reasons.append(
            f"coupon_frequency is {coupon_frequency!r}, not "
            f"{UST_PROFILE_COUPON_FREQUENCY.value}; the profile covers semi-annual "
            "fixed-coupon bullets only"
        )

    # A missing or malformed valuation/maturity date is *not* rejected here:
    # it blocks only the individual fields that depend on it (``status`` and
    # ``reporting_date``, resolved per field in
    # :func:`resolve_ust_advanced_field_profile`), leaving the rest resolved.
    # Only a bond Shiori can positively see has matured fails closed.
    valuation = _optional_iso_date(valuation_date, "valuation_date")
    maturity = _optional_iso_date(bond_master.get("maturity_date"), "maturity_date")
    if maturity is not None and valuation is not None and maturity <= valuation:
        reasons.append(
            f"maturity_date ({maturity.isoformat()}) is not after valuation_date "
            f"({valuation.isoformat()}); a matured bond is never pre-filled as ACTIVE"
        )

    return tuple(reasons)


def resolve_ust_advanced_field_profile(
    *,
    convention_profile: object,
    isin: object,
    currency: object,
    bond_master: dict | None,
    bond_master_raw: dict | None,
    valuation_date: object,
    expiry_date: object = None,
) -> BLIUstAdvancedFieldProfile:
    """Resolve the eight Advanced technical fields for one Bloomberg-loaded bond.

    ``convention_profile`` is required, caller-selected browser state naming
    which convention profile to apply -- see the module docstring's
    "Convention Profile" section. Raises ``ValueError`` for a missing,
    blank, or unrecognized selection; this PR's scope accepts only
    ``"UST"``. ``isin`` is carried through for identification only and is
    not used to gate anything (the withdrawn ISIN-country-prefix check is
    not here).

    Returns an unsupported profile with explicit reasons for anything whose
    *product shape* does not fit the selected profile (see
    :func:`_product_rejection_reasons`) -- no partial profile is ever
    returned at that stage, and no field is filled for a bond the profile's
    shape does not cover. An irregular coupon grid rejects the whole profile
    the same way, because the typed pricing adapter fails closed on the
    underlying schedule. Neither is repairable by a trader override.

    Everything else resolves **per field** (Issue #161): a field whose own
    inputs are missing or contradictory comes back in ``unresolved_fields``
    and every other field keeps its value.

    ``expiry_date`` is optional because the trader may not have entered the
    expiry yet. Without it, the two settlement dates are reported in
    ``pending_field_paths`` instead of being guessed; the other fields are
    still resolved, so the ordinary workflow is unblocked immediately after
    Bloomberg Load.

    Raises ``BLIQuantLibNotAvailableError`` when QuantLib is not installed --
    the coupon schedule and the U.S. government-bond calendar both come from
    it, and neither is approximated locally.
    """

    _require_quantlib()
    convention_profile = _require_supported_convention_profile(convention_profile)
    bond_master = dict(bond_master or {})
    bond_master_raw = dict(bond_master_raw or {})

    reasons = _product_rejection_reasons(
        currency=currency,
        bond_master=bond_master,
        valuation_date=valuation_date,
    )
    if reasons:
        return _unsupported(convention_profile, reasons)

    valuation = _optional_iso_date(valuation_date, "valuation_date")
    maturity = _optional_iso_date(bond_master.get("maturity_date"), "maturity_date")
    missing_schedule_inputs = tuple(
        name
        for name in ("issue_date", "maturity_date", "first_coupon_date")
        if _optional_iso_date(bond_master.get(name), name) is None
    )

    # The coupon grid is only *judged* when all three dates that define it are
    # present. A grid that exists and is irregular is a product/schedule
    # refusal (no Advanced edit repairs it). Dates that are simply absent
    # leave nothing to judge, so only last_coupon_date blocks, below.
    derived_last_coupon = None
    if not missing_schedule_inputs:
        try:
            derived_last_coupon = derive_last_coupon_date(
                issue_date=bond_master["issue_date"],
                maturity_date=bond_master["maturity_date"],
                first_coupon_date=bond_master["first_coupon_date"],
                coupon_frequency=UST_PROFILE_COUPON_FREQUENCY,
            )
        except BLIBondScheduleError:
            derived_last_coupon = None
        confirmed_last_coupon = bond_master.get("last_coupon_date")
        if derived_last_coupon is None or (
            confirmed_last_coupon is not None and confirmed_last_coupon != derived_last_coupon
        ):
            return _unsupported(
                convention_profile,
                (
                    "current pricing adapter supports regular coupon schedules only; "
                    "editing last_coupon_date cannot repair the underlying schedule",
                ),
            )

    fields: list[BLIUstProfileField] = []
    blocked: list[BLIUstUnresolvedField] = []

    def _confirmed_typed_value(path: str) -> object | None:
        master_field = _BLOOMBERG_TYPED_FIELD_BY_PATH.get(path)
        return None if master_field is None else bond_master.get(master_field)

    def _resolve_field(path: str, value: object, provenance: str) -> None:
        """Fill one field from Bloomberg if it confirmed a typed value, else as given."""

        confirmed = _confirmed_typed_value(path)
        if confirmed is not None:
            fields.append(
                BLIUstProfileField(path=path, value=confirmed, provenance=PROVENANCE_BLOOMBERG_AUTO)
            )
            return
        fields.append(BLIUstProfileField(path=path, value=value, provenance=provenance))

    def _block(path: str, reason: str) -> None:
        """Withhold exactly one field, leaving every other field untouched."""

        blocked.append(BLIUstUnresolvedField(path=path, reason=reason))

    # Day count. Bloomberg's DAY_CNT_DES is the one description string still
    # read, and only to withhold this single field when it contradicts the
    # profile (Issue #161) -- never to produce a typed value.
    day_count_evidence = bond_master_raw.get("day_count")
    day_count_evidence_contradicts = (
        _confirmed_typed_value(PATH_DAY_COUNT) is None
        and isinstance(day_count_evidence, str)
        and bool(day_count_evidence.strip())
        and day_count_evidence != UST_PROFILE_DAY_COUNT_EVIDENCE
    )
    if day_count_evidence_contradicts:
        _block(
            PATH_DAY_COUNT,
            f"Bloomberg day-count evidence reads {day_count_evidence!r}, not "
            f"{UST_PROFILE_DAY_COUNT_EVIDENCE!r}, so it contradicts this profile's day "
            "count; Shiori will not map a description string into a typed day count, and "
            "will not apply the profile default over Bloomberg's own description",
        )
    else:
        _resolve_field(PATH_DAY_COUNT, UST_PROFILE_DAY_COUNT.value, PROVENANCE_UST_PROFILE_DEFAULT)

    _resolve_field(PATH_BOND_TYPE, UST_PROFILE_BOND_TYPE.value, PROVENANCE_UST_PROFILE_DEFAULT)
    _resolve_field(
        PATH_EX_DIVIDEND_DAYS, UST_PROFILE_EX_DIVIDEND_DAYS, PROVENANCE_UST_PROFILE_DEFAULT
    )

    # Last coupon date. A confirmed typed Bloomberg value wins; otherwise the
    # reviewed coupon-schedule adapter derives it -- which needs all three
    # schedule dates. Without them there is one blocked field, not an empty
    # profile.
    if (
        _confirmed_typed_value(PATH_LAST_COUPON_DATE) is not None
        or derived_last_coupon is not None
    ):
        _resolve_field(PATH_LAST_COUPON_DATE, derived_last_coupon, PROVENANCE_SHIORI_DERIVED)
    else:
        _block(
            PATH_LAST_COUPON_DATE,
            "Bloomberg did not return a usable "
            f"{', '.join(missing_schedule_inputs)} for this bond, so the reviewed coupon "
            "schedule has nothing to derive the last coupon date from",
        )

    # Status. ACTIVE is asserted only where the product gate could actually
    # prove the bond has not matured -- which needs both dates.
    if valuation is None or maturity is None:
        _block(
            PATH_STATUS,
            "Shiori cannot compare this bond's maturity date "
            f"({bond_master.get('maturity_date')!r}) with the run's valuation date "
            f"({valuation_date!r}), so it will not assert that the bond is still active",
        )
    else:
        _resolve_field(PATH_STATUS, UST_PROFILE_STATUS.value, PROVENANCE_UST_PROFILE_DEFAULT)

    # The reporting date is the run's own valuation date, carried across
    # mechanically -- not a market convention and not a profile constant.
    if valuation is None:
        _block(
            PATH_REPORTING_DATE,
            f"this run's valuation_date is {valuation_date!r}, not an ISO date "
            "(YYYY-MM-DD), so there is nothing to carry across",
        )
    else:
        _resolve_field(PATH_REPORTING_DATE, valuation.isoformat(), PROVENANCE_SHIORI_DERIVED)

    expiry = _optional_iso_date(expiry_date, "expiry_date")
    pending: list[str] = []
    if expiry is None:
        pending.extend(EXPIRY_DEPENDENT_FIELD_PATHS)
    else:
        settlement = advance_ust_government_bond_business_days(
            expiry, UST_PROFILE_SETTLEMENT_BUSINESS_DAYS
        ).isoformat()
        _resolve_field(PATH_FORWARD_SETTLEMENT_DATE, settlement, PROVENANCE_SHIORI_DERIVED)
        _resolve_field(PATH_OPTION_SETTLEMENT_DATE, settlement, PROVENANCE_SHIORI_DERIVED)

    return BLIUstAdvancedFieldProfile(
        supported=True,
        convention_profile=convention_profile,
        rejection_reasons=(),
        fields=tuple(fields),
        pending_field_paths=tuple(pending),
        unresolved_fields=tuple(blocked),
    )


def _unsupported(
    convention_profile: str, reasons: tuple[str, ...]
) -> BLIUstAdvancedFieldProfile:
    """Return the fail-closed result: no fields, and no Advanced route out."""

    return BLIUstAdvancedFieldProfile(
        supported=False,
        convention_profile=convention_profile,
        rejection_reasons=reasons,
        fields=(),
        pending_field_paths=(),
        unresolved_fields=(),
    )
