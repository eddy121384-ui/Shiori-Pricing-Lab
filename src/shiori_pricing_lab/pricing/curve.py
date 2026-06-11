from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

_TENOR_TO_YEARS = {
    "1M": 1 / 12,
    "3M": 0.25,
    "6M": 0.5,
    "1Y": 1.0,
    "2Y": 2.0,
    "3Y": 3.0,
    "5Y": 5.0,
    "7Y": 7.0,
    "10Y": 10.0,
    "20Y": 20.0,
    "30Y": 30.0,
}


@dataclass(frozen=True)
class RateCurve:
    """Simple rates curve representation for early prototypes.

    This is intentionally simple. v0.1 does not attempt full curve bootstrapping.
    Rates are expected in decimal terms, e.g. 4.25% = 0.0425.
    """

    date: str
    points: pd.DataFrame

    @classmethod
    def from_rates_points(cls, frame: pd.DataFrame, date: str | None = None) -> "RateCurve":
        if frame.empty:
            raise ValueError("Cannot build a curve from an empty frame")

        selected_date = date or str(frame["date"].iloc[0])
        points = frame.loc[frame["date"].astype(str) == selected_date].copy()
        if points.empty:
            raise ValueError(f"No rates points found for date={selected_date}")

        points["years"] = points["tenor"].map(tenor_to_years)
        points = points.sort_values("years").reset_index(drop=True)
        return cls(date=selected_date, points=points)

    def to_frame(self) -> pd.DataFrame:
        return self.points.copy()

    def shocked_parallel(self, shock_bp: float) -> "RateCurve":
        """Return a parallel-shocked curve.

        shock_bp uses basis points. +1 bp = +0.0001 in decimal rate terms.
        """

        shocked = self.points.copy()
        shocked["value"] = shocked["value"] + shock_bp / 10_000
        shocked["source"] = shocked["source"].astype(str) + f"|parallel_shock_{shock_bp:g}bp"
        return RateCurve(date=self.date, points=shocked)


def tenor_to_years(tenor: str) -> float:
    """Convert a tenor label such as 2Y or 6M into year fraction.

    This is a simplified mapping for prototypes. Production-grade curve construction
    should use explicit calendars, day-count conventions, and instrument definitions.
    """

    if tenor not in _TENOR_TO_YEARS:
        raise ValueError(f"Unsupported tenor: {tenor}")
    return _TENOR_TO_YEARS[tenor]
