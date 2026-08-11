"""Bloomberg SW174 override comparison probe (Issue #165 round 9).

Bounded, read-only diagnostic CLI -- **not** part of the production pricing
or ingestion path, and never imported by either. Round 8's live evidence
confirmed ``"USD Curncy"`` + ``SW174`` + ``SW569=Y`` returns valid bulk
zero rates -- but Eddy manually compared identical dates against terminal
Curve #490 / SWDF and the values **materially do not match**. That route is
therefore not Curve #490.

This round stops curve-identity/taxonomy research entirely and instead
asks a narrower comparative question: does a *different* documented
``SW174`` override produce a curve that matches, when compared side by
side against the same no-override baseline? It sends **three independent**
``ReferenceDataRequest`` calls for ``SW174`` against ``"USD Curncy"`` --
never combined into one request, never inferred to agree or disagree with
each other:

1. no override at all (the field's own default curve);
2. ``SW569`` (``OVERNIGHT_INDEX_SWAP_DISCOUNTING``) ``= "Y"`` (already
   proven not to match Curve #490, kept here only as the round-8 baseline
   for side-by-side comparison, not re-asserted as a candidate);
3. ``SW564`` (``DUAL_CURVE_STRIPPING_OPTION``) ``= "Y"`` -- the other
   documented ``SW174`` override prior rounds' own Bloomberg
   ``FieldInfoRequest`` evidence named, not yet tried.

**The security identifier is never derived, inferred, or guessed.**
``--security`` defaults to ``"USD Curncy"`` -- the same explicit
workstation input round 8 already used (round 7 separately proved
``"S490"`` is ``BAD_SEC`` as a ``ReferenceDataRequest`` security). A
rejection is corrected only by Eddy's own next explicit ``--security``
value.

**This script does not decide which variant, if any, matches Curve #490.**
Each variant's raw response and structurally parsed Date/Zero Coupon Rate
rows are kept fully separate and reported independently -- Eddy compares
identical dates across the three variants against terminal Curve #490 /
SWDF by eye. No variant is labeled a winner, a match, or a candidate by
this script.

**One command, run once.** Eddy runs::

    python tools/bloomberg_curve_zero_rate_comparison_probe.py

(or ``--security <corrected identifier>``) and pastes back the console
output (or the written report files).

**Reuses, not reimplements:** the same ``securityData``/``securityError``/
``fieldExceptions`` validation shape rounds 7-8 and
``data/bloomberg_bond_quote.py`` already established, and round 7's own
``bloomberg_curve_value_probe._flatten_row``/``BulkFieldRow`` for generic,
no-guessed-column-name bulk-row flattening (imported unchanged). Each of
the three variants is its own independent request/response cycle using
this same logic, not a new implementation per variant.

**Deliberately not in this script.** No curve-identity or taxonomy search
of any kind (no ``FieldSearchRequest``, no ``curveListRequest``). No
production loader, no pricing, no OIS bootstrap, no wiring into any
pricing or workbench code. No ``SW173`` request.

**Separation from production/pricing.** Never imported by anything under
``src/``. Reuses ``bloomberg_curve_discovery_probe._safe_str``,
``bloomberg_curve_value_probe._flatten_row``/``BulkFieldRow``, and
``bloomberg_dapi_probe._send_request`` -- no new session/request or
row-flattening implementation.
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

# Same explicit workstation input round 8 already used (Issue #165 round
# 9) -- not derived. Round 7 already proved "S490" is BAD_SEC.
DEFAULT_SECURITY = "USD Curncy"

# SW_CRV_ZERO_RATES -- confirmed by prior rounds' own Bloomberg
# FieldInfoRequest documentation. Only this one field this round; SW173 is
# not requested.
FIELD = "SW174"

# Exactly Eddy's three variants, in order -- (label, override_field,
# override_value). override_field is None for the no-override baseline.
# Not guessed: SW569/SW564 are both documented SW174 overrides per prior
# rounds' own Bloomberg FieldInfoRequest evidence.
DEFAULT_VARIANTS: tuple[tuple[str, str | None, str | None], ...] = (
    ("no_override", None, None),
    ("SW569=Y", "SW569", "Y"),
    ("SW564=Y", "SW564", "Y"),
)

DEFAULT_OUTPUT_DIRNAME = "shiori_curve_zero_rate_comparison_output"
MARKDOWN_FILENAME = "bloomberg_curve_zero_rate_comparison_probe.md"
JSON_FILENAME = "bloomberg_curve_zero_rate_comparison_probe.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class VariantResult:
    variant_label: str
    override_field: str | None
    override_value: str | None
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
    note: str


@dataclass(frozen=True)
class ZeroRateComparisonReport:
    generated_at: str
    security: str
    variants: tuple[VariantResult, ...]
    blocker_note: str


def _run_one_variant(
    security: str,
    variant_label: str,
    override_field: str | None,
    override_value: str | None,
    send_request,
) -> VariantResult:
    """Send one independent ``ReferenceDataRequest`` for ``FIELD`` and parse it.

    ``override_field``/``override_value`` are both ``None`` for the
    no-override baseline; otherwise both must be supplied together. Each
    call is its own request/response cycle -- variants are never combined
    into one request.
    """

    def _configure(request) -> None:
        request.append("securities", security)
        request.append("fields", FIELD)
        if override_field is not None:
            overrides_element = request.getElement("overrides")
            override_element = overrides_element.appendElement()
            override_element.setElement("fieldId", override_field)
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

    context = f"ReferenceDataRequest({security!r}, field={FIELD!r}"
    context += f", override={override_field}={override_value!r})" if override_field else ")"

    try:
        send_request(
            service_uri=_REFDATA_SERVICE,
            request_name="ReferenceDataRequest",
            configure=_configure,
            collect=_collect,
            context=context,
        )
        request_status = "sent"
        request_error = None
    except (RuntimeError, ImportError) as exc:
        request_status = "error"
        request_error = sanitize_external_text(str(exc))

    raw_dump = "\n---\n".join(sanitize_external_text(text) for text in raw_messages if text) or None

    if request_status == "error":
        return VariantResult(
            variant_label=variant_label,
            override_field=override_field,
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
            note=f"The ReferenceDataRequest failed: {request_error}",
        )

    if len(security_data_records) != 1:
        record_count_error = (
            f"Bloomberg returned {len(security_data_records)} securityData record(s) for "
            f"{security!r}, expected exactly one."
        )
        return VariantResult(
            variant_label=variant_label,
            override_field=override_field,
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
            note=f"{record_count_error} Review the raw response dump below.",
        )

    record = security_data_records[0]

    security_error: str | None = None
    try:
        if record.hasElement("securityError"):
            security_error = sanitize_external_text(_safe_str(record.getElement("securityError")))
    except Exception:  # noqa: BLE001
        pass

    if security_error is not None:
        return VariantResult(
            variant_label=variant_label,
            override_field=override_field,
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
            note=(
                f"Bloomberg rejected security identifier {security!r} verbatim: "
                f"{security_error}. No fallback identifier is guessed."
            ),
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
    # override could surface under the override's own id or under FIELD.
    # Both are checked; this script does not assume which one Bloomberg
    # uses, and does nothing if override_field is None (no override sent).
    override_error = field_exception_details.get(FIELD)
    if override_field is not None:
        override_error = field_exception_details.get(override_field) or override_error

    if override_error is not None:
        return VariantResult(
            variant_label=variant_label,
            override_field=override_field,
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
            note=f"Bloomberg reported a field/override error verbatim: {override_error}.",
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
        return VariantResult(
            variant_label=variant_label,
            override_field=override_field,
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
            note=f"{FIELD} is absent from the response's fieldData for this variant.",
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
        return VariantResult(
            variant_label=variant_label,
            override_field=override_field,
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
            note=f"{FIELD} returned {row_count} bulk row(s) for this variant.",
        )

    scalar_value = None
    try:
        scalar_value = sanitize_external_text(field_data.getElementAsString(FIELD))
    except Exception:  # noqa: BLE001
        scalar_value = None
    return VariantResult(
        variant_label=variant_label,
        override_field=override_field,
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
        note=f"{FIELD} was present but not bulk-shaped (0 rows) for this variant.",
    )


def run_zero_rate_comparison_probe(
    security: str = DEFAULT_SECURITY,
    variants: tuple[tuple[str, str | None, str | None], ...] = DEFAULT_VARIANTS,
    send_request=_send_request,
) -> ZeroRateComparisonReport:
    """Send three independent ``SW174`` requests against ``security`` and keep each result separate.

    ``security`` must be a non-blank string, explicitly supplied by the
    caller (default: ``"USD Curncy"``) -- never derived. Raises
    :class:`ValueError` for a blank/non-string security. This function does
    not compare the three variants against each other or against terminal
    Curve #490 -- it only collects and reports each independently.
    """

    if not isinstance(security, str) or not security.strip():
        raise ValueError("security must be a non-blank string, explicitly supplied by the caller")
    security = security.strip()
    generated_at = _utc_now()

    results = tuple(
        _run_one_variant(security, label, override_field, override_value, send_request)
        for label, override_field, override_value in variants
    )

    blocker_note = (
        "Three independent SW174 requests were sent against "
        f"{security!r} -- see each variant's own rows/status below. This script does not "
        "infer which variant, if any, matches terminal Curve #490 / SWDF; compare identical "
        "dates by eye."
    )

    return ZeroRateComparisonReport(
        generated_at=generated_at,
        security=security,
        variants=results,
        blocker_note=blocker_note,
    )


# --- rendering -------------------------------------------------------------------


def _row_to_dict(row: BulkFieldRow) -> dict:
    return {"index": row.index, "fields": row.fields, "raw_row_dump": row.raw_row_dump}


def _variant_to_dict(variant: VariantResult) -> dict:
    return {
        "variant_label": variant.variant_label,
        "override_field": variant.override_field,
        "override_value": variant.override_value,
        "request_status": variant.request_status,
        "request_error": variant.request_error,
        "record_count_error": variant.record_count_error,
        "security_error": variant.security_error,
        "override_error": variant.override_error,
        "field_status": variant.field_status,
        "row_count": variant.row_count,
        "rows": [_row_to_dict(r) for r in variant.rows],
        "scalar_value": variant.scalar_value,
        "raw_response_dump": variant.raw_response_dump,
        "note": variant.note,
    }


def build_report(report: ZeroRateComparisonReport) -> dict:
    return {
        "generated_at": report.generated_at,
        "security": report.security,
        "field": FIELD,
        "variants": [_variant_to_dict(v) for v in report.variants],
        "blocker_note": report.blocker_note,
    }


def render_markdown(data: dict) -> str:
    lines: list[str] = []
    lines.append("# Bloomberg SW174 override comparison probe (Issue #165 round 9)")
    lines.append("")
    lines.append(f"Generated at: {data['generated_at']}")
    lines.append(f"Security (Eddy-supplied, verbatim): {data['security']}")
    lines.append(f"Field: {data['field']}")
    lines.append("")
    lines.append(data["blocker_note"])
    lines.append("")

    for variant in data["variants"]:
        override_desc = (
            f"{variant['override_field']}={variant['override_value']}"
            if variant["override_field"]
            else "no override"
        )
        lines.append(f"## Variant: {variant['variant_label']} ({override_desc})")
        lines.append("")
        lines.append(f"Request status: {variant['request_status']}")
        if variant["request_error"]:
            lines.append(f"Request error: {variant['request_error']}")
        if variant["record_count_error"]:
            lines.append(f"Record count error: {variant['record_count_error']}")
        if variant["security_error"]:
            lines.append(f"Security error (verbatim): {variant['security_error']}")
        if variant["override_error"]:
            lines.append(f"Field/override error (verbatim): {variant['override_error']}")
        lines.append(f"Field status: {variant['field_status']} ({variant['row_count']} rows)")
        if variant["scalar_value"] is not None:
            lines.append(f"Scalar value (unexpectedly non-bulk): {variant['scalar_value']}")
        lines.append(f"Note: {variant['note']}")
        lines.append("")
        for row in variant["rows"]:
            lines.append(f"- row {row['index']}: {row['fields'] or '(no flattened fields)'}")
            if row["raw_row_dump"]:
                lines.append("  raw:")
                lines.append("  ```")
                lines.append(f"  {row['raw_row_dump']}")
                lines.append("  ```")
        lines.append("")
        if variant["raw_response_dump"]:
            lines.append("Raw response dump:")
            lines.append("```")
            lines.append(variant["raw_response_dump"])
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
            "Bounded read-only Bloomberg SW174 override comparison probe (Issue #165 round "
            "9). Sends three independent SW174 requests: no override, SW569=Y, SW564=Y."
        )
    )
    parser.add_argument(
        "--security",
        default=DEFAULT_SECURITY,
        help=(
            "Bloomberg security identifier to request, supplied explicitly -- never derived "
            f"or guessed by this script. Default: {DEFAULT_SECURITY!r}."
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

    print("Shiori Bloomberg SW174 override comparison probe (Issue #165 round 9)")
    print(f"Security: {args.security!r}  Field: {FIELD}")
    print(f"Variants: {', '.join(label for label, _, _ in DEFAULT_VARIANTS)}")
    print("")

    try:
        report = run_zero_rate_comparison_probe(args.security)
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

    for variant in data["variants"]:
        print(
            f"  - {variant['variant_label']}: {variant['request_status']}, "
            f"{variant['field_status']} ({variant['row_count']} rows)"
        )
        if variant["security_error"]:
            print(f"      security_error: {variant['security_error']}")
        if variant["override_error"]:
            print(f"      override_error: {variant['override_error']}")
    print("")
    print(data["blocker_note"])

    print("")
    print("Full report (paste back or attach either file):")
    print(f"  {markdown_path.resolve()}")
    print(f"  {json_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
