"""Bloomberg USD curveListRequest SOFR ticker probe (Issue #165 round 10).

Bounded, read-only diagnostic CLI -- **not** part of the production pricing
or ingestion path, and never imported by either. Round 9's live evidence
was conclusive: no-override, ``SW569=Y``, and ``SW564=Y`` all return
materially the same USD default zero curve via ``SW174``, and all
materially differ from terminal Curve #490 / SWDF. **This route -- guessing
at ``SW174`` overrides -- is closed.** No further override is tried by
this or any following round.

This round goes back to ``//blp/instruments``'s ``curveListRequest`` --
already live-confirmed working in round 6 -- but with a **much narrower
filter** than round 6 used, and a **different goal**. Round 6 filtered by
``currencyCode=USD``/``type=IRS``/``subtype=OIS`` looking for Curve #490's
own identity. This round sends only:

- ``currencyCode = "USD"``
- ``maxResults = 0`` (all available results)

**``type``/``subtype``/``query``/``curveid``/``bbgid`` are deliberately not
set.** The goal this round is **not** Curve #490 identity proof -- it is to
obtain Bloomberg's own returned curve ticker(s) for USD SOFR, verbatim, so
they can be directly tried as ``SW174`` acquisition inputs in a following
round. A record is flagged only because Bloomberg's own ``curve`` or
``description`` field contains the literal substring "SOFR" -- every raw
record is still reported regardless of that flag, and this script never
chooses one record over another or sends any ``SW174`` request itself.

**Re-confirms ``curveListRequest`` via this run's own introspection before
sending anything** (reusing ``bloomberg_curve_discovery_probe.discover_service``
unmodified) -- never assumed present just because a prior round saw it.

**Parses every returned ``CurveRecord`` structurally** into the same nine
named fields round 6 already confirmed (``curve``, ``description``,
``country``, ``currency``, ``curveid``, ``type``, ``subtype``,
``publisher``, ``bbgid``). The wrapping results array's own element name is
discovered structurally via the same candidate-name probe round 6 already
built and tested (imported unchanged) -- never asserted, with the full raw
response always captured as a fallback.

**One command, run once.** Eddy runs::

    python tools/bloomberg_curve_usd_sofr_ticker_probe.py

and pastes back the console output (or the written report files).

**Deliberately not in this script.** No ``SW174`` request of any kind, no
choosing between multiple SOFR records, no production loader, no pricing,
no OIS bootstrap, no wiring into any pricing or workbench code.

**Separation from production/pricing.** Never imported by anything under
``src/``. Reuses ``bloomberg_curve_discovery_probe.discover_service``
(schema introspection, unmodified), ``bloomberg_curve_list_probe``'s own
``_PROVEN_OPERATION``/``_find_results_array``/``CURVE_RECORD_FIELDS``
(unchanged), ``bloomberg_curve_identity_search_probe._optional_element_string``,
and ``bloomberg_dapi_probe._send_request`` -- no new session/request or
results-array-discovery implementation.
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
from bloomberg_curve_list_probe import (
    _PROVEN_OPERATION,
    CURVE_RECORD_FIELDS,
    _find_results_array,
)
from bloomberg_dapi_probe import _send_request  # shared session/event-loop plumbing
from bloomberg_input_sourcing_probe import sanitize_external_text

_INSTRUMENTS_SERVICE = "//blp/instruments"

# Exactly this round's filter, nothing else -- currencyCode=USD,
# maxResults=0 only. type/subtype/query/curveid/bbgid are deliberately not
# set (Issue #165 round 10).
_REQUEST_FILTER: tuple[tuple[str, object], ...] = (
    ("currencyCode", "USD"),
    ("maxResults", 0),
)

DEFAULT_OUTPUT_DIRNAME = "shiori_curve_usd_sofr_ticker_output"
MARKDOWN_FILENAME = "bloomberg_curve_usd_sofr_ticker_probe.md"
JSON_FILENAME = "bloomberg_curve_usd_sofr_ticker_probe.json"


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


@dataclass(frozen=True)
class UsdCurveListReport:
    generated_at: str
    operation_confirmed: bool
    service_open_error: str | None
    request_status: str  # "not_attempted" | "sent" | "error"
    request_error: str | None
    results_array_element_name: str | None
    records: tuple[CurveRecord, ...]
    raw_response_dump: str | None
    sofr_records: tuple[CurveRecord, ...]
    blocker_note: str


def _parse_curve_record(record) -> CurveRecord:
    values = {field: _optional_element_string(record, field) for field in CURVE_RECORD_FIELDS}
    sanitized = {
        field: sanitize_external_text(value) if value is not None else None
        for field, value in values.items()
    }
    curve_text = sanitized["curve"] or ""
    description_text = sanitized["description"] or ""
    mentions_sofr = "sofr" in curve_text.lower() or "sofr" in description_text.lower()
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
    )


def run_usd_curve_list_probe(
    send_request=_send_request,
    discover_service_fn=None,
) -> UsdCurveListReport:
    """Send exactly ``currencyCode=USD``/``maxResults=0`` to ``curveListRequest`` and parse it.

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
        return UsdCurveListReport(
            generated_at=generated_at,
            operation_confirmed=False,
            service_open_error=service.open_error,
            request_status="not_attempted",
            request_error=None,
            results_array_element_name=None,
            records=(),
            raw_response_dump=None,
            sofr_records=(),
            blocker_note=blocker_note,
        )

    operation_confirmed = any(op.name == _PROVEN_OPERATION for op in service.operations)
    if not operation_confirmed:
        blocker_note = (
            f"{_INSTRUMENTS_SERVICE} opened, but this run's own introspection did not "
            f"report an operation named {_PROVEN_OPERATION!r}. No request was sent."
        )
        return UsdCurveListReport(
            generated_at=generated_at,
            operation_confirmed=False,
            service_open_error=None,
            request_status="not_attempted",
            request_error=None,
            results_array_element_name=None,
            records=(),
            raw_response_dump=None,
            sofr_records=(),
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
        return UsdCurveListReport(
            generated_at=generated_at,
            operation_confirmed=True,
            service_open_error=None,
            request_status=request_status,
            request_error=request_error,
            results_array_element_name=None,
            records=(),
            raw_response_dump=raw_dump,
            sofr_records=(),
            blocker_note=f"The {_PROVEN_OPERATION} request failed: {request_error}",
        )

    sofr_records = tuple(record for record in parsed_records if record.mentions_sofr)

    if not parsed_records:
        blocker_note = (
            "No CurveRecord could be parsed from the response -- either Bloomberg genuinely "
            "returned zero records for currencyCode=USD, or the results array's own element "
            "name did not match any candidate name this script tries. Review the raw response "
            "dump below by hand."
        )
    elif sofr_records:
        blocker_note = (
            f"{len(sofr_records)} of {len(parsed_records)} USD CurveRecord(s) mention SOFR -- "
            "reported below. This is not Curve #490 identity proof; the goal is to obtain "
            "Bloomberg's own curve ticker(s) so they can be tried directly as SW174 "
            "acquisition inputs in a following round. No record is chosen here, and no "
            "SW174 request is sent by this script."
        )
    else:
        blocker_note = (
            f"{len(parsed_records)} USD CurveRecord(s) were returned, but none mentioned "
            "SOFR in their own curve/description fields. All records are reported below for "
            "review."
        )

    return UsdCurveListReport(
        generated_at=generated_at,
        operation_confirmed=True,
        service_open_error=None,
        request_status=request_status,
        request_error=None,
        results_array_element_name=results_array_element_name,
        records=tuple(parsed_records),
        raw_response_dump=raw_dump,
        sofr_records=sofr_records,
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
    }


def build_report(report: UsdCurveListReport) -> dict:
    return {
        "generated_at": report.generated_at,
        "operation_confirmed": report.operation_confirmed,
        "service_open_error": report.service_open_error,
        "request_filter": dict(_REQUEST_FILTER),
        "request_status": report.request_status,
        "request_error": report.request_error,
        "results_array_element_name": report.results_array_element_name,
        "records": [_record_to_dict(r) for r in report.records],
        "raw_response_dump": report.raw_response_dump,
        "sofr_records": [_record_to_dict(r) for r in report.sofr_records],
        "blocker_note": report.blocker_note,
    }


def render_markdown(data: dict) -> str:
    lines: list[str] = []
    lines.append("# Bloomberg USD curveListRequest SOFR ticker probe (Issue #165 round 10)")
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
    lines.append(data["blocker_note"])
    lines.append("")

    lines.append(f"## SOFR-flagged records ({len(data['sofr_records'])})")
    lines.append("")
    for record in data["sofr_records"]:
        lines.append(
            f"- `{record['curve']}` (curveid={record['curveid']}, bbgid={record['bbgid']})"
        )
        lines.append(f"  description: {record['description']}")
        lines.append(
            f"  country={record['country']} currency={record['currency']} "
            f"type={record['type']} subtype={record['subtype']} publisher={record['publisher']}"
        )
    lines.append("")

    lines.append(f"## All USD records ({len(data['records'])})")
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
            "Bounded read-only Bloomberg USD curveListRequest SOFR ticker probe (Issue "
            "#165 round 10). Sends exactly currencyCode=USD/maxResults=0. Run with no "
            "arguments."
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

    print("Shiori Bloomberg USD curveListRequest SOFR ticker probe (Issue #165 round 10)")
    print("Filter: currencyCode=USD, maxResults=0 (type/subtype/query/curveid/bbgid not set)")
    print("")

    try:
        report = run_usd_curve_list_probe()
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
    print(f"SOFR-flagged records: {len(data['sofr_records'])}")
    print("")
    print(data["blocker_note"])

    print("")
    print("Full report (paste back or attach either file):")
    print(f"  {markdown_path.resolve()}")
    print(f"  {json_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
