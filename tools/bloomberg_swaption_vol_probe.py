"""Bloomberg USD swaption normal-vol read-only route probe (Market Data
Reconnection Phase 1).

Bounded, read-only workstation diagnostic CLI -- **not** part of the
production pricing, vol, ingestion or workbench path, and never imported by
any of them. This run writes no vol surface, changes no production field
mapping, opens no SQLite database, and calls no pricing entry point.

What "read-only" does *not* mean here, stated precisely because an auditor
will rely on it: this file deliberately **reuses** the repository's existing
probe helpers, and at *import* time those helpers pull in production modules.
``bloomberg_vcub_field_hunter`` imports
``shiori_pricing_lab.app.standalone_option_workbench``, and the shared
evidence module reaches ``bloomberg_dapi_probe``, which imports
``shiori_pricing_lab.data.bloomberg_bond_quote`` at module level. So importing
this probe loads those modules; it just never alters them and never calls a
pricing, Black-76 or persistence entry point from them. The guarantee the
phase actually needs -- no production behaviour changes, nothing under
``src/`` imports this file, no schema or field map is touched -- holds.

**The one question.** Can Shiori obtain a real USD swaption *normal*
volatility -- the first target being 1Y expiry x 5Y tenor, ATM, MID --
directly from Bloomberg's Desktop API on an already-licensed workstation,
and what does Bloomberg *itself* say about the field that returns it?

**Why a probe, and not a loader.** Issue #179 already ran the full
non-visual route hunt for the OVME *proxy* coordinate (3Mo x 4Yr, ticker
``USSNAC4 BVOL Curncy``) and resolved it ``NO NON-VISUAL ROUTE FOUND``
across ``//blp/refdata``, ``//blp/mktdata`` and ``//blp/instruments``, for
that ticker, those mnemonics and those overrides. That verdict is explicitly
*bounded*: it never tested the ATM swaption-surface coordinate this phase
targets. This probe tests that coordinate. It assigns no verdict of its
own -- whether the evidence adds up to a route is Eddy's judgment on the
report, exactly as ``tools/bloomberg_vcub_field_hunter.py`` established.

**Reuse, not a second implementation.** Every Bloomberg interaction below
goes through a helper this repository already proved live, never a new copy:

- :func:`bloomberg_curve_discovery_probe.discover_service` -- session start
  and service open/introspection (the connectivity + service-authorisation
  evidence), and the ``//blp/apiflds`` ``FieldSearchRequest.searchSpec``
  capability gate;
- :func:`bloomberg_vcub_field_hunter.hunt_vol_fields` -- the field *search*
  and its documented-override expansion, i.e. how a mnemonic and the
  expiry/tenor/strike parameters a field demands are *obtained from
  Bloomberg* rather than guessed;
- :func:`bloomberg_vcub_field_hunter.probe_candidate_values` (over
  :func:`bloomberg_dapi_probe.probe_fields`) -- the ``ReferenceDataRequest``
  value probe and its per-field ``returned`` / ``absent`` /
  ``field_exception`` / ``request_error`` outcome recording;
- :func:`bloomberg_input_sourcing_probe.sanitize_external_text` -- the
  existing no-leak guard, applied to every external string before it is
  printed or written.

What is genuinely new here is only the *report contract*: one normalized
record answering this phase's ten questions, with each answer labelled by
the class of evidence that actually establishes it.

**No ticker, RIC, mnemonic or override value is ever invented.** There is no
default ``--security``, no default ``--field`` and no default override
anywhere in this file. Stage 3 runs only for candidates the operator
supplies; with none, it reports ``not_attempted`` and says why. Stage 2 is
free to *search* (a search term is a query, not a claim about an
instrument), and its hits are reported as Bloomberg-published metadata for a
human to read -- never promoted into a route.

**Prior live evidence this probe does not re-litigate** (Issue #179, Eddy's
own workstation run, verbatim from that tool's docstring; recorded here so
the same dead ends are not re-tried by habit): ``refdata USSNAC4 BVOL
Curncy`` -> ``INVALID_SECURITY``; ``refdata USSNAC4 Curncy`` with
``PX_LAST``/``LAST_PRICE`` -> absent; ``refdata USSNAC4 BGN Curncy`` with
``SWPN_IMPLIED_VOL`` -> not applicable; the same with
``BVOL_OIS_SWAPTION`` + ``IR_VOL_FMT=normal`` -> not applicable; ``mktdata``
on ``USSNAC4 Curncy`` and ``USSNAC4 BGN Curncy`` -> resolution failed;
``instruments`` lookup for ``USSNAC4`` and ``USSNAC4 BVOL Curncy`` -> empty
``results[]``.

**Four evidence classes, never mixed.** Every one of this phase's ten
report items carries exactly one:

- ``VENDOR_RETURNED`` -- Bloomberg's own response establishes it.
- ``OPERATOR_DECLARED`` -- the operator asserted it on the command line. It
  is recorded verbatim, and it is **not** Bloomberg evidence.
- ``PROBE_OBSERVED`` -- this run observed it locally (the clock, an opened
  service).
- ``NOT_ESTABLISHED`` -- nothing established it. Never filled in.

The distinction is the point of the whole file. In particular a returned
*number* does not establish its own vol type or unit: "this mnemonic is a
normal vol quoted in bp" is a semantic claim about a field's meaning, and
this probe reports the operator's declaration plus Bloomberg's *published
field documentation* for a human to weigh -- it never labels a value
"normal vol, bp" on the strength of its magnitude. That is exactly the
silent unit coercion Annex A.8.1 forbids.

**Local output only.** The reports go to a git-ignored directory
(``shiori_swaption_vol_probe_output/``). They can contain licensed market
values and the operator's own inputs; they are workstation evidence and must
never be committed or uploaded anywhere. Every external string is passed
through the existing sanitizer first, and every override value sent is
scrubbed against the structural form the sanitizer already handles.

**Two commands, run at the Bloomberg workstation** (Terminal running and
logged in locally, Bloomberg's official ``blpapi`` installed)::

    python tools/bloomberg_swaption_vol_probe.py --session-only

    python tools/bloomberg_swaption_vol_probe.py \\
        --security "OPERATOR-SUPPLIED-TICKER" \\
        --field OPERATOR_SUPPLIED_FIELD \\
        --override OPERATOR_SUPPLIED_OVERRIDE=VALUE \\
        --expiry 1Y --tenor 5Y --strike ATM --quote-side MID

Neither the ticker nor the mnemonic above is real -- both are the operator's
to supply, from stage 2's own report or a VCUB cell's generated Excel
formula. See ``docs/35_market_data_reconnection_phase1_probe.md`` for the
workstation runbook and the failure-diagnosis table.

**Deliberately not in this file.** No verdict, no ranking of candidates, no
"closest match". No vol-surface construction, no interpolation, no
calibration, no ``sigma_vcub`` -> yield-vol -> price-vol conversion, no
Black-76, no ``PRICE_VOL`` contract change, no auto-fill, no persistence, no
schema or production field-map change, and no *call* into anything under
``shiori_pricing_lab.pricing`` or ``shiori_pricing_lab.data``. Those modules
are still *imported* transitively by the reused helpers -- see the module
docstring's precision note above -- which is a cost of reuse, not a licence to
call them.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path

# The bootstrap runs before every local tool import below, not after them:
# the reused `bloomberg_dapi_probe` imports `shiori_pricing_lab` at module
# level and has no bootstrap of its own, so a bootstrap placed after these
# imports would never execute in the checkout-without-editable-install case it
# exists for -- the CLI would die on the import rather than reach its own
# honest missing-blpapi handling. Same placement (and same reason) as
# ``tools/bloomberg_bond_yield_field_probe.py`` and
# ``tools/bloomberg_bond_yield_history_acceptance.py``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bloomberg_curve_discovery_probe import (  # noqa: E402
    _APIFLDS_SERVICE,
    discover_service,
)
from bloomberg_dapi_probe import _send_request  # noqa: E402
from bloomberg_input_sourcing_probe import sanitize_external_text  # noqa: E402
from bloomberg_vcub_field_hunter import (  # noqa: E402
    DEFAULT_VCUB_SEARCH_TERMS,
    MAX_CANDIDATE_PROBES,
    hunt_vol_fields,
    probe_candidate_values,
)
from market_data_probe_evidence import (  # noqa: E402
    AppliedOverride,
    AuthorizationStatus,
    EvidenceClass,
    ReportItem,
    items_to_dict,
    sanitize_tree,
    utc_now,
)

#: The services this probe opens for connectivity/service-authorisation
#: evidence. Both are services this repository already uses live; ``refdata``
#: is the value-probe service and ``apiflds`` is the field-search service.
#: Nothing else is opened -- ``//blp/mktdata`` and ``//blp/instruments`` were
#: already probed to exhaustion for the related coordinate in Issue #179 and
#: are not re-opened here by habit.
DEFAULT_SERVICES: tuple[str, ...] = ("//blp/refdata", _APIFLDS_SERVICE)

#: This probe's own search vocabulary. Its default is Issue #179's proven
#: list, reused verbatim rather than re-worded, because that list has
#: actually been sent to ``//blp/apiflds`` and returned records; the operator
#: extends it with ``--search-term``. A search term is a query, never a claim
#: that a matching field is the right one.
DEFAULT_SEARCH_TERMS: tuple[str, ...] = DEFAULT_VCUB_SEARCH_TERMS

DEFAULT_OUTPUT_DIRNAME = "shiori_swaption_vol_probe_output"
JSON_FILENAME = "bloomberg_swaption_vol_probe.json"
MARKDOWN_FILENAME = "bloomberg_swaption_vol_probe.md"

#: The bound ``probe_candidate_values`` actually enforces, aliased rather than
#: restated: a second hard-coded 40 here would silently drift from the cap the
#: helper really applies, and the operator would be told the wrong limit.
MAX_VALUE_PROBES = MAX_CANDIDATE_PROBES

#: Field outcomes in which Bloomberg itself answered about the field. A
#: ``request_error`` is deliberately not one of them: the request failed at the
#: envelope, so nothing was learned about the field's entitlement.
_ANSWERED_FIELD_STATUSES = frozenset({"returned", "absent", "field_exception"})

PROBE_NAME = "bloomberg_swaption_vol_probe"
PROBE_VERSION = "PHASE1_V1"
PHASE = "MARKET_DATA_RECONNECTION_PHASE_1"


@dataclass(frozen=True)
class ServiceOutcome:
    """One service's open result, exactly as Bloomberg's ``blpapi`` returned it."""

    service_uri: str
    opened: bool
    open_error: str | None
    operation_count: int
    operation_list_error: str | None


@dataclass(frozen=True)
class SessionReport:
    """Connectivity and service-level authorisation evidence for one run."""

    host: str
    port: int
    attempted_service_uris: tuple[str, ...]
    outcomes: tuple[ServiceOutcome, ...]
    apiflds_open_error: str | None


@dataclass(frozen=True)
class TargetCoordinate:
    """The coordinate the operator says he is asking for.

    Every member is ``None`` unless the operator stated it, and every member
    that is present is *his* assertion. Nothing here is read off a returned
    number, and nothing here is defaulted to the phase's nominal target: a
    report that silently says "1Y x 5Y ATM MID" because that is what the
    phase targeted would make an unstated coordinate look stated.
    """

    expiry: str | None = None
    tenor: str | None = None
    strike: str | None = None
    quote_side: str | None = None
    vol_type: str | None = None
    vol_unit: str | None = None
    data_as_of: str | None = None


@dataclass(frozen=True)
class ValueProbeRecord:
    """One (security, field, overrides) cell's raw outcome, plus its context."""

    security: str
    field: str
    overrides: tuple[tuple[str, str], ...]
    status: str  # "returned" | "absent" | "field_exception" | "request_error"
    value: str | None
    detail: str | None
    fetched_at: str


def probe_session_services(
    service_uris: tuple[str, ...],
    *,
    discover=None,
) -> SessionReport:
    """Open each service read-only and report exactly what Bloomberg said.

    The connectivity stage, and the first thing that fails on a workstation:
    no Terminal running, ``bbcomm`` not up, or a service the account may not
    open. ``discover`` defaults to the repository's proven
    :func:`bloomberg_curve_discovery_probe.discover_service`, which sends no
    request of its own -- opening a service already retrieves its schema
    locally, so this stage is read-only by construction.

    A service that fails to open is reported, never retried against another
    host or port: the host and port are the DAPI defaults this repository's
    production loader already uses, and there is no fallback chain.
    """

    discover = discover or discover_service
    outcomes: list[ServiceOutcome] = []
    apiflds_open_error: str | None = None

    for service_uri in service_uris:
        try:
            evidence = discover(service_uri)
        except ImportError:
            raise
        except Exception as exc:  # noqa: BLE001 -- one refused service is evidence, not a crash
            outcomes.append(
                ServiceOutcome(
                    service_uri=service_uri,
                    opened=False,
                    open_error=sanitize_external_text(str(exc)),
                    operation_count=0,
                    operation_list_error=None,
                )
            )
            if service_uri == _APIFLDS_SERVICE:
                apiflds_open_error = sanitize_external_text(str(exc))
            continue

        outcomes.append(
            ServiceOutcome(
                service_uri=service_uri,
                opened=bool(evidence.opened),
                open_error=sanitize_external_text(evidence.open_error),
                operation_count=len(evidence.operations),
                operation_list_error=sanitize_external_text(evidence.operation_list_error),
            )
        )
        if service_uri == _APIFLDS_SERVICE and not evidence.opened:
            apiflds_open_error = sanitize_external_text(evidence.open_error)

    return SessionReport(
        host="localhost",
        port=8194,
        attempted_service_uris=service_uris,
        outcomes=tuple(outcomes),
        apiflds_open_error=apiflds_open_error,
    )


def run_field_discovery(
    search_terms: tuple[str, ...],
    *,
    hunt=None,
    send_request=None,
    discover=None,
):
    """Search ``//blp/apiflds`` for volatility field metadata and expand it.

    :func:`bloomberg_vcub_field_hunter.hunt_vol_fields` reused unchanged: it
    confirms the ``FieldSearchRequest.searchSpec`` capability from *this
    run's own* introspection before sending anything, keeps only records
    whose Bloomberg-published text contains a literal volatility marker, and
    expands the survivors through the repository's proven two-pass
    documentation reader -- which is the only honest way to learn what
    overrides (expiry / tenor / strike parameters) a field demands.

    No mnemonic is guessed if the search capability is not confirmed; the
    report says so through ``blocker_note`` instead.
    """

    hunt = hunt or hunt_vol_fields
    return hunt(
        search_terms=search_terms,
        send_request=send_request or _send_request,
        discover_service_fn=discover or discover_service,
    )


def run_value_probes(
    securities: tuple[str, ...],
    fields: tuple[str, ...],
    overrides: tuple[tuple[str, str], ...] = (),
    *,
    probe=None,
    clock=None,
) -> tuple[ValueProbeRecord, ...]:
    """Probe exactly the operator-supplied candidates, recording every outcome.

    ``securities`` and ``fields`` come from the operator and are never
    defaulted or extended. Delegates to
    :func:`bloomberg_vcub_field_hunter.probe_candidate_values`, so the
    repository's existing ``MAX_CANDIDATE_PROBES`` bound still applies and a
    broad brute-force sweep is refused by that helper rather than allowed
    here.

    Every external string is sanitized with the override values this run
    actually sent, so an override echoed back inside Bloomberg's own error
    text is scrubbed on the error path exactly as on the success path.
    """

    if not securities or not fields:
        return ()

    applied = tuple(
        AppliedOverride(field=field_id, value=value, source="user_supplied")
        for field_id, value in overrides
    )

    raw = probe_candidate_values(securities, fields, overrides, probe=probe)
    # ``clock or utc_now`` rather than a ``clock=utc_now`` default: a default
    # binds the function object at import time, so monkeypatching
    # ``market_data_probe_evidence.utc_now`` -- the seam that file advertises --
    # would have no effect on this call.
    read_at = (clock or utc_now)()

    records: list[ValueProbeRecord] = []
    for outcome in raw:
        records.append(
            ValueProbeRecord(
                security=sanitize_external_text(outcome.security, applied)
                or outcome.security,
                field=outcome.field,
                overrides=tuple(
                    (field_id, value) for field_id, value in outcome.overrides
                ),
                status=outcome.status,
                value=sanitize_external_text(outcome.value, applied),
                detail=sanitize_external_text(outcome.detail, applied),
                fetched_at=read_at,
            )
        )
    return tuple(records)


def _returned_a_value(record: ValueProbeRecord) -> bool:
    """Whether this cell carries a magnitude.

    A ``returned`` status with an empty string is not a value:
    ``bloomberg_dapi_probe.probe_fields`` sets ``returned`` only when the field
    element exists, and an empty element is an absence, not a magnitude.
    """

    return record.status == "returned" and bool((record.value or "").strip())


def _authorization_status(
    session: SessionReport, value_probes: tuple[ValueProbeRecord, ...]
) -> AuthorizationStatus:
    """How far entitlement was actually established, from this run's own evidence.

    Only a status Bloomberg itself produced counts as field-level evidence.
    ``request_error`` is deliberately excluded: a request that failed at the
    envelope (an invalid security, a timeout, a dead session) answered
    *nothing*, so treating it as an observed field would present an untested
    field as an entitled one. A run whose every cell is ``request_error`` stays
    at service level, which is exactly what it established.
    """

    if not any(outcome.opened for outcome in session.outcomes):
        return AuthorizationStatus.NOT_ESTABLISHED
    if not any(record.status in _ANSWERED_FIELD_STATUSES for record in value_probes):
        return AuthorizationStatus.SERVICE_LEVEL_ONLY
    return AuthorizationStatus.FIELD_LEVEL_OBSERVED


def _documented_override_evidence(discovery) -> tuple[str, ...]:
    """Bloomberg's own documented override ids, gathered from the expansion.

    Best-effort and evidence-only: these are the ids *Bloomberg published* for
    the matched fields, reported so a human can see which mnemonics ask for
    expiry/tenor/strike parameters. Nothing here asserts that any of them is
    the parameter this phase needs.

    The expansion's shape is the reused
    :func:`bloomberg_curve_field_documentation_probe.build_report` dict: a
    ``primary_descriptions`` / ``override_descriptions`` pair, where each entry
    carries ``mnemonic`` (or ``field``) and its own ``overrides`` list. Read
    from those keys rather than by pattern-matching key names -- an earlier
    revision matched keys *ending in* ``"override"``, which none of them do, so
    it silently reported "Bloomberg documents no overrides" for every run.
    """

    expansion = getattr(discovery, "documentation_expansion", None)
    if not isinstance(expansion, dict):
        return ()

    found: list[str] = []
    for section in ("primary_descriptions", "override_descriptions"):
        entries = expansion.get(section)
        if not isinstance(entries, (list, tuple)):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            mnemonic = entry.get("mnemonic") or entry.get("field")
            override_ids = entry.get("overrides")
            if not isinstance(override_ids, (list, tuple)):
                continue
            for override_id in override_ids:
                if not isinstance(override_id, str) or not override_id.strip():
                    continue
                override_id = override_id.strip()
                found.append(
                    f"{mnemonic} -> {override_id}" if mnemonic else override_id
                )

    return tuple(dict.fromkeys(found))


def build_report_items(
    session: SessionReport,
    coordinate: TargetCoordinate,
    value_probes: tuple[ValueProbeRecord, ...],
    documented_overrides: tuple[str, ...],
    *,
    fetched_at: str,
) -> tuple[ReportItem, ...]:
    """The phase's ten questions, each labelled with the evidence behind it.

    This is where the file refuses to be helpful in the wrong direction.
    Eight of these ten items are *statements about what a mnemonic means*,
    and a returned number cannot establish any of them: it says nothing about
    whether it is a normal or lognormal vol, what it is quoted in, which
    expiry/tenor it belongs to, which side it is, or as of when. So a value
    that came back is recorded as evidence of ``value`` and of nothing else,
    and the operator's coordinate is recorded as operator-declared rather
    than promoted into Bloomberg evidence.
    """

    any_service_opened = any(outcome.opened for outcome in session.outcomes)
    returned = tuple(record for record in value_probes if _returned_a_value(record))
    opined = tuple(record for record in value_probes if not _returned_a_value(record))

    def _declared(item: str, declared: str | None, note: str) -> ReportItem:
        if declared is None:
            return ReportItem(
                item=item,
                evidence_class=EvidenceClass.NOT_ESTABLISHED,
                detail=f"the operator did not state {item}; this probe never defaults it. {note}",
            )
        return ReportItem(
            item=item,
            evidence_class=EvidenceClass.OPERATOR_DECLARED,
            detail=(
                f"the operator stated {item}={declared!r}. This is his assertion, not "
                f"Bloomberg evidence. {note}"
            ),
        )

    items: list[ReportItem] = []

    items.append(
        ReportItem(
            item="connection",
            evidence_class=(
                EvidenceClass.PROBE_OBSERVED
                if any_service_opened
                else EvidenceClass.NOT_ESTABLISHED
            ),
            detail=(
                "Bloomberg DAPI agreed to open at least one of "
                f"{list(session.attempted_service_uris)} at {session.host}:{session.port}, so a "
                "session was established. This is this run's own observation."
                if any_service_opened
                else (
                    f"no service opened at {session.host}:{session.port}; see the per-service "
                    "open_error values, which carry Bloomberg's own message."
                )
            ),
        )
    )

    items.append(
        ReportItem(
            item="authorization",
            evidence_class=(
                EvidenceClass.VENDOR_RETURNED
                if any_service_opened
                else EvidenceClass.NOT_ESTABLISHED
            ),
            detail=(
                f"status={_authorization_status(session, value_probes).value}. Bloomberg's own "
                "acceptance of openService is the only service-level evidence; per-field "
                "entitlement is evidenced only by each field's own response or fieldException "
                "text, which are reported per cell. No blanket account entitlement is claimed."
            ),
        )
    )

    items.append(
        ReportItem(
            item="value",
            evidence_class=(
                EvidenceClass.VENDOR_RETURNED if returned else EvidenceClass.NOT_ESTABLISHED
            ),
            detail=(
                f"{len(returned)} of {len(value_probes)} probed cell(s) returned a value; "
                "each is one Bloomberg-returned raw string in value_probes."
                if returned
                else (
                    f"no probed cell returned a value ({len(opined)} other outcome(s) recorded)."
                    if value_probes
                    else "no candidate was supplied, so no value was requested."
                )
            ),
        )
    )

    # vol_type / vol_unit / expiry / tenor / quote_side / data_as_of are all
    # claims about what a field *means*, which no returned number can settle.
    for item, declared in (
        ("vol_type", coordinate.vol_type),
        ("vol_unit", coordinate.vol_unit),
        ("expiry", coordinate.expiry),
        ("tenor", coordinate.tenor),
        ("quote_side", coordinate.quote_side),
        ("data_date", coordinate.data_as_of),
    ):
        items.append(
            _declared(
                item,
                declared,
                (
                    "A returned number cannot establish this: it is a claim about the field's "
                    "meaning, not about its magnitude. Read field_discovery's Bloomberg-published "
                    "descriptions for evidence about the field itself."
                    if item in ("vol_type", "vol_unit")
                    else (
                        "A returned number cannot establish this either; it is not carried in a "
                        "ReferenceDataRequest value. See field_discovery for the override ids "
                        "Bloomberg documents for the matched fields."
                    )
                ),
            )
        )

    items.append(
        ReportItem(
            item="fetched_at",
            evidence_class=EvidenceClass.PROBE_OBSERVED,
            detail=(
                f"{fetched_at} -- this run's own UTC clock, read once as the value stage "
                "returned. It is when the probe ran, never the market data's own date. Note a "
                "multi-security run sends its cells sequentially and stamps them all with this "
                "one post-batch reading; per-cell detail carries its own fetched_at, and none "
                "of them is a quote time."
            ),
        )
    )

    if documented_overrides:
        items.append(
            ReportItem(
                item="bloomberg_documented_overrides",
                evidence_class=EvidenceClass.VENDOR_RETURNED,
                detail=(
                    "Bloomberg's own published override ids for the matched fields: "
                    f"{list(documented_overrides)}. Evidence about what the fields demand -- not "
                    "an assertion that any of them parameterises this phase's coordinate."
                ),
            )
        )

    return tuple(items)


def _discovery_to_dict(
    discovery, overrides: tuple[AppliedOverride, ...], search_terms: tuple[str, ...]
) -> dict | None:
    """Serialize the reused field-discovery report, sanitized, or ``None``.

    ``search_terms`` is what this run actually sent, not the module default:
    a report that echoed the default after the operator extended it would
    misdescribe the run's own evidence.
    """

    if discovery is None:
        return None
    if is_dataclass(discovery) and not isinstance(discovery, type):
        raw = asdict(discovery)
    else:  # pragma: no cover - only if the reused helper's return type changes
        raw = {"repr": repr(discovery)}
    return {"search_terms": list(search_terms), "report": sanitize_tree(raw, overrides)}


def build_report(
    session: SessionReport,
    coordinate: TargetCoordinate,
    discovery,
    value_probes: tuple[ValueProbeRecord, ...],
    *,
    generated_at: str,
    search_terms: tuple[str, ...] = DEFAULT_SEARCH_TERMS,
    discovery_failure_reason: str | None = None,
) -> dict:
    """Assemble the whole run into one sanitized, JSON-serializable record."""

    applied = tuple(
        AppliedOverride(field=field_id, value=value, source="user_supplied")
        for record in value_probes
        for field_id, value in record.overrides
    )

    documented_overrides = _documented_override_evidence(discovery)
    items = build_report_items(
        session,
        coordinate,
        value_probes,
        documented_overrides,
        fetched_at=value_probes[-1].fetched_at if value_probes else generated_at,
    )

    payload = {
        "probe": PROBE_NAME,
        "probe_version": PROBE_VERSION,
        "phase": PHASE,
        "generated_at": generated_at,
        "read_only": True,
        "production_paths_touched": [],
        "session": sanitize_tree(asdict(session), applied),
        "authorization_status": _authorization_status(session, value_probes).value,
        "target_coordinate_declared_by_operator": sanitize_tree(asdict(coordinate), applied),
        "field_discovery": _discovery_to_dict(discovery, applied, search_terms),
        "discovery_failure_reason": sanitize_external_text(discovery_failure_reason, applied),
        "documented_overrides": list(documented_overrides),
        "value_probes": [sanitize_tree(asdict(record), applied) for record in value_probes],
        "required_report_items": items_to_dict(items),
        "verdict": None,
        "verdict_assigned": False,
        "verdict_note": (
            "This probe assigns no verdict, exactly as tools/bloomberg_vcub_field_hunter.py "
            "established, and encodes that the same way that tool does: verdict is null. "
            "Whether the recorded evidence adds up to a usable non-visual route to a USD "
            "swaption normal vol is Eddy's judgment on this report. In particular a returned "
            "value is evidence of a value only -- the report deliberately does not label it a "
            "normal vol in bp, because that is a claim about the field's meaning that no "
            "returned number can settle (Annex A.8.1)."
        ),
    }

    # One last pass over the *whole* payload, not just the blocks built above:
    # `required_report_items` carries Bloomberg-published override ids and
    # vendor-published enum text, and the declared coordinate carries operator
    # input. Sanitizing the assembled record means a future field cannot be
    # added outside the guard by accident.
    return sanitize_tree(payload, applied)


def render_markdown(data: dict) -> str:
    """Render the report for a terminal-side read."""

    lines = [
        f"# {data['probe']} ({data['phase']})",
        "",
        f"- generated_at: {data['generated_at']}",
        f"- read_only: {data['read_only']}",
        f"- authorization_status: **{data['authorization_status']}**",
        f"- verdict: {json.dumps(data['verdict'])} (verdict_assigned: "
        f"{json.dumps(data['verdict_assigned'])}) -- this probe assigns no verdict",
        "",
        "## Session / services",
        "",
        "| service | opened | operations | open_error |",
        "| --- | --- | --- | --- |",
    ]
    for outcome in data["session"]["outcomes"]:
        error = outcome["open_error"] or ""
        lines.append(
            f"| {outcome['service_uri']} | {outcome['opened']} | "
            f"{outcome['operation_count']} | {error} |"
        )

    lines += ["", "## Declared target coordinate (operator, not Bloomberg)", ""]
    declared = data["target_coordinate_declared_by_operator"]
    if any(value is not None for value in declared.values()):
        for key, value in declared.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- nothing declared")

    lines += [
        "",
        "## Report items and the evidence behind each",
        "",
        "| item | evidence_class | detail |",
        "| --- | --- | --- |",
    ]
    for item in data["required_report_items"]:
        lines.append(f"| {item['item']} | {item['evidence_class']} | {item['detail']} |")

    lines += ["", "## Value probes", ""]
    if data["value_probes"]:
        lines += [
            "| security | field | status | value | detail | fetched_at |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for probe in data["value_probes"]:
            lines.append(
                f"| {probe['security']} | {probe['field']} | {probe['status']} | "
                f"{probe['value'] or ''} | {probe['detail'] or ''} | {probe['fetched_at']} |"
            )
    else:
        lines.append("- no operator-supplied candidate was probed")

    lines += [
        "",
        "## Field discovery (Bloomberg-published metadata)",
        "",
        "```json",
        json.dumps(data.get("field_discovery"), indent=2, ensure_ascii=False),
        "```",
        "",
        "## Verdict note",
        "",
        data["verdict_note"],
        "",
    ]
    return "\n".join(lines)


def render_json(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def write_report(data: dict, output_dir: Path) -> tuple[Path, Path]:
    """Write both report forms into a local, git-ignored directory."""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / JSON_FILENAME
    markdown_path = output_dir / MARKDOWN_FILENAME
    json_path.write_text(render_json(data), encoding="utf-8")
    markdown_path.write_text(render_markdown(data), encoding="utf-8")
    return json_path, markdown_path

def _parse_override(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError(
            f"override {raw!r} must be FIELD=VALUE -- neither an override id nor its value is "
            "guessed here"
        )
    field_id, value = raw.split("=", 1)
    if not field_id.strip():
        raise argparse.ArgumentTypeError(f"override {raw!r} has an empty override id")
    return field_id.strip(), value


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Bloomberg DAPI probe for a USD swaption normal vol route "
            "(Market Data Reconnection Phase 1). Invents no ticker, mnemonic or override."
        )
    )
    parser.add_argument(
        "--session-only",
        action="store_true",
        help="Report session/service evidence only; send no field search and no value request",
    )
    parser.add_argument(
        "--skip-discovery",
        action="store_true",
        help="Do not send the //blp/apiflds field search",
    )
    parser.add_argument(
        "--search-term",
        action="append",
        default=None,
        help=f"Extra //blp/apiflds search term (repeatable). Default: {list(DEFAULT_SEARCH_TERMS)}",
    )
    parser.add_argument(
        "--service",
        action="append",
        default=None,
        help=f"Service URI to open (repeatable). Default: {list(DEFAULT_SERVICES)}",
    )
    parser.add_argument(
        "--security",
        action="append",
        default=None,
        help="Operator-supplied Bloomberg security/ticker (repeatable, REQUIRED for a value "
        "probe). This probe has no default and will not guess one.",
    )
    parser.add_argument(
        "--field",
        action="append",
        default=None,
        help="Operator-supplied Bloomberg field mnemonic (repeatable, REQUIRED for a value "
        "probe). This probe has no default and will not guess one.",
    )
    parser.add_argument(
        "--override",
        action="append",
        default=None,
        type=_parse_override,
        metavar="FIELD=VALUE",
        help="Override sent verbatim (repeatable). Read from a VCUB cell's own generated Excel "
        "formula or stage 2's report -- never guessed.",
    )
    parser.add_argument("--expiry", default=None, help="Declared expiry label, e.g. 1Y (operator)")
    parser.add_argument(
        "--tenor", default=None, help="Declared swap tenor label, e.g. 5Y (operator)"
    )
    parser.add_argument("--strike", default=None, help="Declared strike, e.g. ATM (operator)")
    parser.add_argument(
        "--quote-side",
        default=None,
        choices=("BID", "MID", "OFFER"),
        help="Declared quote side (operator)",
    )
    parser.add_argument(
        "--vol-type",
        default=None,
        help="Declared volatility space, e.g. Normal / Lognormal (operator assertion, recorded "
        "as such and never presented as Bloomberg evidence)",
    )
    parser.add_argument(
        "--vol-unit",
        default=None,
        help="Declared volatility unit, e.g. bp (operator assertion, recorded as such and never "
        "presented as Bloomberg evidence)",
    )
    parser.add_argument(
        "--data-as-of",
        default=None,
        help="Declared market data date/time (operator assertion; this probe cannot read a "
        "per-value timestamp out of a ReferenceDataRequest response)",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIRNAME,
        help=f"Local report directory (default: {DEFAULT_OUTPUT_DIRNAME})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    services = tuple(args.service) if args.service else DEFAULT_SERVICES
    search_terms = tuple(args.search_term) if args.search_term else DEFAULT_SEARCH_TERMS
    securities = tuple(args.security) if args.security else ()
    fields = tuple(args.field) if args.field else ()
    overrides = tuple(args.override) if args.override else ()

    coordinate = TargetCoordinate(
        expiry=args.expiry,
        tenor=args.tenor,
        strike=args.strike,
        quote_side=args.quote_side,
        vol_type=args.vol_type,
        vol_unit=args.vol_unit,
        data_as_of=args.data_as_of,
    )

    generated_at = utc_now()

    print(f"{PROBE_NAME} {PROBE_VERSION} ({PHASE})")
    print(f"Opening: {list(services)}")

    try:
        session = probe_session_services(services)
    except ImportError as exc:
        print(
            "error: blpapi is not installed -- run this on the Bloomberg-networked workstation "
            f"with Bloomberg's official blpapi package installed ({exc})",
            file=sys.stderr,
        )
        return 2

    for outcome in session.outcomes:
        state = "opened" if outcome.opened else "REFUSED"
        print(f"  {outcome.service_uri}: {state}"
              + (f" ({outcome.open_error})" if outcome.open_error else ""))

    if not any(outcome.opened for outcome in session.outcomes):
        print(
            "error: no Bloomberg DAPI service could be opened -- confirm a Bloomberg Terminal is "
            "running and logged in on this workstation (see the failure table in "
            "docs/35_market_data_reconnection_phase1_probe.md)",
            file=sys.stderr,
        )
        # Write the failure record anyway: this is the most likely first-run
        # outcome on a fresh workstation, and the diagnosis table tells the
        # operator to read the per-service open_error out of the report. A
        # refused session is fully representable -- every item simply stays
        # NOT_ESTABLISHED.
        data = build_report(
            session,
            coordinate,
            None,
            (),
            generated_at=generated_at,
            search_terms=search_terms,
        )
        json_path, markdown_path = write_report(data, Path(args.output_dir))
        print(f"wrote {json_path}")
        print(f"wrote {markdown_path}")
        print(
            "note: these reports can contain licensed market values and operator inputs -- keep "
            "them on the approved workstation; the output directory is git-ignored"
        )
        return 1

    discovery = None
    discovery_failure_reason = None
    if not args.session_only and not args.skip_discovery:
        print(f"Searching //blp/apiflds for: {list(search_terms)}")
        try:
            discovery = run_field_discovery(search_terms)
        except ImportError as exc:  # pragma: no cover - the session stage already imported it
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except RuntimeError as exc:
            # Kept in the report, not only on stderr: "the search failed and
            # here is why" must stay distinguishable from "--skip-discovery"
            # in the artifact the operator sends back.
            discovery_failure_reason = sanitize_external_text(str(exc))
            print(
                f"warning: field discovery failed: {discovery_failure_reason}",
                file=sys.stderr,
            )
            discovery = None
        else:
            confirmed = getattr(discovery, "search_capability_confirmed", False)
            print(f"  search capability confirmed: {confirmed}")
            for mnemonic in getattr(discovery, "marker_relevant_mnemonics", ()):
                print(f"    candidate mnemonic: {sanitize_external_text(str(mnemonic))}")
            blocker = getattr(discovery, "blocker_note", None)
            if blocker:
                print(f"  blocker: {sanitize_external_text(blocker)}")

    value_probes: tuple[ValueProbeRecord, ...] = ()
    # `--session-only` promises "send no field search and no value request",
    # so it gates the value stage too. Without this, a `--session-only
    # --security X --field Y` run would send a licensed ReferenceDataRequest
    # while reporting itself as a connectivity check.
    if securities and fields and not args.session_only:
        print(f"Probing {len(securities)} security x {len(fields)} field combination(s)")
        try:
            value_probes = run_value_probes(securities, fields, overrides)
        except ValueError as exc:
            print(f"error: {sanitize_external_text(str(exc))}", file=sys.stderr)
            return 2
        except (RuntimeError, ImportError) as exc:
            print(f"error: {sanitize_external_text(str(exc))}", file=sys.stderr)
            return 1
        for record in value_probes:
            print(
                f"  {sanitize_external_text(record.security)} / "
                f"{sanitize_external_text(record.field)}: {record.status} "
                f"{record.value or record.detail or ''}"
            )
    elif args.session_only:
        print("--session-only: sending no field search and no value request")
    elif securities or fields:
        supplied = "--security" if securities else "--field"
        missing = "--field" if securities else "--security"
        print(
            f"{supplied} was supplied but {missing} was not, so no value was requested; this "
            "probe needs both and never invents the other one"
        )
    else:
        print(
            "no --security/--field supplied, so no value was requested; "
            "this probe never invents a ticker or a mnemonic"
        )

    data = build_report(
        session,
        coordinate,
        discovery,
        value_probes,
        generated_at=generated_at,
        search_terms=search_terms,
        discovery_failure_reason=discovery_failure_reason,
    )
    json_path, markdown_path = write_report(data, Path(args.output_dir))

    print("")
    print(f"authorization_status: {data['authorization_status']}")
    print(f"verdict: {json.dumps(data['verdict'])} (this probe assigns none -- see the report)")
    print(f"wrote {json_path}")
    print(f"wrote {markdown_path}")
    print(
        "note: these reports can contain licensed market values and operator inputs -- keep "
        "them on the approved workstation; the output directory is git-ignored"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

