"""Bloomberg DAPI curve/bulk-data acquisition discovery probe (Issue #165 follow-up).

Bounded, read-only diagnostic CLI -- **not** part of the production pricing
or ingestion path, and never imported by either. It exists to answer one
question without guessing at it: **how would Shiori acquire a Bloomberg
Curve Analysis / Stripped Curve (e.g. Curve #490 USD SOFR) via DAPI at
all** -- what the curve identifier acquisition route is, what the
stripped-curve bulk field is called, what overrides it needs, and what its
raw response looks like -- using only Bloomberg's own service/schema
introspection, never a hardcoded operation name or field mnemonic.

**One command, run once (Issue #165 request).** Eddy runs::

    python tools/bloomberg_curve_discovery_probe.py

on a Bloomberg-networked workstation with the Terminal running and logged
in locally, and pastes back the console output (or the written report
files) -- no per-keyword FLDS lookups, no manual field hunting.

**What this probes, and how, without guessing:**

1. Opens ``//blp/apiflds`` and ``//blp/refdata`` and, for each, captures
   Bloomberg's own full service schema text (``str(service)``/
   ``Service.toString()``) -- the single most robust, best-documented
   introspection call in blpapi's Python SDK (it is what Bloomberg's own
   ``ServiceSchema`` sample does). This alone typically lists every
   operation the service supports and every element of its request/response
   schema, in Bloomberg's own words.
2. Best-effort, structured on top of that: walks ``Service.numOperations()``/
   ``getOperation(i)`` to list each operation's name, description, request
   schema and response schema individually -- so a partial failure on one
   operation (e.g. an SDK-version method mismatch) is reported against that
   operation only and never blocks the rest of the run or the schema dump
   already captured in step 1.
3. For ``//blp/apiflds`` only, sends one request per search term (default:
   "stripped curve", "zero curve", "discount factor", "curve members",
   "SWDF") via ``FieldSearchRequest.searchSpec`` -- the one (operation,
   request element) pair a real workstation run confirmed works (Issue
   #165 round 3), still gated on ``discover_service``'s own introspection
   of *this run* actually reporting that pair, never assumed present
   without it. Round 2's broader heuristic -- any top-level ``STRING``
   element on any operation -- is deliberately narrowed away: it risked
   sending requests to unrelated operations that merely happened to have a
   string field. If this run's introspection does not report that exact
   pair, that absence is reported explicitly as a limitation, rather than
   falling back to a guessed mnemonic or a different element.
4. ``//blp/refdata`` gets the same schema capture (steps 1-2) but no search
   attempt (Issue #165 item 5 only asks for the formal schema, not a search
   call there).

**Separation from production/pricing (Issue #165 boundary).** This module
is never imported by anything under ``src/``, performs no pricing, no OIS
bootstrap, and writes nothing except its own diagnostic report files. It
reuses ``tools/bloomberg_dapi_probe.py``'s ``_send_request`` for the one
place this script sends an actual request (step 3) -- the same shared
session/event-loop plumbing ``probe_fields``/``describe_fields`` already
use, so this is not a fourth copy of that handling in this repo -- and
reuses ``tools/bloomberg_input_sourcing_probe.py``'s
``sanitize_external_text`` so no host, path, or session detail Bloomberg's
own error text might carry ever reaches a committable output. Schema and
operation names themselves are Bloomberg's own structural/semantic
documentation, not a market value or credential, so (unlike the
``MARKET_LEVEL`` redaction rule in ``bloomberg_input_sourcing_probe.py``)
they are reported verbatim, only sanitized for host/path/session leakage.

**What this script does not do.** No curve identifier is assumed or
required as input -- service/operation schema introspection needs no
security at all. No candidate mnemonic is asserted as correct; every
mnemonic/operation name that appears in this script's output came from
Bloomberg's own response, not from this repo. No production loader, no
``BLICurvePoint`` construction, no wiring into any pricing or workbench
code.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bloomberg_dapi_probe import _send_request  # shared session/event-loop plumbing
from bloomberg_input_sourcing_probe import sanitize_external_text

_DAPI_HOST = "localhost"
_DAPI_PORT = 8194
_APIFLDS_SERVICE = "//blp/apiflds"
_REFDATA_SERVICE = "//blp/refdata"

DEFAULT_SEARCH_TERMS: tuple[str, ...] = (
    "stripped curve",
    "zero curve",
    "discount factor",
    "curve members",
    "SWDF",
)

# Issue #165 round 3: Eddy's real workstation run proved exactly this
# (operation, request element) pair is the working //blp/apiflds field
# search route -- it returned SW174/SW173/SW167-169/SW754-755/DY766/DY768
# for the search terms above. This is no longer "any top-level STRING
# element on any operation" (round 2's blind heuristic); it is narrowed to
# the one pair workstation evidence actually confirmed, still gated on
# `discover_service`'s own introspection reporting that pair for real on
# this run -- never assumed present without that confirmation.
_PROVEN_SEARCH_OPERATION = "FieldSearchRequest"
_PROVEN_SEARCH_ELEMENT = "searchSpec"

DEFAULT_OUTPUT_DIRNAME = "shiori_curve_discovery_output"
MARKDOWN_FILENAME = "bloomberg_curve_discovery_probe.md"
JSON_FILENAME = "bloomberg_curve_discovery_probe.json"

_MAX_SCHEMA_CHARS = 40_000
_MAX_RESPONSE_CHARS = 20_000


@dataclass(frozen=True)
class OperationEvidence:
    """One operation's own schema, exactly as Bloomberg's ``blpapi`` reports it."""

    name: str
    description: str | None
    request_schema: str | None
    response_schemas: tuple[str, ...]
    candidate_string_elements: tuple[str, ...]
    introspection_error: str | None = None


@dataclass(frozen=True)
class ServiceEvidence:
    service_uri: str
    opened: bool
    open_error: str | None
    full_schema_dump: str | None
    operations: tuple[OperationEvidence, ...]
    operation_list_error: str | None


@dataclass(frozen=True)
class SearchAttempt:
    operation_name: str
    request_element_used: str
    term: str
    status: str  # "sent" | "error"
    raw_response_dump: str | None
    error: str | None


@dataclass(frozen=True)
class DiscoveryReport:
    generated_at: str
    apiflds: ServiceEvidence
    refdata: ServiceEvidence
    search_terms: tuple[str, ...]
    search_attempts: tuple[SearchAttempt, ...]
    search_capability_found: bool
    limitation_note: str | None


def _collapse(text: str, limit: int) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n... <truncated {len(text) - limit} more characters>"
    return text


def _safe_str(value) -> str | None:
    """``str(value)`` guarded against a native ``blpapi`` failure on this SDK version."""

    try:
        return str(value)
    except Exception as exc:  # noqa: BLE001 -- any native blpapi failure, never let it abort the run
        return f"<could not stringify: {exc}>"


def _candidate_string_elements(blpapi_module, request_definition) -> tuple[str, ...]:
    """Top-level request-schema element names whose type is ``STRING``.

    Discovered from ``request_definition``'s own ``SchemaTypeDefinition`` --
    never a hardcoded field name. Returns an empty tuple, not a guess, if this
    schema cannot be walked this way (a different blpapi SDK version, or a
    request shaped without any top-level string element).
    """

    try:
        type_definition = request_definition.typeDefinition()
        count = type_definition.numElementDefinitions()
    except Exception:  # noqa: BLE001
        return ()

    candidates: list[str] = []
    for i in range(count):
        try:
            element_definition = type_definition.getElementDefinition(i)
            element_datatype = element_definition.typeDefinition().datatype()
            if element_datatype == blpapi_module.DataType.STRING:
                candidates.append(str(element_definition.name()))
        except Exception:  # noqa: BLE001 -- one unwalkable element never blocks the others
            continue
    return tuple(candidates)


def _describe_operation(blpapi_module, operation) -> OperationEvidence:
    name = "<unknown>"
    try:
        name = str(operation.name())
    except Exception as exc:  # noqa: BLE001
        return OperationEvidence(
            name=name,
            description=None,
            request_schema=None,
            response_schemas=(),
            candidate_string_elements=(),
            introspection_error=str(exc),
        )

    description = None
    try:
        description = _safe_str(operation.description())
    except Exception:  # noqa: BLE001
        description = None

    request_definition = None
    request_schema = None
    try:
        request_definition = operation.requestDefinition()
        request_schema = _safe_str(request_definition)
    except Exception as exc:  # noqa: BLE001
        return OperationEvidence(
            name=name,
            description=description,
            request_schema=None,
            response_schemas=(),
            candidate_string_elements=(),
            introspection_error=str(exc),
        )

    response_schemas: list[str] = []
    try:
        for i in range(operation.numResponseDefinitions()):
            try:
                response_schemas.append(_safe_str(operation.getResponseDefinitionAt(i)))
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass

    candidate_elements = (
        _candidate_string_elements(blpapi_module, request_definition)
        if request_definition is not None
        else ()
    )

    return OperationEvidence(
        name=name,
        description=description,
        request_schema=request_schema,
        response_schemas=tuple(response_schemas),
        candidate_string_elements=candidate_elements,
    )


def discover_service(service_uri: str) -> ServiceEvidence:
    """Open one Bloomberg DAPI service and introspect it via ``blpapi``'s own schema APIs.

    Sends no request -- opening a service already retrieves its schema
    locally. The full ``str(service)`` dump is always attempted first (the
    most robust call); the structured per-operation walk is best-effort on
    top of it, so an SDK-version mismatch on the structured walk never loses
    the schema dump already captured.
    """

    import blpapi

    session_options = blpapi.SessionOptions()
    session_options.setServerHost(_DAPI_HOST)
    session_options.setServerPort(_DAPI_PORT)
    session = blpapi.Session(session_options)

    try:
        if not session.start():
            return ServiceEvidence(
                service_uri=service_uri,
                opened=False,
                open_error=(
                    f"Bloomberg DAPI session failed to start against {_DAPI_HOST}:{_DAPI_PORT} "
                    "-- confirm a Bloomberg Terminal is running and logged in locally"
                ),
                full_schema_dump=None,
                operations=(),
                operation_list_error=None,
            )

        if not session.openService(service_uri):
            return ServiceEvidence(
                service_uri=service_uri,
                opened=False,
                open_error=f"Bloomberg DAPI failed to open service {service_uri}",
                full_schema_dump=None,
                operations=(),
                operation_list_error=None,
            )

        service = session.getService(service_uri)

        full_schema_dump = sanitize_external_text(_safe_str(service))

        operations: list[OperationEvidence] = []
        operation_list_error: str | None = None
        try:
            operation_count = service.numOperations()
            for i in range(operation_count):
                operation = service.getOperation(i)
                evidence = _describe_operation(blpapi, operation)
                operations.append(
                    OperationEvidence(
                        name=evidence.name,
                        description=sanitize_external_text(evidence.description),
                        request_schema=sanitize_external_text(evidence.request_schema),
                        response_schemas=tuple(
                            sanitize_external_text(s) for s in evidence.response_schemas
                        ),
                        candidate_string_elements=evidence.candidate_string_elements,
                        introspection_error=evidence.introspection_error,
                    )
                )
        except Exception as exc:  # noqa: BLE001 -- structured walk is best-effort
            operation_list_error = str(exc)

        return ServiceEvidence(
            service_uri=service_uri,
            opened=True,
            open_error=None,
            full_schema_dump=full_schema_dump,
            operations=tuple(operations),
            operation_list_error=operation_list_error,
        )
    finally:
        session.stop()


def attempt_field_search(
    apiflds_evidence: ServiceEvidence,
    search_terms: tuple[str, ...],
    send_request=_send_request,
) -> tuple[SearchAttempt, ...]:
    """Call ``FieldSearchRequest.searchSpec`` -- the one pair workstation evidence proved.

    Issue #165 round 3: this no longer tries every apiflds operation with
    any top-level ``STRING`` request element (round 2's broader, unproven
    heuristic risked sending requests to unrelated operations that merely
    happened to have a string field). It only fires for the operation named
    ``FieldSearchRequest`` using its ``searchSpec`` element -- and even then
    only when ``discover_service``'s own introspection of *this run*
    actually reports that element on that operation, never assumed present
    without that confirmation. A failure on one attempt is recorded against
    that attempt only.
    """

    attempts: list[SearchAttempt] = []
    if not apiflds_evidence.opened:
        return tuple(attempts)

    for operation in apiflds_evidence.operations:
        if operation.name != _PROVEN_SEARCH_OPERATION:
            continue
        for element_name in operation.candidate_string_elements:
            if element_name != _PROVEN_SEARCH_ELEMENT:
                continue
            for term in search_terms:
                collected: list[str] = []

                def _configure(request, _element_name=element_name, _term=term) -> None:
                    try:
                        request.set(_element_name, _term)
                    except Exception:  # noqa: BLE001
                        request.append(_element_name, _term)

                def _collect(message, _collected=collected) -> None:
                    _collected.append(_safe_str(message))

                try:
                    send_request(
                        service_uri=_APIFLDS_SERVICE,
                        request_name=operation.name,
                        configure=_configure,
                        collect=_collect,
                        context=f"{operation.name}({element_name}={term!r})",
                    )
                    raw = "\n---\n".join(collected) if collected else None
                    attempts.append(
                        SearchAttempt(
                            operation_name=operation.name,
                            request_element_used=element_name,
                            term=term,
                            status="sent",
                            raw_response_dump=sanitize_external_text(raw),
                            error=None,
                        )
                    )
                except (RuntimeError, ImportError) as exc:
                    attempts.append(
                        SearchAttempt(
                            operation_name=operation.name,
                            request_element_used=element_name,
                            term=term,
                            status="error",
                            raw_response_dump=None,
                            error=sanitize_external_text(str(exc)),
                        )
                    )

    return tuple(attempts)


def run_discovery(search_terms: tuple[str, ...] = DEFAULT_SEARCH_TERMS) -> DiscoveryReport:
    apiflds = discover_service(_APIFLDS_SERVICE)
    refdata = discover_service(_REFDATA_SERVICE)
    search_attempts = attempt_field_search(apiflds, search_terms)

    search_capability_found = any(
        operation.name == _PROVEN_SEARCH_OPERATION
        and _PROVEN_SEARCH_ELEMENT in operation.candidate_string_elements
        for operation in apiflds.operations
    )
    limitation_note = None
    if apiflds.opened and not search_capability_found:
        limitation_note = (
            f"This run's own introspection did not report {_PROVEN_SEARCH_OPERATION}."
            f"{_PROVEN_SEARCH_ELEMENT} -- the one (operation, element) pair a prior "
            "workstation run confirmed (Issue #165 round 3). This script deliberately "
            "no longer treats any other top-level STRING request element as a search "
            "capability (round 2's broader heuristic was narrowed after that "
            "confirmation). The full schema dump above (service.toString()) is still "
            "captured and should be reviewed by hand -- e.g. to confirm whether "
            f"{_PROVEN_SEARCH_OPERATION} is present under a different element name on "
            "this workstation/SDK version -- before falling back to a minimal manual "
            "FLDS search, per Issue #165's own escalation order."
        )
    elif not apiflds.opened:
        limitation_note = (
            "//blp/apiflds could not be opened at all in this run -- see "
            "apiflds.open_error. No search attempt was possible."
        )

    return DiscoveryReport(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        apiflds=apiflds,
        refdata=refdata,
        search_terms=search_terms,
        search_attempts=search_attempts,
        search_capability_found=search_capability_found,
        limitation_note=limitation_note,
    )


# --- rendering -------------------------------------------------------------------


def _operation_to_dict(operation: OperationEvidence) -> dict:
    return {
        "name": operation.name,
        "description": operation.description,
        "request_schema": operation.request_schema,
        "response_schemas": list(operation.response_schemas),
        "candidate_string_elements": list(operation.candidate_string_elements),
        "introspection_error": operation.introspection_error,
    }


def _service_to_dict(evidence: ServiceEvidence) -> dict:
    return {
        "service_uri": evidence.service_uri,
        "opened": evidence.opened,
        "open_error": evidence.open_error,
        "full_schema_dump": evidence.full_schema_dump,
        "operations": [_operation_to_dict(op) for op in evidence.operations],
        "operation_list_error": evidence.operation_list_error,
    }


def build_report(report: DiscoveryReport) -> dict:
    return {
        "generated_at": report.generated_at,
        "search_terms": list(report.search_terms),
        "apiflds": _service_to_dict(report.apiflds),
        "refdata": _service_to_dict(report.refdata),
        "search_attempts": [
            {
                "operation_name": attempt.operation_name,
                "request_element_used": attempt.request_element_used,
                "term": attempt.term,
                "status": attempt.status,
                "raw_response_dump": attempt.raw_response_dump,
                "error": attempt.error,
            }
            for attempt in report.search_attempts
        ],
        "search_capability_found": report.search_capability_found,
        "limitation_note": report.limitation_note,
    }


def render_markdown(data: dict) -> str:
    lines: list[str] = []
    lines.append("# Bloomberg curve/bulk-data acquisition discovery (Issue #165)")
    lines.append("")
    lines.append(f"Generated at: {data['generated_at']}")
    lines.append(f"Search terms: {', '.join(data['search_terms'])}")
    lines.append("")

    for service_key, title in (("apiflds", "//blp/apiflds"), ("refdata", "//blp/refdata")):
        service = data[service_key]
        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"Opened: {service['opened']}")
        if service["open_error"]:
            lines.append(f"Open error: {service['open_error']}")
        if service["operation_list_error"]:
            lines.append(
                f"Operation-list error (best-effort walk only): {service['operation_list_error']}"
            )
        lines.append("")
        if service["full_schema_dump"]:
            lines.append("### Full schema dump (Bloomberg's own `service.toString()`)")
            lines.append("")
            lines.append("```")
            lines.append(_collapse(service["full_schema_dump"], _MAX_SCHEMA_CHARS))
            lines.append("```")
            lines.append("")
        if service["operations"]:
            lines.append("### Operations (structured walk)")
            lines.append("")
            for operation in service["operations"]:
                lines.append(f"#### {operation['name']}")
                if operation["description"]:
                    lines.append(f"Description: {operation['description']}")
                if operation["introspection_error"]:
                    lines.append(f"Introspection error: {operation['introspection_error']}")
                if operation["candidate_string_elements"]:
                    lines.append(
                        "Candidate top-level STRING request elements: "
                        + ", ".join(operation["candidate_string_elements"])
                    )
                if operation["request_schema"]:
                    lines.append("")
                    lines.append("Request schema:")
                    lines.append("```")
                    lines.append(_collapse(operation["request_schema"], _MAX_SCHEMA_CHARS))
                    lines.append("```")
                for i, response_schema in enumerate(operation["response_schemas"]):
                    lines.append(f"Response schema [{i}]:")
                    lines.append("```")
                    lines.append(_collapse(response_schema, _MAX_SCHEMA_CHARS))
                    lines.append("```")
                lines.append("")

    lines.append("## Field-search attempts (//blp/apiflds only)")
    lines.append("")
    lines.append(f"Search capability found: {data['search_capability_found']}")
    if data["limitation_note"]:
        lines.append("")
        lines.append(f"**Limitation:** {data['limitation_note']}")
    lines.append("")
    for attempt in data["search_attempts"]:
        lines.append(
            f"### {attempt['operation_name']} / {attempt['request_element_used']} = "
            f"{attempt['term']!r} -> {attempt['status']}"
        )
        if attempt["error"]:
            lines.append(f"Error: {attempt['error']}")
        if attempt["raw_response_dump"]:
            lines.append("```")
            lines.append(_collapse(attempt["raw_response_dump"], _MAX_RESPONSE_CHARS))
            lines.append("```")
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


# --- CLI ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Bounded read-only Bloomberg DAPI service/schema discovery probe for "
            "curve/bulk-data acquisition (Issue #165). Run with no arguments."
        )
    )
    parser.add_argument(
        "--search-term",
        action="append",
        default=None,
        dest="search_terms",
        help=(
            "Search term to try against any discovered apiflds search-capable "
            "operation; repeatable. Default: " + ", ".join(f"{t!r}" for t in DEFAULT_SEARCH_TERMS)
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
    search_terms = tuple(args.search_terms) if args.search_terms else DEFAULT_SEARCH_TERMS
    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd() / DEFAULT_OUTPUT_DIRNAME

    print("Shiori Bloomberg curve/bulk-data discovery probe (Issue #165) -- read-only")
    print(f"Search terms: {', '.join(search_terms)}")
    print("")

    try:
        report = run_discovery(search_terms)
    except ImportError as exc:
        print(
            "error: blpapi is not installed -- run this on a Bloomberg-networked "
            f"workstation with Bloomberg's official blpapi package installed ({exc})",
            file=sys.stderr,
        )
        return 2

    data = build_report(report)
    markdown_path, json_path = write_report(data, output_dir)

    for label, service_key in (("//blp/apiflds", "apiflds"), ("//blp/refdata", "refdata")):
        service = data[service_key]
        print(f"{label:<16} opened={service['opened']}  operations={len(service['operations'])}")
        if service["open_error"]:
            print(f"  open_error: {service['open_error']}")

    print("")
    print(f"Field-search capability found: {data['search_capability_found']}")
    if data["limitation_note"]:
        print(f"Limitation: {data['limitation_note']}")
    for attempt in data["search_attempts"]:
        print(
            f"  - {attempt['operation_name']} / {attempt['request_element_used']} = "
            f"{attempt['term']!r} -> {attempt['status']}"
        )

    print("")
    print("Full report (paste back or attach either file):")
    print(f"  {markdown_path.resolve()}")
    print(f"  {json_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
