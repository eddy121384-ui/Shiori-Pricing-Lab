"""Bloomberg //blp/instruments curve-security-identity exploration (Issue #165 round 5).

Bounded, read-only diagnostic CLI -- **not** part of the production pricing
or ingestion path, and never imported by either. Rounds 2-4 exhausted the
one route already proven safe for this exact question --
``//blp/apiflds``'s ``FieldSearchRequest.searchSpec`` -- without finding a
field whose Bloomberg documentation ties a curve number/identifier to a
``ReferenceDataRequest`` security. This round does **not** repeat that
broad ``//blp/apiflds`` field search; it explores a different, previously
untried service instead: ``//blp/instruments``.

**``//blp/instruments`` is treated as an exploratory candidate only, never
a confirmed contract.** This script does not assume it opens, does not
assume it has any curve-related capability, and does not assume any
operation name or request/response element name on it -- everything below
is discovered from Bloomberg's own live introspection of *this run*, or the
script stops and says so.

**What this script does, in order:**

1. Attempts ``openService("//blp/instruments")`` (reusing
   ``bloomberg_curve_discovery_probe.discover_service`` unchanged -- the
   same schema-introspection machinery rounds 2-4 already built and
   tested, applied to a new service URI, not a new implementation). If it
   cannot open, that is the whole result: a precise blocker, nothing
   guessed.
2. If it opens, captures Bloomberg's own full service schema
   (``service.toString()``) and every operation's name, description,
   request schema and response schema -- exactly as round 2 already does
   for ``//blp/apiflds``/``//blp/refdata``.
3. Never hardcodes an operation name. Instead, keeps only the operations
   whose *own* Bloomberg-reported description, request schema, or response
   schema text contains the literal substring "curve" (case-insensitive) --
   a plain text search over what Bloomberg itself published about that
   operation, not an inference from the operation's name.
4. For each such curve-related operation, if its own introspected request
   schema exposes a top-level ``STRING`` element (the same
   ``candidate_string_elements`` discovery round 2 already performs), sends
   one request per search term ("SOFR", "USD SOFR", "490") using that
   *discovered* element name, and raw-captures Bloomberg's whole response
   text (``str(message)``) -- no assumed response shape, unlike round 4's
   ``//blp/apiflds``-specific ``fieldData`` parse (that assumption was
   justified there by ``FieldSearchRequest``/``FieldInfoRequest`` sharing
   one well-known metadata-service family; ``//blp/instruments`` has no
   such precedent in this repo, so its response is captured raw only, for
   a human to read).
5. If no operation mentions "curve" at all, or none of the curve-related
   operations expose a searchable top-level string element, that is
   reported as a precise blocker -- never a fallback to guessing an
   operation, an element, or a security ticker.

**The evidence bar (Issue #165 round 5's own words).** A raw response is
Curve #490 identity evidence only if it itself, in one place, ties
USD/SOFR/Curve #490 to a security identifier -- this script makes no such
claim on its own; it only captures what Bloomberg returned for a human to
read against that bar.

**One command, run once.** Eddy runs::

    python tools/bloomberg_curve_instruments_service_probe.py

and pastes back the console output (or the written report files).

**Deliberately not in this script.** No ``SW174``/``SW173`` value loader --
curve security identity has to be settled first. No production loader, no
pricing, no OIS bootstrap.

**Separation from production/pricing.** Never imported by anything under
``src/``. Reuses ``bloomberg_curve_discovery_probe.discover_service``
(schema introspection, unmodified) and ``bloomberg_dapi_probe._send_request``
(the one place this script sends a live request) -- no new session/request
implementation.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bloomberg_curve_discovery_probe import (
    OperationEvidence,
    ServiceEvidence,
    _safe_str,
    _service_to_dict,
    discover_service,
)
from bloomberg_dapi_probe import _send_request  # shared session/event-loop plumbing
from bloomberg_input_sourcing_probe import sanitize_external_text

_INSTRUMENTS_SERVICE = "//blp/instruments"

# Exactly Eddy's three terms -- no additions, no rewording.
DEFAULT_INSTRUMENT_SEARCH_TERMS: tuple[str, ...] = ("SOFR", "USD SOFR", "490")

DEFAULT_OUTPUT_DIRNAME = "shiori_curve_instruments_service_output"
MARKDOWN_FILENAME = "bloomberg_curve_instruments_service_probe.md"
JSON_FILENAME = "bloomberg_curve_instruments_service_probe.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _mentions_curve(operation: OperationEvidence) -> bool:
    """True if Bloomberg's own text about this operation contains "curve".

    Checked against ``description``/``request_schema``/every
    ``response_schemas`` entry -- Bloomberg's own published text, not this
    operation's *name* (which is never pattern-matched here; a curve-named
    operation whose own schema/description happen to say nothing about
    curves would not pass this check, and an oddly-named operation whose
    schema explicitly documents curve content would).
    """

    haystacks = [operation.description, operation.request_schema, *operation.response_schemas]
    return any(text and "curve" in text.lower() for text in haystacks)


@dataclass(frozen=True)
class InstrumentSearchAttempt:
    operation_name: str
    request_element_used: str
    term: str
    status: str  # "sent" | "error"
    raw_response_dump: str | None
    error: str | None


@dataclass(frozen=True)
class InstrumentsExplorationReport:
    generated_at: str
    service: ServiceEvidence
    curve_related_operation_names: tuple[str, ...]
    search_terms: tuple[str, ...]
    search_attempts: tuple[InstrumentSearchAttempt, ...]
    blocker_note: str | None


def explore_instruments_service(
    search_terms: tuple[str, ...] = DEFAULT_INSTRUMENT_SEARCH_TERMS,
    send_request=_send_request,
    discover_service_fn=None,
) -> InstrumentsExplorationReport:
    """Explore ``//blp/instruments`` for curve-identity search capability.

    Every step is gated on this run's own live introspection -- an
    unopenable service, an operation list with nothing curve-related, or a
    curve-related operation with no searchable string element all produce
    a precise, honest ``blocker_note`` rather than a guess.
    """

    discover_service_fn = discover_service_fn or discover_service
    generated_at = _utc_now()
    service = discover_service_fn(_INSTRUMENTS_SERVICE)

    if not service.opened:
        blocker_note = (
            f"{_INSTRUMENTS_SERVICE} could not be opened this run "
            f"({service.open_error}). Curve #490's security identity cannot be explored "
            "via this route this run -- no operation or ticker is guessed as a fallback."
        )
        return InstrumentsExplorationReport(
            generated_at=generated_at,
            service=service,
            curve_related_operation_names=(),
            search_terms=search_terms,
            search_attempts=(),
            blocker_note=blocker_note,
        )

    curve_related = tuple(op for op in service.operations if _mentions_curve(op))

    if not curve_related:
        blocker_note = (
            f"{_INSTRUMENTS_SERVICE} opened, but none of its "
            f"{len(service.operations)} operations' own description/request/response "
            'schema text mentioned "curve" in this run\'s introspection. No '
            "curve-identity search capability was found on this service this run -- "
            "review the full schema dump above by hand before concluding the service "
            "genuinely has none; no operation or ticker is guessed as a fallback."
        )
        return InstrumentsExplorationReport(
            generated_at=generated_at,
            service=service,
            curve_related_operation_names=(),
            search_terms=search_terms,
            search_attempts=(),
            blocker_note=blocker_note,
        )

    search_attempts: list[InstrumentSearchAttempt] = []
    any_searchable = False
    for operation in curve_related:
        for element_name in operation.candidate_string_elements:
            any_searchable = True
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
                        service_uri=_INSTRUMENTS_SERVICE,
                        request_name=operation.name,
                        configure=_configure,
                        collect=_collect,
                        context=f"{operation.name}({element_name}={term!r})",
                    )
                    raw = "\n---\n".join(text for text in collected if text) or None
                    search_attempts.append(
                        InstrumentSearchAttempt(
                            operation_name=operation.name,
                            request_element_used=element_name,
                            term=term,
                            status="sent",
                            raw_response_dump=sanitize_external_text(raw),
                            error=None,
                        )
                    )
                except (RuntimeError, ImportError) as exc:
                    search_attempts.append(
                        InstrumentSearchAttempt(
                            operation_name=operation.name,
                            request_element_used=element_name,
                            term=term,
                            status="error",
                            raw_response_dump=None,
                            error=sanitize_external_text(str(exc)),
                        )
                    )

    if not any_searchable:
        blocker_note = (
            f"{_INSTRUMENTS_SERVICE} has {len(curve_related)} curve-related operation(s) "
            f"({', '.join(op.name for op in curve_related)}) by this run's own text search, "
            "but none exposed a top-level STRING request element in this run's "
            "introspection, so no search could be attempted without guessing a request "
            "element name. Review each operation's own request schema in the full report "
            "by hand -- no element name or ticker is guessed as a fallback."
        )
    else:
        blocker_note = (
            "Search attempted against the curve-related operation(s) above. Whether any "
            "raw response ties USD/SOFR/Curve #490 to a security identifier is this "
            "round's own follow-up reading of the output below -- this script asserts no "
            "such binding itself."
        )

    return InstrumentsExplorationReport(
        generated_at=generated_at,
        service=service,
        curve_related_operation_names=tuple(op.name for op in curve_related),
        search_terms=search_terms,
        search_attempts=tuple(search_attempts),
        blocker_note=blocker_note,
    )


# --- rendering -------------------------------------------------------------------


def _attempt_to_dict(attempt: InstrumentSearchAttempt) -> dict:
    return {
        "operation_name": attempt.operation_name,
        "request_element_used": attempt.request_element_used,
        "term": attempt.term,
        "status": attempt.status,
        "raw_response_dump": attempt.raw_response_dump,
        "error": attempt.error,
    }


def build_report(report: InstrumentsExplorationReport) -> dict:
    return {
        "generated_at": report.generated_at,
        "service": _service_to_dict(report.service),
        "curve_related_operation_names": list(report.curve_related_operation_names),
        "search_terms": list(report.search_terms),
        "search_attempts": [_attempt_to_dict(a) for a in report.search_attempts],
        "blocker_note": report.blocker_note,
    }


def render_markdown(data: dict) -> str:
    lines: list[str] = []
    lines.append("# Bloomberg //blp/instruments curve identity exploration (Issue #165 round 5)")
    lines.append("")
    lines.append(f"Generated at: {data['generated_at']}")
    service = data["service"]
    lines.append(f"Service opened: {service['opened']}")
    if service["open_error"]:
        lines.append(f"Open error: {service['open_error']}")
    lines.append("")

    if service["full_schema_dump"]:
        lines.append("## Full schema dump (Bloomberg's own `service.toString()`)")
        lines.append("")
        lines.append("```")
        lines.append(service["full_schema_dump"])
        lines.append("```")
        lines.append("")

    lines.append(f"## Curve-related operations ({len(data['curve_related_operation_names'])})")
    lines.append("")
    lines.append(", ".join(data["curve_related_operation_names"]) or "(none)")
    lines.append("")

    if service["operations"]:
        lines.append("## All operations (structured walk)")
        lines.append("")
        for operation in service["operations"]:
            marker = (
                "**CURVE-RELATED**"
                if operation["name"] in data["curve_related_operation_names"]
                else "not curve-related"
            )
            lines.append(f"### {operation['name']} ({marker})")
            if operation["description"]:
                lines.append(f"Description: {operation['description']}")
            if operation["candidate_string_elements"]:
                lines.append(
                    "Candidate top-level STRING request elements: "
                    + ", ".join(operation["candidate_string_elements"])
                )
            if operation["request_schema"]:
                lines.append("Request schema:")
                lines.append("```")
                lines.append(operation["request_schema"])
                lines.append("```")
            lines.append("")

    lines.append("## Search attempts")
    lines.append("")
    lines.append(f"Search terms: {', '.join(data['search_terms'])}")
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
            lines.append(attempt["raw_response_dump"])
            lines.append("```")
        lines.append("")

    if data["blocker_note"]:
        lines.append("## Status / blocker")
        lines.append("")
        lines.append(data["blocker_note"])

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
            "Bounded read-only //blp/instruments curve-identity exploration probe "
            "(Issue #165 round 5). Run with no arguments."
        )
    )
    parser.add_argument(
        "--search-term",
        action="append",
        default=None,
        dest="search_terms",
        help=(
            "Search term to try against any discovered curve-related operation; "
            "repeatable. Default: " + ", ".join(f"{t!r}" for t in DEFAULT_INSTRUMENT_SEARCH_TERMS)
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
    search_terms = (
        tuple(args.search_terms) if args.search_terms else DEFAULT_INSTRUMENT_SEARCH_TERMS
    )
    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd() / DEFAULT_OUTPUT_DIRNAME

    print("Shiori Bloomberg //blp/instruments curve identity exploration (Issue #165 round 5)")
    print(f"Search terms: {', '.join(search_terms)}")
    print("")

    try:
        report = explore_instruments_service(search_terms)
    except ImportError as exc:
        print(
            "error: blpapi is not installed -- run this on a Bloomberg-networked "
            f"workstation with Bloomberg's official blpapi package installed ({exc})",
            file=sys.stderr,
        )
        return 2

    data = build_report(report)
    markdown_path, json_path = write_report(data, output_dir)

    print(f"Service opened: {data['service']['opened']}")
    if data["service"]["open_error"]:
        print(f"  open_error: {data['service']['open_error']}")
    print(f"Operations discovered: {len(data['service']['operations'])}")
    print(
        "Curve-related operations: "
        + (", ".join(data["curve_related_operation_names"]) or "(none)")
    )
    print("")
    for attempt in data["search_attempts"]:
        print(
            f"  - {attempt['operation_name']} / {attempt['request_element_used']} = "
            f"{attempt['term']!r} -> {attempt['status']}"
        )
    print("")
    if data["blocker_note"]:
        print(f"Status: {data['blocker_note']}")

    print("")
    print("Full report (paste back or attach either file):")
    print(f"  {markdown_path.resolve()}")
    print(f"  {json_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
