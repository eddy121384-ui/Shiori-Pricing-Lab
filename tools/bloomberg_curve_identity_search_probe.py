"""Bloomberg curve identity search probe (Issue #165 round 4).

Bounded, read-only diagnostic CLI -- **not** part of the production pricing
or ingestion path, and never imported by either. Rounds 2-3 confirmed:

- ``//blp/apiflds``'s ``FieldSearchRequest.searchSpec`` works on a real
  workstation and returns real field mnemonics for a search phrase.
- ``SW174``/``SW173`` (``SW_CRV_ZERO_RATES``/``SW_CRV_DISCOUNT_FACTORS``)
  are confirmed real ``BulkFormat`` fields whose own Bloomberg documentation
  says they use "the default curve for the specified currency" -- so
  *something* still has to identify which curve that is.
- ``SW564``/``SW569`` (``DUAL_CURVE_STRIPPING_OPTION``/
  ``OVERNIGHT_INDEX_SWAP_DISCOUNTING``) prove an OIS discounting mode
  exists, but not that it equals Curve #490.
- ``DT290``/``DT153`` (``MARKET_SIDE``/``CURVE_NAME``) map to
  ``PY299``/``PY560`` (``PAR_CURVE``/``INTEREST_RATE_CURVE_ANALYTICS``
  hourly snaps) -- **not** confirmed as ``SW173``'s Curve #490 selector,
  and this script does not promote, assume, or search for either of those
  two mnemonics as if they were.

**This script's one job:** run ``FieldSearchRequest.searchSpec`` -- the
already-proven route -- for curve-*identity* terms specifically ("curve
number", "curve id", "curve identifier", "swap curve number", "swap curve
id", "curve name", "default curve"), keep only the results whose own
Bloomberg-reported category looks like actual curve identity (``Security
Specific/Curves``, ``Analysis/Spread/Curves/Swap``, ``Analysis/Market
Data``), and expand full ``FieldInfoRequest`` documentation (reusing round
3's ``discover_field_documentation`` unchanged) for whatever mnemonics
survive that filter.

**One command, run once.** Eddy runs::

    python tools/bloomberg_curve_identity_search_probe.py

and pastes back the console output (or the written report files).

**Category extraction is best-effort, never a guess promoted to fact.**
``FieldSearchRequest``'s exact response schema (which element, if any,
carries a field's category) is not proven by this repo -- rounds 2-3 only
proved the request itself works. This script tries a small set of
plausible element names (``category``/``categoryName``/
``categoryDescription``) purely *structurally*, via ``hasElement`` probes
that never assert one must exist, mirroring how
``bloomberg_dapi_probe.describe_fields`` already probes
``mnemonic``/``overrides`` on the sibling ``FieldInfoRequest``. The
per-record raw text dump (``str(record)``) is always captured too, so
nothing is lost if none of those element-name guesses land -- a human
reviewing the report can still see and category-filter every record by eye.
Category filtering itself is a plain substring match against the three
category strings Eddy supplied (his own reading of Bloomberg's search UI),
never an inference from a mnemonic's name.

**No security or curve identifier is requested here at all.** Both
``FieldSearchRequest`` and ``FieldInfoRequest`` are ``//blp/apiflds``
metadata-only requests -- neither needs a curve identifier as input, which
is exactly why this script can run before Curve #490's own identifier is
known.

**Deliberately not in this script (item 5's own gate).** Whether this run's
metadata is *sufficient* to map a curve number/identifier onto a
``ReferenceDataRequest`` security -- and, if so, building that next
value-level probe -- is a judgment call on this script's *output*, which
does not exist until Eddy runs it. Building that probe now would mean
guessing a security identifier, which this script and its siblings never
do.

**Separation from production/pricing.** Never imported by anything under
``src/``, performs no pricing, no OIS bootstrap, and writes nothing except
its own diagnostic report files. Reuses
``tools/bloomberg_curve_discovery_probe.py``'s ``discover_service`` and its
proven ``(FieldSearchRequest, searchSpec)`` pair (re-confirmed via *this
run's own* introspection before sending anything, never assumed from a
prior round), ``tools/bloomberg_dapi_probe.py``'s ``_send_request``, and
``tools/bloomberg_curve_field_documentation_probe.py``'s
``discover_field_documentation`` -- no new session/request implementation
and no second documentation-expansion pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
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
from bloomberg_dapi_probe import _send_request  # shared session/event-loop plumbing
from bloomberg_input_sourcing_probe import sanitize_external_text

# Issue #165 round 4's own search terms -- curve *identity*, not the
# generic curve-content terms round 2 used ("stripped curve", "zero
# curve", ...). Exactly Eddy's list, no additions or rewording.
DEFAULT_CURVE_IDENTITY_SEARCH_TERMS: tuple[str, ...] = (
    "curve number",
    "curve id",
    "curve identifier",
    "swap curve number",
    "swap curve id",
    "curve name",
    "default curve",
)

# Category strings Eddy identified from Bloomberg's own search UI as
# actual curve-identity territory -- used only as a literal substring
# filter, never as a semantic inference about what any mnemonic means.
CATEGORY_FILTER_STRINGS: tuple[str, ...] = (
    "Security Specific/Curves",
    "Analysis/Spread/Curves/Swap",
    "Analysis/Market Data",
)

# Candidate element names tried structurally (hasElement probes only) when
# parsing one FieldSearchRequest record's fieldInfo for a category value.
# None of these is confirmed to exist; an absent one simply yields None.
_CANDIDATE_CATEGORY_ELEMENT_NAMES: tuple[str, ...] = (
    "category",
    "categoryName",
    "categoryDescription",
)

DEFAULT_OUTPUT_DIRNAME = "shiori_curve_identity_search_output"
MARKDOWN_FILENAME = "bloomberg_curve_identity_search_probe.md"
JSON_FILENAME = "bloomberg_curve_identity_search_probe.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class SearchResultRecord:
    """One candidate record from one search term's ``FieldSearchRequest`` response."""

    search_term: str
    field_id: str | None
    mnemonic: str | None
    description: str | None
    category: str | None
    raw_record_dump: str | None
    category_relevant: bool


@dataclass(frozen=True)
class SearchTermOutcome:
    term: str
    status: str  # "sent" | "error"
    error: str | None
    structured_parse_ok: bool
    raw_response_dump: str | None
    records: tuple[SearchResultRecord, ...]


@dataclass(frozen=True)
class CurveIdentitySearchReport:
    generated_at: str
    search_capability_confirmed: bool
    apiflds_open_error: str | None
    search_terms: tuple[str, ...]
    term_outcomes: tuple[SearchTermOutcome, ...]
    category_relevant_mnemonics: tuple[str, ...]
    documentation_expansion: dict | None
    blocker_note: str | None


def _is_category_relevant(category: str | None, raw_dump: str | None) -> bool:
    haystacks = [text for text in (category, raw_dump) if text]
    return any(
        needle.lower() in haystack.lower()
        for haystack in haystacks
        for needle in CATEGORY_FILTER_STRINGS
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

    mnemonic = _optional_element_string(info, "mnemonic")
    description = _optional_element_string(info, "description")
    category = None
    for candidate_name in _CANDIDATE_CATEGORY_ELEMENT_NAMES:
        category = _optional_element_string(info, candidate_name)
        if category is not None:
            break

    return {
        "field_id": field_id,
        "mnemonic": mnemonic,
        "description": description,
        "category": category,
        "raw_record_dump": _safe_str(record),
    }


def _parse_search_response(message) -> tuple[bool, tuple[dict, ...]]:
    """Best-effort: parse one ``FieldSearchRequest`` response as a ``fieldData`` array.

    Mirrors ``bloomberg_dapi_probe.describe_fields``'s own proven
    ``FieldInfoRequest`` parsing shape (same ``//blp/apiflds`` service
    family) -- a structural assumption, not confirmed evidence for
    ``FieldSearchRequest`` specifically. Never raises: a mismatch just
    means ``parsed_ok=False`` and the caller falls back to the whole
    message's raw ``str()`` dump instead, so no evidence is ever lost to a
    parsing assumption that turns out wrong.
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


def search_curve_identity_candidates(
    search_terms: tuple[str, ...] = DEFAULT_CURVE_IDENTITY_SEARCH_TERMS,
    send_request=_send_request,
    discover_service_fn=None,
    describe=None,
) -> CurveIdentitySearchReport:
    """Search for curve-identity fields via the proven ``FieldSearchRequest.searchSpec``.

    Re-confirms the proven ``(FieldSearchRequest, searchSpec)`` pair via
    *this run's own* ``discover_service`` introspection before sending
    anything -- never assumed from a prior round. If that pair is not
    confirmed this run, no search is attempted and ``blocker_note``
    explains precisely why. Category-relevant mnemonics (deduplicated, in
    first-seen order) are handed to ``discover_field_documentation`` for a
    full documentation expansion; an empty candidate set skips that step.
    """

    discover_service_fn = discover_service_fn or discover_service
    generated_at = _utc_now()

    apiflds = discover_service_fn(_APIFLDS_SERVICE)
    search_capability_confirmed = apiflds.opened and any(
        operation.name == _PROVEN_SEARCH_OPERATION
        and _PROVEN_SEARCH_ELEMENT in operation.candidate_string_elements
        for operation in apiflds.operations
    )

    if not search_capability_confirmed:
        blocker_note = (
            f"{_PROVEN_SEARCH_OPERATION}.{_PROVEN_SEARCH_ELEMENT} was not confirmed by "
            "this run's own //blp/apiflds introspection, so no curve-identity search was "
            "attempted. See apiflds.open_error / operation_list_error in "
            "bloomberg_curve_discovery_probe's own discover_service output for why."
        )
        return CurveIdentitySearchReport(
            generated_at=generated_at,
            search_capability_confirmed=False,
            apiflds_open_error=apiflds.open_error,
            search_terms=search_terms,
            term_outcomes=(),
            category_relevant_mnemonics=(),
            documentation_expansion=None,
            blocker_note=blocker_note,
        )

    term_outcomes: list[SearchTermOutcome] = []
    category_relevant_mnemonics: list[str] = []

    for term in search_terms:
        raw_messages: list[str] = []
        parsed_messages: list[tuple[bool, tuple[dict, ...]]] = []

        def _configure(request, _term=term) -> None:
            try:
                request.set(_PROVEN_SEARCH_ELEMENT, _term)
            except Exception:  # noqa: BLE001
                request.append(_PROVEN_SEARCH_ELEMENT, _term)

        def _collect(message, _raw_messages=raw_messages, _parsed_messages=parsed_messages) -> None:
            _raw_messages.append(_safe_str(message))
            _parsed_messages.append(_parse_search_response(message))

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
                SearchTermOutcome(
                    term=term,
                    status="error",
                    error=sanitize_external_text(str(exc)),
                    structured_parse_ok=False,
                    raw_response_dump=None,
                    records=(),
                )
            )
            continue

        structured_parse_ok = any(ok for ok, _ in parsed_messages)
        records: list[SearchResultRecord] = []
        for ok, raw_records in parsed_messages:
            if not ok:
                continue
            for raw_record in raw_records:
                category_relevant = _is_category_relevant(
                    raw_record["category"], raw_record["raw_record_dump"]
                )
                record = SearchResultRecord(
                    search_term=term,
                    field_id=raw_record["field_id"],
                    mnemonic=raw_record["mnemonic"],
                    description=sanitize_external_text(raw_record["description"]),
                    category=sanitize_external_text(raw_record["category"]),
                    raw_record_dump=sanitize_external_text(raw_record["raw_record_dump"]),
                    category_relevant=category_relevant,
                )
                records.append(record)
                if (
                    category_relevant
                    and record.mnemonic
                    and record.mnemonic not in category_relevant_mnemonics
                ):
                    category_relevant_mnemonics.append(record.mnemonic)

        raw_dump = (
            "\n---\n".join(sanitize_external_text(text) for text in raw_messages if text) or None
        )
        term_outcomes.append(
            SearchTermOutcome(
                term=term,
                status="sent",
                error=None,
                structured_parse_ok=structured_parse_ok,
                raw_response_dump=raw_dump,
                records=tuple(records),
            )
        )

    documentation_expansion = None
    if category_relevant_mnemonics:
        documentation_report = discover_field_documentation(
            tuple(category_relevant_mnemonics), describe=describe
        )
        documentation_expansion = build_documentation_report(documentation_report)

    if not category_relevant_mnemonics:
        blocker_note = (
            "FieldSearchRequest.searchSpec returned no category-relevant curve-identity "
            f"candidate (category text matching {', '.join(CATEGORY_FILTER_STRINGS)!r}) for "
            f"any of this run's {len(search_terms)} search terms. Curve #490's security "
            "identifier acquisition route is still unresolved. Review each term's raw "
            "response dump by hand before falling back to manual FLDS -- the structured "
            "category parse in this script is a best-effort guess at Bloomberg's response "
            "element names and may simply not have found the right one this run."
        )
    else:
        blocker_note = (
            "Category-relevant candidates were found and their documentation expanded "
            "below -- reading whether that documentation resolves Curve #490's security "
            "identifier is this round's own follow-up, not asserted by this script."
        )

    return CurveIdentitySearchReport(
        generated_at=generated_at,
        search_capability_confirmed=True,
        apiflds_open_error=None,
        search_terms=search_terms,
        term_outcomes=tuple(term_outcomes),
        category_relevant_mnemonics=tuple(category_relevant_mnemonics),
        documentation_expansion=documentation_expansion,
        blocker_note=blocker_note,
    )


# --- rendering -------------------------------------------------------------------


def _record_to_dict(record: SearchResultRecord) -> dict:
    return {
        "search_term": record.search_term,
        "field_id": record.field_id,
        "mnemonic": record.mnemonic,
        "description": record.description,
        "category": record.category,
        "raw_record_dump": record.raw_record_dump,
        "category_relevant": record.category_relevant,
    }


def _term_outcome_to_dict(outcome: SearchTermOutcome) -> dict:
    return {
        "term": outcome.term,
        "status": outcome.status,
        "error": outcome.error,
        "structured_parse_ok": outcome.structured_parse_ok,
        "raw_response_dump": outcome.raw_response_dump,
        "records": [_record_to_dict(r) for r in outcome.records],
    }


def build_report(report: CurveIdentitySearchReport) -> dict:
    return {
        "generated_at": report.generated_at,
        "search_capability_confirmed": report.search_capability_confirmed,
        "apiflds_open_error": report.apiflds_open_error,
        "search_terms": list(report.search_terms),
        "category_filter_strings": list(CATEGORY_FILTER_STRINGS),
        "term_outcomes": [_term_outcome_to_dict(o) for o in report.term_outcomes],
        "category_relevant_mnemonics": list(report.category_relevant_mnemonics),
        "documentation_expansion": report.documentation_expansion,
        "blocker_note": report.blocker_note,
    }


def render_markdown(data: dict) -> str:
    lines: list[str] = []
    lines.append("# Bloomberg curve identity search (Issue #165 round 4)")
    lines.append("")
    lines.append(f"Generated at: {data['generated_at']}")
    lines.append(f"Search capability confirmed this run: {data['search_capability_confirmed']}")
    if data["apiflds_open_error"]:
        lines.append(f"apiflds open error: {data['apiflds_open_error']}")
    lines.append(f"Search terms: {', '.join(data['search_terms'])}")
    lines.append(f"Category filter strings: {', '.join(data['category_filter_strings'])}")
    lines.append("")

    for outcome in data["term_outcomes"]:
        lines.append(f"## {outcome['term']!r} -> {outcome['status']}")
        if outcome["error"]:
            lines.append(f"Error: {outcome['error']}")
        lines.append(f"Structured parse ok: {outcome['structured_parse_ok']}")
        if not outcome["records"]:
            if outcome["raw_response_dump"]:
                lines.append("Raw response dump:")
                lines.append("```")
                lines.append(outcome["raw_response_dump"])
                lines.append("```")
            lines.append("")
            continue
        for record in outcome["records"]:
            marker = "**RELEVANT**" if record["category_relevant"] else "not relevant"
            lines.append(
                f"- `{record['mnemonic'] or record['field_id'] or '?'}` ({marker})"
                + (f" -- {record['description']}" if record["description"] else "")
            )
            if record["category"]:
                lines.append(f"  category: {record['category']}")
        lines.append("")

    lines.append("## Category-relevant mnemonics")
    lines.append("")
    lines.append(", ".join(data["category_relevant_mnemonics"]) or "(none)")
    lines.append("")

    if data["blocker_note"]:
        lines.append("## Blocker / status")
        lines.append("")
        lines.append(data["blocker_note"])
        lines.append("")

    if data["documentation_expansion"]:
        lines.append("## Documentation expansion for category-relevant mnemonics")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(data["documentation_expansion"], indent=2, ensure_ascii=False))
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
            "Bounded read-only Bloomberg curve-identity FieldSearchRequest probe "
            "(Issue #165 round 4). Run with no arguments."
        )
    )
    parser.add_argument(
        "--search-term",
        action="append",
        default=None,
        dest="search_terms",
        help=(
            "Curve-identity search term; repeatable. Default: "
            + ", ".join(f"{t!r}" for t in DEFAULT_CURVE_IDENTITY_SEARCH_TERMS)
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
        tuple(args.search_terms) if args.search_terms else DEFAULT_CURVE_IDENTITY_SEARCH_TERMS
    )
    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd() / DEFAULT_OUTPUT_DIRNAME

    print("Shiori Bloomberg curve identity search probe (Issue #165 round 4)")
    print(f"Search terms: {', '.join(search_terms)}")
    print("")

    try:
        report = search_curve_identity_candidates(search_terms)
    except ImportError as exc:
        print(
            "error: blpapi is not installed -- run this on a Bloomberg-networked "
            f"workstation with Bloomberg's official blpapi package installed ({exc})",
            file=sys.stderr,
        )
        return 2

    data = build_report(report)
    markdown_path, json_path = write_report(data, output_dir)

    print(f"Search capability confirmed this run: {data['search_capability_confirmed']}")
    for outcome in data["term_outcomes"]:
        relevant = sum(1 for r in outcome["records"] if r["category_relevant"])
        print(f"  - {outcome['term']!r}: {outcome['status']}, {relevant} category-relevant")
    print("")
    print(
        f"Category-relevant mnemonics: {', '.join(data['category_relevant_mnemonics']) or '(none)'}"
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
