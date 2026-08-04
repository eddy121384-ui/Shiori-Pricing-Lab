"""Structural checks for the redacted Issue #153 evidence template only."""

from __future__ import annotations

import json
from pathlib import Path

_TEMPLATE = (
    Path(__file__).parents[1] / "docs" / "evidence" / "ust_ovme_capture_template.json"
)


def test_ust_ovme_capture_template_has_replay_binding_fields_and_nulls_parse() -> None:
    capture = json.loads(_TEMPLATE.read_text(encoding="utf-8"))

    assert capture["terms_and_dates"]["issue_date"] is None
    assert capture["premium"]["shiori_model_fair_premium_per_100"] is None
    assert capture["premium"]["shiori_total_notional_model_fair_premium"] is None
    assert capture["premium"]["position_sign_applied_to_shiori_model_premium"] is False

    binding = capture["shiori_run_binding"]
    assert binding["request_contract_name"] == "BLIStandaloneBondOptionRequest"
    assert {
        "pricing_status",
        "product_id",
        "snapshot_id",
        "valuation_date",
        "pricing_timestamp",
        "market_data_source_system",
        "market_data_source_as_of",
        "retrieved_at",
        "pricing_engine_name",
        "pricing_engine_version",
        "pricing_method",
        "contract_code_git_commit_sha",
        "normalized_request_sha256",
        "current_run_export_sha256",
    } <= binding.keys()


def test_ust_ovme_capture_template_is_redacted_by_default() -> None:
    capture = json.loads(_TEMPLATE.read_text(encoding="utf-8"))

    assert capture["provenance"]["raw_material_committed"] is False
    assert not any(capture["redaction_check"].values())
