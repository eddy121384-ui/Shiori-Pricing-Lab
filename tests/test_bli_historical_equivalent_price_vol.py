"""Deterministic pins for Issue #211's Historical Yield Vol -> Equivalent
Price Vol conversion.

The approved bridge is a single multiplication, ``sigma_P = |D_B| x
sigma_hist_abs`` (Annex A v1.4 §A.8.6). What these tests mostly pin is what
must *not* happen on the way: no forward yield, no relative-vol round trip,
no convexity, no DCF adjustment, no second unit conversion, and no path by
which a raw ``YIELD_VOL`` becomes priceable.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest

from shiori_pricing_lab.data.bli_snapshot import BLIMarketDataStatus, BLIVolatilityBasis
from shiori_pricing_lab.data.bloomberg_bond_yield_history import (
    BloombergBondYieldHistory,
    BondYieldObservation,
)
from shiori_pricing_lab.data.historical_yield_volatility import (
    ANNUALIZATION_FACTOR,
    HISTORICAL_YIELD_VOL_MO_SOURCE,
    PUBLISHED_VOLATILITY_UNIT,
    calculate_historical_yield_volatility,
    historical_yield_vol_volatility_input,
)
from shiori_pricing_lab.pricing.bli_bond_modified_duration import (
    calculate_bond_modified_duration,
)
from shiori_pricing_lab.pricing.bli_bond_option_price_basis import BondOptionPriceBasis
from shiori_pricing_lab.pricing.bli_historical_equivalent_price_vol import (
    EQUIVALENT_PRICE_VOL_METHODOLOGY_VERSION,
    VOLATILITY_KIND,
    BLIHistoricalEquivalentPriceVolError,
    historical_equivalent_price_vol,
    historical_equivalent_price_vol_volatility_input,
)

_SECURITY = "/isin/US0000000000"
_START = date(2026, 1, 1)


def _history(values, *, field_unit="PERCENT", security=_SECURITY):
    dates = [_START + timedelta(days=index) for index in range(len(values))]
    return BloombergBondYieldHistory(
        requested_identifier=security,
        security=security,
        yield_field="YLD_YTM_MID",
        field_meaning="Yield to Maturity (Mid)",
        field_unit=field_unit,
        requested_start_date=dates[0],
        requested_end_date=dates[-1],
        observations=tuple(
            BondYieldObservation(
                observation_date=day,
                yield_value=value,
                raw_value=None if value is None else repr(value),
            )
            for day, value in zip(dates, values, strict=True)
        ),
        source_system="BLOOMBERG_DAPI",
        acquired_at="2026-09-11T09:00:00+00:00",
    )


def _vol_result(values=None, *, field_unit="PERCENT", security=_SECURITY):
    if values is None:
        values = [4.00, 4.10, 3.80, 4.30]
    return calculate_historical_yield_volatility(
        _history(values, field_unit=field_unit, security=security),
        requested_observation_count=len(values),
    )


def _result_with_vol(annualized, *, field_unit="DECIMAL", security=_SECURITY):
    """A #197 result carrying an exact annualized figure, kept self-consistent.

    ``_require_publishable_shape`` cross-checks that the annualized vol really
    is the daily one times sqrt(252), so the daily figure has to move with it.
    Replacing only the annualized value would be refused by #197 -- correctly,
    since such a result describes no calculation that ever happened.
    """

    return dataclasses.replace(
        _vol_result(field_unit=field_unit, security=security),
        daily_yield_vol=annualized / ANNUALIZATION_FACTOR,
        annualized_yield_vol=annualized,
    )


def _duration(*, security=_SECURITY, price_basis=BondOptionPriceBasis.DIRTY,
              settlement_date=date(2027, 2, 14)):
    return calculate_bond_modified_duration(
        security=security,
        convention_profile="UST",
        price_basis=price_basis,
        clean_price_per_100=101.067593,
        settlement_date=settlement_date,
        maturity_date=date(2035, 8, 15),
        coupon_percent=4.25,
        pricing_timestamp="2027-02-12T16:00:00+00:00",
        calculated_at="2027-02-12T16:00:05+00:00",
    )


def _duration_with(absolute, *, security=_SECURITY):
    """A real duration object with its ``D_B`` replaced by an exact figure.

    ``dataclasses.replace`` on a genuine result rather than a hand-built
    stub: every other provenance field stays real, so a test that needs one
    exact number does not quietly also test a fictional bond.
    """

    base = _duration(security=security)
    return dataclasses.replace(
        base, modified_duration=absolute, absolute_modified_duration=abs(absolute)
    )


# --- The canonical vector the issue specifies --------------------------------


def test_the_canonical_conversion_vector():
    # sigma_hist_abs = 0.0073070244 (the #197 Middle Office parity figure)
    # D_B            = 6.5
    # -> sigma_P     = 0.0474956586
    #
    # DECIMAL source unit so the normalization factor is exactly 1.0 and this
    # test pins the *conversion* rather than the unit arithmetic; the PERCENT
    # path is pinned separately below.
    result = _result_with_vol(0.0073070244)

    converted = historical_equivalent_price_vol(result, _duration_with(6.5))

    assert converted.historical_yield_vol_decimal_annual == 0.0073070244
    assert converted.duration.absolute_modified_duration == 6.5
    assert converted.equivalent_price_vol == 0.0474956586


def test_the_conversion_is_exactly_the_product_and_nothing_else():
    # No forward yield, no convexity, no DCF factor, no fitted multiplier:
    # whatever the inputs, the output is the bare product.
    result = _vol_result()
    duration = _duration()

    converted = historical_equivalent_price_vol(result, duration)
    expected = (
        duration.absolute_modified_duration
        * historical_yield_vol_volatility_input(result).volatility
    )
    assert converted.equivalent_price_vol == expected


# --- Sign, units, labelling --------------------------------------------------


def test_a_negative_duration_cannot_make_the_volatility_negative():
    # abs(D_B) is in the contract precisely so a sign convention on the
    # duration can never hand Black-76 a negative sigma.
    result = _result_with_vol(0.0073070244)

    negative = historical_equivalent_price_vol(result, _duration_with(-6.5))
    positive = historical_equivalent_price_vol(result, _duration_with(6.5))

    assert negative.duration.modified_duration == -6.5
    assert negative.equivalent_price_vol > 0
    assert negative.equivalent_price_vol == positive.equivalent_price_vol == 0.0474956586


def test_the_unit_conversion_happens_exactly_once():
    # A PERCENT Yield field normalizes by 1e-2 on its way into
    # DECIMAL_ANNUAL, in #197's own helper, and never again here. Applying it
    # twice (or not at all) is a 100x error in sigma_P in either direction,
    # so all three candidates are checked apart.
    result = _vol_result(field_unit="PERCENT")
    duration = _duration_with(6.5)
    raw = result.annualized_yield_vol

    converted = historical_equivalent_price_vol(result, duration)

    once = 6.5 * (raw * 1e-2)
    never = 6.5 * raw
    twice = 6.5 * (raw * 1e-2 * 1e-2)

    assert converted.equivalent_price_vol == once
    assert converted.equivalent_price_vol != never
    assert converted.equivalent_price_vol != twice
    assert converted.historical_yield_vol_normalization_factor == 1e-2
    assert converted.historical_yield_vol_field_unit == "PERCENT"
    assert converted.historical_yield_vol_in_field_unit == raw


@pytest.mark.parametrize(
    ("unit", "factor"), [("DECIMAL", 1.0), ("PERCENT", 1e-2), ("BASIS_POINTS", 1e-4)]
)
def test_each_supported_source_unit_normalizes_by_its_own_declared_factor(unit, factor):
    result = _vol_result(field_unit=unit)
    converted = historical_equivalent_price_vol(result, _duration_with(6.5))

    assert converted.historical_yield_vol_normalization_factor == factor
    assert converted.equivalent_price_vol == 6.5 * (result.annualized_yield_vol * factor)


def test_the_result_is_labelled_equivalent_price_vol_and_never_price_or_yield_vol():
    converted = historical_equivalent_price_vol(_vol_result(), _duration())

    assert converted.volatility_basis is BLIVolatilityBasis.EQUIVALENT_PRICE_VOL
    assert converted.volatility_basis is not BLIVolatilityBasis.PRICE_VOL
    assert converted.volatility_basis is not BLIVolatilityBasis.YIELD_VOL
    assert converted.source_system == HISTORICAL_YIELD_VOL_MO_SOURCE
    assert converted.unit == PUBLISHED_VOLATILITY_UNIT == "DECIMAL_ANNUAL"


def test_the_source_is_never_presented_as_implied_or_as_vcub():
    converted = historical_equivalent_price_vol(_vol_result(), _duration())

    assert converted.volatility_kind == VOLATILITY_KIND == "HISTORICAL_REALIZED"
    assert converted.bond_vol_source_mode == HISTORICAL_YIELD_VOL_MO_SOURCE
    assert "VCUB" not in converted.bond_vol_source_mode

    published = historical_equivalent_price_vol_volatility_input(converted)
    assert "not Bloomberg implied vol" in published.override_or_fallback_audit
    assert "no VCUB DCF adjustment" in published.override_or_fallback_audit


# --- Price basis is inherited, never chosen ----------------------------------


@pytest.mark.parametrize("basis", list(BondOptionPriceBasis))
def test_the_equivalent_price_vol_follows_the_durations_basis(basis):
    result = _vol_result()
    duration = _duration(price_basis=basis)
    converted = historical_equivalent_price_vol(result, duration)

    sigma_hist = historical_yield_vol_volatility_input(result).volatility
    assert converted.price_basis is basis
    assert converted.equivalent_price_vol == (
        duration.absolute_modified_duration * sigma_hist
    )


def test_the_two_bases_give_different_volatilities_when_accrued_is_non_zero():
    # Both are ordinary-looking numbers a few percent apart; only the carried
    # basis distinguishes them, which is why it must never be droppable.
    result = _vol_result()
    clean = historical_equivalent_price_vol(
        result, _duration(price_basis=BondOptionPriceBasis.CLEAN)
    )
    dirty = historical_equivalent_price_vol(
        result, _duration(price_basis=BondOptionPriceBasis.DIRTY)
    )

    assert clean.duration.accrued_interest_per_100 > 0
    assert clean.equivalent_price_vol != dirty.equivalent_price_vol
    assert clean.equivalent_price_vol > dirty.equivalent_price_vol
    assert clean.price_basis is BondOptionPriceBasis.CLEAN
    assert dirty.price_basis is BondOptionPriceBasis.DIRTY


def test_the_two_bases_agree_when_there_is_no_accrued_interest():
    on_coupon = date(2027, 2, 15)
    result = _vol_result()
    clean = historical_equivalent_price_vol(
        result, _duration(price_basis=BondOptionPriceBasis.CLEAN, settlement_date=on_coupon)
    )
    dirty = historical_equivalent_price_vol(
        result, _duration(price_basis=BondOptionPriceBasis.DIRTY, settlement_date=on_coupon)
    )

    assert clean.duration.accrued_interest_per_100 == 0.0
    assert clean.equivalent_price_vol == dirty.equivalent_price_vol


@pytest.mark.parametrize("basis", list(BondOptionPriceBasis))
def test_the_basis_survives_into_the_published_input_audit(basis):
    converted = historical_equivalent_price_vol(_vol_result(), _duration(price_basis=basis))
    published = historical_equivalent_price_vol_volatility_input(converted)

    audit = published.override_or_fallback_audit
    assert f"{basis.value} price basis" in audit
    assert f"only be composed with {basis.value} forward/strike" in audit


def test_a_duration_whose_basis_and_label_disagree_is_refused():
    # The last point at which a corrupted basis is detectable at all: after
    # the multiplication the two are indistinguishable by inspection.
    duration = dataclasses.replace(
        _duration(price_basis=BondOptionPriceBasis.DIRTY),
        price_basis=BondOptionPriceBasis.CLEAN,
    )
    with pytest.raises(BLIHistoricalEquivalentPriceVolError) as excinfo:
        historical_equivalent_price_vol(_vol_result(), duration)
    assert "disagree" in str(excinfo.value)


def test_a_duration_that_divided_by_the_wrong_price_state_is_refused():
    duration = dataclasses.replace(
        _duration(price_basis=BondOptionPriceBasis.DIRTY), basis_price_per_100=101.067593
    )
    with pytest.raises(BLIHistoricalEquivalentPriceVolError) as excinfo:
        historical_equivalent_price_vol(_vol_result(), duration)
    assert "inconsistent" in str(excinfo.value)


@pytest.mark.parametrize("bad", [None, "", "GROSS", 1])
def test_a_duration_carrying_no_usable_basis_is_refused(bad):
    duration = dataclasses.replace(_duration(), price_basis=bad)
    with pytest.raises(BLIHistoricalEquivalentPriceVolError) as excinfo:
        historical_equivalent_price_vol(_vol_result(), duration)
    assert "price basis" in str(excinfo.value)


# --- The pricing guard is not weakened ---------------------------------------


def test_the_raw_yield_vol_basis_is_still_refused_by_the_pricing_input_guard():
    # The boundary this issue must not cross. The guard's supported set is
    # unchanged; this slice converts rather than making it permissive.
    from shiori_pricing_lab.pricing.bli_mvp_required_input_guard import (
        _SUPPORTED_VOLATILITY_BASES,
    )

    assert BLIVolatilityBasis.YIELD_VOL not in _SUPPORTED_VOLATILITY_BASES
    assert _SUPPORTED_VOLATILITY_BASES == frozenset(
        {BLIVolatilityBasis.PRICE_VOL, BLIVolatilityBasis.EQUIVALENT_PRICE_VOL}
    )


def test_the_converted_input_is_on_a_basis_the_guard_already_accepts():
    from shiori_pricing_lab.pricing.bli_mvp_required_input_guard import (
        _SUPPORTED_VOLATILITY_BASES,
    )

    converted = historical_equivalent_price_vol(_vol_result(), _duration())
    published = historical_equivalent_price_vol_volatility_input(converted)

    assert published.volatility_basis in _SUPPORTED_VOLATILITY_BASES
    # ...while #197's own raw publication is still on the refused basis.
    raw = historical_yield_vol_volatility_input(_vol_result())
    assert raw.volatility_basis not in _SUPPORTED_VOLATILITY_BASES


# --- Both parents, and only the same bond ------------------------------------


def test_a_duration_for_another_bond_is_refused():
    # Arithmetically fine, financially meaningless, and nothing downstream
    # could detect it.
    with pytest.raises(BLIHistoricalEquivalentPriceVolError) as excinfo:
        historical_equivalent_price_vol(
            _vol_result(security="/isin/US0000000000"),
            _duration(security="/isin/DE0000000000"),
        )
    message = str(excinfo.value)
    assert "US0000000000" in message
    assert "DE0000000000" in message


def test_both_lineages_survive_the_conversion():
    result = _vol_result()
    duration = _duration()
    converted = historical_equivalent_price_vol(result, duration)

    # #197 lineage
    assert converted.historical_yield_vol_observation_count == result.observation_count
    assert converted.historical_yield_vol_change_count == result.yield_change_count
    assert (
        converted.historical_yield_vol_convention == result.standard_deviation_convention
    )
    assert (
        converted.historical_yield_vol_annualization_trading_days
        == result.annualization_trading_days
    )
    assert converted.historical_yield_vol_window_status == result.window_status.value
    assert converted.historical_yield_vol_calculated_at == result.calculated_at

    # Duration lineage, whole -- not a copied number.
    assert converted.duration is duration
    assert converted.duration.price_basis is BondOptionPriceBasis.DIRTY
    assert converted.price_basis is converted.duration.price_basis
    assert converted.duration.basis_price_per_100 == converted.duration.dirty_price_per_100
    assert converted.duration.dirty_price_per_100 == (
        duration.clean_price_per_100 + duration.accrued_interest_per_100
    )
    assert converted.methodology_version == EQUIVALENT_PRICE_VOL_METHODOLOGY_VERSION


def test_the_published_audit_carries_both_parents_and_the_conversion():
    converted = historical_equivalent_price_vol(_vol_result(), _duration())
    published = historical_equivalent_price_vol_volatility_input(converted)

    audit = published.override_or_fallback_audit
    assert published.volatility == converted.equivalent_price_vol
    assert published.status is BLIMarketDataStatus.ACTIVE
    assert published.source_system == HISTORICAL_YIELD_VOL_MO_SOURCE
    for expected in (
        "sigma_P = |D_B| x sigma_hist_abs",
        "MODIFIED_DURATION_DIRTY_PRICE",
        "DIRTY",
        "SAMPLE_STDEV_S_DDOF_1",
        "no fitted factor",
        converted.duration.settlement_date.isoformat(),
        converted.duration.pricing_timestamp,
    ):
        assert expected in audit


def test_no_module_in_the_pricing_package_reads_the_system_clock():
    # Same repository invariant as the duration module: every timestamp is an
    # argument, so a result never silently depends on when it ran.
    from pathlib import Path

    import shiori_pricing_lab.pricing.bli_historical_equivalent_price_vol as converter

    text = Path(converter.__file__).read_text(encoding="utf-8")
    assert "datetime.now(" not in text
    assert "date.today(" not in text


def test_the_calculated_at_defaults_to_the_durations_own_timestamp():
    duration = _duration()
    converted = historical_equivalent_price_vol(_vol_result(), duration)
    assert converted.calculated_at == duration.calculated_at

    supplied = historical_equivalent_price_vol(
        _vol_result(), duration, calculated_at="2027-03-01T09:00:00+00:00"
    )
    assert supplied.calculated_at == "2027-03-01T09:00:00+00:00"


@pytest.mark.parametrize("bad", ["", "   ", 5])
def test_a_blank_supplied_calculated_at_is_refused(bad):
    with pytest.raises(BLIHistoricalEquivalentPriceVolError):
        historical_equivalent_price_vol(_vol_result(), _duration(), calculated_at=bad)


def test_the_published_value_crosses_the_boundary_unrescaled():
    converted = historical_equivalent_price_vol(_vol_result(), _duration())
    published = historical_equivalent_price_vol_volatility_input(converted)

    assert published.volatility == converted.equivalent_price_vol


# --- Fail-closed -------------------------------------------------------------


def test_a_window_with_no_history_never_reaches_a_price_vol():
    result = _vol_result()
    empty = dataclasses.replace(
        result, annualized_yield_vol=None, observation_count=0, yield_change_count=0
    )
    with pytest.raises(BLIHistoricalEquivalentPriceVolError):
        historical_equivalent_price_vol(empty, _duration())


def test_an_undeclared_source_unit_refuses_conversion():
    # #197's own fail-closed rule, inherited rather than re-implemented.
    result = _vol_result()
    with pytest.raises(BLIHistoricalEquivalentPriceVolError):
        historical_equivalent_price_vol(
            dataclasses.replace(result, field_unit=None), _duration()
        )


def test_an_unsupported_source_unit_refuses_conversion():
    result = _vol_result()
    with pytest.raises(BLIHistoricalEquivalentPriceVolError):
        historical_equivalent_price_vol(
            dataclasses.replace(result, field_unit="PERCENTAGE POINTS"), _duration()
        )


@pytest.mark.parametrize("bad", [0.0, -6.5])
def test_a_non_positive_duration_refuses_conversion(bad):
    result = _vol_result()
    duration = dataclasses.replace(
        _duration(), modified_duration=bad, absolute_modified_duration=bad
    )
    with pytest.raises(BLIHistoricalEquivalentPriceVolError) as excinfo:
        historical_equivalent_price_vol(result, duration)
    assert "not positive" in str(excinfo.value)


def test_a_non_finite_duration_refuses_conversion():
    result = _vol_result()
    duration = dataclasses.replace(
        _duration(),
        modified_duration=float("inf"),
        absolute_modified_duration=float("inf"),
    )
    with pytest.raises(BLIHistoricalEquivalentPriceVolError):
        historical_equivalent_price_vol(result, duration)


def test_a_product_that_underflows_its_own_conversion_is_refused_not_published():
    result = _result_with_vol(5e-324 * ANNUALIZATION_FACTOR)
    duration = dataclasses.replace(
        _duration(), modified_duration=5e-324, absolute_modified_duration=5e-324
    )
    with pytest.raises(BLIHistoricalEquivalentPriceVolError) as excinfo:
        historical_equivalent_price_vol(result, duration)
    assert "underflow" in str(excinfo.value)


@pytest.mark.parametrize("bad", [None, "vol", 0.01])
def test_a_non_result_input_is_refused(bad):
    with pytest.raises(BLIHistoricalEquivalentPriceVolError):
        historical_equivalent_price_vol(bad, _duration())
    with pytest.raises(BLIHistoricalEquivalentPriceVolError):
        historical_equivalent_price_vol(_vol_result(), bad)


def test_publishing_a_non_converted_object_is_refused():
    with pytest.raises(BLIHistoricalEquivalentPriceVolError):
        historical_equivalent_price_vol_volatility_input("not a conversion")
