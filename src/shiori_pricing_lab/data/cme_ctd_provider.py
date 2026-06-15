"""CME CTD data cache helpers.

This module intentionally does not hard-code a proprietary CME endpoint. CME data
access can vary by tool, login state, entitlement, and delivery mechanism. The
safe pattern for this repo is:

1. Fetch from a configured URL in a script or workflow.
2. Normalize into a small local JSON cache.
3. Let pricing/UI modules consume only the normalized cache.

Normalized cache schema:

{
  "updated_at": "2026-06-15T08:00:00Z",
  "source_url": "https://...",
  "records": [
    {
      "contract_code": "ZNM6",
      "futures_symbol": "ZN",
      "delivery_month": "2026-06",
      "ctd_cusip": "...",
      "coupon_rate": 0.04,
      "maturity_date": "2034-05-15",
      "conversion_factor": 0.9012,
      "last_delivery_date": "2026-06-30",
      "source": "CME / manual / sample",
      "description": "Synthetic sample"
    }
  ]
}
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shiori_pricing_lab.pricing.treasury_futures import CTDInstrument, ctd_from_dict


FIELD_ALIASES = {
    "contract_code": ["contract_code", "contract", "futures_contract", "futuresContract", "code"],
    "futures_symbol": ["futures_symbol", "product", "symbol", "futuresSymbol"],
    "delivery_month": ["delivery_month", "deliveryMonth", "contract_month", "contractMonth"],
    "ctd_cusip": ["ctd_cusip", "cusip", "ctdCusip", "security_id", "securityId"],
    "coupon_rate": ["coupon_rate", "coupon", "couponRate", "ctd_coupon", "ctdCoupon"],
    "maturity_date": ["maturity_date", "maturity", "maturityDate", "ctd_maturity", "ctdMaturity"],
    "conversion_factor": ["conversion_factor", "conversionFactor", "cf", "conv_factor"],
    "last_delivery_date": [
        "last_delivery_date",
        "lastDeliveryDate",
        "delivery_date",
        "deliveryDate",
        "settlement_date",
        "settlementDate",
    ],
    "source": ["source"],
    "updated_at": ["updated_at", "updatedAt"],
    "description": ["description", "security", "securityDescription", "ctdDescription"],
}


def load_ctd_cache(path: str | Path) -> list[CTDInstrument]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    records = payload.get("records", payload if isinstance(payload, list) else [])
    return [ctd_from_dict(record) for record in records]


def read_raw_payload(path: str | Path) -> Any:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".csv":
        return list(csv.DictReader(text.splitlines()))
    return json.loads(text)


def normalize_ctd_records(payload: Any, *, source: str = "unknown") -> list[dict[str, Any]]:
    """Normalize common JSON/CSV shapes into the cache schema.

    This function is intentionally forgiving because upstream CME/export schemas
    can change. It supports:
    - a list of records;
    - a dict containing ``records``, ``data``, ``items``, ``deliverables`` or
      ``contracts``;
    - one level of nested deliverable basket records under each contract.
    """

    candidate_records = _extract_records(payload)
    normalized: list[dict[str, Any]] = []

    for raw_record in candidate_records:
        if not isinstance(raw_record, dict):
            continue

        record = _normalize_one_record(raw_record, source=source)
        required = [
            "contract_code",
            "coupon_rate",
            "maturity_date",
            "conversion_factor",
            "last_delivery_date",
        ]
        if all(record.get(field) not in (None, "") for field in required):
            normalized.append(record)

    return normalized


def write_ctd_cache(
    records: Iterable[dict[str, Any]],
    path: str | Path,
    *,
    source_url: str | None = None,
    updated_at: str | None = None,
) -> None:
    payload = {
        "updated_at": updated_at or datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source_url": source_url,
        "records": list(records),
    }

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _extract_records(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        return []

    for key in ("records", "data", "items", "deliverables", "contracts", "rows"):
        value = payload.get(key)
        if isinstance(value, list):
            flattened: list[Any] = []
            for item in value:
                if isinstance(item, dict):
                    nested = item.get("deliverables") or item.get("deliverableBasket") or item.get("securities")
                    if isinstance(nested, list):
                        for child in nested:
                            if isinstance(child, dict):
                                merged = {**item, **child}
                                merged.pop("deliverables", None)
                                merged.pop("deliverableBasket", None)
                                merged.pop("securities", None)
                                flattened.append(merged)
                    else:
                        flattened.append(item)
                else:
                    flattened.append(item)
            return flattened

    return [payload]


def _normalize_one_record(raw_record: dict[str, Any], *, source: str) -> dict[str, Any]:
    record: dict[str, Any] = {}

    for target_field, aliases in FIELD_ALIASES.items():
        value = _first_present(raw_record, aliases)
        if value is not None:
            record[target_field] = value

    record.setdefault("source", source)
    record["coupon_rate"] = _parse_rate(record.get("coupon_rate"))
    record["conversion_factor"] = _parse_float(record.get("conversion_factor"))

    if not record.get("futures_symbol") and record.get("contract_code"):
        record["futures_symbol"] = str(record["contract_code"])[:2]

    return record


def _first_present(raw_record: dict[str, Any], aliases: list[str]) -> Any | None:
    lower_map = {str(key).lower(): value for key, value in raw_record.items()}
    for alias in aliases:
        if alias in raw_record:
            return raw_record[alias]
        if alias.lower() in lower_map:
            return lower_map[alias.lower()]
    return None


def _parse_rate(value: Any) -> float | None:
    if value is None or value == "":
        return None

    if isinstance(value, str):
        text = value.strip().replace("%", "")
        parsed = float(text)
        # Treat values like 4.25 or "4.25%" as percent inputs.
        return parsed / 100 if abs(parsed) > 1 else parsed

    parsed = float(value)
    return parsed / 100 if abs(parsed) > 1 else parsed


def _parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(str(value).strip())
