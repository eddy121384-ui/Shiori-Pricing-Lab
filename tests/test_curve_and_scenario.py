import pytest

from shiori_pricing_lab.data.providers import ManualMarketDataProvider
from shiori_pricing_lab.pricing.curve import RateCurve, tenor_to_years
from shiori_pricing_lab.pricing.scenario import run_parallel_curve_shock


def _sample_curve() -> RateCurve:
    provider = ManualMarketDataProvider(
        [
            {
                "date": "2026-06-10",
                "ticker": "UST_2Y",
                "tenor": "2Y",
                "value": 0.043,
                "data_type": "yield",
                "source": "test",
            },
            {
                "date": "2026-06-10",
                "ticker": "UST_10Y",
                "tenor": "10Y",
                "value": 0.042,
                "data_type": "yield",
                "source": "test",
            },
        ]
    )
    return RateCurve.from_rates_points(provider.load_rates_points())


def test_tenor_to_years():
    assert tenor_to_years("2Y") == 2.0
    assert tenor_to_years("6M") == 0.5


def test_curve_builds_and_sorts_by_tenor_years():
    curve = _sample_curve()
    frame = curve.to_frame()

    assert list(frame["tenor"]) == ["2Y", "10Y"]
    assert list(frame["years"]) == [2.0, 10.0]


def test_parallel_curve_shock_adds_one_bp():
    curve = _sample_curve()
    shocked = curve.shocked_parallel(shock_bp=1)

    base_values = curve.to_frame()["value"].tolist()
    shocked_values = shocked.to_frame()["value"].tolist()

    assert shocked_values == [value + 0.0001 for value in base_values]


def test_scenario_output_has_change_bp():
    curve = _sample_curve()
    result = run_parallel_curve_shock(curve, shock_bp=5)
    frame = result.to_frame()

    assert set(["base_value", "shocked_value", "change_bp"]).issubset(frame.columns)
    # Compare with a tolerance: change_bp is derived from float arithmetic
    # (rate + shock) * 10_000, which is not bit-exact across numpy/pandas builds.
    assert frame["change_bp"].tolist() == pytest.approx([5.0, 5.0])
