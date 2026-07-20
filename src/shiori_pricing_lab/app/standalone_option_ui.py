"""Streamlit page: standalone bond-option trader workbench (Issue #97, PR B;
Issue #125 benchmark comparison / implied PRICE_VOL extension; Issue #101
current-run export).

A thin trader UI over the merged headless workflow
(``standalone_option_workbench.price_standalone_option_case`` /
``price_standalone_option_case_with_benchmark``). It prices **one standalone
bond-option leg** from a local JSON case -- an editable copy of the bundled
sanitized-synthetic example, or an uploaded ``.json`` file -- and displays the
result verbatim. Optionally, in benchmark mode, it also evaluates that same
priced case against one explicit benchmark JSON case (comparison + implied
``PRICE_VOL`` calibration). After a real result exists, it offers two
current-run export downloads (JSON / Markdown) of that same display context.

**This page performs no pricing, comparison, calibration, resolver, solver,
Black-76, curve, discounting, accrual, volatility, error-mapping, or fallback
logic.** Its only execution calls are ``price_standalone_option_case`` and
``price_standalone_option_case_with_benchmark`` -- it never imports or calls
the builder, request constructor, pricing engine, comparison function,
calibration function, resolver, solver, guard, curve/volatility helpers,
provider, or QuantLib adapter directly. Every displayed value is a verbatim
read of the headless workflow's display context. It reads no system clock.

**Current-run export (Issue #101).** Once a real workflow result exists (in
either mode), an "Export current run" section offers two
``st.download_button`` controls -- "Download current run JSON" and
"Download current run Markdown" -- with fixed file names
(``shiori_standalone_run.json`` / ``shiori_standalone_run.md``) and explicit
MIME types. Both call only the bounded, pure
``standalone_option_run_export.render_standalone_run_as_json`` /
``render_standalone_run_as_markdown`` helpers on the **already-computed**
``display`` dict -- pricing, comparison, and calibration are never called
again for export, no server-side file is written, and no session/report
history is added. ``on_click="ignore"`` (this installed Streamlit version's
supported mechanism) avoids an unnecessary app rerun on download.

**Input surface (Issue #97 PR B binding decision).** An explicit input-source
selector avoids any hidden precedence/fallback:

- *Editable example JSON*: a ``st.text_area`` prefilled from
  ``examples/standalone_option_case.json``;
- *Upload local JSON*: a ``.json``-only ``st.file_uploader``, decoded strictly
  as UTF-8. When no file is uploaded the page shows a clear message and does
  **not** price -- it never silently falls back to the textarea or the
  bundled example.

A separate optional ``retrieved_at`` text input is caller-supplied workbench
provenance only: an empty string maps to ``None``; non-empty text is passed
verbatim; it is never read from a clock, normalized, validated, persisted, or
used to overwrite ``source_as_of``.

**Benchmark mode (Issue #125).** An explicit ``Mode`` selector offers
*Price only* (unchanged default behavior) and *Price + benchmark comparison /
implied PRICE_VOL*. Benchmark mode adds: its own, entirely separate
editable/uploaded benchmark JSON input (never merged into the pricing-case
JSON); an explicit ``active quote side`` selector with **no preselected
value** (``st.selectbox(..., index=None)``) -- the page refuses to run the
benchmark evaluation until a side is explicitly chosen; and the same single
"Price standalone option" execution button drives both the pricing call and,
in benchmark mode, the comparison + calibration call.

**No bundled benchmark example (Codex P1 review of PR #126).** Unlike the
pricing case, this page ships **no** bundled benchmark JSON. The existing
``BLIBenchmarkQuote`` contract only accepts ``source_type`` ``BLOOMBERG`` or
``VENDOR`` -- there is no ``SYNTHETIC``/``MANUAL``/``TEST`` member, and this
page does not add one. A bundled example claiming ``BLOOMBERG`` while
carrying an engineered synthetic premium would misrepresent the workbench as
Bloomberg-validated when it is not. The "paste benchmark JSON" textarea
therefore always starts **empty**, with placeholder text instructing the
trader to paste one actually-observed benchmark quote or use the upload
path instead; there is no ``_load_example_benchmark_text`` and no benchmark
counterpart to ``examples/standalone_option_case.json``.

**Failure display.** Only the expected local-input exceptions
(``json.JSONDecodeError``, ``UnicodeDecodeError``, ``TypeError``,
``ValueError``) are caught at the button boundary and rendered verbatim via
``st.error(str(exc))`` -- never classified, rewritten, suppressed, converted
into a ``PricingResult``, or swallowed by a broad ``except``. A pricing
``FAILED`` result is shown as a clear banner with the full structured errors
(code, message, complete detail) and no premium/intermediate metrics and no
fabricated PV. A comparison ``NON_COMPARABLE`` or a calibration ``FAILED``
outcome renders its existing reason, diagnostic note, and (when the solver
actually ran) full solver diagnostics -- any field the existing result leaves
``None`` is never replaced by a fabricated ``0``/placeholder value; it is
simply not rendered as a metric. The model fair premium is never labeled a
client quote.

**Issue #6 Phase 4: Bloomberg DAPI bond-quote source.** An explicit
"Bond quote source" selector -- *Case JSON* (unchanged default) or
*Bloomberg DAPI* -- with no hidden precedence. *Case JSON* preserves the
existing behavior exactly, including the existing benchmark active-side
selector, and never imports or calls anything Bloomberg-related.
*Bloomberg DAPI* requires an explicit security, an explicit quote side
(``index=None``, no preselected value), and an explicit ``source_as_of``,
then calls only the new headless
``price_standalone_option_case_with_bloomberg_quote`` /
``_and_benchmark`` workflows; in benchmark mode the same quote side drives
both the Bloomberg price field and the comparison/calibration
``active_quote_side`` -- the page shows no separate active-side selector in
this mode. The page states plainly that the case JSON's ``bond_quote`` is
replaced and never used as a fallback. A Bloomberg failure
(``BLIBloombergDapiError``, caught alongside the existing local-input
exceptions -- never a broad ``except Exception``) renders the exact error
and prices nothing; there is no stale previous result and no fabricated
zero. This page adds no background refresh, polling, caching, auto-run,
session history, credentials UI, or Bloomberg connection settings.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from shiori_pricing_lab.app.standalone_option_run_export import (
    render_standalone_run_as_json,
    render_standalone_run_as_markdown,
)
from shiori_pricing_lab.app.standalone_option_workbench import (
    price_standalone_option_case,
    price_standalone_option_case_with_benchmark,
    price_standalone_option_case_with_bloomberg_quote,
    price_standalone_option_case_with_bloomberg_quote_and_benchmark,
)
from shiori_pricing_lab.data.bloomberg_bond_quote import BLIBloombergDapiError

_EXPORT_JSON_FILE_NAME = "shiori_standalone_run.json"
_EXPORT_MARKDOWN_FILE_NAME = "shiori_standalone_run.md"
_EXPORT_JSON_MIME = "application/json"
_EXPORT_MARKDOWN_MIME = "text/markdown; charset=utf-8"

_EXAMPLE_PATH = (
    Path(__file__).resolve().parents[3] / "examples" / "standalone_option_case.json"
)

_EDITABLE_SOURCE = "Editable example JSON"
_UPLOAD_SOURCE = "Upload local JSON"

# No bundled benchmark example exists (Codex P1 review of PR #126): the
# benchmark textarea always starts empty, never prefilled from a file.
_BENCHMARK_PASTE_SOURCE = "Paste benchmark JSON"
_BENCHMARK_JSON_PLACEHOLDER = (
    "Paste one actually-observed benchmark quote JSON here "
    '(e.g. {"benchmark_id": "...", "source_type": "BLOOMBERG", "source_system": "...", '
    '"source_as_of": "...", "retrieved_at": "...", "quote_side": "...", '
    '"premium_per_100": ..., "total_premium": ..., "currency": "...", "product_id": "...", '
    '"snapshot_id": "...", "underlying_id": "...", "source_reference": "...", "notes": null}) '
    "-- or upload a benchmark JSON file instead. This field is never prefilled."
)

_MODE_PRICE_ONLY = "Price only"
_MODE_PRICE_AND_BENCHMARK = "Price + benchmark comparison / implied PRICE_VOL"

_QUOTE_SIDE_OPTIONS = ("BID", "MID", "OFFER")

_BOND_QUOTE_SOURCE_CASE_JSON = "Case JSON"
_BOND_QUOTE_SOURCE_BLOOMBERG = "Bloomberg DAPI"

_COMPARISON_STATUS_RENDERERS = {
    "PASS": st.success,
    "WARNING": st.warning,
    "FAIL": st.error,
    "NON_COMPARABLE": st.error,
}
_CALIBRATION_STATUS_RENDERERS = {
    "SUCCESS": st.success,
    "FAILED": st.error,
}


def _load_example_case_text() -> str:
    """Return the bundled sanitized-synthetic example JSON text, verbatim."""

    return _EXAMPLE_PATH.read_text(encoding="utf-8")


def _retrieved_at_or_none(text: str | None) -> str | None:
    """Map the optional ``retrieved_at`` text input to its workflow value.

    An empty string becomes ``None``; any non-empty text is returned verbatim
    (no stripping, normalization, or validation). Never reads a clock.
    """

    return text or None


def _decode_uploaded_json_text(raw: bytes) -> str:
    """Strictly decode uploaded file bytes as UTF-8 (raises on invalid UTF-8)."""

    return raw.decode("utf-8")


def _render_pricing_result(display: dict) -> None:
    """Render the headless workflow's display context verbatim.

    The reproducibility/engine context is shown for both SUCCESS and FAILED.
    Premium/intermediate metrics are shown only for a non-failed result, and
    a FAILED result shows the full structured errors with no replacement
    values.
    """

    status = display["status"]

    st.subheader("Context")
    st.write(
        {
            "product_id": display["product_id"],
            "product_type": display["product_type"],
            "valuation_date": display["valuation_date"],
            "result_currency": display["result_currency"],
            "status": status,
            "method": display["method"],
            "source_system": display["source_system"],
            "source_as_of": display["source_as_of"],
            "retrieved_at": display["retrieved_at"],
            "snapshot_id": display["snapshot_id"],
            "engine_name": display["engine_name"],
            "engine_version": display["engine_version"],
        }
    )

    if status == "FAILED":
        st.error(
            "Pricing FAILED — no model fair premium or intermediate values are shown."
        )
        st.subheader("Errors")
        for error in display["errors"]:
            st.markdown(f"- **{error['code']}**: {error['message']}")
            if error["detail"]:
                st.json(error["detail"])
        return

    st.success(f"Pricing {status}")

    premium_left, premium_right = st.columns(2)
    with premium_left:
        st.metric(
            "Model fair premium per 100",
            f"{display['model_fair_premium_per_100']:.6f}",
        )
    with premium_right:
        st.metric(
            "Total notional model fair premium",
            f"{display['total_notional_model_fair_premium']:.6f}",
        )

    detail_left, detail_right = st.columns(2)
    with detail_left:
        st.metric(
            "Forward clean price per 100",
            f"{display['forward_clean_price_per_100']:.6f}",
        )
        st.metric(
            "Effective reporting-date discount factor",
            f"{display['effective_reporting_date_discount_factor']:.6f}",
        )
    with detail_right:
        st.metric("Black-76 PV per 100", f"{display['black76_pv_per_100']:.6f}")
        st.metric("Time to expiry (years)", f"{display['time_to_expiry_year_fraction']:.6f}")

    st.write(f"**Excluded components:** {', '.join(display['excluded_components'])}")
    with st.expander("Assumptions"):
        st.json(display["assumptions"])


def _render_metric_or_caption(label: str, value: float | None, *, note: str) -> None:
    """Render one ``st.metric`` for ``value``, or a plain caption when it is ``None``.

    Never substitutes a fabricated ``0``/``N/A`` numeric placeholder: a
    ``None`` value (a structured outcome the existing result already left
    unset -- e.g. a theoretical-bound solver failure) is shown as an honest
    caption explaining why, using the caller-supplied ``note`` (already an
    existing verbatim field on the result, e.g. ``alignment_note`` /
    ``diagnostic_note``), never a number.
    """

    if value is None:
        st.caption(f"{label}: not available — {note}")
    else:
        st.metric(label, f"{value:.6f}")


def _render_benchmark_result(display: dict) -> None:
    """Render the Benchmark, Comparison, and Calibration sections verbatim.

    Only called in benchmark mode. Every value is a direct read from
    ``display["benchmark"]`` / ``["comparison"]`` / ``["calibration"]``
    (:func:`standalone_option_workbench.prepare_standalone_benchmark_display`)
    -- no residual, comparison, or calibration math happens here. Model fair
    premium, benchmark premium, comparison residual/status, and implied
    ``PRICE_VOL`` are rendered in distinct, separately labeled sections so
    they are never conflated.
    """

    benchmark = display["benchmark"]
    comparison = display["comparison"]
    calibration = display["calibration"]

    st.subheader("Benchmark")
    st.write(
        {
            "benchmark_id": benchmark["benchmark_id"],
            "source_type": benchmark["source_type"],
            "source_system": benchmark["source_system"],
            "source_as_of": benchmark["source_as_of"],
            "retrieved_at": benchmark["retrieved_at"],
            "quote_side": benchmark["quote_side"],
            "currency": benchmark["currency"],
            "product_id": benchmark["product_id"],
            "snapshot_id": benchmark["snapshot_id"],
            "underlying_id": benchmark["underlying_id"],
            "source_reference": benchmark["source_reference"],
            "notes": benchmark["notes"],
        }
    )
    benchmark_left, benchmark_right = st.columns(2)
    with benchmark_left:
        st.metric("Benchmark premium per 100", f"{benchmark['premium_per_100']:.6f}")
    with benchmark_right:
        st.metric("Benchmark total premium", f"{benchmark['total_premium']:.6f}")

    st.subheader("Comparison")
    comparison_status = comparison["status"]
    _COMPARISON_STATUS_RENDERERS[comparison_status](
        f"Comparison {comparison_status} — {comparison['reason']}"
    )
    st.write(comparison["alignment_note"])

    comparison_left, comparison_right = st.columns(2)
    with comparison_left:
        _render_metric_or_caption(
            "Model fair premium per 100 (comparison)",
            comparison["model_fair_premium_per_100"],
            note=comparison["alignment_note"],
        )
        _render_metric_or_caption(
            "Signed residual per 100",
            comparison["signed_residual_per_100"],
            note=comparison["alignment_note"],
        )
    with comparison_right:
        st.metric(
            "Benchmark premium per 100 (comparison)",
            f"{comparison['benchmark_premium_per_100']:.6f}",
        )
        _render_metric_or_caption(
            "Relative residual",
            comparison["relative_residual"],
            note=comparison["alignment_note"],
        )
    st.caption(
        f"pass_threshold={comparison['pass_threshold']}, "
        f"fail_threshold={comparison['fail_threshold']}, "
        f"near_zero_threshold_per_100={comparison['near_zero_threshold_per_100']}"
    )

    st.subheader("Calibration")
    calibration_status = calibration["status"]
    _CALIBRATION_STATUS_RENDERERS[calibration_status](
        f"Calibration {calibration_status} — {calibration['reason']}"
    )
    st.write(calibration["diagnostic_note"])
    if calibration["resolution_error_type"] is not None:
        st.error(
            f"{calibration['resolution_error_type']}: {calibration['resolution_error_message']}"
        )

    calibration_left, calibration_right = st.columns(2)
    with calibration_left:
        _render_metric_or_caption(
            "Implied PRICE_VOL",
            calibration["implied_price_vol"],
            note=calibration["diagnostic_note"],
        )
        _render_metric_or_caption(
            "Model premium per 100 (solver)",
            calibration["model_premium_per_100"],
            note=calibration["diagnostic_note"],
        )
    with calibration_right:
        _render_metric_or_caption(
            "Premium residual per 100 (solver)",
            calibration["premium_residual_per_100"],
            note=calibration["diagnostic_note"],
        )
        if calibration["solver_status"] is not None:
            st.write(
                f"Solver status/reason: {calibration['solver_status']} / "
                f"{calibration['solver_reason']}"
            )

    if calibration["solver_status"] is not None:
        with st.expander("Solver diagnostics"):
            st.json(
                {
                    "lower_price_vol": calibration["lower_price_vol"],
                    "upper_price_vol": calibration["upper_price_vol"],
                    "premium_tolerance_per_100": calibration["premium_tolerance_per_100"],
                    "price_vol_tolerance": calibration["price_vol_tolerance"],
                    "max_iterations": calibration["max_iterations"],
                    "iterations": calibration["iterations"],
                    "final_bracket_lower_price_vol": calibration["final_bracket_lower_price_vol"],
                    "final_bracket_upper_price_vol": calibration["final_bracket_upper_price_vol"],
                    "diagnostic_note": calibration["solver_diagnostic_note"],
                }
            )


def _render_live_bloomberg_quote(display: dict) -> None:
    """Render the live Bloomberg quote provenance section, verbatim.

    Only called in Bloomberg-DAPI bond-quote-source mode. Shown as its own
    section, distinct from the model fair premium, the benchmark premium,
    and the implied ``PRICE_VOL`` -- every value is a direct read of
    ``display["live_bloomberg_quote"]``
    (:func:`standalone_option_workbench.prepare_live_bloomberg_quote_display`),
    never recomputed or reinterpreted here.
    """

    st.subheader("Live Bloomberg Quote")
    st.write(display["live_bloomberg_quote"])


def _render_export_section(display: dict) -> None:
    """Render the "Export current run" downloads for the already-computed ``display``.

    Only called after a real workflow result exists. Both downloads are
    produced by the pure, bounded export helper reading ``display`` alone --
    pricing, comparison, and calibration are not invoked again, no
    server-side file is written, and nothing is persisted to session state.
    ``on_click="ignore"`` avoids an unnecessary rerun of the whole page when
    the trader clicks a download button.
    """

    st.subheader("Export current run")
    export_left, export_right = st.columns(2)
    with export_left:
        st.download_button(
            "Download current run JSON",
            data=render_standalone_run_as_json(display),
            file_name=_EXPORT_JSON_FILE_NAME,
            mime=_EXPORT_JSON_MIME,
            on_click="ignore",
        )
    with export_right:
        st.download_button(
            "Download current run Markdown",
            data=render_standalone_run_as_markdown(display),
            file_name=_EXPORT_MARKDOWN_FILE_NAME,
            mime=_EXPORT_MARKDOWN_MIME,
            on_click="ignore",
        )


def render_standalone_option_workbench_page() -> None:
    st.header("Standalone Bond Option Workbench")
    st.caption(
        "Prices the standalone bond-option leg only (the deposit leg and full "
        "structured-product value are excluded). The bundled example is "
        "sanitized synthetic market-shaped data — not Bloomberg or real-market "
        "validation."
    )

    mode = st.radio("Mode", [_MODE_PRICE_ONLY, _MODE_PRICE_AND_BENCHMARK])
    benchmark_mode = mode == _MODE_PRICE_AND_BENCHMARK

    bond_quote_source = st.radio(
        "Bond quote source", [_BOND_QUOTE_SOURCE_CASE_JSON, _BOND_QUOTE_SOURCE_BLOOMBERG]
    )
    bloomberg_mode = bond_quote_source == _BOND_QUOTE_SOURCE_BLOOMBERG

    input_source = st.radio("Input source", [_EDITABLE_SOURCE, _UPLOAD_SOURCE])

    textarea_text: str | None = None
    uploaded_file = None
    if input_source == _EDITABLE_SOURCE:
        textarea_text = st.text_area(
            "Standalone option case JSON",
            value=_load_example_case_text(),
            height=400,
        )
    else:
        uploaded_file = st.file_uploader(
            "Upload standalone option case JSON (.json, UTF-8)",
            type=["json"],
        )
        if uploaded_file is None:
            st.info("Upload a .json file to price. No file is loaded yet.")

    retrieved_at_text = st.text_input(
        "retrieved_at (optional workbench provenance; kept separate from source-as-of)",
        value="",
    )

    bloomberg_security_text: str | None = None
    bloomberg_quote_side: str | None = None
    bloomberg_source_as_of_text: str | None = None
    if bloomberg_mode:
        st.subheader("Bloomberg DAPI bond quote")
        st.caption(
            "The case JSON's bond_quote will be replaced by one live Bloomberg DAPI "
            "quote and is never used as a fallback -- not on failure, not merged "
            "field-by-field."
        )
        bloomberg_security_text = st.text_input("Bloomberg security", value="")
        bloomberg_quote_side = st.selectbox(
            "Quote side",
            _QUOTE_SIDE_OPTIONS,
            index=None,
            placeholder="Select a quote side (required — no default)",
        )
        bloomberg_source_as_of_text = st.text_input(
            "source_as_of (must equal the case JSON's as_of_timestamp exactly)",
            value="",
        )

    benchmark_input_source: str | None = None
    benchmark_textarea_text: str | None = None
    benchmark_uploaded_file = None
    active_quote_side: str | None = None
    if benchmark_mode:
        st.subheader("Benchmark input")
        st.caption(
            "No bundled benchmark example is provided. Paste one actually-observed "
            "benchmark quote JSON (BLOOMBERG or VENDOR source) or upload a benchmark "
            "JSON file -- this page never prefills or fabricates a benchmark quote."
        )
        benchmark_input_source = st.radio(
            "Benchmark input source", [_BENCHMARK_PASTE_SOURCE, _UPLOAD_SOURCE]
        )
        if benchmark_input_source == _BENCHMARK_PASTE_SOURCE:
            benchmark_textarea_text = st.text_area(
                "Standalone option benchmark JSON",
                value="",
                height=250,
                placeholder=_BENCHMARK_JSON_PLACEHOLDER,
            )
        else:
            benchmark_uploaded_file = st.file_uploader(
                "Upload standalone option benchmark JSON (.json, UTF-8)",
                type=["json"],
            )
            if benchmark_uploaded_file is None:
                st.info("Upload a benchmark .json file to compare. No file is loaded yet.")

        if bloomberg_mode:
            st.caption(
                "Active quote side: driven by the Bloomberg quote side above -- "
                "no separate selector in Bloomberg DAPI mode."
            )
        else:
            active_quote_side = st.selectbox(
                "Active quote side",
                _QUOTE_SIDE_OPTIONS,
                index=None,
                placeholder="Select a quote side (required — no default)",
            )

    if not st.button("Price standalone option"):
        return

    retrieved_at = _retrieved_at_or_none(retrieved_at_text)

    try:
        if input_source == _UPLOAD_SOURCE:
            if uploaded_file is None:
                st.warning(
                    "No file uploaded — nothing to price. Upload a .json file first."
                )
                return
            case_text = _decode_uploaded_json_text(uploaded_file.getvalue())
        else:
            case_text = textarea_text

        if bloomberg_mode:
            if not bloomberg_security_text or not bloomberg_security_text.strip():
                st.warning(
                    "No Bloomberg security entered — nothing to price. Enter a "
                    "security first."
                )
                return
            if bloomberg_quote_side is None:
                st.warning(
                    "No quote side selected — nothing to price. Select BID, MID, "
                    "or OFFER first."
                )
                return
            if not bloomberg_source_as_of_text or not bloomberg_source_as_of_text.strip():
                st.warning(
                    "No source_as_of entered — nothing to price. Enter the case "
                    "JSON's as_of_timestamp first."
                )
                return

        if benchmark_mode:
            if not bloomberg_mode and active_quote_side is None:
                st.warning(
                    "No active quote side selected — nothing to compare. Select "
                    "BID, MID, or OFFER first."
                )
                return
            if benchmark_input_source == _UPLOAD_SOURCE:
                if benchmark_uploaded_file is None:
                    st.warning(
                        "No benchmark file uploaded — nothing to compare. Upload a "
                        ".json file first."
                    )
                    return
                benchmark_text = _decode_uploaded_json_text(benchmark_uploaded_file.getvalue())
            else:
                benchmark_text = benchmark_textarea_text

        if bloomberg_mode and benchmark_mode:
            (
                _request,
                _result,
                _live_quote,
                _benchmark,
                _comparison,
                _calibration,
                display,
            ) = price_standalone_option_case_with_bloomberg_quote_and_benchmark(
                case_text,
                benchmark_text,
                bloomberg_security=bloomberg_security_text,
                quote_side=bloomberg_quote_side,
                source_as_of=bloomberg_source_as_of_text,
                retrieved_at=retrieved_at,
            )
        elif bloomberg_mode:
            (
                _request,
                _result,
                _live_quote,
                display,
            ) = price_standalone_option_case_with_bloomberg_quote(
                case_text,
                bloomberg_security=bloomberg_security_text,
                quote_side=bloomberg_quote_side,
                source_as_of=bloomberg_source_as_of_text,
                retrieved_at=retrieved_at,
            )
        elif benchmark_mode:
            (
                _request,
                _result,
                _benchmark,
                _comparison,
                _calibration,
                display,
            ) = price_standalone_option_case_with_benchmark(
                case_text,
                benchmark_text,
                active_quote_side=active_quote_side,
                retrieved_at=retrieved_at,
            )
        else:
            _request, _result, display = price_standalone_option_case(
                case_text, retrieved_at=retrieved_at
            )
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        TypeError,
        ValueError,
        BLIBloombergDapiError,
    ) as exc:
        st.error(str(exc))
        return

    _render_pricing_result(display)
    if bloomberg_mode:
        _render_live_bloomberg_quote(display)
    if benchmark_mode:
        _render_benchmark_result(display)
    _render_export_section(display)
