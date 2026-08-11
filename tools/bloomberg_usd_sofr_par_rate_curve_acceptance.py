"""Bloomberg USD SOFR OIS SWAP (Par Rate) acceptance path (Issue #168).

Bounded, read-only workstation diagnostic CLI -- **not** part of the
production pricing path, and never imported by it. Calls the production
loader directly, ``src/shiori_pricing_lab/data/bloomberg_usd_sofr_par_rate_
curve.py::load_bloomberg_usd_sofr_par_rate_curve``, with its default tenor
set -- ``DEFAULT_USD_SOFR_TENORS``, the full 32-tenor approved ``USOSFR*``
universe (Issue #168's own table).

**Purpose.** Let Eddy run the production ingestion path once on his own
Bloomberg workstation and see, side by side, every requested tenor's
``USOSFR*`` security, its raw ``LAST_PRICE``, and the normalized
``par_rate_percent`` -- so he can compare selected short/belly/long-end
nodes against terminal Curve #490 / SWDF directly before declaring this
issue merge-ready (Issue #168 requirement #8). **This script asserts no
such match itself** -- it only surfaces the values for Eddy's own manual
acceptance judgment, the same discipline
``bloomberg_usd_sofr_option_discount_curve_acceptance.py`` (Issue #165)
already established.

**Compact full-curve table.** The written Markdown report leads with one
row per tenor -- tenor, security, raw ``LAST_PRICE``, and the normalized
par rate percent -- so Eddy can scan the whole curve and pick short/belly/
long-end nodes to check against terminal Curve #490 / SWDF without reading
the full per-tenor detail below it. The same fields are on every node in
the JSON output too.

**One command, run once.** Eddy runs::

    python tools/bloomberg_usd_sofr_par_rate_curve_acceptance.py

and pastes back the console output (or the written report files).
``--tenors`` accepts an explicit, comma-separated override (e.g.
``--tenors 1Y,2Y,5Y``) if Eddy wants to acceptance-test a different tenor
set than the production default -- this script never generates or guesses
a tenor itself, exactly like the production loader it calls.

**Deliberately not in this script.** No pricing, no interpolation, no
bootstrap, no comparison assertion, no wiring into
``load_bloomberg_usd_sofr_option_discount_curve`` or any pricing module,
no merge.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from shiori_pricing_lab.data.bloomberg_bond_quote import BLIBloombergDapiError
from shiori_pricing_lab.data.bloomberg_usd_sofr_par_rate_curve import (
    DEFAULT_USD_SOFR_TENORS,
    BloombergUsdSofrParRateCurveResult,
    load_bloomberg_usd_sofr_par_rate_curve,
)

DEFAULT_OUTPUT_DIRNAME = "shiori_usd_sofr_par_rate_curve_acceptance_output"
MARKDOWN_FILENAME = "bloomberg_usd_sofr_par_rate_curve_acceptance.md"
JSON_FILENAME = "bloomberg_usd_sofr_par_rate_curve_acceptance.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class AcceptanceReport:
    generated_at: str
    tenors: tuple[str, ...]
    status: str  # "ok" | "error"
    error: str | None
    nodes: tuple[dict, ...]


def run_acceptance(
    tenors: tuple[str, ...] = DEFAULT_USD_SOFR_TENORS,
    load=load_bloomberg_usd_sofr_par_rate_curve,
) -> AcceptanceReport:
    """Run the production loader once and build a side-by-side acceptance report.

    ``load`` is an injectable callable (default: the real production
    loader) so this report-building/CLI logic can be tested without
    ``blpapi``. Never asserts a match to any prior observation -- only
    reports what the production loader returned for Eddy's own manual
    comparison.
    """

    generated_at = _utc_now()

    try:
        result: BloombergUsdSofrParRateCurveResult = load(tenors=tenors)
    except (BLIBloombergDapiError, ImportError, ValueError) as exc:
        return AcceptanceReport(
            generated_at=generated_at,
            tenors=tenors,
            status="error",
            error=str(exc),
            nodes=(),
        )

    nodes: list[dict] = []
    for point in result.points:
        nodes.append(
            {
                "tenor": point.tenor,
                "security": point.security,
                "raw_last_price": point.raw_last_price,
                "par_rate_percent": point.par_rate_percent,
                "source_system": point.source_system,
            }
        )

    return AcceptanceReport(
        generated_at=generated_at,
        tenors=tenors,
        status="ok",
        error=None,
        nodes=tuple(nodes),
    )


# --- rendering -------------------------------------------------------------------


def build_report(report: AcceptanceReport) -> dict:
    return {
        "generated_at": report.generated_at,
        "tenors": list(report.tenors),
        "status": report.status,
        "error": report.error,
        "nodes": list(report.nodes),
    }


_COMPACT_TABLE_HEADERS = (
    "Tenor",
    "Security",
    "Raw LAST_PRICE",
    "Par rate (percent)",
)


def _compact_table_row(node: dict) -> tuple[str, ...]:
    return (
        node["tenor"],
        node["security"],
        node["raw_last_price"],
        str(node["par_rate_percent"]),
    )


def render_markdown(data: dict) -> str:
    lines: list[str] = []
    lines.append("# Bloomberg USD SOFR OIS SWAP (Par Rate) acceptance path (Issue #168)")
    lines.append("")
    lines.append(f"Generated at: {data['generated_at']}")
    lines.append(f"Tenors: {', '.join(data['tenors'])}")
    lines.append(f"Status: {data['status']}")
    if data["error"]:
        lines.append(f"Error: {data['error']}")
    lines.append("")

    if data["nodes"]:
        lines.append("## Full curve, compact table")
        lines.append("")
        lines.append("| " + " | ".join(_COMPACT_TABLE_HEADERS) + " |")
        lines.append("|" + "|".join(["---"] * len(_COMPACT_TABLE_HEADERS)) + "|")
        for node in data["nodes"]:
            lines.append("| " + " | ".join(_compact_table_row(node)) + " |")
        lines.append("")
        lines.append(
            "Compare selected short / belly / long-end rows above against "
            "terminal Curve #490 / SWDF manually. This table does not claim "
            "Bloomberg parity itself."
        )
        lines.append("")

    lines.append("## Per-tenor detail")
    lines.append("")
    for node in data["nodes"]:
        lines.append(f"### tenor {node['tenor']}")
        lines.append(
            f"security: {node['security']}  source_system: {node['source_system']}"
        )
        lines.append(
            f"par rate: {node['par_rate_percent']} percent "
            f"(raw LAST_PRICE {node['raw_last_price']!r})"
        )
        lines.append("")

    lines.append("## Status")
    lines.append("")
    lines.append(
        "Compare selected short / belly / long-end nodes' par rate above against "
        "terminal Curve #490 / SWDF. This script asserts no match itself -- that "
        "judgment is Eddy's."
    )

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
            "Workstation acceptance path for the production USD SOFR OIS SWAP "
            "(Par Rate) loader (Issue #168). Runs the loader once and reports "
            "each node for Eddy's own manual comparison."
        )
    )
    parser.add_argument(
        "--tenors",
        default=None,
        help=(
            "Comma-separated tenor labels to request, explicitly supplied -- never "
            "generated or guessed. Default: the production loader's own "
            f"{len(DEFAULT_USD_SOFR_TENORS)}-tenor approved default "
            f"({','.join(DEFAULT_USD_SOFR_TENORS)})."
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
    tenors = (
        tuple(t.strip() for t in args.tenors.split(",") if t.strip())
        if args.tenors
        else DEFAULT_USD_SOFR_TENORS
    )

    print("Shiori Bloomberg USD SOFR OIS SWAP (Par Rate) acceptance path (Issue #168)")
    print(f"Tenors: {', '.join(tenors)}")
    print("")

    report = run_acceptance(tenors)
    data = build_report(report)
    markdown_path, json_path = write_report(data, output_dir)

    print(f"Status: {data['status']}")
    if data["error"]:
        print(f"error: {data['error']}", file=sys.stderr)
    for node in data["nodes"]:
        print(
            f"  - tenor {node['tenor']} ({node['security']}): "
            f"par_rate_percent={node['par_rate_percent']}  "
            f"raw_last_price={node['raw_last_price']!r}"
        )
    print("")
    print("Full report (paste back or attach either file):")
    print(f"  {markdown_path.resolve()}")
    print(f"  {json_path.resolve()}")

    return 0 if data["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
