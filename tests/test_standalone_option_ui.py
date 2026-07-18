"""Tests for the standalone bond-option trader workbench UI (Issue #97, PR B).

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
from shiori_pricing_lab.app.standalone_option_ui import (
    _decode_uploaded_json_text,
    _load_example_case_text,
    _retrieved_at_or_none,
    render_standalone_option_workbench_page,
)
from shiori_pricing_lab.app.standalone_option_workbench import price_standalone_option_case
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


def _render_display_script(display: dict) -> None:
    # Self-contained AppTest.from_function entry point: renders a prepared
    # display dict via the real page render helper. No BLI construction.
    from shiori_pricing_lab.app.standalone_option_ui import _render_pricing_result

    _render_pricing_result(display)


def _render_page_script() -> None:
    # Self-contained AppTest.from_function entry point for the full page.
    from shiori_pricing_lab.app.standalone_option_ui import (
        render_standalone_option_workbench_page,
    )

    render_standalone_option_workbench_page()


def _run_render(display: dict) -> AppTest:
    at = AppTest.from_function(
        _render_display_script, kwargs={"display": display}, default_timeout=60
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


def _press_price(at: AppTest) -> None:
    next(b for b in at.button if b.label == "Price standalone option").click().run()


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
    # No shortcut around the builder / direct request construction.
    assert "BLIStandaloneBondOptionRequest(" not in source
    assert "build_bli_standalone_option_request" not in source
    # No direct pricing/guard/curve/vol/provider/QuantLib imports or calls.
    for forbidden in (
        "price_bli_mvp_standalone_option",
        "bli_pricing_engine",
        "required_input_guard",
        "bli_curve_discount_factor",
        "bli_black76",
        "bli_forward_clean_price",
        "quantlib",
        "providers",
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
