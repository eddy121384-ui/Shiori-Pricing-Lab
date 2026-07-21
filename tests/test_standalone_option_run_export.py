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
        # Issue #133 Slice A Greek fields, same shape as prepare_standalone_display.
        "forward_price_delta_per_100": 0.6821450795268977,
        "forward_price_gamma_per_100": 0.04413241269876543,
        "vega_per_vol_point_per_100": 0.1418766453210987,
        "theta_per_calendar_day_per_100": -0.0091827364554321,
        "position_forward_price_delta_total": 0.34107253976344885,
        "position_forward_price_gamma_total": 0.022066206349382715,
        "position_vega_per_vol_point_total": 0.07093832266054935,
        "position_theta_per_calendar_day_total": -0.00459136822771605,
        "position": "BUY",
        "position_multiplier": 1.0,
        "greeks_per_100_position_sign_applied": False,
        "greeks_position_total_sign_applied": True,
        "greeks_units": {
            "forward_price_delta": "premium per 100 per +1.00 forward clean price point",
            "vega": "premium per 100 per +0.01 absolute volatility",
        },
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


# --- 9b. Greeks section (Issue #133, Slice A) ----------------------------------------

_EXPORT_GREEK_PER_100_KEYS = (
    "forward_price_delta_per_100",
    "forward_price_gamma_per_100",
    "vega_per_vol_point_per_100",
    "theta_per_calendar_day_per_100",
)
_EXPORT_GREEK_POSITION_TOTAL_KEYS = (
    "position_forward_price_delta_total",
    "position_forward_price_gamma_total",
    "position_vega_per_vol_point_total",
    "position_theta_per_calendar_day_total",
)
_EXPORT_GREEK_KEYS = _EXPORT_GREEK_PER_100_KEYS + _EXPORT_GREEK_POSITION_TOTAL_KEYS


def test_markdown_greeks_section_states_every_unit_and_value_verbatim():
    display = _synthetic_price_only_display()
    md = render_standalone_run_as_markdown(display)

    assert "## Greeks" in md
    # Labels carry the unit, so no exported figure is ambiguous.
    assert "per +1.00 clean price point" in md
    assert "per +1.00 clean price point squared" in md
    assert "per +0.01 absolute volatility" in md
    assert "per +1 calendar day" in md
    # Values are verbatim -- full precision, never rounded to the UI's .6f.
    for key in _EXPORT_GREEK_KEYS:
        assert str(display[key]) in md


def test_markdown_greeks_section_separates_instrument_from_position_basis():
    display = _synthetic_price_only_display()
    md = render_standalone_run_as_markdown(display)

    assert "### Instrument analytics (per 100, no position sign)" in md
    assert "### Position risk (notional and BUY/SELL sign applied)" in md
    # Each subsection states, in words, whether the position sign is applied.
    assert "BUY/SELL position sign is NOT applied" in md
    assert "position multiplier (BUY = +1, SELL = -1)" in md

    instrument_block, position_block = (
        md.split("### Instrument analytics")[1].split("### Position risk")[0],
        md.split("### Position risk")[1],
    )
    for key in _EXPORT_GREEK_PER_100_KEYS:
        assert str(display[key]) in instrument_block
    for key in _EXPORT_GREEK_POSITION_TOTAL_KEYS:
        assert str(display[key]) in position_block
    # Every position-total label is position-qualified; no bare "total".
    for label in ("Position forward price delta total", "Position vega total"):
        assert f"- **{label}" in md
    assert "Position multiplier" in position_block


def test_json_export_carries_every_greek_key_verbatim():
    display = _synthetic_price_only_display()
    round_tripped = json.loads(render_standalone_run_as_json(display))

    for key in _EXPORT_GREEK_KEYS:
        assert round_tripped[key] == display[key]
    assert round_tripped["greeks_units"] == display["greeks_units"]
    assert round_tripped["position"] == "BUY"
    assert round_tripped["position_multiplier"] == 1.0
    assert round_tripped["greeks_per_100_position_sign_applied"] is False
    assert round_tripped["greeks_position_total_sign_applied"] is True


def test_markdown_greeks_section_renders_none_as_not_available_never_zero():
    # A FAILED run's display carries every Greek key as None; the export must
    # never substitute a fabricated 0.
    failed = _synthetic_price_only_display(
        status="FAILED",
        **{key: None for key in _EXPORT_GREEK_KEYS},
        greeks_units=None,
        position=None,
        position_multiplier=None,
        greeks_per_100_position_sign_applied=None,
        greeks_position_total_sign_applied=None,
    )
    md = render_standalone_run_as_markdown(failed)
    greeks_lines = md.split("## Greeks")[1].split("\n## ")[0].splitlines()
    value_lines = [line for line in greeks_lines if line.startswith("- **")]

    # 8 Greeks + position/multiplier/two sign flags + the units mapping.
    assert len(value_lines) == len(_EXPORT_GREEK_KEYS) + 5
    for line in value_lines:
        # Every value position reads "not available" -- no 0, no placeholder.
        assert line.split(":**")[1].strip() == "not available"


def test_markdown_greek_zero_is_not_reported_as_missing():
    # A genuine 0.0 Greek (e.g. an infinitesimal delta) is a real value.
    display = _synthetic_price_only_display(forward_price_delta_per_100=0.0)
    md = render_standalone_run_as_markdown(display)
    delta_line = next(
        line
        for line in md.splitlines()
        if line.startswith("- **Forward price delta per 100")
    )
    assert delta_line.endswith("0.0")


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
    assert "## Greeks" in md
    assert "## Assumptions" in md
    assert "## Excluded Components" in md
    # Every real Greek reaches the export at full precision.
    for key in _EXPORT_GREEK_KEYS:
        assert str(display[key]) in md
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


# ==================================================================================
# Codex P2 review of PR #127: nested structured Markdown rendering (errors[*].detail,
# nested assumptions values) must use deterministic bullets, never Python repr, and
# must not conflate a genuine falsy value (0, 0.0, False, "") with a missing one.
# ==================================================================================


def _display_with_errors(detail: dict) -> dict:
    display = _synthetic_price_only_display(status="FAILED")
    display["errors"] = [
        {"code": "UNSUPPORTED_PRODUCT", "message": "synthetic rejection", "detail": detail}
    ]
    return display


# --- 11. reasons list renders as nested bullets, not Python repr -------------------


def test_error_detail_reasons_list_renders_as_nested_bullets_not_repr():
    display = _display_with_errors({"product_id": "TEST-PRODUCT-ID", "reasons": ["A", "B"]})
    md = render_standalone_run_as_markdown(display)

    assert "['A', 'B']" not in md
    assert "reasons: ['A', 'B']" not in md
    assert "- **reasons:**" in md
    lines = md.splitlines()
    reasons_index = lines.index(next(line for line in lines if "**reasons:**" in line))
    assert lines[reasons_index + 1].strip() == "- A"
    assert lines[reasons_index + 2].strip() == "- B"


# --- 12. Nested dict/list/tuple structure preserved deterministically --------------


def test_nested_dict_and_list_structure_preserved_deterministically():
    display = _display_with_errors(
        {
            "outer_list": ["first", "second"],
            "outer_dict": {"inner_key": "inner_value", "inner_list": [1, 2, 3]},
            "outer_tuple": ("x", "y"),
        }
    )
    md = render_standalone_run_as_markdown(display)

    assert "- **outer_list:**" in md
    assert "        - first" in md
    assert "        - second" in md
    assert "- **outer_dict:**" in md
    assert "        - **inner_key:** inner_value" in md
    assert "        - **inner_list:**" in md
    assert "            - 1" in md
    assert "            - 2" in md
    assert "            - 3" in md
    assert "- **outer_tuple:**" in md
    assert "        - x" in md
    assert "        - y" in md


# --- 13. Nested None renders as "not available" ------------------------------------


def test_nested_none_renders_as_not_available():
    display = _display_with_errors({"maybe_reason": None, "reasons": ["A", None, "C"]})
    md = render_standalone_run_as_markdown(display)

    assert "- **maybe_reason:** not available" in md
    lines = md.splitlines()
    reasons_index = lines.index(next(line for line in lines if "**reasons:**" in line))
    assert lines[reasons_index + 1].strip() == "- A"
    assert lines[reasons_index + 2].strip() == "- not available"
    assert lines[reasons_index + 3].strip() == "- C"


# --- 14. Genuine falsy values stay distinct from "not available" -------------------


def test_genuine_falsy_nested_values_are_not_replaced():
    display = _display_with_errors(
        {
            "zero_int": 0,
            "zero_float": 0.0,
            "false_flag": False,
            "empty_string": "",
            "none_value": None,
        }
    )
    md = render_standalone_run_as_markdown(display)

    assert "- **zero_int:** 0" in md
    assert "- **zero_float:** 0.0" in md
    assert "- **false_flag:** False" in md
    assert "- **empty_string:** " in md
    assert "- **none_value:** not available" in md
    # None must never be rendered as any of the genuine falsy values above.
    assert "- **none_value:** 0" not in md
    assert "- **none_value:** False" not in md


def test_genuine_falsy_values_in_nested_list_are_not_replaced():
    display = _display_with_errors({"flags": [0, 0.0, False, "", None]})
    md = render_standalone_run_as_markdown(display)
    lines = md.splitlines()
    flags_index = lines.index(next(line for line in lines if "**flags:**" in line))
    assert lines[flags_index + 1].strip() == "- 0"
    assert lines[flags_index + 2].strip() == "- 0.0"
    assert lines[flags_index + 3].strip() == "- False"
    assert lines[flags_index + 4].strip() == "-"  # empty string bullet
    assert lines[flags_index + 5].strip() == "- not available"


# --- 15. Unsupported nested object types raise TypeError explicitly ----------------


def test_unsupported_nested_object_in_error_detail_raises_type_error():
    display = _display_with_errors({"bad_field": _UnsupportedType()})
    with pytest.raises(TypeError):
        render_standalone_run_as_markdown(display)


def test_unsupported_nested_object_inside_list_raises_type_error():
    display = _display_with_errors({"reasons": ["A", _UnsupportedType()]})
    with pytest.raises(TypeError):
        render_standalone_run_as_markdown(display)


def test_unsupported_nested_object_inside_dict_value_raises_type_error():
    display = _display_with_errors({"nested": {"bad_field": {1, 2, 3}}})
    with pytest.raises(TypeError):
        render_standalone_run_as_markdown(display)


# --- 16. Determinism and no-mutation hold for nested structured values -------------


def test_nested_structured_markdown_is_deterministic():
    display = _display_with_errors({"product_id": "TEST-PRODUCT-ID", "reasons": ["A", "B"]})
    assert render_standalone_run_as_markdown(display) == render_standalone_run_as_markdown(
        display
    )


def test_nested_structured_export_does_not_mutate_input():
    display = _display_with_errors({"product_id": "TEST-PRODUCT-ID", "reasons": ["A", "B"]})
    before = copy.deepcopy(display)
    render_standalone_run_as_markdown(display)
    assert display == before


# --- 17. A genuine reachable pricing FAILED workflow exports cleanly ---------------


def test_real_pricing_failed_workflow_with_reasons_list_exports_cleanly():
    envelope = json.loads(_example_text())
    envelope["volatility_input"] = {
        **envelope["volatility_input"],
        "volatility_basis": "YIELD_VOL",
    }
    _request, _result, display = price_standalone_option_case(envelope)
    assert display["status"] == "FAILED"
    assert isinstance(display["errors"][0]["detail"]["reasons"], list)

    md = render_standalone_run_as_markdown(display)
    assert "['" not in md  # no Python list repr anywhere in the document
    assert "- **reasons:**" in md
    lines = md.splitlines()
    reasons_index = lines.index(next(line for line in lines if "**reasons:**" in line))
    assert lines[reasons_index + 1].strip().startswith("- ")
    assert "not available" not in lines[reasons_index + 1]

    # JSON export is unaffected by this Markdown-only fix.
    json_text = render_standalone_run_as_json(display)
    assert json.loads(json_text) == display


# --- 18. Issue #6: live Bloomberg quote provenance export, acquisition-time
#     contract (issue #6 comment 5028876767, PR #129 comment 5028878866) -----------


def _live_bloomberg_quote_fixture(**overrides) -> dict:
    fixture = {
        "security": "91282CQX Govt",
        "verified_isin": "US91282CQX29",
        "source_system": "BLOOMBERG_DAPI",
        "quote_side": "MID",
        "currency": "USD",
        "clean_price_per_100": 99.222656,
        "accrued_interest_per_100": 0.235394,
        "acquired_at": "2026-07-01T16:05:00+00:00",
        "timestamp_basis": "SHIORI_ACQUISITION_TIME",
        "bloomberg_quote_observation_time": None,
        "case_as_of_timestamp": "2026-07-01T16:00:00Z",
        "refreshed_scope": "BOND_QUOTE_ONLY",
        "other_market_inputs": "CASE_JSON_UNCHANGED",
    }
    fixture.update(overrides)
    return fixture


def test_json_export_includes_live_bloomberg_quote_section_verbatim():
    live_quote = _live_bloomberg_quote_fixture()
    display = _synthetic_price_only_display(live_bloomberg_quote=live_quote)

    json_text = render_standalone_run_as_json(display)

    assert json.loads(json_text)["live_bloomberg_quote"] == live_quote


def test_json_export_has_no_live_source_as_of_field():
    live_quote = _live_bloomberg_quote_fixture()
    display = _synthetic_price_only_display(live_bloomberg_quote=live_quote)

    json_text = render_standalone_run_as_json(display)

    assert "source_as_of" not in json.loads(json_text)["live_bloomberg_quote"]


def test_markdown_export_includes_conditional_live_bloomberg_quote_section():
    live_quote = _live_bloomberg_quote_fixture()
    display = _synthetic_price_only_display(live_bloomberg_quote=live_quote)

    md = render_standalone_run_as_markdown(display)

    assert "## Live Bloomberg Quote" in md
    assert f"- **Security:** {live_quote['security']}" in md
    assert f"- **Verified ISIN:** {live_quote['verified_isin']}" in md
    assert f"- **Source system:** {live_quote['source_system']}" in md
    assert f"- **Quote side:** {live_quote['quote_side']}" in md
    assert f"- **Currency:** {live_quote['currency']}" in md
    assert f"- **Clean price per 100:** {live_quote['clean_price_per_100']}" in md
    assert f"- **Accrued interest per 100:** {live_quote['accrued_interest_per_100']}" in md
    assert f"- **Acquired at:** {live_quote['acquired_at']}" in md
    assert f"- **Timestamp basis:** {live_quote['timestamp_basis']}" in md
    assert "- **Bloomberg quote observation time:** not available" in md
    assert f"- **Case as-of timestamp:** {live_quote['case_as_of_timestamp']}" in md
    assert f"- **Refreshed scope:** {live_quote['refreshed_scope']}" in md
    assert f"- **Other market inputs:** {live_quote['other_market_inputs']}" in md
    assert "- **Source as-of:**" not in md.split("## Live Bloomberg Quote")[1].split("##")[0]
    # Distinct from, and placed after, the Pricing section (model fair premium).
    assert md.index("## Pricing") < md.index("## Live Bloomberg Quote")


def test_markdown_export_live_bloomberg_quote_section_states_disclaimer():
    live_quote = _live_bloomberg_quote_fixture()
    display = _synthetic_price_only_display(live_bloomberg_quote=live_quote)

    md = render_standalone_run_as_markdown(display)

    section = md.split("## Live Bloomberg Quote")[1].split("##")[0]
    assert "quote-observation time is not provided" in section
    assert "Acquired at is when Shiori received" in section
    assert "Only the bond quote was refreshed" in section
    assert "mixed-provenance" in section


def test_markdown_export_omits_live_bloomberg_quote_section_when_absent():
    display = _synthetic_price_only_display()
    assert "live_bloomberg_quote" not in display

    md = render_standalone_run_as_markdown(display)

    assert "## Live Bloomberg Quote" not in md
    assert "Live Bloomberg" not in md


def test_live_bloomberg_quote_observation_time_none_renders_not_available():
    live_quote = _live_bloomberg_quote_fixture()
    display = _synthetic_price_only_display(live_bloomberg_quote=live_quote)

    md = render_standalone_run_as_markdown(display)

    assert "- **Bloomberg quote observation time:** not available" in md


def test_live_bloomberg_quote_values_are_not_rounded_or_recomputed():
    live_quote = _live_bloomberg_quote_fixture(clean_price_per_100=99.2226561234567)
    display = _synthetic_price_only_display(live_bloomberg_quote=live_quote)

    md = render_standalone_run_as_markdown(display)

    assert "- **Clean price per 100:** 99.2226561234567" in md


def test_live_bloomberg_quote_export_does_not_mutate_input():
    display = _synthetic_price_only_display(live_bloomberg_quote=_live_bloomberg_quote_fixture())
    before = copy.deepcopy(display)

    render_standalone_run_as_json(display)
    render_standalone_run_as_markdown(display)

    assert display == before


def test_manual_mode_display_without_live_quote_key_export_unchanged():
    # Manual (Case JSON) mode never produces a live_bloomberg_quote key --
    # export output must be identical whether or not this feature exists.
    display = _synthetic_price_only_display()

    json_text = render_standalone_run_as_json(display)
    md = render_standalone_run_as_markdown(display)

    assert "live_bloomberg_quote" not in json.loads(json_text)
    assert "Live Bloomberg" not in md
