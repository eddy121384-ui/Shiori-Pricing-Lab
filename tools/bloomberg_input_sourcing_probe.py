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

**Overrides are never guessed.** ``OPT_UNDL_FORWARD_PX`` and the
volatility candidates are sent bare by default, so the run records exactly
what Bloomberg demands. Once Eddy has confirmed an override's name and
meaning from Bloomberg's own documentation, re-run with repeated
``--override FIELD=VALUE`` to record the answer; this script proposes no
override name and attaches no default.

**Redaction.** Market levels (forward price, volatility, discounting) are
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
``tools/bloomberg_dapi_probe.probe_fields`` -- this script adds no second
session implementation.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bloomberg_dapi_probe import ProbeFieldResult, probe_fields

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
                    "Sent bare unless --override was supplied, so the run records what "
                    "Bloomberg itself demands instead of a guessed override name. A "
                    "returned number stays DISPLAY_ONLY until quote side, override set, "
                    "unit and acquisition timestamp are separately confirmed."
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
class GroupEvidence:
    """Outcome of one DAPI request (one security, one override set)."""

    group: str
    mnemonics: tuple[str, ...]
    overrides: tuple[tuple[str, str], ...]
    results: dict[str, ProbeFieldResult]
    error: str | None


@dataclass(frozen=True)
class SecurityEvidence:
    identifier: str
    qualified_identifier: str
    groups: tuple[GroupEvidence, ...]


def _group_mnemonics(group: str) -> tuple[str, ...]:
    """Every catalogued mnemonic for one request group, in catalogue order, deduplicated."""

    ordered: list[str] = []
    for row in INPUT_ROWS:
        for candidate in row.candidates:
            if candidate.group == group and candidate.mnemonic not in ordered:
                ordered.append(candidate.mnemonic)
    return tuple(ordered)


def collect_evidence(
    identifiers: tuple[str, ...],
    overrides: tuple[tuple[str, str], ...],
    probe: Callable[..., list[ProbeFieldResult]] | None = None,
) -> tuple[SecurityEvidence, ...]:
    """Run one bounded DAPI request per (security, request group) and collect raw outcomes.

    A ``RuntimeError`` from one request is recorded against that request
    group only -- every other group and every other security still runs.
    ``ImportError`` (no ``blpapi`` at all) propagates: nothing can be probed.
    """

    probe = probe or probe_fields
    evidence: list[SecurityEvidence] = []
    for identifier in identifiers:
        _, qualified = parse_bond_identifier(identifier)
        groups: list[GroupEvidence] = []
        for group in (STATIC_GROUP, OPTION_CONTEXT_GROUP):
            mnemonics = _group_mnemonics(group)
            group_overrides = overrides if group == OPTION_CONTEXT_GROUP else ()
            if not mnemonics:
                continue
            try:
                results = probe(qualified, list(mnemonics), overrides=list(group_overrides))
            except RuntimeError as exc:
                groups.append(
                    GroupEvidence(
                        group=group,
                        mnemonics=mnemonics,
                        overrides=group_overrides,
                        results={},
                        error=_collapse(str(exc), _MAX_DETAIL_CHARS),
                    )
                )
                continue
            groups.append(
                GroupEvidence(
                    group=group,
                    mnemonics=mnemonics,
                    overrides=group_overrides,
                    results={result.field: result for result in results},
                    error=None,
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


def _next_delivery_issue(rows: list[dict]) -> dict:
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
        "must_still_block_until_resolved": blocked,
        "explicitly_out_of_scope": [
            "spot/repo forward reconstruction",
            "yield-vol conversion",
            "curve construction or any new discounting methodology",
            "any production Bloomberg mapping promoted without Eddy's approval",
        ],
    }


def build_report(
    evidence: tuple[SecurityEvidence, ...],
    overrides: tuple[tuple[str, str], ...],
    generated_at: str,
) -> dict:
    """Build the deterministic JSON-serializable result for one probe run."""

    rows = [_row_result(row, evidence) for row in INPUT_ROWS]
    return {
        "report": "shiori_bloomberg_input_sourcing_probe",
        "issue": 149,
        "generated_at": generated_at,
        "read_only": True,
        "securities": [
            {
                "identifier": security.identifier,
                "requests": [
                    {
                        "request_group": group.group,
                        "fields": list(group.mnemonics),
                        "overrides": [
                            {"field": name, "value": value} for name, value in group.overrides
                        ],
                        "error": group.error,
                    }
                    for group in security.groups
                ],
            }
            for security in evidence
        ],
        "overrides_supplied": [{"field": name, "value": value} for name, value in overrides],
        "summary": _summary(rows),
        "inputs": rows,
        "prohibited_routes": list(PROHIBITED_ROUTES),
        "next_delivery_issue": _next_delivery_issue(rows),
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
                ", ".join(f"{item['field']}={item['value']}" for item in request["overrides"])
                or "none"
            )
            lines += [
                f"- Request group `{request['request_group']}`",
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
            "Advanced: a Bloomberg request override to send with the option-context "
            "request, repeatable. No override is guessed or defaulted -- supply one "
            "only after confirming its name and meaning."
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

    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd() / DEFAULT_OUTPUT_DIRNAME

    print("Shiori Bloomberg input-sourcing probe (Issue #149) -- read-only")
    print(f"Securities: {', '.join(identifiers)}")
    print(f"Overrides:  {', '.join(f'{k}={v}' for k, v in overrides) or 'none'}")
    print("")

    try:
        evidence = collect_evidence(identifiers, overrides)
    except ImportError as exc:
        print(
            "error: blpapi is not installed -- run this on a Bloomberg-networked "
            f"workstation with Bloomberg's official blpapi package installed ({exc})",
            file=sys.stderr,
        )
        return 2

    report = build_report(
        evidence,
        overrides,
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

    request_errors = [
        f"{security['identifier']} / {request['request_group']}: {request['error']}"
        for security in report["securities"]
        for request in security["requests"]
        if request["error"]
    ]
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
