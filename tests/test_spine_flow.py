"""End-to-end tests for the connected valuation spine.

Flow under test:

    provider -> MarketDataSnapshot -> ValuationContext -> RateCurve -> scenario

These tests use only synthetic / manual data and never rely on the system date.
"""

from pathlib import Path

import pandas as pd
import pytest

from shiori_pricing_lab.data.providers import CSVMarketDataProvider, ManualMarketDataProvider
from shiori_pricing_lab.data.snapshot import MarketDataSnapshot
from shiori_pricing_lab.pricing.scenario import run_parallel_curve_shock
from shiori_pricing_lab.valuation.context import ValuationContext

SAMPLE_DATA = Path(__file__).resolve().parents[1] / "examples" / "sample_market_data.csv"

# A fixed, explicit historical valuation date. It is deliberately not "today"
# so the tests prove the spine never reaches for the system date.
HISTORICAL_DATE = "2026-06-10"


def _manual_provider() -> ManualMarketDataProvider:
    return ManualMarketDataProvider(
        [
            {
                "date": HISTORICAL_DATE,
                "ticker": "UST_2Y",
                "tenor": "2Y",
                "value": 0.043,
                "data_type": "yield",
                "source": "synthetic",
            },
            {
                "date": HISTORICAL_DATE,
                "ticker": "UST_10Y",
                "tenor": "10Y",
                "value": 0.042,
                "data_type": "yield",
                "source": "synthetic",
            },
        ]
    )


def test_manual_provider_through_full_spine():
    frame = _manual_provider().load_rates_points()
    snapshot = MarketDataSnapshot.from_rates_points(
        frame, valuation_date=HISTORICAL_DATE, source="synthetic"
    )
    context = ValuationContext.from_snapshot(snapshot)
    curve = context.build_curve()

    # +1 bp and +5 bp produce the expected decimal rate changes.
    one_bp = run_parallel_curve_shock(curve, shock_bp=1)
    five_bp = run_parallel_curve_shock(curve, shock_bp=5)

    assert curve.date == HISTORICAL_DATE
    assert one_bp.to_frame()["change_bp"].tolist() == pytest.approx([1.0, 1.0])
    assert five_bp.to_frame()["change_bp"].tolist() == pytest.approx([5.0, 5.0])


def test_csv_provider_through_spine_to_curve():
    frame = CSVMarketDataProvider(SAMPLE_DATA).load_rates_points()
    snapshot = MarketDataSnapshot.from_rates_points(
        frame, valuation_date=HISTORICAL_DATE, source="synthetic"
    )
    context = ValuationContext.from_snapshot(snapshot)
    curve = context.build_curve()

    assert curve.date == HISTORICAL_DATE
    # Sample CSV tenors for this date, sorted by year fraction.
    assert list(curve.to_frame()["tenor"]) == ["2Y", "5Y", "10Y", "30Y"]


def test_multi_date_data_selects_only_requested_valuation_date():
    frame = pd.concat(
        [
            _manual_provider().load_rates_points(),
            ManualMarketDataProvider(
                [
                    {
                        "date": "2026-06-11",
                        "ticker": "UST_2Y",
                        "tenor": "2Y",
                        "value": 0.0445,
                        "data_type": "yield",
                        "source": "synthetic",
                    }
                ]
            ).load_rates_points(),
        ],
        ignore_index=True,
    )

    snapshot = MarketDataSnapshot.from_rates_points(
        frame, valuation_date=HISTORICAL_DATE, source="synthetic"
    )
    curve = ValuationContext.from_snapshot(snapshot).build_curve()

    assert set(curve.to_frame()["date"].astype(str)) == {HISTORICAL_DATE}
    assert sorted(curve.to_frame()["tenor"]) == ["10Y", "2Y"]


def test_fixed_historical_valuation_date_is_preserved_through_spine():
    frame = _manual_provider().load_rates_points()
    snapshot = MarketDataSnapshot.from_rates_points(
        frame, valuation_date=HISTORICAL_DATE, source="synthetic"
    )
    context = ValuationContext.from_snapshot(snapshot)

    # The explicit historical date flows unchanged through every layer.
    assert snapshot.valuation_date == HISTORICAL_DATE
    assert context.valuation_date == HISTORICAL_DATE
    assert context.build_curve().date == HISTORICAL_DATE


def test_from_snapshot_uses_snapshot_valuation_date():
    frame = _manual_provider().load_rates_points()
    snapshot = MarketDataSnapshot.from_rates_points(
        frame, valuation_date=HISTORICAL_DATE, source="synthetic"
    )

    context = ValuationContext.from_snapshot(snapshot)

    assert context.valuation_date == snapshot.valuation_date
    assert context.market_snapshot is snapshot
    assert context.reporting_currency == "USD"
