"""Tests for ``BLIStandaloneBondOptionRequest`` construction (Issue #95).

Construction rejects *cross-object incoherence only* (invalid valuation
date, ineligible reference data, ISIN mismatch, currency mismatch,
valuation-date mismatch, future as-of date, missing option-leg curve
purpose). It deliberately does **not** narrow a valid general
``BondOption`` down to the supported *pricing* shape -- American exercise,
a yield payoff, physical settlement, ``YIELD_VOL``, and a yield-only bond
quote all stay *constructible* here and are rejected later by the pricing
guard (see ``tests/test_bli_pricing_engine_standalone_option.py``).

Also pins the mechanically-relocated ``_parse_as_of_calendar_date``
(now in ``data/_validation.py``) directly, proving the move preserved its
policy unchanged (Issue #95 correction #3); the existing bundle as-of tests
in ``tests/test_bli_mvp_input_bundle.py`` remain the retention proof that
the old bundle behavior is unchanged.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date

import pytest

from shiori_pricing_lab.data._validation import _parse_as_of_calendar_date
from shiori_pricing_lab.data.bli_mvp_input_bundle_fixtures import (
    SYNTHETIC_BLI_MVP_INPUT_BUNDLE,
)
from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIMarketDataStatus,
    BLIVolatilityBasis,
)
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.products.enums import (
    Currency,
    ExerciseStyle,
    PayoffBasis,
    SettlementType,
)
from shiori_pricing_lab.reference_data.fixtures import SYNTHETIC_BOND_FIXTURES

# Economic pieces of the shared, reviewed MVP fixture, reused as-is.
_BOND_OPTION = SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option
_REFERENCE_DATA = SYNTHETIC_BLI_MVP_INPUT_BUNDLE.resolved_bond_reference_data
_VALUATION_DATE = SYNTHETIC_BLI_MVP_INPUT_BUNDLE.valuation_date
_SNAPSHOT = SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT

# Synthetic timing/date contract values (Issue #94 human methodology
# approval, comment 5001749998). _BOND_OPTION.expiry_date is "2026-09-29".
# forward_settlement_date and option_settlement_date are deliberately
# different dates below, proving the contract never requires them equal.
_PRICING_TIMESTAMP = "2026-07-01T16:00:00Z"
_EXPIRY_TIMESTAMP = "2026-09-29T16:00:00Z"
_REPORTING_DATE = "2026-07-01"
_FORWARD_SETTLEMENT_DATE = "2026-10-01"
_OPTION_SETTLEMENT_DATE = "2026-10-02"


def _short_tenor_curve_points(
    currency: Currency, *, include_deposit: bool = True
) -> tuple[BLICurvePoint, ...]:
    """Short-tenor Bond Reference + Option Discount (+ optional Deposit) nodes.

    Mirrors ``tests/test_bli_pricing_engine.py``'s own test-local
    short-tenor curve helper. ``include_deposit=False`` proves a standalone
    request needs no Deposit Curve.
    """

    common = {
        "currency": currency,
        "rate_basis": BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
        "source_system": "TEST_LOCAL_CURVE",
        "status": BLIMarketDataStatus.ACTIVE,
    }
    bond_reference_nodes = tuple(
        BLICurvePoint(
            curve_id="TEST_LOCAL_BOND_REFERENCE_CURVE",
            curve_name="TEST_LOCAL_BOND_REFERENCE_CURVE",
            curve_purpose=BLICurvePurpose.BOND_REFERENCE_CURVE,
            tenor=tenor,
            rate=rate,
            **common,
        )
        for tenor, rate in (("1M", 0.030), ("1Y", 0.035))
    )
    option_discount_nodes = tuple(
        BLICurvePoint(
            curve_id="TEST_LOCAL_OPTION_DISCOUNT_CURVE",
            curve_name="TEST_LOCAL_OPTION_DISCOUNT_CURVE",
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            tenor=tenor,
            rate=rate,
            **common,
        )
        for tenor, rate in (("1M", 0.028), ("1Y", 0.032))
    )
    nodes = bond_reference_nodes + option_discount_nodes
    if include_deposit:
        nodes += tuple(
            point
            for point in SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.curve_points
            if point.curve_purpose is BLICurvePurpose.DEPOSIT_CURVE
        )
    return nodes


def _make_request(**overrides) -> BLIStandaloneBondOptionRequest:
    params = dict(
        bond_option=_BOND_OPTION,
        resolved_bond_reference_data=_REFERENCE_DATA,
        valuation_date=_VALUATION_DATE,
        market_data_snapshot=_SNAPSHOT,
        pricing_timestamp=_PRICING_TIMESTAMP,
        expiry_timestamp=_EXPIRY_TIMESTAMP,
        reporting_date=_REPORTING_DATE,
        forward_settlement_date=_FORWARD_SETTLEMENT_DATE,
        option_settlement_date=_OPTION_SETTLEMENT_DATE,
    )
    params.update(overrides)
    return BLIStandaloneBondOptionRequest(**params)


# --- 1. Happy path: a coherent standalone request constructs -----------------


def test_coherent_request_constructs():
    request = _make_request()
    assert isinstance(request, BLIStandaloneBondOptionRequest)
    assert request.bond_option is _BOND_OPTION
    assert request.resolved_bond_reference_data is _REFERENCE_DATA
    assert request.market_data_snapshot is _SNAPSHOT
    # Every supplied timing/date string is preserved exactly -- parsing in
    # __post_init__ is for validation only, never normalization/rewriting.
    assert request.pricing_timestamp == _PRICING_TIMESTAMP
    assert request.expiry_timestamp == _EXPIRY_TIMESTAMP
    assert request.reporting_date == _REPORTING_DATE
    assert request.forward_settlement_date == _FORWARD_SETTLEMENT_DATE
    assert request.option_settlement_date == _OPTION_SETTLEMENT_DATE


def test_request_needs_no_deposit_curve():
    # Acceptance criterion: no Deposit Curve is required for standalone
    # option-leg pricing. A snapshot carrying only the two option-leg curve
    # purposes (no DEPOSIT_CURVE) must still construct.
    deposit_free_snapshot = replace(
        SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT,
        snapshot_id="TEST_DEPOSIT_FREE_SNAPSHOT",
        source_system="TEST_LOCAL_CURVE",
        curve_points=_short_tenor_curve_points(_BOND_OPTION.currency, include_deposit=False),
    )
    present_purposes = {
        point.curve_purpose for point in deposit_free_snapshot.curve_points
    }
    assert BLICurvePurpose.DEPOSIT_CURVE not in present_purposes

    request = _make_request(market_data_snapshot=deposit_free_snapshot)
    assert isinstance(request, BLIStandaloneBondOptionRequest)


# --- 2. Shape is NOT narrowed at construction (Issue #95 correction #4) -------


def test_american_exercise_still_constructible():
    american_option = replace(
        _BOND_OPTION,
        exercise_style=ExerciseStyle.AMERICAN,
        exercise_start_date="2026-06-01",
    )
    # No exception: the pricing guard, not this constructor, rejects it.
    request = _make_request(bond_option=american_option)
    assert request.bond_option.exercise_style is ExerciseStyle.AMERICAN


def test_yield_payoff_still_constructible():
    yield_option = replace(
        _BOND_OPTION,
        payoff_basis=PayoffBasis.YIELD,
        strike_price=None,
        strike_yield=0.035,
    )
    request = _make_request(bond_option=yield_option)
    assert request.bond_option.payoff_basis is PayoffBasis.YIELD


def test_physical_settlement_still_constructible():
    # A bare BondOption is not wrapped by BondLinkedStructuredProduct, so
    # PHYSICAL settlement is constructible here (unlike the bundle path);
    # the pricing guard rejects it later.
    physical_option = replace(_BOND_OPTION, settlement_type=SettlementType.PHYSICAL)
    request = _make_request(bond_option=physical_option)
    assert request.bond_option.settlement_type is SettlementType.PHYSICAL


def test_yield_vol_still_constructible():
    yield_vol_snapshot = replace(
        _SNAPSHOT,
        volatility_input=replace(
            _SNAPSHOT.volatility_input, volatility_basis=BLIVolatilityBasis.YIELD_VOL
        ),
    )
    request = _make_request(market_data_snapshot=yield_vol_snapshot)
    assert (
        request.market_data_snapshot.volatility_input.volatility_basis
        is BLIVolatilityBasis.YIELD_VOL
    )


def test_yield_only_bond_quote_still_constructible():
    yield_only_snapshot = replace(
        _SNAPSHOT,
        bond_quote=replace(
            _SNAPSHOT.bond_quote, clean_price_per_100=None, yield_value=0.035
        ),
    )
    request = _make_request(market_data_snapshot=yield_only_snapshot)
    assert request.market_data_snapshot.bond_quote.clean_price_per_100 is None


# --- 3. Cross-object incoherence IS rejected at construction ------------------


def test_wrong_bond_option_type_rejected():
    with pytest.raises(TypeError, match="bond_option must be a BondOption"):
        _make_request(bond_option=object())


def test_wrong_reference_data_type_rejected():
    with pytest.raises(TypeError, match="resolved_bond_reference_data must be a BondReferenceData"):
        _make_request(resolved_bond_reference_data=object())


def test_wrong_snapshot_type_rejected():
    with pytest.raises(TypeError, match="market_data_snapshot must be a BLIMarketDataSnapshot"):
        _make_request(market_data_snapshot=object())


def test_invalid_valuation_date_rejected():
    with pytest.raises(ValueError, match="valuation_date"):
        _make_request(valuation_date="not-a-date")


def test_ineligible_reference_data_rejected():
    ineligible_bond = SYNTHETIC_BOND_FIXTURES[1]  # XS0000000002, not MVP-eligible
    option_for_ineligible = replace(
        _BOND_OPTION, underlying_isin=ineligible_bond.isin
    )
    with pytest.raises(ValueError, match="not MVP-pricing eligible"):
        _make_request(
            bond_option=option_for_ineligible,
            resolved_bond_reference_data=ineligible_bond,
        )


def test_isin_mismatch_rejected():
    mismatched_option = replace(_BOND_OPTION, underlying_isin="XS0000000099")
    with pytest.raises(ValueError, match="does not exactly match"):
        _make_request(bond_option=mismatched_option)


def test_currency_mismatch_rejected():
    eur_option = replace(_BOND_OPTION, currency=Currency.EUR)
    with pytest.raises(ValueError, match="does not match"):
        _make_request(bond_option=eur_option)


def test_valuation_date_mismatch_rejected():
    # snapshot.valuation_date is 2026-07-01; request says 2026-06-30.
    with pytest.raises(ValueError, match="must equal request valuation_date"):
        _make_request(valuation_date="2026-06-30")


def test_future_as_of_date_rejected():
    future_snapshot = replace(_SNAPSHOT, as_of_timestamp="2026-07-02T00:00:00Z")
    with pytest.raises(ValueError, match="no-look-ahead"):
        _make_request(market_data_snapshot=future_snapshot)


def test_missing_option_leg_curve_purpose_rejected():
    # Drop OPTION_DISCOUNT_CURVE rows entirely.
    without_option_discount = replace(
        _SNAPSHOT,
        curve_points=tuple(
            point
            for point in _SNAPSHOT.curve_points
            if point.curve_purpose is not BLICurvePurpose.OPTION_DISCOUNT_CURVE
        ),
    )
    with pytest.raises(ValueError, match="missing required curve purpose"):
        _make_request(market_data_snapshot=without_option_discount)


# --- 4. Immutability ---------------------------------------------------------


def test_request_is_frozen():
    request = _make_request()
    with pytest.raises(FrozenInstanceError):
        request.valuation_date = "2026-01-01"  # type: ignore[misc]


# --- 5. Relocated _parse_as_of_calendar_date (mechanical move, policy same) ---


def test_relocated_as_of_parser_accepts_bare_date():
    assert _parse_as_of_calendar_date("2026-07-01", "as_of") == date(2026, 7, 1)


def test_relocated_as_of_parser_accepts_utc_z_suffix():
    assert _parse_as_of_calendar_date("2026-07-01T16:00:00Z", "as_of") == date(2026, 7, 1)


def test_relocated_as_of_parser_accepts_naive_datetime():
    assert _parse_as_of_calendar_date("2026-07-01T16:00:00", "as_of") == date(2026, 7, 1)


def test_relocated_as_of_parser_rejects_non_utc_offset():
    with pytest.raises(ValueError, match="non-UTC timezone"):
        _parse_as_of_calendar_date("2026-07-01T23:30:00-05:00", "as_of")


# --- 6. Timing/date contract (Issue #94 human methodology approval) ----------


def test_offset_variants_all_accepted():
    # "Z", "+00:00", and a non-UTC explicit offset (e.g. "+08:00") are all
    # permitted -- only an explicit offset is required, not UTC
    # specifically. pricing_timestamp.date() (the local calendar date
    # embedded in the string, per the contract) must still equal
    # valuation_date ("2026-07-01") regardless of which offset is used.
    for pricing_ts in (
        "2026-07-01T16:00:00Z",
        "2026-07-01T16:00:00+00:00",
        "2026-07-01T08:00:00+08:00",
    ):
        request = _make_request(pricing_timestamp=pricing_ts)
        assert request.pricing_timestamp == pricing_ts


def test_bare_date_pricing_timestamp_rejected():
    with pytest.raises(ValueError, match="pricing_timestamp"):
        _make_request(pricing_timestamp="2026-07-01")


def test_bare_date_expiry_timestamp_rejected():
    with pytest.raises(ValueError, match="expiry_timestamp"):
        _make_request(expiry_timestamp="2026-09-29")


def test_naive_pricing_timestamp_rejected():
    with pytest.raises(ValueError, match="pricing_timestamp"):
        _make_request(pricing_timestamp="2026-07-01T16:00:00")


def test_naive_expiry_timestamp_rejected():
    with pytest.raises(ValueError, match="expiry_timestamp"):
        _make_request(expiry_timestamp="2026-09-29T16:00:00")


def test_malformed_pricing_timestamp_rejected():
    with pytest.raises(ValueError, match="pricing_timestamp"):
        _make_request(pricing_timestamp="not-a-timestamp")


def test_malformed_expiry_timestamp_rejected():
    with pytest.raises(ValueError, match="expiry_timestamp"):
        _make_request(expiry_timestamp="not-a-timestamp")


def test_pricing_timestamp_date_mismatch_with_valuation_date_rejected():
    # valuation_date is "2026-07-01"; shift pricing_timestamp's calendar
    # date one day forward while keeping it otherwise well-formed.
    with pytest.raises(ValueError, match="must fall on valuation_date"):
        _make_request(pricing_timestamp="2026-07-02T16:00:00Z")


def test_expiry_timestamp_date_mismatch_with_bond_option_expiry_date_rejected():
    # bond_option.expiry_date is "2026-09-29".
    with pytest.raises(ValueError, match="must fall on bond_option.expiry_date"):
        _make_request(expiry_timestamp="2026-09-30T16:00:00Z")


def test_expiry_timestamp_not_strictly_after_pricing_timestamp_rejected():
    # A same-day expiry (expiry_date == valuation_date) keeps both
    # date-match checks satisfied while making the ordering check genuinely
    # reachable: the expiry instant (10:00Z) precedes the pricing instant
    # (16:00Z) on that same calendar date.
    same_day_option = replace(_BOND_OPTION, expiry_date=_VALUATION_DATE)
    with pytest.raises(ValueError, match="must be strictly after pricing_timestamp"):
        _make_request(
            bond_option=same_day_option,
            pricing_timestamp="2026-07-01T16:00:00Z",
            expiry_timestamp="2026-07-01T10:00:00Z",
        )


def test_malformed_reporting_date_rejected():
    with pytest.raises(ValueError, match="reporting_date"):
        _make_request(reporting_date="not-a-date")


def test_malformed_forward_settlement_date_rejected():
    with pytest.raises(ValueError, match="forward_settlement_date"):
        _make_request(forward_settlement_date="not-a-date")


def test_malformed_option_settlement_date_rejected():
    with pytest.raises(ValueError, match="option_settlement_date"):
        _make_request(option_settlement_date="not-a-date")


def test_reporting_date_before_valuation_date_rejected():
    with pytest.raises(ValueError, match="reporting_date"):
        _make_request(reporting_date="2026-06-30")


def test_reporting_date_equal_to_valuation_date_accepted():
    # ">=" per the contract -- equality is allowed, not just strictly after.
    request = _make_request(reporting_date=_VALUATION_DATE)
    assert request.reporting_date == _VALUATION_DATE


def test_forward_settlement_date_before_expiry_date_rejected():
    # bond_option.expiry_date is "2026-09-29".
    with pytest.raises(ValueError, match="forward_settlement_date"):
        _make_request(forward_settlement_date="2026-09-28")


def test_option_settlement_date_before_expiry_date_rejected():
    with pytest.raises(ValueError, match="option_settlement_date"):
        _make_request(option_settlement_date="2026-09-28")


def test_forward_and_option_settlement_dates_equal_to_expiry_date_accepted():
    # ">=" per the contract -- equal to bond_option.expiry_date is allowed.
    request = _make_request(
        forward_settlement_date=_BOND_OPTION.expiry_date,
        option_settlement_date=_BOND_OPTION.expiry_date,
    )
    assert request.forward_settlement_date == _BOND_OPTION.expiry_date
    assert request.option_settlement_date == _BOND_OPTION.expiry_date


def test_forward_and_option_settlement_dates_may_differ():
    # The contract never requires these two distinct documented concepts to
    # be equal -- the default _make_request() already uses different dates
    # for each; this test pins that explicitly.
    request = _make_request(
        forward_settlement_date="2026-10-01", option_settlement_date="2026-11-15"
    )
    assert request.forward_settlement_date == "2026-10-01"
    assert request.option_settlement_date == "2026-11-15"
    assert request.forward_settlement_date != request.option_settlement_date


def test_construction_does_not_derive_from_settlement_lag_days():
    # bond_option.settlement_lag_days is whatever the fixture carries;
    # forward_settlement_date / option_settlement_date here are deliberately
    # far past bond_option.expiry_date + settlement_lag_days and are neither
    # read against, compared to, nor rejected for disagreeing with it --
    # proving no lag/calendar derivation exists in this contract.
    far_future_date = "2027-01-01"
    request = _make_request(
        forward_settlement_date=far_future_date, option_settlement_date=far_future_date
    )
    assert request.forward_settlement_date == far_future_date
    assert request.option_settlement_date == far_future_date
    assert isinstance(_BOND_OPTION.settlement_lag_days, int)  # sanity: field exists, unused here


def test_module_does_not_access_settlement_lag_days_attribute():
    # AST-based, not raw text search: the module's own docstrings
    # legitimately *discuss* settlement_lag_days in prose (explaining what
    # this contract does NOT derive from it), which would false-positive a
    # plain substring scan. Parsing the actual code and checking for a real
    # `.settlement_lag_days` attribute access is the precise proof that no
    # such derivation exists in executable logic.
    import ast
    import inspect

    from shiori_pricing_lab.data import bli_standalone_option_request as module

    tree = ast.parse(inspect.getsource(module))
    accessed_attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "settlement_lag_days" not in accessed_attributes
