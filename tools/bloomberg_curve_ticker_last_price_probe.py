"""Bloomberg Curve #490 ticker LAST_PRICE probe (Issue #165 round 11).

Bounded, read-only diagnostic CLI -- **not** part of the production pricing
or ingestion path, and never imported by either. Rounds 7-10 tried, in
turn, a guessed security identifier (``S490``, ``BAD_SEC``), guessed
``SW174`` overrides (``SW569``/``SW564``, all materially wrong vs. terminal
Curve #490), and Bloomberg's own ``curveListRequest`` taxonomy -- none of
these produced a confirmed acquisition route. This round stops all of that:
Eddy found the *exact* ticker strings Curve #490's own Excel export uses,
by exporting Bloomberg's own formulas directly from the terminal::

    =BDP("S0490Z 2Y BLC2 Curncy","LAST_PRICE")
    =BDP("S0490D 1Y BLC2 Curncy","LAST_PRICE")

**Both security identifiers are treated as workstation evidence, never
inferred ticker patterns.** They are Eddy's own literal strings, copied
verbatim from his exported Excel formulas -- not derived from any
taxonomy/search/override probing in rounds 4-10, and not a pattern this
script extrapolates to other tenors. ``--zero-rate-security``/
``--discount-factor-security`` default to those two literal strings; a
rejected identifier is corrected only via an explicit CLI override, never
by this script trying a variant suffix/tenor/yellow-key.

**Requests exactly one field, already used verbatim in Eddy's own Excel
formula, never guessed:** ``LAST_PRICE``, for both securities in one
``ReferenceDataRequest`` against ``//blp/refdata``.

**Per-security identity matching, not response-order assumption.** Both
securities are requested in the same call, so this script reuses the same
identity-check idea ``data/bloomberg_bond_quote.py``'s production loaders
already trust: each returned ``securityData`` record carries its own
``security`` element, and every result below is matched to its role
(zero-rate ticker / discount-factor ticker) by that returned identifier
string, never by assuming array order. A security identifier that doesn't
come back at all, or comes back more than once, is reported as its own
per-security record-count problem -- it never silently blocks the other
security's own result.

**Scalar values are reported separately, never combined.** ``LAST_PRICE``
is a scalar (unlike ``SW173``/``SW174``'s bulk rows) -- each security's own
raw string value is parsed as a float, when possible, purely as a
convenience for Eddy's manual comparison; this script never asserts a
match to terminal Curve #490.

**Discount-factor plausibility is a factual range check only, not a
validation verdict.** For the discount-factor ticker's own parsed value,
this script reports whether it falls in ``(0, 1]`` -- a discount factor's
own known domain constraint, not an opinion about whether it matches
Curve #490/SWDF at the corresponding tenor/date. Round 11's own acceptance
criteria (a zero-rate value consistent with terminal Curve #490's own
zero-rate instrument, and a plausible discount factor consistent with
Curve #490/SWDF at the corresponding tenor/date) are Eddy's own manual
judgment against the terminal screen -- this script only surfaces the raw
and parsed values for that comparison, never decides it.

**One command, run once.** Eddy runs::

    python tools/bloomberg_curve_ticker_last_price_probe.py

and pastes back the console output (or the written report files).

**Deliberately not in this script (per Eddy's explicit round-11
instruction).** No other tenor is derived, no ticker pattern is generated
or extrapolated from these two strings, no ``SW174``/``curveListRequest``/
curve-identity/bootstrap exploration of any kind, no production loader, no
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

# Eddy's own literal strings, copied verbatim from Curve #490's exported
# Excel formulas (Issue #165 round 11) -- not derived, inferred, or
# extrapolated by this script. A rejected identifier is corrected via the
# matching --*-security flag, never by this script trying a variant.
DEFAULT_ZERO_RATE_SECURITY = "S0490Z 2Y BLC2 Curncy"
DEFAULT_DISCOUNT_FACTOR_SECURITY = "S0490D 1Y BLC2 Curncy"

# The field named verbatim in both of Eddy's Excel formulas -- not guessed.
FIELD = "LAST_PRICE"

ZERO_RATE_ROLE = "zero_rate_ticker"
DISCOUNT_FACTOR_ROLE = "discount_factor_ticker"

DEFAULT_OUTPUT_DIRNAME = "shiori_curve_ticker_last_price_output"
MARKDOWN_FILENAME = "bloomberg_curve_ticker_last_price_probe.md"
JSON_FILENAME = "bloomberg_curve_ticker_last_price_probe.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class SecurityLastPriceResult:
    role: str
    security: str
    # "returned" | "security_error" | "field_exception" | "absent" |
    # "record_count_error"
    status: str
    raw_value: str | None
    parsed_value: float | None
    detail: str | None
    # Only meaningful for DISCOUNT_FACTOR_ROLE: True/False once a value
    # parsed, None if no numeric value was available to check at all.
    plausible_discount_factor: bool | None


@dataclass(frozen=True)
class TickerLastPriceReport:
    generated_at: str
    request_status: str  # "sent" | "error"
    request_error: str | None
    results: tuple[SecurityLastPriceResult, ...]
    raw_response_dump: str | None
    blocker_note: str


def _parse_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value


def _resolve_one_security(
    role: str,
    security: str,
    records_by_identifier: dict[str, list],
) -> SecurityLastPriceResult:
    matches = records_by_identifier.get(security, [])

    if len(matches) != 1:
        detail = (
            f"Bloomberg returned {len(matches)} securityData record(s) whose own security "
            f"identifier equals {security!r}, expected exactly one."
        )
        return SecurityLastPriceResult(
            role=role,
            security=security,
            status="record_count_error",
            raw_value=None,
            parsed_value=None,
            detail=sanitize_external_text(detail),
            plausible_discount_factor=None,
        )

    record = matches[0]

    security_error: str | None = None
    try:
        if record.hasElement("securityError"):
            security_error = sanitize_external_text(_safe_str(record.getElement("securityError")))
    except Exception:  # noqa: BLE001
        pass

    if security_error is not None:
        return SecurityLastPriceResult(
            role=role,
            security=security,
            status="security_error",
            raw_value=None,
            parsed_value=None,
            detail=security_error,
            plausible_discount_factor=None,
        )

    field_exception_detail: str | None = None
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
                if field_id == FIELD:
                    detail = _safe_str(exception_element)
                    try:
                        if exception_element.hasElement("errorInfo"):
                            detail = _safe_str(exception_element.getElement("errorInfo"))
                    except Exception:  # noqa: BLE001
                        pass
                    field_exception_detail = sanitize_external_text(detail)
    except Exception:  # noqa: BLE001
        pass

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

    if has_field:
        raw_value: str | None = None
        try:
            raw_value = field_data.getElementAsString(FIELD)
        except Exception:  # noqa: BLE001
            raw_value = None
        sanitized_raw_value = sanitize_external_text(raw_value) if raw_value is not None else None
        parsed_value = _parse_float(raw_value)

        plausible_discount_factor: bool | None = None
        if role == DISCOUNT_FACTOR_ROLE and parsed_value is not None:
            plausible_discount_factor = 0.0 < parsed_value <= 1.0

        return SecurityLastPriceResult(
            role=role,
            security=security,
            status="returned",
            raw_value=sanitized_raw_value,
            parsed_value=parsed_value,
            detail=None,
            plausible_discount_factor=plausible_discount_factor,
        )

    if field_exception_detail is not None:
        return SecurityLastPriceResult(
            role=role,
            security=security,
            status="field_exception",
            raw_value=None,
            parsed_value=None,
            detail=field_exception_detail,
            plausible_discount_factor=None,
        )

    return SecurityLastPriceResult(
        role=role,
        security=security,
        status="absent",
        raw_value=None,
        parsed_value=None,
        detail=None,
        plausible_discount_factor=None,
    )


def run_ticker_last_price_probe(
    zero_rate_security: str = DEFAULT_ZERO_RATE_SECURITY,
    discount_factor_security: str = DEFAULT_DISCOUNT_FACTOR_SECURITY,
    send_request=_send_request,
) -> TickerLastPriceReport:
    """Send one ``ReferenceDataRequest`` for both tickers/``LAST_PRICE`` and parse the response.

    Both security identifiers must be non-blank strings, explicitly supplied
    by the caller (defaults: Eddy's own literal Excel-formula strings) --
    never derived. Raises :class:`ValueError` for a blank/non-string
    security, the same caller-input-problem contract this repo's other
    Bloomberg probes already use.
    """

    if not isinstance(zero_rate_security, str) or not zero_rate_security.strip():
        raise ValueError(
            "zero_rate_security must be a non-blank string, explicitly supplied by the caller"
        )
    if not isinstance(discount_factor_security, str) or not discount_factor_security.strip():
        raise ValueError(
            "discount_factor_security must be a non-blank string, explicitly supplied by the caller"
        )
    zero_rate_security = zero_rate_security.strip()
    discount_factor_security = discount_factor_security.strip()
    generated_at = _utc_now()

    def _configure(request) -> None:
        request.append("securities", zero_rate_security)
        request.append("securities", discount_factor_security)
        request.append("fields", FIELD)

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
                f"ReferenceDataRequest([{zero_rate_security!r}, {discount_factor_security!r}], "
                f"fields=[{FIELD!r}])"
            ),
        )
        request_status = "sent"
        request_error = None
    except (RuntimeError, ImportError) as exc:
        request_status = "error"
        request_error = sanitize_external_text(str(exc))

    raw_dump = "\n---\n".join(sanitize_external_text(text) for text in raw_messages if text) or None

    if request_status == "error":
        return TickerLastPriceReport(
            generated_at=generated_at,
            request_status=request_status,
            request_error=request_error,
            results=(),
            raw_response_dump=raw_dump,
            blocker_note=f"The ReferenceDataRequest failed: {request_error}",
        )

    records_by_identifier: dict[str, list] = {}
    for record in security_data_records:
        try:
            identifier = (
                record.getElementAsString("security") if record.hasElement("security") else None
            )
        except Exception:  # noqa: BLE001
            identifier = None
        if identifier is None:
            continue
        records_by_identifier.setdefault(identifier, []).append(record)

    results = (
        _resolve_one_security(ZERO_RATE_ROLE, zero_rate_security, records_by_identifier),
        _resolve_one_security(
            DISCOUNT_FACTOR_ROLE, discount_factor_security, records_by_identifier
        ),
    )

    if all(result.status == "returned" for result in results):
        blocker_note = (
            "LAST_PRICE returned for both tickers -- compare the zero-rate ticker's own "
            "value against terminal Curve #490's own zero-rate instrument, and the "
            "discount-factor ticker's own value against Curve #490/SWDF at the "
            "corresponding tenor/date. Both comparisons are Eddy's own manual judgment; "
            "this script asserts neither."
        )
    else:
        failing = ", ".join(f"{r.role} ({r.status})" for r in results if r.status != "returned")
        blocker_note = (
            f"LAST_PRICE was not returned for: {failing}. Review each security's own "
            "status/detail below before concluding whether the identifier is wrong or the "
            "field is genuinely inapplicable here. No fallback identifier or tenor is "
            "guessed."
        )

    return TickerLastPriceReport(
        generated_at=generated_at,
        request_status=request_status,
        request_error=None,
        results=results,
        raw_response_dump=raw_dump,
        blocker_note=blocker_note,
    )


# --- rendering -------------------------------------------------------------------


def _result_to_dict(result: SecurityLastPriceResult) -> dict:
    return {
        "role": result.role,
        "security": result.security,
        "status": result.status,
        "raw_value": result.raw_value,
        "parsed_value": result.parsed_value,
        "detail": result.detail,
        "plausible_discount_factor": result.plausible_discount_factor,
    }


def build_report(report: TickerLastPriceReport) -> dict:
    return {
        "generated_at": report.generated_at,
        "field": FIELD,
        "request_status": report.request_status,
        "request_error": report.request_error,
        "results": [_result_to_dict(r) for r in report.results],
        "raw_response_dump": report.raw_response_dump,
        "blocker_note": report.blocker_note,
    }


def render_markdown(data: dict) -> str:
    lines: list[str] = []
    lines.append("# Bloomberg Curve #490 ticker LAST_PRICE probe (Issue #165 round 11)")
    lines.append("")
    lines.append(f"Generated at: {data['generated_at']}")
    lines.append(f"Field requested (Eddy's own Excel formula, verbatim): {data['field']}")
    lines.append(f"Request status: {data['request_status']}")
    if data["request_error"]:
        lines.append(f"Request error: {data['request_error']}")
    lines.append("")

    for result in data["results"]:
        lines.append(f"## {result['role']}: {result['security']} -- {result['status']}")
        if result["raw_value"] is not None:
            lines.append(f"Raw LAST_PRICE: {result['raw_value']}")
        if result["parsed_value"] is not None:
            lines.append(f"Parsed value: {result['parsed_value']}")
        if result["plausible_discount_factor"] is not None:
            lines.append(
                f"Plausible discount factor in (0, 1]: {result['plausible_discount_factor']}"
            )
        if result["detail"]:
            lines.append(f"Detail: {result['detail']}")
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
            "Bounded read-only Bloomberg LAST_PRICE probe (Issue #165 round 11). Requests "
            "exactly LAST_PRICE for Eddy's two Excel-formula ticker strings from Curve #490."
        )
    )
    parser.add_argument(
        "--zero-rate-security",
        default=DEFAULT_ZERO_RATE_SECURITY,
        help=(
            "Bloomberg security identifier for the zero-rate ticker, supplied explicitly -- "
            f"never derived or guessed. Default: {DEFAULT_ZERO_RATE_SECURITY!r} (Eddy's own "
            "Excel formula, verbatim)."
        ),
    )
    parser.add_argument(
        "--discount-factor-security",
        default=DEFAULT_DISCOUNT_FACTOR_SECURITY,
        help=(
            "Bloomberg security identifier for the discount-factor ticker, supplied "
            f"explicitly -- never derived or guessed. Default: "
            f"{DEFAULT_DISCOUNT_FACTOR_SECURITY!r} (Eddy's own Excel formula, verbatim)."
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

    print("Shiori Bloomberg Curve #490 ticker LAST_PRICE probe (Issue #165 round 11)")
    print(f"Zero-rate ticker: {args.zero_rate_security!r}")
    print(f"Discount-factor ticker: {args.discount_factor_security!r}")
    print(f"Field: {FIELD}")
    print("")

    try:
        report = run_ticker_last_price_probe(args.zero_rate_security, args.discount_factor_security)
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
    for result in data["results"]:
        value = result["raw_value"] if result["status"] == "returned" else (result["detail"] or "")
        print(f"  - {result['role']} ({result['security']}): {result['status']}  {value}")
    print("")
    print(data["blocker_note"])

    print("")
    print("Full report (paste back or attach either file):")
    print(f"  {markdown_path.resolve()}")
    print(f"  {json_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
