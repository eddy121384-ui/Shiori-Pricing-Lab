"""Tests for the standalone bond-option trader workbench UI (Issue #97, PR B;
Issue #125 benchmark comparison / implied PRICE_VOL; Issue #101 current-run
export; Issue #6 live Bloomberg bond quote; **Issue #133 Slice B trader
workbench rebuild**).

Isolation strategy (see the full-suite interference note below):

- **Page wiring** is exercised with ``AppTest.from_function`` targeting the
  page directly, never by re-executing the whole ``streamlit_app.py`` (which
  would pull in the unrelated ``bli_mvp`` demo-fixture rebuild).
- **Render correctness** (SUCCESS metrics, FAILED errors, ``retrieved_at``
  separation) is exercised by rendering **real display dicts built once at
  import time** (before any test body runs) through ``AppTest.from_function``
  with ``kwargs``. Rendering a plain display dict does no BLI construction, so
  these assertions are strong *and* immune to the process-state hazard.
- **Navigation / default page** is verified with a source assertion on
  ``streamlit_app.py``.

Full-suite interference (root cause, not fixed here): the import-isolation
suites ``test_products`` / ``test_products_ccs_fxswap`` / ``test_pricing_engine``
call ``del sys.modules[...]`` for ``products``/``data``/``app`` prefixes to
assert layering, but never delete ``reference_data`` and never restore
``sys.modules``. That leaves two distinct ``Currency`` enum objects; any *fresh*
construction mixing them raises. These UI tests avoid triggering a fresh
construction after that state by building their pricing inputs at import time
and only re-rendering them.
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
    _safe_prefill,
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

_PRICE_BUTTON = "Price standalone option"
_REFRESH_BUTTON = "Refresh Bloomberg quote and price"


def _yield_vol_case_text() -> str:
    envelope = json.loads(_EXAMPLE_PATH.read_text(encoding="utf-8"))
    envelope["volatility_input"] = {
        **envelope["volatility_input"],
        "volatility_basis": "YIELD_VOL",
    }
    return json.dumps(envelope)


# Real display contexts, built at import time (before any test body runs).
# Rendering these does no BLI construction. A YIELD_VOL case is a guard
# rejection -> FAILED, and needs no QuantLib.
_FAILED_DISPLAY = price_standalone_option_case(
    _yield_vol_case_text(), retrieved_at=_RETRIEVED_AT
)[2]
_FAILED_DISPLAY_NO_RETRIEVED = price_standalone_option_case(_yield_vol_case_text())[2]
_SUCCESS_DISPLAY = (
    price_standalone_option_case(_load_example_case_text(), retrieved_at=_RETRIEVED_AT)[2]
    if _QUANTLIB_AVAILABLE
    else None
)


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


# --- Render-only AppTest entry points (module-level for from_function) -----------


def _render_display_script(display: dict) -> None:
    from shiori_pricing_lab.app.standalone_option_ui import (
        _render_export_section,
        _render_forward_and_underlying,
        _render_pricing_result,
    )

    _render_pricing_result(display)
    _render_forward_and_underlying(display)
    _render_export_section(display)


def _render_page_script() -> None:
    from shiori_pricing_lab.app.standalone_option_ui import (
        render_standalone_option_workbench_page,
    )

    render_standalone_option_workbench_page()


def _render_benchmark_display_script(display: dict) -> None:
    from shiori_pricing_lab.app.standalone_option_ui import (
        _render_benchmark_result,
        _render_export_section,
        _render_forward_and_underlying,
        _render_pricing_result,
    )

    _render_pricing_result(display)
    _render_forward_and_underlying(display)
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
    next(b for b in at.button if b.label == _PRICE_BUTTON).click().run()


# --- Live Bloomberg render fixtures ----------------------------------------------

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
    next(
        t for t in at.text_input if t.label == "Bloomberg security (Yellow Key)"
    ).set_value(security).run()
    next(s for s in at.selectbox if s.label == "Quote side").set_value(side).run()


def _press_bloomberg_refresh(at: AppTest) -> None:
    next(b for b in at.button if b.label == _REFRESH_BUTTON).click().run()


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
    assert _retrieved_at_or_none("  ") == "  "


def test_decode_uploaded_json_text_strict_utf8():
    assert _decode_uploaded_json_text(b'{"a": 1}') == '{"a": 1}'
    with pytest.raises(UnicodeDecodeError):
        _decode_uploaded_json_text(b"\xff\xfe invalid utf-8")


def test_safe_prefill_reads_seven_values_from_case_and_falls_back_on_garbage():
    prefill = _safe_prefill(_load_example_case_text())
    assert prefill["option_type"] == "CALL"
    assert prefill["position"] == "BUY"
    assert prefill["strike_price"] == 99.5
    assert prefill["notional"] == 50.0
    assert prefill["forward_clean_price_per_100"] == 101.3
    assert prefill["forward_quote_side"] == "MID"
    assert prefill["volatility"] == 0.18

    # Any parse/shape problem is a display fallback, never a raise or a
    # fabricated pricing value.
    fallback = _safe_prefill("{ not json")
    assert fallback["option_type"] == "CALL"
    assert _safe_prefill(None)["strike_price"] == 99.5


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
    assert metrics["Model fair premium per 100"] != metrics["Total notional model fair premium"]
    for label in (
        "Forward clean price per 100",
        "Black-76 PV per 100",
        "Effective reporting-date discount factor",
        "Time to expiry (years)",
    ):
        assert label in metrics


@_requires_quantlib
def test_success_render_shows_every_greek_with_its_unit_in_the_label():
    at = _run_render(_SUCCESS_DISPLAY)
    assert not at.exception

    metrics = {m.label: m.value for m in at.metric}
    expected = {
        "Delta / 100 (per +1.00 price pt)": "forward_price_delta_per_100",
        "Gamma / 100 (per price pt^2)": "forward_price_gamma_per_100",
        "Vega / 100 (per +0.01 vol)": "vega_per_vol_point_per_100",
        "Theta / 100 (per calendar day)": "theta_per_calendar_day_per_100",
        "Position delta total (per +1.00 price pt)": "position_forward_price_delta_total",
        "Position gamma total (per price pt^2)": "position_forward_price_gamma_total",
        "Position vega total (per +0.01 vol)": "position_vega_per_vol_point_total",
        "Position theta total (per calendar day)": "position_theta_per_calendar_day_total",
    }
    for label, key in expected.items():
        assert label in metrics
        assert float(metrics[label]) == pytest.approx(_SUCCESS_DISPLAY[key], abs=1e-6)

    captions = " ".join(c.value for c in at.caption)
    assert "+1.00 forward clean price point" in captions
    assert "+0.01 absolute volatility" in captions
    assert "+1 calendar day" in captions


@_requires_quantlib
def test_success_render_labels_instrument_analytics_apart_from_position_risk():
    at = _run_render(_SUCCESS_DISPLAY)
    assert not at.exception

    markdown = " ".join(m.value for m in at.markdown)
    assert "**Instrument analytics (per 100)**" in markdown
    assert "**Position risk (notional and BUY/SELL sign applied)**" in markdown

    captions = " ".join(c.value for c in at.caption)
    assert "BUY/SELL position sign is NOT applied" in captions
    assert "(BUY = +1, SELL = -1)" in captions
    assert f"Position {_SUCCESS_DISPLAY['position']}" in captions

    total_labels = [m.label for m in at.metric if "total" in m.label.lower()]
    assert total_labels
    for label in total_labels:
        assert label.startswith("Position ") or "premium" in label.lower()


@_requires_quantlib
def test_success_render_shows_pending_risk_areas_as_captions_never_metrics():
    # The approved mockup's OAS/DV01/duration/convexity analytics are visible
    # as honest pending states -- never fabricated numbers, never st.metric.
    at = _run_render(_SUCCESS_DISPLAY)
    assert not at.exception

    metric_labels = {m.label for m in at.metric}
    for pending in ("OAS Delta", "DV01", "DV01 Gamma", "Modified Duration", "Convexity"):
        assert pending not in metric_labels
    rendered = " ".join(m.value for m in at.markdown)
    assert "Pending model support" in rendered
    assert "DV01: Pending model support" in rendered


# --- 3. FAILED render: no premium, structured error detail preserved ---------


def test_failed_render_shows_no_premium_and_preserves_error_detail():
    at = _run_render(_FAILED_DISPLAY)
    assert not at.exception

    assert any("Pricing FAILED" in e.value for e in at.error)
    assert len(at.metric) == 0  # no premium/intermediate/greek/pending metrics
    assert any("UNSUPPORTED_PRODUCT" in m.value for m in at.markdown)

    json_blocks = [json.loads(j.value) for j in at.json]
    # Structured error detail preserved verbatim (product_id + reasons).
    assert any(
        isinstance(block, dict) and "product_id" in block and "reasons" in block
        for block in json_blocks
    )
    # The reproducibility context is still available on a failure.
    assert any(
        isinstance(block, dict) and block.get("status") == "FAILED" for block in json_blocks
    )


def test_failed_render_shows_no_fabricated_numbers_anywhere():
    at = _run_render(_FAILED_DISPLAY)
    assert not at.exception
    # No forward/underlying metrics on a failure either.
    assert len(at.metric) == 0


# --- 4. retrieved_at stays separate from source-as-of ------------------------


def test_retrieved_at_supplied_is_separate_from_source_as_of():
    at = _run_render(_FAILED_DISPLAY)
    context = next(
        json.loads(j.value)
        for j in at.json
        if isinstance(json.loads(j.value), dict) and "retrieved_at" in json.loads(j.value)
    )
    assert context["retrieved_at"] == _RETRIEVED_AT
    assert context["source_as_of"] == _SOURCE_AS_OF
    assert context["retrieved_at"] != context["source_as_of"]


def test_retrieved_at_empty_maps_to_none_in_context():
    at = _run_render(_FAILED_DISPLAY_NO_RETRIEVED)
    context = next(
        json.loads(j.value)
        for j in at.json
        if isinstance(json.loads(j.value), dict) and "retrieved_at" in json.loads(j.value)
    )
    assert context["retrieved_at"] is None
    assert context["source_as_of"] == _SOURCE_AS_OF


# --- 5. Full-page wiring: button triggers the workflow (malformed input) ------


def test_malformed_json_renders_error_and_no_metrics():
    at = _run_page()
    _set_case_json(at, "{ this is not valid json ")
    _press_price(at)

    assert not at.exception  # handled at the UI boundary, not raised
    assert len(at.error) >= 1
    assert len(at.metric) == 0
    assert len(at.download_button) == 0


# --- 6. Navigation + default page (source assertion) -------------------------


def test_streamlit_app_makes_standalone_workbench_the_default_page():
    source = _APP_PATH.read_text(encoding="utf-8")

    # New page label routed to the new render function.
    assert '"Standalone Bond Option Workbench"' in source
    assert "render_standalone_option_workbench_page" in source

    # Existing two pages and their render functions are unchanged/present.
    assert '"Rates Curve Demo"' in source
    assert '"Bond Option (BLI MVP)"' in source
    assert "render_rates_curve_demo_page()" in source
    assert "render_bli_mvp_page()" in source

    # The standalone workbench is the FIRST radio option -> the default page,
    # ahead of both legacy pages.
    workbench_pos = source.index('"Standalone Bond Option Workbench"')
    rates_pos = source.index('"Rates Curve Demo"')
    bli_pos = source.index('"Bond Option (BLI MVP)"')
    assert workbench_pos < rates_pos < bli_pos


# --- 7. Source-level guarantees ----------------------------------------------


def test_ui_calls_only_headless_workflow_for_execution():
    source = inspect.getsource(ui_module)
    assert "price_standalone_option_case(" in source
    assert "price_standalone_option_case_with_benchmark(" in source
    # The bounded overlay is the single bridge before every workflow call.
    assert "apply_standalone_option_input_overlay(" in source
    # No shortcut around the builder / direct request construction.
    assert "BLIStandaloneBondOptionRequest(" not in source
    assert "build_bli_standalone_option_request" not in source
    # No direct pricing/comparison/calibration/resolver/solver/Black-76/
    # curve/vol/provider/QuantLib calls.
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
    body = source.replace(ui_module.__doc__ or "", "")
    assert "except Exception" not in body
    assert "client quote" not in body.lower()
    assert "client_quote" not in body.lower()
    assert "json.JSONDecodeError" in body
    assert "UnicodeDecodeError" in body


def test_only_two_quote_side_selectors_have_no_default():
    # The Bloomberg quote side and the benchmark active side each start
    # unselected (index=None). The forward quote side is a supported editable
    # input with a real default and must NOT be one of them.
    source = inspect.getsource(ui_module)
    body = source.replace(ui_module.__doc__ or "", "")
    assert body.count("index=None") == 2


# --- 8. Benchmark mode -------------------------------------------------------


def test_mode_selector_offers_price_only_and_benchmark_modes_with_price_only_default():
    at = _run_page()
    mode_radio = next(r for r in at.radio if r.label == "Mode")
    assert list(mode_radio.options) == [_MODE_PRICE_ONLY, _MODE_PRICE_AND_BENCHMARK]
    assert mode_radio.value == _MODE_PRICE_ONLY


def test_price_only_mode_never_renders_benchmark_controls():
    at = _run_page()
    _set_case_json(at, "{ this is not valid json ")
    _press_price(at)

    assert not at.exception
    assert len(at.error) >= 1
    assert not any(t.label == "Standalone option benchmark JSON" for t in at.text_area)
    assert not any(s.label == "Active quote side" for s in at.selectbox)


def test_benchmark_mode_without_quote_side_selected_shows_warning_and_does_not_run():
    at = _run_page()
    _set_mode(at, _MODE_PRICE_AND_BENCHMARK)
    _press_price(at)

    assert not at.exception
    assert any("quote side" in w.value.lower() for w in at.warning)
    assert len(at.metric) == 0


def test_benchmark_textarea_starts_empty_with_no_bundled_example():
    at = _run_page()
    _set_mode(at, _MODE_PRICE_AND_BENCHMARK)
    text_area = next(t for t in at.text_area if t.label == "Standalone option benchmark JSON")
    assert text_area.value == ""
    assert any(
        "no bundled benchmark example" in c.value.lower() or "paste" in c.value.lower()
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


@_requires_quantlib
def test_benchmark_pass_render_shows_distinct_pricing_benchmark_and_calibration_metrics():
    at = _run_benchmark_render(_BENCHMARK_PASS_DISPLAY)
    assert not at.exception
    assert any("Comparison PASS" in s.value for s in at.success)
    assert any("Calibration SUCCESS" in s.value for s in at.success)

    metrics = {m.label: m.value for m in at.metric}
    assert "Model fair premium per 100" in metrics
    assert "Benchmark premium per 100" in metrics
    assert "Model fair premium per 100 (comparison)" in metrics
    assert "Implied PRICE_VOL" in metrics
    assert float(metrics["Implied PRICE_VOL"]) == pytest.approx(0.18, abs=1e-4)


@_requires_quantlib
def test_non_comparable_render_shows_no_fabricated_residual_metrics():
    at = _run_benchmark_render(_BENCHMARK_NON_COMPARABLE_DISPLAY)
    assert not at.exception
    assert any("Comparison NON_COMPARABLE" in e.value for e in at.error)

    metrics = {m.label: m.value for m in at.metric}
    assert "Relative residual" not in metrics
    assert "Signed residual per 100" not in metrics
    assert any("not available" in c.value for c in at.caption)


@_requires_quantlib
def test_calibration_solver_failed_render_shows_no_fabricated_implied_vol():
    at = _run_benchmark_render(_BENCHMARK_SOLVER_FAILED_DISPLAY)
    assert not at.exception
    assert any("Calibration FAILED" in e.value for e in at.error)

    metrics = {m.label: m.value for m in at.metric}
    assert "Implied PRICE_VOL" not in metrics
    assert any("not available" in c.value for c in at.caption)

    json_blocks = [json.loads(j.value) for j in at.json]
    solver_diagnostics = json_blocks[-1]
    assert solver_diagnostics["lower_price_vol"] is not None
    assert solver_diagnostics["upper_price_vol"] is not None
    assert solver_diagnostics["max_iterations"] is not None


# --- 9. Export ----------------------------------------------------------------

_EXPORT_JSON_LABEL = "Download current run JSON"
_EXPORT_MARKDOWN_LABEL = "Download current run Markdown"


def test_no_download_controls_before_execution():
    at = _run_page()
    assert len(at.download_button) == 0


def test_no_download_controls_when_malformed_json_blocks_execution():
    at = _run_page()
    _set_case_json(at, "{ this is not valid json ")
    _press_price(at)

    assert not at.exception
    assert len(at.error) >= 1
    assert len(at.download_button) == 0


def test_both_download_controls_appear_after_failed_price_only_result():
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


@_requires_quantlib
def test_both_download_controls_appear_after_benchmark_pass_calibrated():
    at = _run_benchmark_render(_BENCHMARK_PASS_DISPLAY)
    assert not at.exception
    labels = {b.label for b in at.download_button}
    assert _EXPORT_JSON_LABEL in labels
    assert _EXPORT_MARKDOWN_LABEL in labels


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


def test_export_section_never_calls_pricing_or_benchmark_workflow_again(monkeypatch):
    from shiori_pricing_lab.app import standalone_option_ui as ui_module_local

    def _fail(*args, **kwargs):
        raise AssertionError("export must not call the pricing/benchmark workflow again")

    monkeypatch.setattr(ui_module_local, "price_standalone_option_case", _fail)
    monkeypatch.setattr(ui_module_local, "price_standalone_option_case_with_benchmark", _fail)
    ui_module_local._render_export_section(_FAILED_DISPLAY)


def test_ui_calls_only_the_bounded_export_helper():
    source = inspect.getsource(ui_module)
    assert "render_standalone_run_as_json(" in source
    assert "render_standalone_run_as_markdown(" in source
    # The UI never assembles JSON/Markdown text itself.
    assert "json.dumps(" not in source


# --- 10. Bloomberg (Issue #6) + Yellow Key + no Live/Verified before retrieval -----


def test_bond_quote_source_selector_defaults_to_case_json_with_no_bloomberg_controls():
    at = _run_page()
    radio = next(r for r in at.radio if r.label == "Bond quote source")
    assert list(radio.options) == [_BOND_QUOTE_SOURCE_CASE_JSON, _BOND_QUOTE_SOURCE_BLOOMBERG]
    assert radio.value == _BOND_QUOTE_SOURCE_CASE_JSON
    assert not any(t.label == "Bloomberg security (Yellow Key)" for t in at.text_input)
    assert any(b.label == _PRICE_BUTTON for b in at.button)
    assert not any(b.label == _REFRESH_BUTTON for b in at.button)


def test_bloomberg_security_control_mentions_yellow_key():
    at = _run_page()
    _set_bond_quote_source(at, _BOND_QUOTE_SOURCE_BLOOMBERG)
    assert any(
        t.label == "Bloomberg security (Yellow Key)" for t in at.text_input
    ), "Bloomberg security control must mention its Yellow Key"


def test_bloomberg_mode_shows_explicit_inputs_with_no_preselected_side():
    at = _run_page()
    _set_bond_quote_source(at, _BOND_QUOTE_SOURCE_BLOOMBERG)

    quote_side_box = next(s for s in at.selectbox if s.label == "Quote side")
    assert list(quote_side_box.options) == ["BID", "MID", "OFFER"]
    assert quote_side_box.value is None
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
    assert any(b.label == _REFRESH_BUTTON for b in at.button)
    assert not any(b.label == _PRICE_BUTTON for b in at.button)


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
    next(t for t in at.text_input if t.label == "Bloomberg security (Yellow Key)").set_value(
        "91282CQX Govt"
    ).run()
    _press_bloomberg_refresh(at)

    assert not at.exception
    assert any("quote side" in w.value.lower() for w in at.warning)
    assert len(at.metric) == 0


def test_header_shows_no_live_or_verified_isin_before_a_successful_retrieval():
    # Before any retrieval, the header must not claim Live or Verified ISIN;
    # the expected ISIN is shown as unverified.
    at = _run_page()
    _set_bond_quote_source(at, _BOND_QUOTE_SOURCE_BLOOMBERG)
    rendered = " ".join(m.value for m in at.markdown)
    assert "Verified ISIN" not in rendered
    assert "● Live" not in rendered
    assert "unverified" in rendered


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
    # No Live/Verified claim after a failed retrieval.
    rendered = " ".join(m.value for m in at.markdown)
    assert "Verified ISIN" not in rendered
    assert "● Live" not in rendered


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


def test_live_bloomberg_quote_renders_as_distinct_section_verbatim():
    at = _run_bloomberg_render(_FAKE_BLOOMBERG_PRICE_ONLY_DISPLAY)
    assert not at.exception
    rendered = " ".join(m.value for m in at.markdown)
    assert "Live Bloomberg quote" in rendered

    json_blocks = [json.loads(j.value) for j in at.json]
    assert _FAKE_LIVE_BLOOMBERG_QUOTE_DISPLAY in json_blocks


def test_live_bloomberg_quote_section_states_provenance_disclaimer():
    at = _run_bloomberg_render(_FAKE_BLOOMBERG_PRICE_ONLY_DISPLAY)
    assert not at.exception
    captions = " ".join(c.value for c in at.caption)
    assert "quote-observation time is not provided" in captions
    assert "acquired_at is when Shiori received" in captions
    assert "Only the bond quote was refreshed" in captions
    assert "mixed-provenance" in captions


def test_ui_catches_bloomberg_dapi_error_alongside_existing_local_input_exceptions():
    source = inspect.getsource(ui_module)
    body = source.replace(ui_module.__doc__ or "", "")
    assert "except Exception" not in body
    assert "BLIBloombergDapiError" in body
    assert "json.JSONDecodeError" in body
    assert "UnicodeDecodeError" in body


# ==================================================================================
# Issue #133 Slice B: the seven-value overlay reaches ALL FOUR workflow paths.
#
# Each stub captures the exact ``case`` object the UI passes. Because the UI
# applies apply_standalone_option_input_overlay before every workflow call, the
# captured case must be the OVERLAID envelope carrying the trader's edited value
# (here: a strike changed away from the bundled example's 99.5), not the raw
# base case. This proves the single bounded overlay is the real bridge on all
# four paths.
# ==================================================================================

_EDITED_STRIKE = 97.25


def _assert_overlaid(case: object) -> None:
    # The UI always passes a dict (the overlay return); assert the seven fields
    # are present and the edited strike took effect.
    assert isinstance(case, dict)
    assert case["bond_option"]["strike_price"] == _EDITED_STRIKE
    assert case["bond_option"]["option_type"] in ("CALL", "PUT")
    assert case["bond_option"]["position"] in ("BUY", "SELL")
    assert "notional" in case["bond_option"]
    assert "forward_clean_price_per_100" in case["forward_clean_price_input"]
    assert "quote_side" in case["forward_clean_price_input"]
    assert "volatility" in case["volatility_input"]


def _set_edited_strike(at: AppTest) -> None:
    next(
        n for n in at.number_input if n.label == "Strike price (clean, per 100)"
    ).set_value(_EDITED_STRIKE).run()


def _overlay_price_only_script(captured: list, display: dict) -> None:
    from shiori_pricing_lab.app import standalone_option_ui as fresh_ui_module

    def _stub(case, *, retrieved_at=None):
        captured.append(case)
        return None, None, display

    fresh_ui_module.price_standalone_option_case = _stub
    fresh_ui_module.render_standalone_option_workbench_page()


def test_price_only_workflow_receives_the_overlaid_case():
    captured: list = []
    at = AppTest.from_function(
        _overlay_price_only_script,
        kwargs={"captured": captured, "display": _FAILED_DISPLAY},
        default_timeout=60,
    )
    at.run()
    _set_edited_strike(at)
    _press_price(at)

    assert not at.exception
    assert len(captured) == 1
    _assert_overlaid(captured[0])


def _overlay_benchmark_script(captured: list, display: dict) -> None:
    from shiori_pricing_lab.app import standalone_option_ui as fresh_ui_module

    def _stub(case, benchmark_case, *, active_quote_side, retrieved_at=None):
        captured.append(case)
        return None, None, None, None, None, display

    fresh_ui_module.price_standalone_option_case_with_benchmark = _stub
    fresh_ui_module.render_standalone_option_workbench_page()


@_requires_quantlib
def test_manual_benchmark_workflow_receives_the_overlaid_case():
    captured: list = []
    at = AppTest.from_function(
        _overlay_benchmark_script,
        kwargs={"captured": captured, "display": _BENCHMARK_PASS_DISPLAY},
        default_timeout=60,
    )
    at.run()
    _set_mode(at, _MODE_PRICE_AND_BENCHMARK)
    next(t for t in at.text_area if t.label == "Standalone option benchmark JSON").set_value(
        json.dumps(_benchmark_envelope())
    ).run()
    next(s for s in at.selectbox if s.label == "Active quote side").set_value("MID").run()
    _set_edited_strike(at)
    _press_price(at)

    assert not at.exception
    assert len(captured) == 1
    _assert_overlaid(captured[0])


def _overlay_bloomberg_price_only_script(captured: list, display: dict) -> None:
    from shiori_pricing_lab.app import standalone_option_ui as fresh_ui_module

    def _stub(case, *, bloomberg_security, quote_side):
        captured.append(case)
        return None, None, None, display

    fresh_ui_module.price_standalone_option_case_with_bloomberg_quote = _stub
    fresh_ui_module.render_standalone_option_workbench_page()


def test_bloomberg_price_only_workflow_receives_the_overlaid_case():
    captured: list = []
    at = AppTest.from_function(
        _overlay_bloomberg_price_only_script,
        kwargs={"captured": captured, "display": _FAKE_BLOOMBERG_PRICE_ONLY_DISPLAY},
        default_timeout=60,
    )
    at.run()
    _set_bond_quote_source(at, _BOND_QUOTE_SOURCE_BLOOMBERG)
    _fill_bloomberg_inputs(at, security="91282CQX Govt", side="MID")
    _set_edited_strike(at)
    _press_bloomberg_refresh(at)

    assert not at.exception
    assert len(captured) == 1
    _assert_overlaid(captured[0])


def _overlay_bloomberg_benchmark_script(captured: list, display: dict) -> None:
    from shiori_pricing_lab.app import standalone_option_ui as fresh_ui_module

    def _stub(case, benchmark_case, *, bloomberg_security, quote_side):
        captured.append(case)
        return None, None, None, None, None, None, display

    fresh_ui_module.price_standalone_option_case_with_bloomberg_quote_and_benchmark = _stub
    fresh_ui_module.render_standalone_option_workbench_page()


@_requires_quantlib
def test_bloomberg_benchmark_workflow_receives_the_overlaid_case():
    captured: list = []
    at = AppTest.from_function(
        _overlay_bloomberg_benchmark_script,
        kwargs={"captured": captured, "display": _FAKE_BLOOMBERG_BENCHMARK_DISPLAY},
        default_timeout=60,
    )
    at.run()
    _set_mode(at, _MODE_PRICE_AND_BENCHMARK)
    _set_bond_quote_source(at, _BOND_QUOTE_SOURCE_BLOOMBERG)
    next(t for t in at.text_area if t.label == "Standalone option benchmark JSON").set_value(
        json.dumps(_benchmark_envelope())
    ).run()
    _fill_bloomberg_inputs(at, security="91282CQX Govt", side="MID")
    _set_edited_strike(at)
    _press_bloomberg_refresh(at)

    assert not at.exception
    assert len(captured) == 1
    _assert_overlaid(captured[0])


def test_overlay_is_applied_before_every_workflow_call_in_source():
    # Exactly one overlay call site feeds the shared overlaid_case into all four
    # branches -- a single bounded bridge, not four ad-hoc ones.
    source = inspect.getsource(ui_module)
    assert source.count("apply_standalone_option_input_overlay(") == 1
    assert "overlaid_case" in source
