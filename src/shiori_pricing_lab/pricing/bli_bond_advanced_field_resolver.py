"""Common bond resolver for the standalone route's eight non-market Advanced
technical fields (Issue #161, parent milestone #143; supersedes PR #162's
``bli_ust_fixed_coupon_profile``).

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

**One resolver, many markets (Issue #161 requirement A).** PR #162 shipped
this logic as ``bli_ust_fixed_coupon_profile``, with every derivation --
including the ones that have nothing to do with any market's conventions --
sitting behind a gate named for, and reachable only through, the UST profile.
Real-workstation UAT on a U.S. corporate bond and a German government bond
showed the cost: both loaded from Bloomberg fine, and both still made the
trader hand-type a backend contract, because the *shared* machinery was
UST-shaped.

The split this module now implements:

- **Here** -- everything that does not vary by market: the reporting date
  carried from the run's own valuation date, the matured/ACTIVE comparison,
  the coupon grid and its ``last_coupon_date`` via the reviewed schedule
  adapter, the precedence of a confirmed typed Bloomberg value over every
  other tier, the product-shape refusals that are about the bond's own terms
  (callable, sinkable, non-positive or non-numeric coupon, already matured),
  and the whole field-level BLOCKED/unresolved contract.
- **``bli_bond_convention_profile.py``** -- only the values that genuinely
  differ between markets, one frozen record per profile: currency, the coupon
  frequencies its conventions are stated for, day count and (where observed)
  the Bloomberg description string that agrees with it, bond type,
  ex-dividend days where a default was confirmed, status, settlement lag,
  settlement calendar, and whether the profile may be applied only to a bond
  whose plain fixed-coupon structure is positively established.

Supporting a further market is therefore a new profile record, never a second
copy of this file. Nothing below branches on a profile *name*.

**Field-level resolution (Issue #161).** The first revision of this logic
resolved all eight fields or none of them: one failing condition emptied
``fields`` entirely. Real-workstation UAT on ``US91282CMC28`` showed why that
is wrong -- a single display-only description string (``MTY_TYP = NORMAL``
rather than ``AT MATURITY``) wiped out seven fields that were perfectly well
known, and left a trader typing a backend contract by hand for an ordinary
Treasury note.

The gate is split in two, and the difference is exactly the difference a
trader can act on:

- **Product/schedule admission** (:func:`_product_rejection_reasons` plus the
  coupon-grid check) -- callable, sinkable, a currency or coupon frequency the
  selected profile states no conventions for, zero/non-fixed coupon, already
  matured, or a coupon grid the reviewed adapter cannot carry. These fail
  closed for the *whole* profile: ``supported`` is ``False``, ``fields`` is
  empty, and **no Advanced edit can repair the ticket**, so the browser must
  not offer one.
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
   depend on the selected profile at all: a confirmed Bloomberg value is
   used regardless of which profile is selected.
2. ``SHIORI_DERIVED`` -- mechanically derived from already-loaded bond terms,
   the valuation date, the expiry date and existing reviewed logic:
   ``last_coupon_date`` (the reviewed coupon-schedule adapter),
   ``reporting_date`` (the run's own valuation date) and the two settlement
   dates (the selected profile's reviewed QuantLib calendar). These
   derivations are mechanical, not profile-owned constants -- but they only
   run once the product-shape gate below has passed for the selected profile.
3. ``<PROFILE>_PROFILE_DEFAULT`` -- the selected profile's own constants:
   ``UST_PROFILE_DEFAULT`` for the registered UST profile, and a
   correspondingly-named tier for any other registered profile. **This is the
   one tier actually owned by, and named after, the selected convention
   profile**, and the label comes from the profile record itself
   (``BLIConventionProfile.default_provenance``) so the two can never drift.
4. ``TRADER_OVERRIDE`` -- owned by the caller, not by this module. Nothing
   here ever overwrites a value the trader has taken over; the browser keeps
   the override set and simply does not re-apply an overridden path.
5. ``BLOCKED`` -- this one field has no safe data or method on this run, and
   is reported in ``unresolved_fields`` with its own reason rather than
   guessed. It never removes a value from any other field.

**Convention Profile: a browser-state input, never a server-computed identity
claim (Issue #157 P1-1 correction, unchanged).** This logic's first revision
tried to gate admission on *proving* the bond is issued by the U.S. Treasury
(first via the ISIN's ``US`` country prefix, then via the CUSIP's ``912``
issuer-number block). Both were withdrawn on Eddy's explicit
product-direction correction: Shiori's long-term direction is not
Treasury-only, and this module must never claim to have verified an issuer's
identity -- there is no ``identity_verified`` field, no issuer
classification, and no such claim anywhere in this module's output.

Instead, ``convention_profile`` is a **required, explicit parameter** of
:func:`resolve_bond_advanced_field_profile` -- supplied by the caller
(ultimately, browser state) on every call -- naming *which convention profile
is currently selected*, not what Shiori has deduced about the bond. It is
validated against the registry in ``bli_bond_convention_profile.py``; a
missing, blank, or unregistered value raises ``ValueError`` immediately, and
Shiori never silently falls back to a default profile. The resolved profile's
own :attr:`BLIBondAdvancedFieldProfile.convention_profile` echoes the
validated selection back, so the browser renders the profile it was actually
resolved against rather than the one it assumes it asked for.

**Shiori never chooses the profile.**
``bli_bond_convention_profile.convention_profile_candidates`` narrows the
registry to the profiles whose stated conventions cover this bond's own
confirmed currency and coupon frequency, and stops there: those two facts
describe a bond's cash flows, not its issuer class, and the Bloomberg field
that would classify it (``SECURITY_TYP``) is an unprobed candidate. The
trader selects from what is left. When nothing is left, that is a refusal;
when something is, the honest request is a profile selection, never eight
hand-typed technical fields.

**Plain fixed-coupon structural evidence.** A positive coupon at some
frequency, on a bond that is neither callable nor sinkable, does not
establish that the bond is an ordinary fixed-coupon bullet. A floating-rate
note returns its *current* coupon in ``CPN`` and its reset frequency in
``CPN_FREQ``; an inflation-linked bond, a convertible, and a bond with any
other non-bullet redemption structure all pass the same checks.

Bloomberg workstation evidence (Issue #161 follow-up; see
``bli_bond_convention_profile``'s own evidence log for the full probe
record) confirms all four
:data:`~bli_bond_convention_profile.PLAIN_FIXED_COUPON_EVIDENCE_FIELDS`:
``coupon_type`` (via ``CPN_TYP``, confirmed value ``"FIXED"``),
``inflation_linked_flag`` and ``convertible_flag`` (via
``INFLATION_LINKED_INDICATOR``/``CONVERTIBLE``, confirmed value ``False``),
and ``maturity_refund_type`` (via ``MTY_TYP``/DS092, confirmed values
``"NORMAL"`` or ``"AT MATURITY"`` -- see below). Each is checked by *value*,
via :func:`~bli_bond_convention_profile.confirms_plain_fixed_coupon_evidence`,
never merely by presence -- a real floater's ``coupon_type == "FLOATING"``
must still fail to confirm anything, and so must a real callable bond's
``maturity_refund_type == "CALLABLE"``.

``security_type`` is deliberately not one of the four fields
(Eddy's explicit correction): ``SECURITY_TYP`` is confirmed to return a
value (``"US GOVERNMENT"`` / ``"GLOBAL"`` / ``"EURO-ZONE"`` across the three
probed securities), but it cannot safely classify a profile, and Shiori
never auto-selects one anyway -- the trader always picks explicitly (see
``bli_bond_convention_profile.convention_profile_candidates``) -- so gating
*admission* on a field whose only conceivable job would have been
classification added friction with no safety benefit. It is still carried
on ``bond_master`` as display/evidence data; it just no longer blocks
anything here.

``amortizing_flag`` was the fourth field until Eddy's redemption-structure
correction replaced it with ``maturity_refund_type`` (below): it had **no
approved criterion at all** (no working amortizing-evidence mnemonic exists
-- ``IS_AMORTIZING``/``AMORT_TYP``/``REDEMP_TYP``/``SCHED_TYP`` confirmed
``BAD_FLD``, ``MTG_TYP`` confirmed not applicable, ``PRINCIPAL_FACTOR ==
1.0`` explicitly not approved as evidence), so it always failed
:func:`confirms_plain_fixed_coupon_evidence` regardless of what
``bond_master`` held for it -- keeping every non-UST profile permanently
fail-closed, with no path to admission for any bond. It is no longer read
here at all, the same non-gating treatment as ``security_type``.

``maturity_refund_type`` (Issue #161 follow-up: redemption-structure gate)
is sourced from Bloomberg's ``MTY_TYP`` (DS092), whose own Full Definition /
Enumerations name a bounded set of redemption-structure values. Unlike
``amortizing_flag``, this is a real, bounded classification: exactly
``"NORMAL"`` and ``"AT MATURITY"`` describe a plain, unconditional bullet
redemption and are the entire allowlist (see
:func:`~bli_bond_convention_profile._confirms_plain_bullet_redemption_structure`);
``"CALLABLE"``, ``"PUTABLE"``, ``"SINKABLE"``, ``"CONVERTIBLE"``,
``"PERPETUAL"``, ``"PASS-THRU"``, ``"EXTENDIBLE"``, any combination value
carrying one of those as a substring, and anything absent or
field-exceptioned are all outside it and fail closed the same way -- **not**
a denylist, so a value Bloomberg adds later fails closed too. ``UST``
(``US91282CMC28``) confirmed ``"NORMAL"``; a real USD corporate bond, AMZN,
confirmed ``"CALLABLE"`` and is correctly refused -- proof the allowlist
discriminates, not a candidate positive UAT security. See ``MTY_TYP``'s own
note below for why this is a *different* use of the same mnemonic from the
one this module withdrew, not a reintroduction of it.

Every profile except ``UST`` therefore refuses a bond whose structure is not
*fully* positively established, and the refusal is a *product* refusal no
Advanced edit repairs. It is checked here rather than in the browser
precisely so that selecting a profile by hand cannot push a floater, or a
real callable bond, through. ``UST`` is exempt by Eddy's explicit
instruction: that behaviour passed real Bloomberg workstation UAT on
``US91282CMC28`` and must not regress.

**Product-shape gate: fail-closed, and never an identity claim.** The
profile is applied only to a bond that passes *every* condition in
:func:`_product_rejection_reasons`: a currency and coupon frequency the
selected profile states conventions for, not callable, not sinkable, a
positive numeric coupon, not already matured, and (for every profile but
``UST``) a positively established plain fixed-coupon structure. Anything that fails gets no
profile at all and is reported with its reasons, so an unsupported instrument
stops clearly instead of being silently given some market's conventions. None
of these conditions is, or was ever intended as, proof of who issued the
bond -- they test only whether this bond's own terms fit the shape the
selected profile's conventions assume. A bond that fits the shape but is not
actually from that market still passes when that profile is selected -- that
is the explicit, accepted design (Issue #157 review discussion), not an
oversight: the profile is applied because it is selected, not because Shiori
believes it has identified the issuer.

**The display-only description strings are evidence, never a single-string
admission equality (Issue #161 correction).** The first revision made
``DAY_CNT_DES = ACT/ACT``, ``MTY_TYP = AT MATURITY`` and ``CALC_TYP_DES =
STREET CONVENTION`` *necessary* conditions for the whole profile -- one
hardcoded expected string, compared for equality against the raw
``bond_master_raw`` description. Bloomberg workstation UAT on
``US91282CMC28`` -- an ordinary 4.5% 12/31/31 Treasury note -- returned
``MTY_TYP = NORMAL``, so that string equality was rejecting real USTs.
``CALC_TYP_DES`` is therefore no longer read here at all: nothing in this
module has established what its values mean semantically, and an unproven
string must not decide a product's fate in either direction.

``MTY_TYP`` itself is read again (Issue #161 follow-up: redemption-structure
gate above), but as a genuinely different mechanism, not a reinstatement of
the withdrawn one: it is now a **typed structural-evidence field**
(``maturity_refund_type`` on ``bond_master``, populated the same way
``coupon_type``/``inflation_linked_flag``/``convertible_flag`` are), checked
against a **two-value allowlist** (``"NORMAL"`` *or* ``"AT MATURITY"``) that
does not depend on which profile is selected -- exactly the bug the first
revision had, where a single profile-specific expected string rejected the
other equally-valid one, is what the allowlist has two entries to prevent.
The separate, still-unchanged ``bond_master_raw["maturity_type"]`` display
string is untouched by this and still never interpreted.

``DAY_CNT_DES`` keeps exactly one, strictly-narrowing, **field-level** role:
when it is present and reads something other than the selected profile's
``day_count_evidence``, Bloomberg's own description contradicts the day count
that profile would otherwise supply, so ``day_count`` alone comes back
``BLOCKED`` for the trader to set. The seven other fields are unaffected.
This is still the opposite of the mapping #145 forbids: a matching string
never *produces* a typed value (the value comes from the approved profile
constant), a missing string blocks nothing, and a contradicting string can
only withhold the one field it is evidence about. A profile whose own
``day_count_evidence`` is ``None`` -- because Bloomberg's description string
for that market has not been observed -- does not read ``DAY_CNT_DES`` at
all, since comparing against a guessed expected string would block
``day_count`` on every ordinary bond in that market.

**Ex-dividend days follow the same "no approved value, no guess" rule.** A
profile carries an ex-dividend default only where one was actually confirmed
for that market. Where it was not, a typed Bloomberg value wins if one is
ever returned, and otherwise ``ex_dividend_days`` alone comes back
``BLOCKED``: ``ex-dividend-days-input`` is a real Advanced control, so a
trader override is a genuine route past it, and every other Advanced field
still resolves normally. All three profiles registered today (``UST``,
``US_CORPORATE``, ``GERMAN_GOVT``) carry a confirmed zero (Issue #161
follow-up: ex-dividend convention convergence), so this path no longer
blocks any of them -- the mechanism stays exactly as it is for a future
market whose window is not confirmed, or is confirmed non-zero (a UK Gilt;
see ``bli_bond_convention_profile``'s own "Deliberately not registered"
note).

**Irregular schedules fail closed.** The current typed pricing adapter accepts
only one internally consistent regular coupon grid. An irregular or stubbed
grid therefore rejects the whole profile: editing ``last_coupon_date`` cannot
repair the underlying issue/maturity/first-coupon grid that the adapter also
validates. That is a genuine product/schedule refusal, not a field-level
blocker, and the browser must not offer an Advanced route out of it.

**A missing schedule date leaves the rest of the profile filled -- but is not
always a repairable ``BLOCKED`` (Issue #161 P2 correction).** ``BLOCKED``
promises the browser a genuine route past it: an Advanced control the
trader can fill that, once filled, actually lets the ticket price. That
promise is only true when the field the trader would type into is the one
thing standing in the way.

``reporting_date`` keeps that promise: it depends on this run's own
``valuation_date`` (never a Bloomberg field), and ``reporting-date-input``
is a real Advanced control, so a malformed ``valuation_date`` correctly
comes back ``BLOCKED`` there.

``last_coupon_date`` and ``status`` do not, when the *reason* is a missing
``issue_date``, ``maturity_date`` or ``first_coupon_date``. Real Bloomberg
workstation UAT (a bond shaped like ``US91282CMC28`` with no confirmed
``first_coupon_date``) exposed the bug: the first revision of this
correction marked ``last_coupon_date`` ``BLOCKED`` there, and the browser
offered its Advanced control as if typing a date would complete the
ticket. It never could -- ``issue_date``, ``maturity_date`` and
``first_coupon_date`` are genuine ``BondReferenceData`` destination fields
(see :data:`_BOND_MASTER_ONLY_FIELDS`) with **no Advanced override
anywhere on this route**, so the reviewed typed builder
(``BLIStandaloneBondReferenceData.__post_init__``) still refuses the draft
on the missing one regardless of what the trader types for
``last_coupon_date``. The browser's own "Bloomberg Bond Master" workflow
group already reports and blocks on exactly this, correctly, with the real
next step (re-run Bloomberg Load) -- offering a second, ineffective route
beside it is precisely the fake exit Issue #161 exists to remove.

So: when the underlying reason is one of :data:`_BOND_MASTER_ONLY_FIELDS`
being missing or unusable, ``last_coupon_date`` and ``status`` are left
**unresolved** -- absent from both ``fields`` and ``unresolved_fields`` --
rather than ``BLOCKED``. Every other field in scope (day count, bond type,
ex-dividend days, and ``status``/``reporting_date`` when *their own* inputs
are fine) is still resolved exactly as before; only the one or two fields
this specific gap actually prevents are affected.

**Coupon frequency comes from Bloomberg, compatibility comes from the
profile.** The coupon grid is generated with the frequency ``CPN_FREQ``
confirmed for this bond, not with a constant belonging to any one market. The
profile's role is only to state which frequencies its conventions cover, so a
bond whose confirmed frequency is outside that set fails closed rather than
borrowing another frequency's conventions. For the UST profile the two are
the same value, which is why UST behaviour is unchanged by the split.

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

from shiori_pricing_lab.pricing.bli_bond_convention_profile import (
    PLAIN_FIXED_COUPON_EVIDENCE_FIELDS,
    BLIConventionProfile,
    confirms_plain_fixed_coupon_evidence,
    get_convention_profile,
)
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import (
    BLIBondScheduleError,
    BLIQuantLibNotAvailableError,
    derive_last_coupon_date,
)
from shiori_pricing_lab.reference_data._validation import _parse_iso_date

try:
    import QuantLib as ql
except ImportError:  # QuantLib is optional -- pyproject.toml [project.optional-dependencies].quant
    ql = None


# --- Provenance tiers (Issue #157) --------------------------------------------

PROVENANCE_BLOOMBERG_AUTO = "BLOOMBERG_AUTO"
PROVENANCE_SHIORI_DERIVED = "SHIORI_DERIVED"
PROVENANCE_TRADER_OVERRIDE = "TRADER_OVERRIDE"
# Not a value tier: the explicit label for a field this run refused to fill.
PROVENANCE_BLOCKED = "BLOCKED"

# The value tiers that are the same whatever profile is selected. The
# remaining one is the selected profile's own ``default_provenance``
# (``UST_PROFILE_DEFAULT``, and one per further registered profile), which is
# owned by ``bli_bond_convention_profile.py`` rather than named here.
PROVENANCE_MARKET_INDEPENDENT_TIERS = (
    PROVENANCE_BLOOMBERG_AUTO,
    PROVENANCE_SHIORI_DERIVED,
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

ADVANCED_FIELD_PATHS = (
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

# Bond Master keys that are genuine BondReferenceData destination fields, so a
# non-null value there is a typed Bloomberg value rather than a description
# string. ``status`` is deliberately absent: it is not a destination field at
# all, so no Bloomberg tier exists for it.
#
# ``ex_dividend_days`` is listed even though the Bloomberg bond-quote loader
# does not populate it today (its ``_BOND_MASTER_DESTINATION_FIELDS`` has no
# such entry, and no mnemonic for one is confirmed -- ``EX_DVD_DT`` is still
# an unprobed candidate). Listing it adds no mapping and invents no mnemonic:
# it means that *if* Eddy ever confirms one, the typed value it returns is
# used ahead of any profile default, exactly like the other three. Until
# then ``bond_master.get("ex_dividend_days")`` is simply ``None``, and a
# profile with no approved default blocks that one field.
_BLOOMBERG_TYPED_FIELD_BY_PATH = {
    PATH_DAY_COUNT: "day_count",
    PATH_BOND_TYPE: "bond_type",
    PATH_LAST_COUPON_DATE: "last_coupon_date",
    PATH_EX_DIVIDEND_DAYS: "ex_dividend_days",
}

# Bond Master destination fields with no Advanced override control anywhere
# on this route (Issue #161 P2 correction) -- the browser's "Bloomberg Bond
# Master" section shows them read-only, sourced from Bloomberg verbatim, and
# offers no manual-entry control for any of them. When one of these is
# missing or unusable, no Advanced entry can repair the fields that derive
# from it: the fix is a fresh, successful Bloomberg Load, not a trader
# override. See the module docstring's "A missing schedule date leaves the
# rest of the profile filled" section.
_BOND_MASTER_ONLY_FIELDS = ("issue_date", "maturity_date", "first_coupon_date")


@dataclass(frozen=True)
class BLIBondProfileField:
    """One resolved field: where it goes, what it is, and which tier it came from."""

    path: str
    value: object
    provenance: str


@dataclass(frozen=True)
class BLIBondUnresolvedField:
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
class BLIBondAdvancedFieldProfile:
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
    fields: tuple[BLIBondProfileField, ...]
    pending_field_paths: tuple[str, ...]
    unresolved_fields: tuple[BLIBondUnresolvedField, ...]


def _require_quantlib() -> None:
    if ql is None:
        raise BLIQuantLibNotAvailableError(
            "QuantLib is not installed -- install the optional 'quant' dependency group "
            '(pip install "shiori-pricing-lab[quant]") to use this function'
        )


def advance_settlement_business_days(
    value: date, business_days: int, profile: BLIConventionProfile
) -> date:
    """Return ``value`` advanced by ``business_days`` of ``profile``'s calendar.

    ``business_days`` must be positive. QuantLib's own ``Calendar.advance``
    performs the roll, so a start date that is itself a weekend or holiday is
    handled by QuantLib's rules rather than by any arithmetic here, and the
    calendar itself is the reviewed one the profile names -- this module
    writes no holiday table of its own, partial or otherwise.
    """

    if isinstance(business_days, bool) or not isinstance(business_days, int):
        raise ValueError(f"business_days must be a positive integer, got {business_days!r}")
    if business_days <= 0:
        raise ValueError(f"business_days must be a positive integer, got {business_days}")
    _require_quantlib()
    calendar = profile.calendar()
    advanced = calendar.advance(ql.Date(value.day, value.month, value.year), business_days, ql.Days)
    return date(advanced.year(), advanced.month(), advanced.dayOfMonth())


def _optional_iso_date(value: object, field_name: str) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _parse_iso_date(value, field_name)
    except ValueError:
        return None


def _product_rejection_reasons(
    *,
    profile: BLIConventionProfile,
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

    Currency and coupon frequency are checked against the *selected
    profile's* stated conventions rather than against any constant of this
    module's own: a profile covers the combinations it actually states
    conventions for, and a bond outside them fails closed instead of
    borrowing another market's rules.

    Every reason here is one **no Advanced override can repair**. Anything a
    trader could genuinely fix by typing a value is a field-level
    ``BLOCKED`` instead, resolved per field below.

    Coupon-grid *regularity* is checked separately, immediately after this
    function's reasons come back empty, by
    :func:`resolve_bond_advanced_field_profile` itself: an irregular,
    stubbed, or Bloomberg-confirmed-mismatched grid rejects the whole profile
    there (see the module docstring's "Irregular schedules fail closed"
    section), not just ``last_coupon_date``. A schedule date that is simply
    *absent* is not a rejection at all -- there is no grid to call irregular,
    so only ``last_coupon_date`` blocks.
    """

    reasons: list[str] = []

    if currency != profile.currency.value:
        reasons.append(
            f"currency {currency!r} is not {profile.currency.value} -- the {profile.name} "
            f"convention profile covers {profile.currency.value}-denominated bonds only"
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
    if not any(frequency.value == coupon_frequency for frequency in profile.coupon_frequencies):
        stated = ", ".join(frequency.value for frequency in profile.coupon_frequencies)
        reasons.append(
            f"coupon_frequency is {coupon_frequency!r}, not {stated}; the {profile.name} "
            "convention profile states conventions for those coupon frequencies only"
        )

    # Plain fixed-coupon structural evidence. A positive coupon at some
    # frequency, on a bond that is neither callable nor sinkable, does *not*
    # establish a fixed-coupon bullet: a floater returns its current coupon in
    # CPN and its reset frequency in CPN_FREQ, and an inflation-linked bond, a
    # convertible, and a bond with any other non-bullet redemption structure
    # all pass every check above too.
    #
    # This is a *value* check, not a presence check (Issue #161 follow-up):
    # `bond_master.get("coupon_type")` can be a real floater's "FLOATING" --
    # non-None, but not evidence of a plain bullet -- so each field is judged
    # by `confirms_plain_fixed_coupon_evidence`, which requires the specific
    # value Bloomberg workstation evidence confirmed (see
    # `bli_bond_convention_profile`'s own evidence log). `security_type` is
    # deliberately not one of PLAIN_FIXED_COUPON_EVIDENCE_FIELDS any more
    # (Eddy's correction: it cannot safely classify a profile, and Shiori
    # never auto-selects one anyway, so gating on it added friction with no
    # safety benefit) -- it is still carried on bond_master as display/
    # evidence data, just never read here.
    #
    # `maturity_refund_type` (Bloomberg's MTY_TYP/DS092) is this gate's
    # redemption-structure evidence (Issue #161 follow-up, replacing the
    # permanently-unconfirmable `amortizing_flag`): only Bloomberg's own
    # "NORMAL"/"AT MATURITY" -- a plain bullet at maturity -- confirm
    # anything; every other confirmed enumeration value (CALLABLE, PUTABLE,
    # SINKABLE, CONVERTIBLE, PERPETUAL, PASS-THRU, EXTENDIBLE, or a
    # combination carrying one of those) and anything absent or
    # field-exceptioned fail closed the same way. A real callable bond's
    # confirmed "CALLABLE" is non-None and must still fail to confirm
    # anything -- the same value-not-presence rule as `coupon_type` above,
    # not a special case. Selecting a profile by hand can never push such a
    # bond through: this is checked here, not in the browser.
    if profile.plain_fixed_coupon_evidence_required:
        unconfirmed = tuple(
            field
            for field in PLAIN_FIXED_COUPON_EVIDENCE_FIELDS
            if not confirms_plain_fixed_coupon_evidence(field, bond_master.get(field))
        )
        if unconfirmed:
            reasons.append(
                "Bloomberg has not confirmed this bond is a plain fixed-coupon bullet: "
                f"{', '.join(unconfirmed)} {'is' if len(unconfirmed) == 1 else 'are'} not "
                "confirmed, so Shiori cannot rule out a floating-rate, inflation-linked, "
                "convertible, or unsupported redemption structure (callable, putable, "
                "sinkable, perpetual, pass-through, extendible, or otherwise not a plain "
                f"bullet at maturity). The {profile.name} convention profile is applied "
                "only to a bond whose structure is positively established, and selecting "
                "it by hand does not establish it"
            )

    # A missing or malformed valuation/maturity date is *not* rejected here:
    # it blocks only the individual fields that depend on it (``status`` and
    # ``reporting_date``, resolved per field in
    # :func:`resolve_bond_advanced_field_profile`), leaving the rest resolved.
    # Only a bond Shiori can positively see has matured fails closed.
    valuation = _optional_iso_date(valuation_date, "valuation_date")
    maturity = _optional_iso_date(bond_master.get("maturity_date"), "maturity_date")
    if maturity is not None and valuation is not None and maturity <= valuation:
        reasons.append(
            f"maturity_date ({maturity.isoformat()}) is not after valuation_date "
            f"({valuation.isoformat()}); a matured bond is never pre-filled as ACTIVE"
        )

    return tuple(reasons)


def resolve_bond_advanced_field_profile(
    *,
    convention_profile: object,
    isin: object,
    currency: object,
    bond_master: dict | None,
    bond_master_raw: dict | None,
    valuation_date: object,
    expiry_date: object = None,
) -> BLIBondAdvancedFieldProfile:
    """Resolve the eight Advanced technical fields for one Bloomberg-loaded bond.

    ``convention_profile`` is required, caller-selected browser state naming
    which convention profile to apply -- see the module docstring's
    "Convention Profile" section. Raises ``ValueError`` for a missing,
    blank, or unregistered selection. ``isin`` is carried through for
    identification only and is not used to gate anything (the withdrawn
    ISIN-country-prefix check is not here).

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
    the coupon schedule and the settlement calendar both come from it, and
    neither is approximated locally.
    """

    _require_quantlib()
    profile = get_convention_profile(convention_profile)
    bond_master = dict(bond_master or {})
    bond_master_raw = dict(bond_master_raw or {})

    reasons = _product_rejection_reasons(
        profile=profile,
        currency=currency,
        bond_master=bond_master,
        valuation_date=valuation_date,
    )
    if reasons:
        return _unsupported(profile.name, reasons)

    valuation = _optional_iso_date(valuation_date, "valuation_date")
    maturity = _optional_iso_date(bond_master.get("maturity_date"), "maturity_date")
    missing_schedule_inputs = tuple(
        name
        for name in _BOND_MASTER_ONLY_FIELDS
        if _optional_iso_date(bond_master.get(name), name) is None
    )

    # The coupon grid is only *judged* when all three dates that define it are
    # present. A grid that exists and is irregular is a product/schedule
    # refusal (no Advanced edit repairs it). Dates that are simply absent
    # leave nothing to judge, so only last_coupon_date blocks, below. The
    # frequency is the one Bloomberg confirmed for this bond; the gate above
    # has already established the selected profile states conventions for it.
    derived_last_coupon = None
    if not missing_schedule_inputs:
        try:
            derived_last_coupon = derive_last_coupon_date(
                issue_date=bond_master["issue_date"],
                maturity_date=bond_master["maturity_date"],
                first_coupon_date=bond_master["first_coupon_date"],
                coupon_frequency=bond_master["coupon_frequency"],
            )
        except BLIBondScheduleError:
            derived_last_coupon = None
        confirmed_last_coupon = bond_master.get("last_coupon_date")
        if derived_last_coupon is None or (
            confirmed_last_coupon is not None and confirmed_last_coupon != derived_last_coupon
        ):
            return _unsupported(
                profile.name,
                (
                    "current pricing adapter supports regular coupon schedules only; "
                    "editing last_coupon_date cannot repair the underlying schedule",
                ),
            )

    fields: list[BLIBondProfileField] = []
    blocked: list[BLIBondUnresolvedField] = []

    def _confirmed_typed_value(path: str) -> object | None:
        master_field = _BLOOMBERG_TYPED_FIELD_BY_PATH.get(path)
        return None if master_field is None else bond_master.get(master_field)

    def _resolve_field(path: str, value: object, provenance: str) -> None:
        """Fill one field from Bloomberg if it confirmed a typed value, else as given."""

        confirmed = _confirmed_typed_value(path)
        if confirmed is not None:
            fields.append(
                BLIBondProfileField(
                    path=path, value=confirmed, provenance=PROVENANCE_BLOOMBERG_AUTO
                )
            )
            return
        fields.append(BLIBondProfileField(path=path, value=value, provenance=provenance))

    def _block(path: str, reason: str) -> None:
        """Withhold exactly one field, leaving every other field untouched."""

        blocked.append(BLIBondUnresolvedField(path=path, reason=reason))

    # Day count. Bloomberg's DAY_CNT_DES is the one description string still
    # read, and only to withhold this single field when it contradicts the
    # selected profile (Issue #161) -- never to produce a typed value.
    # A profile with no confirmed ``day_count_evidence`` string does not read
    # DAY_CNT_DES at all: without an observed expected value there is nothing
    # to call a contradiction, and comparing against a guessed string would
    # block ``day_count`` on every ordinary bond in that market.
    day_count_evidence = bond_master_raw.get("day_count")
    day_count_evidence_contradicts = (
        profile.day_count_evidence is not None
        and _confirmed_typed_value(PATH_DAY_COUNT) is None
        and isinstance(day_count_evidence, str)
        and bool(day_count_evidence.strip())
        and day_count_evidence != profile.day_count_evidence
    )
    if day_count_evidence_contradicts:
        _block(
            PATH_DAY_COUNT,
            f"Bloomberg day-count evidence reads {day_count_evidence!r}, not "
            f"{profile.day_count_evidence!r}, so it contradicts this profile's day "
            "count; Shiori will not map a description string into a typed day count, and "
            "will not apply the profile default over Bloomberg's own description",
        )
    else:
        _resolve_field(PATH_DAY_COUNT, profile.day_count.value, profile.default_provenance)

    _resolve_field(PATH_BOND_TYPE, profile.bond_type.value, profile.default_provenance)

    # Ex-dividend days. A profile carries an approved default only where one
    # was actually confirmed for that market (``UST``'s zero). Where it was
    # not, a typed Bloomberg value is used if one is ever returned, and
    # otherwise this one field blocks for the trader to set -- a genuine
    # route past it, since ``ex-dividend-days-input`` is a real Advanced
    # control. Every other Advanced field still resolves.
    if (
        profile.ex_dividend_days is not None
        or _confirmed_typed_value(PATH_EX_DIVIDEND_DAYS) is not None
    ):
        _resolve_field(
            PATH_EX_DIVIDEND_DAYS, profile.ex_dividend_days, profile.default_provenance
        )
    else:
        _block(
            PATH_EX_DIVIDEND_DAYS,
            f"the {profile.name} convention profile has no approved ex-dividend default, "
            "and Bloomberg returned no typed ex-dividend value for this bond, so Shiori "
            "will not guess one; every other field on this bond keeps the value Shiori "
            "resolved for it",
        )

    # Last coupon date. A confirmed typed Bloomberg value wins; otherwise the
    # reviewed coupon-schedule adapter derives it -- which needs all three
    # schedule dates. When one of those (all `_BOND_MASTER_ONLY_FIELDS`, with
    # no Advanced override anywhere) is missing, this is left unresolved
    # rather than BLOCKED: BLOCKED promises a trader override is a genuine
    # route past it, and there is none here -- see the module docstring's
    # "A missing schedule date leaves the rest of the profile filled"
    # section (Issue #161 P2 correction).
    if _confirmed_typed_value(PATH_LAST_COUPON_DATE) is not None or derived_last_coupon is not None:
        _resolve_field(PATH_LAST_COUPON_DATE, derived_last_coupon, PROVENANCE_SHIORI_DERIVED)
    # else: left unresolved (absent from both `fields` and `unresolved_fields`)
    # -- `missing_schedule_inputs` is always non-empty here, so this is never
    # a field the trader's own Advanced entry could fix.

    # Status. ACTIVE is asserted only where the product gate could actually
    # prove the bond has not matured. A missing maturity_date is the same
    # no-Advanced-override situation as last_coupon_date above, so it is left
    # unresolved rather than BLOCKED for the same reason. A missing or
    # malformed valuation_date is different: it is this run's own date, not
    # a Bloomberg Bond Master field, and reporting_date's own BLOCKED case
    # below shows the same input genuinely is repairable in Advanced.
    if maturity is not None:
        if valuation is None:
            _block(
                PATH_STATUS,
                "Shiori cannot compare this bond's maturity date "
                f"({bond_master.get('maturity_date')!r}) with the run's valuation date "
                f"({valuation_date!r}), so it will not assert that the bond is still active",
            )
        else:
            _resolve_field(PATH_STATUS, profile.status.value, profile.default_provenance)
    # else: left unresolved (maturity_date missing) -- see comment above.

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
        settlement = advance_settlement_business_days(
            expiry, profile.settlement_business_days, profile
        ).isoformat()
        _resolve_field(PATH_FORWARD_SETTLEMENT_DATE, settlement, PROVENANCE_SHIORI_DERIVED)
        _resolve_field(PATH_OPTION_SETTLEMENT_DATE, settlement, PROVENANCE_SHIORI_DERIVED)

    return BLIBondAdvancedFieldProfile(
        supported=True,
        convention_profile=profile.name,
        rejection_reasons=(),
        fields=tuple(fields),
        pending_field_paths=tuple(pending),
        unresolved_fields=tuple(blocked),
    )


def _unsupported(convention_profile: str, reasons: tuple[str, ...]) -> BLIBondAdvancedFieldProfile:
    """Return the fail-closed result: no fields, and no Advanced route out."""

    return BLIBondAdvancedFieldProfile(
        supported=False,
        convention_profile=convention_profile,
        rejection_reasons=reasons,
        fields=(),
        pending_field_paths=(),
        unresolved_fields=(),
    )
