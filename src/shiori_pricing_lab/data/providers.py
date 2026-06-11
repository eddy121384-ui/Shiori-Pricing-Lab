from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pandas as pd

REQUIRED_MARKET_DATA_COLUMNS = ["date", "ticker", "tenor", "value", "data_type", "source"]


@dataclass(frozen=True)
class RatesPoint:
    """One normalized rates market data point.

    Rates should be represented in decimal terms, e.g. 4.25% = 0.0425.
    """

    date: str
    ticker: str
    tenor: str
    value: float
    data_type: str
    source: str


class MarketDataProvider(Protocol):
    """Interface shared by all market data providers."""

    def load_rates_points(self) -> pd.DataFrame:
        """Return normalized rates points with REQUIRED_MARKET_DATA_COLUMNS."""


class CSVMarketDataProvider:
    """Load normalized rates points from a CSV file."""

    def __init__(self, csv_path: str | Path) -> None:
        self.csv_path = Path(csv_path)

    def load_rates_points(self) -> pd.DataFrame:
        frame = pd.read_csv(self.csv_path)
        validate_rates_points_frame(frame)
        return frame


class ManualMarketDataProvider:
    """Load rates points from in-memory dictionaries.

    This is useful for early prototypes and tests.
    """

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def load_rates_points(self) -> pd.DataFrame:
        frame = pd.DataFrame(self.rows)
        validate_rates_points_frame(frame)
        return frame


def validate_rates_points_frame(frame: pd.DataFrame) -> None:
    """Validate the minimum normalized market data schema."""

    missing = [column for column in REQUIRED_MARKET_DATA_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing required market data columns: {missing}")

    if frame.empty:
        raise ValueError("Market data frame must not be empty")
