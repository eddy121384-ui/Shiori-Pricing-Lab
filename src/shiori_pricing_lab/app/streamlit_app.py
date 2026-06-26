from __future__ import annotations

from pathlib import Path

import plotly.express as px
import streamlit as st

from shiori_pricing_lab.charts.series import curve_to_chart_series, scenario_to_chart_series
from shiori_pricing_lab.data.providers import CSVMarketDataProvider
from shiori_pricing_lab.data.snapshot import MarketDataSnapshot
from shiori_pricing_lab.pricing.scenario import run_parallel_curve_shock
from shiori_pricing_lab.valuation.context import ValuationContext

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_DATA = PROJECT_ROOT / "examples" / "sample_market_data.csv"


def main() -> None:
    st.set_page_config(page_title="Shiori Pricing Lab", layout="wide")
    st.title("Shiori Pricing Lab")
    st.caption(
        "Local prototype: load sample rates data, build a simple curve, "
        "and run a parallel shock."
    )

    # Spine: provider -> MarketDataSnapshot -> ValuationContext -> RateCurve -> scenario.
    provider = CSVMarketDataProvider(SAMPLE_DATA)
    market_data = provider.load_rates_points()

    # Valuation date is chosen explicitly from the data, never from the system date.
    available_dates = sorted(market_data["date"].astype(str).unique())
    valuation_date = st.sidebar.selectbox("Valuation date", available_dates, index=0)

    snapshot = MarketDataSnapshot.from_rates_points(
        market_data, valuation_date=valuation_date, source="synthetic"
    )
    context = ValuationContext.from_snapshot(snapshot)
    curve = context.build_curve()

    shock_bp = st.sidebar.number_input("Parallel shock (bp)", value=1.0, step=1.0)
    scenario = run_parallel_curve_shock(curve, shock_bp=shock_bp)

    st.subheader("Market data")
    st.dataframe(market_data, use_container_width=True)

    st.subheader("Yield curve")
    curve_series = curve_to_chart_series(curve)
    fig = px.line(curve_series, x="years", y="value", text="tenor", markers=True)
    fig.update_traces(textposition="top center")
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Scenario output")
    scenario_series = scenario_to_chart_series(scenario)
    st.dataframe(scenario_series, use_container_width=True)


if __name__ == "__main__":
    main()
