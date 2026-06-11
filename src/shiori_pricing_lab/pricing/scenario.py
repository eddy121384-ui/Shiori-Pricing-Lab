from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from shiori_pricing_lab.pricing.curve import RateCurve


@dataclass(frozen=True)
class CurveScenarioResult:
    """Output container for simple curve shock scenarios."""

    base_curve: RateCurve
    shocked_curve: RateCurve
    shock_bp: float

    def to_frame(self) -> pd.DataFrame:
        base = self.base_curve.to_frame()[["tenor", "years", "value"]].copy()
        shocked = self.shocked_curve.to_frame()[["tenor", "years", "value"]].copy()
        base = base.rename(columns={"value": "base_value"})
        shocked = shocked.rename(columns={"value": "shocked_value"})
        merged = base.merge(shocked, on=["tenor", "years"], how="inner")
        merged["shock_bp"] = self.shock_bp
        merged["change_bp"] = (merged["shocked_value"] - merged["base_value"]) * 10_000
        return merged


def run_parallel_curve_shock(curve: RateCurve, shock_bp: float) -> CurveScenarioResult:
    """Apply a parallel curve shock and return scenario output."""

    shocked_curve = curve.shocked_parallel(shock_bp=shock_bp)
    return CurveScenarioResult(base_curve=curve, shocked_curve=shocked_curve, shock_bp=shock_bp)
