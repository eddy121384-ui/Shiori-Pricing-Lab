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
from fractions import Fraction

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
    MIDDLE_OFFICE_6M_YIELD_CHANGE_COUNT,
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
    source_system: str = "BLOOMBERG_DAPI",
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
        source_system=source_system,
        acquired_at="2026-09-07T09:00:00+08:00",
    )


# --- The parity-confirmed convention, pinned -------------------------------


def test_the_middle_office_horizon_is_180_changes_over_181_observations():
    # The correction the Middle Office parity UAT forced. "180-day" is 180
    # Yield CHANGES, and N changes need N+1 observations -- so the standard
    # window is 181 observations, not the 180 this module provisionally
    # assumed. Both constants are asserted against literals rather than
    # against each other, because deriving one from the other in the test is
    # how the original 180 -> 179 error survived its own test suite.
    assert MIDDLE_OFFICE_6M_YIELD_CHANGE_COUNT == 180
    assert MIDDLE_OFFICE_6M_OBSERVATION_COUNT == 181

    values = [4.0 + (index % 7) * 0.01 for index in range(MIDDLE_OFFICE_6M_OBSERVATION_COUNT)]
    result = calculate_historical_yield_volatility(_history(values))

    assert result.observation_count == 181
    assert result.requested_observation_count == 181
    assert result.yield_change_count == 180
    assert result.window_status is HistoricalYieldVolStatus.FULL_WINDOW
    assert result.blockers == ()
    assert result.warnings == ()
    assert result.is_usable is True


def test_the_default_window_is_the_middle_office_horizon():
    # The default is the contract: a caller that names no window gets 181
    # observations and exactly 180 changes, without having to know either
    # number. This is the path the workbench route and the card both take.
    values = [4.0 + (index % 7) * 0.01 for index in range(400)]
    result = calculate_historical_yield_volatility(_history(values))

    assert result.requested_observation_count == 181
    assert result.observation_count == 181
    assert result.yield_change_count == 180


def test_the_whole_middle_office_chain_end_to_end_on_the_default_window():
    # The five steps of the parity-confirmed methodology in one place, each
    # checked against an independently computed expectation rather than
    # against the calculator's own intermediate: 181 PERCENT observations ->
    # 180 changes -> STDEV.S -> x sqrt(252) -> /100 into DECIMAL_ANNUAL.
    #
    # Every previous convention error in this module survived because the
    # pieces were pinned separately and nothing checked that the assembled
    # chain was the one Middle Office runs.
    values = [4.0 + math.sin(index) * 0.03 for index in range(181)]
    result = calculate_historical_yield_volatility(
        _history(values, field_unit="PERCENT")
    )

    # strict=False for the same reason the calculator uses it: pairing a
    # series with its own tail is the one place unequal lengths are the point.
    expected_changes = [
        current - previous for previous, current in zip(values, values[1:], strict=False)
    ]
    assert len(expected_changes) == 180

    expected_daily = statistics.stdev(expected_changes)
    assert result.daily_yield_vol == expected_daily
    assert result.annualized_yield_vol == expected_daily * math.sqrt(252)

    # The headline figures stay in the Yield field's own unit -- PERCENT --
    # which is the number a Middle Office parity run compares against.
    assert result.field_unit == "PERCENT"

    # Normalization to the published contract's unit happens only at
    # publication, and only by the explicit declared factor.
    published = historical_yield_vol_volatility_input(result)
    assert published.volatility == result.annualized_yield_vol * 0.01


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


@pytest.mark.parametrize("bad_row", [None, "2026-01-01", 42, object()])
def test_a_row_that_is_not_an_observation_fails_closed(bad_row):
    # The row is typed before anything is read off it. This guard exists for
    # producers that are not the #196 loader, and it used to dereference
    # `.observation_date` on whatever the sequence held -- so `(None,)` raised
    # AttributeError instead of this module's one error type, and the route
    # answered HTTP 500 under a docstring promising HTTP 400 (Codex review,
    # PR #200).
    history = _history([4.00, 4.10, 3.80, 4.30])
    broken = dataclasses.replace(history, observations=(bad_row,) + history.observations[1:])

    with pytest.raises(HistoricalYieldVolInputError) as excinfo:
        calculate_historical_yield_volatility(broken, requested_observation_count=4)

    assert "BondYieldObservation" in str(excinfo.value)


@pytest.mark.parametrize("bad_series", [None, "abc", 123, 4.5])
def test_a_series_that_is_not_a_sequence_fails_closed(bad_series):
    # The neighbouring case one level up: `observations=None` is not iterable
    # and raised TypeError, which is the same contract breach as a malformed
    # row. Codex reported the row; this is the container.
    broken = dataclasses.replace(_history([4.00, 4.10, 3.80]), observations=bad_series)

    with pytest.raises(HistoricalYieldVolInputError) as excinfo:
        calculate_historical_yield_volatility(broken, requested_observation_count=3)

    assert "must be a sequence" in str(excinfo.value)


def test_an_empty_series_is_still_a_legitimate_no_history():
    # The sequence guard must not swallow the honest empty answer: zero
    # observations is NO_HISTORY, not a malformed series.
    result = calculate_historical_yield_volatility(
        _history([]), requested_observation_count=181
    )

    assert result.window_status is HistoricalYieldVolStatus.NO_HISTORY
    assert result.blockers != ()


def test_an_integer_no_float_represents_exactly_fails_closed():
    """The one finding so far that was a wrong number, not a refusal.

    [2**53, 2**53+1, 2**53+2] has identical integer changes and an exact
    sigma of zero. float() succeeds on every one of them and quietly maps the
    middle value onto the first, so the changes become [0.0, 2.0] and the
    calculator answered sqrt(2) -- a number the data does not support, with no
    error anywhere (Codex review, PR #200).
    """

    with pytest.raises(HistoricalYieldVolInputError) as excinfo:
        calculate_historical_yield_volatility(
            _history([2**53, 2**53 + 1, 2**53 + 2]), requested_observation_count=3
        )

    assert "no float represents exactly" in str(excinfo.value)


def test_integers_a_float_does_represent_exactly_are_still_accepted():
    # The rule must not refuse ordinary integer observations, which are
    # exactly representable and which the previous round's tests rely on.
    result = calculate_historical_yield_volatility(
        _history([4, 5, 4, 5]), requested_observation_count=4
    )
    assert result.daily_yield_vol is not None

    exact = calculate_historical_yield_volatility(
        _history([2**53, 2**53, 2**53]), requested_observation_count=3
    )
    assert exact.daily_yield_vol == 0.0


def test_integer_changes_that_lose_precision_on_subtraction_fail_closed():
    """Exact per-value conversion is not enough to keep the statistic honest.

    Every value in [1, 2**100, 2**101] is exactly representable, so the
    per-observation guard passes all three. The exact changes are
    [2**100 - 1, 2**100] and their ddof=1 sigma is sqrt(1/2); float
    subtraction returns 2**100 for both and the calculator answered a flat
    zero volatility -- a wrong number with no overflow and no exception
    (Codex review, PR #200).
    """

    with pytest.raises(HistoricalYieldVolInputError) as excinfo:
        calculate_historical_yield_volatility(
            _history([1, 2**100, 2**101]), requested_observation_count=3
        )

    message = str(excinfo.value)
    assert "cannot be differenced" in message
    assert "1267650600228229401496703205375" in message


def test_large_integers_whose_changes_are_exact_still_calculate():
    # The rule is about losing the change, not about magnitude. These three
    # convert exactly and differ by exactly 2, which is representable, so the
    # honest answer is the flat zero the data really shows.
    result = calculate_historical_yield_volatility(
        _history([2**53, 2**53 + 2, 2**53 + 4]), requested_observation_count=3
    )

    assert result.daily_yield_vol == 0.0
    assert result.annualized_yield_vol == 0.0
    assert result.blockers == ()


def test_float_pairs_are_not_held_to_the_exact_difference_rule():
    """A float observation is its float value, so IEEE754 subtraction is the method.

    A negative-yield series crossing zero produces differences that are not
    the exact difference of the two floats on a small fraction of ordinary
    pairs. Refusing those would reject honest Bund/JGB history, so the rule
    deliberately stops at pairs with an integer endpoint.
    """

    values = [
        0.0008575465478184933,
        -0.002862526213472576,
        0.002428522257291637,
        0.006576038572850303,
    ]
    assert Fraction(values[1] - values[0]) != Fraction(values[1]) - Fraction(values[0])

    result = calculate_historical_yield_volatility(
        _history(values), requested_observation_count=4
    )

    assert result.daily_yield_vol is not None
    assert result.blockers == ()


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
    assert result.requested_observation_count == 181
    assert result.observation_count == 90
    assert result.yield_change_count == 89
    assert result.annualized_yield_vol is not None
    # A short window that still supports the convention is a WARNING, never a
    # blocker: a consumer that discarded it would have thrown away the only
    # honest answer available for this bond (Codex review, PR #200).
    assert result.blockers == ()
    assert result.is_usable is True
    assert any("INSUFFICIENT_HISTORY" in warning for warning in result.warnings)
    assert any("90 of the requested 181" in warning for warning in result.warnings)


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
    assert "90 of the requested 181" in published.override_or_fallback_audit


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
        ({"observation_count": 9, "yield_change_count": 8}, "yields exactly 4"),
        ({"series_observation_count": 1}, "yields exactly 1"),
        ({"requested_observation_count": 180}, "which is INSUFFICIENT_HISTORY"),
        ({"window_status": "FULL_WINDOW"}, "must be a HistoricalYieldVolStatus"),
        ({"annualized_yield_vol": None}, "exist together or not at all"),
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
        ({"standard_deviation_convention": "POPULATION_STDEV_P"}, "this calculator produces"),
        ({"annualization_trading_days": 365}, "this calculator uses sqrt(252)"),
        ({"annualization_factor": 19.1}, "this calculator uses sqrt(252)"),
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
        (None, "exist together or not at all"),
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
        # A real NO_HISTORY result still carries the window that was ASKED
        # for -- the calculator refuses any requested count below 3, so 0 here
        # was a third fixture describing a result this module cannot produce.
        requested_observation_count=MIDDLE_OFFICE_6M_OBSERVATION_COUNT,
        window_status=HistoricalYieldVolStatus.NO_HISTORY,
        # Kept internally consistent so this test isolates the condition it
        # names: leaving the four dates behind now trips the date/count check
        # first, which is a different (also correct) refusal.
        observation_dates=(),
        first_observation_date=None,
        last_observation_date=None,
    )

    with pytest.raises(HistoricalYieldVolUnavailableError) as excinfo:
        historical_yield_vol_volatility_input(result)

    assert "0 Yield Change(s)" in str(excinfo.value)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"observation_dates": ()}, "lists 0 dates"),
        ({"observation_dates": [_START]}, "must be a tuple"),
        (
            {"observation_dates": (_START, _START, _START + timedelta(days=2), _START)},
            "strictly ascending",
        ),
        ({"first_observation_date": _START + timedelta(days=1)}, "not the first date used"),
        ({"last_observation_date": _START}, "not the last date used"),
        (
            {
                "observation_dates": (
                    _START - timedelta(days=5),
                    _START,
                    _START + timedelta(days=1),
                    _START + timedelta(days=2),
                )
            },
            "outside the declared range",
        ),
    ],
)
def test_observation_date_provenance_is_checked_against_the_counts(overrides, expected):
    """The dates are what the route displays as "where this number came from".

    An empty, duplicated, out-of-range or simply different tuple published
    false calculation provenance under a live source (Codex review, PR #200).
    """

    base = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )

    assert expected in str(result_shape_problem(dataclasses.replace(base, **overrides)))


def test_the_selected_window_count_is_exactly_what_the_calculator_would_use():
    # series=200, requested=180, used=4 satisfied both inequalities I had
    # written while describing a window this module never produces: the tail
    # slice always takes exactly min(series, requested) (Codex review, #200).
    base = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )
    result = dataclasses.replace(
        base,
        series_observation_count=200,
        requested_observation_count=180,
        window_status=HistoricalYieldVolStatus.INSUFFICIENT_HISTORY,
    )

    assert "yields exactly 180" in str(result_shape_problem(result))
    with pytest.raises(HistoricalYieldVolUnavailableError):
        historical_yield_vol_volatility_input(result)


def test_the_calculator_always_uses_the_minimum_of_series_and_requested():
    """The invariant the rule above encodes, checked against the calculator."""

    for series_length, requested in ((90, 180), (200, 180), (4, 4), (0, 180)):
        result = calculate_historical_yield_volatility(
            _history([4.0 + (index % 5) * 0.02 for index in range(series_length)]),
            requested_observation_count=requested,
        )
        assert result.observation_count == min(series_length, requested)


@pytest.mark.parametrize(
    "overrides",
    [
        {"annualized_yield_vol": None},
        {"daily_yield_vol": None},
    ],
)
def test_a_half_populated_result_is_refused_before_serialization(overrides):
    # The route serializes EVERY result, so a half-populated one answered
    # HTTP 200 showing a daily risk figure with no fatal blocker beside it.
    base = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )

    problem = result_shape_problem(dataclasses.replace(base, **overrides))

    assert problem is not None
    assert "exist together or not at all" in problem


def test_the_fixture_builder_produces_series_the_196_loader_could_return():
    """Twice a new rule caught a fixture asserting an impossible series.

    Those fixtures are what every guard in this file was validated against, so
    the builder's own output is checked against #196's documented invariants:
    dates strictly ascending, no duplicates, every observation inside the
    declared range, values finite or explicitly absent. A fixture that could
    not come off the wire proves nothing about production (Codex review,
    PR #200).
    """

    for values in ([], [4.0], [4.0, 4.1], [4.0 + i * 0.01 for i in range(180)], [4.0, None, 4.2]):
        history = _history(values)

        assert history.requested_start_date <= history.requested_end_date
        previous = None
        for observation in history.observations:
            assert type(observation.observation_date) is date
            if previous is not None:
                assert observation.observation_date > previous, "dates must strictly ascend"
            assert history.requested_start_date <= observation.observation_date
            assert observation.observation_date <= history.requested_end_date
            if observation.yield_value is not None:
                assert math.isfinite(observation.yield_value)
            previous = observation.observation_date


# --- Are the guards stricter than the calculator? ---------------------------


def _real_results():
    """Results the calculator actually produces, across the shapes it supports."""

    for series_length in (0, 1, 2, 3, 4, 5, 90, 180, 200):
        for requested in (3, 4, 180):
            for pattern in ("varied", "flat-ish", "subnormal", "subnormal-flat", "large"):
                if pattern == "varied":
                    values = [4.0 + (index % 7) * 0.01 for index in range(series_length)]
                elif pattern == "flat-ish":
                    values = [4.0 + index * 0.5 for index in range(series_length)]
                elif pattern == "subnormal":
                    # Genuinely below sys.float_info.min (2.2e-308), which
                    # 1e-8 is not -- the sweep claimed subnormal coverage it
                    # did not have (Codex review, PR #200).
                    values = [1e-308 * (index % 3) for index in range(series_length)]
                elif pattern == "subnormal-flat":
                    values = [1e-310 * (1 + index) for index in range(series_length)]
                else:
                    values = [1e6 + (index % 5) * 13.0 for index in range(series_length)]
                nonzero = [abs(value) for value in values if value]
                if pattern.startswith("subnormal") and nonzero:
                    assert min(nonzero) < sys.float_info.min, (
                        "the subnormal patterns must actually be subnormal"
                    )
                try:
                    yield calculate_historical_yield_volatility(
                        _history(values), requested_observation_count=requested
                    )
                except HistoricalYieldVolInputError:
                    # A refusal is correct behaviour for some of these shapes
                    # (a sigma that underflows to zero, for one). The sweep is
                    # about what the guard does with results that DO come back.
                    continue


def test_every_result_the_calculator_produces_passes_the_shared_shape_check():
    """The guard must never refuse a result this module itself built.

    Every rule in `result_shape_problem` is my reading of what the calculator
    guarantees, and every other test in this file starts from tampered
    calculator output -- so an over-strict rule would refuse a legitimate
    result and none of them would notice (Codex review, PR #200: I encoded an
    equality as two inequalities and the gap went unseen in both directions).
    This drives the calculator across the shapes it supports and asserts the
    guard accepts all of them.
    """

    checked = 0
    for result in _real_results():
        problem = result_shape_problem(result)
        assert problem is None, f"the guard refused a real result: {problem}"
        checked += 1
    assert checked > 50
    # And the sweep must actually have produced subnormal-magnitude results,
    # or its claim to cover that boundary is the same kind of overstatement
    # this whole review has been about.
    assert any(
        r.daily_yield_vol is not None and 0 < r.daily_yield_vol < sys.float_info.min
        for r in _real_results()
    )


def test_publication_never_raises_anything_but_its_own_error_on_a_real_result():
    """Whatever the calculator produces, publication publishes or explains.

    The single-error-type promise, checked against real output rather than
    against the shapes I imagined -- it has been broken six times, every time
    by a value this module produced elsewhere and did not validate.
    """

    published = refused = 0
    for result in _real_results():
        try:
            historical_yield_vol_volatility_input(result)
            published += 1
        except HistoricalYieldVolUnavailableError:
            refused += 1
    # Both outcomes must actually occur, or this test is asserting nothing.
    assert published > 0
    assert refused > 0


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        # The calculator refuses any requested count below 3 before it
        # calculates anything, so provenance claiming one is impossible.
        (
            {
                "requested_observation_count": 2,
                "series_observation_count": 2,
                "observation_count": 2,
                "yield_change_count": 1,
            },
            "below the 3 this calculator accepts",
        ),
        # Figures follow the change count, not merely blocker-emptiness.
        ({"yield_change_count": 3, "observation_count": 4}, None),
        # Both figures non-None but unusable: the route serializes these for
        # every response, so "x"/"y" reached HTTP 200 as repr strings.
        (
            {"daily_yield_vol": "x", "annualized_yield_vol": "y"},
            "is not a finite number",
        ),
        # bool(None) is False, so blockers=None looked blocker-free and then
        # made the route raise TypeError at list(...).
        ({"blockers": None}, "blockers must be a tuple"),
        ({"warnings": None}, "warnings must be a tuple"),
        ({"blockers": ("",)}, "non-blank string"),
        ({"warnings": (7,)}, "non-blank string"),
        # Two finite figures that cannot both be true. Publication refused
        # this, the route caught that refusal and still answered HTTP 200
        # with both headline numbers (Codex review, PR #200).
        ({"daily_yield_vol": 99.0}, "which is not that daily figure times"),
        (
            {"daily_yield_vol": -0.01, "annualized_yield_vol": -0.01 * ANNUALIZATION_FACTOR},
            "never negative",
        ),
        # A methodology the canonical calculator cannot produce, serialized
        # under the hard-coded canonical label.
        ({"standard_deviation_convention": "POPULATION_STDEV_P"}, "this calculator produces"),
        ({"annualization_trading_days": 365}, "annualization by sqrt"),
        # A full window does not carry the short-window qualification.
        ({"warnings": ("Applied VCUB substitute",)}, "that status carries exactly"),
        # _write_json calls json.dumps outside the handler's exception
        # boundary, so an unserializable provenance value terminated the
        # response instead of returning the promised HTTP 400.
        ({"calculated_at": object()}, "calculated_at must be a non-blank string"),
        ({"acquired_at": ""}, "acquired_at must be a non-blank string"),
        ({"field_unit": 7}, "field_unit must be a non-blank string or None"),
    ],
)
def test_the_shared_guard_refuses_what_the_calculator_could_not_produce(overrides, expected):
    base = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )

    problem = result_shape_problem(dataclasses.replace(base, **overrides))

    if expected is None:
        assert problem is None
    else:
        assert problem is not None and expected in problem


def test_figures_are_required_exactly_when_the_change_count_supports_them():
    # Two observations make one change, which has no ddof=1 standard
    # deviation -- a figure there is one the calculator could not produce.
    base = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )
    short = dataclasses.replace(
        base,
        series_observation_count=2,
        observation_count=2,
        yield_change_count=1,
        window_status=HistoricalYieldVolStatus.INSUFFICIENT_HISTORY,
        observation_dates=base.observation_dates[:2],
        last_observation_date=base.observation_dates[1],
    )

    problem = result_shape_problem(short)

    assert problem is not None
    assert "1 Yield Change(s)" in problem


def test_an_insufficient_history_result_must_keep_its_warning():
    # The other direction of the same rule: stripping the warning turns a
    # short window into something a consumer reads as an ordinary result,
    # and the route displays it as calculation output (Codex review, #200).
    short = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=180
    )
    assert short.window_status is HistoricalYieldVolStatus.INSUFFICIENT_HISTORY
    assert result_shape_problem(short) is None

    problem = result_shape_problem(dataclasses.replace(short, warnings=()))

    assert problem is not None
    assert "that status carries exactly" in problem

    # And it must be that warning, not merely one: an arbitrary string here
    # published beside an audit line contradicting it (Codex review, #200).
    contradictory = result_shape_problem(
        dataclasses.replace(short, warnings=("Applied VCUB substitute",))
    )
    assert contradictory is not None
    assert "Applied VCUB substitute" in contradictory

    # Two copies of the real warning is not "a warning" either.
    doubled = result_shape_problem(
        dataclasses.replace(short, warnings=short.warnings + short.warnings)
    )
    assert doubled is not None


def test_a_fatal_blocker_forces_is_usable_false():
    """The property is public and is read without the publication guard.

    The calculator never emits a blocker beside a figure, but `is_usable` is
    an accessor on a plain dataclass and its docstring calls itself equivalent
    to `not self.blockers`. A directly constructed result carrying both
    answered True — the opposite of what the blocker says (Codex review, #200).
    """

    base = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )
    assert base.is_usable is True

    blocked = dataclasses.replace(base, blockers=("this result must not be used",))

    assert blocked.is_usable is False
    assert blocked.is_usable == (not blocked.blockers)


def test_a_blocked_window_must_keep_its_own_blocker():
    """The fatal half of the same rule, which was only checked for non-blankness.

    A blocker is the only text the route shows when there is no number -- it
    is both the calculation blocker and the publication-refusal reason -- so
    an invented one was rendered as this calculator's own explanation of why
    there is no Historical Yield Vol, against the substitution #197 most
    explicitly forbids (Codex review, #200).
    """

    empty = calculate_historical_yield_volatility(
        _history([]), requested_observation_count=180
    )
    assert empty.window_status is HistoricalYieldVolStatus.NO_HISTORY
    assert result_shape_problem(empty) is None

    substituted = result_shape_problem(
        dataclasses.replace(empty, blockers=("Use a VCUB substitute instead",))
    )
    assert substituted is not None
    assert "Use a VCUB substitute instead" in substituted

    # Stripping it is the other direction: no number, and nothing saying why.
    assert result_shape_problem(dataclasses.replace(empty, blockers=())) is not None
    # Two copies of the real blocker is not "the blocker" either.
    doubled = dataclasses.replace(empty, blockers=empty.blockers + empty.blockers)
    assert result_shape_problem(doubled) is not None


def test_a_window_too_short_for_the_convention_must_keep_its_own_blocker():
    short = calculate_historical_yield_volatility(
        _history([4.00, 4.10]), requested_observation_count=180
    )
    assert short.yield_change_count == 1
    assert result_shape_problem(short) is None

    invented = result_shape_problem(
        dataclasses.replace(
            short, blockers=("Extend the window with the benchmark instead",)
        )
    )
    assert invented is not None
    assert "Extend the window with the benchmark instead" in invented


def test_a_full_window_carries_no_blocker_at_all():
    # The must-still-pass twin, and the third direction: a result that really
    # is complete may not carry one either.
    full = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )
    assert full.window_status is HistoricalYieldVolStatus.FULL_WINDOW
    assert full.blockers == ()
    assert result_shape_problem(full) is None


@pytest.mark.parametrize("missing", ["daily_yield_vol", "annualized_yield_vol"])
def test_half_a_result_is_not_usable(missing):
    """The two figures exist together or not at all -- including here.

    `result_shape_problem` rejects the half-populated shape, but a caller
    reading `is_usable` is not going through it, which is the whole reason
    the property exists. A result whose daily sigma had gone missing under a
    populated annualized figure still answered True (Codex review, #200).
    """

    base = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )
    assert base.is_usable is True

    half = dataclasses.replace(base, **{missing: None})

    assert half.is_usable is False
    # And the guard says the same thing, so the two never disagree.
    assert result_shape_problem(half) is not None


@pytest.mark.parametrize("label", ["REUTERS", "BLOOMBERG_VCUB", "bloomberg_dapi"])
def test_history_from_another_source_system_is_refused(label):
    """The one acquisition path this statistic is built on.

    A hand-built or future history carrying `source_system="REUTERS"` was
    copied straight through, passed the guard, and published as an ACTIVE
    source while the card displayed it as Bloomberg provenance (Codex
    review, PR #200).

    `bloomberg_dapi` is here on purpose: unlike the Yield unit, this label
    never passes through a trader's typing, so there is nothing to tidy and
    loosening the match would only widen what the card presents as audited.
    """

    result = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30], source_system=label),
        requested_observation_count=4,
    )

    problem = result_shape_problem(result)

    assert problem is not None
    assert "is only taken over BLOOMBERG_DAPI history" in problem


@pytest.mark.parametrize(
    "stamp",
    [
        "not-a-time",
        "",
        # No offset: a local reading nobody can place.
        "2026-09-01T14:05:00",
        "2026-09-01",
    ],
)
@pytest.mark.parametrize("field", ["acquired_at", "calculated_at"])
def test_a_timestamp_that_records_no_placeable_moment_is_refused(stamp, field):
    # Both are evidence of *when* -- when Bloomberg was read, and when this
    # number was calculated -- and "not-a-time" was displayed as exactly
    # that. The loader and this module both stamp an offset-aware ISO-8601
    # string (Codex review, PR #200).
    base = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )

    problem = result_shape_problem(dataclasses.replace(base, **{field: stamp}))

    assert problem is not None
    assert field in problem


def test_the_loaders_own_timestamp_shape_is_accepted():
    # The rule must not refuse what the #196 loader actually stamps:
    # `datetime.now().astimezone().isoformat(timespec="seconds")`.
    stamped = datetime.now().astimezone().isoformat(timespec="seconds")
    base = calculate_historical_yield_volatility(
        _history([4.00, 4.10, 3.80, 4.30]), requested_observation_count=4
    )

    assert result_shape_problem(dataclasses.replace(base, acquired_at=stamped)) is None


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
