from pathlib import Path

from shiori_pricing_lab.data.providers import CSVMarketDataProvider, ManualMarketDataProvider


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


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
