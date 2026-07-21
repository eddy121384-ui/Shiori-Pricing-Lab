"""Streamlit page: standalone bond-option trader workbench (Issue #97, PR B;
Issue #125 benchmark comparison / implied PRICE_VOL; Issue #101 current-run
export; Issue #6 live Bloomberg bond quote; **Issue #133 Slice B trader
workbench rebuild**).

A trader-facing desktop dashboard over the merged headless workflow
(``standalone_option_workbench``). Issue #133 Slice B replaces the previous
long engineering form with one coherent dashboard whose visual fidelity
target is ``docs/ui/reference/standalone_bond_option_workbench_light_v1.png``.
The page keeps every existing workflow, display, failure and export contract
unchanged: it is still a thin UI that performs **no** pricing, comparison,
calibration, resolver, solver, Black-76, curve, discounting, accrual,
volatility, error-mapping, or fallback logic, reads no system clock, and makes
no provider/network call of its own.

**Dashboard structure (Slice B).**

1. *Header* -- compact instrument identity and market snapshot (from the
   selected case), the quote source, the active side, and an honest retrieval
   status. ``Live`` and ``Verified ISIN`` appear only after a successful
   Bloomberg retrieval; before that the expected ISIN is shown as unverified.
2. *Main workspace* -- the seven supported trader inputs and execution
   controls on the left; premium, forward context, Greeks and position risk
   on the right.
3. *Lower workspace* -- Bloomberg provenance, benchmark/comparison/calibration,
   current-run export, assumptions/errors, and the Advanced case JSON drawer.

**Seven supported editable inputs (Slice B).** The trader edits exactly these
values directly, prefilled from the selected case:
``bond_option.option_type``, ``bond_option.position``,
``bond_option.strike_price``, ``bond_option.notional``,
``forward_clean_price_input.forward_clean_price_per_100``,
``forward_clean_price_input.quote_side``, and ``volatility_input.volatility``.
They are applied through the single bounded, non-mutating
:func:`apply_standalone_option_input_overlay` before **every** workflow call
(manual price-only, manual benchmark, Bloomberg price-only, Bloomberg
benchmark). Every other case field stays read-only and is edited only through
the Advanced case JSON drawer. The primary workflow is fully usable without
opening that drawer.

**Pending product areas.** The approved mockup contains product areas this
milestone's backend does not implement (OAS/DV01/duration/convexity risk,
repo/carry rate, normal / lognormal yield vol, the yield history chart,
instrument yield). Each such area stays visible as an honest ``Pending model
support`` / ``Not calculated`` state -- never a fabricated number, chart, or
supported-output claim, and never added to export.

**Bloomberg (Issue #6).** The Bloomberg security control is labeled with its
Yellow Key. The Bloomberg quote side and the benchmark active side each start
with no default (``index=None``). The page never claims ``Live`` or ``Verified
ISIN`` before a successful retrieval. Only ``price_standalone_option_case`` /
``price_standalone_option_case_with_benchmark`` /
``price_standalone_option_case_with_bloomberg_quote`` /
``price_standalone_option_case_with_bloomberg_quote_and_benchmark`` are called
for execution. A Bloomberg failure, a valuation-date mismatch, or a pricing
failure renders the exact error and prices nothing.

**No bundled benchmark example (Codex P1 review of PR #126).** The benchmark
input always starts empty; there is no bundled benchmark JSON.

**Failure display.** Only the expected local-input exceptions
(``json.JSONDecodeError``, ``UnicodeDecodeError``, ``TypeError``,
``ValueError``) and ``BLIBloombergDapiError`` are caught at the button
boundary and rendered verbatim -- never classified, rewritten, suppressed, or
swallowed by a broad ``except``. A pricing ``FAILED`` shows the structured
errors and no premium/intermediate metrics and no Greek. A comparison
``NON_COMPARABLE`` or a calibration ``FAILED`` renders its existing reason and
never substitutes a fabricated ``0`` for a field the result left ``None``. The
model fair premium is never labeled a client quote.
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
    apply_standalone_option_input_overlay,
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
_OPTION_TYPE_OPTIONS = ("CALL", "PUT")
_POSITION_OPTIONS = ("BUY", "SELL")

_BOND_QUOTE_SOURCE_CASE_JSON = "Case JSON"
_BOND_QUOTE_SOURCE_BLOOMBERG = "Bloomberg DAPI"

_PENDING_MODEL_SUPPORT = "Pending model support"
_NOT_CALCULATED = "Not calculated"

# Neutral fallback prefill for the seven overlay inputs when the selected case
# cannot be parsed (mirrors the bundled sanitized-synthetic example values).
_FALLBACK_PREFILL = {
    "option_type": "CALL",
    "position": "BUY",
    "strike_price": 99.5,
    "notional": 50.0,
    "forward_clean_price_per_100": 101.3,
    "forward_quote_side": "MID",
    "volatility": 0.18,
}

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

# Issue #133 Slice A/B. Every label states the unit AND the basis, so no
# displayed Greek is ambiguous: the left column is instrument analytics
# (per 100, CALL/PUT direction only) and the right column is trader position
# risk (notional and BUY/SELL sign applied). Rendered only on a non-FAILED
# result.
_GREEKS_PER_100_FIELDS = (
    ("Delta / 100 (per +1.00 price pt)", "forward_price_delta_per_100"),
    ("Gamma / 100 (per price pt^2)", "forward_price_gamma_per_100"),
    ("Vega / 100 (per +0.01 vol)", "vega_per_vol_point_per_100"),
    ("Theta / 100 (per calendar day)", "theta_per_calendar_day_per_100"),
)
_GREEKS_POSITION_TOTAL_FIELDS = (
    ("Position delta total (per +1.00 price pt)", "position_forward_price_delta_total"),
    ("Position gamma total (per price pt^2)", "position_forward_price_gamma_total"),
    ("Position vega total (per +0.01 vol)", "position_vega_per_vol_point_total"),
    ("Position theta total (per calendar day)", "position_theta_per_calendar_day_total"),
)

# Approved-mockup risk analytics with no current model support. Kept visible as
# honest pending states -- never metrics, never fabricated numbers, never
# exported.
_PENDING_RISK_ANALYTICS = (
    "OAS Delta",
    "DV01",
    "DV01 Gamma",
    "Modified Duration",
    "Convexity",
)

_LOCAL_CSS = """
<style>
/* Issue #133 Slice B: bounded local dashboard styling (no framework). */
.block-container { padding-top: 2.4rem; padding-bottom: 2rem; max-width: 1600px; }
/* Compact, tabular metrics so the dashboard scans like a trader screen. */
div[data-testid="stMetric"] { padding: 0.1rem 0; }
div[data-testid="stMetricValue"] {
    font-size: 1.15rem !important;
    line-height: 1.35 !important;
    font-variant-numeric: tabular-nums;
}
div[data-testid="stMetricValue"] > div { font-size: 1.15rem !important; }
div[data-testid="stMetricLabel"] p { font-size: 0.78rem; color: #6b7280; }
.shiori-brand {
    font-size: 1.35rem;
    font-weight: 700;
    letter-spacing: -0.01em;
    margin-bottom: 0.1rem;
}
.shiori-instrument {
    font-size: 1.05rem;
    font-weight: 600;
    margin-bottom: 0.15rem;
}
.shiori-panel-title {
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #6b7280;
    margin: 0.1rem 0 0.35rem 0;
}
.shiori-chip {
    display: inline-block;
    padding: 0.12rem 0.55rem;
    margin: 0.1rem 0.3rem 0.1rem 0;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
    border: 1px solid #d1d5db;
    color: #374151;
    background: #f9fafb;
}
.shiori-chip.live { color: #047857; border-color: #6ee7b7; background: #ecfdf5; }
.shiori-chip.verified { color: #1d4ed8; border-color: #93c5fd; background: #eff6ff; }
.shiori-chip.muted { color: #9ca3af; }
.shiori-chip.side { color: #b45309; border-color: #fcd34d; background: #fffbeb; }
.shiori-pending {
    color: #9ca3af;
    font-size: 0.82rem;
    font-style: italic;
}
.shiori-market-label { color: #6b7280; font-size: 0.78rem; }
.shiori-market-value { font-size: 1.0rem; font-weight: 600; font-variant-numeric: tabular-nums; }
</style>
"""


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


def _safe_prefill(base_case_text: str | None) -> dict:
    """Return the seven overlay prefill values read from ``base_case_text``.

    A best-effort **display convenience only**: parses the selected base case
    just to seed the seven input widgets. Any parse/shape problem falls back to
    the bundled-example values -- it never prices, never raises, and never
    substitutes a fabricated pricing value (the real workflow still validates
    every value when the trader prices).
    """

    if not base_case_text:
        return dict(_FALLBACK_PREFILL)
    try:
        envelope = json.loads(base_case_text)
        bond_option = envelope["bond_option"]
        forward = envelope["forward_clean_price_input"]
        volatility = envelope["volatility_input"]
        return {
            "option_type": bond_option["option_type"],
            "position": bond_option["position"],
            "strike_price": float(bond_option["strike_price"]),
            "notional": float(bond_option["notional"]),
            "forward_clean_price_per_100": float(forward["forward_clean_price_per_100"]),
            "forward_quote_side": forward["quote_side"],
            "volatility": float(volatility["volatility"]),
        }
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return dict(_FALLBACK_PREFILL)


def _index_or_zero(options: tuple[str, ...], value: str) -> int:
    """Return the index of ``value`` in ``options``, or 0 if absent (prefill only)."""

    try:
        return options.index(value)
    except ValueError:
        return 0


def _fmt(value: float | None, spec: str = ".6f") -> str:
    """Format a numeric display value, or an em dash when it is ``None``."""

    return format(value, spec) if isinstance(value, (int, float)) else "—"


# --- Header ----------------------------------------------------------------------


def _chip(text: str, kind: str = "") -> str:
    css = f"shiori-chip {kind}".strip()
    return f'<span class="{css}">{text}</span>'


def _render_header(base_envelope: dict | None, display: dict | None, *, bloomberg_mode: bool,
                   quote_source: str, active_side: str | None) -> None:
    """Render the compact instrument/market header with honest status chips.

    ``Live`` and ``Verified ISIN`` are shown only when ``display`` carries a
    successful ``live_bloomberg_quote`` (a real retrieval). Before that, the
    expected ISIN is shown as unverified and no ``Live`` chip appears.
    """

    bond_option = (base_envelope or {}).get("bond_option", {})
    bond_quote = (base_envelope or {}).get("bond_quote", {})
    product_id = bond_option.get("product_id", "—")
    expected_isin = bond_option.get("underlying_isin")
    currency = bond_option.get("currency", "—")

    live_quote = (display or {}).get("live_bloomberg_quote")
    retrieved = isinstance(live_quote, dict)
    verified_isin = live_quote.get("verified_isin") if retrieved else None
    clean_price = (
        live_quote.get("clean_price_per_100")
        if retrieved
        else bond_quote.get("clean_price_per_100")
    )

    left, right = st.columns([0.55, 0.45])
    with left:
        st.markdown('<div class="shiori-brand">Bond Option Pricer</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="shiori-instrument">{product_id}</div>', unsafe_allow_html=True
        )
        chips = [_chip(f"Source: {quote_source}")]
        if bloomberg_mode:
            chips.append(_chip("Yellow Key", "side"))
        if active_side:
            chips.append(_chip(f"Side: {active_side}", "side"))
        if retrieved:
            chips.append(_chip("● Live", "live"))
            if verified_isin:
                chips.append(_chip(f"✓ Verified ISIN {verified_isin}", "verified"))
        else:
            isin_text = f"ISIN {expected_isin} (unverified)" if expected_isin else "ISIN —"
            chips.append(_chip(isin_text, "muted"))
        st.markdown(" ".join(chips), unsafe_allow_html=True)

    with right:
        m1, m2, m3 = st.columns(3)
        with m1:
            st.markdown('<div class="shiori-market-label">Bond clean price</div>',
                        unsafe_allow_html=True)
            st.markdown(
                f'<div class="shiori-market-value">{_fmt(clean_price, ".4f")}</div>',
                unsafe_allow_html=True,
            )
        with m2:
            st.markdown('<div class="shiori-market-label">Valuation date</div>',
                        unsafe_allow_html=True)
            st.markdown(
                f'<div class="shiori-market-value">'
                f'{(base_envelope or {}).get("valuation_date", "—")}</div>',
                unsafe_allow_html=True,
            )
        with m3:
            st.markdown('<div class="shiori-market-label">Currency</div>',
                        unsafe_allow_html=True)
            st.markdown(
                f'<div class="shiori-market-value">{currency}</div>', unsafe_allow_html=True
            )


# --- Left column: option terms + execution controls ------------------------------


def _render_option_terms(prefill: dict, *, forward_side_options: tuple[str, ...]) -> dict:
    """Render the seven editable trader inputs and return their values.

    Read-only echoes (product type, exercise, settlement, model, payoff basis)
    describe the fixed European price-based cash-settled Black-76 path; they are
    not editable in this slice.
    """

    st.markdown('<div class="shiori-panel-title">Option terms</div>', unsafe_allow_html=True)

    option_type = st.radio(
        "Call / Put",
        _OPTION_TYPE_OPTIONS,
        index=_index_or_zero(_OPTION_TYPE_OPTIONS, prefill["option_type"]),
        horizontal=True,
    )
    position = st.radio(
        "Direction",
        _POSITION_OPTIONS,
        index=_index_or_zero(_POSITION_OPTIONS, prefill["position"]),
        horizontal=True,
    )
    strike_price = st.number_input(
        "Strike price (clean, per 100)",
        value=float(prefill["strike_price"]),
        step=0.25,
        format="%.4f",
    )
    notional = st.number_input(
        "Notional",
        value=float(prefill["notional"]),
        step=1.0,
        format="%.4f",
    )
    forward_clean_price_per_100 = st.number_input(
        "Forward clean price (per 100)",
        value=float(prefill["forward_clean_price_per_100"]),
        step=0.25,
        format="%.4f",
    )
    forward_quote_side = st.selectbox(
        "Forward quote side",
        forward_side_options,
        index=_index_or_zero(forward_side_options, prefill["forward_quote_side"]),
        help=(
            "Must match the bond quote's observation side (one coherent side); a "
            "mismatch is rejected by the existing pricing contract."
        ),
    )
    volatility = st.number_input(
        "Price volatility (PRICE_VOL, absolute)",
        value=float(prefill["volatility"]),
        step=0.01,
        format="%.4f",
    )

    with st.expander("Fixed model path (read-only)"):
        st.markdown(
            "- Product: European vanilla, price-based, cash-settled\n"
            "- Exercise: European · Settlement: Cash · Payoff basis: PRICE\n"
            "- Model: Black-76 on the explicit forward clean price\n"
            "- Volatility basis: PRICE_VOL (YIELD_VOL is not supported and prices FAILED)"
        )
    st.markdown(
        f'<span class="shiori-pending">Normal yield vol (bp): {_PENDING_MODEL_SUPPORT}'
        "</span>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<span class="shiori-pending">Lognormal yield vol: {_PENDING_MODEL_SUPPORT}</span>',
        unsafe_allow_html=True,
    )

    return {
        "option_type": option_type,
        "position": position,
        "strike_price": strike_price,
        "notional": notional,
        "forward_clean_price_per_100": forward_clean_price_per_100,
        "forward_quote_side": forward_quote_side,
        "volatility": volatility,
    }


# --- Right column: premium, Greeks, forward context ------------------------------


def _render_pricing_result(display: dict) -> None:
    """Render the priced result: context, premium, and Greeks (or FAILED errors).

    On a FAILED result, no premium/intermediate metrics and no Greek are shown
    -- only the structured errors, verbatim and complete. Pending model areas
    are rendered as honest captions, never as ``st.metric`` and never with a
    fabricated number.
    """

    status = display["status"]

    st.markdown('<div class="shiori-panel-title">Pricing results</div>', unsafe_allow_html=True)

    if status == "FAILED":
        st.error(
            "Pricing FAILED — no model fair premium or intermediate values are shown."
        )
        st.markdown("**Errors**")
        for error in display["errors"]:
            st.markdown(f"- **{error['code']}**: {error['message']}")
            if error["detail"]:
                st.json(error["detail"])
        with st.expander("Context"):
            st.write(_context_view(display))
        return

    st.success(f"Pricing {status}")

    premium_left, premium_right = st.columns(2)
    with premium_left:
        st.metric(
            "Model fair premium per 100",
            f"{display['model_fair_premium_per_100']:.6f}",
        )
        st.metric(
            "Forward clean price per 100",
            f"{display['forward_clean_price_per_100']:.6f}",
        )
    with premium_right:
        st.metric(
            "Total notional model fair premium",
            f"{display['total_notional_model_fair_premium']:.6f}",
        )
        st.metric("Black-76 PV per 100", f"{display['black76_pv_per_100']:.6f}")

    detail_left, detail_right = st.columns(2)
    with detail_left:
        st.metric(
            "Effective reporting-date discount factor",
            f"{display['effective_reporting_date_discount_factor']:.6f}",
        )
    with detail_right:
        st.metric("Time to expiry (years)", f"{display['time_to_expiry_year_fraction']:.6f}")

    st.markdown('<div class="shiori-panel-title">Greeks &amp; position risk</div>',
                unsafe_allow_html=True)
    st.caption(
        "European Black-76 closed-form Greeks on the same resolved inputs as the "
        "premium. Delta/Gamma per +1.00 forward clean price point, Vega per +0.01 "
        "absolute volatility, Theta per +1 calendar day."
    )
    greeks_left, greeks_right = st.columns(2)
    with greeks_left:
        st.markdown("**Instrument analytics (per 100)**")
        st.caption(
            "CALL/PUT direction only. BUY/SELL position sign is NOT applied — "
            "an otherwise identical BUY and SELL show the same values here."
        )
        for label, key in _GREEKS_PER_100_FIELDS:
            st.metric(label, f"{display[key]:.6f}")
    with greeks_right:
        st.markdown("**Position risk (notional and BUY/SELL sign applied)**")
        st.caption(
            f"Position {display['position']} — per 100 x notional / 100 x "
            f"position multiplier {display['position_multiplier']:+g} "
            "(BUY = +1, SELL = -1). An otherwise identical SELL total is the "
            "negative of the BUY total."
        )
        for label, key in _GREEKS_POSITION_TOTAL_FIELDS:
            st.metric(label, f"{display[key]:.6f}")

    st.markdown("**Rate / spread risk analytics**")
    for label in _PENDING_RISK_ANALYTICS:
        st.markdown(
            f'<span class="shiori-pending">{label}: {_PENDING_MODEL_SUPPORT}</span>',
            unsafe_allow_html=True,
        )

    st.write(f"**Excluded components:** {', '.join(display['excluded_components'])}")
    with st.expander("Context"):
        st.write(_context_view(display))
    with st.expander("Assumptions"):
        st.json(display["assumptions"])


def _context_view(display: dict) -> dict:
    """Return the reproducibility/provenance context, read verbatim from ``display``."""

    return {
        "product_id": display["product_id"],
        "product_type": display["product_type"],
        "valuation_date": display["valuation_date"],
        "result_currency": display["result_currency"],
        "status": display["status"],
        "method": display["method"],
        "source_system": display["source_system"],
        "source_as_of": display["source_as_of"],
        "retrieved_at": display["retrieved_at"],
        "snapshot_id": display["snapshot_id"],
        "engine_name": display["engine_name"],
        "engine_version": display["engine_version"],
    }


def _render_forward_and_underlying(display: dict) -> None:
    """Render the forward/carry and underlying-snapshot panels.

    Every value is a verbatim read of ``display`` / ``display['assumptions']``
    -- no math here. Repo/carry rate and yield/duration/convexity analytics that
    the current model does not construct stay as honest pending states.
    """

    if display["status"] == "FAILED":
        return

    assumptions = display["assumptions"]

    fwd_col, snap_col = st.columns(2)
    with fwd_col:
        st.markdown('<div class="shiori-panel-title">Forward &amp; carry</div>',
                    unsafe_allow_html=True)
        st.metric(
            "Forward clean price per 100",
            f"{assumptions.get('forward_clean_price_per_100'):.6f}",
        )
        st.metric(
            "Forward dirty price per 100",
            f"{assumptions.get('forward_dirty_price_per_100'):.6f}",
        )
        st.caption(f"Forward construction: {assumptions.get('forward_construction')}")
        st.markdown(
            f'<span class="shiori-pending">Repo / carry rate: {_PENDING_MODEL_SUPPORT} '
            "(explicit forward clean price input; no repo forward construction)</span>",
            unsafe_allow_html=True,
        )

    with snap_col:
        st.markdown('<div class="shiori-panel-title">Underlying snapshot</div>',
                    unsafe_allow_html=True)
        st.metric(
            "Strike clean price per 100",
            f"{assumptions.get('strike_clean_price_per_100'):.6f}",
        )
        st.metric(
            "Strike dirty price per 100",
            f"{assumptions.get('strike_dirty_price_per_100'):.6f}",
        )
        st.metric(
            "Accrued interest at forward settlement per 100",
            f"{assumptions.get('accrued_interest_at_forward_settlement_per_100'):.6f}",
        )
        st.markdown(
            f'<span class="shiori-pending">Yield / OAS / duration / convexity / '
            f"DV01: {_PENDING_MODEL_SUPPORT}</span>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<span class="shiori-pending">Yield history chart: {_NOT_CALCULATED}</span>',
            unsafe_allow_html=True,
        )


def _render_instrument_details(base_envelope: dict | None) -> None:
    """Render the read-only instrument details from the case reference universe."""

    st.markdown('<div class="shiori-panel-title">Instrument details</div>',
                unsafe_allow_html=True)
    universe = (base_envelope or {}).get("bond_reference_data_universe")
    if not isinstance(universe, list) or not universe:
        st.markdown(
            f'<span class="shiori-pending">Reference data: {_NOT_CALCULATED}</span>',
            unsafe_allow_html=True,
        )
        return
    ref = universe[0]
    left, right = st.columns(2)
    with left:
        st.write(
            {
                "Issuer": ref.get("issuer"),
                "ISIN": ref.get("isin"),
                "Coupon": ref.get("coupon"),
                "Coupon frequency": ref.get("coupon_frequency"),
                "Maturity": ref.get("maturity_date"),
            }
        )
    with right:
        st.write(
            {
                "Day count": ref.get("day_count"),
                "Bond type": ref.get("bond_type"),
                "Callable": ref.get("callable_flag"),
                "Currency": ref.get("currency"),
                "Status": ref.get("status"),
            }
        )


# --- Lower workspace: benchmark, live quote, export ------------------------------


def _render_metric_or_caption(label: str, value: float | None, *, note: str) -> None:
    """Render one ``st.metric`` for ``value``, or a plain caption when it is ``None``.

    Never substitutes a fabricated ``0``/``N/A`` numeric placeholder for a
    ``None`` field (a structured outcome the existing result already left
    unset); it is shown as an honest caption using the caller-supplied ``note``.
    """

    if value is None:
        st.caption(f"{label}: not available — {note}")
    else:
        st.metric(label, f"{value:.6f}")


def _render_benchmark_result(display: dict) -> None:
    """Render the Benchmark, Comparison, and Calibration sections verbatim."""

    benchmark = display["benchmark"]
    comparison = display["comparison"]
    calibration = display["calibration"]

    st.markdown('<div class="shiori-panel-title">Benchmark</div>', unsafe_allow_html=True)
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

    st.markdown('<div class="shiori-panel-title">Comparison</div>', unsafe_allow_html=True)
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

    st.markdown('<div class="shiori-panel-title">Calibration</div>', unsafe_allow_html=True)
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
    """Render the live Bloomberg quote provenance section, verbatim."""

    st.markdown('<div class="shiori-panel-title">Live Bloomberg quote</div>',
                unsafe_allow_html=True)
    st.caption(
        "Bloomberg quote-observation time is not provided by this DAPI path. "
        "acquired_at is when Shiori received this quote. Only the bond quote "
        "was refreshed -- curve, forward, volatility, credit-spread, and "
        "other market inputs remain from the case JSON. This is a current-run "
        "mixed-provenance calculation, not a historical replay."
    )
    st.write(display["live_bloomberg_quote"])


def _render_export_section(display: dict) -> None:
    """Render the "Export current run" downloads for the already-computed ``display``.

    Both downloads are produced by the pure, bounded export helper reading
    ``display`` alone -- pricing/comparison/calibration are never invoked again,
    no server-side file is written, nothing is persisted. ``on_click="ignore"``
    avoids an unnecessary rerun of the whole page on download.
    """

    st.markdown('<div class="shiori-panel-title">Export current run</div>',
                unsafe_allow_html=True)
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


# --- Page --------------------------------------------------------------------------


def render_standalone_option_workbench_page() -> None:
    st.markdown(_LOCAL_CSS, unsafe_allow_html=True)

    # Reserve dashboard zones top -> bottom; results are backfilled after the
    # button press computes them (Streamlit fills containers by creation order,
    # not code order).
    header_slot = st.container()
    main_slot = st.container()
    lower_slot = st.container()
    footer_slot = st.container()

    with main_slot:
        left_col, right_col = st.columns([0.42, 0.58], gap="large")

    with lower_slot:
        bloomberg_out_slot = st.container()
        benchmark_out_slot = st.container()
        instrument_slot = st.container()
        export_slot = st.container()
        advanced_slot = st.container()

    # --- Advanced case JSON drawer (base case), rendered first for prefill --------
    with advanced_slot:
        with st.expander("Advanced: case JSON input (base case & upload)"):
            st.caption(
                "The primary workflow works without opening this drawer: the seven "
                "trader inputs above overlay the selected base case. Every other "
                "case field is edited only here."
            )
            input_source = st.radio("Input source", [_EDITABLE_SOURCE, _UPLOAD_SOURCE])
            textarea_text: str | None = None
            uploaded_file = None
            if input_source == _EDITABLE_SOURCE:
                textarea_text = st.text_area(
                    "Standalone option case JSON",
                    value=_load_example_case_text(),
                    height=320,
                )
            else:
                uploaded_file = st.file_uploader(
                    "Upload standalone option case JSON (.json, UTF-8)",
                    type=["json"],
                )
                if uploaded_file is None:
                    st.info("Upload a .json file to price. No file is loaded yet.")

    # Best-effort base-case text used only to prefill the seven input widgets.
    if input_source == _UPLOAD_SOURCE and uploaded_file is not None:
        try:
            prefill_source_text: str | None = _decode_uploaded_json_text(uploaded_file.getvalue())
        except UnicodeDecodeError:
            prefill_source_text = None
    else:
        prefill_source_text = textarea_text
    prefill = _safe_prefill(prefill_source_text)

    # --- Left column: execution controls + option terms ---------------------------
    with left_col:
        st.markdown('<div class="shiori-panel-title">Execution &amp; data source</div>',
                    unsafe_allow_html=True)
        mode = st.radio("Mode", [_MODE_PRICE_ONLY, _MODE_PRICE_AND_BENCHMARK])
        benchmark_mode = mode == _MODE_PRICE_AND_BENCHMARK

        bond_quote_source = st.radio(
            "Bond quote source", [_BOND_QUOTE_SOURCE_CASE_JSON, _BOND_QUOTE_SOURCE_BLOOMBERG]
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
            st.caption(
                "The case JSON's bond_quote and pricing_timestamp are replaced by one "
                "live Bloomberg quote and the Shiori acquisition time -- never a "
                "fallback, never merged field-by-field. Only the bond quote is "
                "refreshed; a valuation-date mismatch is rejected before any pricing."
            )
            bloomberg_security_text = st.text_input("Bloomberg security (Yellow Key)", value="")
            bloomberg_quote_side = st.selectbox(
                "Quote side",
                _QUOTE_SIDE_OPTIONS,
                index=None,
                placeholder="Select a quote side (required — no default)",
            )

        overlay = _render_option_terms(prefill, forward_side_options=_QUOTE_SIDE_OPTIONS)

    # --- Benchmark input (execution config for benchmark mode) --------------------
    benchmark_input_source: str | None = None
    benchmark_textarea_text: str | None = None
    benchmark_uploaded_file = None
    active_quote_side: str | None = None
    if benchmark_mode:
        with benchmark_out_slot:
            st.markdown('<div class="shiori-panel-title">Benchmark input</div>',
                        unsafe_allow_html=True)
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
                    height=220,
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

    # --- Footer: primary execution button -----------------------------------------
    with footer_slot:
        st.divider()
        button_label = (
            "Refresh Bloomberg quote and price"
            if bloomberg_mode
            else "Price standalone option"
        )
        pressed = st.button(button_label, type="primary")
        footer_status_slot = st.container()

    active_side_for_header = (
        bloomberg_quote_side if bloomberg_mode else overlay["forward_quote_side"]
    )
    base_envelope_for_header = _safe_envelope(prefill_source_text)

    if not pressed:
        with header_slot:
            _render_header(
                base_envelope_for_header,
                None,
                bloomberg_mode=bloomberg_mode,
                quote_source=bond_quote_source,
                active_side=active_side_for_header,
            )
        with right_col:
            st.markdown('<div class="shiori-panel-title">Pricing results</div>',
                        unsafe_allow_html=True)
            st.info("Set the seven trader inputs and press the button to price this run.")
        with instrument_slot:
            _render_instrument_details(base_envelope_for_header)
        return

    # --- Compute (button pressed) -------------------------------------------------
    retrieved_at = _retrieved_at_or_none(retrieved_at_text)
    display: dict | None = None
    error_message: str | None = None
    warning_message: str | None = None

    try:
        if input_source == _UPLOAD_SOURCE:
            if uploaded_file is None:
                warning_message = "No file uploaded — nothing to price. Upload a .json file first."
                case_text = None
            else:
                case_text = _decode_uploaded_json_text(uploaded_file.getvalue())
        else:
            case_text = textarea_text

        if warning_message is None and bloomberg_mode:
            if not bloomberg_security_text or not bloomberg_security_text.strip():
                warning_message = (
                    "No Bloomberg security entered — nothing to price. Enter a security first."
                )
            elif bloomberg_quote_side is None:
                warning_message = (
                    "No quote side selected — nothing to price. Select BID, MID, or OFFER first."
                )

        benchmark_text: str | None = None
        if warning_message is None and benchmark_mode:
            if not bloomberg_mode and active_quote_side is None:
                warning_message = (
                    "No active quote side selected — nothing to compare. Select BID, MID, "
                    "or OFFER first."
                )
            elif benchmark_input_source == _UPLOAD_SOURCE:
                if benchmark_uploaded_file is None:
                    warning_message = (
                        "No benchmark file uploaded — nothing to compare. Upload a .json "
                        "file first."
                    )
                else:
                    benchmark_text = _decode_uploaded_json_text(benchmark_uploaded_file.getvalue())
            else:
                benchmark_text = benchmark_textarea_text

        if warning_message is None:
            # The single bounded, non-mutating overlay before EVERY workflow call.
            overlaid_case = apply_standalone_option_input_overlay(
                case_text,
                option_type=overlay["option_type"],
                position=overlay["position"],
                strike_price=overlay["strike_price"],
                notional=overlay["notional"],
                forward_clean_price_per_100=overlay["forward_clean_price_per_100"],
                forward_quote_side=overlay["forward_quote_side"],
                volatility=overlay["volatility"],
            )

            if bloomberg_mode and benchmark_mode:
                (*_ignored, display) = (
                    price_standalone_option_case_with_bloomberg_quote_and_benchmark(
                        overlaid_case,
                        benchmark_text,
                        bloomberg_security=bloomberg_security_text,
                        quote_side=bloomberg_quote_side,
                    )
                )
            elif bloomberg_mode:
                (*_ignored, display) = price_standalone_option_case_with_bloomberg_quote(
                    overlaid_case,
                    bloomberg_security=bloomberg_security_text,
                    quote_side=bloomberg_quote_side,
                )
            elif benchmark_mode:
                (*_ignored, display) = price_standalone_option_case_with_benchmark(
                    overlaid_case,
                    benchmark_text,
                    active_quote_side=active_quote_side,
                    retrieved_at=retrieved_at,
                )
            else:
                _request, _result, display = price_standalone_option_case(
                    overlaid_case, retrieved_at=retrieved_at
                )
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        TypeError,
        ValueError,
        BLIBloombergDapiError,
    ) as exc:
        error_message = str(exc)

    # --- Backfill dashboard zones -------------------------------------------------
    with header_slot:
        _render_header(
            base_envelope_for_header,
            display,
            bloomberg_mode=bloomberg_mode,
            quote_source=bond_quote_source,
            active_side=active_side_for_header,
        )

    with right_col:
        if error_message is not None:
            st.markdown('<div class="shiori-panel-title">Pricing results</div>',
                        unsafe_allow_html=True)
            st.error(error_message)
        elif warning_message is not None:
            st.markdown('<div class="shiori-panel-title">Pricing results</div>',
                        unsafe_allow_html=True)
            st.warning(warning_message)
        elif display is not None:
            _render_pricing_result(display)
            _render_forward_and_underlying(display)

    with instrument_slot:
        _render_instrument_details(base_envelope_for_header)

    if display is not None:
        if bloomberg_mode:
            with bloomberg_out_slot:
                _render_live_bloomberg_quote(display)
        if benchmark_mode:
            with benchmark_out_slot:
                _render_benchmark_result(display)
        with export_slot:
            _render_export_section(display)

    with footer_status_slot:
        if error_message is not None:
            st.error(f"Not priced — {error_message}")
        elif warning_message is not None:
            st.warning(warning_message)
        elif display is not None and display["status"] == "FAILED":
            st.error("Pricing FAILED — see structured errors above.")
        elif display is not None:
            st.success("Inputs valid — current run priced.")


def _safe_envelope(base_case_text: str | None) -> dict | None:
    """Return the parsed base envelope for header/instrument display, or ``None``.

    Best-effort read for display only -- never raises, never prices.
    """

    if not base_case_text:
        return None
    try:
        parsed = json.loads(base_case_text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None
