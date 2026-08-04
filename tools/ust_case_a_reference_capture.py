"""Bounded local-only DAPI capture for Issue #153 UST Case A.

Run on Eddy's Bloomberg workstation with a real ISIN/CUSIP.  The identifier is
used only for the request and is never written to the output.  This helper
requests only the six mnemonics already confirmed by PR #141; it neither probes
new fields nor touches a pricing path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from bloomberg_dapi_probe import ProbeFieldResult, probe_fields

from shiori_pricing_lab.data.bloomberg_bond_quote import parse_bond_identifier

FIELDS = ("CPN", "CPN_FREQ", "ISSUE_DT", "MATURITY", "FIRST_CPN_DT", "DAY_CNT_DES")
FIELD_DESTINATIONS = {
    "CPN": "coupon_rate_decimal",
    "CPN_FREQ": "coupon_frequency",
    "ISSUE_DT": "issue_date",
    "MATURITY": "maturity_date",
    "FIRST_CPN_DT": "first_coupon_date",
    "DAY_CNT_DES": "day_count_display_only",
}
FREQUENCIES = {"1": "ANNUAL", "2": "SEMI_ANNUAL", "4": "QUARTERLY", "12": "MONTHLY"}


def normalize_treasury_fraction(value: str) -> float:
    """Normalize Bloomberg-style 32nds; ``+`` is one half of a 32nd."""

    match = re.fullmatch(r"(\d+)-(\d{2})(\+|\d)?", value)
    if match is None:
        raise ValueError(f"unsupported Treasury fraction: {value!r}")
    whole, thirty_seconds, tail = match.groups()
    numerator = int(thirty_seconds)
    if numerator > 31:
        raise ValueError(f"32nds component must be 00..31: {value!r}")
    extra_256ths = 4 if tail == "+" else int(tail or "0")
    return int(whole) + numerator / 32 + extra_256ths / 256


def normalize_display_percent(value: str) -> float:
    """Convert one explicit percent display to decimal, with no basis inference."""

    if not re.fullmatch(r"\d+(?:\.\d+)?%", value):
        raise ValueError(f"unsupported percent display: {value!r}")
    return float(Decimal(value[:-1]) / Decimal(100))


def _normalize(result: ProbeFieldResult) -> object | None:
    if result.status != "returned" or result.value is None:
        return None
    value = result.value.strip()
    if result.field == "CPN":
        try:
            return float(value) / 100.0
        except ValueError:
            return None
    if result.field == "CPN_FREQ":
        return FREQUENCIES.get(value)
    return value


def build_capture(
    results: list[ProbeFieldResult], *, requested_at: str, retrieved_at: str
) -> dict:
    """Build the redacted artifact; per-field failures remain isolated."""

    by_field = {result.field: result for result in results}
    fields = []
    for mnemonic in FIELDS:
        result = by_field.get(mnemonic, ProbeFieldResult(mnemonic, "absent"))
        normalized = _normalize(result)
        fields.append(
            {
                "target": FIELD_DESTINATIONS[mnemonic],
                "bloomberg_field": mnemonic,
                "adapter_symbol": (
                    "_BOND_MASTER_RAW_DISPLAY_FIELD_MAP"
                    if mnemonic == "DAY_CNT_DES"
                    else "_BOND_MASTER_FIELD_MAP"
                ),
                "source": "DAPI_REFERENCE_DATA",
                "source_identifier": "UST_CASE_A_UNDERLYING_REDACTED_V1",
                "retrieval_timestamp": retrieved_at,
                "status": result.status.upper(),
                "normalized_value": normalized,
                "error": result.detail,
            }
        )
    return {
        "schema": "issue-153-case-a-dapi-reference-capture-v1",
        "case_id": "UST_OVME_CASE_A_20260804_REDACTED_V1",
        "redacted_underlying_key": "UST_CASE_A_UNDERLYING_REDACTED_V1",
        "requested_at": requested_at,
        "retrieved_at": retrieved_at,
        "identifier_committed": False,
        "raw_bloomberg_payload_committed": False,
        "fields": fields,
        "unmapped_required_terms": [
            "last_coupon_date",
            "redemption_amount",
            "settlement_convention",
            "business_day_convention",
            "typed_day_count",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--identifier", required=True, help="Real ISIN/CUSIP; never persisted")
    parser.add_argument("--output", required=True, type=Path, help="Local JSON output path")
    args = parser.parse_args()
    qualified_identifier = parse_bond_identifier(args.identifier)
    requested_at = datetime.now(UTC).isoformat()
    results = probe_fields(qualified_identifier, list(FIELDS))
    retrieved_at = datetime.now(UTC).isoformat()
    capture = build_capture(results, requested_at=requested_at, retrieved_at=retrieved_at)
    serialized = json.dumps(capture, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized, encoding="utf-8")
    print(f"wrote redacted capture: {args.output}")
    print(f"sha256: {hashlib.sha256(serialized.encode()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
