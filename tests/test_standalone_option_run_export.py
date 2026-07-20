"""Tests for the deterministic current-run JSON/Markdown export helper (Issue #101).

The export helper is a pure function of the **existing** display dict already
returned by ``price_standalone_option_case`` / ``price_standalone_option_case_with_
benchmark`` -- it prices, compares, or calibrates nothing itself. Most tests below
therefore exercise it with small, hand-built synthetic display dicts (mirroring the
exact shape ``prepare_standalone_display`` / ``prepare_standalone_benchmark_display``
already produce) so they run without QuantLib; a handful of tests that need a
genuinely complete, real display (price-only SUCCESS, benchmark PASS/CALIBRATED,
NON_COMPARABLE, SOLVER_FAILED) drive the real bounded workflow and are marked
QuantLib-dependent, following this test suite's existing convention.

None of these fixtures are Bloomberg output, market evidence, or UAT -- every
synthetic value here is explicitly local-test-only.
"""

from __future__ import annotations

import copy
import json
from datetime import date, datetime
from pathlib import Path

import pytest

from shiori_pricing_lab.app.standalone_option_run_export import (
    render_standalone_run_as_json,
    render_standalone_run_as_markdown,
)
from shiori_pricing_lab.app.standalone_option_workbench import (
    price_standalone_option_case,
    price_standalone_option_case_with_benchmark,
)
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available

_QUANTLIB_AVAILABLE = is_quantlib_available()
_requires_quantlib = pytest.mark.skipif(
    not _QUANTLIB_AVAILABLE, reason="QuantLib is not installed in this environment"
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EXAMPLE_PATH = _REPO_ROOT / "examples" / "standalone_option_case.json"

_EXPECTED_PV = 2.2755055634196273
_EXPECTED_BLACK76_PV_PER_100 = 4.551011126839255


def _example_text() -> str:
    return _EXAMPLE_PATH.read_text(encoding="utf-8")


def _synthetic_price_only_display(**overrides) -> dict:
    """A minimal, hand-built display dict mirroring ``prepare_standalone_display``'s
    exact field shape -- test-local synthetic data only, not a real pricing run."""

    display = {
        "status": "SUCCESS",
        "method": "black76_forward_dirty_price_ovme_v1",
        "product_id": "TEST-PRODUCT-ID",
        "product_type": "BOND_OPTION",
        "valuation_date": "2026-07-01",
        "result_currency": "USD",
        "model_fair_premium_per_100": _EXPECTED_BLACK76_PV_PER_100,
        "total_notional_model_fair_premium": _EXPECTED_PV,
        "forward_clean_price_per_100": 101.3,
        "black76_pv_per_100": _EXPECTED_BLACK76_PV_PER_100,
        "effective_reporting_date_discount_factor": 0.9927830612383566,
        "time_to_expiry_year_fraction": 0.2465753424657534,
        "pv_scaling_formula": "pv = black76_pv_per_100 * notional / 100",
        "priced_component": "bond_option_leg",
        "priced_component_scope": "option_leg_only_not_full_structured_product",
        "excluded_components": ["deposit_leg", "principal_redemption", "physical_delivery"],
        "assumptions": {
            "price_volatility": 0.18,
            "notional": 50.0,
            "excluded_components": ["deposit_leg", "principal_redemption", "physical_delivery"],
        },
        "source_system": "TEST_LOCAL_SOURCE",
        "source_as_of": "2026-07-01T16:00:00Z",
        "retrieved_at": None,
        "snapshot_id": "TEST-SNAPSHOT-0001",
        "engine_name": "bli_standalone_bond_option_ovme_black76_engine",
        "engine_version": "1.0.0",
        "errors": [],
    }
    display.update(overrides)
    return display


# --- 1. Determinism ----------------------------------------------------------------


def test_identical_input_produces_identical_json_text():
    display = _synthetic_price_only_display()
    assert render_standalone_run_as_json(display) == render_standalone_run_as_json(display)


def test_identical_input_produces_identical_markdown_text():
    display = _synthetic_price_only_display()
    assert render_standalone_run_as_markdown(display) == render_standalone_run_as_markdown(
        display
    )


def test_json_ends_with_exactly_one_trailing_newline():
    text = render_standalone_run_as_json(_synthetic_price_only_display())
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_markdown_ends_with_exactly_one_trailing_newline():
    text = render_standalone_run_as_markdown(_synthetic_price_only_display())
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


# --- 2. JSON round-trips to the normalized display content --------------------------


def test_json_round_trips_to_original_display_content():
    display = _synthetic_price_only_display()
    loaded = json.loads(render_standalone_run_as_json(display))
    assert loaded == display


def test_json_preserves_none_values_as_null():
    display = _synthetic_price_only_display(retrieved_at=None)
    loaded = json.loads(render_standalone_run_as_json(display))
    assert loaded["retrieved_at"] is None


# --- 3. UTF-8 content preserved ------------------------------------------------------


def test_utf8_content_is_preserved_not_escaped():
    display = _synthetic_price_only_display(
        source_system="TEST_LOCAL_SOURCE_Ümläut_日本語"
    )
    text = render_standalone_run_as_json(display)
    assert "Ümläut" in text
    assert "日本語" in text
    assert "\\u" not in text  # ensure_ascii=False: no escaped-unicode sequences
    loaded = json.loads(text)
    assert loaded["source_system"] == display["source_system"]

    md = render_standalone_run_as_markdown(display)
    assert "Ümläut" in md
    assert "日本語" in md


# --- 4. Date/datetime values normalize deterministically -----------------------------


def test_date_and_datetime_values_normalize_to_isoformat():
    display = _synthetic_price_only_display()
    display["assumptions"] = {
        **display["assumptions"],
        "synthetic_date_value": date(2026, 7, 1),
        "synthetic_datetime_value": datetime(2026, 7, 1, 16, 0, 0),
    }
    text = render_standalone_run_as_json(display)
    loaded = json.loads(text)
    assert loaded["assumptions"]["synthetic_date_value"] == date(2026, 7, 1).isoformat()
    assert loaded["assumptions"]["synthetic_datetime_value"] == datetime(
        2026, 7, 1, 16, 0, 0
    ).isoformat()


# --- 5. Unsupported object types fail explicitly --------------------------------------


class _UnsupportedType:
    """A deliberately unsupported object type for the explicit-rejection test."""


def test_unsupported_object_type_raises_type_error():
    display = _synthetic_price_only_display()
    display["assumptions"] = {**display["assumptions"], "bad_field": _UnsupportedType()}
    with pytest.raises(TypeError):
        render_standalone_run_as_json(display)


def test_unsupported_object_type_is_not_silently_stringified():
    display = _synthetic_price_only_display()
    display["assumptions"] = {**display["assumptions"], "bad_field": {1, 2, 3}}  # a set
    with pytest.raises(TypeError):
        render_standalone_run_as_json(display)


# --- 6. Input display dict is not mutated ---------------------------------------------


def test_json_export_does_not_mutate_input():
    display = _synthetic_price_only_display()
    before = copy.deepcopy(display)
    render_standalone_run_as_json(display)
    assert display == before


def test_markdown_export_does_not_mutate_input():
    display = _synthetic_price_only_display()
    before = copy.deepcopy(display)
    render_standalone_run_as_markdown(display)
    assert display == before


# --- 7. Type boundary ------------------------------------------------------------------


def test_json_export_rejects_non_dict_input():
    with pytest.raises(TypeError):
        render_standalone_run_as_json(["not", "a", "dict"])  # type: ignore[arg-type]


def test_markdown_export_rejects_non_dict_input():
    with pytest.raises(TypeError):
        render_standalone_run_as_markdown("not a dict")  # type: ignore[arg-type]


# --- 8. Standalone-leg disclaimer and excluded components are present -----------------


def test_markdown_contains_standalone_leg_disclaimer_verbatim():
    md = render_standalone_run_as_markdown(_synthetic_price_only_display())
    assert (
        "Internal current-run evidence for the standalone bond-option leg only. "
        "Not a saved quote, replay contract, booking record, client termsheet, "
        "or full structured-product valuation." in md
    )


def test_markdown_contains_excluded_components_section():
    md = render_standalone_run_as_markdown(_synthetic_price_only_display())
    assert "## Excluded Components" in md
    assert "- deposit_leg" in md
    assert "- principal_redemption" in md
    assert "- physical_delivery" in md


# --- 9. "not available" for None, never a fabricated numeric replacement --------------


def test_markdown_distinguishes_none_from_genuine_zero():
    display = _synthetic_price_only_display()
    display["comparison"] = {
        "status": "NON_COMPARABLE",
        "reason": "NEAR_ZERO_BENCHMARK",
        "comparison_metric": "RELATIVE_RESIDUAL_PER_100",
        "active_quote_side": "MID",
        "pass_threshold": 0.02,
        "fail_threshold": 0.05,
        "near_zero_threshold_per_100": 0.01,
        "model_fair_premium_per_100": _EXPECTED_BLACK76_PV_PER_100,
        "model_total_premium": _EXPECTED_PV,
        "benchmark_premium_per_100": 0.0,
        "benchmark_total_premium": 0.0,
        "signed_residual_per_100": None,
        "absolute_residual_per_100": None,
        "relative_residual": None,
        "alignment_note": "benchmark premium is within the near-zero threshold",
    }
    md = render_standalone_run_as_markdown(display)
    # The genuine zero premium renders as a real number...
    assert "**Benchmark premium per 100:** 0.0" in md
    # ...while the structurally-unset residuals render as an honest caption,
    # never a fabricated "0" or "0.0".
    assert "**Signed residual per 100:** not available" in md
    assert "**Relative residual:** not available" in md


# --- 10. Real-run integration coverage (QuantLib-dependent) ---------------------------


@_requires_quantlib
def test_price_only_success_export_is_complete():
    _request, _result, display = price_standalone_option_case(_example_text())
    assert display["status"] == "SUCCESS"

    json_text = render_standalone_run_as_json(display)
    assert json.loads(json_text) == display

    md = render_standalone_run_as_markdown(display)
    assert "## Context" in md
    assert "## Pricing" in md
    assert "## Assumptions" in md
    assert "## Excluded Components" in md
    assert "## Errors" not in md  # no errors on a SUCCESS result
    assert "## Benchmark" not in md  # price-only: no benchmark section at all
    assert str(_EXPECTED_BLACK76_PV_PER_100) in md


@_requires_quantlib
def test_benchmark_pass_calibration_success_export_is_complete():
    benchmark = {
        "benchmark_id": "TEST_LOCAL_EXPORT_BENCHMARK_0001",
        "source_type": "BLOOMBERG",
        "source_system": "TEST_LOCAL_EXPORT_BENCHMARK_SOURCE",
        "source_as_of": "2026-07-01T16:00:00Z",
        "retrieved_at": "2026-07-01T16:05:00Z",
        "quote_side": "MID",
        "premium_per_100": _EXPECTED_BLACK76_PV_PER_100,
        "total_premium": _EXPECTED_PV,
        "currency": "USD",
        "product_id": "BONDOPT-SYNTHETIC-0001",
        "snapshot_id": "SANITIZED_SYNTHETIC_STANDALONE_SNAPSHOT_0001",
        "underlying_id": "XS0000000001",
        "source_reference": "TEST_LOCAL_EXPORT_BENCHMARK_REFERENCE_0001",
        "notes": "Test-local synthetic fixture for deterministic export testing only.",
    }
    _request, _result, _benchmark, comparison, calibration, display = (
        price_standalone_option_case_with_benchmark(
            _example_text(), json.dumps(benchmark), active_quote_side="MID"
        )
    )
    assert comparison.status.value == "PASS"
    assert calibration.status.value == "SUCCESS"

    json_text = render_standalone_run_as_json(display)
    assert json.loads(json_text) == display

    md = render_standalone_run_as_markdown(display)
    for heading in (
        "## Context",
        "## Pricing",
        "## Benchmark",
        "## Comparison",
        "## Calibration",
        "## Assumptions",
        "## Excluded Components",
        "## Solver Diagnostics",
    ):
        assert heading in md
    assert "## Errors" not in md
    assert "Comparison" in md and "PASS" in md
    assert "CALIBRATED" in md
    implied_vol_line = next(
        line for line in md.splitlines() if line.startswith("- **Implied PRICE_VOL:**")
    )
    assert "not available" not in implied_vol_line
    assert str(calibration.solver_result.implied_price_vol) in implied_vol_line


@_requires_quantlib
def test_pricing_failed_export_preserves_errors_and_no_premium():
    envelope = json.loads(_example_text())
    envelope["volatility_input"] = {
        **envelope["volatility_input"],
        "volatility_basis": "YIELD_VOL",
    }
    _request, _result, display = price_standalone_option_case(envelope)
    assert display["status"] == "FAILED"
    assert display["errors"]

    json_text = render_standalone_run_as_json(display)
    assert json.loads(json_text) == display

    md = render_standalone_run_as_markdown(display)
    assert "## Errors" in md
    assert "UNSUPPORTED_PRODUCT" in md
    assert "**Model fair premium per 100:** not available" in md
    assert "**Total notional model fair premium:** not available" in md
    # complete structured detail is preserved, not summarized away.
    assert "reasons" in md
    assert "product_id" in md


@_requires_quantlib
def test_non_comparable_export_preserves_reason_and_none_residuals():
    benchmark = {
        "benchmark_id": "TEST_LOCAL_EXPORT_BENCHMARK_0002",
        "source_type": "BLOOMBERG",
        "source_system": "TEST_LOCAL_EXPORT_BENCHMARK_SOURCE",
        "source_as_of": "2026-07-01T16:00:00Z",
        "retrieved_at": "2026-07-01T16:05:00Z",
        "quote_side": "MID",
        "premium_per_100": _EXPECTED_BLACK76_PV_PER_100,
        "total_premium": _EXPECTED_PV,
        "currency": "USD",
        "product_id": "OTHER-PRODUCT-ID-MISMATCH",
        "snapshot_id": "SANITIZED_SYNTHETIC_STANDALONE_SNAPSHOT_0001",
        "underlying_id": "XS0000000001",
        "source_reference": "TEST_LOCAL_EXPORT_BENCHMARK_REFERENCE_0002",
        "notes": "Test-local synthetic fixture for deterministic export testing only.",
    }
    _request, _result, _benchmark, comparison, _calibration, display = (
        price_standalone_option_case_with_benchmark(
            _example_text(), json.dumps(benchmark), active_quote_side="MID"
        )
    )
    assert comparison.status.value == "NON_COMPARABLE"
    assert comparison.reason.value == "PRODUCT_ID_MISMATCH"
    assert display["comparison"]["signed_residual_per_100"] is None

    json_text = render_standalone_run_as_json(display)
    loaded = json.loads(json_text)
    assert loaded["comparison"]["reason"] == "PRODUCT_ID_MISMATCH"
    assert loaded["comparison"]["signed_residual_per_100"] is None
    assert loaded["comparison"]["relative_residual"] is None

    md = render_standalone_run_as_markdown(display)
    assert "PRODUCT_ID_MISMATCH" in md
    assert "**Signed residual per 100:** not available" in md
    assert "**Relative residual:** not available" in md


@_requires_quantlib
def test_solver_failed_export_preserves_solver_reason_config_and_no_implied_vol():
    benchmark = {
        "benchmark_id": "TEST_LOCAL_EXPORT_BENCHMARK_0003",
        "source_type": "BLOOMBERG",
        "source_system": "TEST_LOCAL_EXPORT_BENCHMARK_SOURCE",
        "source_as_of": "2026-07-01T16:00:00Z",
        "retrieved_at": "2026-07-01T16:05:00Z",
        "quote_side": "MID",
        "premium_per_100": 0.0,
        "total_premium": 0.0,
        "currency": "USD",
        "product_id": "BONDOPT-SYNTHETIC-0001",
        "snapshot_id": "SANITIZED_SYNTHETIC_STANDALONE_SNAPSHOT_0001",
        "underlying_id": "XS0000000001",
        "source_reference": "TEST_LOCAL_EXPORT_BENCHMARK_REFERENCE_0003",
        "notes": "Test-local synthetic fixture for deterministic export testing only.",
    }
    _request, _result, _benchmark, _comparison, calibration, display = (
        price_standalone_option_case_with_benchmark(
            _example_text(), json.dumps(benchmark), active_quote_side="MID"
        )
    )
    assert calibration.status.value == "FAILED"
    assert calibration.reason.value == "SOLVER_FAILED"
    assert display["calibration"]["implied_price_vol"] is None

    json_text = render_standalone_run_as_json(display)
    loaded = json.loads(json_text)
    assert loaded["calibration"]["reason"] == "SOLVER_FAILED"
    assert loaded["calibration"]["implied_price_vol"] is None
    # Solver config/bounds are preserved even though the solver failed.
    assert loaded["calibration"]["lower_price_vol"] is not None
    assert loaded["calibration"]["upper_price_vol"] is not None
    assert loaded["calibration"]["max_iterations"] is not None

    md = render_standalone_run_as_markdown(display)
    assert "SOLVER_FAILED" in md
    assert "**Implied PRICE_VOL:** not available" in md
    assert "## Solver Diagnostics" in md
    assert "**Lower price vol:** 1e-06" in md
    assert "**Max iterations:** 100" in md
