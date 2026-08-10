"""Bloomberg SW173/SW174 curve value probe (Issue #165 round 7 -- shortest validation path).

Bounded, read-only diagnostic CLI -- **not** part of the production pricing
or ingestion path, and never imported by either. Rounds 4-6 spent several
rounds trying to *derive* Curve #490's own security identifier from
Bloomberg metadata (``FieldSearchRequest`` sweeps, ``curveListRequest``
taxonomy). This round stops that derivation effort and takes the shortest
path instead: Eddy already knows, from the terminal itself, that the
security he wants is written ``S490`` -- so this script sends exactly that,
as an **explicit, Eddy-supplied input**, and asks Bloomberg directly for
the two confirmed stripped-curve bulk fields.

**The security identifier is never derived, inferred, or guessed by this
script.** ``--security`` defaults to the literal string ``S490`` because
that is Eddy's own first workstation input, supplied verbatim in Issue
#165 round 7 -- not a value this script computed. If Bloomberg rejects it
(``securityError``, e.g. ``BAD_SEC``), this script stops and reports
Bloomberg's own error text verbatim; it never tries a guessed suffix or
yellow-key variant as a fallback. A corrected identifier is always
Eddy's own next explicit ``--security`` value, never something this script
invents.

**Requests exactly two fields, already confirmed by prior rounds' own
Bloomberg documentation evidence, never guessed:**

- ``SW173`` (``SW_CRV_DISCOUNT_FACTORS``)
- ``SW174`` (``SW_CRV_ZERO_RATES``)

via one ``ReferenceDataRequest`` against ``//blp/refdata``, reusing the
same security-level validation ``data/bloomberg_bond_quote.py``'s
production loaders already use and this repo already trusts: every
``securityData`` record is collected across all ``PARTIAL_RESPONSE``/
``RESPONSE`` events before anything is read, exactly one record is
required, ``securityError`` is checked and surfaced verbatim, and
``fieldExceptions`` is checked per field.

**Bulk rows are parsed generically, never against a guessed column name.**
``SW173``/``SW174`` are ``BulkFormat`` fields -- each one's value is an
array of rows, and this repo has no confirmed evidence for what Bloomberg
calls each row's own sub-elements (a date column, a rate column, a
discount-factor column, ...). Rather than guess those names, each row is
flattened generically via ``blpapi``'s own ``Element.elements()``
iteration -- this requires no prior knowledge of a row's element names at
all, so nothing here is a guess. Every row's own raw ``str()`` dump is
captured alongside the flattened fields regardless, so a human can always
read the row even if the generic flatten fails on a given ``blpapi``
build.

**One command, run once.** Eddy runs::

    python tools/bloomberg_curve_value_probe.py

(or ``--security <corrected identifier>`` if ``S490`` comes back
``BAD_SEC``) and pastes back the console output (or the written report
files).

**Manual validation criterion (Issue #165 round 7's own words, not
asserted here).** This script makes no claim that the returned rows match
terminal Curve #490's SWDF screen. Eddy picks a few tenor nodes from
terminal Curve #490 / SWDF and compares Date / Zero Rate / Discount Factor
against the rows this script prints. Only if they agree can this
acquisition route be promoted to Issue #165's Bloomberg evidence -- that
promotion decision is Eddy's, not this script's.

**Deliberately not in this script.** No production loader, no
``BLICurvePoint`` construction, no pricing, no OIS bootstrap, no wiring
into any pricing or workbench code.

**Separation from production/pricing.** Never imported by anything under
``src/``. Reuses ``bloomberg_curve_discovery_probe._safe_str`` and
``bloomberg_dapi_probe._send_request`` (the one place this script sends a
live request) -- no new session/request implementation.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bloomberg_curve_discovery_probe import _safe_str
from bloomberg_dapi_probe import _send_request  # shared session/event-loop plumbing
from bloomberg_input_sourcing_probe import sanitize_external_text

_REFDATA_SERVICE = "//blp/refdata"

# Eddy's own first workstation input, verbatim (Issue #165 round 7) -- not
# derived or guessed by this script. A rejected identifier is corrected via
# --security, never by this script trying a suffix/yellow-key variant.
DEFAULT_SECURITY = "S490"

# Confirmed field mnemonics -- SW_CRV_DISCOUNT_FACTORS / SW_CRV_ZERO_RATES,
# already documented in prior rounds' own Bloomberg FieldInfoRequest
# evidence. Not guessed here.
BULK_FIELDS: tuple[str, ...] = ("SW173", "SW174")

DEFAULT_OUTPUT_DIRNAME = "shiori_curve_value_output"
MARKDOWN_FILENAME = "bloomberg_curve_value_probe.md"
JSON_FILENAME = "bloomberg_curve_value_probe.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class BulkFieldRow:
    index: int
    fields: dict[str, str]
    raw_row_dump: str | None


@dataclass(frozen=True)
class BulkFieldResult:
    field: str
    status: str  # "returned_bulk" | "present_non_bulk_or_empty" | "field_exception" | "absent"
    row_count: int
    rows: tuple[BulkFieldRow, ...]
    scalar_value: str | None
    detail: str | None


@dataclass(frozen=True)
class CurveValueProbeReport:
    generated_at: str
    security: str
    request_status: str  # "sent" | "error"
    request_error: str | None
    record_count_error: str | None
    security_error: str | None
    field_results: tuple[BulkFieldResult, ...]
    raw_response_dump: str | None
    blocker_note: str


def _flatten_row(row_element) -> dict[str, str] | None:
    """Best-effort: generically enumerate one bulk row's own named sub-elements.

    Uses ``blpapi``'s own generic ``Element.elements()`` iteration -- this
    requires no prior knowledge of SW173/SW174's actual per-row column
    names (unconfirmed by any evidence this repo has), so nothing here is
    a guess. Returns ``None`` if this row's structure can't be walked this
    way on this ``blpapi`` build; the caller always keeps this row's own
    raw ``str()`` dump as a fallback regardless.
    """

    try:
        pairs: dict[str, str] = {}
        for element in row_element.elements():
            try:
                name = str(element.name())
                value = element.getValueAsString()
            except Exception:  # noqa: BLE001
                continue
            pairs[name] = value
        return pairs or None
    except Exception:  # noqa: BLE001
        return None


def run_curve_value_probe(
    security: str = DEFAULT_SECURITY,
    send_request=_send_request,
) -> CurveValueProbeReport:
    """Send one ``ReferenceDataRequest`` for ``security``/``BULK_FIELDS`` and parse the response.

    ``security`` must be a non-blank string, explicitly supplied by the
    caller (default: Eddy's own literal ``"S490"`` first input) -- never
    derived. Raises :class:`ValueError` for a blank/non-string security,
    the same caller-input-problem contract this repo's other Bloomberg
    loaders already use.
    """

    if not isinstance(security, str) or not security.strip():
        raise ValueError("security must be a non-blank string, explicitly supplied by the caller")
    security = security.strip()
    generated_at = _utc_now()

    def _configure(request) -> None:
        request.append("securities", security)
        for field in BULK_FIELDS:
            request.append("fields", field)

    raw_messages: list[str] = []
    security_data_records: list = []

    def _collect(message) -> None:
        raw_messages.append(_safe_str(message))
        try:
            if not message.hasElement("securityData"):
                return
            array = message.getElement("securityData")
            for i in range(array.numValues()):
                security_data_records.append(array.getValueAsElement(i))
        except Exception:  # noqa: BLE001
            return

    try:
        send_request(
            service_uri=_REFDATA_SERVICE,
            request_name="ReferenceDataRequest",
            configure=_configure,
            collect=_collect,
            context=f"ReferenceDataRequest({security!r}, fields={list(BULK_FIELDS)})",
        )
        request_status = "sent"
        request_error = None
    except (RuntimeError, ImportError) as exc:
        request_status = "error"
        request_error = sanitize_external_text(str(exc))

    raw_dump = "\n---\n".join(sanitize_external_text(text) for text in raw_messages if text) or None

    if request_status == "error":
        return CurveValueProbeReport(
            generated_at=generated_at,
            security=security,
            request_status=request_status,
            request_error=request_error,
            record_count_error=None,
            security_error=None,
            field_results=(),
            raw_response_dump=raw_dump,
            blocker_note=f"The ReferenceDataRequest failed: {request_error}",
        )

    if len(security_data_records) != 1:
        record_count_error = (
            f"Bloomberg returned {len(security_data_records)} securityData record(s) for "
            f"{security!r}, expected exactly one."
        )
        return CurveValueProbeReport(
            generated_at=generated_at,
            security=security,
            request_status=request_status,
            request_error=None,
            record_count_error=record_count_error,
            security_error=None,
            field_results=(),
            raw_response_dump=raw_dump,
            blocker_note=f"{record_count_error} Review the raw response dump below.",
        )

    record = security_data_records[0]

    security_error: str | None = None
    try:
        if record.hasElement("securityError"):
            security_error = sanitize_external_text(_safe_str(record.getElement("securityError")))
    except Exception:  # noqa: BLE001
        pass

    if security_error is not None:
        blocker_note = (
            f"Bloomberg rejected security identifier {security!r} verbatim: {security_error}. "
            "No suffix or yellow-key variant is guessed as a fallback -- rerun with a "
            "corrected --security supplied explicitly."
        )
        return CurveValueProbeReport(
            generated_at=generated_at,
            security=security,
            request_status=request_status,
            request_error=None,
            record_count_error=None,
            security_error=security_error,
            field_results=(),
            raw_response_dump=raw_dump,
            blocker_note=blocker_note,
        )

    field_exception_details: dict[str, str] = {}
    try:
        if record.hasElement("fieldExceptions"):
            exceptions = record.getElement("fieldExceptions")
            for i in range(exceptions.numValues()):
                exception_element = exceptions.getValueAsElement(i)
                field_id = None
                try:
                    if exception_element.hasElement("fieldId"):
                        field_id = exception_element.getElementAsString("fieldId")
                except Exception:  # noqa: BLE001
                    field_id = None
                detail = _safe_str(exception_element)
                try:
                    if exception_element.hasElement("errorInfo"):
                        detail = _safe_str(exception_element.getElement("errorInfo"))
                except Exception:  # noqa: BLE001
                    pass
                if field_id:
                    field_exception_details[field_id] = sanitize_external_text(detail)
    except Exception:  # noqa: BLE001
        pass

    field_data = None
    try:
        if record.hasElement("fieldData"):
            field_data = record.getElement("fieldData")
    except Exception:  # noqa: BLE001
        field_data = None

    field_results: list[BulkFieldResult] = []
    for field in BULK_FIELDS:
        has_field = False
        try:
            has_field = field_data is not None and field_data.hasElement(field)
        except Exception:  # noqa: BLE001
            has_field = False

        if has_field:
            field_element = field_data.getElement(field)
            try:
                row_count = field_element.numValues()
            except Exception:  # noqa: BLE001
                row_count = None

            if row_count:
                rows: list[BulkFieldRow] = []
                for i in range(row_count):
                    try:
                        row_element = field_element.getValueAsElement(i)
                    except Exception:  # noqa: BLE001
                        continue
                    flattened = _flatten_row(row_element)
                    sanitized_fields = (
                        {k: sanitize_external_text(v) for k, v in flattened.items()}
                        if flattened
                        else {}
                    )
                    rows.append(
                        BulkFieldRow(
                            index=i,
                            fields=sanitized_fields,
                            raw_row_dump=sanitize_external_text(_safe_str(row_element)),
                        )
                    )
                field_results.append(
                    BulkFieldResult(
                        field=field,
                        status="returned_bulk",
                        row_count=row_count,
                        rows=tuple(rows),
                        scalar_value=None,
                        detail=None,
                    )
                )
            else:
                scalar_value = None
                try:
                    scalar_value = sanitize_external_text(field_data.getElementAsString(field))
                except Exception:  # noqa: BLE001
                    scalar_value = None
                field_results.append(
                    BulkFieldResult(
                        field=field,
                        status="present_non_bulk_or_empty",
                        row_count=row_count or 0,
                        rows=(),
                        scalar_value=scalar_value,
                        detail=None,
                    )
                )
        elif field in field_exception_details:
            field_results.append(
                BulkFieldResult(
                    field=field,
                    status="field_exception",
                    row_count=0,
                    rows=(),
                    scalar_value=None,
                    detail=field_exception_details[field],
                )
            )
        else:
            field_results.append(
                BulkFieldResult(
                    field=field,
                    status="absent",
                    row_count=0,
                    rows=(),
                    scalar_value=None,
                    detail=None,
                )
            )

    if any(result.status == "returned_bulk" for result in field_results):
        blocker_note = (
            "Bulk rows returned below for SW173/SW174 -- compare Date/Zero Rate/Discount "
            "Factor point-by-point against terminal Curve #490's SWDF screen for a few tenor "
            "nodes. If they agree, this acquisition route can be promoted to Issue #165's "
            "Bloomberg evidence; that promotion decision is Eddy's, not this script's."
        )
    else:
        blocker_note = (
            "No bulk rows were returned for SW173 or SW174 against this security -- review "
            "each field's own status/detail below before concluding whether the security "
            "identifier is wrong or the fields are genuinely inapplicable here. No fallback "
            "identifier is guessed."
        )

    return CurveValueProbeReport(
        generated_at=generated_at,
        security=security,
        request_status=request_status,
        request_error=None,
        record_count_error=None,
        security_error=None,
        field_results=tuple(field_results),
        raw_response_dump=raw_dump,
        blocker_note=blocker_note,
    )


# --- rendering -------------------------------------------------------------------


def _row_to_dict(row: BulkFieldRow) -> dict:
    return {"index": row.index, "fields": row.fields, "raw_row_dump": row.raw_row_dump}


def _field_result_to_dict(result: BulkFieldResult) -> dict:
    return {
        "field": result.field,
        "status": result.status,
        "row_count": result.row_count,
        "rows": [_row_to_dict(r) for r in result.rows],
        "scalar_value": result.scalar_value,
        "detail": result.detail,
    }


def build_report(report: CurveValueProbeReport) -> dict:
    return {
        "generated_at": report.generated_at,
        "security": report.security,
        "fields_requested": list(BULK_FIELDS),
        "request_status": report.request_status,
        "request_error": report.request_error,
        "record_count_error": report.record_count_error,
        "security_error": report.security_error,
        "field_results": [_field_result_to_dict(r) for r in report.field_results],
        "raw_response_dump": report.raw_response_dump,
        "blocker_note": report.blocker_note,
    }


def render_markdown(data: dict) -> str:
    lines: list[str] = []
    lines.append("# Bloomberg SW173/SW174 curve value probe (Issue #165 round 7)")
    lines.append("")
    lines.append(f"Generated at: {data['generated_at']}")
    lines.append(f"Security (Eddy-supplied, verbatim): {data['security']}")
    lines.append(f"Fields requested: {', '.join(data['fields_requested'])}")
    lines.append(f"Request status: {data['request_status']}")
    if data["request_error"]:
        lines.append(f"Request error: {data['request_error']}")
    if data["record_count_error"]:
        lines.append(f"Record count error: {data['record_count_error']}")
    if data["security_error"]:
        lines.append(f"Security error (Bloomberg's own text, verbatim): {data['security_error']}")
    lines.append("")

    for result in data["field_results"]:
        lines.append(f"## {result['field']}: {result['status']} ({result['row_count']} rows)")
        if result["detail"]:
            lines.append(f"Detail: {result['detail']}")
        if result["scalar_value"] is not None:
            lines.append(f"Scalar value (unexpectedly non-bulk): {result['scalar_value']}")
        for row in result["rows"]:
            lines.append(f"- row {row['index']}: {row['fields'] or '(no flattened fields)'}")
            if row["raw_row_dump"]:
                lines.append("  raw:")
                lines.append("  ```")
                lines.append(f"  {row['raw_row_dump']}")
                lines.append("  ```")
        lines.append("")

    lines.append("## Status")
    lines.append("")
    lines.append(data["blocker_note"])
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
            "Bounded read-only Bloomberg SW173/SW174 curve value probe (Issue #165 round "
            "7). Requests exactly SW173/SW174 for one explicit security identifier."
        )
    )
    parser.add_argument(
        "--security",
        default=DEFAULT_SECURITY,
        help=(
            "Bloomberg security identifier to request, supplied explicitly -- never derived "
            f"or guessed by this script. Default: {DEFAULT_SECURITY!r} (Eddy's own literal "
            "first workstation input)."
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
    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd() / DEFAULT_OUTPUT_DIRNAME

    print("Shiori Bloomberg SW173/SW174 curve value probe (Issue #165 round 7)")
    print(f"Security: {args.security!r}  Fields: {', '.join(BULK_FIELDS)}")
    print("")

    try:
        report = run_curve_value_probe(args.security)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ImportError as exc:
        print(
            "error: blpapi is not installed -- run this on a Bloomberg-networked "
            f"workstation with Bloomberg's official blpapi package installed ({exc})",
            file=sys.stderr,
        )
        return 2

    data = build_report(report)
    markdown_path, json_path = write_report(data, output_dir)

    print(f"Request status: {data['request_status']}")
    if data["security_error"]:
        print(f"Security error: {data['security_error']}")
    for result in data["field_results"]:
        print(f"  - {result['field']}: {result['status']} ({result['row_count']} rows)")
    print("")
    print(data["blocker_note"])

    print("")
    print("Full report (paste back or attach either file):")
    print(f"  {markdown_path.resolve()}")
    print(f"  {json_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
