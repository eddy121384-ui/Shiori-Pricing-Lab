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
   ``last_coupon_date``) are ever read this
   way, and today none of them is mapped, so this tier yields nothing until
   Eddy confirms a mnemonic for one of them. The display-only description
   strings (``DAY_CNT_DES`` / ``MTY_TYP`` / ``CALC_TYP_DES``, carried as
   ``bond_master_raw``) are **never** coerced into a typed value here -- see
   the admission gate below for the only, strictly-narrowing use they have.
2. ``SHIORI_DERIVED`` -- mechanically derived from already-loaded bond terms,
   the valuation date, the expiry date and existing reviewed logic:
   ``last_coupon_date`` (the reviewed coupon-schedule adapter),
   ``reporting_date`` (the run's own valuation date) and the two settlement
   dates (the QuantLib U.S. government-bond calendar).
3. ``UST_PROFILE_DEFAULT`` -- the narrow profile constants this issue
   approves: ACT/ACT day count, fixed-coupon bullet bond type, zero
   ex-dividend days, and the ACTIVE status.
4. ``TRADER_OVERRIDE`` -- owned by the caller, not by this module. Nothing
   here ever overwrites a value the trader has taken over; the browser keeps
   the override set and simply does not re-apply an overridden path.

**Admission gate: fail-closed, and never a mapping.** The profile is applied
only to a bond that passes *every* condition in
:func:`_ust_fixed_coupon_bullet_rejection_reasons`. Anything that fails --
non-USD, a non-US ISIN, callable, sinkable, zero-coupon, not semi-annual,
already matured, an irregular or internally inconsistent coupon grid, or
Bloomberg evidence that does not match the profile's shape exactly -- gets no
profile at all and is reported with its reasons, so an unsupported instrument
stops clearly instead of being silently given UST conventions.

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

**Names that differ from Issue #157's prose (reported deliberately).**

- *Day count.* The issue says "ACT/ACT". This repo's ``DayCount`` enum has
  exactly one ACT/ACT member, ``ACT_ACT_ISDA``, which the reviewed adapter
  maps to ``ql.ActualActual(ql.ActualActual.ISDA)``. That existing member is
  used; no new enum member, and no ICMA/ISMA variant, is invented here.
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
UST_PROFILE_DAY_COUNT = DayCount.ACT_ACT_ISDA
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

_ISIN_LENGTH = 12
_UST_ISIN_COUNTRY_PREFIX = "US"


@dataclass(frozen=True)
class BLIUstProfileField:
    """One resolved field: where it goes, what it is, and which tier it came from."""

    path: str
    value: object
    provenance: str


@dataclass(frozen=True)
class BLIUstAdvancedFieldProfile:
    """The full result of one profile resolution.

    ``supported`` is the admission decision. When it is ``False``, ``fields``
    is empty and ``rejection_reasons`` says exactly why -- never a partial
    profile, and never a UST convention applied to something that is not one.
    ``pending_field_paths`` names fields that are in scope but cannot be
    resolved yet because the run has no expiry date, so they are absent rather
    than guessed.
    """

    supported: bool
    rejection_reasons: tuple[str, ...]
    fields: tuple[BLIUstProfileField, ...]
    pending_field_paths: tuple[str, ...]


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
    advanced = calendar.advance(
        ql.Date(value.day, value.month, value.year), business_days, ql.Days
    )
    return date(advanced.year(), advanced.month(), advanced.dayOfMonth())


def _optional_iso_date(value: object, field_name: str) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _parse_iso_date(value, field_name)
    except ValueError:
        return None


def _ust_fixed_coupon_bullet_rejection_reasons(
    *,
    isin: object,
    currency: object,
    bond_master: dict,
    bond_master_raw: dict,
    valuation_date: object,
) -> tuple[str, ...]:
    """Return every reason this bond is outside the narrow UST profile.

    An empty tuple means admitted. Every reason is collected rather than
    short-circuited, so a trader sees the whole picture at once instead of
    fixing one blocker only to meet the next.
    """

    reasons: list[str] = []

    if not isinstance(isin, str) or len(isin) != _ISIN_LENGTH:
        reasons.append(
            f"isin must be a {_ISIN_LENGTH}-character ISIN to be matched against the "
            f"UST profile, got {isin!r}"
        )
    elif not isin.startswith(_UST_ISIN_COUNTRY_PREFIX):
        reasons.append(
            f"isin {isin!r} does not carry the {_UST_ISIN_COUNTRY_PREFIX!r} country "
            "prefix, so it is not a US-issued security"
        )

    if currency != UST_PROFILE_CURRENCY.value:
        reasons.append(
            f"currency {currency!r} is not {UST_PROFILE_CURRENCY.value} -- the UST "
            "fixed-coupon bullet profile covers USD Treasuries only"
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

    if issue is not None and maturity is not None and first_coupon is not None:
        try:
            derive_last_coupon_date(
                issue_date=issue.isoformat(),
                maturity_date=maturity.isoformat(),
                first_coupon_date=first_coupon.isoformat(),
                coupon_frequency=UST_PROFILE_COUPON_FREQUENCY,
            )
        except BLIBondScheduleError as exc:
            reasons.append(
                "the reviewed coupon-schedule adapter does not accept this bond's grid as "
                f"regular, so no coupon date is derived: {exc}"
            )

    return tuple(reasons)


def resolve_ust_advanced_field_profile(
    *,
    isin: object,
    currency: object,
    bond_master: dict | None,
    bond_master_raw: dict | None,
    valuation_date: object,
    expiry_date: object = None,
) -> BLIUstAdvancedFieldProfile:
    """Resolve the eight Advanced technical fields for one Bloomberg-loaded bond.

    Returns an unsupported profile with explicit reasons for anything outside
    the narrow UST fixed-coupon bullet universe (see
    :func:`_ust_fixed_coupon_bullet_rejection_reasons`) -- no partial profile
    is ever returned, and no field is filled for a bond the profile does not
    cover.

    ``expiry_date`` is optional because the trader may not have entered the
    expiry yet. Without it, the two settlement dates are reported in
    ``pending_field_paths`` instead of being guessed; the other six fields are
    still resolved, so the ordinary workflow is unblocked immediately after
    Bloomberg Load.

    Raises ``BLIQuantLibNotAvailableError`` when QuantLib is not installed --
    the coupon schedule and the U.S. government-bond calendar both come from
    it, and neither is approximated locally.
    """

    _require_quantlib()
    bond_master = dict(bond_master or {})
    bond_master_raw = dict(bond_master_raw or {})

    reasons = _ust_fixed_coupon_bullet_rejection_reasons(
        isin=isin,
        currency=currency,
        bond_master=bond_master,
        bond_master_raw=bond_master_raw,
        valuation_date=valuation_date,
    )
    if reasons:
        return BLIUstAdvancedFieldProfile(
            supported=False, rejection_reasons=reasons, fields=(), pending_field_paths=()
        )

    fields: list[BLIUstProfileField] = []

    def _confirmed_typed_value(path: str) -> object | None:
        return bond_master.get(_BLOOMBERG_TYPED_FIELD_BY_PATH[path])

    def _add(path: str, value: object, provenance: str) -> None:
        confirmed = (
            _confirmed_typed_value(path) if path in _BLOOMBERG_TYPED_FIELD_BY_PATH else None
        )
        if confirmed is not None:
            fields.append(
                BLIUstProfileField(
                    path=path, value=confirmed, provenance=PROVENANCE_BLOOMBERG_AUTO
                )
            )
            return
        fields.append(BLIUstProfileField(path=path, value=value, provenance=provenance))

    _add(PATH_DAY_COUNT, UST_PROFILE_DAY_COUNT.value, PROVENANCE_UST_PROFILE_DEFAULT)
    _add(PATH_BOND_TYPE, UST_PROFILE_BOND_TYPE.value, PROVENANCE_UST_PROFILE_DEFAULT)
    _add(PATH_EX_DIVIDEND_DAYS, UST_PROFILE_EX_DIVIDEND_DAYS, PROVENANCE_UST_PROFILE_DEFAULT)
    _add(
        PATH_LAST_COUPON_DATE,
        derive_last_coupon_date(
            issue_date=bond_master["issue_date"],
            maturity_date=bond_master["maturity_date"],
            first_coupon_date=bond_master["first_coupon_date"],
            coupon_frequency=UST_PROFILE_COUPON_FREQUENCY,
        ),
        PROVENANCE_SHIORI_DERIVED,
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
        rejection_reasons=(),
        fields=tuple(fields),
        pending_field_paths=tuple(pending),
    )
