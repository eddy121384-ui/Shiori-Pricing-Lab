"""UST S490 repo-carry forward vs OVME F parity path (Issue #173).

Bounded, read-only workstation diagnostic CLI -- **not** part of the
production pricing path, and never imported by it. This is the
expiry-driven prototype Issue #173 asks for: Eddy supplies one already-
valid standalone option case and one or more forward (expiry) dates, and
for **each** date Shiori re-resolves the Bloomberg Curve #490 / S490
funding and re-derives the forward clean price from scratch. No repo rate
is ever typed in, and no OVME repo value is hard-coded anywhere in this
repository.

**One command, run at the Bloomberg workstation**::

    python tools/ust_s490_repo_carry_forward_parity.py \\
        --case my_case.json \\
        --spot-settlement-date 2026-08-13 \\
        --horizon 2026-09-15 \\
        --horizon 2026-11-13=99.8125 \\
        --horizon 2027-02-16=99.5

``--horizon`` is repeatable and is the expiry/forward-date knob: each
value is one forward settlement date, optionally followed by ``=`` and the
OVME F **displayed forward clean price per 100 as a decimal** for that
same date. Supply the OVME number to get a parity residual; omit it to get
the derived forward alone. Issue #173 asks for at least three different
expiries/horizons, so pass ``--horizon`` at least three times when
producing acceptance evidence.

**What each horizon reports** (Issue #173 acceptance criteria 7-8): the
S490-derived funding rate and carry factor with their conventions, every
step of the spot-clean -> spot-dirty -> forward-dirty -> forward-clean
transition, the forward as a decimal **and** as a Treasury fraction, the
OVME F number as supplied, the decimal residual, and the residual in
Treasury ticks (32nds).

**Where every number comes from -- pure composition, no new math here.**

- the case is parsed by the existing, unmodified
  ``app/standalone_option_workbench.build_request_from_standalone_option_case``,
  so the underlying bond, its spot clean quote, the valuation date and the
  currency are resolved exactly as the Workbench itself resolves them --
  this script contains no case parser, schema, or bond-matching rule of
  its own;
- the S490 curve is acquired by the existing, unmodified Issue #171
  injector ``app/standalone_option_workbench_server.inject_live_option_
  discount_curve_if_absent``, which calls the production Curve #490 loader
  and applies the same same-as-of RED gate (a live curve is only ever
  paired with a ``valuation_date`` equal to today). A case that already
  carries manual curve nodes is used exactly as supplied, untouched;
- the funding transformation is
  ``pricing/bli_s490_funding_resolver.resolve_s490_repo_carry_funding``,
  under whichever of its two labeled prototype methods ``--funding-method``
  selects (default: the spot-starting term-rate reading -- see that
  module's docstring for both, and for why exactly two exist);
- the forward is ``pricing/bli_repo_carry_forward.repo_carry_forward_clean_price``.

Run it once per method to let parity, not this script, decide between
them.

**This script asserts no Bloomberg match.** It reports the residual it
computed and nothing more -- exactly the discipline
``bloomberg_usd_sofr_option_discount_curve_acceptance.py`` (Issue #165)
and ``bloomberg_usd_sofr_par_rate_curve_acceptance.py`` (Issue #168)
already established. Whether the S490 transformation is close enough to
OVME F is Eddy's judgment, on his own evidence.

**Deliberately not in this script.** No Black-76, option discounting,
volatility, or premium of any kind -- the case's own
``forward_clean_price_input`` (the existing explicit-forward override
contract) is read by nothing here and is left completely untouched. No
repo-rate calibration, tuning, or spread adjustment. No OVME screen
scraping or IR repo-display reverse-engineering. No pricing-path wiring,
and no merge.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from shiori_pricing_lab.app.standalone_option_workbench import (
    build_request_from_standalone_option_case,
)
from shiori_pricing_lab.app.standalone_option_workbench_server import (
    inject_live_option_discount_curve_if_absent,
)
from shiori_pricing_lab.pricing.bli_repo_carry_forward import (
    repo_carry_forward_clean_price,
)
from shiori_pricing_lab.pricing.bli_s490_funding_resolver import (
    DEFAULT_S490_FUNDING_METHOD,
    S490FundingMethod,
    resolve_s490_repo_carry_funding,
)

DEFAULT_OUTPUT_DIRNAME = "shiori_ust_s490_repo_carry_forward_parity_output"
MARKDOWN_FILENAME = "ust_s490_repo_carry_forward_parity.md"
JSON_FILENAME = "ust_s490_repo_carry_forward_parity.json"

# Treasury price display (Issue #161's own trader-desk convention, applied
# in the opposite direction). ``script.js::parseTreasuryQuote`` already
# reads "98-16" / "98-16+" / "98-164" into a decimal per 100; this is that
# same convention rendered back out, so a number Eddy compares against OVME
# reads the way his screen does.
_TREASURY_32NDS_PER_POINT = 32
_TREASURY_EIGHTHS_PER_32ND = 8
_TREASURY_PLUS_EIGHTHS = 4


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def format_price_as_treasury_fraction(price_per_100: float) -> str:
    """Return ``price_per_100`` as a Treasury quote string, e.g. ``"98-16+"``.

    The inverse of the existing ``parseTreasuryQuote`` convention in
    ``prototype/bond-option-workbench/script.js`` (Issue #161): a handle,
    two 32nds digits, then an optional sub-32nd -- ``"+"`` for a half 32nd,
    otherwise a digit counting eighths of a 32nd.

    **This is a rounded display, never a pricing value.** A derived forward
    is an arbitrary decimal, so it is rounded to the nearest eighth of a
    32nd (1/256 of a point) to be displayable at all; every report this
    script writes carries the exact decimal alongside it, and every
    residual is computed from the decimal, never from this string. A
    negative price (not a thing this prototype produces, but not worth
    silently mis-rendering) is returned with a leading ``-`` on the
    magnitude.
    """

    sign = "-" if price_per_100 < 0 else ""
    magnitude = abs(float(price_per_100))
    total_eighths = round(magnitude * _TREASURY_32NDS_PER_POINT * _TREASURY_EIGHTHS_PER_32ND)
    eighths_per_point = _TREASURY_32NDS_PER_POINT * _TREASURY_EIGHTHS_PER_32ND
    handle, remainder = divmod(total_eighths, eighths_per_point)
    thirty_seconds, eighths = divmod(remainder, _TREASURY_EIGHTHS_PER_32ND)

    if eighths == 0:
        tail = ""
    elif eighths == _TREASURY_PLUS_EIGHTHS:
        tail = "+"
    else:
        tail = str(eighths)
    return f"{sign}{handle}-{thirty_seconds:02d}{tail}"


@dataclass(frozen=True)
class ParityHorizon:
    """One requested forward settlement date and its optional OVME F number."""

    forward_settlement_date: str
    observed_ovme_forward_clean_price_per_100: float | None


@dataclass(frozen=True)
class ParityReport:
    generated_at: str
    status: str  # "ok" | "error"
    error: str | None
    case: dict
    horizons: tuple[dict, ...]


def parse_horizon_argument(raw: str) -> ParityHorizon:
    """Parse one ``--horizon`` value: ``DATE`` or ``DATE=OVME_FORWARD_DECIMAL``.

    The date itself is **not** validated here -- it is passed straight to
    the funding resolver / forward primitive, whose own already-reviewed
    ``_parse_iso_date`` reports a malformed date with a precise message.
    The optional OVME F price must be a plain finite decimal per 100 (a
    Treasury quote string such as ``"99-16+"`` is deliberately not accepted
    here: this script does not own a second quote parser). Raises
    ``ValueError`` for an empty date or an unparseable price.
    """

    date_text, separator, price_text = raw.partition("=")
    date_text = date_text.strip()
    if not date_text:
        raise ValueError(f"--horizon {raw!r} is missing a forward settlement date")
    if not separator:
        return ParityHorizon(
            forward_settlement_date=date_text,
            observed_ovme_forward_clean_price_per_100=None,
        )
    price_text = price_text.strip()
    try:
        observed = float(price_text)
    except ValueError as exc:
        raise ValueError(
            f"--horizon {raw!r} has an OVME forward {price_text!r} that is not a decimal "
            "price per 100 (for example 99.828125)"
        ) from exc
    return ParityHorizon(
        forward_settlement_date=date_text,
        observed_ovme_forward_clean_price_per_100=observed,
    )


def _horizon_result(
    *,
    request,
    horizon: ParityHorizon,
    spot_settlement_date: str,
    spot_clean_price_per_100: float,
    method: S490FundingMethod | str,
) -> dict:
    """Resolve S490 funding and the repo-carry forward for one horizon.

    Returns one flat dict per horizon. A failure affecting only this
    horizon (a date outside the curve's node range, an interim coupon, a
    malformed date) is captured as this row's own ``status``/``error`` so
    the remaining horizons in the same run still report -- Issue #173 asks
    for the residual to be reported, never hidden, and a run covering three
    expiries should not lose two of them to one bad third.
    """

    row: dict = {
        "forward_settlement_date": horizon.forward_settlement_date,
        "observed_ovme_forward_clean_price_per_100": (
            horizon.observed_ovme_forward_clean_price_per_100
        ),
        "status": "ok",
        "error": None,
        "funding": None,
        "forward": None,
        "forward_clean_price_per_100": None,
        "forward_clean_price_treasury_fraction": None,
        "residual_decimal_per_100": None,
        "residual_treasury_ticks_32nds": None,
    }

    try:
        funding = resolve_s490_repo_carry_funding(
            request.market_data_snapshot.curve_points,
            currency=request.bond_option.currency,
            curve_as_of_date=request.valuation_date,
            spot_settlement_date=spot_settlement_date,
            forward_settlement_date=horizon.forward_settlement_date,
            method=method,
        )
        forward = repo_carry_forward_clean_price(
            bond=request.resolved_bond_reference_data,
            spot_clean_price_per_100=spot_clean_price_per_100,
            spot_settlement_date=spot_settlement_date,
            forward_settlement_date=horizon.forward_settlement_date,
            repo_rate_decimal=funding.derived_repo_rate_decimal,
        )
    except Exception as exc:  # noqa: BLE001 -- reported per horizon, never swallowed
        row["status"] = "error"
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row

    row["funding"] = asdict(funding)
    row["forward"] = asdict(forward)
    row["forward_clean_price_per_100"] = forward.forward_clean_price_per_100
    row["forward_clean_price_treasury_fraction"] = format_price_as_treasury_fraction(
        forward.forward_clean_price_per_100
    )

    observed = horizon.observed_ovme_forward_clean_price_per_100
    if observed is not None:
        residual = forward.forward_clean_price_per_100 - observed
        row["residual_decimal_per_100"] = residual
        row["residual_treasury_ticks_32nds"] = residual * _TREASURY_32NDS_PER_POINT
    return row


def run_parity(
    *,
    case: dict,
    spot_settlement_date: str,
    horizons: tuple[ParityHorizon, ...],
    method: S490FundingMethod | str = DEFAULT_S490_FUNDING_METHOD,
    inject_curve=inject_live_option_discount_curve_if_absent,
    build_request=build_request_from_standalone_option_case,
) -> ParityReport:
    """Run the whole expiry-driven parity pass and return its report.

    ``inject_curve`` and ``build_request`` are injectable callables
    (defaults: the real, unmodified production functions) so this
    report-building/CLI logic is testable without ``blpapi`` -- the same
    seam ``bloomberg_usd_sofr_par_rate_curve_acceptance.py`` already uses.

    A failure that affects the whole run (the Bloomberg curve acquisition,
    the same-as-of gate, an invalid case, a missing spot clean price)
    returns ``status="error"`` with no horizon rows; a failure affecting one
    horizon only is reported on that horizon's own row.
    """

    generated_at = _utc_now()

    try:
        priced_case = inject_curve(case)
        request = build_request(priced_case)
        spot_clean_price = request.market_data_snapshot.bond_quote.clean_price_per_100
        if spot_clean_price is None:
            raise ValueError(
                "bond_quote.clean_price_per_100 must be present -- a yield-only quote "
                "carries no spot clean price for the repo-carry forward to start from"
            )
    except Exception as exc:  # noqa: BLE001 -- surfaced verbatim in the report
        return ParityReport(
            generated_at=generated_at,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            case={},
            horizons=(),
        )

    snapshot = request.market_data_snapshot
    case_summary = {
        "s490_funding_method": str(method),
        "snapshot_id": snapshot.snapshot_id,
        "underlying_isin": request.resolved_bond_reference_data.isin,
        "currency": str(request.bond_option.currency),
        "valuation_date": request.valuation_date,
        "spot_settlement_date": spot_settlement_date,
        "spot_clean_price_per_100": spot_clean_price,
        "spot_clean_price_treasury_fraction": format_price_as_treasury_fraction(spot_clean_price),
        "bond_quote_side": str(snapshot.bond_quote.quote_side),
        "bond_quote_source_system": snapshot.bond_quote.source_system,
        "curve_point_count": len(snapshot.curve_points),
        "curve_ids": sorted({point.curve_id for point in snapshot.curve_points}),
    }

    rows = tuple(
        _horizon_result(
            request=request,
            horizon=horizon,
            spot_settlement_date=spot_settlement_date,
            spot_clean_price_per_100=spot_clean_price,
            method=method,
        )
        for horizon in horizons
    )

    return ParityReport(
        generated_at=generated_at,
        status="ok",
        error=None,
        case=case_summary,
        horizons=rows,
    )


# --- rendering -------------------------------------------------------------------


def build_report(report: ParityReport) -> dict:
    return {
        "generated_at": report.generated_at,
        "status": report.status,
        "error": report.error,
        "case": report.case,
        "horizons": list(report.horizons),
    }


_PARITY_TABLE_HEADERS = (
    "Forward date",
    "Term (days)",
    "S490 repo rate (%)",
    "Carry factor",
    "Shiori forward",
    "Shiori (32nds)",
    "OVME F",
    "Residual",
    "Residual (ticks)",
)


def _cell(value: object, digits: int) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.{digits}f}"


def _parity_table_row(row: dict) -> tuple[str, ...]:
    if row["status"] != "ok":
        return (
            row["forward_settlement_date"],
            "error",
            "error",
            "error",
            "error",
            "error",
            _cell(row["observed_ovme_forward_clean_price_per_100"], 6),
            "error",
            "error",
        )
    funding = row["funding"]
    return (
        row["forward_settlement_date"],
        str(funding["repo_term_days"]),
        _cell(funding["derived_repo_rate_decimal"] * 100.0, 6),
        _cell(funding["carry_factor"], 10),
        _cell(row["forward_clean_price_per_100"], 6),
        row["forward_clean_price_treasury_fraction"],
        _cell(row["observed_ovme_forward_clean_price_per_100"], 6),
        _cell(row["residual_decimal_per_100"], 6),
        _cell(row["residual_treasury_ticks_32nds"], 4),
    )


def render_markdown(data: dict) -> str:
    lines: list[str] = []
    lines.append("# UST S490 repo-carry forward vs OVME F parity (Issue #173)")
    lines.append("")
    lines.append(f"Generated at: {data['generated_at']}")
    lines.append(f"Status: {data['status']}")
    if data["error"]:
        lines.append(f"Error: {data['error']}")
    lines.append("")

    if data["case"]:
        lines.append("## Case")
        lines.append("")
        for key, value in data["case"].items():
            lines.append(f"- {key}: {value}")
        lines.append("")

    if data["horizons"]:
        lines.append("## Parity by horizon")
        lines.append("")
        lines.append("| " + " | ".join(_PARITY_TABLE_HEADERS) + " |")
        lines.append("|" + "|".join(["---"] * len(_PARITY_TABLE_HEADERS)) + "|")
        for row in data["horizons"]:
            lines.append("| " + " | ".join(_parity_table_row(row)) + " |")
        lines.append("")
        lines.append(
            "Residual = Shiori derived forward clean price - OVME F displayed forward, "
            "in decimal per 100 and in Treasury ticks (32nds). Both are computed from "
            "the exact decimals; the 32nds column is a rounded display only."
        )
        lines.append("")

        lines.append("## Per-horizon detail")
        lines.append("")
        for row in data["horizons"]:
            lines.append(f"### forward settlement {row['forward_settlement_date']}")
            if row["status"] != "ok":
                lines.append(f"status: error -- {row['error']}")
                lines.append("")
                continue
            funding = row["funding"]
            forward = row["forward"]
            lines.append(f"S490 funding method: {funding['method']}")
            lines.append(f"source representation: {funding['source_representation']}")
            lines.append(
                f"curve ids: {', '.join(funding['curve_ids'])}  "
                f"source systems: {', '.join(funding['source_systems'])}"
            )
            for evaluation in funding["curve_evaluations"]:
                lines.append(
                    f"S490 read [{evaluation['label']}] at {evaluation['target_date']}: "
                    f"coordinate {evaluation['curve_year_fraction']} "
                    f"({funding['curve_coordinate_day_count_convention']}), "
                    f"discount factor {evaluation['discount_factor']}"
                )
            lines.append(
                f"derived repo rate: {funding['derived_repo_rate_decimal']} decimal "
                f"({funding['derived_repo_rate_decimal'] * 100.0} percent), "
                f"{funding['repo_day_count_convention']}, "
                f"{forward['repo_compounding_convention']} compounding, "
                f"term {funding['repo_term_days']} days "
                f"({funding['repo_term_year_fraction']})"
            )
            lines.append(f"carry factor: {funding['carry_factor']}")
            lines.append(
                f"spot clean {forward['spot_clean_price_per_100']} + AI("
                f"{forward['spot_settlement_date']}) "
                f"{forward['accrued_interest_at_spot_settlement_per_100']} "
                f"= spot dirty {forward['spot_dirty_price_per_100']}"
            )
            lines.append(
                f"spot dirty x carry factor = forward dirty "
                f"{forward['forward_dirty_price_per_100']}"
            )
            lines.append(
                f"forward dirty - AI({forward['forward_settlement_date']}) "
                f"{forward['accrued_interest_at_forward_settlement_per_100']} "
                f"= forward clean {forward['forward_clean_price_per_100']} "
                f"({row['forward_clean_price_treasury_fraction']})"
            )
            if row["observed_ovme_forward_clean_price_per_100"] is not None:
                lines.append(
                    f"OVME F forward: {row['observed_ovme_forward_clean_price_per_100']}  "
                    f"residual: {row['residual_decimal_per_100']} per 100 "
                    f"({row['residual_treasury_ticks_32nds']} ticks)"
                )
            else:
                lines.append("OVME F forward: not supplied -- no residual computed")
            lines.append("")

    lines.append("## Status")
    lines.append("")
    lines.append(
        "This script asserts no Bloomberg parity itself. The S490 funding "
        "transformation is a labeled Issue #173 prototype assumption "
        "(see pricing/bli_s490_funding_resolver.py); whether the residuals above "
        "are small and stable enough to proceed is Eddy's judgment."
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Derive a UST forward clean price from Bloomberg Curve #490 / S490 "
            "repo-carry funding for one or more expiries, and compare it with "
            "OVME F (Issue #173)."
        )
    )
    parser.add_argument(
        "--case",
        required=True,
        help="Path to one standalone option case JSON file (the Workbench case envelope).",
    )
    parser.add_argument(
        "--spot-settlement-date",
        required=True,
        help=(
            "Explicit spot settlement date (YYYY-MM-DD) the spot clean quote settles on. "
            "Required and explicit: this repository derives no settlement date from a "
            "lag, calendar, or business-day rule."
        ),
    )
    parser.add_argument(
        "--horizon",
        action="append",
        required=True,
        metavar="DATE[=OVME_FORWARD]",
        help=(
            "Forward settlement date, optionally with the OVME F displayed forward "
            "clean price per 100 as a decimal. Repeatable -- Issue #173 asks for at "
            "least three different expiries/horizons."
        ),
    )
    parser.add_argument(
        "--funding-method",
        default=str(DEFAULT_S490_FUNDING_METHOD),
        choices=[member.value for member in S490FundingMethod],
        help=(
            "Which labeled S490 transformation to apply (both are Issue #173 prototype "
            f"assumptions; default: {DEFAULT_S490_FUNDING_METHOD.value})."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Directory to write the Markdown/JSON report into "
            f"(default: ./{DEFAULT_OUTPUT_DIRNAME})."
        ),
    )
    args = parser.parse_args(argv)

    try:
        horizons = tuple(parse_horizon_argument(raw) for raw in args.horizon)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    case_path = Path(args.case)
    try:
        case = json.loads(case_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: could not read case JSON {str(case_path)!r}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(case, dict):
        print(f"error: case JSON {str(case_path)!r} must be a JSON object", file=sys.stderr)
        return 2

    report = run_parity(
        case=case,
        spot_settlement_date=args.spot_settlement_date,
        horizons=horizons,
        method=args.funding_method,
    )
    data = build_report(report)

    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd() / DEFAULT_OUTPUT_DIRNAME
    markdown_path, json_path = write_report(data, output_dir)

    print(render_markdown(data))
    print("")
    print(f"Wrote {markdown_path}")
    print(f"Wrote {json_path}")
    return 0 if report.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
