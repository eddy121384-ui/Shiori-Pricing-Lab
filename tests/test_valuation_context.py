import pandas as pd
import pytest

from shiori_pricing_lab.data.providers import ManualMarketDataProvider
from shiori_pricing_lab.data.snapshot import MarketDataSnapshot
from shiori_pricing_lab.pricing.curve import RateCurve
from shiori_pricing_lab.valuation.context import ValuationContext


def _multi_date_frame() -> pd.DataFrame:
    """Synthetic frame spanning two valuation dates (mimics historical data)."""

    provider = ManualMarketDataProvider(
        [
            {
                "date": "2026-06-10",
                "ticker": "UST_2Y",
                "tenor": "2Y",
                "value": 0.043,
                "data_type": "yield",
                "source": "synthetic",
            },
            {
                "date": "2026-06-10",
                "ticker": "UST_10Y",
                "tenor": "10Y",
                "value": 0.042,
                "data_type": "yield",
                "source": "synthetic",
            },
            {
                "date": "2026-06-11",
                "ticker": "UST_2Y",
                "tenor": "2Y",
                "value": 0.044,
                "data_type": "yield",
                "source": "synthetic",
            },
        ]
    )
    return provider.load_rates_points()


def _snapshot(valuation_date: str = "2026-06-10") -> MarketDataSnapshot:
    return MarketDataSnapshot.from_rates_points(
        _multi_date_frame(), valuation_date=valuation_date, source="synthetic"
    )


def test_snapshot_wraps_synthetic_data_with_explicit_date():
    snapshot = _snapshot()

    assert snapshot.valuation_date == "2026-06-10"
    assert snapshot.source == "synthetic"
    assert snapshot.metadata == {}


def test_snapshot_filters_frame_to_requested_valuation_date():
    snapshot = _snapshot("2026-06-10")

    # The multi-date frame has rows for 2026-06-10 and 2026-06-11; only the
    # requested date should survive the filter.
    assert set(snapshot.rates_points["date"].astype(str)) == {"2026-06-10"}
    assert sorted(snapshot.rates_points["tenor"]) == ["10Y", "2Y"]


def test_snapshot_raises_when_no_rows_for_valuation_date():
    with pytest.raises(ValueError, match="No market data rows"):
        MarketDataSnapshot.from_rates_points(
            _multi_date_frame(), valuation_date="2026-06-12", source="synthetic"
        )


def test_snapshot_requires_explicit_valuation_date_argument():
    # valuation_date has no default; omitting it is a programming error.
    with pytest.raises(TypeError):
        MarketDataSnapshot.from_rates_points(_multi_date_frame(), source="synthetic")  # type: ignore[call-arg]


def test_context_creation_does_not_rely_on_system_date():
    snapshot = _snapshot("2026-06-10")
    context = ValuationContext(valuation_date="2026-06-10", market_snapshot=snapshot)

    assert context.valuation_date == "2026-06-10"
    assert context.reporting_currency == "USD"
    assert context.model_settings == {}
    assert context.scenario is None


def test_context_requires_explicit_valuation_date_argument():
    snapshot = _snapshot()
    with pytest.raises(TypeError):
        ValuationContext(market_snapshot=snapshot)  # type: ignore[call-arg]


def test_context_rejects_empty_valuation_date():
    snapshot = _snapshot()
    with pytest.raises(ValueError, match="non-empty"):
        ValuationContext(valuation_date="", market_snapshot=snapshot)


def test_context_rejects_snapshot_date_mismatch():
    snapshot = _snapshot("2026-06-10")
    with pytest.raises(ValueError, match="does not match"):
        ValuationContext(valuation_date="2026-06-11", market_snapshot=snapshot)


def test_model_settings_default_is_not_shared():
    snapshot = _snapshot()
    a = ValuationContext(valuation_date="2026-06-10", market_snapshot=snapshot)
    b = ValuationContext(valuation_date="2026-06-10", market_snapshot=snapshot)

    assert a.model_settings is not b.model_settings


def test_curve_construction_uses_snapshot_valuation_date():
    snapshot = _snapshot("2026-06-10")

    from_snapshot = RateCurve.from_snapshot(snapshot)
    from_context = ValuationContext(
        valuation_date="2026-06-10", market_snapshot=snapshot
    ).build_curve()

    assert from_snapshot.date == "2026-06-10"
    assert from_context.date == "2026-06-10"
    assert list(from_snapshot.to_frame()["tenor"]) == ["2Y", "10Y"]


@pytest.mark.parametrize("blank", ["", "   "])
def test_snapshot_rejects_blank_valuation_date(blank):
    with pytest.raises(ValueError, match="non-empty"):
        MarketDataSnapshot.from_rates_points(
            _multi_date_frame(), valuation_date=blank, source="synthetic"
        )


def test_mutating_input_frame_does_not_change_snapshot():
    frame = _multi_date_frame()
    snapshot = MarketDataSnapshot.from_rates_points(
        frame, valuation_date="2026-06-10", source="synthetic"
    )
    before = snapshot.rates_points["value"].tolist()

    # Mutate the original input frame in place after snapshot creation.
    frame.loc[:, "value"] = -1.0

    assert snapshot.rates_points["value"].tolist() == before


def test_mutating_exposed_rates_points_does_not_affect_snapshot_or_curve():
    snapshot = _snapshot("2026-06-10")
    before = snapshot.rates_points["value"].tolist()

    # Mutate the frame returned by the snapshot.
    exposed = snapshot.rates_points
    exposed.loc[:, "value"] = -1.0

    # Stored data is unchanged...
    assert snapshot.rates_points["value"].tolist() == before
    # ...and a curve built afterwards still reflects the original data.
    curve = RateCurve.from_snapshot(snapshot)
    assert curve.to_frame()["value"].tolist() == before


def test_build_curve_works_after_removing_to_rate_curve():
    snapshot = _snapshot("2026-06-10")
    context = ValuationContext(valuation_date="2026-06-10", market_snapshot=snapshot)

    # The data-layer helper was removed; curve construction lives in pricing.
    assert not hasattr(snapshot, "to_rate_curve")
    assert context.build_curve().date == "2026-06-10"


def test_data_layer_does_not_import_pricing_at_runtime():
    import sys

    # Drop any pricing modules, import the data snapshot module fresh, and
    # confirm the data layer pulls in no pricing module at runtime.
    for name in list(sys.modules):
        if name.startswith("shiori_pricing_lab.pricing") or name == (
            "shiori_pricing_lab.data.snapshot"
        ):
            del sys.modules[name]

    importlib = __import__("importlib")
    importlib.import_module("shiori_pricing_lab.data.snapshot")

    assert not any(name.startswith("shiori_pricing_lab.pricing") for name in sys.modules)
