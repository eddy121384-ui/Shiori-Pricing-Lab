"""Workstation acceptance path for the historical bond-Yield loader (Issue #196 §E).

Bounded, read-only CLI. Runs the production loader
``data/bloomberg_bond_yield_history.load_bloomberg_bond_yield_history``
exactly once -- the same code path Markets -> Bond Yield History uses, never
a parallel request implementation -- against one real supported bond, and
writes the acceptance record Issue #196 §E asks for:

- security requested, and the security Bloomberg itself resolved;
- the confirmed Yield field (whatever ``--field`` you pass; this tool has no
  default and never guesses one -- see
  ``tools/bloomberg_bond_yield_field_probe.py``, which produces it);
- request date range;
- number of observations returned;
- first/last observation dates, and how many returned rows carried no value;
- unit/source semantics, exactly as you supply them from Bloomberg's own
  documentation (``--field-unit``/``--field-meaning``) -- never inferred here;
- acquisition timestamp.

**Live values never reach a file.** The written report carries only that
shape evidence. A bounded sample of dated values is printed to the console
so you can check a handful of dates against the Terminal or Excel by eye;
that sample is deliberately not written to disk, so running this inside a
repository checkout cannot leave proprietary Bloomberg values behind.

Nothing here prices, stores, or wires anything: no VCUB store, no vol
resolver, no PRICE_VOL/YIELD_VOL, no Forward, no Discounting. It reads one
series and reports it.

Example::

    python tools/bloomberg_bond_yield_history_acceptance.py \\
        --identifier US91282CQX00 \\
        --field <confirmed mnemonic> \\
        --start 2025-09-01 --end 2026-09-01
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from shiori_pricing_lab.data.bloomberg_bond_quote import (  # noqa: E402
    BLIBloombergDapiError,
    parse_bond_identifier,
)
from shiori_pricing_lab.data.bloomberg_bond_yield_history import (  # noqa: E402
    BloombergBondYieldHistory,
    load_bloomberg_bond_yield_history,
)

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

DEFAULT_OUTPUT_DIRNAME = "shiori_bond_yield_history_acceptance_output"
MARKDOWN_FILENAME = "bloomberg_bond_yield_history_acceptance.md"
JSON_FILENAME = "bloomberg_bond_yield_history_acceptance.json"

DEFAULT_SAMPLE_ROWS = 8

CONSOLE_SAMPLE_WARNING = (
    "The sample rows below are live Bloomberg data. They are printed for your own "
    "Terminal/Excel comparison only -- they are not written to any report file, and "
    "must not be pasted into the repository."
)


@dataclass(frozen=True)
class AcceptanceReport:
    generated_at: str
    requested_identifier: str
    bloomberg_identifier: str
    identifier_kind: str
    yield_field: str
    start_date: str
    end_date: str
    status: str  # "loaded" | "error"
    history: BloombergBondYieldHistory | None
    error: str | None


def _parse_iso_date_argument(value: str, name: str) -> date:
    if not _ISO_DATE_RE.match(value):
        raise ValueError(f"--{name} must be a YYYY-MM-DD date, got {value!r}")
    return date.fromisoformat(value)


def run_acceptance(
    *,
    identifier: str,
    yield_field: str,
    start: date,
    end: date,
    field_meaning: str | None,
    field_unit: str | None,
) -> AcceptanceReport:
    """Resolve the identifier and run the production loader once."""

    generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    kind, bloomberg_identifier = parse_bond_identifier(identifier)

    try:
        history = load_bloomberg_bond_yield_history(
            identifier=bloomberg_identifier,
            yield_field=yield_field,
            start_date=start,
            end_date=end,
            field_meaning=field_meaning,
            field_unit=field_unit,
        )
    except (BLIBloombergDapiError, ValueError) as exc:
        return AcceptanceReport(
            generated_at=generated_at,
            requested_identifier=identifier,
            bloomberg_identifier=bloomberg_identifier,
            identifier_kind=kind,
            yield_field=yield_field,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            status="error",
            history=None,
            error=f"{type(exc).__name__}: {exc}",
        )

    return AcceptanceReport(
        generated_at=generated_at,
        requested_identifier=identifier,
        bloomberg_identifier=bloomberg_identifier,
        identifier_kind=kind,
        yield_field=yield_field,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        status="loaded",
        history=history,
        error=None,
    )


def build_report(report: AcceptanceReport) -> dict:
    """The acceptance record as plain data -- never a Bloomberg value."""

    history = report.history
    observations = history.observations if history else ()
    valued = [o for o in observations if o.yield_value is not None]
    return {
        "acceptance": "bloomberg_bond_yield_history",
        "issue": 196,
        "generated_at": report.generated_at,
        "status": report.status,
        "error": report.error,
        "requested_identifier": report.requested_identifier,
        "identifier_kind": report.identifier_kind,
        "bloomberg_identifier": report.bloomberg_identifier,
        "resolved_security": history.security if history else None,
        "yield_field": report.yield_field,
        "field_meaning": history.field_meaning if history else None,
        "field_unit": history.field_unit if history else None,
        "requested_start_date": report.start_date,
        "requested_end_date": report.end_date,
        "observation_count": len(observations),
        "observations_with_a_value": len(valued),
        "rows_with_no_value": len(observations) - len(valued),
        "first_observation_date": (
            observations[0].observation_date.isoformat() if observations else None
        ),
        "last_observation_date": (
            observations[-1].observation_date.isoformat() if observations else None
        ),
        "source_system": history.source_system if history else None,
        "acquired_at": history.acquired_at if history else None,
        "values_note": (
            "This record deliberately carries no Bloomberg value. Only counts, dates "
            "and provenance are recorded."
        ),
    }


def render_markdown(data: dict) -> str:
    lines = [
        "# Bloomberg historical bond-Yield acceptance (Issue #196)",
        "",
        f"- Generated at: {data['generated_at']}",
        f"- Status: {data['status']}",
    ]
    if data["error"]:
        lines += [f"- Error: {data['error']}"]
    lines += [
        f"- Requested identifier: {data['requested_identifier']} "
        f"({data['identifier_kind']}) -> {data['bloomberg_identifier']}",
        f"- Resolved security: {data['resolved_security'] or '-'}",
        f"- Yield field: {data['yield_field']}",
        f"- Field meaning: {data['field_meaning'] or '(not confirmed)'}",
        f"- Field unit: {data['field_unit'] or '(not confirmed)'}",
        f"- Requested range: {data['requested_start_date']} .. {data['requested_end_date']}",
        f"- Observations returned: {data['observation_count']}",
        f"- Observations carrying a value: {data['observations_with_a_value']}",
        f"- Returned rows with no value: {data['rows_with_no_value']}",
        f"- First observation date: {data['first_observation_date'] or '-'}",
        f"- Last observation date: {data['last_observation_date'] or '-'}",
        f"- Source: {data['source_system'] or '-'}",
        f"- Acquired at: {data['acquired_at'] or '-'}",
        "",
        f"_{data['values_note']}_",
        "",
    ]
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


def console_sample(
    report: AcceptanceReport, sample_rows: int
) -> tuple[tuple[str, str | None], ...]:
    """A bounded ``(date, raw value)`` sample for the operator's own eye only."""

    if report.history is None or sample_rows <= 0:
        return ()
    observations = report.history.observations
    chosen = list(observations[:sample_rows])
    if len(observations) > sample_rows:
        chosen.append(observations[-1])
    return tuple((o.observation_date.isoformat(), o.raw_value) for o in chosen)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Workstation acceptance path for the production historical bond-Yield "
            "loader (Issue #196). Runs the loader once against one real bond and "
            "records the acceptance evidence."
        )
    )
    parser.add_argument("--identifier", required=True, help="A 12-char ISIN or 9-char CUSIP.")
    parser.add_argument(
        "--field",
        required=True,
        help=(
            "The workstation-confirmed Bloomberg Yield mnemonic. Required, with no "
            "default -- produced by tools/bloomberg_bond_yield_field_probe.py."
        ),
    )
    parser.add_argument("--start", required=True, help="Inclusive start date, YYYY-MM-DD.")
    parser.add_argument("--end", required=True, help="Inclusive end date, YYYY-MM-DD.")
    parser.add_argument(
        "--field-meaning",
        default=None,
        help="Bloomberg's own description of what this field is, recorded verbatim.",
    )
    parser.add_argument(
        "--field-unit",
        default=None,
        help="Bloomberg's own unit for this field, recorded verbatim (never inferred).",
    )
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=DEFAULT_SAMPLE_ROWS,
        help=(
            f"How many dated values to print for your Terminal comparison (default "
            f"{DEFAULT_SAMPLE_ROWS}). Never written to a file."
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
        start = _parse_iso_date_argument(args.start, "start")
        end = _parse_iso_date_argument(args.end, "end")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd() / DEFAULT_OUTPUT_DIRNAME

    print("Shiori Bloomberg historical bond-Yield acceptance path (Issue #196)")
    print(f"Bond: {args.identifier}   Field: {args.field}   Range: {args.start} .. {args.end}")
    print("")

    try:
        report = run_acceptance(
            identifier=args.identifier,
            yield_field=args.field,
            start=start,
            end=end,
            field_meaning=args.field_meaning,
            field_unit=args.field_unit,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    data = build_report(report)
    markdown_path, json_path = write_report(data, output_dir)

    print(f"Status: {data['status']}")
    if data["error"]:
        print(f"error: {data['error']}", file=sys.stderr)
    print(f"Resolved security: {data['resolved_security']}")
    print(
        f"Observations: {data['observation_count']} "
        f"(with a value: {data['observations_with_a_value']}, "
        f"no value: {data['rows_with_no_value']})"
    )
    print(f"First / last: {data['first_observation_date']} / {data['last_observation_date']}")
    print(f"Source: {data['source_system']}   Acquired at: {data['acquired_at']}")

    sample = console_sample(report, max(0, args.sample_rows))
    if sample:
        print("")
        print(CONSOLE_SAMPLE_WARNING)
        for observation_date, raw_value in sample:
            print(f"    {observation_date}  {raw_value if raw_value is not None else '(no value)'}")

    print("")
    print(f"Report written: {markdown_path}")
    print(f"Report written: {json_path}")
    return 0 if data["status"] == "loaded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
