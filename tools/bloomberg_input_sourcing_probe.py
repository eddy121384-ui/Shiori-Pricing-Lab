"""One-command Bloomberg evidence probe for Shiori's remaining non-trading inputs (Issue #149).

Bounded, read-only diagnostic CLI -- **not** part of the production pricing
or workbench path, and never imported by either. One command::

    python tools/bloomberg_input_sourcing_probe.py

probes the default evidence pair (a supported US Treasury,
``US91282CLJ89``, and a conventional UK Gilt, ``GB00BFX0ZL78`` -- known
verification cases, not a hard-coded product scope), then writes a redacted
Markdown summary and a deterministic JSON result to one clearly printed
local directory. Eddy writes no JSON, supplies no Bloomberg mnemonic, and
runs no per-security command.

**What this script decides and what it does not.** For every remaining
non-trading input it reports one *disposition* --  ``BLOOMBERG_AUTO``,
``SHIORI_DERIVED_CANDIDATE``, ``APPROVED_PROFILE_REQUIRED``,
``ADVANCED_OVERRIDE_REQUIRED``, ``UNRESOLVED`` or
``NOT_REQUIRED_FOR_STANDALONE`` -- plus a main-screen recommendation. A
disposition is computed from two things only: the *declared* audit
conclusions already accepted in Issue #145 (recorded verbatim in the
catalogue below, never re-derived here), and the *actual* per-field
Bloomberg outcome of this run. Nothing is promoted into a production
mapping, schema, default or pricing behavior by this script: a candidate
mnemonic reaches ``BLOOMBERG_AUTO`` only if every probed security returned
it *and* the catalogue already records that its value type is unambiguous
enough to be a Shiori typed value. Every candidate whose unit, side,
override set or role is unconfirmed is classified ``DISPLAY_ONLY`` even
when Bloomberg returns a number -- "Bloomberg returned something" is never
by itself evidence that a mapping is safe.

**Candidate mnemonics are candidates.** Each candidate carries its own
provenance: ``PR141_CONFIRMED_DISPLAY_ONLY`` / ``PR141_BAD_FLD`` (real DAPI
evidence already recorded in this repo),
``NAMED_IN_ISSUE`` (named by Eddy in #145/#149), or ``PROBE_PROPOSED`` (a
name this probe proposes so the run can prove or disprove it -- not
evidence of anything until this probe answers). A ``BAD_FLD`` result for a
``PROBE_PROPOSED`` name is a useful, honest outcome, not a failure.

**Overrides are discovered, never guessed, and never homework.** The same
single run first asks ``//blp/apiflds`` (``FieldInfoRequest``) what each
option-context field publishes, so the override field ids
``OPT_UNDL_FORWARD_PX`` / ``PRICE_VOL`` / ``EQUIVALENT_PRICE_VOL`` demand
come from Bloomberg itself rather than from a human reading documentation
and re-running with a second command. It then sends those override field
ids straight back to ``//blp/apiflds`` in a second request, so the report
also carries each override's own mnemonic, description, datatype,
documentation excerpt and status -- e.g. what ``OP046``, ``OP188`` and
``OP131`` actually are. That metadata is evidence, not a role: naming an
override does not establish which Shiori option-context value it takes, so
an override whose role Eddy has not confirmed stays
``OVERRIDE_ROLE_UNRESOLVED`` with its metadata recorded beside it.

Values for the overrides whose *meaning* is confirmed come from the repo's
own committed synthetic option case
(``examples/standalone_option_case.json``) via ``APPROVED_OVERRIDE_ROLES``;
one entry there is the entire change needed to activate an override, and
until it exists the override is reported ``OVERRIDE_ROLE_UNRESOLVED``
rather than handed back to the user. ``--override FIELD=VALUE`` stays as an
advanced escape hatch for an override whose role is *not* confirmed, and is
never required for a normal run. It cannot replace a confirmed override's
value: naming ``OP046`` or ``OP188`` with anything other than the approved
value is rejected before any request is sent.

**Acquisition timestamps.** Every DAPI request -- the metadata discovery
request and each per-security value request -- records its own
``requested_at``/``received_at`` window on this probe's UTC clock, separate
from the report's own generation time. That is probe-side acquisition
timing; Bloomberg's own as-of timestamp for a value remains a separate,
unconfirmed question and is reported as such.

**Redaction.** Override *values* never appear anywhere -- not in the
Markdown, the JSON, or the console: an override is recorded as its field
id, where the value came from, and a shape descriptor of the value's nature
(numeric shape, date format, timestamp, text length), which is what a
reviewer needs to audit request semantics. Market levels (forward price,
volatility, discounting) are
recorded as a value *shape* (``<redacted numeric: positive, 3 integer
digits, 4 decimal places>``), never the number, so a result can be pasted
into a GitHub issue. Static/reference tokens whose exact text is the point
of the investigation (a day-count description, a maturity type, a date) are
recorded verbatim but whitespace-collapsed and length-capped. No
credential, terminal identifier, host, user or raw response payload is read
or emitted.

**Failure isolation.** One field, one entitlement, one security or one
whole request may fail without stopping the rest: a request-level failure
marks only that security's own request group ``RESPONSE_ERROR`` and the run
continues. Only a missing ``blpapi`` (nothing can be probed at all) stops
the run.

**Reuse.** All DAPI session, request and response-envelope handling is
``tools/bloomberg_dapi_probe`` (``probe_fields`` for values,
``describe_fields`` for metadata, both over one shared session helper) --
this script adds no second session implementation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from bloomberg_dapi_probe import (
    FieldDescription,
    ProbeFieldResult,
    describe_fields,
    probe_fields,
)

from shiori_pricing_lab.data.bloomberg_bond_quote import parse_bond_identifier

DEFAULT_IDENTIFIERS = ("US91282CLJ89", "GB00BFX0ZL78")
DEFAULT_OUTPUT_DIRNAME = "shiori_probe_output"
MARKDOWN_FILENAME = "bloomberg_input_sourcing_probe.md"
JSON_FILENAME = "bloomberg_input_sourcing_probe.json"

# --- vocabularies (Issue #149) -------------------------------------------------

CONFIRMED = "CONFIRMED"
DISPLAY_ONLY = "DISPLAY_ONLY"
BAD_FLD = "BAD_FLD"
RESPONSE_ERROR = "RESPONSE_ERROR"
ENTITLEMENT_BLOCKED = "ENTITLEMENT_BLOCKED"
UNMAPPED = "UNMAPPED"

BLOOMBERG_AUTO = "BLOOMBERG_AUTO"
SHIORI_DERIVED_CANDIDATE = "SHIORI_DERIVED_CANDIDATE"
APPROVED_PROFILE_REQUIRED = "APPROVED_PROFILE_REQUIRED"
ADVANCED_OVERRIDE_REQUIRED = "ADVANCED_OVERRIDE_REQUIRED"
UNRESOLVED = "UNRESOLVED"
NOT_REQUIRED_FOR_STANDALONE = "NOT_REQUIRED_FOR_STANDALONE"

REMOVE_FROM_MAIN_SCREEN = "REMOVE_FROM_MAIN_SCREEN"
AUTO_SOURCED_READ_ONLY = "AUTO_SOURCED_READ_ONLY"
MOVE_TO_ADVANCED = "MOVE_TO_ADVANCED"
KEEP_AS_TRADE_INPUT = "KEEP_AS_TRADE_INPUT"
BLOCK_WITH_UNRESOLVED_MESSAGE = "BLOCK_WITH_UNRESOLVED_MESSAGE"

# Disposition -> main-screen recommendation. A row may override this
# explicitly (`declared_recommendation`) where the honest recommendation is
# not the mechanical one -- e.g. an input whose *automatic sourcing* is
# UNRESOLVED but which must still stay enterable today.
_RECOMMENDATION_BY_DISPOSITION = {
    BLOOMBERG_AUTO: AUTO_SOURCED_READ_ONLY,
    SHIORI_DERIVED_CANDIDATE: AUTO_SOURCED_READ_ONLY,
    APPROVED_PROFILE_REQUIRED: AUTO_SOURCED_READ_ONLY,
    ADVANCED_OVERRIDE_REQUIRED: MOVE_TO_ADVANCED,
    UNRESOLVED: BLOCK_WITH_UNRESOLVED_MESSAGE,
    NOT_REQUIRED_FOR_STANDALONE: REMOVE_FROM_MAIN_SCREEN,
}

# Candidate mnemonic provenance.
PR141_CONFIRMED_DISPLAY_ONLY = "PR141_CONFIRMED_DISPLAY_ONLY"
PR141_BAD_FLD = "PR141_BAD_FLD"
NAMED_IN_ISSUE = "NAMED_IN_ISSUE"
PROBE_PROPOSED = "PROBE_PROPOSED"

# Disclosure level -- how a returned value may appear in a committable report.
SEMANTIC = "SEMANTIC"
MARKET_LEVEL = "MARKET_LEVEL"

# Request groups: one DAPI request each, per security.
STATIC_GROUP = "static_reference"
OPTION_CONTEXT_GROUP = "option_context"

_MAX_SEMANTIC_CHARS = 64
_MAX_DETAIL_CHARS = 200
_MAX_DOCUMENTATION_CHARS = 400


@dataclass(frozen=True)
class Candidate:
    """One Bloomberg mnemonic probed for one Shiori input."""

    mnemonic: str
    provenance: str
    group: str
    disclosure: str
    # True only when a returned value's type, unit and role are unambiguous
    # enough to become a Shiori typed value without a further approved
    # mapping table, override set or side/basis decision. False keeps a
    # returned value DISPLAY_ONLY -- evidence, never an automatic mapping.
    typed_mapping_safe: bool
    note: str


@dataclass(frozen=True)
class InputRow:
    """One remaining non-trading input, its candidates and its declared fallback."""

    input_id: str
    section: str
    question: str
    candidates: tuple[Candidate, ...]
    fallback_disposition: str
    reason: str
    owner_approval_required: bool = False
    declared_disposition: str | None = None
    declared_recommendation: str | None = None


# --- catalogue -----------------------------------------------------------------
#
# Order is fixed and is the report's order. Every `reason` traces to an
# accepted conclusion in Issue #145 (or to this repo's own PR #141 DAPI
# evidence) -- no financial methodology is decided here.

SECTION_STATIC = "1. Static bond reference"
SECTION_TIMING = "2. Timing and settlement"
SECTION_FORWARD = "3. Forward clean price"
SECTION_VOL = "4. PRICE_VOL / benchmark premium"
SECTION_DISCOUNTING = "5. Option discounting"

INPUT_ROWS: tuple[InputRow, ...] = (
    InputRow(
        input_id="resolved_bond_reference_data.day_count",
        section=SECTION_STATIC,
        question="Can Day Count be sourced safely rather than typed by the trader?",
        candidates=(
            Candidate(
                mnemonic="DAY_CNT_DES",
                provenance=PR141_CONFIRMED_DISPLAY_ONLY,
                group=STATIC_GROUP,
                disclosure=SEMANTIC,
                typed_mapping_safe=False,
                note=(
                    "Returns a description string. #145 forbids mapping 'ACT/ACT' to "
                    "ACT_ACT_ISDA; a typed value needs an approved mapping table."
                ),
            ),
            Candidate(
                mnemonic="DAY_CNT",
                provenance=PROBE_PROPOSED,
                group=STATIC_GROUP,
                disclosure=SEMANTIC,
                typed_mapping_safe=False,
                note=(
                    "Proposed numeric/code companion to DAY_CNT_DES. A code still needs "
                    "an approved code-to-enum table before it is a typed value."
                ),
            ),
        ),
        fallback_disposition=APPROVED_PROFILE_REQUIRED,
        reason=(
            "#145 lists Day Count among the fields a narrow UST / conventional-Gilt "
            "profile may own. Bloomberg text or codes are evidence for that profile, "
            "not a substitute for it."
        ),
        owner_approval_required=True,
    ),
    InputRow(
        input_id="resolved_bond_reference_data.bond_type",
        section=SECTION_STATIC,
        question=(
            "Can fixed-coupon bullet UST / conventional Gilt eligibility be identified "
            "without coercing Bloomberg strings into Shiori enums?"
        ),
        candidates=(
            Candidate(
                mnemonic="MTY_TYP",
                provenance=PR141_CONFIRMED_DISPLAY_ONLY,
                group=STATIC_GROUP,
                disclosure=SEMANTIC,
                typed_mapping_safe=False,
                note=(
                    "#145 forbids inferring FIXED_COUPON_BULLET from 'NORMAL' or "
                    "'AT MATURITY' alone."
                ),
            ),
            Candidate(
                mnemonic="CALC_TYP_DES",
                provenance=PR141_CONFIRMED_DISPLAY_ONLY,
                group=STATIC_GROUP,
                disclosure=SEMANTIC,
                typed_mapping_safe=False,
                note=(
                    "#145 forbids inferring a yield/bond convention from "
                    "'STREET CONVENTION' or 'UK:BUMP/DMO METHOD'."
                ),
            ),
            Candidate(
                mnemonic="SECURITY_TYP",
                provenance=PROBE_PROPOSED,
                group=STATIC_GROUP,
                disclosure=SEMANTIC,
                typed_mapping_safe=False,
                note="Proposed instrument-classification candidate; classification text only.",
            ),
            Candidate(
                mnemonic="CPN_TYP",
                provenance=PROBE_PROPOSED,
                group=STATIC_GROUP,
                disclosure=SEMANTIC,
                typed_mapping_safe=False,
                note=(
                    "Proposed coupon-type candidate; would evidence 'fixed coupon', not "
                    "bullet redemption. Combined with the already-confirmed CALLABLE / "
                    "SINKABLE flags it is profile input, not a typed mapping."
                ),
            ),
        ),
        fallback_disposition=APPROVED_PROFILE_REQUIRED,
        reason=(
            "#145: supported-universe classification is profile-owned for UST and "
            "conventional Gilts; instruments outside the profile must stop clearly."
        ),
        owner_approval_required=True,
    ),
    InputRow(
        input_id="resolved_bond_reference_data.ex_dividend_days",
        section=SECTION_STATIC,
        question="Does Bloomberg expose ex-dividend days or the ex-dividend rule?",
        candidates=(
            Candidate(
                mnemonic="EX_DVD_DT",
                provenance=PROBE_PROPOSED,
                group=STATIC_GROUP,
                disclosure=SEMANTIC,
                typed_mapping_safe=False,
                note=(
                    "Proposed ex-dividend date candidate. A date is not the ex-dividend "
                    "*day count* the accrual path consumes; the relationship would need "
                    "its own approved rule."
                ),
            ),
            Candidate(
                mnemonic="EX_DVD_DAYS",
                provenance=PROBE_PROPOSED,
                group=STATIC_GROUP,
                disclosure=SEMANTIC,
                typed_mapping_safe=False,
                note=(
                    "Proposed ex-dividend day-count candidate. Even a returned integer "
                    "needs its calendar basis (business vs calendar days) confirmed."
                ),
            ),
        ),
        fallback_disposition=APPROVED_PROFILE_REQUIRED,
        reason=(
            "#145: ex-dividend treatment is profile-owned and UK Gilt ex-dividend days "
            "must never be defaulted to zero."
        ),
        owner_approval_required=True,
    ),
    InputRow(
        input_id="resolved_bond_reference_data.last_coupon_date",
        section=SECTION_STATIC,
        question="Is there a Bloomberg coupon-schedule route for Last / Previous coupon date?",
        candidates=(
            Candidate(
                mnemonic="PREV_CPN_DT",
                provenance=PROBE_PROPOSED,
                group=STATIC_GROUP,
                disclosure=SEMANTIC,
                typed_mapping_safe=False,
                note=(
                    "Proposed previous-coupon-date candidate. 'Previous' is relative to "
                    "Bloomberg's own as-of/settlement assumption, which is not the "
                    "option's forward settlement date -- the role must be confirmed "
                    "before it maps to last_coupon_date."
                ),
            ),
            Candidate(
                mnemonic="PENULTIMATE_COUPON_DATE",
                provenance=PR141_BAD_FLD,
                group=STATIC_GROUP,
                disclosure=SEMANTIC,
                typed_mapping_safe=False,
                note=(
                    "Already BAD_FLD against both test securities in PR #141; re-probed "
                    "only to re-confirm, never to re-enable."
                ),
            ),
        ),
        fallback_disposition=SHIORI_DERIVED_CANDIDATE,
        reason=(
            "#145: Last Coupon Date may be derived only by a reviewed regular-schedule "
            "generator that validates issue date, first coupon, frequency, maturity and "
            "EOM and rejects irregular/stub schedules -- never maturity minus one "
            "period. A bulk coupon-schedule DAPI route is not covered by this scalar "
            "ReferenceDataRequest probe and remains an open evidence step."
        ),
        owner_approval_required=True,
    ),
    InputRow(
        input_id="resolved_bond_reference_data.status",
        section=SECTION_STATIC,
        question="Can Active / Inactive security status be sourced?",
        candidates=(
            Candidate(
                mnemonic="SECURITY_STATUS",
                provenance=PROBE_PROPOSED,
                group=STATIC_GROUP,
                disclosure=SEMANTIC,
                typed_mapping_safe=False,
                note=(
                    "Proposed status candidate; a returned status string would still "
                    "need an approved status policy before it gates eligibility."
                ),
            ),
        ),
        fallback_disposition=APPROVED_PROFILE_REQUIRED,
        reason="#145: status policy is profile-owned, not inferred from a Bloomberg string.",
        owner_approval_required=True,
    ),
    InputRow(
        input_id="resolved_bond_reference_data.business_day_convention",
        section=SECTION_STATIC,
        question="Is this consumed by the reviewed PRICE / EUROPEAN / CASH route at all?",
        candidates=(),
        fallback_disposition=NOT_REQUIRED_FOR_STANDALONE,
        reason=(
            "#145 approved removal: the QuantLib adapter explicitly ignores it on this "
            "route. Nothing to source. Tracked by #146 / PR #147."
        ),
        declared_disposition=NOT_REQUIRED_FOR_STANDALONE,
    ),
    InputRow(
        input_id="resolved_bond_reference_data.redemption_amount",
        section=SECTION_STATIC,
        question="Is this consumed by the reviewed PRICE / EUROPEAN / CASH route at all?",
        candidates=(),
        fallback_disposition=NOT_REQUIRED_FOR_STANDALONE,
        reason=(
            "#145 approved removal: not read by this option pricing path, and #145 "
            "forbids a global default of 100. REDEMPTION_VALUE is already BAD_FLD "
            "(PR #141), so removal -- not sourcing -- is the answer."
        ),
        declared_disposition=NOT_REQUIRED_FOR_STANDALONE,
    ),
    InputRow(
        input_id="resolved_bond_reference_data.yield_convention",
        section=SECTION_STATIC,
        question="Is this consumed by the reviewed PRICE / EUROPEAN / CASH route at all?",
        candidates=(),
        fallback_disposition=NOT_REQUIRED_FOR_STANDALONE,
        reason=(
            "#145 approved removal: not consumed by price-based Black-76, and #145 "
            "forbids mapping 'STREET CONVENTION' / 'UK:BUMP/DMO METHOD' to it."
        ),
        declared_disposition=NOT_REQUIRED_FOR_STANDALONE,
    ),
    InputRow(
        input_id="bond_option.settlement_lag_days",
        section=SECTION_TIMING,
        question="Is settlement lag needed once explicit settlement dates are authoritative?",
        candidates=(),
        fallback_disposition=NOT_REQUIRED_FOR_STANDALONE,
        reason=(
            "#145 approved removal: the approved standalone timing contract does not "
            "derive or validate settlement dates from it. Tracked by #146 / PR #147."
        ),
        declared_disposition=NOT_REQUIRED_FOR_STANDALONE,
    ),
    InputRow(
        input_id="pricing_inputs.forward_settlement_date",
        section=SECTION_TIMING,
        question="Can the forward (bond delivery) settlement date be sourced or derived?",
        candidates=(
            Candidate(
                mnemonic="SETTLE_DT",
                provenance=PROBE_PROPOSED,
                group=STATIC_GROUP,
                disclosure=SEMANTIC,
                typed_mapping_safe=False,
                note=(
                    "Proposed standard-settlement candidate. Even if returned, its role "
                    "is spot/standard settlement for the cash bond -- NOT the option's "
                    "forward settlement date. Roles must not be conflated."
                ),
            ),
            Candidate(
                mnemonic="DAYS_TO_SETTLE",
                provenance=PROBE_PROPOSED,
                group=STATIC_GROUP,
                disclosure=SEMANTIC,
                typed_mapping_safe=False,
                note=(
                    "Proposed settlement-lag candidate; evidence for a future approved "
                    "calendar policy only. Rolling a date from it needs an approved "
                    "market calendar, which this repo does not have."
                ),
            ),
        ),
        fallback_disposition=ADVANCED_OVERRIDE_REQUIRED,
        reason=(
            "#145: explicit settlement dates remain authoritative unless an approved "
            "calendar/settlement policy replaces manual entry."
        ),
        owner_approval_required=True,
    ),
    InputRow(
        input_id="pricing_inputs.option_settlement_date",
        section=SECTION_TIMING,
        question="Can the option cash-settlement date be sourced?",
        candidates=(),
        fallback_disposition=ADVANCED_OVERRIDE_REQUIRED,
        reason=(
            "No Bloomberg reference field describes an OTC option's own cash settlement "
            "date for a bond the terminal does not know as an option; no candidate is "
            "proposed rather than guessed. #145 keeps explicit settlement dates "
            "authoritative, so this stays a reviewable Advanced input."
        ),
        declared_disposition=ADVANCED_OVERRIDE_REQUIRED,
        owner_approval_required=True,
    ),
    InputRow(
        input_id="bond_option.expiry_timestamp_offset",
        section=SECTION_TIMING,
        question="Can the expiry time-of-day / timezone be sourced or fixed by route policy?",
        candidates=(),
        fallback_disposition=UNRESOLVED,
        reason=(
            "Expiry is a genuine trade decision and Bloomberg reference data does not "
            "carry this option's expiry timestamp. #145 records only that expiry date "
            "and time should become one UI interaction preserving the offset-aware "
            "contract -- no default time-of-day or timezone convention is approved, and "
            "this probe will not invent one."
        ),
        declared_disposition=UNRESOLVED,
        declared_recommendation=KEEP_AS_TRADE_INPUT,
        owner_approval_required=True,
    ),
    InputRow(
        input_id="pricing_inputs.reporting_date",
        section=SECTION_TIMING,
        question="Can Reporting Date be fixed by the workbench route?",
        candidates=(),
        fallback_disposition=SHIORI_DERIVED_CANDIDATE,
        reason=(
            "#145: Reporting Date may be fixed to Valuation Date, but only after Eddy's "
            "explicit approval -- it is a route/methodology decision, not a data source."
        ),
        declared_disposition=SHIORI_DERIVED_CANDIDATE,
        owner_approval_required=True,
    ),
    InputRow(
        input_id="pricing_inputs.forward_clean_price",
        section=SECTION_FORWARD,
        question=(
            "Does OPT_UNDL_FORWARD_PX return a usable forward clean price, and with what "
            "overrides?"
        ),
        candidates=(
            Candidate(
                mnemonic="OPT_UNDL_FORWARD_PX",
                provenance=NAMED_IN_ISSUE,
                group=OPTION_CONTEXT_GROUP,
                disclosure=MARKET_LEVEL,
                typed_mapping_safe=False,
                note=(
                    "The run discovers this field's own documented overrides and sends the "
                    "ones whose meaning Eddy has confirmed: OP046 (pricing model, "
                    "documented bond-option value) and OP188 (Shiori's valuation date). "
                    "OP131 is the equity/index dividend yield and is never sent. A returned "
                    "number still stays DISPLAY_ONLY until quote side, unit and Bloomberg's "
                    "own as-of timestamp are separately confirmed."
                ),
            ),
        ),
        fallback_disposition=ADVANCED_OVERRIDE_REQUIRED,
        reason=(
            "The workbench already accepts an explicit forward clean price. Until this "
            "field's overrides, side and unit are confirmed, the forward stays a "
            "reviewable Advanced input; spot/repo forward reconstruction is explicitly "
            "out of scope for #149."
        ),
        owner_approval_required=True,
    ),
    InputRow(
        input_id="volatility_input.price_vol_direct",
        section=SECTION_VOL,
        question=(
            "Can a direct PRICE_VOL / EQUIVALENT_PRICE_VOL be sourced with basis, side and "
            "timestamp?"
        ),
        candidates=(
            Candidate(
                mnemonic="PRICE_VOL",
                provenance=NAMED_IN_ISSUE,
                group=OPTION_CONTEXT_GROUP,
                disclosure=MARKET_LEVEL,
                typed_mapping_safe=False,
                note=(
                    "Basis (price vs yield), unit (percent vs decimal), side and the "
                    "strike/expiry overrides it needs are all unconfirmed. #149 forbids "
                    "inferring the unit from a value's magnitude."
                ),
            ),
            Candidate(
                mnemonic="EQUIVALENT_PRICE_VOL",
                provenance=NAMED_IN_ISSUE,
                group=OPTION_CONTEXT_GROUP,
                disclosure=MARKET_LEVEL,
                typed_mapping_safe=False,
                note=(
                    "'Equivalent' implies a conversion whose source basis is exactly "
                    "what must not be assumed; a returned number is evidence only."
                ),
            ),
        ),
        fallback_disposition=UNRESOLVED,
        reason=(
            "#149 stop condition: unknown PRICE_VOL / EQUIVALENT_PRICE_VOL basis, unit "
            "or side must be reported unresolved, never guessed. YIELD_VOL must not be "
            "substituted."
        ),
        owner_approval_required=True,
    ),
    InputRow(
        input_id="benchmark_quote.premium_per_100",
        section=SECTION_VOL,
        question=(
            "Can a benchmark premium feed the existing deterministic implied PRICE_VOL "
            "solver?"
        ),
        candidates=(),
        fallback_disposition=ADVANCED_OVERRIDE_REQUIRED,
        reason=(
            "The consumer already exists and is deterministic "
            "(pricing/bli_implied_price_vol_solver.py, "
            "pricing/bli_implied_price_vol_calibration.py), so a sourced premium would "
            "need no new methodology. The *source* does not: docs/"
            "bloomberg_ovme_source_mapping.md records the OVME per-unit / total premium "
            "semantics as manually observed UI evidence with no API mnemonic, and "
            "explicitly forbids inferring API fields from UI labels. It therefore stays "
            "a manually entered Advanced input feeding the existing solver."
        ),
        declared_disposition=ADVANCED_OVERRIDE_REQUIRED,
        owner_approval_required=True,
    ),
    InputRow(
        input_id="curve_points[OPTION_DISCOUNT_CURVE]",
        section=SECTION_DISCOUNTING,
        question=(
            "Is there a provable continuous-zero-rate source, or a simpler route-specific "
            "discounting input?"
        ),
        candidates=(),
        fallback_disposition=UNRESOLVED,
        reason=(
            "No candidate is probed, deliberately. #145 and "
            "docs/bloomberg_ovme_source_mapping.md forbid relabelling MMkt, repo, FTP, "
            "par, swap or generic yield rates as continuous zero rates, and record the "
            "SWDF S490 stripped zero-rate route as PARTIALLY_CONFIRMED with unverified "
            "compounding, interpolation and date treatment. The reviewed engine consumes "
            "discount factors only for Reporting Date and Option Settlement Date, so an "
            "explicit route-specific discount factor / zero rate may be a smaller "
            "contract than a full editable curve -- but that is a contract and "
            "methodology decision for Eddy, not evidence this probe can produce."
        ),
        declared_disposition=UNRESOLVED,
        declared_recommendation=MOVE_TO_ADVANCED,
        owner_approval_required=True,
    ),
)

# Routes that must never be used as a substitute, restated in every report so
# a reader of the output alone cannot mistake an unresolved row for licence to
# improvise. Verbatim from #145 / docs/bloomberg_ovme_source_mapping.md.
PROHIBITED_ROUTES: tuple[str, ...] = (
    "Do not treat YIELD_VOL (normal or lognormal) as PRICE_VOL.",
    "Do not infer a volatility unit or basis from a value's magnitude.",
    "Do not relabel MMkt, repo, FTP, par, swap or generic yield rates as continuous zero rates.",
    "Do not map Bloomberg 'ACT/ACT' to ACT_ACT_ISDA.",
    "Do not map 'NORMAL' or 'AT MATURITY' to FIXED_COUPON_BULLET.",
    "Do not map 'STREET CONVENTION' or 'UK:BUMP/DMO METHOD' to a Shiori yield convention.",
    "Do not default redemption to 100 globally.",
    "Do not default UK Gilt ex-dividend days to zero.",
    "Do not derive Last Coupon Date by maturity minus one period.",
    "Do not promote any candidate here into a production mapping without Eddy's approval.",
)


# --- evidence collection -------------------------------------------------------


@dataclass(frozen=True)
class AppliedOverride:
    """One override actually sent, and where its value came from."""

    field: str
    value: str  # never rendered -- only `redact_override_value` output is
    source: str  # "user_supplied" | "fixture:<role>"


@dataclass(frozen=True)
class GroupEvidence:
    """Outcome of one DAPI request (one security, one override set)."""

    group: str
    mnemonics: tuple[str, ...]
    overrides: tuple[AppliedOverride, ...]
    results: dict[str, ProbeFieldResult]
    error: str | None
    requested_at: str
    received_at: str


@dataclass(frozen=True)
class SecurityEvidence:
    identifier: str
    qualified_identifier: str
    groups: tuple[GroupEvidence, ...]


@dataclass(frozen=True)
class OverridePlanEntry:
    """One override Bloomberg documents for an option-context field, and its status."""

    override_field: str
    required_by: tuple[str, ...]
    status: str  # OVERRIDE_APPLIED | OVERRIDE_ROLE_UNRESOLVED | OVERRIDE_FIXTURE_MISSING
    role: str | None
    source: str | None
    note: str
    # What Bloomberg publishes about this override field id itself, from
    # discovery's second pass. Recorded as evidence; it never decides a role.
    metadata: FieldDescription | None = None


@dataclass(frozen=True)
class DiscoveryEvidence:
    """Outcome of the two ``//blp/apiflds`` field-metadata requests.

    First pass: what the option-context value fields publish (including the
    override field ids they demand). Second pass: what Bloomberg publishes
    about *those override field ids themselves* -- e.g. once
    ``OPT_UNDL_FORWARD_PX`` reports ``OP046``/``OP188``/``OP131``, the same
    run asks what ``OP046`` is. Both passes are metadata only: they record
    what Bloomberg says, and never make an override's role or meaning
    follow from it.
    """

    fields: tuple[str, ...]
    descriptions: tuple[FieldDescription, ...]
    error: str | None
    requested_at: str
    received_at: str
    override_fields: tuple[str, ...] = ()
    override_descriptions: tuple[FieldDescription, ...] = ()
    override_error: str | None = None
    override_requested_at: str | None = None
    override_received_at: str | None = None


OVERRIDE_APPLIED = "OVERRIDE_APPLIED"
OVERRIDE_ROLE_UNRESOLVED = "OVERRIDE_ROLE_UNRESOLVED"
OVERRIDE_FIXTURE_MISSING = "OVERRIDE_FIXTURE_MISSING"
OVERRIDE_VALUE_UNCONFIRMED = "OVERRIDE_VALUE_UNCONFIRMED"
OVERRIDE_NOT_APPLICABLE = "OVERRIDE_NOT_APPLICABLE"
_SCRUBBED_OVERRIDE_VALUE = "<redacted override value>"

# Structural patterns scrubbed out of any external text before it is stored.
# Bloomberg's own error text can carry connection and session detail as well
# as a quoted request, and none of it belongs in a committable report.
_HOST_PORT_PATTERN = re.compile(r"\b[A-Za-z0-9_.-]+:\d{2,5}\b")
_PATH_PATTERN = re.compile(
    r"(?:[A-Za-z]:\\[^\s\"']+|/(?:home|Users|users|root|var|tmp|opt)/[^\s\"']+)"
)
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)\b(uuid|user(?:name)?|terminal|serial|sid|session|token|password|passwd|auth|"
    r"credential|api[_-]?key)\s*[=:]\s*\S+"
)

# The option context this probe sends when a discovered override's role is
# approved: the repo's own committed, synthetic standalone-option case. No
# real trade, position or market level is involved, and the values are
# redacted in every output anyway.
FIXTURE_CASE_PATH = Path(__file__).resolve().parents[1] / "examples" / "standalone_option_case.json"

# Which JSON path in that case supplies each option-context role. Roles are
# Shiori's own concepts, named here once so an approved override maps to a
# value without anyone re-typing it.
_FIXTURE_ROLE_PATHS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("strike_price", ("bond_option", "strike_price")),
    ("expiry_date", ("bond_option", "expiry_date")),
    ("option_type", ("bond_option", "option_type")),
    ("exercise_style", ("bond_option", "exercise_style")),
    ("settlement_type", ("bond_option", "settlement_type")),
    ("currency", ("bond_option", "currency")),
    ("notional", ("bond_option", "notional")),
    ("expiry_timestamp", ("expiry_timestamp",)),
    ("forward_settlement_date", ("forward_settlement_date",)),
    ("option_settlement_date", ("option_settlement_date",)),
    ("valuation_date", ("valuation_date",)),
)

# Roles whose value is a fixed documented constant rather than a value read
# out of the option case (which carries a trade's own terms, not a model
# choice).
_FIXED_ROLE_NAMES = ("pricing_model",)

# The exact override value for each fixed role, recorded verbatim from
# Bloomberg's own documentation -- never guessed, never inferred from a
# label. `pricing_model` is `"Black"`: Bloomberg's documentation lists that
# value for a bond option, and Eddy confirmed it on the workstation. A fixed
# role with no recorded token here is reported OVERRIDE_VALUE_UNCONFIRMED
# and simply not sent, while the rest of the run still proceeds.
FIXED_ROLE_VALUES: dict[str, str] = {
    "pricing_model": "Black",
}

# Overrides confirmed on the workstation as *not* belonging on a bond-option
# request, with the reason. Checked before APPROVED_OVERRIDE_ROLES and before
# any --override, so one of these can never be sent by a later role entry or
# by a command-line argument.
NEVER_MAPPED_OVERRIDES: dict[str, str] = {
    "OP131": (
        "Confirmed on the Bloomberg workstation as the equity / index dividend yield "
        "override. It is not a bond-option input, is mapped to no Shiori role, and is "
        "never sent with these requests."
    ),
}

# Bloomberg override field id -> option-context role. An entry appears here
# only after Eddy confirms that override's meaning on the workstation --
# what an override *means* is a semantic question #149 forbids guessing, and
# the run's own metadata discovery (see `describe_fields`) records what
# Bloomberg publishes without ever deciding a role. Once an entry exists,
# every later ordinary run sends that override automatically with its value
# from the option case or from FIXED_ROLE_VALUES; an override with no entry
# is reported OVERRIDE_ROLE_UNRESOLVED, never handed to the user as a lookup
# task.
#
# Workstation-confirmed (Issue #149):
# - OP046 is the pricing model; Bloomberg's documentation lists "Black" as
#   its bond-option value (see FIXED_ROLE_VALUES).
# - OP188 is the valuation date, which is Shiori's own valuation date.
APPROVED_OVERRIDE_ROLES: dict[str, str] = {
    "OP046": "pricing_model",
    "OP188": "valuation_date",
}


def _utc_now() -> str:
    # Milliseconds, so a fast request still shows a real acquisition window
    # rather than one timestamp twice.
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def load_option_context_fixture(path: Path | None = None) -> dict[str, str]:
    """Read the committed synthetic option case into ``role -> value`` strings.

    A missing or unreadable fixture is not an error: it yields an empty
    context, every approved override is reported ``OVERRIDE_FIXTURE_MISSING``
    and the rest of the run continues.
    """

    fixture_path = path or FIXTURE_CASE_PATH
    try:
        case = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

    context: dict[str, str] = {}
    for role, json_path in _FIXTURE_ROLE_PATHS:
        value = case
        for key in json_path:
            if not isinstance(value, dict) or key not in value:
                value = None
                break
            value = value[key]
        if value is not None and not isinstance(value, (dict, list)):
            context[role] = str(value)
    return context


def sanitize_external_text(
    text: str | None, overrides: tuple[AppliedOverride, ...] = ()
) -> str | None:
    """Sanitize Bloomberg-authored text before this run stores or prints it.

    Every external string that reaches the console, the Markdown or the JSON
    goes through here first, so the no-leak guarantee holds on error paths
    exactly as it does on success paths. Removed, in order:

    1. every override value this run actually sent (Bloomberg can quote the
       request back in a rejection);
    2. host:port pairs;
    3. filesystem paths;
    4. ``key=value`` pairs whose key names a terminal, session, user,
       serial, token or credential.

    The failure itself stays legible -- field ids, error categories and
    surrounding wording are untouched. Best-effort by design for (1): it
    removes the exact value sent and its stripped form, and does not claim
    to catch a value Bloomberg reformatted.
    """

    if not text:
        return text
    sanitized = str(text)
    variants = {
        variant
        for item in overrides
        for variant in (item.value, str(item.value).strip())
        if variant
    }
    # Token-boundary matching, so a value of any length -- including a
    # one-character one such as `--override OP999=1` -- is removed where
    # Bloomberg quotes it back, without mangling an unrelated substring
    # (a "1" inside "8194", a "Black" inside "Blackrock").
    for variant in sorted(variants, key=len, reverse=True):
        pattern = re.compile(rf"(?<![\w.-]){re.escape(variant)}(?![\w.-])")
        sanitized = pattern.sub(_SCRUBBED_OVERRIDE_VALUE, sanitized)
    sanitized = _SENSITIVE_KEY_PATTERN.sub(r"\1=<redacted>", sanitized)
    sanitized = _PATH_PATTERN.sub("<redacted path>", sanitized)
    sanitized = _HOST_PORT_PATTERN.sub("<redacted host>", sanitized)
    return sanitized


def _group_mnemonics(group: str) -> tuple[str, ...]:
    """Every catalogued mnemonic for one request group, in catalogue order, deduplicated."""

    ordered: list[str] = []
    for row in INPUT_ROWS:
        for candidate in row.candidates:
            if candidate.group == group and candidate.mnemonic not in ordered:
                ordered.append(candidate.mnemonic)
    return tuple(ordered)


def _sanitized_description(description: FieldDescription) -> FieldDescription:
    """Sanitize the Bloomberg-authored text on one field description.

    ``//blp/apiflds`` can answer with a per-field ``fieldError`` rather than
    raising, so this path carries external text just like the two exception
    handlers do -- and its description/documentation are Bloomberg-authored
    as well. All three go through :func:`sanitize_external_text` here, at the
    boundary, so nothing downstream has to remember to.
    """

    return replace(
        description,
        description=sanitize_external_text(description.description),
        documentation=sanitize_external_text(description.documentation),
        detail=sanitize_external_text(description.detail),
    )


def discover_option_context_metadata(
    describe: Callable[..., list[FieldDescription]] | None = None,
    clock: Callable[[], str] | None = None,
) -> DiscoveryEvidence:
    """Ask Bloomberg what the option-context fields document, inside the same run.

    This is what keeps the normal flow one command: the override field ids
    an option-context field demands come from ``//blp/apiflds``, not from a
    human reading Bloomberg documentation and re-running with ``--override``.

    Two passes, both inside this one call. The first asks about the
    option-context value fields. Whatever override field ids they report --
    e.g. ``OP046``, ``OP188``, ``OP131`` for ``OPT_UNDL_FORWARD_PX`` -- are
    then themselves sent back to ``//blp/apiflds`` in a second request, so
    the run also records each override's own mnemonic, description,
    datatype, documentation and status. That is the last step a machine can
    take honestly: recording what Bloomberg publishes about an override is
    not the same as knowing which Shiori role it fills, so the metadata is
    reported and the role stays ``OVERRIDE_ROLE_UNRESOLVED`` until Eddy
    confirms it.

    Either pass may fail without stopping the other or the value probes, and
    either failure's text goes through :func:`sanitize_external_text` before
    it is recorded -- no override sent in these requests, but a connection
    failure can still carry host, path or session detail.
    """

    describe = describe or describe_fields
    clock = clock or _utc_now
    fields = _group_mnemonics(OPTION_CONTEXT_GROUP)
    requested_at = clock()
    if not fields:
        return DiscoveryEvidence(
            fields=(),
            descriptions=(),
            error=None,
            requested_at=requested_at,
            received_at=clock(),
        )
    try:
        descriptions = tuple(_sanitized_description(item) for item in describe(list(fields)))
    except RuntimeError as exc:
        return DiscoveryEvidence(
            fields=fields,
            descriptions=(),
            error=_collapse(sanitize_external_text(str(exc)), _MAX_DETAIL_CHARS),
            requested_at=requested_at,
            received_at=clock(),
        )
    received_at = clock()

    override_fields: list[str] = []
    for description in descriptions:
        for override_field in description.overrides:
            if override_field not in override_fields:
                override_fields.append(override_field)
    if not override_fields:
        return DiscoveryEvidence(
            fields=fields,
            descriptions=descriptions,
            error=None,
            requested_at=requested_at,
            received_at=received_at,
        )

    override_requested_at = clock()
    try:
        override_descriptions = tuple(
            _sanitized_description(item) for item in describe(list(override_fields))
        )
    except RuntimeError as exc:
        return DiscoveryEvidence(
            fields=fields,
            descriptions=descriptions,
            error=None,
            requested_at=requested_at,
            received_at=received_at,
            override_fields=tuple(override_fields),
            override_descriptions=(),
            override_error=_collapse(sanitize_external_text(str(exc)), _MAX_DETAIL_CHARS),
            override_requested_at=override_requested_at,
            override_received_at=clock(),
        )
    return DiscoveryEvidence(
        fields=fields,
        descriptions=descriptions,
        error=None,
        requested_at=requested_at,
        received_at=received_at,
        override_fields=tuple(override_fields),
        override_descriptions=override_descriptions,
        override_error=None,
        override_requested_at=override_requested_at,
        override_received_at=clock(),
    )


def plan_overrides(
    discovery: DiscoveryEvidence,
    context: dict[str, str],
    user_overrides: tuple[tuple[str, str], ...],
) -> tuple[tuple[OverridePlanEntry, ...], tuple[AppliedOverride, ...]]:
    """Turn discovered override ids plus the fixture into the overrides actually sent.

    Deterministic order: discovered overrides in discovery order first, then
    any ``--override`` the user supplied that a discovered entry did not
    already cover. Nothing is guessed -- an override whose role is not in
    ``APPROVED_OVERRIDE_ROLES`` is planned as ``OVERRIDE_ROLE_UNRESOLVED``
    and simply not sent.
    """

    metadata_by_field = {
        description.field: description for description in discovery.override_descriptions
    }
    required_by: dict[str, list[str]] = {}
    for description in discovery.descriptions:
        for override_field in description.overrides:
            required_by.setdefault(override_field, []).append(description.field)

    user_supplied = dict(user_overrides)
    plan: list[OverridePlanEntry] = []
    applied: list[AppliedOverride] = []

    for override_field, fields in required_by.items():
        if override_field in NEVER_MAPPED_OVERRIDES:
            plan.append(
                OverridePlanEntry(
                    override_field=override_field,
                    required_by=tuple(fields),
                    status=OVERRIDE_NOT_APPLICABLE,
                    role=None,
                    source=None,
                    note=NEVER_MAPPED_OVERRIDES[override_field]
                    + (
                        " A --override for it was ignored."
                        if override_field in user_supplied
                        else ""
                    ),
                    metadata=metadata_by_field.get(override_field),
                )
            )
            continue
        role = APPROVED_OVERRIDE_ROLES.get(override_field)
        if override_field in user_supplied and role is None:
            applied.append(
                AppliedOverride(
                    field=override_field,
                    value=user_supplied[override_field],
                    source="user_supplied",
                )
            )
            plan.append(
                OverridePlanEntry(
                    override_field=override_field,
                    required_by=tuple(fields),
                    status=OVERRIDE_APPLIED,
                    role=None,
                    source="user_supplied",
                    note="Sent verbatim from --override; its value is redacted in every output.",
                    metadata=metadata_by_field.get(override_field),
                )
            )
            continue
        if role is None:
            metadata = metadata_by_field.get(override_field)
            if metadata is not None and metadata.status == "described":
                evidence_note = (
                    "Bloomberg's own published metadata for this override id was retrieved "
                    "and is recorded in the override field metadata table. It names and "
                    "describes the field but does not "
                    "by itself establish which Shiori option-context role it fills, so the "
                    "role stays unresolved rather than being inferred from the text."
                )
            elif metadata is not None:
                evidence_note = (
                    f"The metadata request for this override id came back {metadata.status!r}, "
                    "so Bloomberg published nothing usable about it here."
                )
            else:
                evidence_note = (
                    "No metadata was retrieved for this override id in this run "
                    "(see the discovery error above)."
                )
            plan.append(
                OverridePlanEntry(
                    override_field=override_field,
                    required_by=tuple(fields),
                    status=OVERRIDE_ROLE_UNRESOLVED,
                    role=None,
                    source=None,
                    note=(
                        "Bloomberg documents this override for the field(s) above, but its "
                        f"meaning is not confirmed, so nothing was sent. {evidence_note} "
                        "Record the role once in APPROVED_OVERRIDE_ROLES and the next "
                        "ordinary run sends it automatically from the committed synthetic "
                        "option case -- no manual mnemonic lookup and no extra command."
                    ),
                    metadata=metadata,
                )
            )
            continue
        is_fixed_role = role in _FIXED_ROLE_NAMES
        if role not in context:
            plan.append(
                OverridePlanEntry(
                    override_field=override_field,
                    required_by=tuple(fields),
                    status=(
                        OVERRIDE_VALUE_UNCONFIRMED if is_fixed_role else OVERRIDE_FIXTURE_MISSING
                    ),
                    role=role,
                    source=None,
                    note=(
                        (
                            f"Role {role!r} is confirmed for this override, but its exact "
                            "Bloomberg value is not recorded in FIXED_ROLE_VALUES yet, so "
                            "nothing was sent. The token must be transcribed from Bloomberg's "
                            "own documentation -- this probe will not guess it."
                        )
                        if is_fixed_role
                        else (
                            f"Role {role!r} is approved for this override but the option-context "
                            "fixture supplied no value for it, so nothing was sent."
                        )
                    ),
                    metadata=metadata_by_field.get(override_field),
                )
            )
            continue
        source = f"{'fixed' if is_fixed_role else 'fixture'}:{role}"
        applied.append(
            AppliedOverride(field=override_field, value=context[role], source=source)
        )
        applied_note = (
            "Applied automatically from the documented fixed value for this role."
            if is_fixed_role
            else "Applied automatically from the committed synthetic option case."
        )
        if override_field in user_supplied:
            # A confirmed override's value is not replaceable: `main` rejects
            # the run outright, and a programmatic caller that gets here is
            # still held to the confirmed value rather than the CLI one.
            applied_note += (
                " A --override for this field was rejected: its value is fixed by the "
                f"confirmed {role!r} semantics."
            )
        plan.append(
            OverridePlanEntry(
                override_field=override_field,
                required_by=tuple(fields),
                status=OVERRIDE_APPLIED,
                role=role,
                source=source,
                note=applied_note,
                metadata=metadata_by_field.get(override_field),
            )
        )

    for override_field, value in user_overrides:
        if override_field in required_by:
            continue
        if override_field in APPROVED_OVERRIDE_ROLES:
            continue
        if override_field in NEVER_MAPPED_OVERRIDES:
            plan.append(
                OverridePlanEntry(
                    override_field=override_field,
                    required_by=(),
                    status=OVERRIDE_NOT_APPLICABLE,
                    role=None,
                    source=None,
                    note=(
                        NEVER_MAPPED_OVERRIDES[override_field]
                        + " A --override for it was ignored."
                    ),
                    metadata=metadata_by_field.get(override_field),
                )
            )
            continue
        applied.append(
            AppliedOverride(field=override_field, value=value, source="user_supplied")
        )
        plan.append(
            OverridePlanEntry(
                override_field=override_field,
                required_by=(),
                status=OVERRIDE_APPLIED,
                role=None,
                source="user_supplied",
                note=(
                    "Sent verbatim from --override; no probed field documented this override "
                    "in this run."
                ),
            )
        )

    return tuple(plan), tuple(applied)


def collect_evidence(
    identifiers: tuple[str, ...],
    overrides: tuple[AppliedOverride, ...],
    probe: Callable[..., list[ProbeFieldResult]] | None = None,
    clock: Callable[[], str] | None = None,
) -> tuple[SecurityEvidence, ...]:
    """Run one bounded DAPI request per (security, request group) and collect raw outcomes.

    External text from any request is sanitized against *every* override this
    run applied, not only the ones that request carried -- a value is
    sensitive wherever it is echoed.

    Every request records its own acquisition window (``requested_at`` /
    ``received_at``, this probe's UTC clock -- Bloomberg's own as-of
    timestamp is a separate, still-unconfirmed question). A ``RuntimeError``
    from one request is recorded against that request group only -- every
    other group and every other security still runs. ``ImportError`` (no
    ``blpapi`` at all) propagates: nothing can be probed.
    """

    probe = probe or probe_fields
    clock = clock or _utc_now
    evidence: list[SecurityEvidence] = []
    for identifier in identifiers:
        _, qualified = parse_bond_identifier(identifier)
        groups: list[GroupEvidence] = []
        for group in (STATIC_GROUP, OPTION_CONTEXT_GROUP):
            mnemonics = _group_mnemonics(group)
            group_overrides = overrides if group == OPTION_CONTEXT_GROUP else ()
            if not mnemonics:
                continue
            requested_at = clock()
            try:
                results = probe(
                    qualified,
                    list(mnemonics),
                    overrides=[(item.field, item.value) for item in group_overrides],
                )
            except RuntimeError as exc:
                groups.append(
                    GroupEvidence(
                        group=group,
                        mnemonics=mnemonics,
                        overrides=group_overrides,
                        results={},
                        error=_collapse(
                            sanitize_external_text(str(exc), overrides),
                            _MAX_DETAIL_CHARS,
                        ),
                        requested_at=requested_at,
                        received_at=clock(),
                    )
                )
                continue
            groups.append(
                GroupEvidence(
                    group=group,
                    mnemonics=mnemonics,
                    overrides=group_overrides,
                    results={
                        result.field: replace(
                            result,
                            detail=sanitize_external_text(result.detail, overrides),
                        )
                        for result in results
                    },
                    error=None,
                    requested_at=requested_at,
                    received_at=clock(),
                )
            )
        evidence.append(
            SecurityEvidence(
                identifier=identifier,
                qualified_identifier=qualified,
                groups=tuple(groups),
            )
        )
    return tuple(evidence)


# --- classification and redaction ----------------------------------------------


def _collapse(text: str, limit: int) -> str:
    collapsed = " ".join(str(text).split())
    if len(collapsed) > limit:
        return collapsed[:limit] + " <truncated>"
    return collapsed


def redact(value: str, disclosure: str) -> str:
    """Redact one returned Bloomberg value for a committable report.

    ``MARKET_LEVEL`` values (forward price, volatility, discounting) are
    reduced to a value *shape* and never carry the number itself.
    ``SEMANTIC`` values -- the classification strings and dates whose exact
    text is the object of the investigation -- are whitespace-collapsed and
    length-capped, never reformatted or parsed.
    """

    if disclosure != MARKET_LEVEL:
        return _collapse(value, _MAX_SEMANTIC_CHARS)

    stripped = str(value).strip()
    try:
        number = float(stripped)
    except ValueError:
        return f"<redacted non-numeric: {len(stripped)} chars>"
    sign = "negative" if number < 0 else "positive" if number > 0 else "zero"
    digits = stripped.lstrip("+-")
    integer_part, _, decimal_part = digits.partition(".")
    return (
        f"<redacted numeric: {sign}, {len(integer_part)} integer digits, "
        f"{len(decimal_part)} decimal places>"
    )


def redact_override_value(value: str) -> str:
    """Redact one override *value* while keeping the request auditable.

    An override field id is a mnemonic and is always shown; its value can be
    a strike, an expiry or any other request payload, so only its nature and
    shape are recorded -- enough to review what the request meant, never the
    value itself.
    """

    stripped = str(value).strip()
    try:
        number = float(stripped)
    except ValueError:
        number = None
    if number is not None:
        sign = "negative" if number < 0 else "positive" if number > 0 else "zero"
        digits = stripped.lstrip("+-")
        integer_part, _, decimal_part = digits.partition(".")
        return (
            f"<redacted numeric: {sign}, {len(integer_part)} integer digits, "
            f"{len(decimal_part)} decimal places>"
        )
    # Numeric first: an all-digit value is reported by shape rather than
    # guessed to be a compact date, which would be a semantic claim.
    compact = "".join(stripped.split())
    if len(compact) == 10 and compact[4] == "-" and compact[7] == "-":
        return "<redacted date: ISO YYYY-MM-DD>"
    if "T" in compact and compact[:4].isdigit():
        return "<redacted timestamp: ISO 8601>"
    return f"<redacted text: {len(compact)} chars>"


def classify(
    candidate: Candidate,
    result: ProbeFieldResult | None,
    request_error: str | None,
) -> tuple[str, str]:
    """Classify one candidate's outcome for one security: ``(classification, evidence)``."""

    if request_error is not None:
        return RESPONSE_ERROR, f"request failed: {request_error}"
    if result is None:
        return UNMAPPED, "field not present in this run's request"
    if result.status == "returned":
        value = (result.value or "").strip()
        if not value:
            return UNMAPPED, "returned an empty value"
        evidence = redact(value, candidate.disclosure)
        if candidate.typed_mapping_safe:
            return CONFIRMED, evidence
        return DISPLAY_ONLY, evidence
    if result.status == "absent":
        return UNMAPPED, "absent from fieldData"
    detail = _collapse(result.detail or "", _MAX_DETAIL_CHARS)
    upper = detail.upper()
    if "NOT_ENTITLED" in upper or "ENTITLEMENT" in upper or "NOT AUTHORIZED" in upper:
        return ENTITLEMENT_BLOCKED, detail
    if "BAD_FLD" in upper or "INVALID_FIELD" in upper:
        return BAD_FLD, detail
    return RESPONSE_ERROR, detail or "field exception with no detail"


# --- report --------------------------------------------------------------------


def _row_result(row: InputRow, evidence: tuple[SecurityEvidence, ...]) -> dict:
    """Per-candidate classifications plus this row's disposition and recommendation."""

    by_group = {
        security.identifier: {group.group: group for group in security.groups}
        for security in evidence
    }

    candidates: list[dict] = []
    auto_qualified = False
    for candidate in row.candidates:
        per_security: list[dict] = []
        classifications: list[str] = []
        for security in evidence:
            group = by_group[security.identifier].get(candidate.group)
            classification, detail = classify(
                candidate,
                group.results.get(candidate.mnemonic) if group else None,
                group.error if group else None,
            )
            classifications.append(classification)
            per_security.append(
                {
                    "security": security.identifier,
                    "classification": classification,
                    "evidence": detail,
                }
            )
        if classifications and all(item == CONFIRMED for item in classifications):
            auto_qualified = True
        candidates.append(
            {
                "mnemonic": candidate.mnemonic,
                "provenance": candidate.provenance,
                "request_group": candidate.group,
                "typed_mapping_safe": candidate.typed_mapping_safe,
                "note": candidate.note,
                "results": per_security,
            }
        )

    if row.declared_disposition is not None:
        disposition = row.declared_disposition
    elif auto_qualified:
        disposition = BLOOMBERG_AUTO
    else:
        disposition = row.fallback_disposition

    recommendation = row.declared_recommendation or _RECOMMENDATION_BY_DISPOSITION[disposition]

    return {
        "input_id": row.input_id,
        "section": row.section,
        "question": row.question,
        "disposition": disposition,
        "main_screen_recommendation": recommendation,
        "owner_approval_required": row.owner_approval_required,
        "reason": row.reason,
        "candidates": candidates,
    }


def _summary(rows: list[dict]) -> dict:
    def ids(disposition: str) -> list[str]:
        return [row["input_id"] for row in rows if row["disposition"] == disposition]

    def recommended(recommendation: str) -> list[str]:
        return [
            row["input_id"] for row in rows if row["main_screen_recommendation"] == recommendation
        ]

    return {
        "bloomberg_can_supply": ids(BLOOMBERG_AUTO),
        "shiori_may_derive": ids(SHIORI_DERIVED_CANDIDATE),
        "needs_profile_or_owner_decision": ids(APPROVED_PROFILE_REQUIRED),
        "advanced_override_required": ids(ADVANCED_OVERRIDE_REQUIRED),
        "unresolved": ids(UNRESOLVED),
        "not_required_for_standalone": ids(NOT_REQUIRED_FOR_STANDALONE),
        "main_screen_removals": recommended(REMOVE_FROM_MAIN_SCREEN),
        "main_screen_auto_sourced_read_only": recommended(AUTO_SOURCED_READ_ONLY),
        "main_screen_moved_to_advanced": recommended(MOVE_TO_ADVANCED),
        "main_screen_kept_as_trade_input": recommended(KEEP_AS_TRADE_INPUT),
        "main_screen_blocked_unresolved": recommended(BLOCK_WITH_UNRESOLVED_MESSAGE),
        "owner_decisions_required": [
            row["input_id"] for row in rows if row["owner_approval_required"]
        ],
    }


def _next_delivery_issue(rows: list[dict], override_plan: tuple[OverridePlanEntry, ...]) -> dict:
    """What the next coherent delivery issue should close, derived from this run's rows."""

    sections_needing_approval = []
    for row in rows:
        if row["owner_approval_required"] and row["section"] not in sections_needing_approval:
            sections_needing_approval.append(row["section"])
    blocked = [
        row["input_id"]
        for row in rows
        if row["main_screen_recommendation"] == BLOCK_WITH_UNRESOLVED_MESSAGE
    ]
    return {
        "deliverable": (
            "One UI/data delivery issue that (a) removes every "
            "NOT_REQUIRED_FOR_STANDALONE input from the standalone blocking contract, "
            "(b) renders every AUTO_SOURCED_READ_ONLY input as sourced/derived read-only "
            "with provenance once its owner decision is recorded, (c) moves every "
            "ADVANCED_OVERRIDE_REQUIRED input into an audit-stamped Advanced section, and "
            "(d) stops clearly with a named message for anything still UNRESOLVED."
        ),
        "owner_decisions_gating_it": sections_needing_approval,
        "override_roles_awaiting_owner_confirmation": [
            entry.override_field
            for entry in override_plan
            if entry.status == OVERRIDE_ROLE_UNRESOLVED
        ],
        "must_still_block_until_resolved": blocked,
        "explicitly_out_of_scope": [
            "spot/repo forward reconstruction",
            "yield-vol conversion",
            "curve construction or any new discounting methodology",
            "any production Bloomberg mapping promoted without Eddy's approval",
        ],
    }


def _described_field(description: FieldDescription) -> dict:
    """One field's published metadata, whitespace-collapsed and length-capped."""

    return {
        "field": description.field,
        "status": description.status,
        "mnemonic": description.mnemonic,
        "datatype": description.datatype,
        "description": (
            _collapse(description.description, _MAX_DETAIL_CHARS)
            if description.description
            else None
        ),
        "documented_overrides": list(description.overrides),
        "documentation_excerpt": (
            _collapse(description.documentation, _MAX_DOCUMENTATION_CHARS)
            if description.documentation
            else None
        ),
        "detail": (
            _collapse(description.detail, _MAX_DETAIL_CHARS) if description.detail else None
        ),
    }


def _discovery_result(discovery: DiscoveryEvidence) -> dict:
    return {
        "service": "//blp/apiflds",
        "request": "FieldInfoRequest",
        "requested_at": discovery.requested_at,
        "received_at": discovery.received_at,
        "error": discovery.error,
        "fields": [_described_field(description) for description in discovery.descriptions],
        # Second pass: what Bloomberg publishes about the override field ids
        # the first pass reported. Evidence only -- it never decides a role.
        "override_field_metadata": {
            "service": "//blp/apiflds",
            "request": "FieldInfoRequest",
            "requested_at": discovery.override_requested_at,
            "received_at": discovery.override_received_at,
            "error": discovery.override_error,
            "requested_fields": list(discovery.override_fields),
            "fields": [
                _described_field(description)
                for description in discovery.override_descriptions
            ],
        },
    }


def build_report(
    evidence: tuple[SecurityEvidence, ...],
    discovery: DiscoveryEvidence,
    override_plan: tuple[OverridePlanEntry, ...],
    generated_at: str,
) -> dict:
    """Build the deterministic JSON-serializable result for one probe run.

    Override *values* never appear: only the override field id, where the
    value came from, and its redacted shape.
    """

    rows = [_row_result(row, evidence) for row in INPUT_ROWS]
    return {
        "report": "shiori_bloomberg_input_sourcing_probe",
        "issue": 149,
        "generated_at": generated_at,
        "read_only": True,
        "field_metadata_discovery": _discovery_result(discovery),
        "override_plan": [
            {
                "override_field": entry.override_field,
                "required_by": list(entry.required_by),
                "status": entry.status,
                "role": entry.role,
                "source": entry.source,
                "note": entry.note,
                "metadata": (
                    _described_field(entry.metadata) if entry.metadata is not None else None
                ),
            }
            for entry in override_plan
        ],
        "securities": [
            {
                "identifier": security.identifier,
                "requests": [
                    {
                        "request_group": group.group,
                        "service": "//blp/refdata",
                        "request": "ReferenceDataRequest",
                        "fields": list(group.mnemonics),
                        "overrides": [
                            {
                                "field": item.field,
                                "source": item.source,
                                "value_shape": redact_override_value(item.value),
                            }
                            for item in group.overrides
                        ],
                        "requested_at": group.requested_at,
                        "received_at": group.received_at,
                        "error": group.error,
                    }
                    for group in security.groups
                ],
            }
            for security in evidence
        ],
        "summary": _summary(rows),
        "inputs": rows,
        "prohibited_routes": list(PROHIBITED_ROUTES),
        "next_delivery_issue": _next_delivery_issue(rows, override_plan),
    }


# --- markdown ------------------------------------------------------------------


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|")


def _bullets(items: list[str]) -> list[str]:
    if not items:
        return ["- _none_"]
    return [f"- `{item}`" for item in items]


def render_markdown(report: dict) -> str:
    """Render the redacted, fixed-order Markdown summary for one probe run."""

    summary = report["summary"]
    lines: list[str] = [
        "# Bloomberg input-sourcing evidence — Issue #149",
        "",
        f"- Generated at: {report['generated_at']}",
        "- Read-only bounded probe. No production mapping, schema, pricing behavior "
        "or UI was changed by this run.",
        "- Securities probed: "
        + ", ".join(f"`{security['identifier']}`" for security in report["securities"]),
        "- Market levels are redacted to a value shape; no raw payload, credential or "
        "terminal identifier is recorded.",
        "- Every DAPI request below records its own acquisition window on this probe's "
        "UTC clock; Bloomberg's own as-of timestamp for a value is a separate, "
        "unconfirmed question.",
        "- `AUTO_SOURCED_READ_ONLY` for a `SHIORI_DERIVED_CANDIDATE` or "
        "`APPROVED_PROFILE_REQUIRED` input describes the target screen **after** the "
        "owner decision named in its row is recorded — not something this run authorizes.",
        "",
        "## Summary",
        "",
        "**Bloomberg can supply directly (BLOOMBERG_AUTO)**",
        "",
        *_bullets(summary["bloomberg_can_supply"]),
        "",
        "**Shiori may derive, pending owner approval (SHIORI_DERIVED_CANDIDATE)**",
        "",
        *_bullets(summary["shiori_may_derive"]),
        "",
        "**Needs a UST / Gilt profile or an owner decision (APPROVED_PROFILE_REQUIRED)**",
        "",
        *_bullets(summary["needs_profile_or_owner_decision"]),
        "",
        "**Stays a reviewable Advanced input (ADVANCED_OVERRIDE_REQUIRED)**",
        "",
        *_bullets(summary["advanced_override_required"]),
        "",
        "**Still unresolved (UNRESOLVED)**",
        "",
        *_bullets(summary["unresolved"]),
        "",
        "**Not consumed by this route (NOT_REQUIRED_FOR_STANDALONE)**",
        "",
        *_bullets(summary["not_required_for_standalone"]),
        "",
        "### What this removes from the main screen",
        "",
        f"- removed outright: {len(summary['main_screen_removals'])}",
        f"- auto-sourced / derived read-only: {len(summary['main_screen_auto_sourced_read_only'])}",
        f"- moved to Advanced: {len(summary['main_screen_moved_to_advanced'])}",
        f"- kept as a genuine trade input: {len(summary['main_screen_kept_as_trade_input'])}",
        f"- must block with a clear unresolved message: "
        f"{len(summary['main_screen_blocked_unresolved'])}",
        "",
        "## Dispositions",
        "",
        "| Input | Section | Disposition | Main screen | Owner decision |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report["inputs"]:
        lines.append(
            f"| `{_cell(row['input_id'])}` | {_cell(row['section'])} | "
            f"`{row['disposition']}` | `{row['main_screen_recommendation']}` | "
            f"{'yes' if row['owner_approval_required'] else 'no'} |"
        )

    discovery = report["field_metadata_discovery"]
    lines += [
        "",
        "## Field metadata discovery (`//blp/apiflds` FieldInfoRequest)",
        "",
        f"- Acquisition window: {discovery['requested_at']} → {discovery['received_at']}",
        f"- Request error: {discovery['error'] or 'none'}",
        "",
        "| Field | Status | Datatype | Documented overrides | Description |",
        "| --- | --- | --- | --- | --- |",
    ]
    if discovery["fields"]:
        for field in discovery["fields"]:
            documented = ", ".join(f"`{name}`" for name in field["documented_overrides"]) or "none"
            lines.append(
                f"| `{_cell(field['field'])}` | `{field['status']}` | "
                f"{_cell(field['datatype'] or 'not available')} | {_cell(documented)} | "
                f"{_cell(field['description'] or field['detail'] or 'not available')} |"
            )
    else:
        lines.append("| _no field metadata returned_ |  |  |  |  |")

    override_metadata = discovery["override_field_metadata"]
    lines += [
        "",
        "### Override field metadata (second `FieldInfoRequest`)",
        "",
        "What Bloomberg publishes about the override field ids the pass above reported. "
        "Recorded as evidence only — it never decides which Shiori role an override fills.",
        "",
        f"- Acquisition window: {override_metadata['requested_at'] or 'not requested'} → "
        f"{override_metadata['received_at'] or 'not requested'}",
        f"- Request error: {override_metadata['error'] or 'none'}",
        "",
        "| Override field | Status | Mnemonic | Datatype | Description |",
        "| --- | --- | --- | --- | --- |",
    ]
    if override_metadata["fields"]:
        for field in override_metadata["fields"]:
            lines.append(
                f"| `{_cell(field['field'])}` | `{field['status']}` | "
                f"{_cell(field['mnemonic'] or 'not available')} | "
                f"{_cell(field['datatype'] or 'not available')} | "
                f"{_cell(field['description'] or field['detail'] or 'not available')} |"
            )
        documented = [
            field for field in override_metadata["fields"] if field["documentation_excerpt"]
        ]
        if documented:
            lines.append("")
            for field in documented:
                lines.append(
                    f"- `{field['field']}` documentation: {field['documentation_excerpt']}"
                )
    else:
        lines.append("| _no override field metadata was returned in this run_ |  |  |  |  |")

    lines += [
        "",
        "### Override plan",
        "",
        "Override values are never printed — only the field id, where the value came "
        "from, and its shape.",
        "",
        "| Override field | Required by | Status | Role | Source |",
        "| --- | --- | --- | --- | --- |",
    ]
    if report["override_plan"]:
        for entry in report["override_plan"]:
            required_by = ", ".join(f"`{name}`" for name in entry["required_by"]) or "—"
            lines.append(
                f"| `{_cell(entry['override_field'])}` | {_cell(required_by)} | "
                f"`{entry['status']}` | {_cell(entry['role'] or '—')} | "
                f"{_cell(entry['source'] or '—')} |"
            )
        lines.append("")
        for entry in report["override_plan"]:
            lines.append(f"- `{entry['override_field']}`: {entry['note']}")
    else:
        lines.append("| _no override was documented or supplied in this run_ |  |  |  |  |")

    lines += ["", "## Evidence by input", ""]
    for row in report["inputs"]:
        lines += [
            f"### `{row['input_id']}`",
            "",
            f"- Section: {row['section']}",
            f"- Question: {row['question']}",
            f"- Disposition: `{row['disposition']}` → main screen: "
            f"`{row['main_screen_recommendation']}`",
            f"- Reason: {row['reason']}",
            "",
        ]
        if not row["candidates"]:
            lines += ["_No Bloomberg candidate was probed for this input (see reason above)._", ""]
            continue
        lines += [
            "| Candidate | Provenance | Security | Classification | Evidence |",
            "| --- | --- | --- | --- | --- |",
        ]
        for candidate in row["candidates"]:
            for result in candidate["results"]:
                lines.append(
                    f"| `{_cell(candidate['mnemonic'])}` | `{candidate['provenance']}` | "
                    f"`{_cell(result['security'])}` | `{result['classification']}` | "
                    f"{_cell(result['evidence'])} |"
                )
        lines.append("")
        for candidate in row["candidates"]:
            lines.append(f"- `{candidate['mnemonic']}`: {candidate['note']}")
        lines.append("")

    lines += ["## Requests sent", ""]
    for security in report["securities"]:
        lines.append(f"### `{security['identifier']}`")
        lines.append("")
        for request in security["requests"]:
            overrides = (
                ", ".join(
                    f"`{item['field']}` ({item['source']}) = {item['value_shape']}"
                    for item in request["overrides"]
                )
                or "none"
            )
            lines += [
                f"- Request group `{request['request_group']}`",
                f"  - acquisition window: {request['requested_at']} → {request['received_at']}",
                f"  - fields: {', '.join(f'`{name}`' for name in request['fields'])}",
                f"  - overrides: {overrides}",
                f"  - request error: {request['error'] or 'none'}",
            ]
        lines.append("")

    lines += ["## Prohibited routes (restated)", ""]
    lines += [f"- {item}" for item in PROHIBITED_ROUTES]

    next_issue = report["next_delivery_issue"]
    lines += [
        "",
        "## Next delivery issue",
        "",
        next_issue["deliverable"],
        "",
        "**Owner decisions gating it**",
        "",
    ]
    lines += (
        [f"- {section}" for section in next_issue["owner_decisions_gating_it"]]
        if next_issue["owner_decisions_gating_it"]
        else ["- _none_"]
    )
    lines += ["", "**Must still block until resolved**", ""]
    lines += _bullets(next_issue["must_still_block_until_resolved"])
    lines += ["", "**Explicitly out of scope**", ""]
    lines += [f"- {item}" for item in next_issue["explicitly_out_of_scope"]]
    lines.append("")
    return "\n".join(lines)


def render_json(report: dict) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False) + "\n"


def write_report(report: dict, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / MARKDOWN_FILENAME
    json_path = output_dir / JSON_FILENAME
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(render_json(report), encoding="utf-8")
    return markdown_path, json_path


# --- CLI -----------------------------------------------------------------------


def _parse_override(raw: str) -> tuple[str, str]:
    name, separator, value = raw.partition("=")
    if not separator or not name.strip() or not value.strip():
        raise ValueError(f"override must be FIELD=VALUE, got {raw!r}")
    return name.strip(), value.strip()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Bounded read-only Bloomberg evidence probe for Shiori's remaining "
            "non-trading inputs (Issue #149). Run with no arguments to probe the "
            "default UST/Gilt evidence pair."
        )
    )
    parser.add_argument(
        "--identifier",
        action="append",
        default=None,
        help=(
            "ISIN or CUSIP to probe; repeatable. Default: "
            f"{', '.join(DEFAULT_IDENTIFIERS)} (known verification cases)."
        ),
    )
    parser.add_argument(
        "--override",
        action="append",
        default=None,
        metavar="FIELD=VALUE",
        help=(
            "Advanced escape hatch, never required: send this override verbatim with "
            "the option-context request; repeatable. A normal run discovers the "
            "overrides Bloomberg documents by itself. Values supplied here are "
            "redacted in every output."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=f"Where to write the report (default: ./{DEFAULT_OUTPUT_DIRNAME}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    identifiers = tuple(args.identifier) if args.identifier else DEFAULT_IDENTIFIERS
    try:
        overrides = tuple(_parse_override(raw) for raw in (args.override or []))
        for identifier in identifiers:
            parse_bond_identifier(identifier)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # A confirmed override's value is not replaceable from the command line:
    # OP046 stays the documented pricing model and OP188 stays the option
    # case's valuation date. A --override naming one is rejected before any
    # request is sent, unless it repeats the approved value exactly (a no-op).
    # The rejected value is never echoed back.
    option_context = {**load_option_context_fixture(), **FIXED_ROLE_VALUES}
    for override_field, override_value in overrides:
        role = APPROVED_OVERRIDE_ROLES.get(override_field)
        if role is None:
            continue
        if override_value == option_context.get(role):
            continue
        print(
            f"error: --override {override_field} was rejected -- its value is fixed by the "
            f"confirmed {role!r} semantics and must not be replaced. Drop the --override to "
            "use the confirmed value.",
            file=sys.stderr,
        )
        return 2

    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd() / DEFAULT_OUTPUT_DIRNAME

    print("Shiori Bloomberg input-sourcing probe (Issue #149) -- read-only")
    print(f"Securities: {', '.join(identifiers)}")
    print(
        "Overrides:  "
        + (
            ", ".join(f"{field} = {redact_override_value(value)}" for field, value in overrides)
            or "none supplied (a normal run discovers what Bloomberg documents)"
        )
    )
    print("")

    try:
        discovery = discover_option_context_metadata()
        override_plan, applied_overrides = plan_overrides(discovery, option_context, overrides)
        evidence = collect_evidence(identifiers, applied_overrides)
    except ImportError as exc:
        print(
            "error: blpapi is not installed -- run this on a Bloomberg-networked "
            f"workstation with Bloomberg's official blpapi package installed ({exc})",
            file=sys.stderr,
        )
        return 2

    report = build_report(
        evidence,
        discovery,
        override_plan,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    markdown_path, json_path = write_report(report, output_dir)

    summary = report["summary"]
    for label, key in (
        ("BLOOMBERG_AUTO", "bloomberg_can_supply"),
        ("SHIORI_DERIVED_CANDIDATE", "shiori_may_derive"),
        ("APPROVED_PROFILE_REQUIRED", "needs_profile_or_owner_decision"),
        ("ADVANCED_OVERRIDE_REQUIRED", "advanced_override_required"),
        ("UNRESOLVED", "unresolved"),
        ("NOT_REQUIRED_FOR_STANDALONE", "not_required_for_standalone"),
    ):
        print(f"{label:<30}{len(summary[key])}")

    applied = [entry for entry in report["override_plan"] if entry["status"] == OVERRIDE_APPLIED]
    unresolved_roles = [
        entry for entry in report["override_plan"] if entry["status"] == OVERRIDE_ROLE_UNRESOLVED
    ]
    override_metadata = report["field_metadata_discovery"]["override_field_metadata"]
    print("")
    print(f"{'OVERRIDE IDS DISCOVERED':<30}{len(override_metadata['requested_fields'])}")
    print(f"{'OVERRIDE IDS DESCRIBED':<30}{len(override_metadata['fields'])}")
    not_applicable = [
        entry for entry in report["override_plan"] if entry["status"] == OVERRIDE_NOT_APPLICABLE
    ]
    print(f"{'OVERRIDE IDS NOT APPLICABLE':<30}{len(not_applicable)}")
    print(f"{'OVERRIDES APPLIED':<30}{len(applied)}")
    print(f"{'OVERRIDE ROLES UNRESOLVED':<30}{len(unresolved_roles)}")
    for entry in applied:
        request_override = next(
            (
                item
                for security in report["securities"]
                for request in security["requests"]
                for item in request["overrides"]
                if item["field"] == entry["override_field"]
            ),
            None,
        )
        shape = request_override["value_shape"] if request_override else "not sent"
        print(f"  - {entry['override_field']} ({entry['source']}) = {shape}")
    for entry in unresolved_roles:
        print(f"  - {entry['override_field']}: role unconfirmed, not sent")
    for entry in not_applicable:
        print(f"  - {entry['override_field']}: not applicable to a bond option, not sent")
    for entry in report["override_plan"]:
        if entry["status"] == OVERRIDE_VALUE_UNCONFIRMED:
            print(
                f"  - {entry['override_field']} ({entry['role']}): documented value not "
                "recorded in FIXED_ROLE_VALUES, not sent"
            )

    option_rows = [
        row
        for row in report["inputs"]
        if any(
            candidate["request_group"] == OPTION_CONTEXT_GROUP for candidate in row["candidates"]
        )
    ]
    if option_rows:
        print("")
        print("Option-context field outcomes:")
        for row in option_rows:
            for candidate in row["candidates"]:
                if candidate["request_group"] != OPTION_CONTEXT_GROUP:
                    continue
                for result in candidate["results"]:
                    print(
                        f"  - {candidate['mnemonic']} / {result['security']}: "
                        f"{result['classification']} ({result['evidence']})"
                    )

    request_errors = [
        f"{security['identifier']} / {request['request_group']}: {request['error']}"
        for security in report["securities"]
        for request in security["requests"]
        if request["error"]
    ]
    if override_metadata["error"]:
        request_errors.insert(0, f"//blp/apiflds override metadata: {override_metadata['error']}")
    if report["field_metadata_discovery"]["error"]:
        request_errors.insert(
            0, f"//blp/apiflds discovery: {report['field_metadata_discovery']['error']}"
        )
    if request_errors:
        print("")
        print("Request-level failures (other requests still ran):")
        for line in request_errors:
            print(f"  - {line}")

    print("")
    print("Report written to:")
    print(f"  {markdown_path.resolve()}")
    print(f"  {json_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
