"""Refresh normalized CTD cache from a configured CME/public source URL.

Usage examples:

    python scripts/update_cme_ctd.py --source-url "https://example.com/ctd.json"
    CME_CTD_SOURCE_URL="https://example.com/ctd.csv" python scripts/update_cme_ctd.py

The script intentionally requires a configured URL. CME Treasury Analytics is a
web tool and data access may depend on login, entitlement, or a changing internal
API. Do not commit credentials or licensed raw data.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_PATH))

from shiori_pricing_lab.data.cme_ctd_provider import normalize_ctd_records, read_raw_payload, write_ctd_cache


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", default=os.environ.get("CME_CTD_SOURCE_URL"))
    parser.add_argument("--output", default="data/cme_ctd_latest.json")
    parser.add_argument("--allow-empty", action="store_true", help="Exit successfully when no source URL is configured.")
    args = parser.parse_args()

    if not args.source_url:
        print("No CME_CTD_SOURCE_URL configured. Skipping CTD refresh.")
        return 0 if args.allow_empty else 2

    raw_path = _download_to_tempfile(args.source_url)
    raw_payload = read_raw_payload(raw_path)
    records = normalize_ctd_records(raw_payload, source="CME/configured-source")

    if not records:
        raise SystemExit("Source was fetched, but no usable CTD records were found.")

    output_path = PROJECT_ROOT / args.output
    write_ctd_cache(records, output_path, source_url=args.source_url)
    print(f"Wrote {len(records)} CTD record(s) to {output_path.relative_to(PROJECT_ROOT)}")
    return 0


def _download_to_tempfile(source_url: str) -> Path:
    suffix = ".csv" if source_url.lower().endswith(".csv") else ".json"

    if source_url.startswith("file://"):
        return Path(source_url.removeprefix("file://"))

    if not source_url.startswith(("http://", "https://")):
        local_path = Path(source_url)
        if local_path.exists():
            return local_path
        raise FileNotFoundError(source_url)

    with urllib.request.urlopen(source_url, timeout=30) as response:
        body = response.read()

    try:
        json.loads(body.decode("utf-8"))
        suffix = ".json"
    except json.JSONDecodeError:
        suffix = ".csv"

    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    handle.write(body)
    handle.close()
    return Path(handle.name)


if __name__ == "__main__":
    raise SystemExit(main())
