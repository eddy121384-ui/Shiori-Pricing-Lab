"""Bloomberg USD SOFR option discount curve acceptance path (Issue #165
production phase).

Bounded, read-only workstation diagnostic CLI -- **not** part of the
production pricing path, and never imported by it. Calls the production
loader directly, ``src/shiori_pricing_lab/data/bloomberg_option_discount_
curve.py::load_bloomberg_usd_sofr_option_discount_curve``, with its default
tenor set (``"1Y"``, ``"2Y"``) -- exactly the two tenors round 11's own
diagnostic probe (``tools/bloomberg_curve_ticker_last_price_probe.py``)
already confirmed acquisition for against the same two default tickers
(``S0490Z 2Y BLC2 Curncy``, ``S0490D 1Y BLC2 Curncy``).

**Purpose.** Let Eddy run the production ingestion path once on his own
Bloomberg workstation and see, side by side: each generated curve node's
``rate`` (decimal) *and* its recomputed percent value (``rate * 100`` --
the inverse of the loader's own percent-to-decimal conversion, so no
extra Bloomberg field is requested here), each tenor's preserved
discount-factor evidence and its own raw ``LAST_PRICE``, and the node's
own authoritative date. Eddy can then compare these against what round
11's probe already showed him for the same two default tickers, and
against terminal Curve #490 / SWDF directly. **This script asserts no
such match itself** -- it only surfaces the values for Eddy's own manual
acceptance judgment, the same discipline every prior round in this issue
has followed.

**``node_maturity`` vs. ``discount_factor_maturity``.** The report's
``node_maturity`` field is the curve node's own authoritative date --
Bloomberg's ``MATURITY`` for the ``Z`` ticker, read from ``BLICurvePoint.
maturity_date`` (the production loader already requires this to match the
``D`` ticker's own ``MATURITY`` at the same tenor, failing closed
otherwise). ``discount_factor_maturity`` is the ``D`` ticker's own
``MATURITY``, shown separately for transparency only -- it is never
labeled as the curve node's maturity, since ``D`` is corroborating
cross-check evidence, not the node's own source of date.

**One command, run once.** Eddy runs::

    python tools/bloomberg_usd_sofr_option_discount_curve_acceptance.py

and pastes back the console output (or the written report files).
``--tenors`` accepts an explicit, comma-separated override (e.g.
``--tenors 1Y,2Y,5Y``) if Eddy wants to acceptance-test a different tenor
set than the production default -- this script never generates or guesses
a tenor itself, exactly like the production loader it calls.

**Deliberately not in this script.** No pricing, no interpolation, no
discount-factor calculation, no ``BLIMarketDataSnapshot``/pricing-engine
wiring, no OIS bootstrap, no comparison assertion, no merge.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from shiori_pricing_lab.data.bloomberg_bond_quote import BLIBloombergDapiError
from shiori_pricing_lab.data.bloomberg_option_discount_curve import (
    DEFAULT_USD_SOFR_TENORS,
    BloombergUsdSofrOptionDiscountCurveResult,
    load_bloomberg_usd_sofr_option_discount_curve,
)

DEFAULT_OUTPUT_DIRNAME = "shiori_usd_sofr_option_discount_curve_acceptance_output"
MARKDOWN_FILENAME = "bloomberg_usd_sofr_option_discount_curve_acceptance.md"
JSON_FILENAME = "bloomberg_usd_sofr_option_discount_curve_acceptance.json"


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
    load=load_bloomberg_usd_sofr_option_discount_curve,
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
        result: BloombergUsdSofrOptionDiscountCurveResult = load(tenors=tenors)
    except (BLIBloombergDapiError, ImportError, ValueError) as exc:
        return AcceptanceReport(
            generated_at=generated_at,
            tenors=tenors,
            status="error",
            error=str(exc),
            nodes=(),
        )

    evidence_by_tenor = {e.tenor: e for e in result.discount_factor_evidence}
    nodes: list[dict] = []
    for point in result.curve_points:
        evidence = evidence_by_tenor.get(point.tenor)
        nodes.append(
            {
                "tenor": point.tenor,
                "curve_id": point.curve_id,
                "rate_basis": point.rate_basis.value,
                "zero_rate_decimal": point.rate,
                "zero_rate_percent_recomputed": point.rate * 100.0,
                # The curve node's own authoritative date -- Bloomberg's MATURITY
                # for the Z ticker, preserved verbatim on the BLICurvePoint itself.
                # Never the D ticker's date (see discount_factor_maturity below).
                "node_maturity": point.maturity_date,
                "discount_factor_security": evidence.security if evidence else None,
                "discount_factor": evidence.discount_factor if evidence else None,
                "discount_factor_raw_last_price": evidence.raw_last_price if evidence else None,
                "discount_factor_in_zero_one_range": (
                    (0.0 < evidence.discount_factor <= 1.0) if evidence else None
                ),
                # The D ticker's own MATURITY -- shown separately for transparency
                # only. The production loader already requires this to equal
                # node_maturity (fails closed otherwise), so this is corroborating
                # evidence, never a second candidate node date.
                "discount_factor_maturity": evidence.maturity if evidence else None,
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


def render_markdown(data: dict) -> str:
    lines: list[str] = []
    lines.append("# Bloomberg USD SOFR option discount curve acceptance path (Issue #165)")
    lines.append("")
    lines.append(f"Generated at: {data['generated_at']}")
    lines.append(f"Tenors: {', '.join(data['tenors'])}")
    lines.append(f"Status: {data['status']}")
    if data["error"]:
        lines.append(f"Error: {data['error']}")
    lines.append("")

    for node in data["nodes"]:
        lines.append(f"## tenor {node['tenor']}")
        lines.append(f"curve_id: {node['curve_id']}  rate_basis: {node['rate_basis']}")
        lines.append(
            f"zero rate: {node['zero_rate_decimal']} decimal "
            f"({node['zero_rate_percent_recomputed']} recomputed percent)"
        )
        lines.append(
            f"discount factor: {node['discount_factor']} "
            f"(raw LAST_PRICE {node['discount_factor_raw_last_price']!r} from "
            f"{node['discount_factor_security']!r}, in (0,1]: "
            f"{node['discount_factor_in_zero_one_range']})"
        )
        lines.append(f"node maturity (Z, authoritative): {node['node_maturity']}")
        lines.append(
            "discount-factor maturity (D, corroborating evidence): "
            f"{node['discount_factor_maturity']}"
        )
        lines.append("")

    lines.append("## Status")
    lines.append("")
    lines.append(
        "Compare each node's zero rate / discount factor / node maturity above "
        "against terminal Curve #490 / SWDF and against round 11's own probe "
        "output for the same default tickers. This script asserts no match itself "
        "-- that judgment is Eddy's."
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
            "Workstation acceptance path for the production USD SOFR option discount "
            "curve loader (Issue #165 production phase). Runs the loader once and "
            "reports each node for Eddy's own manual comparison."
        )
    )
    parser.add_argument(
        "--tenors",
        default=None,
        help=(
            "Comma-separated tenor labels to request, explicitly supplied -- never "
            f"generated or guessed. Default: {','.join(DEFAULT_USD_SOFR_TENORS)} (the "
            "production loader's own default, and round 11's already-confirmed pair)."
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

    print("Shiori Bloomberg USD SOFR option discount curve acceptance path (Issue #165)")
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
            f"  - tenor {node['tenor']}: rate={node['zero_rate_decimal']} "
            f"({node['zero_rate_percent_recomputed']}%)  DF={node['discount_factor']}  "
            f"node_maturity={node['node_maturity']}  "
            f"discount_factor_maturity={node['discount_factor_maturity']}"
        )
    print("")
    print("Full report (paste back or attach either file):")
    print(f"  {markdown_path.resolve()}")
    print(f"  {json_path.resolve()}")

    return 0 if data["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
