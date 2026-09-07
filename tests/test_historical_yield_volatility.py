"""Deterministic tests for the Middle Office Historical Yield Vol calculator
(Issue #197).

Every observation below is invented. No Bloomberg value, no real Yield
series, and no real field mnemonic appears in this file -- the production
loader has no default field precisely because the real one is workstation
evidence, not repository content.

The numeric fixtures are hand-computable on purpose: the small window's
Yield Changes are exact one-digit decimals whose sample standard deviation
can be written down in closed form, so a change to the arithmetic fails here
rather than in a parity run months later.
"""

from __future__ import annotations

import dataclasses
import math
import statistics
import sys
from datetime import UTC, date, datetime, timedelta

import pytest

import shiori_pricing_lab.data.historical_yield_volatility as module
from shiori_pricing_lab.data.bli_snapshot import BLIMarketDataStatus, BLIVolatilityBasis
from shiori_pricing_lab.data.bloomberg_bond_yield_history import (
    BloombergBondYieldHistory,
    BondYieldObservation,
)
from shiori_pricing_lab.data.historical_yield_volatility import (
    ANNUALIZATION_FACTOR,
    ANNUALIZATION_TRADING_DAYS,
    HISTORICAL_YIELD_VOL_MO_SOURCE,
    MIDDLE_OFFICE_6M_OBSERVATION_COUNT,
    PUBLISHED_VOLATILITY_UNIT,
    STANDARD_DEVIATION_CONVENTION,
    HistoricalYieldVolInputError,
    HistoricalYieldVolStatus,
    HistoricalYieldVolUnavailableError,
    calculate_historical_yield_volatility,
    decimal_annual_normalization_factor,
    historical_yield_vol_volatility_input,
    result_shape_problem,
)

_START = date(2026, 1, 1)


def _default_end_date(dates: list | None, values: list) -> date:
    """The latest real date in the fixture, or a span wide enough for the default.

    Deliberately tolerant of the deliberately-malformed date lists a few tests
    pass: those exist to be refused by the calculator's own date typing, and
    the fixture must not raise first and hide which guard fired.
    """

    if dates:
        real = [d for d in dates if type(d) is date]
        if real:
            return max(real)
    return _START + timedelta(days=max(len(values), 1) * 2)


def _history(
    values: list[float | None],
    *,
    field_unit: str | None = "PERCENT",
    dates: list[date] | None = None,
    security: str = "/isin/US0000000000",
    requested_start_date: date | None = None,
    requested_end_date: date | None = None,
) -> BloombergBondYieldHistory:
    """One synthetic #196 series: consecutive dates unless ``dates`` says otherwise."""

    if dates is None:
        dates = [_START + timedelta(days=index) for index in range(len(values))]
    observations = tuple(
        BondYieldObservation(
            observation_date=observation_date,
            yield_value=value,
            raw_value=None if value is None else repr(value),
        )
        for observation_date, value in zip(dates, values, strict=True)
    )
    return BloombergBondYieldHistory(
        requested_identifier=security,
        security=security,
        yield_field="SYNTHETIC_TEST_YIELD_FIELD",
        field_meaning="synthetic test field",
        field_unit=field_unit,
        # Derived from the dates actually used, so a fixture is always
        # internally consistent: a real #196 series can never carry an
        # observation outside the range it reports, because the loader
        # refuses one, and the calculator now refuses one too.
        requested_start_date=_START if requested_start_date is None else requested_start_date,
        requested_end_date=(
            _default_end_date(dates, values) if requested_end_date is None else requested_end_date
        ),
        observations=observations,
        source_system="BLOOMBERG_DAPI",
        acquired_at="2026-09-07T09:00:00+08:00",
    )


# --- The provisional convention, pinned ------------------------------------


def test_one_hundred_eighty_observations_produce_exactly_one_hundred_seventy_nine_changes():
    values = [4.0 + (index % 7) * 0.01 for index in range(MIDDLE_OFFICE_6M_OBSERVATION_COUNT)]
    result = calculate_historical_yield_volatility(_history(values))

    assert result.observation_count == 180
    assert result.requested_observation_count == 180
    assert result.yield_change_count == 179
    assert result.window_status is HistoricalYieldVolStatus.FULL_WINDOW
    assert result.blockers == ()
    assert result.warnings == ()
    assert result.is_usable is True


def test_daily_changes_are_exactly_current_minus_previous():
    # Four observations -> three changes of +0.10, -0.30, +0.50 (in the
    # field's own unit). Sample standard deviation, ddof=1, of those three:
    #   mean = 0.10, deviations = 0.00, -0.40, +0.40
    #   variance = (0 + 0.16 + 0.16) / 2 = 0.16 -> sigma_daily = 0.40
    result = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )

    assert result.yield_change_count == 3
    assert result.daily_yield_vol == pytest.approx(0.4, abs=1e-12)
    assert result.annualized_yield_vol == pytest.approx(0.4 * math.sqrt(252), abs=1e-12)


def test_sample_standard_deviation_ddof_one_is_used_not_population():
    values = [4.0, 4.1, 3.8, 4.3, 4.05]
    result = calculate_historical_yield_volatility(
        _history(values), requested_observation_count=len(values)
    )
    changes = [b - a for a, b in zip(values, values[1:], strict=False)]

    assert result.daily_yield_vol == pytest.approx(statistics.stdev(changes), rel=1e-12)
    assert result.daily_yield_vol != pytest.approx(statistics.pstdev(changes), rel=1e-9)
    assert result.standard_deviation_convention == STANDARD_DEVIATION_CONVENTION


def test_annualization_is_exactly_daily_times_sqrt_252():
    result = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )

    assert result.annualization_trading_days == ANNUALIZATION_TRADING_DAYS == 252
    assert result.annualization_factor == ANNUALIZATION_FACTOR == math.sqrt(252)
    assert result.annualized_yield_vol == result.daily_yield_vol * math.sqrt(252)


def test_the_window_is_the_most_recent_observations_not_the_earliest():
    # Ten observations, a four-observation window: the last four are used and
    # the earlier six never enter a change.
    values = [9.0, 9.0, 9.0, 9.0, 9.0, 9.0, 4.00, 4.10, 3.80, 4.30]
    result = calculate_historical_yield_volatility(
        _history(values), requested_observation_count=4
    )

    assert result.series_observation_count == 10
    assert result.observation_count == 4
    assert result.first_observation_date == _START + timedelta(days=6)
    assert result.last_observation_date == _START + timedelta(days=9)
    assert result.daily_yield_vol == pytest.approx(0.4, abs=1e-12)


# --- Units ------------------------------------------------------------------


def test_the_volatility_carries_the_yield_field_unit_verbatim_and_unconverted():
    values = [4.00, 4.10, 3.80, 4.30]
    percent = calculate_historical_yield_volatility(
        _history(values, field_unit="PERCENT"), requested_observation_count=4
    )
    basis_points = calculate_historical_yield_volatility(
        _history(values, field_unit="BASIS_POINTS"), requested_observation_count=4
    )

    assert percent.field_unit == "PERCENT"
    assert basis_points.field_unit == "BASIS_POINTS"
    # Same numbers in, same number out: the unit label is provenance, never a
    # scaling instruction.
    assert percent.annualized_yield_vol == basis_points.annualized_yield_vol


def test_an_unconfirmed_unit_stays_unconfirmed():
    result = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30], field_unit=None), requested_observation_count=4
    )

    assert result.field_unit is None
    assert result.annualized_yield_vol is not None


# --- Missing dates are not filled ------------------------------------------


def test_missing_calendar_dates_are_not_filled_in():
    # A ten-day calendar span holding four observations: the changes are
    # between consecutive *returned observations*, and no row is manufactured
    # for the dates in between.
    dates = [
        _START,
        _START + timedelta(days=4),
        _START + timedelta(days=5),
        _START + timedelta(days=9),
    ]
    result = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30], dates=dates), requested_observation_count=4
    )

    assert result.observation_count == 4
    assert result.yield_change_count == 3
    assert result.observation_dates == tuple(dates)
    assert result.daily_yield_vol == pytest.approx(0.4, abs=1e-12)


# --- Fail closed ------------------------------------------------------------


def test_a_row_with_no_value_inside_the_window_fails_closed():
    with pytest.raises(HistoricalYieldVolInputError) as excinfo:
        calculate_historical_yield_volatility(
            _history([4.00, None, 3.80, 4.30]), requested_observation_count=4
        )

    assert "no Yield value" in str(excinfo.value)
    assert (_START + timedelta(days=1)).isoformat() in str(excinfo.value)


def test_a_row_with_no_value_outside_the_window_is_irrelevant():
    result = calculate_historical_yield_volatility(
        _history([None, 4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )

    assert result.observation_count == 4
    assert result.daily_yield_vol == pytest.approx(0.4, abs=1e-12)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_yield_value_fails_closed(bad):
    with pytest.raises(HistoricalYieldVolInputError) as excinfo:
        calculate_historical_yield_volatility(
            _history([4.00, bad, 3.80, 4.30]), requested_observation_count=4
        )

    assert "finite" in str(excinfo.value)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        # The subtraction itself overflows: statistics.stdev is then handed an
        # inf and raises AttributeError, which the route turned into HTTP 500.
        ([1e308, -1e308, 1e308], "Yield Change"),
        # Every change is finite, the daily sigma is finite, and annualizing it
        # is not -- this one returned inf as a usable volatility with no
        # blocker at all until Codex caught it (PR #200).
        ([0.0, 1e308, 0.0], "annualized Historical Yield Vol"),
        ([0.0, 1e307, 0.0], "annualized Historical Yield Vol"),
    ],
)
def test_finite_observations_whose_arithmetic_overflows_fail_closed(values, expected):
    with pytest.raises(HistoricalYieldVolInputError) as excinfo:
        calculate_historical_yield_volatility(
            _history(values), requested_observation_count=len(values)
        )

    assert expected in str(excinfo.value)
    assert "not a finite number" in str(excinfo.value)


def test_an_overflowing_window_never_reports_an_infinite_volatility():
    # The property behind the three cases above: no result this module returns
    # ever carries a non-finite figure, however extreme the observations are.
    for values in ([1e308, -1e308, 1e308], [0.0, 1e308, 0.0], [1e200, -1e200, 1e200, -1e200]):
        try:
            result = calculate_historical_yield_volatility(
                _history(values), requested_observation_count=len(values)
            )
        except HistoricalYieldVolInputError:
            continue
        assert result.daily_yield_vol is None or math.isfinite(result.daily_yield_vol)
        assert result.annualized_yield_vol is None or math.isfinite(result.annualized_yield_vol)


def test_a_standard_deviation_that_underflows_to_zero_fails_closed():
    # Changes [5e-324, 0.0, 0.0, 0.0] are NOT all equal, so the exact ddof=1
    # standard deviation is strictly positive -- and it rounds to 0.0 coming
    # back to a float. Reporting that as a usable zero volatility would be a
    # false risk figure dressed as a flat window (Codex review, PR #200).
    with pytest.raises(HistoricalYieldVolInputError) as excinfo:
        calculate_historical_yield_volatility(
            _history([0.0, 5e-324, 5e-324, 5e-324, 5e-324]), requested_observation_count=5
        )

    assert "underflows to zero" in str(excinfo.value)


def test_a_genuinely_flat_window_is_still_a_legitimate_zero():
    # The other side of that guard: equal changes really do have sigma zero,
    # and that is a result, not a refusal.
    result = calculate_historical_yield_volatility(
        _history([4.0, 5.0, 6.0, 7.0]), requested_observation_count=4
    )

    assert result.daily_yield_vol == 0.0
    assert result.blockers == ()


def test_a_standard_deviation_that_overflows_raises_this_modules_error():
    # statistics.stdev accumulates exactly but converts back to a float at the
    # end, and that conversion can overflow on changes this module has already
    # checked finite -- raising OverflowError, not ValueError, so it escaped
    # as an HTTP 500 under a docstring promising one error type.
    with pytest.raises(HistoricalYieldVolInputError) as excinfo:
        calculate_historical_yield_volatility(
            _history([0.0, sys.float_info.max, 0.0]), requested_observation_count=3
        )

    assert "cannot be represented as a finite number" in str(excinfo.value)


def test_an_integer_observation_beyond_the_float_range_fails_closed():
    # math.isfinite and float() both raise OverflowError -- not ValueError --
    # on an int this large, from a hand-built or future producer.
    with pytest.raises(HistoricalYieldVolInputError) as excinfo:
        calculate_historical_yield_volatility(
            _history([10**400, 1, 2, 3]), requested_observation_count=4
        )

    assert "not a usable Yield value" in str(excinfo.value)


@pytest.mark.parametrize(
    "dates",
    [
        [_START, None, _START + timedelta(days=2), _START + timedelta(days=3)],
        ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"],
        [_START, datetime(2026, 1, 2), _START + timedelta(days=2), _START + timedelta(days=3)],
    ],
)
def test_observation_dates_are_typed_before_they_are_ordered(dates):
    # `<=` on a mixed date/None sequence raises TypeError, and an all-string
    # sequence orders lexicographically, passes, and produces a result whose
    # .isoformat() blows up in the route later (Codex review, PR #200).
    with pytest.raises(HistoricalYieldVolInputError) as excinfo:
        calculate_historical_yield_volatility(
            _history([4.00, 4.10, 3.80, 4.30], dates=dates), requested_observation_count=4
        )

    assert "calendar date" in str(excinfo.value)


def test_observations_outside_the_declared_range_fail_closed():
    # The result copies the declared range verbatim and the route serializes
    # it as this calculation's provenance, so a window built from observations
    # outside it would publish false request provenance (Codex review, #200).
    with pytest.raises(HistoricalYieldVolInputError) as excinfo:
        calculate_historical_yield_volatility(
            _history(
                [4.00, 4.10, 3.80, 4.30],
                requested_start_date=_START + timedelta(days=1),
                requested_end_date=_START + timedelta(days=2),
            ),
            requested_observation_count=4,
        )

    assert "outside the declared range" in str(excinfo.value)


def test_an_inverted_declared_range_fails_closed():
    with pytest.raises(HistoricalYieldVolInputError) as excinfo:
        calculate_historical_yield_volatility(
            _history(
                [4.00, 4.10, 3.80, 4.30],
                requested_start_date=_START + timedelta(days=90),
                requested_end_date=_START,
            ),
            requested_observation_count=4,
        )

    assert "after it ends" in str(excinfo.value)


def test_observations_out_of_chronological_order_fail_closed():
    dates = [
        _START,
        _START + timedelta(days=3),
        _START + timedelta(days=1),
        _START + timedelta(days=4),
    ]
    with pytest.raises(HistoricalYieldVolInputError) as excinfo:
        calculate_historical_yield_volatility(
            _history([4.00, 4.10, 3.80, 4.30], dates=dates), requested_observation_count=4
        )

    assert "strictly ascending" in str(excinfo.value)


@pytest.mark.parametrize("bad", [0, 1, 2, -180, "180", 180.0, True, None])
def test_an_unusable_requested_observation_count_fails_closed(bad):
    with pytest.raises(HistoricalYieldVolInputError):
        calculate_historical_yield_volatility(
            _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=bad
        )


def test_something_other_than_a_196_history_fails_closed():
    with pytest.raises(HistoricalYieldVolInputError):
        calculate_historical_yield_volatility({"observations": []})


# --- Insufficient and zero history -----------------------------------------


def test_short_history_is_labelled_insufficient_with_both_counts():
    values = [4.0 + (index % 5) * 0.02 for index in range(90)]
    result = calculate_historical_yield_volatility(_history(values))

    assert result.window_status is HistoricalYieldVolStatus.INSUFFICIENT_HISTORY
    assert result.requested_observation_count == 180
    assert result.observation_count == 90
    assert result.yield_change_count == 89
    assert result.annualized_yield_vol is not None
    # A short window that still supports the convention is a WARNING, never a
    # blocker: a consumer that discarded it would have thrown away the only
    # honest answer available for this bond (Codex review, PR #200).
    assert result.blockers == ()
    assert result.is_usable is True
    assert any("INSUFFICIENT_HISTORY" in warning for warning in result.warnings)
    assert any("90 of the requested 180" in warning for warning in result.warnings)


def test_short_history_is_never_flat_extended_to_the_requested_window():
    ninety = calculate_historical_yield_volatility(
        _history([4.0 + (index % 5) * 0.02 for index in range(90)])
    )

    assert ninety.observation_count == 90
    assert len(ninety.observation_dates) == 90
    # No date, no observation and no change was manufactured to reach 180, and
    # the result never reports the requested count as though it had been met.
    assert ninety.observation_count != ninety.requested_observation_count


def test_zero_history_is_blocking_and_offers_no_proxy():
    result = calculate_historical_yield_volatility(_history([]))

    assert result.window_status is HistoricalYieldVolStatus.NO_HISTORY
    assert result.observation_count == 0
    assert result.yield_change_count == 0
    assert result.daily_yield_vol is None
    assert result.annualized_yield_vol is None
    assert result.is_usable is False
    assert result.blockers
    assert "no approved proxy" in result.blockers[0]
    assert result.warnings == ()


def test_history_too_short_for_a_sample_standard_deviation_reports_no_number():
    result = calculate_historical_yield_volatility(
        _history([4.00, 4.10]), requested_observation_count=180
    )

    assert result.window_status is HistoricalYieldVolStatus.INSUFFICIENT_HISTORY
    assert result.yield_change_count == 1
    assert result.daily_yield_vol is None
    assert result.annualized_yield_vol is None
    # Both, and each in its own bucket: the window is short (warning) AND too
    # short to produce a standard deviation at all (blocker).
    assert len(result.warnings) == 1
    assert len(result.blockers) == 1
    assert result.is_usable is False


@pytest.mark.parametrize(
    "values",
    [
        [],                                                       # NO_HISTORY
        [4.00, 4.10],                                             # too few changes
        [4.0 + (index % 5) * 0.02 for index in range(90)],         # short but usable
        [4.0 + (index % 7) * 0.01 for index in range(180)],        # full window
    ],
)
def test_blockers_are_fatal_and_warnings_are_not(values):
    """The invariant the dataclass documents, checked on every shape."""

    result = calculate_historical_yield_volatility(_history(values))

    if result.blockers:
        assert result.daily_yield_vol is None
        assert result.annualized_yield_vol is None
        assert result.is_usable is False
    else:
        assert result.daily_yield_vol is not None
        assert result.annualized_yield_vol is not None
        assert result.is_usable is True
    # A warning never implies the absence of a number on its own.
    assert result.is_usable == (not result.blockers)


# --- Provenance -------------------------------------------------------------


def test_provenance_travels_with_the_result():
    result = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )

    assert result.requested_identifier == "/isin/US0000000000"
    assert result.security == "/isin/US0000000000"
    assert result.yield_field == "SYNTHETIC_TEST_YIELD_FIELD"
    assert result.field_meaning == "synthetic test field"
    assert result.source_system == "BLOOMBERG_DAPI"
    assert result.acquired_at == "2026-09-07T09:00:00+08:00"
    assert result.requested_start_date == _START
    assert result.calculated_at


def test_the_statistic_does_not_depend_on_the_calculation_clock(monkeypatch):
    history = _history([4.00, 4.10, 3.80, 4.30])
    monkeypatch.setattr(module, "_calculation_now", lambda: datetime(2020, 1, 1, tzinfo=UTC))
    early = calculate_historical_yield_volatility(history, requested_observation_count=4)
    monkeypatch.setattr(module, "_calculation_now", lambda: datetime(2099, 12, 31, tzinfo=UTC))
    late = calculate_historical_yield_volatility(history, requested_observation_count=4)

    assert early.calculated_at != late.calculated_at
    assert early.observation_dates == late.observation_dates
    assert early.annualized_yield_vol == late.annualized_yield_vol


# --- The normalized volatility source --------------------------------------


def test_a_full_window_publishes_as_the_historical_yield_vol_mo_source():
    result = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )
    published = historical_yield_vol_volatility_input(result)

    assert published.source_system == HISTORICAL_YIELD_VOL_MO_SOURCE == "HISTORICAL_YIELD_VOL_MO"
    assert published.volatility_basis is BLIVolatilityBasis.YIELD_VOL
    assert published.status is BLIMarketDataStatus.ACTIVE
    # A PERCENT field's vol enters the decimal-annual contract as a decimal.
    assert published.volatility == pytest.approx(result.annualized_yield_vol / 100, rel=1e-12)


# --- Publication is normalized to DECIMAL_ANNUAL (docs/30 §1, Annex A §A.8.1) --


@pytest.mark.parametrize(
    ("unit", "factor"),
    [("DECIMAL", 1.0), ("PERCENT", 1e-2), ("BASIS_POINTS", 1e-4), ("  percent  ", 1e-2)],
)
def test_a_declared_unit_normalizes_by_its_own_explicit_factor(unit, factor):
    result = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30], field_unit=unit), requested_observation_count=4
    )
    published = historical_yield_vol_volatility_input(result)

    assert decimal_annual_normalization_factor(unit) == factor
    assert published.volatility == pytest.approx(result.annualized_yield_vol * factor, rel=1e-12)
    # The calculated result itself is never rescaled -- that is the number the
    # Middle Office parity run compares against.
    assert result.annualized_yield_vol == pytest.approx(0.4 * math.sqrt(252), abs=1e-12)


def test_the_published_value_is_never_the_raw_percent_figure():
    result = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30], field_unit="PERCENT"), requested_observation_count=4
    )
    published = historical_yield_vol_volatility_input(result)

    # 6.35-ish percentage points must not enter a decimal-annual risk
    # contract as 6.35 -- that is a hundred-times-too-large volatility.
    assert result.annualized_yield_vol > 6
    assert published.volatility < 0.1


def test_a_unit_outside_the_supported_vocabulary_refuses_publication():
    result = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30], field_unit="PERCENTAGE POINTS"),
        requested_observation_count=4,
    )

    with pytest.raises(HistoricalYieldVolUnavailableError) as excinfo:
        historical_yield_vol_volatility_input(result)

    assert "no approved normalization" in str(excinfo.value)
    # The refusal names what would be accepted, so a trader knows what to
    # confirm rather than guessing.
    for supported in ("DECIMAL", "PERCENT", "BASIS_POINTS"):
        assert supported in str(excinfo.value)


@pytest.mark.parametrize("bad", [None, "", "   ", 100, 1e-2])
def test_an_undeclared_unit_never_falls_back_to_a_factor(bad):
    with pytest.raises(HistoricalYieldVolUnavailableError):
        decimal_annual_normalization_factor(bad)


def test_the_publication_unit_is_the_one_the_contract_states():
    assert PUBLISHED_VOLATILITY_UNIT == "DECIMAL_ANNUAL"


def test_the_audit_records_the_source_unit_and_the_factor_applied():
    result = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30], field_unit="BASIS_POINTS"),
        requested_observation_count=4,
    )
    audit = historical_yield_vol_volatility_input(result).override_or_fallback_audit

    assert audit is not None
    assert "BASIS_POINTS" in audit
    assert "DECIMAL_ANNUAL" in audit
    assert "0.0001" in audit
    assert "FULL_WINDOW" in audit


def test_the_source_is_distinguishable_from_vcub_manual_and_price_vol():
    published = historical_yield_vol_volatility_input(
        calculate_historical_yield_volatility(
            _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
        )
    )

    assert published.source_system not in {"BLOOMBERG_DAPI", "MANUAL_TRADER_ENTRY", "VCUB"}
    assert published.volatility_basis is not BLIVolatilityBasis.PRICE_VOL
    assert published.volatility_basis is not BLIVolatilityBasis.EQUIVALENT_PRICE_VOL


def test_an_insufficient_window_publishes_only_with_an_explicit_audit():
    result = calculate_historical_yield_volatility(
        _history([4.0 + (index % 5) * 0.02 for index in range(90)])
    )
    published = historical_yield_vol_volatility_input(result)

    assert published.override_or_fallback_audit is not None
    assert "INSUFFICIENT_HISTORY" in published.override_or_fallback_audit
    assert "90 of the requested 180" in published.override_or_fallback_audit


def test_zero_history_cannot_be_published_as_a_volatility_source():
    with pytest.raises(HistoricalYieldVolUnavailableError) as excinfo:
        historical_yield_vol_volatility_input(calculate_historical_yield_volatility(_history([])))

    assert "NO_HISTORY" in str(excinfo.value)


def test_an_unconfirmed_unit_cannot_be_published_as_a_volatility_source():
    result = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30], field_unit=None), requested_observation_count=4
    )

    with pytest.raises(HistoricalYieldVolUnavailableError) as excinfo:
        historical_yield_vol_volatility_input(result)

    assert "unit was not established" in str(excinfo.value)


def test_a_degenerate_zero_vol_window_is_not_published_as_a_volatility():
    # Four yields a whole unit apart: every Yield Change is exactly 1.0, so the
    # sample standard deviation is exactly zero -- a real number, but not a
    # volatility anything should be allowed to consume.
    result = calculate_historical_yield_volatility(
        _history([4.0, 5.0, 6.0, 7.0], field_unit="PERCENT"), requested_observation_count=4
    )

    assert result.daily_yield_vol == 0.0
    assert result.annualized_yield_vol == 0.0
    with pytest.raises(HistoricalYieldVolUnavailableError) as excinfo:
        historical_yield_vol_volatility_input(result)

    assert "degenerate" in str(excinfo.value)


def test_a_vol_that_underflows_its_own_unit_conversion_says_so():
    # 0.0 / 5e-324 / 0.0 in PERCENT: the changes are NOT identical and the
    # calculated vol is strictly positive, but x 1e-2 underflows it to zero.
    # The refusal must name that cause, not blame a flat window it never saw
    # (found while probing the boundary Codex's non-finite finding pointed at).
    result = calculate_historical_yield_volatility(
        _history([0.0, 5e-324, 0.0, 5e-324], field_unit="PERCENT"),
        requested_observation_count=4,
    )

    assert result.annualized_yield_vol > 0
    with pytest.raises(HistoricalYieldVolUnavailableError) as excinfo:
        historical_yield_vol_volatility_input(result)

    assert "underflowed to zero" in str(excinfo.value)
    assert "identical" not in str(excinfo.value)


def test_a_genuinely_flat_window_still_names_the_flat_window():
    result = calculate_historical_yield_volatility(
        _history([4.0, 5.0, 6.0, 7.0], field_unit="PERCENT"), requested_observation_count=4
    )

    assert result.annualized_yield_vol == 0.0
    with pytest.raises(HistoricalYieldVolUnavailableError) as excinfo:
        historical_yield_vol_volatility_input(result)

    assert "identical" in str(excinfo.value)
    assert "underflowed" not in str(excinfo.value)


@pytest.mark.parametrize(
    "bad",
    ["0.06", complex(1, 1), float("inf"), float("-inf"), float("nan"), True, []],
)
def test_a_result_carrying_a_malformed_volatility_fails_through_our_error(bad):
    """The publication helper validates the figure it is handed.

    A directly constructed or future result can carry anything there. Before
    this guard a string or complex raised TypeError out of the normalization,
    inf reached BLIVolatilityInput and raised its raw ValueError, and NaN was
    reported as a flat window it never was -- four different ways for a helper
    documenting one error type to deliver another (Codex review, PR #200).
    """

    base = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )
    result = dataclasses.replace(base, annualized_yield_vol=bad)

    with pytest.raises(HistoricalYieldVolUnavailableError) as excinfo:
        historical_yield_vol_volatility_input(result)

    assert "not a finite number" in str(excinfo.value)
    # And never the flat-window explanation, which none of these are.
    assert "identical" not in str(excinfo.value)


def test_a_result_carrying_a_blocker_is_never_published():
    # The dataclass says every blocker is fatal. Until this guard, a directly
    # constructed result with a blocker AND a finite figure published as an
    # ACTIVE risk source -- exposing a number the result itself says must not
    # be used (Codex review, PR #200).
    base = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )
    result = dataclasses.replace(base, blockers=("this result must not be used",))

    with pytest.raises(HistoricalYieldVolUnavailableError) as excinfo:
        historical_yield_vol_volatility_input(result)

    assert "must not be used" in str(excinfo.value)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"observation_count": -1}, "non-negative int"),
        ({"yield_change_count": 999}, "do not describe one calculation"),
        ({"requested_observation_count": True}, "non-negative int"),
        ({"observation_count": 9, "yield_change_count": 8}, "more than were asked for"),
        ({"series_observation_count": 1}, "from a series of"),
        ({"requested_observation_count": 180}, "which is INSUFFICIENT_HISTORY"),
        ({"window_status": "FULL_WINDOW"}, "must be a HistoricalYieldVolStatus"),
        ({"annualized_yield_vol": None}, "no blocker explaining why"),
    ],
)
def test_a_result_whose_counts_contradict_themselves_is_never_published(overrides, expected):
    """Counts are copied verbatim into the audit, so an unchecked one is
    fabricated calculation provenance travelling under this module's name."""

    base = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )
    result = dataclasses.replace(base, **overrides)

    with pytest.raises(HistoricalYieldVolUnavailableError) as excinfo:
        historical_yield_vol_volatility_input(result)

    assert expected in str(excinfo.value)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"standard_deviation_convention": "POPULATION_STDEV_P"}, "cannot be published"),
        ({"annualization_trading_days": 365}, "annualizes by sqrt(252)"),
        ({"annualization_factor": 19.1}, "annualizes by sqrt(252)"),
    ],
)
def test_a_result_claiming_another_methodology_is_never_published(overrides, expected):
    """The canonical source label names one methodology.

    A result claiming POPULATION/365 published an audit asserting exactly that
    under HISTORICAL_YIELD_VOL_MO, whatever its number was computed with
    (Codex review, PR #200).
    """

    base = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )

    with pytest.raises(HistoricalYieldVolUnavailableError) as excinfo:
        historical_yield_vol_volatility_input(dataclasses.replace(base, **overrides))

    assert expected in str(excinfo.value)


@pytest.mark.parametrize(
    ("daily", "expected"),
    [
        (None, "not a finite number"),
        ("x", "not a finite number"),
        (float("inf"), "not a finite number"),
        (float("nan"), "not a finite number"),
        (-0.4, "never negative"),
        (99.0, "times sqrt(252)"),
    ],
)
def test_the_daily_figure_is_validated_alongside_the_annualized_one(daily, expected):
    # The gate was one-sided: a missing, non-finite or simply wrong daily
    # sigma published beside a valid annualized one, and the card drew a dash
    # or a bogus daily headline next to a live source.
    base = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )

    with pytest.raises(HistoricalYieldVolUnavailableError) as excinfo:
        historical_yield_vol_volatility_input(dataclasses.replace(base, daily_yield_vol=daily))

    assert expected in str(excinfo.value)


def test_a_no_history_result_carrying_a_figure_is_never_published():
    # observation_count=0 with a positive figure derived the same NO_HISTORY
    # status and passed, publishing an ACTIVE source whose audit read
    # "calculated from 0 of the requested 0 Yield observations (0 Yield
    # Changes)" (Codex review, PR #200).
    base = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )
    result = dataclasses.replace(
        base,
        observation_count=0,
        yield_change_count=0,
        series_observation_count=0,
        requested_observation_count=0,
        window_status=HistoricalYieldVolStatus.NO_HISTORY,
    )

    with pytest.raises(HistoricalYieldVolUnavailableError) as excinfo:
        historical_yield_vol_volatility_input(result)

    assert "0 Yield Change(s)" in str(excinfo.value)


def test_the_shared_shape_check_is_what_both_consumers_use():
    """One shape checker, two error types.

    The route needs HistoricalYieldVolInputError for its HTTP 400 and the
    publication helper promises HistoricalYieldVolUnavailableError. Sharing a
    raiser would have one of them breaking the contract the other keeps, so
    the shared function returns a message and each caller raises its own type
    (Codex review, PR #200).
    """

    base = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )

    assert result_shape_problem(base) is None
    assert "must be a HistoricalYieldVolStatus" in str(
        result_shape_problem(dataclasses.replace(base, window_status="FULL_WINDOW"))
    )
    # A NO_HISTORY result is malformed to nobody: it serializes fine and is
    # simply not publishable, which is the distinction the split exists for.
    empty = calculate_historical_yield_volatility(_history([]))
    assert result_shape_problem(empty) is None
    with pytest.raises(HistoricalYieldVolUnavailableError):
        historical_yield_vol_volatility_input(empty)


def test_publishing_something_other_than_a_result_fails_closed():
    with pytest.raises(HistoricalYieldVolUnavailableError):
        historical_yield_vol_volatility_input({"annualized_yield_vol": 1.0})


# --- No pricing / VCUB / Forward side effects -------------------------------


def test_the_yield_vol_basis_is_still_refused_by_the_pricing_input_guard():
    # The boundary this issue must not cross: nothing here makes YIELD_VOL
    # priceable. The guard's supported set is unchanged, so a
    # HISTORICAL_YIELD_VOL_MO input cannot reach Black-76 through it.
    from shiori_pricing_lab.pricing.bli_mvp_required_input_guard import (
        _SUPPORTED_VOLATILITY_BASES,
    )

    assert BLIVolatilityBasis.YIELD_VOL not in _SUPPORTED_VOLATILITY_BASES
    assert _SUPPORTED_VOLATILITY_BASES == frozenset(
        {BLIVolatilityBasis.PRICE_VOL, BLIVolatilityBasis.EQUIVALENT_PRICE_VOL}
    )
