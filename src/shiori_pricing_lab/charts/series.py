from __future__ import annotations

import pandas as pd

from shiori_pricing_lab.pricing.curve import RateCurve
from shiori_pricing_lab.pricing.scenario import CurveScenarioResult


def curve_to_chart_series(curve: RateCurve) -> pd.DataFrame:
    """Convert a curve into a chart-ready series."""

    frame = curve.to_frame()
    return frame[["tenor", "years", "value", "ticker"]].copy()


def scenario_to_chart_series(result: CurveScenarioResult) -> pd.DataFrame:
    """Convert a scenario result into chart-ready data."""

    return result.to_frame()
