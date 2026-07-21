"""Streamlit page: standalone bond-option trader workbench.

A thin white trader dashboard over the merged headless workflow
(``standalone_option_workbench``). It prices **one standalone bond-option
leg** from a local JSON case -- an editable copy of the bundled
sanitized-synthetic example, or an uploaded ``.json`` file -- and displays
the result verbatim. Optionally it evaluates that same priced case against
one explicit benchmark JSON case (comparison + implied ``PRICE_VOL``), and
optionally it refreshes the bond quote from Bloomberg DAPI before pricing.
After a real result exists, it offers current-run JSON / Markdown exports of
that same display context.

**This page performs no pricing, comparison, calibration, resolver, solver,
Black-76, curve, discounting, accrual, volatility, error-mapping, or fallback
logic.** Its only execution calls are the four headless workflow functions
imported below -- it never imports or calls the builder, request constructor,
pricing engine, comparison function, calibration function, resolver, solver,
guard, curve/volatility helpers, market-data provider, or the QuantLib
adapter directly. Every displayed value is a verbatim read of the headless
workflow's display context. It reads no system clock.

**Trader overrides (seven values, one bounded pure helper).** The seven
routinely-traded values are surfaced as ordinary Streamlit widgets, prefilled
from the selected case, so the trader never has to open raw JSON to change
them: ``bond_option.option_type`` / ``.position`` / ``.strike_price`` /
``.notional``, ``forward_clean_price_input.forward_clean_price_per_100`` /
``.quote_side``, and ``volatility_input.volatility``.
:func:`apply_trader_overrides` copies the envelope and replaces exactly those
seven values with explicit named parameters -- it mutates neither the original
mapping nor any nested mapping, and it is deliberately **not** a generic
path-patching or form-generation framework. Every other envelope value is
carried through untouched. The overlay is applied strictly before the case
reaches any workflow, so all four workflow paths receive the overlaid case.

Prefill is display-only and never a pricing fallback: when the selected case
cannot be read as a JSON object, or does not carry all seven values with a
usable type, the override form is simply not shown and the case text is
handed to the workflow **unchanged**, so the real envelope/schema error
surfaces verbatim.

**Input surface.** An explicit input-source selector avoids any hidden
precedence/fallback, and lives inside the "Advanced case input" expander:

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

**Benchmark mode.** An explicit ``Mode`` selector offers *Price only* and
*Price + benchmark comparison / implied PRICE_VOL*. Benchmark mode adds its
own, entirely separate editable/uploaded benchmark JSON input (never merged
into the pricing-case JSON) and an explicit active quote side with **no
preselected value** (``index=None``). This page ships **no** bundled
benchmark example: the existing ``BLIBenchmarkQuote`` contract only accepts
``source_type`` ``BLOOMBERG`` or ``VENDOR``, and a bundled file claiming
``BLOOMBERG`` while carrying an engineered synthetic premium would
misrepresent the workbench as Bloomberg-validated when it is not.

**Bloomberg DAPI mode, acquisition-time contract.** An explicit "Bond quote
source" selector -- *Case JSON* (unchanged default) or *Bloomberg DAPI* --
with no hidden precedence. Bloomberg mode requires an explicit security and
an explicit quote side (``index=None``, no preselected value); the case's own
expected ISIN is shown next to the security field **before** retrieval, and a
``Verified ISIN`` is shown only after a successful result. Nothing on the page
shows or implies a live status before a successful retrieval. It calls only
the headless Bloomberg workflows, which capture the Shiori acquisition
timestamp internally (no clock read in this page) and reject a case whose
``valuation_date`` does not match the acquisition date -- before any pricing.
There is no cache, polling, background refresh, stale reuse, or field-by-field
quote merge.

**Failure display.** Only the expected local-input exceptions
(``json.JSONDecodeError``, ``UnicodeDecodeError``, ``TypeError``,
``ValueError``) and ``BLIBloombergDapiError`` are caught at the button
boundary and rendered verbatim via ``st.error(str(exc))`` -- never classified,
rewritten, suppressed, converted into a ``PricingResult``, or swallowed by a
broad ``except``. A pricing ``FAILED`` result is shown as a clear banner with
the full structured errors (code, message, complete detail) and no
premium/intermediate metrics and no fabricated PV. A comparison
``NON_COMPARABLE`` or a calibration ``FAILED`` outcome renders its existing
reason, diagnostic note, and (when the solver actually ran) full solver
diagnostics -- any field the existing result leaves ``None`` is never replaced
by a fabricated ``0``/placeholder value.

**Rendering trust boundary.** ``unsafe_allow_html`` is used only for the
static stylesheet and static section labels defined in this module. No
case-derived or uploaded value is ever interpolated into HTML: every such
value is rendered through an escaping Streamlit text API.
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

# No bundled benchmark example exists: the benchmark textarea always starts
# empty, never prefilled from a file.
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
_OPTION_TYPE_OPTIONS = ("CALL", "PUT")
_POSITION_OPTIONS = ("BUY", "SELL")

_BOND_QUOTE_SOURCE_CASE_JSON = "Case JSON"
_BOND_QUOTE_SOURCE_BLOOMBERG = "Bloomberg DAPI"

_BLOOMBERG_SECURITY_LABEL = "Bloomberg security (include Yellow Key)"
_BLOOMBERG_SECURITY_EXAMPLE = "Example: 91282CQX Govt"

_MISSING_VALUE_TEXT = "--"

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

# Static stylesheet only -- never interpolated with case-derived data. Styles
# the white institutional layout: neutral page tint, card panels, and the
# small-caps section labels used by the input and header cards.
_WORKBENCH_CSS = """
<style>
div[data-testid="stMainBlockContainer"] { padding-top: 2.2rem; max-width: 1500px; }
.shiori-title {
    font-size: 1.45rem; font-weight: 700; color: #0f172a;
    letter-spacing: -0.01em; margin-bottom: 0.15rem;
}
.shiori-section {
    font-size: 0.72rem; font-weight: 700; color: #475569;
    letter-spacing: 0.09em; text-transform: uppercase;
    border-bottom: 1px solid #e2e8f0;
    padding-bottom: 0.45rem; margin-bottom: 0.85rem;
}
.shiori-field-label {
    font-size: 0.7rem; font-weight: 600; color: #64748b;
    letter-spacing: 0.05em; text-transform: uppercase; margin-bottom: 0.1rem;
}
div[data-testid="stMetricValue"] { font-size: 1.35rem; color: #0f172a; }
div[data-testid="stMetricLabel"] { font-size: 0.72rem; color: #64748b; }
</style>
"""


def _inject_workbench_css() -> None:
    """Write the static stylesheet once per render. Contains no dynamic data."""

    st.markdown(_WORKBENCH_CSS, unsafe_allow_html=True)


def _section_label(text: str) -> None:
    """Render one static small-caps card section label."""

    st.markdown(f'<div class="shiori-section">{text}</div>', unsafe_allow_html=True)


def _field(label: str, value: object) -> None:
    """Render one read-only label/value pair.

    ``label`` is a static string from this module and is styled as HTML;
    ``value`` may be case-derived or uploaded, so it is always rendered
    through ``st.text``, which escapes it and never interprets HTML or
    Markdown. A ``None``/absent value is shown as a plain placeholder dash,
    never as a fabricated zero.
    """

    st.markdown(f'<div class="shiori-field-label">{label}</div>', unsafe_allow_html=True)
    st.text(_MISSING_VALUE_TEXT if value is None else str(value))


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


# --- Trader overrides: read-for-prefill, then one bounded pure overlay ---------


def _parse_case_for_prefill(case_text: str | None) -> dict | None:
    """Return the case mapping for prefill/header display, or ``None``.

    Display-only and deliberately non-fatal: an absent, malformed, or
    non-object case yields ``None``, which suppresses the header and the
    override form. It never substitutes the bundled example and never
    changes what is handed to the workflow -- an unreadable case is still
    passed through unchanged so its real error surfaces at execution.
    """

    if not case_text:
        return None
    try:
        envelope = json.loads(case_text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return envelope if isinstance(envelope, dict) else None


def _trader_override_prefill(envelope: dict) -> dict | None:
    """Return the seven prefill values, or ``None`` if the case lacks any of them.

    Reads only the seven approved locations. Returns ``None`` -- suppressing
    the override form entirely -- unless every one of them is present with a
    usable type, so no widget default is ever invented for a case that does
    not actually carry that value.
    """

    bond_option = envelope.get("bond_option")
    forward_input = envelope.get("forward_clean_price_input")
    volatility_input = envelope.get("volatility_input")
    if not all(
        isinstance(section, dict)
        for section in (bond_option, forward_input, volatility_input)
    ):
        return None

    prefill = {
        "option_type": bond_option.get("option_type"),
        "position": bond_option.get("position"),
        "strike_price": bond_option.get("strike_price"),
        "notional": bond_option.get("notional"),
        "forward_clean_price_per_100": forward_input.get("forward_clean_price_per_100"),
        "quote_side": forward_input.get("quote_side"),
        "volatility": volatility_input.get("volatility"),
    }

    if prefill["option_type"] not in _OPTION_TYPE_OPTIONS:
        return None
    if prefill["position"] not in _POSITION_OPTIONS:
        return None
    if prefill["quote_side"] not in _QUOTE_SIDE_OPTIONS:
        return None
    for key in ("strike_price", "notional", "forward_clean_price_per_100", "volatility"):
        value = prefill[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
    return prefill


def apply_trader_overrides(
    envelope: dict,
    *,
    option_type: str,
    position: str,
    strike_price: float,
    notional: float,
    forward_clean_price_per_100: float,
    quote_side: str,
    volatility: float,
) -> dict:
    """Return a copy of ``envelope`` with exactly the seven trader values replaced.

    Bounded and pure: builds a new top-level mapping and new
    ``bond_option`` / ``forward_clean_price_input`` / ``volatility_input``
    mappings, so neither ``envelope`` nor any mapping nested inside it is
    mutated. Every other top-level key and every other nested key is carried
    through unchanged and compares equal to the original. Values are copied
    verbatim -- no coercion, validation, normalization, or unit conversion
    happens here; each remains the existing typed constructor's business.

    This is intentionally not a generic path-patching helper: the seven
    approved locations are named explicitly as keyword parameters, and adding
    an eighth requires an explicit change here.
    """

    return {
        **envelope,
        "bond_option": {
            **envelope["bond_option"],
            "option_type": option_type,
            "position": position,
            "strike_price": strike_price,
            "notional": notional,
        },
        "forward_clean_price_input": {
            **envelope["forward_clean_price_input"],
            "forward_clean_price_per_100": forward_clean_price_per_100,
            "quote_side": quote_side,
        },
        "volatility_input": {
            **envelope["volatility_input"],
            "volatility": volatility,
        },
    }


# --- Read-only instrument header ----------------------------------------------


def _instrument_header_values(envelope: dict) -> dict:
    """Return the read-only instrument header values for ``envelope``.

    A plain, tolerant read of values the case already carries -- issuer,
    coupon, maturity, and reference currency come from the
    ``bond_reference_data_universe`` entry whose ``isin`` matches
    ``bond_option.underlying_isin``. Any value the case does not carry is
    ``None`` and renders as a dash. This resolves nothing for pricing: the
    request builder performs its own reference resolution, unchanged.
    """

    bond_option = envelope.get("bond_option")
    bond_option = bond_option if isinstance(bond_option, dict) else {}
    bond_quote = envelope.get("bond_quote")
    bond_quote = bond_quote if isinstance(bond_quote, dict) else {}

    underlying_isin = bond_option.get("underlying_isin")
    universe = envelope.get("bond_reference_data_universe")
    universe = universe if isinstance(universe, list) else []
    reference = next(
        (
            record
            for record in universe
            if isinstance(record, dict) and record.get("isin") == underlying_isin
        ),
        {},
    )

    return {
        "issuer": reference.get("issuer"),
        "coupon": reference.get("coupon"),
        "maturity": reference.get("maturity_date"),
        "currency": bond_option.get("currency") or reference.get("currency"),
        "underlying_isin": underlying_isin,
        "valuation_date": envelope.get("valuation_date"),
        "case_quote_side": bond_quote.get("quote_side"),
        "case_clean_price_per_100": bond_quote.get("clean_price_per_100"),
    }


def _render_instrument_header(envelope: dict | None) -> None:
    """Render the read-only instrument header derived from the selected case."""

    with st.container(border=True):
        _section_label("Instrument")
        if envelope is None:
            st.caption(
                "No readable case JSON selected — open Advanced case input below to "
                "paste or upload one."
            )
            return

        values = _instrument_header_values(envelope)
        top = st.columns(4)
        with top[0]:
            _field("Issuer", values["issuer"])
        with top[1]:
            _field("Coupon", values["coupon"])
        with top[2]:
            _field("Maturity", values["maturity"])
        with top[3]:
            _field("Currency", values["currency"])

        bottom = st.columns(4)
        with bottom[0]:
            _field("Underlying ISIN", values["underlying_isin"])
        with bottom[1]:
            _field("Valuation date", values["valuation_date"])
        with bottom[2]:
            _field("Case quote side", values["case_quote_side"])
        with bottom[3]:
            _field("Case clean price / 100", values["case_clean_price_per_100"])


def _render_trader_override_form(prefill: dict) -> dict:
    """Render the seven editable trader inputs, prefilled from the selected case.

    Returns the seven current widget values, ready to hand to
    :func:`apply_trader_overrides`. No bounds, steps, or validation rules are
    imposed here beyond the widget type itself -- every financial rule stays
    with the existing typed constructors.
    """

    terms_column, market_column = st.columns(2)

    with terms_column, st.container(border=True):
        _section_label("Option terms")
        option_type = st.selectbox(
            "Option type",
            _OPTION_TYPE_OPTIONS,
            index=_OPTION_TYPE_OPTIONS.index(prefill["option_type"]),
        )
        position = st.selectbox(
            "Position",
            _POSITION_OPTIONS,
            index=_POSITION_OPTIONS.index(prefill["position"]),
        )
        strike_price = st.number_input(
            "Strike price", value=float(prefill["strike_price"]), format="%.6f"
        )
        notional = st.number_input(
            "Notional", value=float(prefill["notional"]), format="%.6f"
        )

    with market_column, st.container(border=True):
        _section_label("Market inputs")
        forward_clean_price_per_100 = st.number_input(
            "Forward clean price per 100",
            value=float(prefill["forward_clean_price_per_100"]),
            format="%.6f",
        )
        quote_side = st.selectbox(
            "Forward quote side",
            _QUOTE_SIDE_OPTIONS,
            index=_QUOTE_SIDE_OPTIONS.index(prefill["quote_side"]),
        )
        volatility = st.number_input(
            "Volatility", value=float(prefill["volatility"]), format="%.6f"
        )
        st.caption(
            "Volatility is used on the case's own volatility_basis — this page "
            "applies no yield-to-price volatility conversion."
        )

    return {
        "option_type": option_type,
        "position": position,
        "strike_price": strike_price,
        "notional": notional,
        "forward_clean_price_per_100": forward_clean_price_per_100,
        "quote_side": quote_side,
        "volatility": volatility,
    }


# --- Result rendering ----------------------------------------------------------


def _render_pricing_result(display: dict) -> None:
    """Render the headless workflow's display context verbatim.

    The reproducibility/engine context is shown for both SUCCESS and FAILED.
    Premium/intermediate metrics are shown only for a non-failed result, and
    a FAILED result shows the full structured errors with no replacement
    values.
    """

    status = display["status"]
    context = {
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

    with st.container(border=True):
        st.subheader("Context")
        summary = st.columns(4)
        with summary[0]:
            _field("Product", context["product_id"])
        with summary[1]:
            _field("Valuation date", context["valuation_date"])
        with summary[2]:
            _field("Currency", context["result_currency"])
        with summary[3]:
            _field("Engine", context["engine_name"])
        with st.expander("Provenance and engine detail"):
            st.json(context)

    if status == "FAILED":
        st.error(
            "Pricing FAILED — no model fair premium or intermediate values are shown."
        )
        with st.container(border=True):
            st.subheader("Errors")
            for error in display["errors"]:
                st.markdown(f"- **{error['code']}**: {error['message']}")
                if error["detail"]:
                    st.json(error["detail"])
        return

    st.success(f"Pricing {status}")

    with st.container(border=True):
        _section_label("Pricing results")
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
            st.metric(
                "Time to expiry (years)",
                f"{display['time_to_expiry_year_fraction']:.6f}",
            )

        st.caption(f"Excluded components: {', '.join(display['excluded_components'])}")
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
    -- no residual, comparison, or calibration math happens here. Model fair
    premium, benchmark premium, comparison residual/status, and implied
    ``PRICE_VOL`` are rendered in distinct, separately labeled sections so
    they are never conflated.
    """

    benchmark = display["benchmark"]
    comparison = display["comparison"]
    calibration = display["calibration"]

    with st.container(border=True):
        st.subheader("Benchmark")
        identity = st.columns(4)
        with identity[0]:
            _field("Benchmark ID", benchmark["benchmark_id"])
        with identity[1]:
            _field("Source type", benchmark["source_type"])
        with identity[2]:
            _field("Quote side", benchmark["quote_side"])
        with identity[3]:
            _field("Currency", benchmark["currency"])

        benchmark_left, benchmark_right = st.columns(2)
        with benchmark_left:
            st.metric("Benchmark premium per 100", f"{benchmark['premium_per_100']:.6f}")
        with benchmark_right:
            st.metric("Benchmark total premium", f"{benchmark['total_premium']:.6f}")
        with st.expander("Benchmark provenance"):
            st.json(benchmark)

    with st.container(border=True):
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

    with st.container(border=True):
        st.subheader("Calibration")
        calibration_status = calibration["status"]
        _CALIBRATION_STATUS_RENDERERS[calibration_status](
            f"Calibration {calibration_status} — {calibration['reason']}"
        )
        st.write(calibration["diagnostic_note"])
        if calibration["resolution_error_type"] is not None:
            st.error(
                f"{calibration['resolution_error_type']}: "
                f"{calibration['resolution_error_message']}"
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
                        "premium_tolerance_per_100": calibration[
                            "premium_tolerance_per_100"
                        ],
                        "price_vol_tolerance": calibration["price_vol_tolerance"],
                        "max_iterations": calibration["max_iterations"],
                        "iterations": calibration["iterations"],
                        "final_bracket_lower_price_vol": calibration[
                            "final_bracket_lower_price_vol"
                        ],
                        "final_bracket_upper_price_vol": calibration[
                            "final_bracket_upper_price_vol"
                        ],
                        "diagnostic_note": calibration["solver_diagnostic_note"],
                    }
                )


def _render_live_bloomberg_quote(display: dict) -> None:
    """Render the Bloomberg quote provenance section, verbatim.

    Only called after a successful Bloomberg-sourced result. Shown as its own
    section, distinct from the model fair premium, the benchmark premium, and
    the implied ``PRICE_VOL`` -- every value is a direct read of
    ``display["live_bloomberg_quote"]``, never recomputed or reinterpreted
    here. ``Verified ISIN`` is shown only here, because only a successful
    result carries a loader-verified ISIN. The disclosure below states the
    acquisition-time contract plainly; it adds no new data field.
    """

    quote = display["live_bloomberg_quote"]

    with st.container(border=True):
        st.subheader("Live Bloomberg Quote")
        identity = st.columns(4)
        with identity[0]:
            _field("Security", quote["security"])
        with identity[1]:
            _field("Verified ISIN", quote["verified_isin"])
        with identity[2]:
            _field("Quote side", quote["quote_side"])
        with identity[3]:
            _field("Clean price / 100", quote["clean_price_per_100"])

        acquisition = st.columns(2)
        with acquisition[0]:
            _field("Acquired at (Shiori)", quote["acquired_at"])
        with acquisition[1]:
            _field("Case as-of timestamp", quote["case_as_of_timestamp"])

        st.caption(
            "Bloomberg quote-observation time is not provided by this DAPI path; "
            "acquired_at is when Shiori received this quote. Only the bond quote "
            "was refreshed — curve, forward, volatility, credit-spread, and other "
            "case inputs are unchanged. This is a current-run mixed-provenance "
            "calculation, not a historical replay."
        )
        with st.expander("Bloomberg quote provenance"):
            st.json(quote)


def _render_export_section(display: dict) -> None:
    """Render the "Export current run" downloads for the already-computed ``display``.

    Only called after a real workflow result exists. Both downloads are
    produced by the pure, bounded export helper reading ``display`` alone --
    pricing, comparison, and calibration are not invoked again, no
    server-side file is written, and nothing is persisted to session state.
    ``on_click="ignore"`` avoids an unnecessary rerun of the whole page when
    the trader clicks a download button.
    """

    with st.container(border=True):
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


# --- Page ----------------------------------------------------------------------


def render_standalone_option_workbench_page() -> None:
    _inject_workbench_css()

    st.markdown(
        '<div class="shiori-title">Standalone Bond Option Workbench</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Prices the standalone bond-option leg only (the deposit leg and full "
        "structured-product value are excluded). The bundled example is "
        "sanitized synthetic market-shaped data — not Bloomberg or real-market "
        "validation."
    )

    instrument_slot = st.container()

    with st.expander("Advanced case input"):
        st.caption(
            "The full case envelope. Primary trader inputs are editable above "
            "without opening this section."
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

    # Read the selected case once, for the header and the override prefill only.
    if input_source == _UPLOAD_SOURCE:
        try:
            selected_case_text = (
                None if uploaded_file is None else _decode_uploaded_json_text(
                    uploaded_file.getvalue()
                )
            )
        except UnicodeDecodeError:
            # Display-only: the real error is raised again, verbatim, at execution.
            selected_case_text = None
    else:
        selected_case_text = textarea_text

    prefill_envelope = _parse_case_for_prefill(selected_case_text)
    with instrument_slot:
        _render_instrument_header(prefill_envelope)

    overrides: dict | None = None
    if prefill_envelope is not None:
        prefill = _trader_override_prefill(prefill_envelope)
        if prefill is None:
            st.info(
                "This case does not carry all seven trader-editable values in a "
                "usable form — edit it under Advanced case input. It is passed to "
                "pricing exactly as written."
            )
        else:
            overrides = _render_trader_override_form(prefill)

    with st.container(border=True):
        _section_label("Run setup")
        mode = st.radio("Mode", [_MODE_PRICE_ONLY, _MODE_PRICE_AND_BENCHMARK])
        benchmark_mode = mode == _MODE_PRICE_AND_BENCHMARK

        bond_quote_source = st.radio(
            "Bond quote source",
            [_BOND_QUOTE_SOURCE_CASE_JSON, _BOND_QUOTE_SOURCE_BLOOMBERG],
        )
        bloomberg_mode = bond_quote_source == _BOND_QUOTE_SOURCE_BLOOMBERG

        retrieved_at_text: str | None = None
        if not bloomberg_mode:
            retrieved_at_text = st.text_input(
                "retrieved_at (optional workbench provenance; kept separate from "
                "source-as-of)",
                value="",
            )

    bloomberg_security_text: str | None = None
    bloomberg_quote_side: str | None = None
    if bloomberg_mode:
        with st.container(border=True):
            _section_label("Bloomberg DAPI bond quote")
            security_column, isin_column = st.columns([2, 1])
            with security_column:
                bloomberg_security_text = st.text_input(
                    _BLOOMBERG_SECURITY_LABEL,
                    value="",
                    placeholder=_BLOOMBERG_SECURITY_EXAMPLE,
                    help=_BLOOMBERG_SECURITY_EXAMPLE,
                )
                bloomberg_quote_side = st.selectbox(
                    "Quote side",
                    _QUOTE_SIDE_OPTIONS,
                    index=None,
                    placeholder="Select a quote side (required — no default)",
                )
            with isin_column:
                _field(
                    "Case expected ISIN",
                    None
                    if prefill_envelope is None
                    else _instrument_header_values(prefill_envelope)["underlying_isin"],
                )
                st.caption("Bloomberg's ID_ISIN is verified against this by the loader.")

            st.caption(
                "The case JSON's bond_quote and pricing_timestamp will be replaced by "
                "one Bloomberg quote and the Shiori acquisition time — never used as a "
                "fallback, never merged field-by-field. Only the bond quote is "
                "refreshed; curve, forward, volatility, credit-spread, and other case "
                "inputs stay unchanged. A valuation-date mismatch against the "
                "acquisition date is rejected before any pricing."
            )

    benchmark_input_source: str | None = None
    benchmark_textarea_text: str | None = None
    benchmark_uploaded_file = None
    active_quote_side: str | None = None
    if benchmark_mode:
        with st.container(border=True):
            _section_label("Benchmark input")
            st.caption(
                "No bundled benchmark example is provided. Paste one "
                "actually-observed benchmark quote JSON (BLOOMBERG or VENDOR source) "
                "or upload a benchmark JSON file — this page never prefills or "
                "fabricates a benchmark quote."
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
                    st.info(
                        "Upload a benchmark .json file to compare. No file is loaded yet."
                    )

            if bloomberg_mode:
                st.caption(
                    "Active quote side: driven by the Bloomberg quote side above — "
                    "no separate selector in Bloomberg DAPI mode."
                )
            else:
                active_quote_side = st.selectbox(
                    "Active quote side",
                    _QUOTE_SIDE_OPTIONS,
                    index=None,
                    placeholder="Select a quote side (required — no default)",
                )

    button_label = (
        "Refresh Bloomberg quote and price" if bloomberg_mode else "Price standalone option"
    )
    if not st.button(button_label, type="primary"):
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

        # The overlay is the only transformation applied to the selected case,
        # and only when that same case was readable and carried all seven
        # values (``overrides`` is non-None only then, and ``prefill_envelope``
        # is that case parsed in this very run). Otherwise the case text is
        # handed over completely unchanged, so its real error surfaces.
        if overrides is None:
            priced_case: str | dict | None = case_text
        else:
            priced_case = apply_trader_overrides(prefill_envelope, **overrides)

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
                benchmark_text = _decode_uploaded_json_text(
                    benchmark_uploaded_file.getvalue()
                )
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
                priced_case,
                benchmark_text,
                bloomberg_security=bloomberg_security_text,
                quote_side=bloomberg_quote_side,
            )
        elif bloomberg_mode:
            (
                _request,
                _result,
                _live_quote,
                display,
            ) = price_standalone_option_case_with_bloomberg_quote(
                priced_case,
                bloomberg_security=bloomberg_security_text,
                quote_side=bloomberg_quote_side,
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
                priced_case,
                benchmark_text,
                active_quote_side=active_quote_side,
                retrieved_at=retrieved_at,
            )
        else:
            _request, _result, display = price_standalone_option_case(
                priced_case, retrieved_at=retrieved_at
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
