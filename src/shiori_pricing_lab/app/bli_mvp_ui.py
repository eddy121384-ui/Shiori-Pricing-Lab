"""Streamlit page: European bond option (BLI MVP) pricer display (Issue #84).

Fixture-first UI over ``price_bli_mvp`` (Issue #44, MVP-8). This module
prices exactly one of two deterministic demo bundles from
``bli_mvp_ui_demo_fixture.py`` and displays the result. It defines no
pricing, curve, discounting, accrual, volatility-conversion, or
error-classification logic of its own -- every displayed number is read
verbatim from the returned ``PricingResult`` / ``PricingResult.assumptions``.

**Out of scope for this page (Issue #84):** a manual input form, login/
permissions, quote lifecycle, audit DB, Bloomberg/FTP ingestion,
termsheet export, AI chat, portfolio aggregation, scenario engine, full
branding.
"""

from __future__ import annotations

import streamlit as st

from shiori_pricing_lab.app.bli_mvp_ui_demo_fixture import (
    SYNTHETIC_UI_DEMO_BUNDLE,
    SYNTHETIC_UI_DEMO_BUNDLE_CURVE_RANGE_ERROR,
)
from shiori_pricing_lab.pricing.bli_pricing_engine import price_bli_mvp
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available
from shiori_pricing_lab.pricing.result import PricingResult, PricingStatus

_DEMO_CASES = {
    "Synthetic supported case (short-tenor demo curves)": SYNTHETIC_UI_DEMO_BUNDLE,
    "Known curve-range ENGINE_ERROR demo": SYNTHETIC_UI_DEMO_BUNDLE_CURVE_RANGE_ERROR,
}


def prepare_bli_mvp_display(result: PricingResult) -> dict:
    """Return display-ready values read verbatim from ``result``.

    No pricing math, unit conversion, or error re-mapping happens here --
    every value is either a direct ``PricingResult`` field or a direct
    ``PricingResult.assumptions`` lookup (``.get`` only to tolerate the
    keys being absent on a ``FAILED`` result, never to compute a
    substitute value).
    """

    assumptions = result.assumptions
    return {
        "status": result.status.value,
        "method": result.method,
        "pv": result.pv,
        "forward_clean_price_per_100": assumptions.get("forward_clean_price_per_100"),
        "black76_pv_per_100": assumptions.get("black76_pv_per_100"),
        "option_discount_factor": assumptions.get("option_discount_factor"),
        "time_to_expiry_year_fraction": assumptions.get("time_to_expiry_year_fraction"),
        "pv_scaling_formula": assumptions.get("pv_scaling_formula"),
        "priced_component": assumptions.get("priced_component"),
        "priced_component_scope": assumptions.get("priced_component_scope"),
        "excluded_components": assumptions.get("excluded_components"),
        "errors": [
            {"code": message.code.value, "message": message.message}
            for message in result.errors
        ],
    }


def render_bli_mvp_page() -> None:
    st.header("European Bond Option (BLI MVP)")
    st.caption(
        "Fixture-first demo over price_bli_mvp. Prices the bond-option leg "
        "only; see 'excluded components' below for what is not priced."
    )

    if not is_quantlib_available():
        st.warning(
            "QuantLib is not installed in this environment, so the bond-option "
            "pricing path cannot run. Install QuantLib to exercise this demo."
        )
        return

    case_label = st.selectbox("Demo case", list(_DEMO_CASES.keys()))
    bundle = _DEMO_CASES[case_label]

    with st.expander("Bond option inputs (read-only)"):
        bond_option = bundle.product.bond_option
        st.write(
            {
                "currency": bond_option.currency.value,
                "option_type": bond_option.option_type.value,
                "expiry_date": bond_option.expiry_date,
                "strike_price": bond_option.strike_price,
                "notional": bond_option.notional,
            }
        )

    result = price_bli_mvp(bundle)
    display = prepare_bli_mvp_display(result)

    if result.status is PricingStatus.FAILED:
        st.error(f"Pricing FAILED (method: {display['method']})")
        for error in display["errors"]:
            st.write(f"- **{error['code']}**: {error['message']}")
        return

    st.success(f"Pricing {display['status']} (method: {display['method']})")

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Premium / PV", f"{display['pv']:.6f}")
        st.metric("Forward clean price per 100", f"{display['forward_clean_price_per_100']:.6f}")
        st.metric("Black-76 PV per 100", f"{display['black76_pv_per_100']:.6f}")
    with col2:
        st.metric("Option discount factor", f"{display['option_discount_factor']:.6f}")
        st.metric("Time to expiry (years)", f"{display['time_to_expiry_year_fraction']:.6f}")

    st.write(f"**Notional scaling formula:** `{display['pv_scaling_formula']}`")
    st.write(
        f"**Priced component:** {display['priced_component']} "
        f"({display['priced_component_scope']})"
    )
    st.write(f"**Excluded components:** {', '.join(display['excluded_components'])}")
