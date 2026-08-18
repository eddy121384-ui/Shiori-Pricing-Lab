"""Bloomberg VCUB field hunter -- OVME bond-option proxy vol source discovery (Issue #179).

Bounded, read-only workstation diagnostic CLI -- **not** part of the
production pricing, vol, or ingestion path, and never imported by any of
them. Its one question, and nothing beyond it:

    Can Shiori obtain the VCUB **normal swaption volatility** that OVME's
    bond-option model uses, `sigma_vcub(Texp, Ttenor, Kproxy)`, from a
    supported Bloomberg data interface -- with no screenshot and no OCR?

**Methodology is given, never re-guessed here.** Bloomberg Quantitative
Analytics, *Valuation of Bond Forwards and Options in OVME* (2025) §2.5
defines the reference swaption coordinate this script reports::

    Texp   = bond forward settlement date TF
    Ttenor = residual bond maturity = TB - TF
    Kproxy = KATM + (KY - FY)

This script computes the parts of that coordinate that are pure date and
price arithmetic, asks Bloomberg for the parts that are conversions
(`FY`/`KY`), and records raw evidence for the parts that are discovery
(`KATM`, `sigma_vcub`). It converts nothing into price volatility, wires
nothing into Black-76, and asserts no Bloomberg match: whether the
evidence adds up to a route is Eddy's judgment on the report, exactly as
`tools/ust_s490_repo_carry_forward_parity.py` and the Issue #165 curve
probes already established.

**One command, run at the Bloomberg workstation** (Terminal running and
logged in locally, Bloomberg's official `blpapi` installed)::

    python tools/bloomberg_vcub_field_hunter.py --case my_case.json

That alone runs stages 1-3 below. Stage 4 needs candidate identifiers,
which this script refuses to invent::

    python tools/bloomberg_vcub_field_hunter.py --case my_case.json \\
        --candidate-security "USSN0110 Curncy" \\
        --candidate-field PX_LAST \\
        --candidate-override "SETTLE_DT=20260915" \\
        --katm 3.85 --katm-unit-matches-yield-field

Stage 5 (the `//blp/mktdata` subscription discovery probe) needs an
operator-confirmed topic, least-assumptive form first::

    python tools/bloomberg_vcub_field_hunter.py --case my_case.json \\
        --skip-field-search --skip-yield-conversion \\
        --mktdata-security "USSNAC4 Curncy"

Stage 6 (the `//blp/instruments` lookup discovery probe) needs an
operator-confirmed query, same least-assumptive shape::

    python tools/bloomberg_vcub_field_hunter.py --case my_case.json \\
        --skip-field-search --skip-yield-conversion \\
        --lookup-security "USSNAC4"

**The six stages.**

1. *Proxy coordinate* (offline, deterministic, no Bloomberg at all). One
   already-valid standalone option case is parsed by the existing,
   unmodified
   `app/standalone_option_workbench.build_request_from_standalone_option_case`,
   so the bond, its maturity `TB`, the valuation date, the forward
   settlement date `TF`, the forward clean price and the strike are
   resolved exactly as the Workbench resolves them -- this script has no
   case parser of its own. `Texp`/`Ttenor` year fractions come from the
   reviewed `pricing/bli_valuation_time.year_fraction_to_expiry`
   (ACT/365F, labeled as such); the raw calendar day counts are reported
   alongside them, because **which convention VCUB's own expiry/tenor
   axes use is not asserted here**.

2. *Field metadata discovery* (`//blp/apiflds`). Runs the already-proven
   `FieldSearchRequest.searchSpec` route -- re-confirmed by *this run's
   own* `discover_service` introspection, never assumed from a prior
   round -- for Issue #179's search vocabulary, keeps the records whose
   own Bloomberg-published text literally contains a volatility-cube
   marker, and expands the survivors through round 3's unmodified
   `discover_field_documentation` (which also reports each field's
   documented **override** ids -- the only honest way to learn what
   expiry/tenor/strike parameters a field demands).

3. *Yield conversion probe* (`//blp/refdata`). `FY` and `KY` are
   price->yield conversions, and this repository has **no reviewed
   price->yield helper**; writing a second approximate YTM here is
   exactly what AGENTS.md §3/§7 forbid. So Bloomberg performs the
   conversion: for each candidate yield mnemonic, its **own documented
   override list** is expanded to mnemonics and searched, by literal
   marker, for a price-like and a settlement-like override; the field is
   then probed twice against the case's own bond -- once at the forward
   clean price, once at the strike, both at `TF`. Same field both times,
   so `KY - FY` is unit-consistent by construction even though this
   script never claims to know that unit.

4. *Candidate value probe* (`//blp/refdata`). Only identifiers and
   mnemonics Eddy supplies (typically read out of stage 2's report or a
   VCUB cell's own Excel formula) are probed, with the overrides he
   supplies, and every outcome -- returned value, `absent`, field
   exception, entitlement error -- is recorded verbatim. The
   security x field cross product is hard-capped, because Issue #179
   forbids broad brute force.

5. *`//blp/mktdata` subscription discovery probe* -- opt-in, only when
   `--mktdata-security` is supplied. Tests the remaining hypothesis: is the
   VCUB cell ticker `USSNAC4` exposed as a market-data/calculated-data
   *subscription* topic even though stage 4's `ReferenceDataRequest` route
   above does not expose it? Opens `//blp/mktdata`, subscribes read-only to
   exactly the operator-supplied topic (never invented), captures a small
   bounded number of the earliest subscription status/data messages under a
   hard timeout and a hard message-count cap, unsubscribes and stops the
   session in every case, and records Bloomberg's own message type and
   field names/values verbatim -- never ranking, renaming or promoting a
   returned field because its value happens to resemble `89.15`. If
   `--mktdata-field` is not supplied, the subscription is sent
   field-agnostic (Bloomberg's own default field set for the topic, if it
   has one); if Bloomberg's own `subscribe()` call itself rejects that
   (e.g. because its API genuinely requires an explicit field list), that
   rejection is captured verbatim as `subscription_failure` rather than
   this script inventing a field list to work around it -- see
   `run_mktdata_subscription`'s own docstring below. This stage assigns no
   verdict either.

6. *`//blp/instruments` lookup discovery probe* -- opt-in, only when
   `--lookup-security` is supplied. Tests whether Bloomberg exposes an
   *instrument-resolution/lookup* route for the operator-supplied query at
   all, distinct from the reference-data and subscription routes stages
   4/5 already probed. First audits `//blp/instruments`'s own schema via
   the same `discover_service` introspection stage 2 already uses (opened,
   full schema dump, every operation's own request schema); only if this
   run's own introspection reports an operation whose *own* request schema
   exposes a top-level `STRING` element does this stage send the query
   through it, using exactly that element -- never a request name or field
   this script assumed from memory or documentation. Every returned
   security/parse-key/description is recorded verbatim (raw dump) and via
   a best-effort field walk, both sanitized; none is promoted, renamed, or
   asserted to be the query's resolved instrument. If `//blp/instruments`
   does not open, or opens but no operation exposes a usable element, that
   is reported explicitly as the feasibility answer -- never papered over
   with a guessed request. No yellow key, suffix, or field mnemonic is
   invented anywhere in this stage. This stage assigns no verdict either.

**Deliberately not in this script.** No `sigma_vcub` -> normal yield vol
-> price vol conversion. No Black-76, no `PRICE_VOL` contract change, no
vol auto-fill, no surface construction, no interpolation of anything (if
Bloomberg returns a grid, the grid is reported as a grid). No screenshot,
OCR or UI automation. No calibration, multiplier or fudge that would make
a candidate number "look right". No hard-coded ticker, curve id, VCUB
coordinate or magic override. No Gilt/EUR/JPY generalization. No guessed
yellow key, ticker suffix, or lookup field mnemonic anywhere (stage 6
included) -- an operation/field is used only once this run's own schema
introspection confirms it.

**Separation from production.** Never imported by anything under `src/`.
Reuses `bloomberg_curve_discovery_probe.discover_service` /
`bloomberg_dapi_probe._send_request` / `probe_fields` /
`bloomberg_curve_field_documentation_probe.discover_field_documentation`
/ `bloomberg_input_sourcing_probe.sanitize_external_text` unchanged -- no
second Bloomberg session, request, or documentation-expansion
implementation. Stage 5's `//blp/mktdata` session
(`_run_mktdata_subscription_session`) is new: this repo was audited first
(every `tools/bloomberg_*.py` module) and had no existing subscription /
`//blp/mktdata` session helper to reuse -- every other Bloomberg helper here
is a `//blp/refdata`/`//blp/apiflds` request/response probe, a different
session shape entirely. It stays inside this developer-tool file rather
than moving into `src/`, still uses `sanitize_external_text` for every
Bloomberg-authored string it stores, and remains its own single, bounded
`//blp/mktdata` implementation -- no second one is added elsewhere. Stage
6 (`probe_instrument_lookup`) adds no new session framework at all: it
reuses `discover_service` and `_send_request` unchanged, the same
request/response shape stage 2 already established for
`//blp/apiflds`/`//blp/refdata` -- `//blp/instruments` is simply a third
service URI sent through that same proven plumbing.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from math import isfinite
from pathlib import Path

from bloomberg_curve_discovery_probe import (
    _APIFLDS_SERVICE,
    _PROVEN_SEARCH_ELEMENT,
    _PROVEN_SEARCH_OPERATION,
    _safe_str,
    discover_service,
)
from bloomberg_curve_field_documentation_probe import (
    build_report as build_documentation_report,
)
from bloomberg_curve_field_documentation_probe import (
    discover_field_documentation,
)
from bloomberg_dapi_probe import (
    _send_request,  # shared session/event-loop plumbing
    probe_fields,
)
from bloomberg_input_sourcing_probe import sanitize_external_text

from shiori_pricing_lab.app.standalone_option_workbench import (
    build_request_from_standalone_option_case,
)
from shiori_pricing_lab.data.bloomberg_bond_quote import parse_bond_identifier
from shiori_pricing_lab.pricing.bli_valuation_time import year_fraction_to_expiry

# Issue #179 §3's own search vocabulary, verbatim -- no additions, no rewording.
DEFAULT_VCUB_SEARCH_TERMS: tuple[str, ...] = (
    "VCUB",
    "swaption",
    "normal vol",
    "normal volatility",
    "volatility cube",
    "ATM swap rate",
    "swaption expiry",
    "swap tenor",
    "strike spread",
    "moneyness",
)

# Literal substring markers used to keep a search record. This is a plain
# case-insensitive text match against what *Bloomberg itself* published
# about a field (mnemonic, description, raw record dump) -- never a
# semantic inference about what a mnemonic means, and never a claim that a
# marked field is the OVME proxy source.
VOL_RELEVANCE_MARKERS: tuple[str, ...] = (
    "swaption",
    "volatility cube",
    "vol cube",
    "normal vol",
    "vcub",
)

# Candidate price->yield mnemonics. UNCONFIRMED against any live DAPI
# response by this repository -- they are probed, never trusted, and this
# script wires none of them anywhere. Stage 3 reports exactly what
# Bloomberg says about each one, including whether it documents a
# price/settlement override at all.
DEFAULT_YIELD_FIELD_CANDIDATES: tuple[str, ...] = (
    "YAS_BOND_YLD",
    "YLD_YTM_MID",
)

# Literal markers used to pick a price-like / settlement-like override out
# of a candidate yield field's *own Bloomberg-documented* override list.
# Matched against the expanded override mnemonics, never against a guessed
# override name.
_PRICE_OVERRIDE_MARKERS: tuple[str, ...] = ("PX", "PRICE")
_SETTLE_OVERRIDE_MARKERS: tuple[str, ...] = ("SETTLE",)

# Issue #179 forbids broad brute force. Stage 4 refuses outright above this.
MAX_CANDIDATE_PROBES = 40

DEFAULT_OUTPUT_DIRNAME = "shiori_vcub_field_hunter_output"
MARKDOWN_FILENAME = "bloomberg_vcub_field_hunter.md"
JSON_FILENAME = "bloomberg_vcub_field_hunter.json"

# The one day-count label this script is entitled to state, because it is
# the one the reused helper implements. VCUB's own axis convention is not
# claimed to be this, or anything else.
_YEAR_FRACTION_CONVENTION = "ACT/365F (pricing.bli_valuation_time.year_fraction_to_expiry)"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


# --- stage 1: proxy coordinate ------------------------------------------------------


@dataclass(frozen=True)
class ProxyCoordinate:
    """The §2.5 reference-swaption coordinate, as far as arithmetic can take it.

    Every field here is either read verbatim off the parsed case or is
    date/price arithmetic over it. Nothing is a Bloomberg observation, and
    nothing is a methodology decision.
    """

    currency: str
    underlying_isin: str
    valuation_date: str
    bond_maturity_date: str  # TB
    forward_settlement_date: str  # TF
    texp_years: float
    texp_days: int
    ttenor_years: float
    ttenor_days: int
    year_fraction_convention: str
    forward_clean_price_per_100: float
    strike_price_per_100: float
    option_type: str


def _enum_value(value) -> str:
    """The plain string behind an already-coerced enum, so the report stays JSON-safe."""

    return str(getattr(value, "value", value))


def derive_proxy_coordinate(request) -> ProxyCoordinate:
    """Derive the §2.5 coordinate from one already-built standalone request.

    `Texp` is measured from the case's own valuation date to the forward
    settlement date `TF`, and `Ttenor` from `TF` to the bond maturity `TB`
    -- both through the reviewed ACT/365F helper, with the raw calendar day
    counts reported next to them so a reader can re-express either one under
    whatever convention VCUB's axes actually use. Raises `ValueError` if the
    case has no explicit forward clean price (the coordinate needs `FY`'s
    input) or if `TF` does not fall between the valuation date and `TB`.
    """

    snapshot = request.market_data_snapshot
    forward_input = snapshot.forward_clean_price_input
    if forward_input is None:
        raise ValueError(
            "case has no forward_clean_price_input -- the proxy coordinate needs an "
            "explicit forward clean price per 100 to convert into FY"
        )
    if request.bond_option.strike_price is None:
        raise ValueError(
            "case has no bond_option.strike_price -- the proxy coordinate needs a price "
            "strike to convert into KY; a yield strike is a different, unhandled shape"
        )

    valuation_date = date.fromisoformat(request.valuation_date)
    forward_settlement_date = date.fromisoformat(request.forward_settlement_date)
    bond_maturity_date = date.fromisoformat(request.resolved_bond_reference_data.maturity_date)

    if not valuation_date < forward_settlement_date < bond_maturity_date:
        raise ValueError(
            "the case's dates do not form a bond-option proxy coordinate: expected "
            f"valuation_date ({request.valuation_date}) < forward_settlement_date "
            f"({request.forward_settlement_date}) < bond maturity "
            f"({request.resolved_bond_reference_data.maturity_date})"
        )

    return ProxyCoordinate(
        currency=_enum_value(request.bond_option.currency),
        underlying_isin=request.bond_option.underlying_isin,
        valuation_date=request.valuation_date,
        bond_maturity_date=request.resolved_bond_reference_data.maturity_date,
        forward_settlement_date=request.forward_settlement_date,
        texp_years=year_fraction_to_expiry(
            request.valuation_date, request.forward_settlement_date
        ),
        texp_days=(forward_settlement_date - valuation_date).days,
        ttenor_years=year_fraction_to_expiry(
            request.forward_settlement_date, request.resolved_bond_reference_data.maturity_date
        ),
        ttenor_days=(bond_maturity_date - forward_settlement_date).days,
        year_fraction_convention=_YEAR_FRACTION_CONVENTION,
        forward_clean_price_per_100=forward_input.forward_clean_price_per_100,
        strike_price_per_100=request.bond_option.strike_price,
        option_type=_enum_value(request.bond_option.option_type),
    )


# --- stage 2: field metadata discovery ----------------------------------------------


@dataclass(frozen=True)
class VolFieldSearchRecord:
    search_term: str
    field_id: str | None
    mnemonic: str | None
    description: str | None
    raw_record_dump: str | None
    marker_relevant: bool


@dataclass(frozen=True)
class VolFieldSearchOutcome:
    term: str
    status: str  # "sent" | "error"
    error: str | None
    structured_parse_ok: bool
    raw_response_dump: str | None
    records: tuple[VolFieldSearchRecord, ...]


@dataclass(frozen=True)
class VolFieldSearchReport:
    search_capability_confirmed: bool
    apiflds_open_error: str | None
    search_terms: tuple[str, ...]
    term_outcomes: tuple[VolFieldSearchOutcome, ...]
    marker_relevant_mnemonics: tuple[str, ...]
    documentation_expansion: dict | None
    blocker_note: str | None


def _is_marker_relevant(*texts: str | None) -> bool:
    return any(
        marker in text.lower() for text in texts if text for marker in VOL_RELEVANCE_MARKERS
    )


def _optional_element_string(element, name: str) -> str | None:
    try:
        if element is None or not element.hasElement(name):
            return None
        return element.getElementAsString(name)
    except Exception:  # noqa: BLE001 -- structural probe only, never fatal
        return None


def _parse_one_record(record) -> dict:
    field_id = _optional_element_string(record, "id")
    info = None
    try:
        if record.hasElement("fieldInfo"):
            info = record.getElement("fieldInfo")
    except Exception:  # noqa: BLE001
        info = None
    return {
        "field_id": field_id,
        "mnemonic": _optional_element_string(info, "mnemonic"),
        "description": _optional_element_string(info, "description"),
        "raw_record_dump": _safe_str(record),
    }


def _parse_search_response(message) -> tuple[bool, tuple[dict, ...]]:
    """Best-effort `fieldData`-array parse of one `FieldSearchRequest` response.

    Never raises: a shape mismatch just means `parsed_ok=False`, and the
    caller keeps the whole message's raw dump instead, so no evidence is
    lost to a parsing assumption that turns out wrong.
    """

    try:
        if not message.hasElement("fieldData"):
            return False, ()
        field_data_array = message.getElement("fieldData")
        count = field_data_array.numValues()
    except Exception:  # noqa: BLE001
        return False, ()

    records: list[dict] = []
    for i in range(count):
        try:
            record = field_data_array.getValueAsElement(i)
        except Exception:  # noqa: BLE001
            continue
        records.append(_parse_one_record(record))
    return True, tuple(records)


def hunt_vol_fields(
    search_terms: tuple[str, ...] = DEFAULT_VCUB_SEARCH_TERMS,
    send_request=_send_request,
    discover_service_fn=None,
    describe=None,
) -> VolFieldSearchReport:
    """Search `//blp/apiflds` for volatility-cube field metadata, then expand it.

    Gated on this run's own introspection of the proven
    `FieldSearchRequest.searchSpec` pair. Marker-relevant mnemonics
    (deduplicated, first-seen order) are handed to round 3's
    `discover_field_documentation`, whose two-pass expansion also reports
    each field's documented override ids -- what a candidate field demands
    as expiry/tenor/strike parameters, from Bloomberg rather than from a
    guess. An empty candidate set skips the expansion entirely.
    """

    discover_service_fn = discover_service_fn or discover_service
    apiflds = discover_service_fn(_APIFLDS_SERVICE)
    search_capability_confirmed = apiflds.opened and any(
        operation.name == _PROVEN_SEARCH_OPERATION
        and _PROVEN_SEARCH_ELEMENT in operation.candidate_string_elements
        for operation in apiflds.operations
    )

    if not search_capability_confirmed:
        return VolFieldSearchReport(
            search_capability_confirmed=False,
            apiflds_open_error=apiflds.open_error,
            search_terms=search_terms,
            term_outcomes=(),
            marker_relevant_mnemonics=(),
            documentation_expansion=None,
            blocker_note=(
                f"{_PROVEN_SEARCH_OPERATION}.{_PROVEN_SEARCH_ELEMENT} was not confirmed by "
                "this run's own //blp/apiflds introspection, so no VCUB field search was "
                "attempted and no mnemonic was guessed as a fallback."
            ),
        )

    term_outcomes: list[VolFieldSearchOutcome] = []
    marker_relevant_mnemonics: list[str] = []

    for term in search_terms:
        raw_messages: list[str] = []
        parsed_messages: list[tuple[bool, tuple[dict, ...]]] = []

        def _configure(request, _term=term) -> None:
            try:
                request.set(_PROVEN_SEARCH_ELEMENT, _term)
            except Exception:  # noqa: BLE001
                request.append(_PROVEN_SEARCH_ELEMENT, _term)

        def _collect(message, _raw=raw_messages, _parsed=parsed_messages) -> None:
            _raw.append(_safe_str(message))
            _parsed.append(_parse_search_response(message))

        try:
            send_request(
                service_uri=_APIFLDS_SERVICE,
                request_name=_PROVEN_SEARCH_OPERATION,
                configure=_configure,
                collect=_collect,
                context=f"{_PROVEN_SEARCH_OPERATION}({_PROVEN_SEARCH_ELEMENT}={term!r})",
            )
        except (RuntimeError, ImportError) as exc:
            term_outcomes.append(
                VolFieldSearchOutcome(
                    term=term,
                    status="error",
                    error=sanitize_external_text(str(exc)),
                    structured_parse_ok=False,
                    raw_response_dump=None,
                    records=(),
                )
            )
            continue

        records: list[VolFieldSearchRecord] = []
        for ok, raw_records in parsed_messages:
            if not ok:
                continue
            for raw_record in raw_records:
                marker_relevant = _is_marker_relevant(
                    raw_record["mnemonic"],
                    raw_record["description"],
                    raw_record["raw_record_dump"],
                )
                record = VolFieldSearchRecord(
                    search_term=term,
                    field_id=raw_record["field_id"],
                    mnemonic=raw_record["mnemonic"],
                    description=sanitize_external_text(raw_record["description"]),
                    raw_record_dump=sanitize_external_text(raw_record["raw_record_dump"]),
                    marker_relevant=marker_relevant,
                )
                records.append(record)
                if (
                    marker_relevant
                    and record.mnemonic
                    and record.mnemonic not in marker_relevant_mnemonics
                ):
                    marker_relevant_mnemonics.append(record.mnemonic)

        term_outcomes.append(
            VolFieldSearchOutcome(
                term=term,
                status="sent",
                error=None,
                structured_parse_ok=any(ok for ok, _ in parsed_messages),
                raw_response_dump=(
                    "\n---\n".join(sanitize_external_text(t) for t in raw_messages if t) or None
                ),
                records=tuple(records),
            )
        )

    documentation_expansion = None
    if marker_relevant_mnemonics:
        documentation_expansion = build_documentation_report(
            discover_field_documentation(tuple(marker_relevant_mnemonics), describe=describe)
        )
        blocker_note = (
            "Marker-relevant candidates were found and expanded below, including each "
            "field's Bloomberg-documented override ids. Whether any of them exposes the "
            "OVME proxy vol at an (expiry, tenor, strike) coordinate is a judgment on "
            "this report, not a claim made by this script."
        )
    else:
        blocker_note = (
            "FieldSearchRequest.searchSpec returned no record whose own Bloomberg text "
            f"contained any of {', '.join(VOL_RELEVANCE_MARKERS)} for any of this run's "
            f"{len(search_terms)} terms. Read each term's raw response dump by hand "
            "before concluding the field dictionary genuinely exposes none -- the "
            "structured parse here is a best-effort guess at Bloomberg's response shape."
        )

    return VolFieldSearchReport(
        search_capability_confirmed=True,
        apiflds_open_error=None,
        search_terms=search_terms,
        term_outcomes=tuple(term_outcomes),
        marker_relevant_mnemonics=tuple(marker_relevant_mnemonics),
        documentation_expansion=documentation_expansion,
        blocker_note=blocker_note,
    )


# --- stage 3: FY / KY yield conversion ----------------------------------------------


@dataclass(frozen=True)
class YieldConversionProbe:
    """One (field, price) conversion attempt and its verbatim outcome."""

    role: str  # "FY" | "KY"
    field: str
    price_override_field: str | None
    price_override_value: str | None
    settle_override_field: str | None
    settle_override_value: str | None
    status: str  # "returned" | "absent" | "field_exception" | "request_error" | "skipped"
    value: str | None
    detail: str | None


@dataclass(frozen=True)
class YieldConversionReport:
    identifier: str
    candidate_fields: tuple[str, ...]
    documentation_expansion: dict | None
    probes: tuple[YieldConversionProbe, ...]
    blocker_note: str | None


def _select_override(mnemonics: tuple[str, ...], markers: tuple[str, ...]) -> str | None:
    for mnemonic in mnemonics:
        upper = mnemonic.upper()
        if any(marker in upper for marker in markers):
            return mnemonic
    return None


def _documented_override_mnemonics(expansion: dict | None, field: str) -> tuple[str, ...]:
    """Map one primary field's documented override ids onto their mnemonics.

    Both halves come from `discover_field_documentation`'s own two passes:
    pass 1 says which override ids a field documents, pass 2 says what each
    of those ids is called. An id pass 2 could not describe keeps its raw
    id, so nothing is silently dropped.
    """

    if not expansion:
        return ()
    id_to_mnemonic = {
        description["field"]: description.get("mnemonic") or description["field"]
        for description in expansion.get("override_descriptions", [])
    }
    for description in expansion.get("primary_descriptions", []):
        if field not in (description.get("field"), description.get("mnemonic")):
            continue
        return tuple(
            id_to_mnemonic.get(override_id, override_id)
            for override_id in description.get("overrides", [])
        )
    return ()


def _format_override_date(iso_date: str) -> str:
    """Bloomberg's `YYYYMMDD` override date form."""

    return date.fromisoformat(iso_date).strftime("%Y%m%d")


def probe_yield_conversion(
    coordinate: ProxyCoordinate,
    candidate_fields: tuple[str, ...] = DEFAULT_YIELD_FIELD_CANDIDATES,
    describe=None,
    probe=None,
) -> YieldConversionReport:
    """Ask Bloomberg to convert the forward clean price and the strike into yields.

    No YTM arithmetic happens in this repository: each candidate field's
    own documented overrides decide how the request is parameterized, and
    the same field answers both `FY` and `KY`, so their difference is
    unit-consistent without this script ever asserting what that unit is.
    A field that documents no price-like override is reported `skipped`,
    with the full documented list, rather than probed with a guessed
    override name.
    """

    probe = probe or probe_fields
    _, identifier = parse_bond_identifier(coordinate.underlying_isin)
    expansion = build_documentation_report(
        discover_field_documentation(candidate_fields, describe=describe)
    )

    settle_value = _format_override_date(coordinate.forward_settlement_date)
    probes: list[YieldConversionProbe] = []

    for field in candidate_fields:
        documented = _documented_override_mnemonics(expansion, field)
        price_override = _select_override(documented, _PRICE_OVERRIDE_MARKERS)
        settle_override = _select_override(documented, _SETTLE_OVERRIDE_MARKERS)

        for role, price in (
            ("FY", coordinate.forward_clean_price_per_100),
            ("KY", coordinate.strike_price_per_100),
        ):
            if price_override is None:
                probes.append(
                    YieldConversionProbe(
                        role=role,
                        field=field,
                        price_override_field=None,
                        price_override_value=None,
                        settle_override_field=settle_override,
                        settle_override_value=settle_value if settle_override else None,
                        status="skipped",
                        value=None,
                        detail=(
                            "Bloomberg documented no price-like override for this field "
                            f"(documented overrides: {', '.join(documented) or 'none'}); no "
                            "override name was guessed"
                        ),
                    )
                )
                continue

            overrides = [(price_override, f"{price}")]
            if settle_override is not None:
                overrides.append((settle_override, settle_value))

            try:
                results = probe(identifier, [field], overrides)
            except (RuntimeError, ImportError, ValueError) as exc:
                probes.append(
                    YieldConversionProbe(
                        role=role,
                        field=field,
                        price_override_field=price_override,
                        price_override_value=f"{price}",
                        settle_override_field=settle_override,
                        settle_override_value=settle_value if settle_override else None,
                        status="request_error",
                        value=None,
                        detail=sanitize_external_text(str(exc)),
                    )
                )
                continue

            result = results[0]
            probes.append(
                YieldConversionProbe(
                    role=role,
                    field=field,
                    price_override_field=price_override,
                    price_override_value=f"{price}",
                    settle_override_field=settle_override,
                    settle_override_value=settle_value if settle_override else None,
                    status=result.status,
                    # A returned value is Bloomberg-authored text too: a
                    # text-valued field can carry a host, path or
                    # credential-shaped string, so it goes through the same
                    # sanitizer as an error detail before being stored.
                    value=sanitize_external_text(result.value),
                    detail=sanitize_external_text(result.detail),
                )
            )

    returned_fields = {p.field for p in probes if p.status == "returned"}
    if not returned_fields:
        blocker_note = (
            "No candidate yield field returned a value for either FY or KY, so the "
            "additive yield moneyness KY - FY is unresolved this run. Nothing was "
            "approximated locally: this repository has no reviewed price->yield helper "
            "and this script does not add one."
        )
    else:
        blocker_note = (
            f"Fields that answered: {', '.join(sorted(returned_fields))}. Their unit is "
            "whatever Bloomberg's own documentation for that field says -- this script "
            "asserts none, and KY - FY is only reported per field, never across fields."
        )

    return YieldConversionReport(
        identifier=identifier,
        candidate_fields=candidate_fields,
        documentation_expansion=expansion,
        probes=tuple(probes),
        blocker_note=blocker_note,
    )


def yield_moneyness_by_field(report: YieldConversionReport) -> dict[str, float]:
    """`KY - FY` per field, only where that same field returned both, parsed as float.

    A leg Bloomberg returned as `NaN`/`Inf` is dropped rather than parsed:
    it is not a yield, and letting it through would put a non-standard
    numeric token in the JSON report and a meaningless moneyness in the
    coordinate.
    """

    per_field: dict[str, dict[str, float]] = {}
    for probe in report.probes:
        if probe.status != "returned" or probe.value is None:
            continue
        try:
            parsed = float(probe.value)
        except ValueError:
            continue
        if not isfinite(parsed):
            continue
        per_field.setdefault(probe.field, {})[probe.role] = parsed
    return {
        field: roles["KY"] - roles["FY"]
        for field, roles in per_field.items()
        if "FY" in roles and "KY" in roles
    }


# --- stage 4: candidate vol / KATM value probe --------------------------------------


@dataclass(frozen=True)
class CandidateValueProbe:
    security: str
    field: str
    overrides: tuple[tuple[str, str], ...]
    status: str  # "returned" | "absent" | "field_exception" | "request_error"
    value: str | None
    detail: str | None


def probe_candidate_values(
    securities: tuple[str, ...],
    fields: tuple[str, ...],
    overrides: tuple[tuple[str, str], ...] = (),
    probe=None,
) -> tuple[CandidateValueProbe, ...]:
    """Probe operator-supplied candidate identifiers x fields, recording raw outcomes.

    Every identifier, mnemonic and override here comes from Eddy (usually
    from stage 2's report or a VCUB cell's own generated Excel formula);
    this script invents none of them. Raises `ValueError` above
    `MAX_CANDIDATE_PROBES` combinations rather than becoming the broad
    brute-force sweep Issue #179 forbids.
    """

    probe = probe or probe_fields
    if not securities or not fields:
        return ()
    if len(securities) * len(fields) > MAX_CANDIDATE_PROBES:
        raise ValueError(
            f"{len(securities)} securities x {len(fields)} fields exceeds the "
            f"{MAX_CANDIDATE_PROBES}-probe cap -- Issue #179 forbids broad brute force; "
            "narrow the candidate list instead"
        )

    probes: list[CandidateValueProbe] = []
    for security in securities:
        try:
            results = probe(security, list(fields), list(overrides))
        except (RuntimeError, ImportError, ValueError) as exc:
            for field in fields:
                probes.append(
                    CandidateValueProbe(
                        security=security,
                        field=field,
                        overrides=overrides,
                        status="request_error",
                        value=None,
                        detail=sanitize_external_text(str(exc)),
                    )
                )
            continue
        for result in results:
            probes.append(
                CandidateValueProbe(
                    security=security,
                    field=result.field,
                    overrides=overrides,
                    status=result.status,
                    value=sanitize_external_text(result.value),
                    detail=sanitize_external_text(result.detail),
                )
            )
    return tuple(probes)


# --- stage 5: //blp/mktdata subscription discovery probe ----------------------------

_MKTDATA_SERVICE = "//blp/mktdata"
_MKTDATA_DAPI_HOST = "localhost"
_MKTDATA_DAPI_PORT = 8194

# Small and bounded, per Issue #179: this is a discovery probe, not a live feed.
DEFAULT_MKTDATA_TIMEOUT_SECONDS = 10.0
DEFAULT_MKTDATA_MESSAGE_CAP = 10

# Literal substring markers against Bloomberg's own `msg.messageType()` text --
# the same marker idiom stage 2's `_is_marker_relevant` already uses, never a
# semantic read of a field value. `SUBSCRIPTION_DATA` is matched on the
# `blpapi.Event` type itself, not text, since that is the structural signal
# Bloomberg's own SDK exposes for "this message carries subscribed field data".
_MKTDATA_STATUS_CATEGORY_MARKERS: tuple[tuple[str, str], ...] = (
    ("Failure", "subscription_failure"),
    ("Terminated", "subscription_terminated"),
    ("Started", "subscription_started"),
)


@dataclass(frozen=True)
class RawMktdataMessage:
    """One captured `//blp/mktdata` message, already reduced to plain Python.

    Bloomberg-native `blpapi` objects never cross out of
    `_run_mktdata_subscription_session` -- this is the boundary, so
    `run_mktdata_subscription`'s classification/sanitization/report logic
    below is deterministic and testable without a live Bloomberg session.
    """

    event_type: str  # "SUBSCRIPTION_STATUS" | "SUBSCRIPTION_DATA" -- Bloomberg's own blpapi.Event
    message_type: str  # str(msg.messageType()), Bloomberg's own text, verbatim
    correlation_id: str | None
    fields: tuple[tuple[str, str | None], ...]  # (name, raw value) pairs, verbatim, unranked
    raw_dump: str | None


@dataclass(frozen=True)
class MktdataField:
    name: str
    value: str | None


@dataclass(frozen=True)
class MktdataMessageEvent:
    sequence: int
    event_type: str
    message_type: str
    category: str  # this script's literal-marker bucket, for the report's outcome only
    correlation_id: str | None
    fields: tuple[MktdataField, ...]
    raw_message_dump: str | None


@dataclass(frozen=True)
class MktdataSubscriptionReport:
    topic: str
    fields_requested: tuple[str, ...]
    message_cap: int
    timeout_seconds: float
    outcome: str  # "subscription_started" | "subscription_failure" | "subscription_terminated"
    #             | "data_received" | "timeout_no_data" | "subscription_status_unclassified"
    subscribe_error: str | None
    events: tuple[MktdataMessageEvent, ...]
    blocker_note: str


def _message_top_level_fields(message) -> tuple[tuple[str, str | None], ...]:
    """Top-level `(name, value)` pairs off one message's own element tree.

    Walks `message.asElement()`'s own `numElements()`/`getElement(index)` --
    Bloomberg's own generic element-tree API, the same structural trust this
    repo already places in `securityData`/`fieldData` elsewhere in this
    file -- and never asks for a field by a mnemonic this script invented. A
    sub-element that is itself a sequence/array is recorded as its own
    `str()` dump rather than a guessed scalar, so nothing is silently
    collapsed or misrepresented. Never raises: a shape this SDK version
    cannot walk this way just yields fewer fields, never a lost message.

    Shared between stage 5 (`//blp/mktdata`) and stage 6
    (`//blp/instruments`) -- both receive a Bloomberg response/event message
    whose exact field shape is not something this repo has confirmed live,
    so both read it the same field-agnostic way rather than each guessing
    its own.
    """

    try:
        element = message.asElement()
        count = element.numElements()
    except Exception:  # noqa: BLE001 -- structural probe only, never fatal
        return ()

    fields: list[tuple[str, str | None]] = []
    for i in range(count):
        try:
            sub = element.getElement(i)
            name = str(sub.name())
        except Exception:  # noqa: BLE001
            continue
        try:
            if sub.numElements() > 0 or sub.numValues() > 1:
                value = _safe_str(sub)
            else:
                value = sub.getValueAsString()
        except Exception:  # noqa: BLE001
            value = _safe_str(sub)
        fields.append((name, value))
    return tuple(fields)


def _run_mktdata_subscription_session(
    topic: str,
    fields: tuple[str, ...],
    max_messages: int,
    timeout_seconds: float,
) -> tuple[list[RawMktdataMessage], str | None]:
    """Open `//blp/mktdata`, subscribe once, drain a bounded window, clean up.

    The one place this file touches a market-data session: start, open
    service, subscribe, collect up to `max_messages` events within
    `timeout_seconds`, then *always* unsubscribe and stop -- mirroring
    `bloomberg_dapi_probe._send_request`'s single-session-per-call shape,
    the only other Bloomberg session helper this repo has (that one is
    `//blp/refdata` request/response; there is no existing `//blp/mktdata`
    subscription helper anywhere in this repo to reuse instead -- confirmed
    by audit before writing this). `blpapi` is imported lazily, exactly like
    every other Bloomberg-touching function in this file, so nothing else
    here needs it installed; tests inject a fake via `sys.modules["blpapi"]`
    before calling this function, the same technique
    `tests/test_bloomberg_dapi_probe.py` already uses for `_send_request`.

    Returns the plain-Python messages captured -- Bloomberg-native objects
    never leave this function -- and a subscribe-time error string if
    `SubscriptionList.add`/`session.subscribe` itself raised before any
    event was received. That is exactly the signal Issue #179 asked this
    probe to surface if Bloomberg's own API requires an explicit field list
    this call did not supply: a captured rejection, not an invented list.
    """

    import blpapi

    session_options = blpapi.SessionOptions()
    session_options.setServerHost(_MKTDATA_DAPI_HOST)
    session_options.setServerPort(_MKTDATA_DAPI_PORT)
    session = blpapi.Session(session_options)

    messages: list[RawMktdataMessage] = []
    subscriptions = None

    try:
        if not session.start():
            return messages, (
                f"Bloomberg DAPI session failed to start against "
                f"{_MKTDATA_DAPI_HOST}:{_MKTDATA_DAPI_PORT} -- confirm a Bloomberg Terminal "
                "is running and logged in locally"
            )
        if not session.openService(_MKTDATA_SERVICE):
            return messages, f"Bloomberg DAPI failed to open service {_MKTDATA_SERVICE}"

        subscriptions = blpapi.SubscriptionList()
        correlation_id = blpapi.CorrelationId(topic)
        try:
            subscriptions.add(topic, ",".join(fields), "", correlation_id)
            session.subscribe(subscriptions)
        except Exception as exc:  # noqa: BLE001 -- a subscribe-time rejection is evidence, not a crash
            # `subscriptions` stays populated (not reset to `None`) so the
            # `finally` block below still attempts `session.unsubscribe` --
            # already itself guarded against raising -- rather than relying
            # on `session.stop()` alone for cleanup on this exit path.
            return messages, str(exc)

        deadline = time.monotonic() + timeout_seconds
        while len(messages) < max_messages:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            event = session.nextEvent(max(1, int(remaining * 1000)))
            event_type = event.eventType()
            if event_type == blpapi.Event.TIMEOUT:
                break
            if event_type not in (
                blpapi.Event.SUBSCRIPTION_STATUS,
                blpapi.Event.SUBSCRIPTION_DATA,
            ):
                continue
            event_type_name = (
                "SUBSCRIPTION_DATA"
                if event_type == blpapi.Event.SUBSCRIPTION_DATA
                else "SUBSCRIPTION_STATUS"
            )
            for msg in event:
                if len(messages) >= max_messages:
                    break
                correlation_id_str = None
                try:
                    correlation_ids = list(msg.correlationIds())
                    if correlation_ids:
                        correlation_id_str = str(correlation_ids[0].value())
                except Exception:  # noqa: BLE001
                    correlation_id_str = None
                messages.append(
                    RawMktdataMessage(
                        event_type=event_type_name,
                        message_type=_safe_str(msg.messageType()) or "",
                        correlation_id=correlation_id_str,
                        fields=_message_top_level_fields(msg),
                        raw_dump=_safe_str(msg),
                    )
                )
        return messages, None
    finally:
        if subscriptions is not None:
            try:
                session.unsubscribe(subscriptions)
            except Exception:  # noqa: BLE001 -- cleanup must never mask evidence already captured
                pass
        try:
            session.stop()
        except Exception:  # noqa: BLE001
            pass


def _classify_mktdata_message(event_type: str, message_type: str) -> str:
    """Bucket one captured message by Bloomberg's own event/message type text.

    `event_type` is Bloomberg's own `blpapi.Event` kind; a `SUBSCRIPTION_DATA`
    event is always `data_received`. Otherwise this is a literal substring
    match against Bloomberg's own `msg.messageType()` text -- the same marker
    idiom stage 2's `_is_marker_relevant` already uses -- never a semantic
    read of a field value.
    """

    if event_type == "SUBSCRIPTION_DATA":
        return "data_received"
    for marker, category in _MKTDATA_STATUS_CATEGORY_MARKERS:
        if marker in message_type:
            return category
    return "subscription_status_unclassified"


def run_mktdata_subscription(
    topic: str,
    fields: tuple[str, ...] = (),
    max_messages: int = DEFAULT_MKTDATA_MESSAGE_CAP,
    timeout_seconds: float = DEFAULT_MKTDATA_TIMEOUT_SECONDS,
    session_runner=None,
) -> MktdataSubscriptionReport:
    """Run one bounded, read-only `//blp/mktdata` subscription discovery probe.

    Issue #179's remaining hypothesis: is the operator-confirmed VCUB cell
    ticker exposed as a market-data/calculated-data *subscription* topic even
    though stage 4's `ReferenceDataRequest` route does not expose it?
    `topic` and `fields` are exactly what the operator supplied -- this
    function invents neither an identifier nor a field mnemonic. If `fields`
    is empty, the subscription is sent field-agnostic, so whatever
    Bloomberg's own default field set for the topic is (if it has one) is
    what gets recorded, rather than this repo guessing a field list. If
    Bloomberg's own `subscribe()` call rejects that -- e.g. because its API
    genuinely requires an explicit field list -- that rejection is captured
    verbatim as `subscription_failure`, which is exactly the feasibility
    delta Issue #179 asked this probe to report rather than paper over with
    an invented list.

    Every field name and value below is Bloomberg's own, verbatim, sanitized
    through `sanitize_external_text`; none is renamed, reordered by apparent
    relevance, or promoted to "the vol" merely because it resembles a number
    Eddy already saw on the VCUB grid. `session_runner` defaults to
    `_run_mktdata_subscription_session` and is the seam deterministic
    offline tests inject through -- mirroring `hunt_vol_fields`'s
    `send_request` seam elsewhere in this file.

    Raises `ValueError` if `topic` is blank (no topic is ever guessed) or if
    `max_messages`/`timeout_seconds` is not positive (a cap or timeout of
    zero would forbid capturing any evidence at all, not bound it).
    """

    if not topic or not topic.strip():
        raise ValueError(
            "run_mktdata_subscription needs an operator-supplied topic/security -- "
            "none is guessed by this script"
        )
    if max_messages <= 0:
        raise ValueError("max_messages must be positive -- a cap of zero forbids any evidence")
    if not isfinite(timeout_seconds) or timeout_seconds <= 0:
        # `nan <= 0` is `False`, so a non-finite value must be checked
        # explicitly -- and `inf` would otherwise reach
        # `int(remaining * 1000)` in the session loop and raise
        # `OverflowError` there instead of failing here with a clear message.
        raise ValueError(
            "timeout_seconds must be a finite, positive number -- a hard timeout cannot "
            "be zero, negative, NaN or infinite"
        )

    session_runner = session_runner or _run_mktdata_subscription_session
    raw_messages, subscribe_error = session_runner(topic, fields, max_messages, timeout_seconds)

    # Defense in depth: this script's own hard cap, even if a session runner
    # (real or injected) somehow returned more messages than it was asked for.
    raw_messages = list(raw_messages)[:max_messages]

    events: list[MktdataMessageEvent] = []
    for sequence, raw in enumerate(raw_messages, start=1):
        events.append(
            MktdataMessageEvent(
                sequence=sequence,
                event_type=raw.event_type,
                message_type=sanitize_external_text(raw.message_type) or "",
                category=_classify_mktdata_message(raw.event_type, raw.message_type or ""),
                correlation_id=sanitize_external_text(raw.correlation_id),
                fields=tuple(
                    MktdataField(name=name, value=sanitize_external_text(value))
                    for name, value in raw.fields
                ),
                raw_message_dump=sanitize_external_text(raw.raw_dump),
            )
        )

    categories = [event.category for event in events]
    if subscribe_error is not None:
        # A caught subscribe-time exception can stringify to "" (an empty
        # message is still a rejection); `is not None` -- not truthiness --
        # is what distinguishes "no rejection happened" from "one happened
        # and had nothing to say", so an empty string is never misread as
        # `timeout_no_data`.
        outcome = "subscription_failure"
    elif "subscription_failure" in categories:
        outcome = "subscription_failure"
    elif "subscription_terminated" in categories:
        outcome = "subscription_terminated"
    elif "data_received" in categories:
        outcome = "data_received"
    elif "subscription_started" in categories:
        outcome = "subscription_started"
    elif not events:
        outcome = "timeout_no_data"
    else:
        outcome = "subscription_status_unclassified"

    return MktdataSubscriptionReport(
        topic=topic,
        fields_requested=tuple(fields),
        message_cap=max_messages,
        timeout_seconds=timeout_seconds,
        outcome=outcome,
        subscribe_error=sanitize_external_text(subscribe_error),
        events=tuple(events),
        blocker_note=(
            "This probe assigns no verdict. Whether any recorded field is the OVME proxy "
            "vol, and whether //blp/mktdata exposes anything //blp/refdata's stage 4 did "
            "not, is Eddy's judgment on this evidence -- never a claim this script makes."
        ),
    )


# --- stage 6: //blp/instruments lookup discovery probe -------------------------------

_INSTRUMENTS_SERVICE = "//blp/instruments"

# A lookup service should expose very few operations; this is a sanity cap
# against an unexpectedly large schema, not a brute-force search -- the same
# no-blind-sweep principle as stage 4's MAX_CANDIDATE_PROBES.
MAX_LOOKUP_OPERATION_ATTEMPTS = 5


@dataclass(frozen=True)
class InstrumentLookupAttempt:
    """One (operation, request element) pair this run's own schema
    introspection confirmed, and what Bloomberg returned for it."""

    operation_name: str
    request_element_used: str
    status: str  # "sent" | "error"
    error: str | None
    raw_response_dump: str | None
    top_level_fields: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True)
class InstrumentLookupReport:
    service_uri: str
    query: str
    service_opened: bool
    open_error: str | None
    full_schema_dump: str | None
    operations_found: tuple[str, ...]
    capability_confirmed: bool
    attempts: tuple[InstrumentLookupAttempt, ...]
    blocker_note: str


def probe_instrument_lookup(
    query: str,
    service_uri: str = _INSTRUMENTS_SERVICE,
    discover_service_fn=None,
    send_request=_send_request,
) -> InstrumentLookupReport:
    """Audit `service_uri`'s own schema, then query it -- only if this run's own
    introspection confirms a usable lookup operation.

    Issue #179's remaining hypothesis after stages 4/5: is there a Bloomberg
    *instrument-resolution/lookup* route for the operator-confirmed VCUB
    cell ticker at all, distinct from the reference-data and subscription
    routes already probed? `//blp/instruments` is Bloomberg's own
    documented instrument-lookup service endpoint -- referencing it by name
    is no more a guess than this file already referencing
    `//blp/refdata`/`//blp/apiflds`/`//blp/mktdata` by name elsewhere. What
    this function refuses to guess is *which request and field* to send
    `query` through: it reuses
    `bloomberg_curve_discovery_probe.discover_service`'s own schema
    introspection (the same helper stage 2 already uses, gated the same
    way) to read every operation this run's own `//blp/instruments`
    actually reports, and sends `query` only through an operation whose
    *own request schema* -- not this script's memory of Bloomberg's
    documentation -- exposes a top-level `STRING` element. No yellow key,
    suffix, or field mnemonic is invented anywhere in this function.

    If the service does not open, or opens but no operation exposes a
    usable top-level string element, `capability_confirmed` is `False` and
    `attempts` is empty -- an explicit, reported "no suitable lookup
    operation" outcome, never a fallback guess. Every returned
    security/parse-key/description Bloomberg sends back is recorded
    verbatim (raw dump) and via a best-effort field walk, both sanitized;
    none is promoted, renamed, or asserted to be the query's resolved form.

    Raises `ValueError` if `query` is blank (no query is ever guessed) or
    if an unexpectedly large operation list would turn this into a blind
    sweep, and `ImportError` if `blpapi` is not installed.
    """

    if not query or not query.strip():
        raise ValueError(
            "probe_instrument_lookup needs an operator-supplied query -- "
            "none is guessed by this script"
        )

    discover_service_fn = discover_service_fn or discover_service
    evidence = discover_service_fn(service_uri)

    if not evidence.opened:
        return InstrumentLookupReport(
            service_uri=service_uri,
            query=query,
            service_opened=False,
            open_error=sanitize_external_text(evidence.open_error),
            full_schema_dump=sanitize_external_text(evidence.full_schema_dump),
            operations_found=(),
            capability_confirmed=False,
            attempts=(),
            blocker_note=(
                f"{service_uri} did not open on this run ({evidence.open_error!r}) -- no "
                "lookup operation could be probed. This is the explicit feasibility "
                "answer Issue #179 asked for, not a fallback to a guessed request."
            ),
        )

    operations_found = tuple(op.name for op in evidence.operations)
    usable_operations = [op for op in evidence.operations if op.candidate_string_elements]

    if not usable_operations:
        return InstrumentLookupReport(
            service_uri=service_uri,
            query=query,
            service_opened=True,
            open_error=None,
            full_schema_dump=sanitize_external_text(evidence.full_schema_dump),
            operations_found=operations_found,
            capability_confirmed=False,
            attempts=(),
            blocker_note=(
                f"This run's own introspection of {service_uri} found "
                f"{len(operations_found)} operation(s) ({', '.join(operations_found) or 'none'}) "
                "but none exposed a top-level STRING request element this probe could use "
                "without guessing one; nothing was queried. Read full_schema_dump by hand "
                "before concluding this workstation's Desktop API genuinely has no lookup "
                "route -- the structured operation walk here is best-effort, exactly like "
                "stage 2's own field search."
            ),
        )

    if len(usable_operations) > MAX_LOOKUP_OPERATION_ATTEMPTS:
        raise ValueError(
            f"{service_uri} reported {len(usable_operations)} operations with a usable "
            f"string element, above the {MAX_LOOKUP_OPERATION_ATTEMPTS}-attempt sanity cap "
            "-- refusing rather than probing all of them blind"
        )

    attempts: list[InstrumentLookupAttempt] = []
    for operation in usable_operations:
        element = operation.candidate_string_elements[0]

        def _configure(request, _element=element, _query=query) -> None:
            try:
                request.set(_element, _query)
            except Exception:  # noqa: BLE001
                request.append(_element, _query)

        raw_messages: list = []

        def _collect(message, _raw=raw_messages) -> None:
            _raw.append(message)

        try:
            send_request(
                service_uri=service_uri,
                request_name=operation.name,
                configure=_configure,
                collect=_collect,
                context=f"{operation.name}({element}={query!r})",
            )
        except (RuntimeError, ImportError) as exc:
            attempts.append(
                InstrumentLookupAttempt(
                    operation_name=operation.name,
                    request_element_used=element,
                    status="error",
                    error=sanitize_external_text(str(exc)),
                    raw_response_dump=None,
                    top_level_fields=(),
                )
            )
            continue

        if not raw_messages:
            # The operation was sent to, cleanly -- it just answered with no
            # messages. Recorded explicitly rather than silently vanishing.
            attempts.append(
                InstrumentLookupAttempt(
                    operation_name=operation.name,
                    request_element_used=element,
                    status="sent",
                    error=None,
                    raw_response_dump=None,
                    top_level_fields=(),
                )
            )
            continue

        for message in raw_messages:
            attempts.append(
                InstrumentLookupAttempt(
                    operation_name=operation.name,
                    request_element_used=element,
                    status="sent",
                    error=None,
                    raw_response_dump=sanitize_external_text(_safe_str(message)),
                    top_level_fields=tuple(
                        (name, sanitize_external_text(value))
                        for name, value in _message_top_level_fields(message)
                    ),
                )
            )

    return InstrumentLookupReport(
        service_uri=service_uri,
        query=query,
        service_opened=True,
        open_error=None,
        full_schema_dump=sanitize_external_text(evidence.full_schema_dump),
        operations_found=operations_found,
        capability_confirmed=True,
        attempts=tuple(attempts),
        blocker_note=(
            "This probe assigns no verdict and promotes no result. Every returned "
            "security/parse-key/description above is Bloomberg's own text, verbatim and "
            "sanitized; which one (if any) resolves the operator-supplied query is Eddy's "
            "judgment on this evidence, not a claim this script makes."
        ),
    )


# --- report -------------------------------------------------------------------------


@dataclass(frozen=True)
class KproxyResolution:
    """`Kproxy = KATM + (KY - FY)`, computed only when both parts are real and comparable."""

    status: str  # "resolved" | "unresolved"
    yield_field: str | None
    ky_minus_fy: float | None
    katm: float | None
    katm_provenance: str | None
    kproxy: float | None
    reason: str | None


def resolve_kproxy(
    moneyness_by_field: dict[str, float],
    katm: float | None,
    katm_unit_matches_yield_field: bool,
    katm_provenance: str | None,
) -> KproxyResolution:
    """Add `KATM` to `KY - FY` only when doing so is arithmetically honest.

    Refuses when no field produced both legs, when more than one field did
    (two candidate conversions that disagree are a stop condition, not a
    thing to average), when no `KATM` was supplied, and when the operator
    has not explicitly confirmed `KATM` is quoted in the same unit as the
    yield field that produced the moneyness. No unit is inferred, and no
    scaling factor is ever applied.
    """

    if not moneyness_by_field:
        return KproxyResolution(
            status="unresolved",
            yield_field=None,
            ky_minus_fy=None,
            katm=katm,
            katm_provenance=katm_provenance,
            kproxy=None,
            reason="no candidate yield field returned both FY and KY, so KY - FY is unknown",
        )
    if len(moneyness_by_field) > 1:
        return KproxyResolution(
            status="unresolved",
            yield_field=None,
            ky_minus_fy=None,
            katm=katm,
            katm_provenance=katm_provenance,
            kproxy=None,
            reason=(
                "more than one candidate yield field returned both FY and KY "
                f"({', '.join(sorted(moneyness_by_field))}); which conversion OVME uses is "
                "a Bloomberg-evidence question, not something this script picks"
            ),
        )

    field, ky_minus_fy = next(iter(moneyness_by_field.items()))
    if katm is not None and not isfinite(katm):
        # A non-finite KATM is not a rate. It is refused outright, and is
        # not echoed into the report, so the JSON never carries a
        # non-standard numeric token.
        return KproxyResolution(
            status="unresolved",
            yield_field=field,
            ky_minus_fy=ky_minus_fy,
            katm=None,
            katm_provenance=katm_provenance,
            kproxy=None,
            reason="the supplied KATM is not a finite number, so Kproxy cannot be formed",
        )
    if katm is None:
        reason = "no KATM was supplied, so Kproxy cannot be formed"
    elif not katm_unit_matches_yield_field:
        reason = (
            f"KATM was supplied but not confirmed to be quoted in the same unit as {field}; "
            "pass --katm-unit-matches-yield-field only if that has actually been checked"
        )
    else:
        return KproxyResolution(
            status="resolved",
            yield_field=field,
            ky_minus_fy=ky_minus_fy,
            katm=katm,
            katm_provenance=katm_provenance,
            kproxy=katm + ky_minus_fy,
            reason=None,
        )

    return KproxyResolution(
        status="unresolved",
        yield_field=field,
        ky_minus_fy=ky_minus_fy,
        katm=katm,
        katm_provenance=katm_provenance,
        kproxy=None,
        reason=reason,
    )


def _probe_to_dict(probe) -> dict:
    data = dict(probe.__dict__)
    if "overrides" in data:
        data["overrides"] = [list(pair) for pair in data["overrides"]]
    return data


def build_report(
    *,
    generated_at: str,
    coordinate: ProxyCoordinate,
    field_search: VolFieldSearchReport | None,
    yield_conversion: YieldConversionReport | None,
    candidate_probes: tuple[CandidateValueProbe, ...],
    candidate_probe_error: str | None,
    kproxy: KproxyResolution,
    mktdata: MktdataSubscriptionReport | None = None,
    mktdata_error: str | None = None,
    instrument_lookup: InstrumentLookupReport | None = None,
    instrument_lookup_error: str | None = None,
) -> dict:
    return {
        "generated_at": generated_at,
        "issue": "#179 VCUB field hunter -- OVME bond-option proxy vol source discovery",
        "proxy_coordinate": dict(coordinate.__dict__),
        "field_search": (
            None
            if field_search is None
            else {
                "search_capability_confirmed": field_search.search_capability_confirmed,
                "apiflds_open_error": field_search.apiflds_open_error,
                "search_terms": list(field_search.search_terms),
                "relevance_markers": list(VOL_RELEVANCE_MARKERS),
                "term_outcomes": [
                    {
                        "term": outcome.term,
                        "status": outcome.status,
                        "error": outcome.error,
                        "structured_parse_ok": outcome.structured_parse_ok,
                        "raw_response_dump": outcome.raw_response_dump,
                        "records": [dict(record.__dict__) for record in outcome.records],
                    }
                    for outcome in field_search.term_outcomes
                ],
                "marker_relevant_mnemonics": list(field_search.marker_relevant_mnemonics),
                "documentation_expansion": field_search.documentation_expansion,
                "blocker_note": field_search.blocker_note,
            }
        ),
        "yield_conversion": (
            None
            if yield_conversion is None
            else {
                "identifier": yield_conversion.identifier,
                "candidate_fields": list(yield_conversion.candidate_fields),
                "documentation_expansion": yield_conversion.documentation_expansion,
                "probes": [_probe_to_dict(p) for p in yield_conversion.probes],
                "ky_minus_fy_by_field": yield_moneyness_by_field(yield_conversion),
                "blocker_note": yield_conversion.blocker_note,
            }
        ),
        "candidate_value_probes": [_probe_to_dict(p) for p in candidate_probes],
        "candidate_value_probe_error": candidate_probe_error,
        "kproxy": dict(kproxy.__dict__),
        "mktdata_subscription": (
            None
            if mktdata is None
            else {
                "topic": mktdata.topic,
                "fields_requested": list(mktdata.fields_requested),
                "message_cap": mktdata.message_cap,
                "timeout_seconds": mktdata.timeout_seconds,
                "outcome": mktdata.outcome,
                "subscribe_error": mktdata.subscribe_error,
                "events": [
                    {
                        "sequence": event.sequence,
                        "event_type": event.event_type,
                        "message_type": event.message_type,
                        "category": event.category,
                        "correlation_id": event.correlation_id,
                        "fields": [dict(field.__dict__) for field in event.fields],
                        "raw_message_dump": event.raw_message_dump,
                    }
                    for event in mktdata.events
                ],
                "blocker_note": mktdata.blocker_note,
            }
        ),
        "mktdata_subscription_error": mktdata_error,
        "instrument_lookup": (
            None
            if instrument_lookup is None
            else {
                "service_uri": instrument_lookup.service_uri,
                "query": instrument_lookup.query,
                "service_opened": instrument_lookup.service_opened,
                "open_error": instrument_lookup.open_error,
                "full_schema_dump": instrument_lookup.full_schema_dump,
                "operations_found": list(instrument_lookup.operations_found),
                "capability_confirmed": instrument_lookup.capability_confirmed,
                "attempts": [
                    {
                        "operation_name": attempt.operation_name,
                        "request_element_used": attempt.request_element_used,
                        "status": attempt.status,
                        "error": attempt.error,
                        "raw_response_dump": attempt.raw_response_dump,
                        "top_level_fields": [list(pair) for pair in attempt.top_level_fields],
                    }
                    for attempt in instrument_lookup.attempts
                ],
                "blocker_note": instrument_lookup.blocker_note,
            }
        ),
        "instrument_lookup_error": instrument_lookup_error,
        "verdict": None,
        "verdict_note": (
            "This script does not assign Issue #179's DIRECT ROUTE FOUND / PARTIAL / "
            "NO NON-VISUAL ROUTE FOUND verdict. It reports what Bloomberg returned, "
            "verbatim, including entitlement and unsupported-field errors; the verdict "
            "is Eddy's judgment on this evidence."
        ),
    }


def render_markdown(data: dict) -> str:
    lines: list[str] = ["# Bloomberg VCUB field hunter (Issue #179)", ""]
    lines.append(f"Generated at: {data['generated_at']}")
    lines.append("")

    coordinate = data["proxy_coordinate"]
    lines.append("## Proxy coordinate (offline, from the supplied case)")
    lines.append("")
    lines.append("| Item | Value |")
    lines.append("|---|---|")
    for key, value in coordinate.items():
        lines.append(f"| {key} | {value} |")
    lines.append("")

    field_search = data["field_search"]
    lines.append("## Field metadata discovery (//blp/apiflds)")
    lines.append("")
    if field_search is None:
        lines.append("(not run)")
        lines.append("")
    else:
        lines.append(
            f"Search capability confirmed this run: {field_search['search_capability_confirmed']}"
        )
        if field_search["apiflds_open_error"]:
            lines.append(f"apiflds open error: {field_search['apiflds_open_error']}")
        lines.append(f"Search terms: {', '.join(field_search['search_terms'])}")
        lines.append(f"Relevance markers: {', '.join(field_search['relevance_markers'])}")
        lines.append("")
        for outcome in field_search["term_outcomes"]:
            relevant = sum(1 for r in outcome["records"] if r["marker_relevant"])
            lines.append(
                f"### {outcome['term']!r} -> {outcome['status']} "
                f"({len(outcome['records'])} record(s), {relevant} marker-relevant)"
            )
            if outcome["error"]:
                lines.append(f"Error: {outcome['error']}")
            for record in outcome["records"]:
                if not record["marker_relevant"]:
                    continue
                label = record["mnemonic"] or record["field_id"] or "?"
                description = f" -- {record['description']}" if record["description"] else ""
                lines.append(f"- `{label}`{description}")
            if not outcome["records"] and outcome["raw_response_dump"]:
                lines.append("Raw response dump:")
                lines.append("```")
                lines.append(outcome["raw_response_dump"])
                lines.append("```")
            lines.append("")
        lines.append(
            "Marker-relevant mnemonics: "
            + (", ".join(field_search["marker_relevant_mnemonics"]) or "(none)")
        )
        lines.append("")
        if field_search["blocker_note"]:
            lines.append(field_search["blocker_note"])
            lines.append("")
        if field_search["documentation_expansion"]:
            lines.append("<details><summary>Field documentation expansion</summary>")
            lines.append("")
            lines.append("```json")
            lines.append(
                json.dumps(field_search["documentation_expansion"], indent=2, ensure_ascii=False)
            )
            lines.append("```")
            lines.append("")
            lines.append("</details>")
            lines.append("")

    yield_conversion = data["yield_conversion"]
    lines.append("## FY / KY yield conversion (//blp/refdata, Bloomberg does the conversion)")
    lines.append("")
    if yield_conversion is None:
        lines.append("(not run)")
        lines.append("")
    else:
        lines.append(f"Identifier: `{yield_conversion['identifier']}`")
        lines.append(f"Candidate fields: {', '.join(yield_conversion['candidate_fields'])}")
        lines.append("")
        lines.append(
            "| Role | Field | Price override | Settle override | Status | Value / detail |"
        )
        lines.append("|---|---|---|---|---|---|")
        for probe in yield_conversion["probes"]:
            price = (
                f"{probe['price_override_field']}={probe['price_override_value']}"
                if probe["price_override_field"]
                else "-"
            )
            settle = (
                f"{probe['settle_override_field']}={probe['settle_override_value']}"
                if probe["settle_override_field"]
                else "-"
            )
            detail = probe["value"] if probe["status"] == "returned" else (probe["detail"] or "")
            lines.append(
                f"| {probe['role']} | `{probe['field']}` | {price} | {settle} | "
                f"{probe['status']} | {detail} |"
            )
        lines.append("")
        for field, moneyness in yield_conversion["ky_minus_fy_by_field"].items():
            lines.append(f"- `{field}`: KY - FY = {moneyness} (in that field's own unit)")
        lines.append("")
        if yield_conversion["blocker_note"]:
            lines.append(yield_conversion["blocker_note"])
            lines.append("")

    lines.append("## Candidate value probes (operator-supplied identifiers only)")
    lines.append("")
    if data["candidate_value_probe_error"]:
        lines.append(f"Error: {data['candidate_value_probe_error']}")
        lines.append("")
    if not data["candidate_value_probes"]:
        lines.append("(none supplied)")
        lines.append("")
    else:
        lines.append("| Security | Field | Overrides | Status | Value / detail |")
        lines.append("|---|---|---|---|---|")
        for probe in data["candidate_value_probes"]:
            overrides = ", ".join(f"{f}={v}" for f, v in probe["overrides"]) or "-"
            detail = probe["value"] if probe["status"] == "returned" else (probe["detail"] or "")
            lines.append(
                f"| `{probe['security']}` | `{probe['field']}` | {overrides} | "
                f"{probe['status']} | {detail} |"
            )
        lines.append("")

    kproxy = data["kproxy"]
    lines.append("## Kproxy = KATM + (KY - FY)")
    lines.append("")
    lines.append(f"Status: {kproxy['status']}")
    for key in ("yield_field", "ky_minus_fy", "katm", "katm_provenance", "kproxy", "reason"):
        if kproxy[key] is not None:
            lines.append(f"- {key}: {kproxy[key]}")
    lines.append("")

    mktdata = data["mktdata_subscription"]
    lines.append("## //blp/mktdata subscription discovery probe (operator-supplied topic only)")
    lines.append("")
    if data["mktdata_subscription_error"]:
        lines.append(f"Error: {data['mktdata_subscription_error']}")
        lines.append("")
    if mktdata is None:
        lines.append("(not run)")
        lines.append("")
    else:
        lines.append(f"Topic: `{mktdata['topic']}`")
        requested_fields = ", ".join(f"`{f}`" for f in mktdata["fields_requested"])
        lines.append("Fields requested: " + (requested_fields or "(none -- field-agnostic)"))
        lines.append(
            f"Message cap: {mktdata['message_cap']} -- Timeout: {mktdata['timeout_seconds']}s"
        )
        lines.append(f"Outcome: **{mktdata['outcome']}**")
        if mktdata["subscribe_error"]:
            lines.append(f"Subscribe error: {mktdata['subscribe_error']}")
        lines.append("")
        if not mktdata["events"]:
            lines.append("(no subscription status/data message captured before timeout)")
            lines.append("")
        else:
            lines.append("| # | Event type | Message type | Category | Fields |")
            lines.append("|---|---|---|---|---|")
            for event in mktdata["events"]:
                fields = (
                    ", ".join(f"{f['name']}={f['value']}" for f in event["fields"]) or "(none)"
                )
                lines.append(
                    f"| {event['sequence']} | {event['event_type']} | {event['message_type']} | "
                    f"{event['category']} | {fields} |"
                )
            lines.append("")
        lines.append(mktdata["blocker_note"])
        lines.append("")

    lookup = data["instrument_lookup"]
    lines.append("## //blp/instruments lookup discovery probe (operator-supplied query only)")
    lines.append("")
    if data["instrument_lookup_error"]:
        lines.append(f"Error: {data['instrument_lookup_error']}")
        lines.append("")
    if lookup is None:
        lines.append("(not run)")
        lines.append("")
    else:
        lines.append(f"Service: `{lookup['service_uri']}` -- Query: `{lookup['query']}`")
        lines.append(f"Service opened this run: {lookup['service_opened']}")
        if lookup["open_error"]:
            lines.append(f"Open error: {lookup['open_error']}")
        found_operations = ", ".join(f"`{o}`" for o in lookup["operations_found"])
        lines.append("Operations found: " + (found_operations or "(none)"))
        lines.append(f"Lookup capability confirmed this run: {lookup['capability_confirmed']}")
        lines.append("")
        if not lookup["attempts"]:
            lines.append("(no operation was queried)")
            lines.append("")
        else:
            lines.append("| Operation | Element used | Status | Fields returned |")
            lines.append("|---|---|---|---|")
            for attempt in lookup["attempts"]:
                fields = (
                    ", ".join(f"{n}={v}" for n, v in attempt["top_level_fields"])
                    or (attempt["error"] or "(none)")
                )
                lines.append(
                    f"| `{attempt['operation_name']}` | `{attempt['request_element_used']}` | "
                    f"{attempt['status']} | {fields} |"
                )
            lines.append("")
            for attempt in lookup["attempts"]:
                if attempt["raw_response_dump"]:
                    lines.append(
                        f"<details><summary>Raw response -- "
                        f"{attempt['operation_name']}</summary>"
                    )
                    lines.append("")
                    lines.append("```")
                    lines.append(attempt["raw_response_dump"])
                    lines.append("```")
                    lines.append("")
                    lines.append("</details>")
                    lines.append("")
        lines.append(lookup["blocker_note"])
        lines.append("")
        if lookup["full_schema_dump"]:
            lines.append("<details><summary>Full //blp/instruments schema dump</summary>")
            lines.append("")
            lines.append("```")
            lines.append(lookup["full_schema_dump"])
            lines.append("```")
            lines.append("")
            lines.append("</details>")
            lines.append("")

    lines.append("## Verdict")
    lines.append("")
    lines.append(data["verdict_note"])
    lines.append("")
    return "\n".join(lines)


def render_json(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def write_report(data: dict, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / MARKDOWN_FILENAME
    json_path = output_dir / JSON_FILENAME
    markdown_path.write_text(render_markdown(data), encoding="utf-8")
    json_path.write_text(render_json(data), encoding="utf-8")
    return markdown_path, json_path


# --- CLI ----------------------------------------------------------------------------


def _finite_float(raw: str) -> float:
    """`float`, minus the `nan`/`inf` spellings `float()` itself accepts."""

    value = float(raw)
    if not isfinite(value):
        raise argparse.ArgumentTypeError(f"expected a finite number, got {raw!r}")
    return value


def _parse_override(raw: str) -> tuple[str, str]:
    field, separator, value = raw.partition("=")
    if not separator or not field.strip() or not value.strip():
        raise ValueError(f"override must be FIELD=VALUE, got {raw!r}")
    return field.strip(), value.strip()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Bounded read-only Bloomberg VCUB proxy-vol discovery probe (Issue #179). "
            "Reports evidence; assigns no verdict."
        )
    )
    parser.add_argument(
        "--case",
        required=True,
        help="Path to one already-valid standalone option case JSON (the Workbench's own format)",
    )
    parser.add_argument(
        "--skip-field-search",
        action="store_true",
        help="Skip stage 2 (//blp/apiflds metadata search) -- useful once it has been captured",
    )
    parser.add_argument(
        "--skip-yield-conversion",
        action="store_true",
        help="Skip stage 3 (FY/KY conversion probes)",
    )
    parser.add_argument(
        "--yield-field",
        action="append",
        default=None,
        dest="yield_fields",
        help=(
            "Candidate price->yield mnemonic; repeatable. Default: "
            + ", ".join(DEFAULT_YIELD_FIELD_CANDIDATES)
        ),
    )
    parser.add_argument(
        "--candidate-security",
        action="append",
        default=None,
        dest="candidate_securities",
        help="Candidate Bloomberg identifier for stage 4; repeatable. Never guessed by this tool",
    )
    parser.add_argument(
        "--candidate-field",
        action="append",
        default=None,
        dest="candidate_fields",
        help="Candidate Bloomberg mnemonic for stage 4; repeatable",
    )
    parser.add_argument(
        "--candidate-override",
        action="append",
        default=None,
        dest="candidate_overrides",
        help="FIELD=VALUE override sent with every stage 4 probe; repeatable",
    )
    parser.add_argument(
        "--katm",
        type=_finite_float,
        default=None,
        help="Observed reference-swaption ATM par rate for this coordinate",
    )
    parser.add_argument(
        "--katm-provenance",
        default=None,
        help="Where --katm came from (Bloomberg function/field/screen), recorded verbatim",
    )
    parser.add_argument(
        "--katm-unit-matches-yield-field",
        action="store_true",
        help=(
            "Confirm --katm is quoted in the same unit as the yield field that produced "
            "KY - FY. Without this, Kproxy stays unresolved rather than being guessed"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=f"Where to write the report (default: ./{DEFAULT_OUTPUT_DIRNAME})",
    )
    parser.add_argument(
        "--mktdata-security",
        default=None,
        help=(
            "Stage 5: operator-supplied //blp/mktdata topic/security to subscribe to "
            "read-only (e.g. 'USSNAC4 Curncy'). Never guessed by this tool -- omit to skip "
            "stage 5 entirely"
        ),
    )
    parser.add_argument(
        "--mktdata-field",
        action="append",
        default=None,
        dest="mktdata_fields",
        help=(
            "Field mnemonic to request in the stage 5 subscription; repeatable. Omitted "
            "entirely means a field-agnostic subscription -- no field name is guessed "
            "either way"
        ),
    )
    parser.add_argument(
        "--mktdata-timeout",
        type=_finite_float,
        default=DEFAULT_MKTDATA_TIMEOUT_SECONDS,
        help=f"Stage 5 hard timeout in seconds (default: {DEFAULT_MKTDATA_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--mktdata-max-messages",
        type=int,
        default=DEFAULT_MKTDATA_MESSAGE_CAP,
        help=f"Stage 5 hard message-count cap (default: {DEFAULT_MKTDATA_MESSAGE_CAP})",
    )
    parser.add_argument(
        "--lookup-security",
        default=None,
        help=(
            "Stage 6: operator-supplied query for the //blp/instruments lookup discovery "
            "probe (e.g. 'USSNAC4'). Never guessed by this tool -- omit to skip stage 6 "
            "entirely"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        overrides = tuple(_parse_override(raw) for raw in (args.candidate_overrides or []))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        request = build_request_from_standalone_option_case(Path(args.case).read_text("utf-8"))
        coordinate = derive_proxy_coordinate(request)
    except (OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print("Shiori Bloomberg VCUB field hunter (Issue #179)")
    print(f"  currency        : {coordinate.currency}")
    print(f"  TB (maturity)   : {coordinate.bond_maturity_date}")
    print(f"  TF (fwd settle) : {coordinate.forward_settlement_date}")
    print(f"  Texp            : {coordinate.texp_years:.6f}y ({coordinate.texp_days} days)")
    print(f"  Ttenor          : {coordinate.ttenor_years:.6f}y ({coordinate.ttenor_days} days)")
    print(f"  year fraction   : {coordinate.year_fraction_convention}")
    print(f"  forward clean   : {coordinate.forward_clean_price_per_100}")
    print(f"  strike          : {coordinate.strike_price_per_100}")
    print("")

    field_search = None
    yield_conversion = None
    candidate_probes: tuple[CandidateValueProbe, ...] = ()
    candidate_probe_error: str | None = None

    try:
        if not args.skip_field_search:
            field_search = hunt_vol_fields()
        if not args.skip_yield_conversion:
            yield_conversion = probe_yield_conversion(
                coordinate,
                tuple(args.yield_fields) if args.yield_fields else DEFAULT_YIELD_FIELD_CANDIDATES,
            )
    except ImportError as exc:
        print(
            "error: blpapi is not installed -- run this on a Bloomberg-networked "
            f"workstation with Bloomberg's official blpapi package installed ({exc})",
            file=sys.stderr,
        )
        return 2

    if args.candidate_securities and args.candidate_fields:
        try:
            candidate_probes = probe_candidate_values(
                tuple(args.candidate_securities), tuple(args.candidate_fields), overrides
            )
        except ValueError as exc:
            candidate_probe_error = str(exc)
        except ImportError as exc:
            candidate_probe_error = f"blpapi is not installed ({exc})"
    elif args.candidate_securities or args.candidate_fields:
        candidate_probe_error = (
            "stage 4 needs both --candidate-security and --candidate-field; neither is "
            "guessed from the other"
        )

    kproxy = resolve_kproxy(
        yield_moneyness_by_field(yield_conversion) if yield_conversion else {},
        args.katm,
        args.katm_unit_matches_yield_field,
        args.katm_provenance,
    )

    mktdata: MktdataSubscriptionReport | None = None
    mktdata_error: str | None = None
    if args.mktdata_security:
        try:
            mktdata = run_mktdata_subscription(
                args.mktdata_security,
                tuple(args.mktdata_fields) if args.mktdata_fields else (),
                max_messages=args.mktdata_max_messages,
                timeout_seconds=args.mktdata_timeout,
            )
        except ValueError as exc:
            mktdata_error = str(exc)
        except ImportError as exc:
            mktdata_error = f"blpapi is not installed ({exc})"

    instrument_lookup: InstrumentLookupReport | None = None
    instrument_lookup_error: str | None = None
    if args.lookup_security:
        try:
            instrument_lookup = probe_instrument_lookup(args.lookup_security)
        except ValueError as exc:
            instrument_lookup_error = str(exc)
        except ImportError as exc:
            instrument_lookup_error = f"blpapi is not installed ({exc})"

    data = build_report(
        generated_at=_utc_now(),
        coordinate=coordinate,
        field_search=field_search,
        yield_conversion=yield_conversion,
        candidate_probes=candidate_probes,
        candidate_probe_error=candidate_probe_error,
        kproxy=kproxy,
        mktdata=mktdata,
        mktdata_error=mktdata_error,
        instrument_lookup=instrument_lookup,
        instrument_lookup_error=instrument_lookup_error,
    )
    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd() / DEFAULT_OUTPUT_DIRNAME
    markdown_path, json_path = write_report(data, output_dir)

    if field_search is not None:
        print(
            "Marker-relevant vol mnemonics: "
            + (", ".join(field_search.marker_relevant_mnemonics) or "(none)")
        )
    if yield_conversion is not None:
        for field, moneyness in yield_moneyness_by_field(yield_conversion).items():
            print(f"KY - FY via {field}: {moneyness} (that field's own unit)")
    if candidate_probe_error:
        print(f"Candidate probe error: {candidate_probe_error}")
    print(f"Kproxy: {kproxy.status}" + (f" -- {kproxy.reason}" if kproxy.reason else ""))
    if mktdata is not None:
        print(f"mktdata subscription ({mktdata.topic!r}): {mktdata.outcome}")
    if mktdata_error:
        print(f"mktdata subscription error: {mktdata_error}")
    if instrument_lookup is not None:
        print(
            f"instrument lookup ({instrument_lookup.query!r}): "
            f"capability_confirmed={instrument_lookup.capability_confirmed}, "
            f"{len(instrument_lookup.attempts)} attempt(s)"
        )
    if instrument_lookup_error:
        print(f"instrument lookup error: {instrument_lookup_error}")
    print("")
    print("Full report (paste back or attach either file):")
    print(f"  {markdown_path.resolve()}")
    print(f"  {json_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
