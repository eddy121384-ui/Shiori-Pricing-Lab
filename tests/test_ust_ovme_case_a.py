"""Structural and normalization checks for redacted UST OVME Case A."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[1]
_EVIDENCE = _ROOT / "docs/evidence/ust_ovme_case_a_redacted.json"
sys.path.insert(0, str(_ROOT / "tools"))
_SPEC = importlib.util.spec_from_file_location(
    "ust_case_a_reference_capture", _ROOT / "tools/ust_case_a_reference_capture.py"
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_case_a_manual_values_are_structured_and_replay_is_honestly_blocked() -> None:
    capture = json.loads(_EVIDENCE.read_text(encoding="utf-8"))
    assert capture["case"]["redacted_underlying_key"] == "UST_CASE_A_UNDERLYING_REDACTED_V1"
    assert capture["provenance"]["ovme_observation_timestamp"] == "2026-08-04T10:37:00+08:00"
    assert capture["terms_and_dates"]["expiry_timestamp"]["value"] == "2026-10-04T06:20:00+08:00"
    assert capture["terms_and_dates"]["option_settlement_date"]["source"] == "UNRESOLVED"
    assert capture["underlying_and_strike"]["underlying_clean_price_per_100"]["value"] == 98.578125
    assert capture["forward"]["explicit_clean_price_per_100"]["value"] == 98.515625
    assert capture["volatility"]["price_volatility_decimal_annual"]["value"] == 0.03942
    assert capture["discounting"]["shiori_option_discount_curve"]["value"] is None
    assert capture["shiori_run_binding"]["pricing_status"] == "BLOCKED"
    assert capture["shiori_run_binding"]["normalized_request_sha256"] is None


def test_case_a_keeps_three_premium_buckets_separate_and_unmapped() -> None:
    capture = json.loads(_EVIDENCE.read_text(encoding="utf-8"))
    buckets = {item["label"]: item for item in capture["premium"]["ovme_buckets"]}
    assert set(buckets) == {"Price (Unit)", "Price (%)", "Price (Total)"}
    assert buckets["Price (Unit)"]["display_verbatim"] is None
    assert buckets["Price (%)"]["normalized_value"] == 0.6159
    assert buckets["Price (Total)"]["normalized_value"] == 6.07
    assert all(item["mapping_status"].endswith("UNRESOLVED") for item in buckets.values())


def test_case_a_redaction_and_required_unresolved_fields_are_pinned() -> None:
    capture = json.loads(_EVIDENCE.read_text(encoding="utf-8"))
    assert not any(capture["redaction_check"].values())
    assert capture["coupon_schedule"]["coupon_count"] is None
    assert (
        capture["coupon_schedule"]["qualification"]
        == "CASE_A_NO_COUPON_BEFORE_EXPIRY_UNRESOLVED"
    )
    assert (
        capture["forward"]["repo_source_curve_security_adjustment"]["display_status"]
        == "NOT DISPLAYED"
    )
    curve_details = capture["discounting"][
        "curve_source_zero_representation_compounding_basis_interpolation_horizon"
    ]
    assert curve_details["display_status"] == "NOT DISPLAYED"


def test_case_a_normalizations_are_deterministic() -> None:
    assert _MODULE.normalize_treasury_fraction("98-18+") == 98.578125
    assert _MODULE.normalize_treasury_fraction("98-16+") == 98.515625
    assert _MODULE.normalize_display_percent("3.942%") == 0.03942
    with pytest.raises(ValueError):
        _MODULE.normalize_treasury_fraction("98-32+")
