from pathlib import Path

import pandas as pd
import pytest

from shiori_pricing_lab.data.providers import (
    REQUIRED_MARKET_DATA_COLUMNS,
    CSVMarketDataProvider,
    ManualMarketDataProvider,
    validate_rates_points_frame,
)
from shiori_pricing_lab.data.snapshot import MarketDataSnapshot

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


def _valid_row() -> dict:
    return {
        "date": "2026-06-10",
        "ticker": "UST_10Y",
        "tenor": "10Y",
        "value": 0.042,
        "data_type": "yield",
        "source": "synthetic",
    }


def test_csv_market_data_provider_loads_sample_data():
    provider = CSVMarketDataProvider(EXAMPLES_DIR / "sample_market_data.csv")
    frame = provider.load_rates_points()

    assert not frame.empty
    assert set(["date", "ticker", "tenor", "value", "data_type", "source"]).issubset(frame.columns)
    assert len(frame) == 4


def test_manual_market_data_provider_loads_rows():
    provider = ManualMarketDataProvider(
        [
            {
                "date": "2026-06-10",
                "ticker": "UST_10Y",
                "tenor": "10Y",
                "value": 0.042,
                "data_type": "yield",
                "source": "manual",
            }
        ]
    )
    frame = provider.load_rates_points()

    assert len(frame) == 1
    assert frame.iloc[0]["ticker"] == "UST_10Y"


def test_validate_rates_points_frame_raises_on_missing_columns():
    # Drop one required column; the rest are present.
    frame = pd.DataFrame([_valid_row()]).drop(columns=["tenor"])

    with pytest.raises(ValueError, match="Missing required market data columns"):
        validate_rates_points_frame(frame)


def test_validate_rates_points_frame_raises_on_empty_frame_with_correct_columns():
    # All required columns present, but no rows.
    frame = pd.DataFrame(columns=REQUIRED_MARKET_DATA_COLUMNS)

    with pytest.raises(ValueError, match="must not be empty"):
        validate_rates_points_frame(frame)


def test_manual_provider_surfaces_missing_column_validation():
    # The provider runs the same validator, so the error surfaces on load.
    row = _valid_row()
    del row["source"]
    provider = ManualMarketDataProvider([row])

    with pytest.raises(ValueError, match="Missing required market data columns"):
        provider.load_rates_points()


def test_snapshot_from_rates_points_rejects_missing_columns():
    # MarketDataSnapshot validates through the same function before filtering.
    frame = pd.DataFrame([_valid_row()]).drop(columns=["value"])

    with pytest.raises(ValueError, match="Missing required market data columns"):
        MarketDataSnapshot.from_rates_points(
            frame, valuation_date="2026-06-10", source="synthetic"
        )
