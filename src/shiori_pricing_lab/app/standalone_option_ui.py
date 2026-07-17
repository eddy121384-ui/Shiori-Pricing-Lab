"""Streamlit page: standalone bond-option trader workbench (Issue #97, PR B).

A thin trader UI over the merged headless workflow
(``standalone_option_workbench.price_standalone_option_case``). It prices
**one standalone bond-option leg** from a local JSON case -- an editable copy
of the bundled sanitized-synthetic example, or an uploaded ``.json`` file --
and displays the result verbatim.

**This page performs no pricing, validation, curve, discounting, accrual,
volatility, error-mapping, or fallback logic.** Its only execution call is
``price_standalone_option_case``; it never imports or calls the builder,
request constructor, pricing engine, guard, curve/volatility helpers,
provider, or QuantLib adapter directly. Every displayed value is a verbatim
read of the headless workflow's display context. It reads no system clock.

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

**Failure display.** Only the expected local-input exceptions
(``json.JSONDecodeError``, ``UnicodeDecodeError``, ``TypeError``,
``ValueError``) are caught at the button boundary and rendered verbatim via
``st.error(str(exc))`` -- never classified, rewritten, suppressed, converted
into a ``PricingResult``, or swallowed by a broad ``except``. A pricing
``FAILED`` result is shown as a clear banner with the full structured errors
(code, message, complete detail) and no premium/intermediate metrics and no
fabricated PV. The model fair premium is never labeled a client quote.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from shiori_pricing_lab.app.standalone_option_workbench import price_standalone_option_case

_EXAMPLE_PATH = (
    Path(__file__).resolve().parents[3] / "examples" / "standalone_option_case.json"
)

_EDITABLE_SOURCE = "Editable example JSON"
_UPLOAD_SOURCE = "Upload local JSON"


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


def render_standalone_option_workbench_page() -> None:
    st.header("Standalone Bond Option Workbench")
    st.caption(
        "Prices the standalone bond-option leg only (the deposit leg and full "
        "structured-product value are excluded). The bundled example is "
        "sanitized synthetic market-shaped data — not Bloomberg or real-market "
        "validation."
    )

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
        _request, _result, display = price_standalone_option_case(
            case_text, retrieved_at=retrieved_at
        )
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
        st.error(str(exc))
        return

    _render_pricing_result(display)
