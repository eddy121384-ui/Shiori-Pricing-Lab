"""Bloomberg historical bond-Yield field discovery probe (Issue #196 §A).

Bounded, read-only workstation diagnostic CLI -- **not** part of the
production ingestion path and never imported by anything under ``src/``.
Its one job is to produce the evidence that answers Issue #196's first
question: *which* Bloomberg field is the bond's historical Yield.

``src/shiori_pricing_lab/data/bloomberg_bond_yield_history.py`` takes that
mnemonic as a required caller input with no default, no candidate list and
no fallback, precisely so the answer comes from this probe's workstation
evidence rather than from a name that looks plausible in a code review.

**Three passes, in this order.**

1. *Catalogue search* (no security involved). ``//blp/apiflds``
   ``FieldSearchRequest.searchSpec`` for each ``--search-term``, reusing
   ``bloomberg_curve_discovery_probe``'s already-reviewed
   ``discover_service``/``attempt_field_search`` rather than opening a
   second search path. This is Bloomberg's own catalogue answering "what
   Yield fields exist", not this repo guessing mnemonics.
2. *Documentation* of each ``--field`` the operator names, via
   ``bloomberg_dapi_probe.describe_fields`` (``FieldInfoRequest`` with
   ``returnFieldDocumentation``). Bloomberg's own description, datatype,
   documented overrides and documentation text, printed verbatim
   (sanitized only for host/session/client leakage).
3. *Historical availability* of each ``--field`` against one real bond over
   one real date range -- one ``HistoricalDataRequest`` per field, sent with
   exactly the four options the production loader pins
   (``DAILY``/``ACTUAL``/``ACTIVE_DAYS_ONLY``/``NIL_VALUE``), so what this
   probe observes is what the loader will observe. Reports per field
   whether the field is available historically at all, how many
   observations came back, the first/last observation dates, how many rows
   carried no value, and the response's own value datatype -- the "date/
   value response shape" and "missing-data behavior" Issue #196 §A asks
   for.

**Candidates are operator-named, never brute-forced.** Pass 3 fires only
for mnemonics given explicitly with ``--field``, one request each. This
probe never iterates a generated mnemonic space, never retries variants of
a name, and never promotes a search hit into a historical request on its
own.

**This probe decides nothing.** It prints Bloomberg's own answers and one
mechanical verdict line:

- exactly one ``--field`` returned a usable historical series -> that is the
  single candidate the evidence supports;
- two or more did -> ``AMBIGUOUS``. Issue #196's stop condition 1 applies:
  stop and report the ambiguity to Eddy/Sophira rather than choosing the
  closest-looking series. This probe will not choose for you, and neither
  will the loader.

Nothing about a field's *economic meaning* -- which Yield definition it is,
its unit, its quote/source semantics -- is inferred here from a mnemonic or
a count. That reading comes from Bloomberg's own documentation text in pass
2 and from Eddy's Terminal, and is then passed into the loader explicitly
as ``field_meaning``/``field_unit``.

**Live values never reach a file.** The written reports carry only shape
evidence: counts, dates, datatypes, statuses. A small sample of dated values
is printed to the console for Eddy's own Terminal/Excel comparison and is
deliberately not written to disk, so a probe run in a repository checkout
cannot leave proprietary Bloomberg values behind to be committed.

**One command, run on the workstation.** For example::

    python tools/bloomberg_bond_yield_field_probe.py \\
        --identifier /isin/XS0000000000 \\
        --start 2025-09-01 --end 2026-09-01 \\
        --field <candidate> --field <other candidate>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path

from bloomberg_curve_discovery_probe import (
    SearchAttempt,
    attempt_field_search,
    discover_service,
)
from bloomberg_dapi_probe import FieldDescription, _send_request, describe_fields
from bloomberg_input_sourcing_probe import (
    sanitize_external_text,
    sanitize_field_documentation_text,
)

_APIFLDS_SERVICE = "//blp/apiflds"
_REFDATA_SERVICE = "//blp/refdata"

# The same four options the production loader pins, so this probe observes
# the response shape the loader will actually get -- see
# data/bloomberg_bond_yield_history.py's own docstring for why.
_HISTORICAL_REQUEST_OPTIONS: tuple[tuple[str, str], ...] = (
    ("periodicitySelection", "DAILY"),
    ("periodicityAdjustment", "ACTUAL"),
    ("nonTradingDayFillOption", "ACTIVE_DAYS_ONLY"),
    ("nonTradingDayFillMethod", "NIL_VALUE"),
)

# Deliberately generic Yield vocabulary for Bloomberg's own catalogue to
# answer against. These are search *terms*, not field mnemonics: nothing
# here is ever sent as a field.
DEFAULT_SEARCH_TERMS: tuple[str, ...] = (
    "yield",
    "yield to maturity",
    "yield to worst",
    "bond yield",
    "historical yield",
)

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FIELD_MNEMONIC_RE = re.compile(r"^[A-Z0-9_]+$")

DEFAULT_OUTPUT_DIRNAME = "shiori_bond_yield_field_probe_output"
MARKDOWN_FILENAME = "bloomberg_bond_yield_field_probe.md"
JSON_FILENAME = "bloomberg_bond_yield_field_probe.json"

DEFAULT_SAMPLE_ROWS = 5

CONSOLE_SAMPLE_WARNING = (
    "The sample rows below are live Bloomberg data. They are printed here for "
    "your own Terminal/Excel comparison only -- they are not written to any "
    "report file, and must not be pasted into the repository."
)


@dataclass(frozen=True)
class HistoricalFieldEvidence:
    """One candidate field's historical-availability evidence -- shape only.

    Carries no Bloomberg value: ``value_datatype`` is the response element's
    own type name, and every other member is a count, a date, or a status.
    """

    field: str
    status: str  # "returned" | "empty" | "field_exception" | "security_error" | "error"
    observation_count: int = 0
    rows_with_no_value: int = 0
    first_observation_date: str | None = None
    last_observation_date: str | None = None
    value_datatype: str | None = None
    resolved_security: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class YieldFieldProbeReport:
    generated_at: str
    identifier: str | None
    start_date: str | None
    end_date: str | None
    search_terms: tuple[str, ...]
    search_attempts: tuple[SearchAttempt, ...]
    search_error: str | None
    descriptions: tuple[FieldDescription, ...]
    historical: tuple[HistoricalFieldEvidence, ...]
    verdict: str


def _parse_iso_date_argument(value: str, name: str) -> date:
    if not _ISO_DATE_RE.match(value):
        raise ValueError(f"--{name} must be a YYYY-MM-DD date, got {value!r}")
    return date.fromisoformat(value)


def _validate_field_mnemonic(field: str) -> str:
    candidate = field.strip()
    if not _FIELD_MNEMONIC_RE.match(candidate):
        raise ValueError(
            "--field must be a Bloomberg field mnemonic (uppercase A-Z, digits and "
            f"underscores only), got {field!r}"
        )
    return candidate


def _sanitized_description(description: FieldDescription) -> FieldDescription:
    """Sanitize Bloomberg-authored text, mirroring the curve documentation probe."""

    return replace(
        description,
        description=sanitize_field_documentation_text(description.description),
        documentation=sanitize_field_documentation_text(description.documentation),
        detail=sanitize_external_text(description.detail),
    )


def search_yield_field_catalogue(
    search_terms: tuple[str, ...],
) -> tuple[tuple[SearchAttempt, ...], str | None]:
    """Ask Bloomberg's own field catalogue which Yield fields exist.

    Pure metadata: no security identifier is involved, nothing is requested
    against a bond, and no hit is promoted into a historical request.
    """

    try:
        apiflds = discover_service(_APIFLDS_SERVICE)
    except (RuntimeError, ImportError) as exc:
        return (), sanitize_external_text(str(exc))
    if not apiflds.opened:
        return (), sanitize_external_text(apiflds.open_error or "//blp/apiflds did not open")
    return attempt_field_search(apiflds, search_terms), None


def probe_historical_field(
    *,
    field: str,
    identifier: str,
    start: date,
    end: date,
    sample_rows: int,
    send_request=_send_request,
) -> tuple[HistoricalFieldEvidence, tuple[tuple[str, str], ...]]:
    """One ``HistoricalDataRequest`` for one candidate field against one bond.

    Returns the field's shape evidence and, separately, a bounded sample of
    ``(date, value)`` pairs for console display only -- the caller never
    writes that sample to a file.
    """

    try:
        import blpapi
    except ImportError as exc:
        return (
            HistoricalFieldEvidence(
                field=field,
                status="error",
                detail=sanitize_external_text(
                    "blpapi is not installed -- Bloomberg's official blpapi package is "
                    f"required to probe historical availability: {exc}"
                ),
            ),
            (),
        )

    collected: list = []

    def _configure(request) -> None:
        request.append("securities", identifier)
        request.append("fields", field)
        request.set("startDate", start.strftime("%Y%m%d"))
        request.set("endDate", end.strftime("%Y%m%d"))
        for option_name, option_value in _HISTORICAL_REQUEST_OPTIONS:
            request.set(option_name, option_value)

    def _collect(message) -> None:
        if message.hasElement("securityData"):
            collected.append(message.getElement("securityData"))

    try:
        send_request(
            service_uri=_REFDATA_SERVICE,
            request_name="HistoricalDataRequest",
            configure=_configure,
            collect=_collect,
            context=f"HistoricalDataRequest({field} on {identifier!r})",
        )
    except (RuntimeError, ImportError) as exc:
        return (
            HistoricalFieldEvidence(
                field=field, status="error", detail=sanitize_external_text(str(exc))
            ),
            (),
        )

    resolved_security: str | None = None
    dates: list[str] = []
    rows_with_no_value = 0
    value_datatype: str | None = None
    sample: list[tuple[str, str]] = []

    for record in collected:
        if record.hasElement("security"):
            resolved_security = record.getElementAsString("security")
        if record.hasElement("securityError"):
            return (
                HistoricalFieldEvidence(
                    field=field,
                    status="security_error",
                    resolved_security=resolved_security,
                    detail=sanitize_external_text(str(record.getElement("securityError"))),
                ),
                (),
            )
        exceptions = (
            record.getElement("fieldExceptions") if record.hasElement("fieldExceptions") else None
        )
        if exceptions is not None and exceptions.numValues() > 0:
            return (
                HistoricalFieldEvidence(
                    field=field,
                    status="field_exception",
                    resolved_security=resolved_security,
                    detail=sanitize_external_text(str(exceptions.getValueAsElement(0))),
                ),
                (),
            )
        if not record.hasElement("fieldData"):
            continue
        field_data = record.getElement("fieldData")
        for index in range(field_data.numValues()):
            row = field_data.getValueAsElement(index)
            observation_date = (
                row.getElementAsString("date") if row.hasElement("date") else "<no date element>"
            )
            dates.append(observation_date)
            if not row.hasElement(field):
                rows_with_no_value += 1
                continue
            element = row.getElement(field)
            if value_datatype is None:
                try:
                    value_datatype = str(element.datatype())
                except (AttributeError, blpapi.exception.Exception):
                    value_datatype = "<datatype unavailable>"
            raw_value = row.getElementAsString(field)
            if not raw_value.strip():
                rows_with_no_value += 1
                continue
            if len(sample) < sample_rows:
                sample.append((observation_date, raw_value))

    ordered = sorted(dates)
    return (
        HistoricalFieldEvidence(
            field=field,
            status="returned" if ordered else "empty",
            observation_count=len(ordered),
            rows_with_no_value=rows_with_no_value,
            first_observation_date=ordered[0] if ordered else None,
            last_observation_date=ordered[-1] if ordered else None,
            value_datatype=value_datatype,
            resolved_security=resolved_security,
        ),
        tuple(sample),
    )


def build_verdict(historical: tuple[HistoricalFieldEvidence, ...]) -> str:
    """One mechanical line about how many candidates the evidence supports.

    Never names a winner beyond "this is the only one that returned data",
    and never resolves an ambiguity -- Issue #196 stop condition 1 is a stop,
    not a tie-break.
    """

    usable = [
        evidence
        for evidence in historical
        if evidence.status == "returned" and evidence.observation_count > 0
    ]
    if not historical:
        return "NO CANDIDATE PROBED -- pass --field (with --identifier/--start/--end) to test one."
    if all(evidence.status == "error" for evidence in historical):
        return (
            "PROBE COULD NOT RUN -- every candidate's request failed before Bloomberg "
            "answered (see the detail above; blpapi missing or no Terminal session is the "
            "usual cause). Nothing here is evidence about any field."
        )
    if not usable:
        return (
            "NO USABLE SERIES -- none of the probed fields returned a historical "
            "observation for this bond over this range. Do not pick one anyway."
        )
    if len(usable) == 1:
        return (
            f"ONE CANDIDATE RETURNED DATA: {usable[0].field} "
            f"({usable[0].observation_count} observations). Confirm its economic meaning "
            "and unit from Bloomberg's own documentation above and from the Terminal "
            "before using it -- an observation count is not a meaning."
        )
    return (
        "AMBIGUOUS -- "
        + ", ".join(f"{item.field} ({item.observation_count} obs)" for item in usable)
        + " all returned a historical series. Issue #196 stop condition 1 applies: stop "
        "and report the ambiguity to Eddy/Sophira. This probe does not choose, and the "
        "loader has no default to fall back on."
    )


def run_probe(
    *,
    identifier: str | None,
    start: date | None,
    end: date | None,
    fields: tuple[str, ...],
    search_terms: tuple[str, ...],
    sample_rows: int,
) -> tuple[YieldFieldProbeReport, dict[str, tuple[tuple[str, str], ...]]]:
    """Run every pass the arguments enable, and return the report plus console samples."""

    search_attempts, search_error = search_yield_field_catalogue(search_terms)

    descriptions: tuple[FieldDescription, ...] = ()
    if fields:
        try:
            descriptions = tuple(
                _sanitized_description(description) for description in describe_fields(list(fields))
            )
        except (RuntimeError, ImportError) as exc:
            descriptions = tuple(
                FieldDescription(
                    field=field, status="field_error", detail=sanitize_external_text(str(exc))
                )
                for field in fields
            )

    historical: list[HistoricalFieldEvidence] = []
    samples: dict[str, tuple[tuple[str, str], ...]] = {}
    if fields and identifier and start and end:
        for field in fields:
            evidence, sample = probe_historical_field(
                field=field,
                identifier=identifier,
                start=start,
                end=end,
                sample_rows=sample_rows,
            )
            historical.append(evidence)
            samples[field] = sample

    report = YieldFieldProbeReport(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        identifier=identifier,
        start_date=start.isoformat() if start else None,
        end_date=end.isoformat() if end else None,
        search_terms=search_terms,
        search_attempts=search_attempts,
        search_error=search_error,
        descriptions=descriptions,
        historical=tuple(historical),
        verdict=build_verdict(tuple(historical)),
    )
    return report, samples


# --- rendering ---------------------------------------------------------------------


def build_report(report: YieldFieldProbeReport) -> dict:
    """The report as plain data -- shape evidence only, never a Bloomberg value."""

    return {
        "probe": "bloomberg_bond_yield_field_probe",
        "issue": 196,
        "generated_at": report.generated_at,
        "identifier": report.identifier,
        "start_date": report.start_date,
        "end_date": report.end_date,
        "search_terms": list(report.search_terms),
        "search_error": report.search_error,
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
        "field_documentation": [
            {
                "field": description.field,
                "status": description.status,
                "mnemonic": description.mnemonic,
                "description": description.description,
                "datatype": description.datatype,
                "overrides": list(description.overrides),
                "documentation": description.documentation,
                "detail": description.detail,
            }
            for description in report.descriptions
        ],
        "historical_availability": [
            {
                "field": evidence.field,
                "status": evidence.status,
                "observation_count": evidence.observation_count,
                "rows_with_no_value": evidence.rows_with_no_value,
                "first_observation_date": evidence.first_observation_date,
                "last_observation_date": evidence.last_observation_date,
                "value_datatype": evidence.value_datatype,
                "resolved_security": evidence.resolved_security,
                "detail": evidence.detail,
            }
            for evidence in report.historical
        ],
        "verdict": report.verdict,
        "values_note": (
            "This report deliberately carries no Bloomberg value. Only counts, dates, "
            "datatypes and statuses are recorded."
        ),
    }


def render_markdown(data: dict) -> str:
    lines = [
        "# Bloomberg historical bond-Yield field probe (Issue #196)",
        "",
        f"- Generated at: {data['generated_at']}",
        f"- Security requested: {data['identifier'] or '(none -- catalogue pass only)'}",
        f"- Range requested: {data['start_date'] or '-'} .. {data['end_date'] or '-'}",
        "",
        f"**Verdict:** {data['verdict']}",
        "",
        f"_{data['values_note']}_",
        "",
        "## 1. Catalogue search (`//blp/apiflds` FieldSearchRequest.searchSpec)",
        "",
        f"Terms: {', '.join(data['search_terms'])}",
        "",
    ]
    if data["search_error"]:
        lines += [f"Search unavailable: {data['search_error']}", ""]
    for attempt in data["search_attempts"]:
        lines += [
            f"### term: `{attempt['term']}` -- {attempt['status']}",
            "",
        ]
        if attempt["error"]:
            lines += [f"error: {attempt['error']}", ""]
        if attempt["raw_response_dump"]:
            lines += ["```", attempt["raw_response_dump"], "```", ""]

    lines += ["## 2. Bloomberg's own documentation for each named candidate", ""]
    if not data["field_documentation"]:
        lines += ["No `--field` was named, so no documentation was requested.", ""]
    for description in data["field_documentation"]:
        lines += [
            f"### `{description['field']}` -- {description['status']}",
            "",
            f"- mnemonic: {description['mnemonic']}",
            f"- datatype: {description['datatype']}",
            f"- overrides: {', '.join(description['overrides']) or '(none documented)'}",
            f"- description: {description['description']}",
            "",
        ]
        if description["documentation"]:
            lines += ["```", description["documentation"], "```", ""]
        if description["detail"]:
            lines += [f"detail: {description['detail']}", ""]

    lines += [
        "## 3. Historical availability (one HistoricalDataRequest per candidate)",
        "",
        "| Field | Status | Obs | Rows w/o value | First | Last | Value datatype |",
        "| --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for evidence in data["historical_availability"]:
        lines.append(
            f"| `{evidence['field']}` | {evidence['status']} | "
            f"{evidence['observation_count']} | {evidence['rows_with_no_value']} | "
            f"{evidence['first_observation_date'] or '-'} | "
            f"{evidence['last_observation_date'] or '-'} | "
            f"{evidence['value_datatype'] or '-'} |"
        )
    lines.append("")
    for evidence in data["historical_availability"]:
        if evidence["detail"]:
            lines += [f"- `{evidence['field']}`: {evidence['detail']}"]
    lines.append("")
    return "\n".join(lines)


def render_json(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


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
            "Bloomberg historical bond-Yield field discovery probe (Issue #196). "
            "Searches Bloomberg's own field catalogue, expands the documentation for "
            "each candidate mnemonic you name, and checks each one's historical "
            "availability against one real bond. Decides nothing."
        )
    )
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        help=(
            "A candidate Bloomberg Yield mnemonic to document and probe historically. "
            "Repeatable. Named explicitly by you -- this probe never generates or "
            "brute-forces mnemonics."
        ),
    )
    parser.add_argument(
        "--identifier",
        default=None,
        help=(
            "Bloomberg security string for the historical pass, e.g. '/isin/<ISIN>' or "
            "'/cusip/<CUSIP>'. Required for pass 3."
        ),
    )
    parser.add_argument("--start", default=None, help="Inclusive start date, YYYY-MM-DD.")
    parser.add_argument("--end", default=None, help="Inclusive end date, YYYY-MM-DD.")
    parser.add_argument(
        "--search-term",
        action="append",
        default=[],
        help=(
            "Catalogue search term for pass 1. Repeatable. Default: "
            f"{', '.join(DEFAULT_SEARCH_TERMS)}."
        ),
    )
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=DEFAULT_SAMPLE_ROWS,
        help=(
            f"How many dated values to print to the console for your own Terminal "
            f"comparison (default {DEFAULT_SAMPLE_ROWS}). Never written to a file."
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

    try:
        fields = tuple(_validate_field_mnemonic(field) for field in args.field)
        start = _parse_iso_date_argument(args.start, "start") if args.start else None
        end = _parse_iso_date_argument(args.end, "end") if args.end else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if start and end and start > end:
        print("error: --start must not be after --end", file=sys.stderr)
        return 2
    if fields and not (args.identifier and start and end):
        print(
            "note: --identifier/--start/--end were not all supplied, so the historical "
            "availability pass is skipped and only documentation is expanded.",
            file=sys.stderr,
        )

    search_terms = tuple(args.search_term) if args.search_term else DEFAULT_SEARCH_TERMS
    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd() / DEFAULT_OUTPUT_DIRNAME

    print("Shiori Bloomberg historical bond-Yield field probe (Issue #196)")
    print(f"Candidates: {', '.join(fields) if fields else '(none named)'}")
    print("")

    report, samples = run_probe(
        identifier=args.identifier,
        start=start,
        end=end,
        fields=fields,
        search_terms=search_terms,
        sample_rows=max(0, args.sample_rows),
    )
    data = build_report(report)
    markdown_path, json_path = write_report(data, output_dir)

    for evidence in data["historical_availability"]:
        print(
            f"{evidence['field']}: {evidence['status']} "
            f"obs={evidence['observation_count']} "
            f"no_value_rows={evidence['rows_with_no_value']} "
            f"first={evidence['first_observation_date']} "
            f"last={evidence['last_observation_date']} "
            f"datatype={evidence['value_datatype']}"
        )
        if evidence["detail"]:
            print(f"  detail: {evidence['detail']}")

    if any(samples.values()):
        print("")
        print(CONSOLE_SAMPLE_WARNING)
        for field, sample in samples.items():
            if not sample:
                continue
            print(f"  {field}:")
            for observation_date, raw_value in sample:
                print(f"    {observation_date}  {raw_value}")

    print("")
    print(f"Verdict: {data['verdict']}")
    print(f"Report written: {markdown_path}")
    print(f"Report written: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
