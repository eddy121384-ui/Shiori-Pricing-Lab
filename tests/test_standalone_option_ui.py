"""Tests for the standalone bond-option trader workbench UI (Issue #97, PR B;
Issue #125 benchmark comparison / implied PRICE_VOL extension; Issue #101
current-run export).

Isolation strategy (see the full-suite interference note below):

- **Page wiring** is exercised with ``AppTest.from_function`` targeting the
  page directly, never by re-executing the whole ``streamlit_app.py`` (which
  would pull in the unrelated ``bli_mvp`` demo-fixture rebuild).
- **Render correctness** (SUCCESS metrics, FAILED errors, ``retrieved_at``
  separation) is exercised by rendering **real display dicts built once at
  import time** (before any test body runs) through
  ``AppTest.from_function`` with ``kwargs``. Rendering a plain display dict
  does no BLI construction, so these assertions are strong *and* immune to
  the process-state hazard described below.
- **Navigation** is verified with a source assertion on ``streamlit_app.py``.

Full-suite interference (root cause, not fixed here): the import-isolation
suites ``test_products`` / ``test_products_ccs_fxswap`` / ``test_pricing_engine``
call ``del sys.modules[...]`` for ``products``/``data``/``app`` prefixes to
assert layering, but never delete ``reference_data`` and never restore
``sys.modules``. That leaves ``reference_data`` cached while ``products`` is
rebuilt, producing two distinct ``Currency`` enum objects; ``BLIMVPInputBundle``
/ ``BLIStandaloneBondOptionRequest`` compare currency with ``is``, so any
*fresh* construction mixing the two raises. These UI tests avoid triggering a
fresh construction after that state by building their pricing inputs at import
time and only re-rendering them. The underlying leak is an existing-test /
schema concern, reported separately; nothing here modifies it.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from shiori_pricing_lab.app import standalone_option_ui as ui_module
from shiori_pricing_lab.app.standalone_option_run_export import (
    render_standalone_run_as_json,
    render_standalone_run_as_markdown,
)
from shiori_pricing_lab.app.standalone_option_ui import (
    _BOND_QUOTE_SOURCE_BLOOMBERG,
    _BOND_QUOTE_SOURCE_CASE_JSON,
    _MODE_PRICE_AND_BENCHMARK,
    _MODE_PRICE_ONLY,
    _decode_uploaded_json_text,
    _load_example_case_text,
    _retrieved_at_or_none,
    render_standalone_option_workbench_page,
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
_APP_PATH = _REPO_ROOT / "src" / "shiori_pricing_lab" / "app" / "streamlit_app.py"
_EXAMPLE_PATH = _REPO_ROOT / "examples" / "standalone_option_case.json"

_EXPECTED_PV = 2.2755055634196273
_EXPECTED_BLACK76_PV_PER_100 = 4.551011126839255
_RETRIEVED_AT = "2026-07-11T09:00:00Z"
_SOURCE_AS_OF = "2026-07-01T16:00:00Z"


def _yield_vol_case_text() -> str:
    envelope = json.loads(_EXAMPLE_PATH.read_text(encoding="utf-8"))
    envelope["volatility_input"] = {
        **envelope["volatility_input"],
        "volatility_basis": "YIELD_VOL",
    }
    return json.dumps(envelope)


# Real display contexts, built at import time (before any test body runs, so the
# module graph is consistent). Rendering these does no BLI construction.
# A YIELD_VOL case is a guard rejection -> FAILED, and needs no QuantLib.
_FAILED_DISPLAY = price_standalone_option_case(
    _yield_vol_case_text(), retrieved_at=_RETRIEVED_AT
)[2]
_FAILED_DISPLAY_NO_RETRIEVED = price_standalone_option_case(_yield_vol_case_text())[2]
_SUCCESS_DISPLAY = (
    price_standalone_option_case(_load_example_case_text(), retrieved_at=_RETRIEVED_AT)[2]
    if _QUANTLIB_AVAILABLE
    else None
)


# --- Issue #125: benchmark-mode display fixtures, built at import time -------------
# Same isolation rationale as _SUCCESS_DISPLAY above -- built once, before any test
# body runs, using the real bounded workflow (price_standalone_option_case_with_
# benchmark). Rendering these does no further BLI construction.
#
# Codex P1 review of PR #126: there is no bundled benchmark example under
# examples/ (BLIBenchmarkQuote.source_type only accepts BLOOMBERG/VENDOR, and
# a bundled file claiming BLOOMBERG for an engineered synthetic premium would
# misrepresent the workbench as Bloomberg-validated). This dict is test-local
# only, never read by production/UI code, and is explicitly not a claim of
# Bloomberg validation or market evidence -- its premium_per_100 is set to
# the pricing example's own model fair premium purely so the render tests
# below get deterministic, pinned PASS/CALIBRATED output.


def _benchmark_envelope(**overrides) -> dict:
    envelope = {
        "benchmark_id": "TEST_LOCAL_SYNTHETIC_BENCHMARK_0001",
        "source_type": "BLOOMBERG",
        "source_system": "TEST_LOCAL_SYNTHETIC_BENCHMARK_SOURCE",
        "source_as_of": _SOURCE_AS_OF,
        "retrieved_at": "2026-07-01T16:05:00Z",
        "quote_side": "MID",
        "premium_per_100": _EXPECTED_BLACK76_PV_PER_100,
        "total_premium": _EXPECTED_PV,
        "currency": "USD",
        "product_id": "BONDOPT-SYNTHETIC-0001",
        "snapshot_id": "SANITIZED_SYNTHETIC_STANDALONE_SNAPSHOT_0001",
        "underlying_id": "XS0000000001",
        "source_reference": "TEST_LOCAL_SYNTHETIC_BENCHMARK_REFERENCE_0001",
        "notes": (
            "Test-local synthetic fixture for deterministic testing only. Not a "
            "Bloomberg or real-market observation, and never exposed to a user."
        ),
    }
    envelope.update(overrides)
    return envelope


_BENCHMARK_PASS_DISPLAY = (
    price_standalone_option_case_with_benchmark(
        _load_example_case_text(), json.dumps(_benchmark_envelope()), active_quote_side="MID"
    )[5]
    if _QUANTLIB_AVAILABLE
    else None
)
_BENCHMARK_NON_COMPARABLE_DISPLAY = (
    price_standalone_option_case_with_benchmark(
        _load_example_case_text(),
        json.dumps(_benchmark_envelope(product_id="OTHER-PRODUCT-ID")),
        active_quote_side="MID",
    )[5]
    if _QUANTLIB_AVAILABLE
    else None
)
_BENCHMARK_SOLVER_FAILED_DISPLAY = (
    price_standalone_option_case_with_benchmark(
        _load_example_case_text(),
        json.dumps(_benchmark_envelope(premium_per_100=0.0, total_premium=0.0)),
        active_quote_side="MID",
    )[5]
    if _QUANTLIB_AVAILABLE
    else None
)


def _render_display_script(display: dict) -> None:
    # Self-contained AppTest.from_function entry point: renders a prepared
    # display dict via the real page render helpers, matching the real
    # page's own order (pricing, then export). No BLI construction.
    from shiori_pricing_lab.app.standalone_option_ui import (
        _render_export_section,
        _render_pricing_result,
    )

    _render_pricing_result(display)
    _render_export_section(display)


def _render_page_script() -> None:
    # Self-contained AppTest.from_function entry point for the full page.
    from shiori_pricing_lab.app.standalone_option_ui import (
        render_standalone_option_workbench_page,
    )

    render_standalone_option_workbench_page()


def _render_benchmark_display_script(display: dict) -> None:
    # Self-contained AppTest.from_function entry point mirroring
    # _render_display_script, but also rendering the benchmark/comparison/
    # calibration sections, then export -- the real page's exact order.
    # No BLI construction.
    from shiori_pricing_lab.app.standalone_option_ui import (
        _render_benchmark_result,
        _render_export_section,
        _render_pricing_result,
    )

    _render_pricing_result(display)
    _render_benchmark_result(display)
    _render_export_section(display)


def _run_render(display: dict) -> AppTest:
    at = AppTest.from_function(
        _render_display_script, kwargs={"display": display}, default_timeout=60
    )
    at.run()
    return at


def _run_benchmark_render(display: dict) -> AppTest:
    at = AppTest.from_function(
        _render_benchmark_display_script, kwargs={"display": display}, default_timeout=60
    )
    at.run()
    return at


def _run_page() -> AppTest:
    at = AppTest.from_function(_render_page_script, default_timeout=60)
    at.run()
    return at


def _set_case_json(at: AppTest, text: str) -> None:
    text_area = next(t for t in at.text_area if t.label == "Standalone option case JSON")
    text_area.set_value(text).run()


def _set_mode(at: AppTest, mode: str) -> None:
    radio = next(r for r in at.radio if r.label == "Mode")
    radio.set_value(mode).run()


def _set_bond_quote_source(at: AppTest, source: str) -> None:
    radio = next(r for r in at.radio if r.label == "Bond quote source")
    radio.set_value(source).run()


def _press_price(at: AppTest) -> None:
    next(b for b in at.button if b.label == "Price standalone option").click().run()


# --- Issue #6: live Bloomberg bond-quote source fixtures, acquisition-time
# contract (issue #6 comment 5028876767, PR #129 comment 5028878866) --------
#
# Same isolation strategy as _SUCCESS_DISPLAY/_BENCHMARK_PASS_DISPLAY above:
# render-only tests use a plain dict (no BLI construction at all); the one
# benchmark-mode wiring test that needs a fully render-complete display
# reuses the real _BENCHMARK_PASS_DISPLAY built at import time, only adding
# the live_bloomberg_quote section a real Bloomberg call would have produced.

_ACQUIRED_AT = "2026-07-01T16:05:00+00:00"
_FAKE_LIVE_BLOOMBERG_QUOTE_DISPLAY = {
    "security": "91282CQX Govt",
    "verified_isin": "XS0000000001",
    "source_system": "BLOOMBERG_DAPI",
    "quote_side": "MID",
    "currency": "USD",
    "clean_price_per_100": 99.5,
    "accrued_interest_per_100": 0.31,
    "acquired_at": _ACQUIRED_AT,
    "timestamp_basis": "SHIORI_ACQUISITION_TIME",
    "bloomberg_quote_observation_time": None,
    "case_as_of_timestamp": _SOURCE_AS_OF,
    "refreshed_scope": "BOND_QUOTE_ONLY",
    "other_market_inputs": "CASE_JSON_UNCHANGED",
}
_FAKE_BLOOMBERG_PRICE_ONLY_DISPLAY = {
    **_FAILED_DISPLAY,
    "live_bloomberg_quote": _FAKE_LIVE_BLOOMBERG_QUOTE_DISPLAY,
}
_FAKE_BLOOMBERG_BENCHMARK_DISPLAY = (
    {**_BENCHMARK_PASS_DISPLAY, "live_bloomberg_quote": _FAKE_LIVE_BLOOMBERG_QUOTE_DISPLAY}
    if _QUANTLIB_AVAILABLE
    else None
)


def _render_bloomberg_display_script(display: dict) -> None:
    from shiori_pricing_lab.app.standalone_option_ui import (
        _render_export_section,
        _render_live_bloomberg_quote,
        _render_pricing_result,
    )

    _render_pricing_result(display)
    _render_live_bloomberg_quote(display)
    _render_export_section(display)


def _run_bloomberg_render(display: dict) -> AppTest:
    at = AppTest.from_function(
        _render_bloomberg_display_script, kwargs={"display": display}, default_timeout=60
    )
    at.run()
    return at


def _fill_bloomberg_inputs(at: AppTest, *, security: str, side: str) -> None:
    next(t for t in at.text_input if t.label == "Bloomberg security").set_value(security).run()
    next(s for s in at.selectbox if s.label == "Quote side").set_value(side).run()


def _press_bloomberg_refresh(at: AppTest) -> None:
    next(
        b for b in at.button if b.label == "Refresh Bloomberg quote and price"
    ).click().run()


# --- 1. Module + helpers ------------------------------------------------------


def test_module_imports_cleanly():
    assert callable(render_standalone_option_workbench_page)
    assert callable(_load_example_case_text)
    assert callable(_retrieved_at_or_none)
    assert callable(_decode_uploaded_json_text)


def test_example_case_text_loaded_from_approved_json_file():
    text = _load_example_case_text()
    assert text == _EXAMPLE_PATH.read_text(encoding="utf-8")
    envelope = json.loads(text)
    assert "bond_option" in envelope
    assert "bond_reference_data_universe" in envelope


def test_retrieved_at_or_none_maps_empty_to_none_and_passes_verbatim():
    assert _retrieved_at_or_none("") is None
    assert _retrieved_at_or_none(None) is None
    assert _retrieved_at_or_none("2026-07-11T09:00:00Z") == "2026-07-11T09:00:00Z"
    # No normalization: whitespace is non-empty and passes through verbatim.
    assert _retrieved_at_or_none("  ") == "  "


def test_decode_uploaded_json_text_strict_utf8():
    assert _decode_uploaded_json_text(b'{"a": 1}') == '{"a": 1}'
    with pytest.raises(UnicodeDecodeError):
        _decode_uploaded_json_text(b"\xff\xfe invalid utf-8")


# --- 2. SUCCESS render: premium per-100 vs total notional (separate) ---------


@_requires_quantlib
def test_success_render_shows_separate_premium_metrics():
    at = _run_render(_SUCCESS_DISPLAY)
    assert not at.exception
    assert any("Pricing SUCCESS" in s.value for s in at.success)

    metrics = {m.label: m.value for m in at.metric}
    assert "Model fair premium per 100" in metrics
    assert "Total notional model fair premium" in metrics
    assert float(metrics["Model fair premium per 100"]) == pytest.approx(
        _EXPECTED_BLACK76_PV_PER_100, abs=1e-6
    )
    assert float(metrics["Total notional model fair premium"]) == pytest.approx(
        _EXPECTED_PV, abs=1e-6
    )
    # Per-100 and total are unmistakably separate, different values.
    assert metrics["Model fair premium per 100"] != metrics["Total notional model fair premium"]
    # Also renders the required intermediates.
    for label in (
        "Forward clean price per 100",
        "Black-76 PV per 100",
        "Effective reporting-date discount factor",
        "Time to expiry (years)",
    ):
        assert label in metrics


# --- 3. FAILED render: no premium, structured error detail preserved ---------


def test_failed_render_shows_no_premium_and_preserves_error_detail():
    at = _run_render(_FAILED_DISPLAY)
    assert not at.exception

    assert any("Pricing FAILED" in e.value for e in at.error)
    assert len(at.metric) == 0  # no premium/intermediate metrics on a failure
    assert any("UNSUPPORTED_PRODUCT" in m.value for m in at.markdown)

    json_blocks = [json.loads(j.value) for j in at.json]
    context = json_blocks[0]
    assert context["status"] == "FAILED"
    detail_blocks = json_blocks[1:]
    assert any("product_id" in block and "reasons" in block for block in detail_blocks)


# --- 4. retrieved_at stays separate from source-as-of ------------------------


def test_retrieved_at_supplied_is_separate_from_source_as_of():
    at = _run_render(_FAILED_DISPLAY)
    context = json.loads(at.json[0].value)
    assert context["retrieved_at"] == _RETRIEVED_AT
    assert context["source_as_of"] == _SOURCE_AS_OF
    assert context["retrieved_at"] != context["source_as_of"]


def test_retrieved_at_empty_maps_to_none_in_context():
    at = _run_render(_FAILED_DISPLAY_NO_RETRIEVED)
    context = json.loads(at.json[0].value)
    assert context["retrieved_at"] is None
    assert context["source_as_of"] == _SOURCE_AS_OF


# --- 5. Full-page wiring: button triggers the workflow (malformed input) ------


def test_malformed_json_renders_exception_and_no_metrics():
    # Full page: proves the button press routes to price_standalone_option_case
    # and that a raised local-input exception is rendered without pricing.
    at = _run_page()
    _set_case_json(at, "{ this is not valid json ")
    _press_price(at)

    assert not at.exception  # handled at the UI boundary, not raised
    assert len(at.error) >= 1
    assert len(at.metric) == 0


# --- 6. Navigation wiring (source assertion; see module docstring) -----------


def test_streamlit_app_wires_new_page_and_keeps_existing():
    source = _APP_PATH.read_text(encoding="utf-8")

    # New page label routed to the new render function.
    assert '"Standalone Bond Option Workbench"' in source
    assert "render_standalone_option_workbench_page" in source

    # Existing two pages and their render functions are unchanged/present.
    assert '"Rates Curve Demo"' in source
    assert '"Bond Option (BLI MVP)"' in source
    assert "render_rates_curve_demo_page()" in source
    assert "render_bli_mvp_page()" in source


# --- 7. Source-level guarantees ----------------------------------------------


def test_ui_calls_only_headless_workflow_for_execution():
    source = inspect.getsource(ui_module)
    assert "price_standalone_option_case(" in source
    assert "price_standalone_option_case_with_benchmark(" in source
    # No shortcut around the builder / direct request construction.
    assert "BLIStandaloneBondOptionRequest(" not in source
    assert "build_bli_standalone_option_request" not in source
    # No direct pricing/comparison/calibration/resolver/solver/Black-76/
    # curve/vol/provider/QuantLib imports or calls (Issue #125).
    for forbidden in (
        "price_bli_mvp_standalone_option",
        "bli_pricing_engine",
        "required_input_guard",
        "bli_curve_discount_factor",
        "bli_black76",
        "bli_forward_clean_price",
        "quantlib",
        "providers",
        "compare_bli_benchmark(",
        "calibrate_bli_implied_price_vol(",
        "bli_benchmark_comparison",
        "bli_implied_price_vol_calibration",
        "bli_implied_price_vol_solver",
        "bli_standalone_option_pricing_inputs",
    ):
        assert forbidden not in source, f"unexpected reference to {forbidden!r}"


def test_ui_upload_no_file_does_not_fall_back():
    source = inspect.getsource(ui_module)
    # An explicit no-file message exists, and the upload branch decodes the
    # uploaded file (never implicitly reusing the textarea/example).
    assert "No file uploaded" in source
    assert "case_text = _decode_uploaded_json_text(" in source
    assert "case_text = textarea_text" in source


def test_ui_has_no_pricing_math_provider_or_system_clock():
    source = inspect.getsource(ui_module)
    for forbidden in (
        "datetime.now(",
        "date.today(",
        "utcnow(",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "read_csv",
        "pandas",
        "numpy",
        "math.exp",
        "math.log",
        "norm.cdf",
    ):
        assert forbidden not in source, f"unexpected reference to {forbidden!r}"


def test_ui_has_no_broad_except_and_no_client_quote_label():
    source = inspect.getsource(ui_module)
    # Scan the code body only: the module docstring legitimately states the
    # "never labeled a client quote" prohibition, which must not count as a
    # rendered client-quote label.
    body = source.replace(ui_module.__doc__ or "", "")
    assert "except Exception" not in body
    assert "client quote" not in body.lower()
    assert "client_quote" not in body.lower()
    # Only the four expected local-input exceptions are caught.
    assert "json.JSONDecodeError" in body
    assert "UnicodeDecodeError" in body


def test_quote_side_selectbox_has_no_default_selection():
    # Issue #125: st.selectbox(..., index=None) is the only way to genuinely
    # start with no selection -- never a silently-chosen MID or first member.
    source = inspect.getsource(ui_module)
    assert "index=None" in source


# ==================================================================================
# Issue #125: benchmark comparison / implied PRICE_VOL orchestration UI.
# ==================================================================================

# --- 8. Mode selector: two explicit modes, default preserves existing behavior ----


def test_mode_selector_offers_price_only_and_benchmark_modes_with_price_only_default():
    at = _run_page()
    mode_radio = next(r for r in at.radio if r.label == "Mode")
    assert list(mode_radio.options) == [_MODE_PRICE_ONLY, _MODE_PRICE_AND_BENCHMARK]
    assert mode_radio.value == _MODE_PRICE_ONLY


def test_price_only_mode_never_renders_benchmark_sections():
    # Malformed pricing JSON exits before pricing is ever reached, so this
    # test needs no QuantLib (Codex P2 review of PR #126) -- it proves only
    # that Price-only mode never exposes benchmark controls/sections, not
    # anything about a successful price.
    at = _run_page()
    _set_case_json(at, "{ this is not valid json ")
    _press_price(at)  # default mode is Price only

    assert not at.exception
    assert len(at.error) >= 1  # the malformed-JSON error is still rendered
    # No "Benchmark input" controls are ever shown in Price-only mode.
    assert not any(t.label == "Standalone option benchmark JSON" for t in at.text_area)
    assert not any(s.label == "Active quote side" for s in at.selectbox)


# --- 9. Benchmark mode: quote side is mandatory, no hidden default ----------------


def test_benchmark_mode_without_quote_side_selected_shows_warning_and_does_not_run():
    at = _run_page()
    _set_mode(at, _MODE_PRICE_AND_BENCHMARK)
    _press_price(at)

    assert not at.exception
    assert any("quote side" in w.value.lower() for w in at.warning)
    assert len(at.metric) == 0


# --- 10. Full-page benchmark mode wiring: routes to the button before construction -
#
# A genuine full end-to-end SUCCESS render through a fresh full-page button click
# is deliberately NOT tested here (unlike the warning-path test above, which exits
# before any BLI construction): this module's own docstring documents a pre-existing,
# out-of-scope full-suite module-reload hazard that makes a *fresh* successful BLI
# construction unreliable once other suites have run. Full success rendering is
# instead proven by test_benchmark_pass_render_shows_distinct_pricing_benchmark_and_
# calibration_metrics below, which re-renders a display built once at import time
# (immune to that hazard, same isolation strategy as _SUCCESS_DISPLAY above).


# --- 11. No bundled benchmark example (Codex P1 review of PR #126) ---------------


def test_benchmark_textarea_starts_empty_with_no_bundled_example():
    # The pricing-case textarea is prefilled from a bundled sanitized example
    # (unchanged); the benchmark textarea deliberately is not -- there is no
    # examples/standalone_option_benchmark.json, so this field always starts
    # empty and instructs the trader to paste an actually-observed quote.
    at = _run_page()
    _set_mode(at, _MODE_PRICE_AND_BENCHMARK)
    text_area = next(t for t in at.text_area if t.label == "Standalone option benchmark JSON")
    assert text_area.value == ""
    assert any(
        "no bundled benchmark example" in c.value.lower()
        or "paste" in c.value.lower()
        for c in at.caption
    )


def test_no_example_benchmark_loader_or_bundled_file_reference_exists():
    assert not hasattr(ui_module, "_load_example_benchmark_text")
    source = inspect.getsource(ui_module)
    assert "standalone_option_benchmark.json" not in source
    assert "_EXAMPLE_BENCHMARK_PATH" not in source


def test_benchmark_upload_no_file_does_not_fall_back_to_paste_source():
    source = inspect.getsource(ui_module)
    assert "No benchmark file uploaded" in source
    assert "benchmark_text = _decode_uploaded_json_text(" in source
    assert "benchmark_text = benchmark_textarea_text" in source


# --- 12. Render helper: PASS + CALIBRATED shows clearly separated metrics --------


@_requires_quantlib
def test_benchmark_pass_render_shows_distinct_pricing_benchmark_and_calibration_metrics():
    at = _run_benchmark_render(_BENCHMARK_PASS_DISPLAY)
    assert not at.exception
    assert any("Comparison PASS" in s.value for s in at.success)
    assert any("Calibration SUCCESS" in s.value for s in at.success)

    metrics = {m.label: m.value for m in at.metric}
    # Model, benchmark, and calibrated values are distinct, separately labeled.
    assert "Model fair premium per 100" in metrics  # pricing section
    assert "Benchmark premium per 100" in metrics  # benchmark section
    assert "Model fair premium per 100 (comparison)" in metrics
    assert "Implied PRICE_VOL" in metrics
    assert float(metrics["Implied PRICE_VOL"]) == pytest.approx(0.18, abs=1e-4)
    assert float(metrics["Model fair premium per 100"]) == pytest.approx(
        _EXPECTED_BLACK76_PV_PER_100
    )
    assert float(metrics["Benchmark premium per 100"]) == pytest.approx(
        _EXPECTED_BLACK76_PV_PER_100
    )


# --- 13. NON_COMPARABLE renders without fabricated residual metrics -------------


@_requires_quantlib
def test_non_comparable_render_shows_no_fabricated_residual_metrics():
    at = _run_benchmark_render(_BENCHMARK_NON_COMPARABLE_DISPLAY)
    assert not at.exception
    assert any("Comparison NON_COMPARABLE" in e.value for e in at.error)

    metrics = {m.label: m.value for m in at.metric}
    # Residual fields are None for a mismatch outcome (reasons 1-8 of the
    # existing compare_bli_benchmark priority) -- never rendered as a
    # fabricated 0/placeholder metric, only as an honest caption.
    assert "Relative residual" not in metrics
    assert "Signed residual per 100" not in metrics
    assert any("not available" in c.value for c in at.caption)


# --- 14. Calibration solver failure renders without fabricated implied vol ------


@_requires_quantlib
def test_calibration_solver_failed_render_shows_no_fabricated_implied_vol():
    at = _run_benchmark_render(_BENCHMARK_SOLVER_FAILED_DISPLAY)
    assert not at.exception
    assert any("Calibration FAILED" in e.value for e in at.error)

    metrics = {m.label: m.value for m in at.metric}
    assert "Implied PRICE_VOL" not in metrics
    assert any("not available" in c.value for c in at.caption)

    # Solver diagnostics (bounds/tolerances/config) are still shown -- the
    # theoretical-bound outcome only drops implied/model/residual, never the
    # whole diagnostic picture.
    json_blocks = [json.loads(j.value) for j in at.json]
    solver_diagnostics = json_blocks[-1]
    assert solver_diagnostics["lower_price_vol"] is not None
    assert solver_diagnostics["upper_price_vol"] is not None
    assert solver_diagnostics["max_iterations"] is not None


# ==================================================================================
# Issue #101: current-run JSON/Markdown export downloads.
# ==================================================================================

_EXPORT_JSON_LABEL = "Download current run JSON"
_EXPORT_MARKDOWN_LABEL = "Download current run Markdown"

# --- 15. No download controls before a real workflow result exists ----------------


def test_no_download_controls_before_execution():
    # Full page, no button press: nothing has been priced yet.
    at = _run_page()
    assert len(at.download_button) == 0


def test_no_download_controls_when_malformed_json_blocks_execution():
    at = _run_page()
    _set_case_json(at, "{ this is not valid json ")
    _press_price(at)

    assert not at.exception
    assert len(at.error) >= 1
    assert len(at.download_button) == 0


def test_no_download_controls_when_benchmark_mode_quote_side_unselected():
    at = _run_page()
    _set_mode(at, _MODE_PRICE_AND_BENCHMARK)
    _press_price(at)

    assert not at.exception
    assert len(at.download_button) == 0


# --- 16. Both download controls appear after a real result (price-only) -----------


def test_both_download_controls_appear_after_failed_price_only_result():
    # FAILED is still a real workflow result -- export must remain available
    # to capture the failure diagnostics as UAT evidence.
    at = _run_render(_FAILED_DISPLAY)
    assert not at.exception
    labels = {b.label for b in at.download_button}
    assert _EXPORT_JSON_LABEL in labels
    assert _EXPORT_MARKDOWN_LABEL in labels
    assert len(at.download_button) == 2


@_requires_quantlib
def test_both_download_controls_appear_after_price_only_success():
    at = _run_render(_SUCCESS_DISPLAY)
    assert not at.exception
    labels = {b.label for b in at.download_button}
    assert _EXPORT_JSON_LABEL in labels
    assert _EXPORT_MARKDOWN_LABEL in labels
    assert len(at.download_button) == 2


# --- 17. Both download controls appear after benchmark-mode execution -------------


@_requires_quantlib
def test_both_download_controls_appear_after_benchmark_pass_calibrated():
    at = _run_benchmark_render(_BENCHMARK_PASS_DISPLAY)
    assert not at.exception
    labels = {b.label for b in at.download_button}
    assert _EXPORT_JSON_LABEL in labels
    assert _EXPORT_MARKDOWN_LABEL in labels
    assert len(at.download_button) == 2


@_requires_quantlib
def test_both_download_controls_appear_after_non_comparable_and_solver_failed():
    for display in (_BENCHMARK_NON_COMPARABLE_DISPLAY, _BENCHMARK_SOLVER_FAILED_DISPLAY):
        at = _run_benchmark_render(display)
        assert not at.exception
        labels = {b.label for b in at.download_button}
        assert _EXPORT_JSON_LABEL in labels
        assert _EXPORT_MARKDOWN_LABEL in labels


# --- 18. Button payloads equal the pure helper output, fixed names/MIME -----------


def test_export_button_payloads_equal_pure_helper_output_and_use_fixed_names_mime(
    monkeypatch,
):
    from shiori_pricing_lab.app import standalone_option_ui as ui_module_local

    calls = []

    def _fake_download_button(label, *, data, file_name, mime, on_click):
        calls.append(
            {
                "label": label,
                "data": data,
                "file_name": file_name,
                "mime": mime,
                "on_click": on_click,
            }
        )
        return False

    monkeypatch.setattr(ui_module_local.st, "download_button", _fake_download_button)
    ui_module_local._render_export_section(_FAILED_DISPLAY)

    assert len(calls) == 2
    json_call = next(c for c in calls if c["label"] == _EXPORT_JSON_LABEL)
    md_call = next(c for c in calls if c["label"] == _EXPORT_MARKDOWN_LABEL)

    assert json_call["data"] == render_standalone_run_as_json(_FAILED_DISPLAY)
    assert json_call["file_name"] == "shiori_standalone_run.json"
    assert json_call["mime"] == "application/json"
    assert json_call["on_click"] == "ignore"

    assert md_call["data"] == render_standalone_run_as_markdown(_FAILED_DISPLAY)
    assert md_call["file_name"] == "shiori_standalone_run.md"
    assert md_call["mime"] == "text/markdown; charset=utf-8"
    assert md_call["on_click"] == "ignore"


# --- 19. Export never repeats a pricing/comparison/calibration call ---------------


def test_export_section_never_calls_pricing_or_benchmark_workflow_again(monkeypatch):
    from shiori_pricing_lab.app import standalone_option_ui as ui_module_local

    def _fail(*args, **kwargs):
        raise AssertionError("export must not call the pricing/benchmark workflow again")

    monkeypatch.setattr(ui_module_local, "price_standalone_option_case", _fail)
    monkeypatch.setattr(ui_module_local, "price_standalone_option_case_with_benchmark", _fail)

    # Must not raise: _render_export_section only reads the already-computed display.
    ui_module_local._render_export_section(_FAILED_DISPLAY)


def test_export_module_source_has_no_pricing_comparison_or_calibration_calls():
    from shiori_pricing_lab.app import standalone_option_run_export as export_module

    # Scan the code body only: the module docstring legitimately explains
    # which upstream functions produce the display dict this module
    # consumes -- that prose must not count as a functional reference.
    source = inspect.getsource(export_module)
    body = source.replace(export_module.__doc__ or "", "")
    for forbidden in (
        "price_bli_mvp_standalone_option",
        "price_standalone_option_case(",
        "price_standalone_option_case_with_benchmark(",
        "compare_bli_benchmark",
        "calibrate_bli_implied_price_vol",
        "resolve_standalone_option_pricing_inputs",
        "solve_implied_dirty_price_vol",
        "black76_dirty_price_option_pv_per_100",
        "black76_price_option_pv_per_100",
        "BLIStandaloneBondOptionRequest",
        "BLIBenchmarkQuote",
        "PricingResult",
        "QuantLib",
        "quantlib",
    ):
        assert forbidden not in body, f"unexpected reference to {forbidden!r}"


# --- 20. Pricing / benchmark / comparison / calibration / export stay distinct ----


@_requires_quantlib
def test_export_section_is_its_own_distinct_subheading():
    at = _run_benchmark_render(_BENCHMARK_PASS_DISPLAY)
    assert not at.exception
    subheadings = [s.value for s in at.subheader]
    for expected in ("Context", "Benchmark", "Comparison", "Calibration", "Export current run"):
        assert expected in subheadings
    # Export is listed after every result section, never interleaved with them.
    assert subheadings.index("Export current run") > subheadings.index("Calibration")


# --- 21. Module-boundary: UI imports only the bounded export helper ---------------


def test_ui_calls_only_the_bounded_export_helper():
    source = inspect.getsource(ui_module)
    assert "render_standalone_run_as_json(" in source
    assert "render_standalone_run_as_markdown(" in source
    # The UI never assembles JSON/Markdown text itself.
    assert "json.dumps(" not in source
    assert "## " not in source


def test_export_helper_module_defines_no_streamlit_names():
    from shiori_pricing_lab.app import standalone_option_run_export as export_module

    module_names = set(dir(export_module))
    forbidden_names = {"streamlit", "st"}
    assert forbidden_names.isdisjoint(module_names)


# ==================================================================================
# Issue #6: live Bloomberg bond-quote source UI, Eddy-approved acquisition-time
# contract (issue #6 comment 5028876767, PR #129 comment 5028878866).
# ==================================================================================

# --- 22. Bond quote source selector: default preserves existing manual mode -------


def test_bond_quote_source_selector_defaults_to_case_json_with_no_bloomberg_controls():
    at = _run_page()
    radio = next(r for r in at.radio if r.label == "Bond quote source")
    assert list(radio.options) == [_BOND_QUOTE_SOURCE_CASE_JSON, _BOND_QUOTE_SOURCE_BLOOMBERG]
    assert radio.value == _BOND_QUOTE_SOURCE_CASE_JSON
    assert not any(t.label == "Bloomberg security" for t in at.text_input)
    assert any(b.label == "Price standalone option" for b in at.button)
    assert not any(b.label == "Refresh Bloomberg quote and price" for b in at.button)


def test_manual_mode_never_calls_bloomberg_workflow(monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("Bloomberg workflow must not be called in Case JSON mode")

    monkeypatch.setattr(ui_module, "price_standalone_option_case_with_bloomberg_quote", _fail)
    monkeypatch.setattr(
        ui_module, "price_standalone_option_case_with_bloomberg_quote_and_benchmark", _fail
    )

    at = _run_page()
    _set_case_json(at, "{ this is not valid json ")
    _press_price(at)  # default Bond quote source is Case JSON

    assert not at.exception
    assert len(at.error) >= 1


# --- 23. Bloomberg mode: explicit security/side, no preselected side, no
#         source_as_of / live retrieved_at controls, distinct button label ---------


def test_bloomberg_mode_shows_explicit_inputs_with_no_preselected_side():
    at = _run_page()
    _set_bond_quote_source(at, _BOND_QUOTE_SOURCE_BLOOMBERG)

    assert any(t.label == "Bloomberg security" for t in at.text_input)
    quote_side_box = next(s for s in at.selectbox if s.label == "Quote side")
    assert list(quote_side_box.options) == ["BID", "MID", "OFFER"]
    assert quote_side_box.value is None
    # No default anywhere yet.
    # No separate "Active quote side" selector in Bloomberg mode, even in
    # benchmark mode (the Bloomberg quote side drives comparison/calibration).
    _set_mode(at, _MODE_PRICE_AND_BENCHMARK)
    assert not any(s.label == "Active quote side" for s in at.selectbox)


def test_bloomberg_mode_has_no_source_as_of_or_live_retrieved_at_controls():
    at = _run_page()
    _set_bond_quote_source(at, _BOND_QUOTE_SOURCE_BLOOMBERG)

    assert not any(t.label.startswith("source_as_of") for t in at.text_input)
    assert not any(t.label.startswith("retrieved_at") for t in at.text_input)


def test_case_json_mode_still_shows_retrieved_at_control():
    at = _run_page()
    assert any(t.label.startswith("retrieved_at") for t in at.text_input)


def test_bloomberg_mode_button_label_is_refresh_and_price():
    at = _run_page()
    _set_bond_quote_source(at, _BOND_QUOTE_SOURCE_BLOOMBERG)

    assert any(b.label == "Refresh Bloomberg quote and price" for b in at.button)
    assert not any(b.label == "Price standalone option" for b in at.button)


def test_bloomberg_mode_missing_security_shows_warning_and_does_not_run():
    at = _run_page()
    _set_bond_quote_source(at, _BOND_QUOTE_SOURCE_BLOOMBERG)
    next(s for s in at.selectbox if s.label == "Quote side").set_value("MID").run()
    _press_bloomberg_refresh(at)

    assert not at.exception
    assert any("security" in w.value.lower() for w in at.warning)
    assert len(at.metric) == 0


def test_bloomberg_mode_missing_quote_side_shows_warning_and_does_not_run():
    at = _run_page()
    _set_bond_quote_source(at, _BOND_QUOTE_SOURCE_BLOOMBERG)
    next(t for t in at.text_input if t.label == "Bloomberg security").set_value(
        "91282CQX Govt"
    ).run()
    _press_bloomberg_refresh(at)

    assert not at.exception
    assert any("quote side" in w.value.lower() for w in at.warning)
    assert len(at.metric) == 0


# --- 24. Bloomberg failure: exact error shown, no fallback, no stale results -------
#
# The stub is installed *inside* the AppTest script function itself (a fresh
# `from ... import ...` there, exactly like the file's other script
# functions) rather than via `monkeypatch.setattr(ui_module, ...)` from the
# test body: under the full suite, earlier suites' `sys.modules` deletion
# games (see the module docstring's "Full-suite interference" note) can
# leave the test file's own `ui_module` reference stale relative to what
# AppTest's fresh import resolves, so an externally-applied patch can miss.
# Patching inside the script -- which reruns on every widget interaction --
# uses whatever module object is current at each execution, sidestepping
# that pre-existing, out-of-scope hazard entirely.


def _bloomberg_failure_script() -> None:
    from shiori_pricing_lab.app import standalone_option_ui as fresh_ui_module
    from shiori_pricing_lab.data.bloomberg_bond_quote import BLIBloombergDapiError as _Error

    def _raise(*args, **kwargs):
        raise _Error("Bloomberg DAPI session failed to start")

    fresh_ui_module.price_standalone_option_case_with_bloomberg_quote = _raise
    fresh_ui_module.render_standalone_option_workbench_page()


def test_bloomberg_failure_shows_exact_error_and_never_falls_back():
    at = AppTest.from_function(_bloomberg_failure_script, default_timeout=60)
    at.run()
    _set_bond_quote_source(at, _BOND_QUOTE_SOURCE_BLOOMBERG)
    _fill_bloomberg_inputs(at, security="91282CQX Govt", side="MID")
    _press_bloomberg_refresh(at)

    assert not at.exception
    assert any("Bloomberg DAPI session failed to start" in e.value for e in at.error)
    assert len(at.metric) == 0
    assert len(at.download_button) == 0


def _bloomberg_date_mismatch_script() -> None:
    from shiori_pricing_lab.app import standalone_option_ui as fresh_ui_module

    def _raise(*args, **kwargs):
        raise ValueError(
            "pricing_timestamp ('2026-07-21T09:00:00+00:00', date '2026-07-21') must "
            "fall on valuation_date ('2026-07-01')"
        )

    fresh_ui_module.price_standalone_option_case_with_bloomberg_quote = _raise
    fresh_ui_module.render_standalone_option_workbench_page()


def test_bloomberg_date_mismatch_shows_exact_error_and_never_falls_back():
    at = AppTest.from_function(_bloomberg_date_mismatch_script, default_timeout=60)
    at.run()
    _set_bond_quote_source(at, _BOND_QUOTE_SOURCE_BLOOMBERG)
    _fill_bloomberg_inputs(at, security="91282CQX Govt", side="MID")
    _press_bloomberg_refresh(at)

    assert not at.exception
    assert any("valuation_date" in e.value for e in at.error)
    assert len(at.metric) == 0
    assert len(at.download_button) == 0


# --- 25. Correct headless workflow is called for each mode combination ------------


def _bloomberg_price_only_stub_script(calls: list, display: dict) -> None:
    from shiori_pricing_lab.app import standalone_option_ui as fresh_ui_module

    def _fake_price_only(case, *, bloomberg_security, quote_side):
        calls.append(
            {
                "bloomberg_security": bloomberg_security,
                "quote_side": quote_side,
            }
        )
        return None, None, None, display

    def _fail_benchmark(*args, **kwargs):
        raise AssertionError("benchmark workflow must not be called in price-only mode")

    fresh_ui_module.price_standalone_option_case_with_bloomberg_quote = _fake_price_only
    fresh_ui_module.price_standalone_option_case_with_bloomberg_quote_and_benchmark = (
        _fail_benchmark
    )
    fresh_ui_module.render_standalone_option_workbench_page()


def test_bloomberg_price_only_mode_calls_correct_workflow_and_renders_live_quote():
    calls: list[dict] = []
    at = AppTest.from_function(
        _bloomberg_price_only_stub_script,
        kwargs={"calls": calls, "display": _FAKE_BLOOMBERG_PRICE_ONLY_DISPLAY},
        default_timeout=60,
    )
    at.run()
    _set_bond_quote_source(at, _BOND_QUOTE_SOURCE_BLOOMBERG)
    _fill_bloomberg_inputs(at, security="91282CQX Govt", side="MID")
    _press_bloomberg_refresh(at)

    assert not at.exception
    assert calls == [{"bloomberg_security": "91282CQX Govt", "quote_side": "MID"}]
    subheadings = [s.value for s in at.subheader]
    assert "Live Bloomberg Quote" in subheadings


def _bloomberg_benchmark_stub_script(calls: list, display: dict) -> None:
    from shiori_pricing_lab.app import standalone_option_ui as fresh_ui_module

    def _fake_benchmark(case, benchmark_case, *, bloomberg_security, quote_side):
        calls.append(
            {
                "bloomberg_security": bloomberg_security,
                "quote_side": quote_side,
            }
        )
        return None, None, None, None, None, None, display

    def _fail_price_only(*args, **kwargs):
        raise AssertionError("price-only workflow must not be called in benchmark mode")

    fresh_ui_module.price_standalone_option_case_with_bloomberg_quote_and_benchmark = (
        _fake_benchmark
    )
    fresh_ui_module.price_standalone_option_case_with_bloomberg_quote = _fail_price_only
    fresh_ui_module.render_standalone_option_workbench_page()


@_requires_quantlib
def test_bloomberg_benchmark_mode_calls_correct_workflow_with_same_side():
    calls: list[dict] = []
    at = AppTest.from_function(
        _bloomberg_benchmark_stub_script,
        kwargs={"calls": calls, "display": _FAKE_BLOOMBERG_BENCHMARK_DISPLAY},
        default_timeout=60,
    )
    at.run()
    _set_mode(at, _MODE_PRICE_AND_BENCHMARK)
    _set_bond_quote_source(at, _BOND_QUOTE_SOURCE_BLOOMBERG)
    next(t for t in at.text_area if t.label == "Standalone option benchmark JSON").set_value(
        json.dumps(_benchmark_envelope())
    ).run()
    _fill_bloomberg_inputs(at, security="91282CQX Govt", side="MID")
    _press_bloomberg_refresh(at)

    assert not at.exception
    assert calls == [{"bloomberg_security": "91282CQX Govt", "quote_side": "MID"}]
    # The same explicit side reached Bloomberg (asserted above) and there is
    # no separate active-side selector for comparison/calibration to diverge.
    assert not any(s.label == "Active quote side" for s in at.selectbox)
    subheadings = [s.value for s in at.subheader]
    assert "Live Bloomberg Quote" in subheadings
    assert "Benchmark" in subheadings
    assert "Comparison" in subheadings
    assert "Calibration" in subheadings


# --- 26. Live provenance renders as its own distinct section ----------------------


def test_live_bloomberg_quote_renders_as_distinct_section_verbatim():
    at = _run_bloomberg_render(_FAKE_BLOOMBERG_PRICE_ONLY_DISPLAY)
    assert not at.exception

    subheadings = [s.value for s in at.subheader]
    assert "Context" in subheadings
    assert "Live Bloomberg Quote" in subheadings
    assert subheadings.index("Live Bloomberg Quote") > subheadings.index("Context")

    json_blocks = [json.loads(j.value) for j in at.json]
    assert _FAKE_LIVE_BLOOMBERG_QUOTE_DISPLAY in json_blocks


def test_live_bloomberg_quote_display_has_no_source_as_of_field():
    assert "source_as_of" not in _FAKE_LIVE_BLOOMBERG_QUOTE_DISPLAY
    assert _FAKE_LIVE_BLOOMBERG_QUOTE_DISPLAY["timestamp_basis"] == "SHIORI_ACQUISITION_TIME"
    assert _FAKE_LIVE_BLOOMBERG_QUOTE_DISPLAY["bloomberg_quote_observation_time"] is None
    assert _FAKE_LIVE_BLOOMBERG_QUOTE_DISPLAY["refreshed_scope"] == "BOND_QUOTE_ONLY"
    assert _FAKE_LIVE_BLOOMBERG_QUOTE_DISPLAY["other_market_inputs"] == "CASE_JSON_UNCHANGED"


def test_live_bloomberg_quote_section_states_provenance_disclaimer():
    at = _run_bloomberg_render(_FAKE_BLOOMBERG_PRICE_ONLY_DISPLAY)
    assert not at.exception

    captions = " ".join(c.value for c in at.caption)
    assert "quote-observation time is not provided" in captions
    assert "acquired_at is when Shiori received" in captions
    assert "Only the bond quote was refreshed" in captions
    assert "mixed-provenance" in captions


# --- 27. Only expected local-input exceptions plus BLIBloombergDapiError caught ----


def test_ui_catches_bloomberg_dapi_error_alongside_existing_local_input_exceptions():
    source = inspect.getsource(ui_module)
    body = source.replace(ui_module.__doc__ or "", "")
    assert "except Exception" not in body
    assert "BLIBloombergDapiError" in body
    assert "json.JSONDecodeError" in body
    assert "UnicodeDecodeError" in body


def test_ui_source_has_no_broad_bloomberg_side_defaults():
    # Every st.selectbox for a quote side (existing active-side and the new
    # Bloomberg quote-side selector) starts unselected -- there are exactly
    # two `index=None` selectboxes in the code body, never a hidden default
    # like "MID". Scan the body only: the docstring legitimately mentions
    # `index=None` in prose, which must not count as a widget occurrence.
    source = inspect.getsource(ui_module)
    body = source.replace(ui_module.__doc__ or "", "")
    assert body.count("index=None") == 2


def test_ui_source_has_no_source_as_of_or_live_retrieved_at_bloomberg_controls():
    source = inspect.getsource(ui_module)
    assert "source_as_of (must equal" not in source
    assert "bloomberg_source_as_of_text" not in source
