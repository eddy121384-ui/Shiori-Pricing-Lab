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
    PUBLISHABLE_PRICE_BASES,
    VOLATILITY_KIND,
    BLIHistoricalEquivalentPriceVolError,
    equivalent_price_vol_from,
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


def _retargeted_duration(target, *, security=_SECURITY,
                         price_basis=BondOptionPriceBasis.DIRTY):
    """A real duration record tampered to report ``target``, self-consistently.

    Numerator and both magnitudes move together, so the record satisfies
    every *internal* check -- basis, type label, denominator, and the
    ``-(dP/dY) / P_basis`` quotient. It is nonetheless a lie: re-running the
    duration producer over the record's own declared inputs does not return
    these figures, because the recorded bumped prices never supported them.

    That is exactly the case the conversion's reproducibility gate exists to
    catch, so every use of this helper below expects a refusal. There is
    deliberately no helper that fabricates an *accepted* duration magnitude:
    since the gate re-runs the producer, the only durations the conversion
    accepts are ones a real bond actually produces.
    """

    base = _duration(security=security, price_basis=price_basis)
    return dataclasses.replace(
        base,
        price_derivative_per_unit_yield=-target * base.basis_price_per_100,
        modified_duration=target,
        absolute_modified_duration=abs(target),
    )


# --- The canonical vector the issue specifies --------------------------------


def test_the_canonical_conversion_vector():
    # The approved fixture, pinned as a generic formula test:
    #   sigma_hist_abs = 0.0073070244 (the #197 Middle Office parity figure)
    #   |D_B|          = 6.5
    #   -> sigma_P     = 0.0474956586
    #
    # Against the formula rather than the producer, because the producer now
    # requires a *reproducible* duration -- and no real bond's duration lands
    # on exactly 6.5. The producer's use of this same formula is pinned by
    # test_the_conversion_is_exactly_the_product_and_nothing_else below.
    assert equivalent_price_vol_from(6.5, 0.0073070244) == 0.0474956586


def test_the_formula_is_the_same_on_either_basis():
    # There is one conversion, selected twice -- not two conversions.
    assert equivalent_price_vol_from(7.086987080284323, 0.0073070244) == (
        0.05178478751812231
    )
    assert equivalent_price_vol_from(6.9418247524496595, 0.0073070244) == (
        0.05072408284667362
    )


@pytest.mark.parametrize("signed", [6.5, -6.5])
def test_the_formula_cannot_return_a_negative_volatility(signed):
    # abs() is in the contract so a sign convention on D_B can never reach
    # Black-76 as a negative sigma, whichever figure a caller passes.
    assert equivalent_price_vol_from(signed, 0.0073070244) == 0.0474956586


def test_the_conversion_is_exactly_the_product_and_nothing_else():
    # No forward yield, no convexity, no DCF factor, no fitted multiplier:
    # whatever the inputs, the output is the bare product -- and it is the
    # same formula the canonical fixture above pins.
    result = _vol_result()
    duration = _duration()

    converted = historical_equivalent_price_vol(result, duration)
    sigma_hist = historical_yield_vol_volatility_input(result).volatility
    assert converted.equivalent_price_vol == (
        duration.absolute_modified_duration * sigma_hist
    )
    assert converted.equivalent_price_vol == equivalent_price_vol_from(
        duration.absolute_modified_duration, sigma_hist
    )


# --- Sign, units, labelling --------------------------------------------------


@pytest.mark.parametrize("basis", list(BondOptionPriceBasis))
def test_the_published_volatility_is_positive_on_a_real_record(basis):
    # The producer side of the abs() contract: a genuine duration carries
    # absolute == abs(signed), so the product cannot come out negative.
    duration = _duration(price_basis=basis)
    converted = historical_equivalent_price_vol(_vol_result(), duration)

    assert duration.absolute_modified_duration == abs(duration.modified_duration)
    assert converted.equivalent_price_vol > 0


def test_the_unit_conversion_happens_exactly_once():
    # A PERCENT Yield field normalizes by 1e-2 on its way into
    # DECIMAL_ANNUAL, in #197's own helper, and never again here. Applying it
    # twice (or not at all) is a 100x error in sigma_P in either direction,
    # so all three candidates are checked apart.
    result = _vol_result(field_unit="PERCENT")
    duration = _duration()
    d = duration.absolute_modified_duration
    raw = result.annualized_yield_vol

    converted = historical_equivalent_price_vol(result, duration)

    once = d * (raw * 1e-2)
    never = d * raw
    twice = d * (raw * 1e-2 * 1e-2)

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
    duration = _duration()
    converted = historical_equivalent_price_vol(result, duration)

    assert converted.historical_yield_vol_normalization_factor == factor
    assert converted.equivalent_price_vol == (
        duration.absolute_modified_duration * (result.annualized_yield_vol * factor)
    )


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


def test_the_basis_survives_into_the_published_input_audit():
    basis = BondOptionPriceBasis.DIRTY
    converted = historical_equivalent_price_vol(_vol_result(), _duration(price_basis=basis))
    published = historical_equivalent_price_vol_volatility_input(converted)

    audit = published.override_or_fallback_audit
    assert f"{basis.value} price basis" in audit
    assert f"only be composed with {basis.value} forward/strike" in audit


# --- CLEAN publication safety (Codex review, PR #212) ------------------------


def test_dirty_publication_into_the_pricing_contract_still_succeeds():
    converted = historical_equivalent_price_vol(
        _vol_result(), _duration(price_basis=BondOptionPriceBasis.DIRTY)
    )
    published = historical_equivalent_price_vol_volatility_input(converted)

    assert published.volatility == converted.equivalent_price_vol
    assert published.volatility_basis is BLIVolatilityBasis.EQUIVALENT_PRICE_VOL
    assert PUBLISHABLE_PRICE_BASES == frozenset({BondOptionPriceBasis.DIRTY})


def test_clean_publication_into_the_pricing_contract_is_explicitly_refused():
    # BLIVolatilityInput carries no price basis, and the standalone engine
    # applies whatever volatility it receives to dirty F/K. Publishing a
    # CLEAN vol there would silently build the forbidden mixed state.
    converted = historical_equivalent_price_vol(
        _vol_result(), _duration(price_basis=BondOptionPriceBasis.CLEAN)
    )

    with pytest.raises(BLIHistoricalEquivalentPriceVolError) as excinfo:
        historical_equivalent_price_vol_volatility_input(converted)

    message = str(excinfo.value)
    assert "CLEAN" in message
    assert "dirty forward" in message
    assert "Phase 4/5" in message


def test_a_clean_conversion_relabelled_dirty_cannot_be_published():
    # The gap Codex found: the allowlist was checked against the top-level
    # label, which `dataclasses.replace` can flip while the nested duration
    # -- and the volatility itself -- stay clean-derived.
    clean = historical_equivalent_price_vol(
        _vol_result(), _duration(price_basis=BondOptionPriceBasis.CLEAN)
    )
    relabelled = dataclasses.replace(clean, price_basis=BondOptionPriceBasis.DIRTY)

    assert relabelled.price_basis is BondOptionPriceBasis.DIRTY
    assert relabelled.duration.price_basis is BondOptionPriceBasis.CLEAN
    assert relabelled.equivalent_price_vol == clean.equivalent_price_vol

    with pytest.raises(BLIHistoricalEquivalentPriceVolError) as excinfo:
        historical_equivalent_price_vol_volatility_input(relabelled)
    message = str(excinfo.value)
    assert "labelled DIRTY" in message
    assert "CLEAN" in message


def test_a_dirty_conversion_relabelled_clean_cannot_be_published_either():
    dirty = historical_equivalent_price_vol(
        _vol_result(), _duration(price_basis=BondOptionPriceBasis.DIRTY)
    )
    relabelled = dataclasses.replace(dirty, price_basis=BondOptionPriceBasis.CLEAN)

    with pytest.raises(BLIHistoricalEquivalentPriceVolError):
        historical_equivalent_price_vol_volatility_input(relabelled)


def test_a_tampered_published_volatility_value_is_refused():
    # The published number must still be the product of its own recorded
    # parents; editing it alone does not make it that product.
    dirty = historical_equivalent_price_vol(
        _vol_result(), _duration(price_basis=BondOptionPriceBasis.DIRTY)
    )
    tampered = dataclasses.replace(
        dirty, equivalent_price_vol=dirty.equivalent_price_vol * 2
    )

    with pytest.raises(BLIHistoricalEquivalentPriceVolError) as excinfo:
        historical_equivalent_price_vol_volatility_input(tampered)
    assert "not the one its own recorded parents produce" in str(excinfo.value)


def test_publication_also_revalidates_the_nested_duration():
    # A conversion whose nested duration was tampered after the fact must not
    # publish either, even though the conversion itself was built legitimately.
    dirty = historical_equivalent_price_vol(
        _vol_result(), _duration(price_basis=BondOptionPriceBasis.DIRTY)
    )
    tampered = dataclasses.replace(
        dirty,
        duration=dataclasses.replace(
            dirty.duration, bumped_clean_price_up_per_100=999.0
        ),
    )

    with pytest.raises(BLIHistoricalEquivalentPriceVolError) as excinfo:
        historical_equivalent_price_vol_volatility_input(tampered)
    assert "not reproducible" in str(excinfo.value)


def test_the_clean_conversion_result_itself_still_exists_and_is_auditable():
    # Refusing publication must not delete the CLEAN answer: it stays fully
    # computable and inspectable, and is never coerced to DIRTY.
    result = _vol_result()
    clean = historical_equivalent_price_vol(
        result, _duration(price_basis=BondOptionPriceBasis.CLEAN)
    )
    dirty = historical_equivalent_price_vol(
        result, _duration(price_basis=BondOptionPriceBasis.DIRTY)
    )

    assert clean.price_basis is BondOptionPriceBasis.CLEAN
    assert clean.equivalent_price_vol > 0
    assert clean.equivalent_price_vol != dirty.equivalent_price_vol
    assert clean.duration.basis_price_per_100 == clean.duration.clean_price_per_100


def test_the_refusal_happens_before_any_volatility_input_is_constructed():
    # The refusal has to precede construction: once a BLIVolatilityInput
    # exists, nothing downstream can tell which basis its number belongs to,
    # and the guard accepts EQUIVALENT_PRICE_VOL regardless.
    import shiori_pricing_lab.pricing.bli_historical_equivalent_price_vol as converter

    converted = historical_equivalent_price_vol(
        _vol_result(), _duration(price_basis=BondOptionPriceBasis.CLEAN)
    )

    constructed = []
    original = converter.BLIVolatilityInput

    class _Tripwire(original):  # type: ignore[misc, valid-type]
        def __post_init__(self):
            constructed.append(self)
            super().__post_init__()

    converter.BLIVolatilityInput = _Tripwire
    try:
        with pytest.raises(BLIHistoricalEquivalentPriceVolError):
            historical_equivalent_price_vol_volatility_input(converted)
    finally:
        converter.BLIVolatilityInput = original

    assert constructed == []


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


# --- Duration arithmetic is revalidated at the boundary ----------------------


def _swap_magnitude(target, source):
    """``target``'s labels and prices, carrying ``source``'s duration magnitude.

    The exact corrupted record the label gates cannot see: every declared
    basis, type and denominator is target's and self-consistent, but the
    magnitude belongs to the other basis.
    """

    return dataclasses.replace(
        target,
        modified_duration=source.modified_duration,
        absolute_modified_duration=source.absolute_modified_duration,
    )


def test_a_clean_record_carrying_the_dirty_duration_magnitude_is_refused():
    clean = _duration(price_basis=BondOptionPriceBasis.CLEAN)
    dirty = _duration(price_basis=BondOptionPriceBasis.DIRTY)
    corrupted = _swap_magnitude(clean, dirty)

    # Every label gate still passes on this record -- that is the point.
    assert corrupted.price_basis is BondOptionPriceBasis.CLEAN
    assert corrupted.duration_type == "MODIFIED_DURATION_CLEAN_PRICE"
    assert corrupted.basis_price_per_100 == corrupted.clean_price_per_100

    with pytest.raises(BLIHistoricalEquivalentPriceVolError) as excinfo:
        historical_equivalent_price_vol(_vol_result(), corrupted)
    assert "not reproducible" in str(excinfo.value)


def test_a_dirty_record_carrying_the_clean_duration_magnitude_is_refused():
    clean = _duration(price_basis=BondOptionPriceBasis.CLEAN)
    dirty = _duration(price_basis=BondOptionPriceBasis.DIRTY)
    corrupted = _swap_magnitude(dirty, clean)

    assert corrupted.price_basis is BondOptionPriceBasis.DIRTY
    assert corrupted.duration_type == "MODIFIED_DURATION_DIRTY_PRICE"
    assert corrupted.basis_price_per_100 == corrupted.dirty_price_per_100

    with pytest.raises(BLIHistoricalEquivalentPriceVolError) as excinfo:
        historical_equivalent_price_vol(_vol_result(), corrupted)
    assert "not reproducible" in str(excinfo.value)


@pytest.mark.parametrize("basis", list(BondOptionPriceBasis))
def test_an_internally_consistent_record_is_accepted_on_either_basis(basis):
    duration = _duration(price_basis=basis)
    converted = historical_equivalent_price_vol(_vol_result(), duration)

    assert converted.price_basis is basis
    # And the gate's own re-derivation reproduces the record exactly.
    assert duration.modified_duration == (
        -duration.price_derivative_per_unit_yield / duration.basis_price_per_100
    )


def test_a_record_from_another_duration_methodology_is_refused():
    duration = dataclasses.replace(
        _duration(), methodology_version="SOME_OTHER_DURATION_V9"
    )
    with pytest.raises(BLIHistoricalEquivalentPriceVolError) as excinfo:
        historical_equivalent_price_vol(_vol_result(), duration)
    assert "SOME_OTHER_DURATION_V9" in str(excinfo.value)


def test_a_tampered_numerator_alone_is_refused():
    duration = dataclasses.replace(
        _duration(), price_derivative_per_unit_yield=-500.0
    )
    with pytest.raises(BLIHistoricalEquivalentPriceVolError):
        historical_equivalent_price_vol(_vol_result(), duration)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("clean_price_per_100", 100.0),
        ("accrued_interest_per_100", 0.5),
        ("dirty_price_per_100", 104.0),
        ("basis_price_per_100", 104.0),
        ("base_yield_percent", 3.9),
        ("bumped_yield_up_percent", 4.5),
        ("bumped_yield_down_percent", 3.5),
        ("bumped_clean_price_up_per_100", 99.0),
        ("bumped_clean_price_down_per_100", 103.0),
        ("yield_bump_basis_points", 5.0),
        ("coupon_percent", 5.0),
        ("coupons_per_year", 1),
        ("schedule_accrual_start", date(2026, 11, 20)),
    ],
)
def test_tampering_with_any_single_recorded_field_is_refused(field_name, value):
    # The reproducibility gate is a chain, not a spot check: every field that
    # feeds or records the calculation is covered, so no single edit produces
    # a different sigma_P while still passing.
    duration = dataclasses.replace(_duration(), **{field_name: value})
    with pytest.raises(BLIHistoricalEquivalentPriceVolError):
        historical_equivalent_price_vol(_vol_result(), duration)


def test_a_half_irregular_schedule_on_a_record_is_refused():
    duration = dataclasses.replace(_duration(), schedule_first_coupon=date(2027, 8, 15))
    with pytest.raises(BLIHistoricalEquivalentPriceVolError) as excinfo:
        historical_equivalent_price_vol(_vol_result(), duration)
    assert "half an irregular first-coupon schedule" in str(excinfo.value)


def test_an_irregular_duration_reproduces_and_converts():
    # The reproduction path must rebuild the schedule from the record, or
    # every irregular bond would be refused as unreproducible.
    from shiori_pricing_lab.pricing.treasury_futures_implied_yield import (
        IrregularFirstCoupon,
    )

    schedule = IrregularFirstCoupon(
        accrual_start=date(2026, 11, 20), first_coupon=date(2027, 8, 15)
    )
    duration = calculate_bond_modified_duration(
        security=_SECURITY,
        convention_profile="UST",
        price_basis=BondOptionPriceBasis.DIRTY,
        clean_price_per_100=101.067593,
        settlement_date=date(2027, 1, 10),
        maturity_date=date(2035, 8, 15),
        coupon_percent=4.25,
        pricing_timestamp="2027-01-08T16:00:00+00:00",
        calculated_at="2027-01-08T16:00:05+00:00",
        schedule=schedule,
    )
    converted = historical_equivalent_price_vol(_vol_result(), duration)

    assert duration.schedule_accrual_start == schedule.accrual_start
    assert converted.equivalent_price_vol > 0
    assert historical_equivalent_price_vol_volatility_input(converted).volatility == (
        converted.equivalent_price_vol
    )


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


@pytest.mark.parametrize("target", [0.0, -6.5, 6.5, 13.883649504899319])
def test_a_self_consistently_retargeted_duration_is_still_refused(target):
    # The gap Codex found: scaling the numerator and both magnitudes together
    # keeps every internal check satisfied while sigma_P moves and the
    # recorded bumped prices support none of it. The last parameter is
    # exactly twice the genuine DIRTY duration -- the reported doubling case.
    duration = _retargeted_duration(target)

    # The internal quotient still holds on this record; that is the point.
    if duration.basis_price_per_100:
        assert duration.modified_duration == pytest.approx(
            -duration.price_derivative_per_unit_yield / duration.basis_price_per_100
        )

    with pytest.raises(BLIHistoricalEquivalentPriceVolError) as excinfo:
        historical_equivalent_price_vol(_vol_result(), duration)
    assert "not reproducible" in str(excinfo.value)


def test_a_negative_absolute_duration_refuses_conversion():
    # absolute_modified_duration must be abs(modified_duration); a record
    # claiming otherwise is caught rather than silently used.
    result = _vol_result()
    duration = dataclasses.replace(_duration(), absolute_modified_duration=-6.5)
    with pytest.raises(BLIHistoricalEquivalentPriceVolError) as excinfo:
        historical_equivalent_price_vol(result, duration)
    assert "absolute_modified_duration" in str(excinfo.value)


def test_a_non_finite_duration_refuses_conversion():
    result = _vol_result()
    duration = dataclasses.replace(
        _duration(),
        modified_duration=float("inf"),
        absolute_modified_duration=float("inf"),
    )
    with pytest.raises(BLIHistoricalEquivalentPriceVolError):
        historical_equivalent_price_vol(result, duration)


def test_the_smallest_publishable_yield_vol_still_produces_a_positive_price_vol():
    # The conversion keeps an underflow guard, but it is now unreachable from
    # the public entry point and this test says so rather than pretending
    # otherwise: the reproducibility gate admits only durations a real bond
    # produces (O(1)-O(30)), and #197 refuses to publish a yield vol whose
    # daily figure is not strictly positive -- so the smallest product that
    # can arrive here is still many orders of magnitude above underflow. The
    # guard stays as defence in depth for a future caller; what is asserted
    # here is the reachable boundary.
    result = _result_with_vol(5e-324 * ANNUALIZATION_FACTOR)
    duration = _duration()

    converted = historical_equivalent_price_vol(result, duration)
    assert converted.equivalent_price_vol > 0


@pytest.mark.parametrize("bad", [None, "vol", 0.01])
def test_a_non_result_input_is_refused(bad):
    with pytest.raises(BLIHistoricalEquivalentPriceVolError):
        historical_equivalent_price_vol(bad, _duration())
    with pytest.raises(BLIHistoricalEquivalentPriceVolError):
        historical_equivalent_price_vol(_vol_result(), bad)


def test_publishing_a_non_converted_object_is_refused():
    with pytest.raises(BLIHistoricalEquivalentPriceVolError):
        historical_equivalent_price_vol_volatility_input("not a conversion")
