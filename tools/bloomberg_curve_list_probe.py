"""Bloomberg //blp/instruments curveListRequest probe (Issue #165 round 6).

Bounded, read-only diagnostic CLI -- **not** part of the production pricing
or ingestion path, and never imported by either. Round 5's exploratory
sweep of ``//blp/instruments`` found a real, schema-documented operation:
``curveListRequest``, confirmed live against Bloomberg's own schema with:

- request elements ``currencyCode`` (3-character currency filter), ``type``
  (enum including ``IRS``), ``subtype`` (enum including ``OIS``),
  ``maxResults`` (``0`` means all available results);
- response ``CurveRecord`` elements ``curve``, ``description``,
  ``country``, ``currency``, ``curveid``, ``type``, ``subtype``,
  ``publisher``, ``bbgid``.

Round 5 also separately tried ``curveid="490"`` as an input filter and got
zero records back -- so **UI "Curve #490" must not be assumed equal to any
API ``curveid`` value** until Bloomberg's own response says so. This round
stops the generic ``FieldSearchRequest``/STRING-fuzzing approach entirely
and sends exactly the one schema-confirmed filter combination instead:
``currencyCode=USD``, ``type=IRS``, ``subtype=OIS``, ``maxResults=0``.

**Re-confirms ``curveListRequest`` via this run's own introspection before
sending anything** (reusing ``bloomberg_curve_discovery_probe.discover_service``
unmodified) -- never assumed present just because a prior round saw it.

**Parses every returned ``CurveRecord`` structurally** into its own nine
named fields (never invented names -- exactly Eddy's confirmed schema
list above). The wrapping response array's own element name is not
confirmed by any evidence this repo has, so it is discovered structurally
by probing a small set of plausible candidate names via ``hasElement``
(never asserted, mirroring round 4's same-shaped ``category`` probe) --
the whole raw response text is always captured too, so nothing is lost if
none of those candidates match.

**No candidate is promoted to "the" answer by name-guessing.** A record is
only labeled a SOFR candidate because Bloomberg's own ``curve`` or
``description`` field for that record contains the literal substring
"SOFR" -- every raw record is still reported regardless. Exactly one SOFR
candidate is reported as *the* USD SOFR API identity candidate without
asserting its ``curveid`` equals terminal Curve #490 -- unless that
record's own ``curveid`` field literally equals ``"490"``, which is
Bloomberg's own response supplying that mapping, not a guess by this
script. More than one SOFR candidate stops here and reports all of them;
resolving between them is explicitly deferred to a following round.

**One command, run once.** Eddy runs::

    python tools/bloomberg_curve_list_probe.py

and pastes back the console output (or the written report files).

**Deliberately not in this script.** No ``SW174``/``SW173`` value request
of any kind -- even a clean single-match result this run only gets
*reported*, never chained into a further value request in the same run.
No production loader, no pricing, no OIS bootstrap.

**Separation from production/pricing.** Never imported by anything under
``src/``. Reuses ``bloomberg_curve_discovery_probe.discover_service``
(schema introspection, unmodified), ``bloomberg_curve_identity_search_probe
._optional_element_string`` (structural field-presence probing, unmodified),
and ``bloomberg_dapi_probe._send_request`` (the one place this script sends
a live request) -- no new session/request implementation.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bloomberg_curve_discovery_probe import _safe_str, discover_service
from bloomberg_curve_identity_search_probe import _optional_element_string
from bloomberg_dapi_probe import _send_request  # shared session/event-loop plumbing
from bloomberg_input_sourcing_probe import sanitize_external_text

_INSTRUMENTS_SERVICE = "//blp/instruments"

# Confirmed live against Bloomberg's own //blp/instruments schema
# (Issue #165 round 5/6 workstation evidence) -- not guessed.
_PROVEN_OPERATION = "curveListRequest"

# The exact, and only, filter this round sends -- Eddy's schema-confirmed
# request elements and values, verbatim. No other filter combination is
# tried; this is not a search sweep.
_REQUEST_FILTER: tuple[tuple[str, object], ...] = (
    ("currencyCode", "USD"),
    ("type", "IRS"),
    ("subtype", "OIS"),
    ("maxResults", 0),
)

# Confirmed CurveRecord field names (Issue #165 round 5/6 workstation
# schema evidence) -- not guessed.
CURVE_RECORD_FIELDS: tuple[str, ...] = (
    "curve",
    "description",
    "country",
    "currency",
    "curveid",
    "type",
    "subtype",
    "publisher",
    "bbgid",
)

# Candidate names for the response's wrapping results array -- none of
# these is confirmed; each is probed structurally via hasElement, never
# asserted present. The whole raw response text is always captured too.
_CANDIDATE_RESULTS_ELEMENT_NAMES: tuple[str, ...] = (
    "results",
    "result",
    "curves",
    "curveList",
    "curveData",
    "records",
    "data",
    "CurveRecord",
)

# UI "Curve #490" as Eddy reads it on the workstation -- compared only
# against a SOFR-labeled record's own returned curveid field, never
# assumed or searched for as an input.
_TERMINAL_CURVE_NUMBER = "490"

DEFAULT_OUTPUT_DIRNAME = "shiori_curve_list_output"
MARKDOWN_FILENAME = "bloomberg_curve_list_probe.md"
JSON_FILENAME = "bloomberg_curve_list_probe.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class CurveRecord:
    curve: str | None
    description: str | None
    country: str | None
    currency: str | None
    curveid: str | None
    type: str | None
    subtype: str | None
    publisher: str | None
    bbgid: str | None
    raw_record_dump: str | None
    mentions_sofr: bool
    curveid_matches_terminal_490: bool


@dataclass(frozen=True)
class CurveListReport:
    generated_at: str
    operation_confirmed: bool
    service_open_error: str | None
    request_status: str  # "not_attempted" | "sent" | "error"
    request_error: str | None
    results_array_element_name: str | None
    records: tuple[CurveRecord, ...]
    raw_response_dump: str | None
    sofr_candidates: tuple[CurveRecord, ...]
    identity_conclusion: str
    blocker_note: str


def _find_results_array(message):
    """Best-effort: find the response's wrapping results array.

    Tries each of :data:`_CANDIDATE_RESULTS_ELEMENT_NAMES` structurally via
    ``hasElement`` -- none is asserted present. Returns
    ``(element_name, element)`` for the first match, or ``(None, None)`` if
    none of the candidates matched this response.
    """

    for name in _CANDIDATE_RESULTS_ELEMENT_NAMES:
        try:
            if message.hasElement(name):
                return name, message.getElement(name)
        except Exception:  # noqa: BLE001 -- structural probe only, never fatal
            continue
    return None, None


def _parse_curve_record(record) -> CurveRecord:
    values = {field: _optional_element_string(record, field) for field in CURVE_RECORD_FIELDS}
    sanitized = {
        field: sanitize_external_text(value) if value is not None else None
        for field, value in values.items()
    }
    curve_text = sanitized["curve"] or ""
    description_text = sanitized["description"] or ""
    mentions_sofr = "sofr" in curve_text.lower() or "sofr" in description_text.lower()
    curveid_matches = sanitized["curveid"] == _TERMINAL_CURVE_NUMBER
    return CurveRecord(
        curve=sanitized["curve"],
        description=sanitized["description"],
        country=sanitized["country"],
        currency=sanitized["currency"],
        curveid=sanitized["curveid"],
        type=sanitized["type"],
        subtype=sanitized["subtype"],
        publisher=sanitized["publisher"],
        bbgid=sanitized["bbgid"],
        raw_record_dump=sanitize_external_text(_safe_str(record)),
        mentions_sofr=mentions_sofr,
        curveid_matches_terminal_490=curveid_matches,
    )


def run_curve_list_probe(
    send_request=_send_request,
    discover_service_fn=None,
) -> CurveListReport:
    """Send exactly Eddy's schema-confirmed ``curveListRequest`` filter and parse the response.

    Re-confirms ``curveListRequest`` via this run's own ``discover_service``
    introspection first; an unconfirmed operation or an unopenable service
    both stop here with a precise ``blocker_note``, and nothing is sent.
    """

    discover_service_fn = discover_service_fn or discover_service
    generated_at = _utc_now()
    service = discover_service_fn(_INSTRUMENTS_SERVICE)

    if not service.opened:
        blocker_note = (
            f"{_INSTRUMENTS_SERVICE} could not be opened this run ({service.open_error}). "
            f"{_PROVEN_OPERATION} could not be re-confirmed, so no request was sent."
        )
        return CurveListReport(
            generated_at=generated_at,
            operation_confirmed=False,
            service_open_error=service.open_error,
            request_status="not_attempted",
            request_error=None,
            results_array_element_name=None,
            records=(),
            raw_response_dump=None,
            sofr_candidates=(),
            identity_conclusion="SERVICE_NOT_OPENED",
            blocker_note=blocker_note,
        )

    operation_confirmed = any(op.name == _PROVEN_OPERATION for op in service.operations)
    if not operation_confirmed:
        blocker_note = (
            f"{_INSTRUMENTS_SERVICE} opened, but this run's own introspection did not "
            f"report an operation named {_PROVEN_OPERATION!r} (the one a prior round's "
            "workstation evidence confirmed). No request was sent -- review the full "
            "schema dump from bloomberg_curve_instruments_service_probe.py by hand rather "
            "than assuming the operation is still there."
        )
        return CurveListReport(
            generated_at=generated_at,
            operation_confirmed=False,
            service_open_error=None,
            request_status="not_attempted",
            request_error=None,
            results_array_element_name=None,
            records=(),
            raw_response_dump=None,
            sofr_candidates=(),
            identity_conclusion="OPERATION_NOT_CONFIRMED",
            blocker_note=blocker_note,
        )

    def _configure(request) -> None:
        for name, value in _REQUEST_FILTER:
            request.set(name, value)

    raw_messages: list[str] = []
    parsed_records: list[CurveRecord] = []
    results_array_element_name: str | None = None

    def _collect(message) -> None:
        nonlocal results_array_element_name
        raw_messages.append(_safe_str(message))
        name, array_element = _find_results_array(message)
        if array_element is None:
            return
        results_array_element_name = name
        try:
            count = array_element.numValues()
        except Exception:  # noqa: BLE001
            return
        for i in range(count):
            try:
                record = array_element.getValueAsElement(i)
            except Exception:  # noqa: BLE001
                continue
            parsed_records.append(_parse_curve_record(record))

    try:
        send_request(
            service_uri=_INSTRUMENTS_SERVICE,
            request_name=_PROVEN_OPERATION,
            configure=_configure,
            collect=_collect,
            context=(
                f"{_PROVEN_OPERATION}(" + ", ".join(f"{k}={v!r}" for k, v in _REQUEST_FILTER) + ")"
            ),
        )
        request_status = "sent"
        request_error = None
    except (RuntimeError, ImportError) as exc:
        request_status = "error"
        request_error = sanitize_external_text(str(exc))

    raw_dump = "\n---\n".join(sanitize_external_text(text) for text in raw_messages if text) or None

    if request_status == "error":
        blocker_note = f"The {_PROVEN_OPERATION} request failed: {request_error}"
        identity_conclusion = "REQUEST_FAILED"
        return CurveListReport(
            generated_at=generated_at,
            operation_confirmed=True,
            service_open_error=None,
            request_status=request_status,
            request_error=request_error,
            results_array_element_name=None,
            records=(),
            raw_response_dump=raw_dump,
            sofr_candidates=(),
            identity_conclusion=identity_conclusion,
            blocker_note=blocker_note,
        )

    sofr_candidates = tuple(record for record in parsed_records if record.mentions_sofr)

    if not parsed_records:
        identity_conclusion = "NO_RECORDS_PARSED"
        blocker_note = (
            "No CurveRecord could be parsed from the response -- either Bloomberg genuinely "
            "returned zero records for currencyCode=USD/type=IRS/subtype=OIS, or the results "
            "array's own element name did not match any of this script's candidate names "
            f"({', '.join(_CANDIDATE_RESULTS_ELEMENT_NAMES)}). Review the raw response dump "
            "below by hand before concluding either way -- no candidate name or ticker is "
            "guessed as a fallback."
        )
    elif len(sofr_candidates) == 1:
        candidate = sofr_candidates[0]
        if candidate.curveid_matches_terminal_490:
            identity_conclusion = "SINGLE_SOFR_CANDIDATE_CURVEID_490_MATCH"
            blocker_note = (
                f"Exactly one USD/IRS/OIS record mentions SOFR, and its own curveid field "
                f"equals {_TERMINAL_CURVE_NUMBER!r} -- Bloomberg's own response ties this "
                "candidate to terminal Curve #490, not a guess by this script. Still no "
                "SW173/SW174 value request is sent in this run; that is the natural next "
                "round."
            )
        else:
            identity_conclusion = "SINGLE_SOFR_CANDIDATE_UNCONFIRMED_490"
            blocker_note = (
                "Exactly one USD/IRS/OIS record mentions SOFR -- reported as the USD SOFR "
                f"API identity candidate below. Its curveid does not literally equal "
                f"{_TERMINAL_CURVE_NUMBER!r}, so this script does NOT assert it is terminal "
                "Curve #490; that mapping needs either a further Bloomberg-supplied tie or "
                "Eddy's own workstation comparison, not an assumption here."
            )
    elif len(sofr_candidates) > 1:
        identity_conclusion = "MULTIPLE_SOFR_CANDIDATES"
        blocker_note = (
            f"{len(sofr_candidates)} USD/IRS/OIS records mention SOFR -- all reported below, "
            "none promoted. Resolving between them is explicitly deferred to a following "
            "round; no candidate is guessed as the answer here."
        )
    else:
        identity_conclusion = "NO_SOFR_CANDIDATES"
        blocker_note = (
            f"{len(parsed_records)} USD/IRS/OIS record(s) were parsed, but none mentioned "
            "SOFR in their own curve/description fields. All records are reported below for "
            "review -- no candidate is guessed as the answer here."
        )

    return CurveListReport(
        generated_at=generated_at,
        operation_confirmed=True,
        service_open_error=None,
        request_status=request_status,
        request_error=request_error,
        results_array_element_name=results_array_element_name,
        records=tuple(parsed_records),
        raw_response_dump=raw_dump,
        sofr_candidates=sofr_candidates,
        identity_conclusion=identity_conclusion,
        blocker_note=blocker_note,
    )


# --- rendering -------------------------------------------------------------------


def _record_to_dict(record: CurveRecord) -> dict:
    return {
        "curve": record.curve,
        "description": record.description,
        "country": record.country,
        "currency": record.currency,
        "curveid": record.curveid,
        "type": record.type,
        "subtype": record.subtype,
        "publisher": record.publisher,
        "bbgid": record.bbgid,
        "raw_record_dump": record.raw_record_dump,
        "mentions_sofr": record.mentions_sofr,
        "curveid_matches_terminal_490": record.curveid_matches_terminal_490,
    }


def build_report(report: CurveListReport) -> dict:
    return {
        "generated_at": report.generated_at,
        "operation_confirmed": report.operation_confirmed,
        "service_open_error": report.service_open_error,
        "request_filter": {k: v for k, v in _REQUEST_FILTER},
        "request_status": report.request_status,
        "request_error": report.request_error,
        "results_array_element_name": report.results_array_element_name,
        "records": [_record_to_dict(r) for r in report.records],
        "raw_response_dump": report.raw_response_dump,
        "sofr_candidates": [_record_to_dict(r) for r in report.sofr_candidates],
        "identity_conclusion": report.identity_conclusion,
        "blocker_note": report.blocker_note,
    }


def render_markdown(data: dict) -> str:
    lines: list[str] = []
    lines.append("# Bloomberg curveListRequest probe (Issue #165 round 6)")
    lines.append("")
    lines.append(f"Generated at: {data['generated_at']}")
    lines.append(f"Operation confirmed this run: {data['operation_confirmed']}")
    if data["service_open_error"]:
        lines.append(f"Service open error: {data['service_open_error']}")
    lines.append(f"Request filter: {data['request_filter']}")
    lines.append(f"Request status: {data['request_status']}")
    if data["request_error"]:
        lines.append(f"Request error: {data['request_error']}")
    lines.append(f"Results array element name found: {data['results_array_element_name']}")
    lines.append("")
    lines.append(f"## Identity conclusion: {data['identity_conclusion']}")
    lines.append("")
    lines.append(data["blocker_note"])
    lines.append("")

    lines.append(f"## SOFR candidates ({len(data['sofr_candidates'])})")
    lines.append("")
    for record in data["sofr_candidates"]:
        marker = " **[curveid == terminal 490]**" if record["curveid_matches_terminal_490"] else ""
        lines.append(f"- `{record['curve']}` (curveid={record['curveid']}){marker}")
        lines.append(f"  description: {record['description']}")
        lines.append(
            f"  country={record['country']} currency={record['currency']} "
            f"type={record['type']} subtype={record['subtype']} "
            f"publisher={record['publisher']} bbgid={record['bbgid']}"
        )
    lines.append("")

    lines.append(f"## All records ({len(data['records'])})")
    lines.append("")
    for record in data["records"]:
        sofr_marker = " (SOFR)" if record["mentions_sofr"] else ""
        lines.append(f"- `{record['curve']}`{sofr_marker} -- {record['description']}")
        lines.append(
            f"  country={record['country']} currency={record['currency']} "
            f"curveid={record['curveid']} type={record['type']} subtype={record['subtype']} "
            f"publisher={record['publisher']} bbgid={record['bbgid']}"
        )
    lines.append("")

    if data["raw_response_dump"]:
        lines.append("## Raw response dump")
        lines.append("")
        lines.append("```")
        lines.append(data["raw_response_dump"])
        lines.append("```")

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
            "Bounded read-only Bloomberg curveListRequest probe (Issue #165 round 6). "
            "Sends exactly currencyCode=USD/type=IRS/subtype=OIS/maxResults=0. Run with "
            "no arguments."
        )
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=f"Where to write the report (default: ./{DEFAULT_OUTPUT_DIRNAME}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd() / DEFAULT_OUTPUT_DIRNAME

    print("Shiori Bloomberg curveListRequest probe (Issue #165 round 6)")
    print("Filter: currencyCode=USD, type=IRS, subtype=OIS, maxResults=0")
    print("")

    try:
        report = run_curve_list_probe()
    except ImportError as exc:
        print(
            "error: blpapi is not installed -- run this on a Bloomberg-networked "
            f"workstation with Bloomberg's official blpapi package installed ({exc})",
            file=sys.stderr,
        )
        return 2

    data = build_report(report)
    markdown_path, json_path = write_report(data, output_dir)

    print(f"Operation confirmed: {data['operation_confirmed']}")
    print(f"Request status: {data['request_status']}")
    print(f"Records parsed: {len(data['records'])}")
    print(f"SOFR candidates: {len(data['sofr_candidates'])}")
    print(f"Identity conclusion: {data['identity_conclusion']}")
    print("")
    print(data["blocker_note"])

    print("")
    print("Full report (paste back or attach either file):")
    print(f"  {markdown_path.resolve()}")
    print(f"  {json_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
