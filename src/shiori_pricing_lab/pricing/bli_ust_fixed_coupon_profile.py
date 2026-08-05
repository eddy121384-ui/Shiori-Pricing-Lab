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

**Four-tier provenance (Issue #157).** Every value carries the tier it came
from, and there is no unlabelled silent default:

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
:func:`_ust_fixed_coupon_bullet_rejection_reasons`: non-USD, callable,
sinkable, zero/non-numeric coupon, not semi-annual, already matured,
missing or malformed schedule dates, or Bloomberg evidence that does not
match the profile's shape exactly. Anything that fails gets no profile at
all and is reported with its reasons, so an unsupported instrument stops
clearly instead of being silently given UST conventions. None of these
conditions is, or was ever intended as, proof of who issued the bond --
they test only whether this bond's own terms fit the shape UST conventions
assume (a fixed, positive, semi-annual coupon on a bond that has not yet
matured). A USD, non-callable, non-sinkable, semi-annual fixed-coupon bond
that is not actually a Treasury still passes this gate when ``"UST"`` is
the selected profile -- that is the explicit, accepted design (Issue #157
review discussion), not an oversight: the profile is applied because it is
selected, not because Shiori believes it has identified the issuer.

The three confirmed display-only description strings are used **only** as an
additional *necessary* condition: a bond whose ``DAY_CNT_DES`` is not exactly
``"ACT/ACT"``, whose ``MTY_TYP`` is not exactly ``"AT MATURITY"``, or whose
``CALC_TYP_DES`` is not exactly ``"STREET CONVENTION"`` is refused the
profile. That is the opposite of the mapping #145 forbids: a matching string
never *produces* a typed value (the value comes from the approved profile
constant), and a non-matching or missing string can only block. This is
deliberately conservative -- a Bloomberg miss on one of those three
description fields leaves the trader exactly where they are today, filling
Advanced by hand.

**Irregular schedules fail closed.** The current typed pricing adapter accepts
only one internally consistent regular coupon grid. An irregular or stubbed
grid therefore rejects the whole profile: editing ``last_coupon_date`` cannot
repair the underlying issue/maturity/first-coupon grid that the adapter also
validates.

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

# The exact display-only Bloomberg description strings a bond must carry to be
# admitted. These are a necessary condition only; none of them is ever mapped
# into a typed value (see the module docstring).
UST_PROFILE_REQUIRED_BLOOMBERG_EVIDENCE = {
    "day_count": "ACT/ACT",
    "maturity_type": "AT MATURITY",
    "calc_type": "STREET CONVENTION",
}

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
    has not been supplied yet, e.g. expiry): this is for a field whose
    inputs are all present but which a real per-field check refused to
    guess. No current condition uses this future-facing interface.
    """

    path: str
    reason: str


@dataclass(frozen=True)
class BLIUstAdvancedFieldProfile:
    """The full result of one profile resolution.

    ``convention_profile`` echoes the validated, caller-selected profile
    this result was resolved against (see the module docstring) -- never a
    value this module invented or deduced.

    ``supported`` is the product-shape admission decision. When it is
    ``False``, ``fields`` is empty and ``rejection_reasons`` says exactly
    why -- never a partial profile. It is a claim about whether this bond's
    *shape* fits the selected profile's conventions, never a claim about who
    issued it.

    ``pending_field_paths`` names fields that are in scope but cannot be
    resolved yet because the run has no expiry date, so they are absent
    rather than guessed. ``unresolved_fields`` remains available for a future
    genuinely field-specific refusal; no current condition uses it.
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


def _ust_fixed_coupon_bullet_rejection_reasons(
    *,
    currency: object,
    bond_master: dict,
    bond_master_raw: dict,
    valuation_date: object,
) -> tuple[str, ...]:
    """Return every reason this bond's *shape* does not fit the UST profile.

    An empty tuple means admitted. Every reason is collected rather than
    short-circuited, so a trader sees the whole picture at once instead of
    fixing one blocker only to meet the next. This checks the bond's own
    terms only -- currency, coupon shape, callable/sinkable flags, maturity,
    and confirmed evidence strings -- and makes no claim about who issued
    it (see the module docstring's "Product-shape gate" section; the
    withdrawn ISIN/CUSIP issuer-identity checks are not here).

    Coupon-grid *regularity* is checked separately, immediately after this
    function's reasons come back empty, by :func:`resolve_ust_advanced_field_profile`
    itself: an irregular, stubbed, or Bloomberg-confirmed-mismatched grid
    rejects the whole profile there (see the module docstring's "Irregular
    schedules fail closed" section), not just ``last_coupon_date``.
    """

    reasons: list[str] = []

    if currency != UST_PROFILE_CURRENCY.value:
        reasons.append(
            f"currency {currency!r} is not {UST_PROFILE_CURRENCY.value} -- the UST "
            "fixed-coupon bullet profile covers USD-denominated bonds only"
        )

    for evidence_key, expected in UST_PROFILE_REQUIRED_BLOOMBERG_EVIDENCE.items():
        actual = bond_master_raw.get(evidence_key)
        if actual != expected:
            reasons.append(
                f"Bloomberg {evidence_key} evidence is {actual!r}, not {expected!r}; the "
                "profile is applied only to a bond whose confirmed description strings "
                "match it exactly (this string is never mapped into a typed value)"
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

    valuation = _optional_iso_date(valuation_date, "valuation_date")
    if valuation is None:
        reasons.append(f"valuation_date must be an ISO date (YYYY-MM-DD), got {valuation_date!r}")

    issue = _optional_iso_date(bond_master.get("issue_date"), "issue_date")
    maturity = _optional_iso_date(bond_master.get("maturity_date"), "maturity_date")
    first_coupon = _optional_iso_date(bond_master.get("first_coupon_date"), "first_coupon_date")
    for name, value in (
        ("issue_date", issue),
        ("maturity_date", maturity),
        ("first_coupon_date", first_coupon),
    ):
        if value is None:
            reasons.append(
                f"Bloomberg {name} is {bond_master.get(name)!r}; the profile derives nothing "
                "from a missing or malformed schedule date"
            )

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
    *shape* does not fit the selected profile (see
    :func:`_ust_fixed_coupon_bullet_rejection_reasons`) -- no partial
    profile is ever returned at that stage, and no field is filled for a
    bond the profile's shape does not cover. An irregular coupon grid rejects
    the whole profile because the typed pricing adapter fails closed on the
    underlying schedule.

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

    reasons = _ust_fixed_coupon_bullet_rejection_reasons(
        currency=currency,
        bond_master=bond_master,
        bond_master_raw=bond_master_raw,
        valuation_date=valuation_date,
    )
    if reasons:
        return BLIUstAdvancedFieldProfile(
            supported=False,
            convention_profile=convention_profile,
            rejection_reasons=reasons,
            fields=(),
            pending_field_paths=(),
            unresolved_fields=(),
        )

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
        return BLIUstAdvancedFieldProfile(
            supported=False,
            convention_profile=convention_profile,
            rejection_reasons=(
                "current pricing adapter supports regular coupon schedules only; "
                "editing last_coupon_date cannot repair the underlying schedule",
            ),
            fields=(),
            pending_field_paths=(),
            unresolved_fields=(),
        )

    fields: list[BLIUstProfileField] = []

    def _confirmed_typed_value(path: str) -> object | None:
        return bond_master.get(_BLOOMBERG_TYPED_FIELD_BY_PATH[path])

    def _add(path: str, value: object, provenance: str) -> None:
        confirmed = _confirmed_typed_value(path) if path in _BLOOMBERG_TYPED_FIELD_BY_PATH else None
        if confirmed is not None:
            fields.append(
                BLIUstProfileField(path=path, value=confirmed, provenance=PROVENANCE_BLOOMBERG_AUTO)
            )
            return
        fields.append(BLIUstProfileField(path=path, value=value, provenance=provenance))

    _add(PATH_DAY_COUNT, UST_PROFILE_DAY_COUNT.value, PROVENANCE_UST_PROFILE_DEFAULT)
    _add(PATH_BOND_TYPE, UST_PROFILE_BOND_TYPE.value, PROVENANCE_UST_PROFILE_DEFAULT)
    _add(PATH_EX_DIVIDEND_DAYS, UST_PROFILE_EX_DIVIDEND_DAYS, PROVENANCE_UST_PROFILE_DEFAULT)

    # Whole-profile admission above has already proved the underlying coupon
    # grid regular before either a confirmed or derived value is applied.
    confirmed_last_coupon = _confirmed_typed_value(PATH_LAST_COUPON_DATE)
    if confirmed_last_coupon is not None:
        fields.append(
            BLIUstProfileField(
                path=PATH_LAST_COUPON_DATE,
                value=confirmed_last_coupon,
                provenance=PROVENANCE_BLOOMBERG_AUTO,
            )
        )
    else:
        fields.append(
            BLIUstProfileField(
                path=PATH_LAST_COUPON_DATE,
                value=derived_last_coupon,
                provenance=PROVENANCE_SHIORI_DERIVED,
            )
        )

    _add(PATH_STATUS, UST_PROFILE_STATUS.value, PROVENANCE_UST_PROFILE_DEFAULT)
    # The reporting date is the run's own valuation date, carried across
    # mechanically -- not a market convention and not a profile constant.
    _add(
        PATH_REPORTING_DATE,
        _parse_iso_date(valuation_date, "valuation_date").isoformat(),
        PROVENANCE_SHIORI_DERIVED,
    )

    expiry = _optional_iso_date(expiry_date, "expiry_date")
    pending: list[str] = []
    if expiry is None:
        pending.extend(EXPIRY_DEPENDENT_FIELD_PATHS)
    else:
        settlement = advance_ust_government_bond_business_days(
            expiry, UST_PROFILE_SETTLEMENT_BUSINESS_DAYS
        ).isoformat()
        _add(PATH_FORWARD_SETTLEMENT_DATE, settlement, PROVENANCE_SHIORI_DERIVED)
        _add(PATH_OPTION_SETTLEMENT_DATE, settlement, PROVENANCE_SHIORI_DERIVED)

    return BLIUstAdvancedFieldProfile(
        supported=True,
        convention_profile=convention_profile,
        rejection_reasons=(),
        fields=tuple(fields),
        pending_field_paths=tuple(pending),
        unresolved_fields=(),
    )
