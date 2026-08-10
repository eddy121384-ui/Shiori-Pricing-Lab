"""Bloomberg curve field/override documentation expansion probe (Issue #165 round 3).

Bounded, read-only diagnostic CLI -- **not** part of the production pricing
or ingestion path, and never imported by either. Round 2's
``bloomberg_curve_discovery_probe.py`` proved, on Eddy's real workstation,
that ``//blp/apiflds``'s ``FieldSearchRequest.searchSpec`` returns real
field mnemonics for "stripped curve" / "zero curve" / "discount factor" /
"curve members" / "SWDF":

- ``SW174`` / ``SW_CRV_ZERO_RATES`` -- Swap Curve Zero Coupon Rates (BulkFormat)
- ``SW173`` / ``SW_CRV_DISCOUNT_FACTORS`` -- Swap Curve Discount Factors (BulkFormat)
- ``SW167`` / ``SW168`` / ``SW169``
- ``SW754`` / ``SW755``
- ``DY766`` / ``CURVE_MEMBERS``
- ``DY768`` / ``CURVE_DATE``

**This script's one job:** expand every one of those mnemonics' own
Bloomberg-published documentation (``//blp/apiflds``
``FieldInfoRequest(returnFieldDocumentation=True)``, via the already-reviewed
``bloomberg_dapi_probe.describe_fields``), then expand the documentation for
every override field id that documentation itself names -- two passes,
mirroring ``bloomberg_input_sourcing_probe.discover_option_context_metadata``'s
existing shape, generalized to this field list instead of that module's
fixed option-context catalogue. **No security or curve identifier is
requested here at all** -- ``FieldInfoRequest`` is pure metadata, so this
needs no curve identifier as input, which is exactly why it can run before
Curve #490's own security identifier is known.

**One command, run once.** Eddy runs::

    python tools/bloomberg_curve_field_documentation_probe.py

and pastes back the console output (or the written report files).

**No semantics are decided here, and none are guessed.** This script prints
Bloomberg's own ``description``/``documentation``/``datatype``/``overrides``
text for each field verbatim (sanitized only for host/session/client
leakage, never redacted for content) -- it makes no claim about what any
field or override means. Determining curve identity, curve date, side,
tenor, frequency, or zero-rate/discount-factor basis semantics from that
documentation, and determining whether it resolves Curve #490's own
security identifier, is Issue #165's own follow-up analysis of this
script's *output* -- never something promoted here from a mnemonic's name
alone.

**Separation from production/pricing.** This module is never imported by
anything under ``src/``, performs no pricing, no OIS bootstrap, and writes
nothing except its own diagnostic report files. It reuses
``tools/bloomberg_dapi_probe.py``'s ``describe_fields`` (already proven, no
new session/request implementation) and
``tools/bloomberg_input_sourcing_probe.py``'s ``sanitize_external_text``
(already widened in this same round for client/session/host identity
leakage) for redaction.

**Deliberately not in this script (Issue #165 round 3 items 4-5).** No
value-level ``ReferenceDataRequest`` against any security is sent here --
that targeted "request SW174 + SW173 against a real curve identifier"
probe is the *next* step, gated on this script's documentation evidence
actually resolving what security identifier to request against. Building
that value-probe before that evidence exists would mean guessing a
security/curve identifier, which this script and its sibling probes never
do.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from bloomberg_dapi_probe import FieldDescription, describe_fields
from bloomberg_input_sourcing_probe import sanitize_external_text

# Field mnemonics Eddy's own Issue #165 round 2 workstation run actually
# returned via //blp/apiflds's FieldSearchRequest.searchSpec for the search
# terms "stripped curve" / "zero curve" / "discount factor" / "curve
# members" / "SWDF" -- not guessed by this repo. Provenance: Eddy's pasted
# workstation output, Issue #165 round 3 request.
ROUND_2_CONFIRMED_FIELDS: tuple[str, ...] = (
    "SW174",  # SW_CRV_ZERO_RATES -- Swap Curve Zero Coupon Rates (BulkFormat)
    "SW173",  # SW_CRV_DISCOUNT_FACTORS -- Swap Curve Discount Factors (BulkFormat)
    "SW167",
    "SW168",
    "SW169",
    "SW754",
    "SW755",
    "DY766",  # CURVE_MEMBERS
    "DY768",  # CURVE_DATE
)

_PRIMARY_FIELD_PROVENANCE = (
    "Confirmed by Eddy's own Issue #165 round 2 workstation "
    "FieldSearchRequest.searchSpec run -- not guessed by this repo."
)

DEFAULT_OUTPUT_DIRNAME = "shiori_curve_field_documentation_output"
MARKDOWN_FILENAME = "bloomberg_curve_field_documentation_probe.md"
JSON_FILENAME = "bloomberg_curve_field_documentation_probe.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _sanitized_description(description: FieldDescription) -> FieldDescription:
    """Sanitize the Bloomberg-authored text on one field description.

    ``description``/``documentation``/``detail`` are Bloomberg-authored
    free text and go through ``sanitize_external_text`` at this boundary,
    exactly like ``bloomberg_input_sourcing_probe``'s own
    ``_sanitized_description`` does for its option-context fields --
    ``field``/``status``/``mnemonic``/``datatype``/``overrides`` are
    Bloomberg's own short structural tokens, not sanitized (they are never
    a place client/session/host identity metadata would appear).
    """

    return replace(
        description,
        description=sanitize_external_text(description.description),
        documentation=sanitize_external_text(description.documentation),
        detail=sanitize_external_text(description.detail),
    )


@dataclass(frozen=True)
class ExpansionReport:
    generated_at: str
    primary_fields: tuple[str, ...]
    primary_descriptions: tuple[FieldDescription, ...]
    primary_error: str | None
    override_fields: tuple[str, ...]
    override_descriptions: tuple[FieldDescription, ...]
    override_error: str | None


def discover_field_documentation(
    fields: tuple[str, ...] = ROUND_2_CONFIRMED_FIELDS,
    describe=None,
    clock=None,
) -> ExpansionReport:
    """Two-pass ``//blp/apiflds`` ``FieldInfoRequest`` documentation expansion.

    Pass 1 describes ``fields`` themselves. Pass 2 describes the union of
    every override field id pass 1's own documentation named for any of
    them -- mirroring
    ``bloomberg_input_sourcing_probe.discover_option_context_metadata``'s
    two-pass shape, generalized to an arbitrary field list instead of that
    module's fixed option-context catalogue. Neither pass sends a security
    request -- ``FieldInfoRequest`` is metadata only. A pass-1 failure still
    returns cleanly with an empty pass 2 (nothing left to expand); a pass-2
    failure is recorded on its own and never discards pass 1's results.
    """

    describe = describe or describe_fields
    clock = clock or _utc_now
    generated_at = clock()
    try:
        primary_descriptions = tuple(_sanitized_description(d) for d in describe(list(fields)))
        primary_error = None
    except RuntimeError as exc:
        primary_descriptions = ()
        primary_error = sanitize_external_text(str(exc))

    override_fields: list[str] = []
    for description in primary_descriptions:
        for override_field in description.overrides:
            if override_field not in override_fields:
                override_fields.append(override_field)

    override_descriptions: tuple[FieldDescription, ...] = ()
    override_error: str | None = None
    if override_fields:
        try:
            override_descriptions = tuple(
                _sanitized_description(d) for d in describe(override_fields)
            )
        except RuntimeError as exc:
            override_error = sanitize_external_text(str(exc))

    return ExpansionReport(
        generated_at=generated_at,
        primary_fields=tuple(fields),
        primary_descriptions=primary_descriptions,
        primary_error=primary_error,
        override_fields=tuple(override_fields),
        override_descriptions=override_descriptions,
        override_error=override_error,
    )


# --- rendering -------------------------------------------------------------------


def _description_to_dict(description: FieldDescription) -> dict:
    return {
        "field": description.field,
        "status": description.status,
        "mnemonic": description.mnemonic,
        "description": description.description,
        "datatype": description.datatype,
        "overrides": list(description.overrides),
        "documentation": description.documentation,
        "detail": description.detail,
    }


def build_report(report: ExpansionReport) -> dict:
    return {
        "generated_at": report.generated_at,
        "primary_fields": list(report.primary_fields),
        "primary_field_provenance": _PRIMARY_FIELD_PROVENANCE,
        "primary_descriptions": [_description_to_dict(d) for d in report.primary_descriptions],
        "primary_error": report.primary_error,
        "override_fields": list(report.override_fields),
        "override_descriptions": [_description_to_dict(d) for d in report.override_descriptions],
        "override_error": report.override_error,
    }


def _render_description_section(title: str, descriptions: list[dict]) -> list[str]:
    lines = [f"## {title}", ""]
    if not descriptions:
        lines.append("(none)")
        lines.append("")
        return lines
    for description in descriptions:
        lines.append(f"### {description['field']}")
        lines.append(f"Status: {description['status']}")
        if description["mnemonic"]:
            lines.append(f"Mnemonic: {description['mnemonic']}")
        if description["datatype"]:
            lines.append(f"Datatype: {description['datatype']}")
        if description["description"]:
            lines.append(f"Description: {description['description']}")
        if description["overrides"]:
            lines.append(f"Overrides: {', '.join(description['overrides'])}")
        if description["documentation"]:
            lines.append("")
            lines.append("Documentation:")
            lines.append("```")
            lines.append(description["documentation"])
            lines.append("```")
        if description["detail"]:
            lines.append(f"Detail: {description['detail']}")
        lines.append("")
    return lines


def render_markdown(data: dict) -> str:
    lines: list[str] = []
    lines.append("# Bloomberg curve field/override documentation expansion (Issue #165 round 3)")
    lines.append("")
    lines.append(f"Generated at: {data['generated_at']}")
    lines.append(f"Primary fields: {', '.join(data['primary_fields'])}")
    lines.append(f"Primary field provenance: {data['primary_field_provenance']}")
    lines.append("")
    if data["primary_error"]:
        lines.append(f"**Primary pass error:** {data['primary_error']}")
        lines.append("")
    lines.extend(_render_description_section("Primary fields", data["primary_descriptions"]))
    lines.append(f"Discovered override fields: {', '.join(data['override_fields']) or '(none)'}")
    lines.append("")
    if data["override_error"]:
        lines.append(f"**Override pass error:** {data['override_error']}")
        lines.append("")
    lines.extend(_render_description_section("Overrides", data["override_descriptions"]))
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
            "Bounded read-only Bloomberg //blp/apiflds field/override documentation "
            "expansion probe (Issue #165 round 3). Run with no arguments."
        )
    )
    parser.add_argument(
        "--field",
        action="append",
        default=None,
        dest="fields",
        help=(
            "Field mnemonic to expand documentation for; repeatable. Default: the "
            f"Issue #165 round 2 confirmed fields ({', '.join(ROUND_2_CONFIRMED_FIELDS)})."
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
    fields = tuple(args.fields) if args.fields else ROUND_2_CONFIRMED_FIELDS
    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd() / DEFAULT_OUTPUT_DIRNAME

    print("Shiori Bloomberg curve field/override documentation probe (Issue #165 round 3)")
    print(f"Primary fields: {', '.join(fields)}")
    print("")

    try:
        report = discover_field_documentation(fields)
    except ImportError as exc:
        print(
            "error: blpapi is not installed -- run this on a Bloomberg-networked "
            f"workstation with Bloomberg's official blpapi package installed ({exc})",
            file=sys.stderr,
        )
        return 2

    data = build_report(report)
    markdown_path, json_path = write_report(data, output_dir)

    if data["primary_error"]:
        print(f"Primary pass error: {data['primary_error']}")
    for description in data["primary_descriptions"]:
        print(
            f"  - {description['field']}: {description['status']}"
            + (f" ({description['datatype']})" if description["datatype"] else "")
        )
        if description["overrides"]:
            print(f"      overrides: {', '.join(description['overrides'])}")

    print("")
    print(f"Discovered override fields: {', '.join(data['override_fields']) or '(none)'}")
    if data["override_error"]:
        print(f"Override pass error: {data['override_error']}")
    for description in data["override_descriptions"]:
        print(f"  - {description['field']}: {description['status']}")

    print("")
    print("Full report (paste back or attach either file):")
    print(f"  {markdown_path.resolve()}")
    print(f"  {json_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
