"""Bloomberg SW174 zero-rate override probe (Issue #165 round 8).

Bounded, read-only diagnostic CLI -- **not** part of the production pricing
or ingestion path, and never imported by either. Round 7's live result was
conclusive: ``"S490"`` as a ``ReferenceDataRequest`` security returned
``BAD_SEC``/``INVALID_SECURITY``. This round stops probing ``S490`` as a
security entirely and takes the route Bloomberg's own ``FieldInfoRequest``
documentation for ``SW174``/``SW_CRV_ZERO_RATES`` actually describes: it
returns zero-coupon rates from "the default curve for the specified
currency", and its own documented overrides explicitly include ``SW569``
(``OVERNIGHT_INDEX_SWAP_DISCOUNTING``) and ``SW564``
(``DUAL_CURVE_STRIPPING_OPTION``).

**Sends exactly one minimal request:**

- security: ``"USD Curncy"`` -- an **explicit, Eddy-supplied workstation
  input** (default), never derived or guessed by this script. A rejection
  is corrected by Eddy's own next explicit ``--security`` value, never a
  suffix/ticker variant this script tries on its own.
- field: ``SW174`` (``SW_CRV_ZERO_RATES``) only -- **no ``SW173`` in this
  request**, deliberately, per this round's own scope.
- override: ``SW569`` (``OVERNIGHT_INDEX_SWAP_DISCOUNTING``) ``= "Y"`` --
  Eddy's own explicit instruction, sent via the same
  ``request.getElement("overrides")``/``appendElement()``/``setElement``
  pattern ``bloomberg_dapi_probe.probe_fields`` already uses and this repo
  already trusts.

**Reuses, not reimplements:** the security-level validation shape
(collect every ``securityData`` record across all response events, require
exactly one, check ``securityError`` before touching ``fieldData``,
check ``fieldExceptions`` per field) mirrors
``data/bloomberg_bond_quote.py``'s production loaders and round 7's
``bloomberg_curve_value_probe.py``. Bulk-row flattening reuses round 7's
own ``bloomberg_curve_value_probe._flatten_row`` unchanged -- generic
``blpapi`` ``Element.elements()`` iteration, no guessed column name, with
each row's raw ``str()`` dump always captured as a fallback.

**Stop conditions, all verbatim, never guessed around:**

- ``"USD Curncy"`` rejected (``securityError``, e.g. ``BAD_SEC``): stop,
  report Bloomberg's own error text verbatim, no fallback identifier tried.
- A field or override error on ``SW174``/``SW569`` (``fieldExceptions``):
  stop, report Bloomberg's own error text verbatim. Bloomberg's
  ``fieldExceptions`` array is keyed by field id, and this script cannot
  know in advance whether a rejected override surfaces under ``SW569`` or
  under the field it was applied to (``SW174``) -- both keys are checked
  and reported, never assumed to be one or the other.
- ``SW174`` returns bulk zero-rate rows: reported for Eddy's own manual
  comparison against terminal Curve #490 / SWDF. **This script asserts no
  match** -- that reading is Eddy's, not this script's.

**One command, run once.** Eddy runs::

    python tools/bloomberg_curve_zero_rate_override_probe.py

(or ``--security <corrected identifier>`` if ``"USD Curncy"`` is rejected)
and pastes back the console output (or the written report files).

**Deliberately not in this script.** No production loader, no
``BLICurvePoint`` construction, no pricing, no OIS bootstrap, no wiring
into any pricing or workbench code, no ``SW173`` request.

**Separation from production/pricing.** Never imported by anything under
``src/``. Reuses ``bloomberg_curve_discovery_probe._safe_str``,
``bloomberg_curve_value_probe._flatten_row``/``BulkFieldRow``, and
``bloomberg_dapi_probe._send_request`` (the one place this script sends a
live request) -- no new session/request or row-flattening implementation.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bloomberg_curve_discovery_probe import _safe_str
from bloomberg_curve_value_probe import BulkFieldRow, _flatten_row
from bloomberg_dapi_probe import _send_request  # shared session/event-loop plumbing
from bloomberg_input_sourcing_probe import sanitize_external_text

_REFDATA_SERVICE = "//blp/refdata"

# Eddy's own explicit workstation input (Issue #165 round 8) -- not derived.
# Round 7 already proved "S490" is BAD_SEC as a ReferenceData security.
DEFAULT_SECURITY = "USD Curncy"

# SW_CRV_ZERO_RATES -- confirmed by prior rounds' own Bloomberg
# FieldInfoRequest documentation. Only this one field this round; SW173
# (SW_CRV_DISCOUNT_FACTORS) is deliberately excluded from this request.
FIELD = "SW174"

# SW569 / OVERNIGHT_INDEX_SWAP_DISCOUNTING -- confirmed as a documented
# SW174 override by prior rounds' own Bloomberg FieldInfoRequest evidence.
# Value "Y" is Eddy's own explicit instruction, not guessed.
OVERRIDE_FIELD = "SW569"
DEFAULT_OVERRIDE_VALUE = "Y"

DEFAULT_OUTPUT_DIRNAME = "shiori_curve_zero_rate_override_output"
MARKDOWN_FILENAME = "bloomberg_curve_zero_rate_override_probe.md"
JSON_FILENAME = "bloomberg_curve_zero_rate_override_probe.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class ZeroRateOverrideProbeReport:
    generated_at: str
    security: str
    override_field: str
    override_value: str
    request_status: str  # "sent" | "error"
    request_error: str | None
    record_count_error: str | None
    security_error: str | None
    override_error: str | None
    field_status: str | None
    row_count: int
    rows: tuple[BulkFieldRow, ...]
    scalar_value: str | None
    raw_response_dump: str | None
    blocker_note: str


def run_zero_rate_override_probe(
    security: str = DEFAULT_SECURITY,
    override_value: str = DEFAULT_OVERRIDE_VALUE,
    send_request=_send_request,
) -> ZeroRateOverrideProbeReport:
    """Send one ``ReferenceDataRequest`` for ``SW174`` with ``SW569`` overridden, and parse it.

    ``security`` must be a non-blank string, explicitly supplied by the
    caller (default: Eddy's own literal ``"USD Curncy"``) -- never derived.
    Raises :class:`ValueError` for a blank/non-string security, the same
    caller-input-problem contract this repo's other Bloomberg loaders
    already use.
    """

    if not isinstance(security, str) or not security.strip():
        raise ValueError("security must be a non-blank string, explicitly supplied by the caller")
    security = security.strip()
    generated_at = _utc_now()

    def _configure(request) -> None:
        request.append("securities", security)
        request.append("fields", FIELD)
        overrides_element = request.getElement("overrides")
        override_element = overrides_element.appendElement()
        override_element.setElement("fieldId", OVERRIDE_FIELD)
        override_element.setElement("value", override_value)

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
            context=(
                f"ReferenceDataRequest({security!r}, field={FIELD!r}, "
                f"override={OVERRIDE_FIELD}={override_value!r})"
            ),
        )
        request_status = "sent"
        request_error = None
    except (RuntimeError, ImportError) as exc:
        request_status = "error"
        request_error = sanitize_external_text(str(exc))

    raw_dump = "\n---\n".join(sanitize_external_text(text) for text in raw_messages if text) or None

    if request_status == "error":
        return ZeroRateOverrideProbeReport(
            generated_at=generated_at,
            security=security,
            override_field=OVERRIDE_FIELD,
            override_value=override_value,
            request_status=request_status,
            request_error=request_error,
            record_count_error=None,
            security_error=None,
            override_error=None,
            field_status=None,
            row_count=0,
            rows=(),
            scalar_value=None,
            raw_response_dump=raw_dump,
            blocker_note=f"The ReferenceDataRequest failed: {request_error}",
        )

    if len(security_data_records) != 1:
        record_count_error = (
            f"Bloomberg returned {len(security_data_records)} securityData record(s) for "
            f"{security!r}, expected exactly one."
        )
        return ZeroRateOverrideProbeReport(
            generated_at=generated_at,
            security=security,
            override_field=OVERRIDE_FIELD,
            override_value=override_value,
            request_status=request_status,
            request_error=None,
            record_count_error=record_count_error,
            security_error=None,
            override_error=None,
            field_status=None,
            row_count=0,
            rows=(),
            scalar_value=None,
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
            "No fallback identifier is guessed -- rerun with a corrected --security supplied "
            "explicitly."
        )
        return ZeroRateOverrideProbeReport(
            generated_at=generated_at,
            security=security,
            override_field=OVERRIDE_FIELD,
            override_value=override_value,
            request_status=request_status,
            request_error=None,
            record_count_error=None,
            security_error=security_error,
            override_error=None,
            field_status=None,
            row_count=0,
            rows=(),
            scalar_value=None,
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

    # Bloomberg's fieldExceptions array is keyed by field id -- a rejected
    # override could plausibly surface under the override's own id (SW569)
    # or under the field it was applied to (SW174). Both are checked; this
    # script does not assume which one Bloomberg will actually use.
    override_error = field_exception_details.get(OVERRIDE_FIELD) or field_exception_details.get(
        FIELD
    )
    if override_error is not None:
        blocker_note = (
            f"Bloomberg reported a field/override error verbatim: {override_error}. No "
            "further request is sent."
        )
        return ZeroRateOverrideProbeReport(
            generated_at=generated_at,
            security=security,
            override_field=OVERRIDE_FIELD,
            override_value=override_value,
            request_status=request_status,
            request_error=None,
            record_count_error=None,
            security_error=None,
            override_error=override_error,
            field_status="field_exception",
            row_count=0,
            rows=(),
            scalar_value=None,
            raw_response_dump=raw_dump,
            blocker_note=blocker_note,
        )

    field_data = None
    try:
        if record.hasElement("fieldData"):
            field_data = record.getElement("fieldData")
    except Exception:  # noqa: BLE001
        field_data = None

    has_field = False
    try:
        has_field = field_data is not None and field_data.hasElement(FIELD)
    except Exception:  # noqa: BLE001
        has_field = False

    if not has_field:
        return ZeroRateOverrideProbeReport(
            generated_at=generated_at,
            security=security,
            override_field=OVERRIDE_FIELD,
            override_value=override_value,
            request_status=request_status,
            request_error=None,
            record_count_error=None,
            security_error=None,
            override_error=None,
            field_status="absent",
            row_count=0,
            rows=(),
            scalar_value=None,
            raw_response_dump=raw_dump,
            blocker_note=(
                f"{FIELD} is absent from the response's fieldData -- no bulk rows to report. "
                "Review the raw response dump below."
            ),
        )

    field_element = field_data.getElement(FIELD)
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
                {k: sanitize_external_text(v) for k, v in flattened.items()} if flattened else {}
            )
            rows.append(
                BulkFieldRow(
                    index=i,
                    fields=sanitized_fields,
                    raw_row_dump=sanitize_external_text(_safe_str(row_element)),
                )
            )
        blocker_note = (
            f"{FIELD} returned {row_count} bulk row(s) below with {OVERRIDE_FIELD}="
            f"{override_value!r} applied -- compare Date/Zero Rate point-by-point against "
            "terminal Curve #490 / SWDF for a few tenor nodes. This script asserts no match; "
            "that reading is Eddy's."
        )
        return ZeroRateOverrideProbeReport(
            generated_at=generated_at,
            security=security,
            override_field=OVERRIDE_FIELD,
            override_value=override_value,
            request_status=request_status,
            request_error=None,
            record_count_error=None,
            security_error=None,
            override_error=None,
            field_status="returned_bulk",
            row_count=row_count,
            rows=tuple(rows),
            scalar_value=None,
            raw_response_dump=raw_dump,
            blocker_note=blocker_note,
        )

    scalar_value = None
    try:
        scalar_value = sanitize_external_text(field_data.getElementAsString(FIELD))
    except Exception:  # noqa: BLE001
        scalar_value = None
    return ZeroRateOverrideProbeReport(
        generated_at=generated_at,
        security=security,
        override_field=OVERRIDE_FIELD,
        override_value=override_value,
        request_status=request_status,
        request_error=None,
        record_count_error=None,
        security_error=None,
        override_error=None,
        field_status="present_non_bulk_or_empty",
        row_count=row_count or 0,
        rows=(),
        scalar_value=scalar_value,
        raw_response_dump=raw_dump,
        blocker_note=(
            f"{FIELD} was present but not bulk-shaped (0 rows) -- see scalar_value below. "
            "Review the raw response dump for the actual shape."
        ),
    )


# --- rendering -------------------------------------------------------------------


def _row_to_dict(row: BulkFieldRow) -> dict:
    return {"index": row.index, "fields": row.fields, "raw_row_dump": row.raw_row_dump}


def build_report(report: ZeroRateOverrideProbeReport) -> dict:
    return {
        "generated_at": report.generated_at,
        "security": report.security,
        "field": FIELD,
        "override_field": report.override_field,
        "override_value": report.override_value,
        "request_status": report.request_status,
        "request_error": report.request_error,
        "record_count_error": report.record_count_error,
        "security_error": report.security_error,
        "override_error": report.override_error,
        "field_status": report.field_status,
        "row_count": report.row_count,
        "rows": [_row_to_dict(r) for r in report.rows],
        "scalar_value": report.scalar_value,
        "raw_response_dump": report.raw_response_dump,
        "blocker_note": report.blocker_note,
    }


def render_markdown(data: dict) -> str:
    lines: list[str] = []
    lines.append("# Bloomberg SW174 zero-rate override probe (Issue #165 round 8)")
    lines.append("")
    lines.append(f"Generated at: {data['generated_at']}")
    lines.append(f"Security (Eddy-supplied, verbatim): {data['security']}")
    lines.append(f"Field: {data['field']}")
    lines.append(f"Override: {data['override_field']} = {data['override_value']}")
    lines.append(f"Request status: {data['request_status']}")
    if data["request_error"]:
        lines.append(f"Request error: {data['request_error']}")
    if data["record_count_error"]:
        lines.append(f"Record count error: {data['record_count_error']}")
    if data["security_error"]:
        lines.append(f"Security error (Bloomberg's own text, verbatim): {data['security_error']}")
    if data["override_error"]:
        lines.append(
            f"Override/field error (Bloomberg's own text, verbatim): {data['override_error']}"
        )
    lines.append(f"Field status: {data['field_status']} ({data['row_count']} rows)")
    if data["scalar_value"] is not None:
        lines.append(f"Scalar value (unexpectedly non-bulk): {data['scalar_value']}")
    lines.append("")

    for row in data["rows"]:
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
            "Bounded read-only Bloomberg SW174 zero-rate override probe (Issue #165 round "
            "8). Requests exactly SW174 with SW569 overridden, for one explicit security."
        )
    )
    parser.add_argument(
        "--security",
        default=DEFAULT_SECURITY,
        help=(
            "Bloomberg security identifier to request, supplied explicitly -- never derived "
            f"or guessed by this script. Default: {DEFAULT_SECURITY!r} (Eddy's own literal "
            "workstation input)."
        ),
    )
    parser.add_argument(
        "--override-value",
        default=DEFAULT_OVERRIDE_VALUE,
        help=f"Value sent for the {OVERRIDE_FIELD} override. Default: {DEFAULT_OVERRIDE_VALUE!r}.",
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

    print("Shiori Bloomberg SW174 zero-rate override probe (Issue #165 round 8)")
    print(
        f"Security: {args.security!r}  Field: {FIELD}  "
        f"Override: {OVERRIDE_FIELD}={args.override_value!r}"
    )
    print("")

    try:
        report = run_zero_rate_override_probe(args.security, args.override_value)
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
    if data["override_error"]:
        print(f"Override/field error: {data['override_error']}")
    print(f"Field status: {data['field_status']} ({data['row_count']} rows)")
    print("")
    print(data["blocker_note"])

    print("")
    print("Full report (paste back or attach either file):")
    print(f"  {markdown_path.resolve()}")
    print(f"  {json_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
