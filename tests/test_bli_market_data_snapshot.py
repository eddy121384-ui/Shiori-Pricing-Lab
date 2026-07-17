"""Tests for ``BLIMarketDataSnapshot`` (docs/23, MarketDataSnapshot schema
preflight -- implementation slice).

Schema-construction and validation tests only. No MVP input bundle, bundle
builder, or pricing engine is involved here -- ``BLIMarketDataSnapshot`` is
a pure market-data container for one BLI valuation context.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from shiori_pricing_lab.data.bli_snapshot import (
    BLIBondQuote,
    BLICreditSpreadInput,
    BLICreditSpreadTreatment,
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIDepositRateObservation,
    BLIForwardCleanPriceInput,
    BLIMarketDataSnapshot,
    BLIMarketDataStatus,
    BLIQuoteBasis,
    BLIVolatilityBasis,
    BLIVolatilityInput,
    require_exact_isin_match,
)
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.products.enums import (
    Currency,
    TreasuryFTPQuoteSide,
    TreasuryFTPTenor,
)


def _bond_quote(**overrides) -> BLIBondQuote:
    params = dict(
        isin="XS0000000001",
        currency=Currency.USD,
        price_type=BLIQuoteBasis.PRICE,
        quote_side=TreasuryFTPQuoteSide.MID,
        source_system="TEST_FEED",
        status=BLIMarketDataStatus.ACTIVE,
        clean_price_per_100=101.25,
    )
    params.update(overrides)
    return BLIBondQuote(**params)


def _curve_point(**overrides) -> BLICurvePoint:
    params = dict(
        curve_id="USD_BOND_REFERENCE_CURVE",
        curve_name="USD Bond Reference Curve",
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.BOND_REFERENCE_CURVE,
        tenor="2Y",
        rate=0.035,
        rate_basis=BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
        source_system="TEST_FEED",
        status=BLIMarketDataStatus.ACTIVE,
    )
    params.update(overrides)
    return BLICurvePoint(**params)


def _deposit_rate_observation(**overrides) -> BLIDepositRateObservation:
    params = dict(
        currency=Currency.USD,
        tenor=TreasuryFTPTenor.THREE_MONTH,
        quote_side=TreasuryFTPQuoteSide.MID,
        ftp_rate_percent_value=3.5500,
        ftp_rate_decimal_value=0.0355,
        source_system="TEST_FEED",
        status=BLIMarketDataStatus.ACTIVE,
    )
    params.update(overrides)
    return BLIDepositRateObservation(**params)


def _volatility_input(**overrides) -> BLIVolatilityInput:
    params = dict(
        volatility=0.18,
        volatility_basis=BLIVolatilityBasis.PRICE_VOL,
        source_system="TEST_FEED",
        status=BLIMarketDataStatus.ACTIVE,
    )
    params.update(overrides)
    return BLIVolatilityInput(**params)


def _credit_spread_input(**overrides) -> BLICreditSpreadInput:
    params = dict(
        spread_treatment=BLICreditSpreadTreatment.OBSERVED,
        source_system="TEST_FEED",
        status=BLIMarketDataStatus.ACTIVE,
        credit_spread=0.0125,
        credit_spread_basis="BPS_OVER_CURVE",
    )
    params.update(overrides)
    return BLICreditSpreadInput(**params)


def _snapshot(**overrides) -> BLIMarketDataSnapshot:
    params = dict(
        valuation_date="2026-07-01",
        as_of_timestamp="2026-07-01T16:00:00Z",
        source_system="TEST_FEED",
        snapshot_id="TEST-SNAPSHOT-0001",
        status=BLIMarketDataStatus.ACTIVE,
        bond_quote=_bond_quote(),
        curve_points=(_curve_point(),),
        volatility_input=_volatility_input(),
        credit_spread_input=_credit_spread_input(),
        deposit_rate_observation=_deposit_rate_observation(),
    )
    params.update(overrides)
    return BLIMarketDataSnapshot(**params)


# --- 1. Happy-path fixture construction --------------------------------


def test_synthetic_fixture_constructs_successfully():
    snapshot = SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
    assert snapshot.bond_quote.isin == "XS0000000001"
    assert snapshot.status is BLIMarketDataStatus.ACTIVE
    assert snapshot.deposit_rate_observation is not None


def test_snapshot_helper_constructs_successfully():
    snapshot = _snapshot()
    assert snapshot.valuation_date == "2026-07-01"
    assert snapshot.deposit_rate_observation.ftp_rate_decimal_value == 0.0355


def test_snapshot_is_frozen():
    snapshot = _snapshot()
    with pytest.raises(AttributeError):
        snapshot.status = BLIMarketDataStatus.STALE


def test_snapshot_allows_none_deposit_rate_observation_for_fixed_rate_style_leg():
    snapshot = _snapshot(deposit_rate_observation=None)
    assert snapshot.deposit_rate_observation is None


# --- 2. Required string fields reject blank values ----------------------


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_isin_rejected(value):
    with pytest.raises(ValueError, match="isin must be a non-blank string"):
        _bond_quote(isin=value)


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_source_system_rejected_on_bond_quote(value):
    with pytest.raises(ValueError, match="source_system must be a non-blank string"):
        _bond_quote(source_system=value)


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_curve_id_rejected(value):
    with pytest.raises(ValueError, match="curve_id must be a non-blank string"):
        _curve_point(curve_id=value)


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_snapshot_id_rejected(value):
    with pytest.raises(ValueError, match="snapshot_id must be a non-blank string"):
        _snapshot(snapshot_id=value)


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_as_of_timestamp_rejected(value):
    with pytest.raises(ValueError, match="as_of_timestamp must be a non-blank string"):
        _snapshot(as_of_timestamp=value)


def test_blank_valuation_date_rejected():
    with pytest.raises(ValueError, match="valuation_date must be a non-blank string"):
        _snapshot(valuation_date="")


def test_valuation_date_bad_format_rejected():
    with pytest.raises(ValueError, match="valuation_date must be a YYYY-MM-DD date string"):
        _snapshot(valuation_date="20260701")


# --- 3. Numeric fields reject NaN / infinity -----------------------------


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_clean_price_rejects_non_finite(value):
    with pytest.raises(ValueError, match="clean_price_per_100 must be a finite number"):
        _bond_quote(clean_price_per_100=value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_curve_rate_rejects_non_finite(value):
    with pytest.raises(ValueError, match="rate must be a finite number"):
        _curve_point(rate=value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_volatility_rejects_non_finite(value):
    with pytest.raises(ValueError, match="volatility must be a finite number"):
        _volatility_input(volatility=value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_credit_spread_rejects_non_finite(value):
    with pytest.raises(ValueError, match="credit_spread must be a finite number"):
        _credit_spread_input(credit_spread=value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_ftp_percent_value_rejects_non_finite(value):
    with pytest.raises(ValueError, match="ftp_rate_percent_value must be a finite number"):
        _deposit_rate_observation(ftp_rate_percent_value=value)


def test_volatility_rejects_non_positive():
    with pytest.raises(ValueError, match="volatility must be positive"):
        _volatility_input(volatility=0.0)


# --- 3b. rate_basis: required, explicit, no default (docs/28 §6) -----------


def test_curve_point_happy_path_rate_basis_is_continuous_zero_rate():
    point = _curve_point()
    assert point.rate_basis is BLICurveRateBasis.CONTINUOUS_ZERO_RATE


def test_curve_point_accepts_raw_string_rate_basis():
    point = _curve_point(rate_basis="CONTINUOUS_ZERO_RATE")
    assert point.rate_basis is BLICurveRateBasis.CONTINUOUS_ZERO_RATE


def test_curve_point_rejects_invalid_rate_basis_string():
    with pytest.raises(ValueError, match="rate_basis"):
        _curve_point(rate_basis="NOT_A_RATE_BASIS")


@pytest.mark.parametrize("value", ["", " "])
def test_curve_point_rejects_blank_rate_basis_string(value):
    with pytest.raises(ValueError, match="rate_basis"):
        _curve_point(rate_basis=value)


def test_curve_point_requires_rate_basis_field():
    # Deliberately not using _curve_point(), whose params dict always
    # supplies a default rate_basis -- this asserts the dataclass field
    # itself has no default, so omitting it entirely fails loudly.
    params = dict(
        curve_id="USD_BOND_REFERENCE_CURVE",
        curve_name="USD Bond Reference Curve",
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.BOND_REFERENCE_CURVE,
        tenor="2Y",
        rate=0.035,
        source_system="TEST_FEED",
        status=BLIMarketDataStatus.ACTIVE,
    )
    with pytest.raises(TypeError):
        BLICurvePoint(**params)


def test_fixture_curve_points_all_state_continuous_zero_rate_basis():
    for point in SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.curve_points:
        assert point.rate_basis is BLICurveRateBasis.CONTINUOUS_ZERO_RATE


def test_rate_basis_does_not_imply_interpolation_or_discount_factor():
    # rate_basis is a data-contract field only (docs/28 §6) -- its
    # presence must not, by itself, make any pricing possible.
    from shiori_pricing_lab.data import bli_snapshot as bli_snapshot_module

    module_names = set(dir(bli_snapshot_module))
    forbidden_names = {
        "interpolate",
        "interpolate_curve",
        "discount_factor",
        "zero_rate",
        "par_rate",
        "forward_price",
        "forward_clean_price",
        "convert_par_to_zero",
        "black76",
        "black_76",
        "compute_pv",
    }
    assert module_names.isdisjoint(forbidden_names)


# --- 4. FTP percent/decimal consistency -----------------------------------


def test_ftp_percent_decimal_consistent_pair_passes():
    observation = _deposit_rate_observation(
        ftp_rate_percent_value=3.5500, ftp_rate_decimal_value=0.0355
    )
    assert observation.ftp_rate_percent_value == 3.5500
    assert observation.ftp_rate_decimal_value == 0.0355


def test_ftp_percent_decimal_inconsistent_pair_rejected():
    with pytest.raises(ValueError, match="inconsistent percent/decimal pair"):
        _deposit_rate_observation(ftp_rate_percent_value=3.5500, ftp_rate_decimal_value=3.55)


# --- 5. Curve duplicate / conflict / ambiguity validation -----------------


def test_same_curve_identity_different_tenor_passes():
    snapshot = _snapshot(
        curve_points=(
            _curve_point(tenor="2Y", rate=0.035),
            _curve_point(tenor="5Y", rate=0.037),
        )
    )
    assert len(snapshot.curve_points) == 2


def test_duplicate_curve_node_rejected():
    with pytest.raises(ValueError, match="duplicate curve node"):
        _snapshot(
            curve_points=(
                _curve_point(tenor="2Y", rate=0.035),
                _curve_point(tenor="2Y", rate=0.035),
            )
        )


def test_conflicting_curve_node_rejected():
    with pytest.raises(ValueError, match="conflicting rate"):
        _snapshot(
            curve_points=(
                _curve_point(tenor="2Y", rate=0.035),
                _curve_point(tenor="2Y", rate=0.036),
            )
        )


def test_ambiguous_curve_id_for_same_purpose_rejected():
    with pytest.raises(ValueError, match="ambiguous curve_id set"):
        _snapshot(
            curve_points=(
                _curve_point(curve_id="CURVE_A", tenor="2Y"),
                _curve_point(curve_id="CURVE_B", tenor="2Y"),
            )
        )


def test_empty_curve_points_rejected():
    with pytest.raises(ValueError, match="curve_points must not be empty"):
        _snapshot(curve_points=())


# --- 6. Deposit curve separation ------------------------------------------


def test_fixture_includes_deposit_curve_and_separate_deposit_rate_input():
    snapshot = SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
    deposit_curve_points = [
        point
        for point in snapshot.curve_points
        if point.curve_purpose is BLICurvePurpose.DEPOSIT_CURVE
    ]
    assert len(deposit_curve_points) >= 1
    assert snapshot.deposit_rate_observation is not None
    assert snapshot.deposit_rate_observation.ftp_rate_decimal_value == 0.0355


def test_fixture_includes_bond_reference_and_option_discount_curves():
    snapshot = SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
    purposes = {point.curve_purpose for point in snapshot.curve_points}
    assert BLICurvePurpose.BOND_REFERENCE_CURVE in purposes
    assert BLICurvePurpose.OPTION_DISCOUNT_CURVE in purposes
    assert BLICurvePurpose.DEPOSIT_CURVE in purposes


# --- 7. Volatility audit ---------------------------------------------------


def test_explicit_volatility_without_override_passes():
    vol = _volatility_input(override_or_fallback_audit=None)
    assert vol.override_or_fallback_audit is None


def test_volatility_override_with_audit_passes():
    vol = _volatility_input(
        override_or_fallback_audit="Manual override: desk head approved 2026-07-01"
    )
    assert vol.override_or_fallback_audit is not None


@pytest.mark.parametrize("value", ["", "   "])
def test_volatility_override_blank_audit_rejected(value):
    with pytest.raises(ValueError, match="override_or_fallback_audit must be a non-blank string"):
        _volatility_input(override_or_fallback_audit=value)


# --- 8. Credit spread audit -------------------------------------------------


def test_explicit_observed_credit_spread_passes():
    spread = _credit_spread_input(spread_treatment=BLICreditSpreadTreatment.OBSERVED)
    assert spread.credit_spread == 0.0125


def test_credit_spread_override_requires_audit():
    with pytest.raises(ValueError, match="override_or_fallback_audit must be a non-blank string"):
        _credit_spread_input(spread_treatment=BLICreditSpreadTreatment.OVERRIDE)


def test_credit_spread_override_with_audit_passes():
    spread = _credit_spread_input(
        spread_treatment=BLICreditSpreadTreatment.OVERRIDE,
        override_or_fallback_audit="Manual override: no bond-specific spread available",
    )
    assert spread.spread_treatment is BLICreditSpreadTreatment.OVERRIDE


def test_credit_spread_not_required_without_audit_rejected():
    with pytest.raises(ValueError, match="override_or_fallback_audit must be a non-blank string"):
        _credit_spread_input(
            spread_treatment=BLICreditSpreadTreatment.NOT_REQUIRED,
            credit_spread=None,
            credit_spread_basis=None,
        )


def test_credit_spread_not_required_with_audit_passes():
    spread = _credit_spread_input(
        spread_treatment=BLICreditSpreadTreatment.NOT_REQUIRED,
        credit_spread=None,
        credit_spread_basis=None,
        override_or_fallback_audit="Not required: cash-settled option, no bond spread applies",
    )
    assert spread.credit_spread is None


def test_credit_spread_not_required_rejects_silent_zero_default():
    with pytest.raises(ValueError, match="credit_spread must be None"):
        _credit_spread_input(
            spread_treatment=BLICreditSpreadTreatment.NOT_REQUIRED,
            credit_spread=0.0,
            credit_spread_basis=None,
            override_or_fallback_audit="Not required",
        )


def test_credit_spread_observed_missing_value_rejected():
    with pytest.raises(ValueError, match="credit_spread is required"):
        _credit_spread_input(
            spread_treatment=BLICreditSpreadTreatment.OBSERVED,
            credit_spread=None,
            credit_spread_basis=None,
        )


# --- 9. Exact ISIN expectation helper ---------------------------------------


def test_require_exact_isin_match_passes_for_matching_isin():
    require_exact_isin_match(SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT, "XS0000000001")


def test_require_exact_isin_match_rejects_mismatched_isin():
    with pytest.raises(ValueError, match="does not exactly match"):
        require_exact_isin_match(SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT, "XS0000000002")


def test_require_exact_isin_match_rejects_fuzzy_prefix_match():
    with pytest.raises(ValueError, match="does not exactly match"):
        require_exact_isin_match(SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT, "XS00000000")


# --- Quote side / price-and-yield semantics (docs/23 §4.2 "and/or") ---------
#
# Codex P2 review of PR #63: a bond price/yield feed may validly report both
# clean_price_per_100 and yield_value for the same observation. The snapshot
# preserves whichever of the two were actually observed -- it never performs
# a yield-to-price or price-to-yield conversion, and never discards one in
# favor of the other.


def test_bond_quote_price_only_passes():
    quote = _bond_quote(price_type=BLIQuoteBasis.PRICE, clean_price_per_100=101.25)
    assert quote.clean_price_per_100 == 101.25
    assert quote.yield_value is None


def test_bond_quote_yield_only_passes():
    quote = _bond_quote(
        price_type=BLIQuoteBasis.YIELD, clean_price_per_100=None, yield_value=0.041
    )
    assert quote.yield_value == 0.041
    assert quote.clean_price_per_100 is None


def test_bond_quote_both_price_and_yield_pass():
    quote = _bond_quote(
        price_type=BLIQuoteBasis.PRICE, clean_price_per_100=101.25, yield_value=0.041
    )
    assert quote.clean_price_per_100 == 101.25
    assert quote.yield_value == 0.041


def test_bond_quote_neither_price_nor_yield_rejected():
    with pytest.raises(
        ValueError, match="at least one of clean_price_per_100 / yield_value is required"
    ):
        _bond_quote(clean_price_per_100=None, yield_value=None)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_bond_quote_yield_value_rejects_non_finite(value):
    with pytest.raises(ValueError, match="yield_value must be a finite number"):
        _bond_quote(clean_price_per_100=None, yield_value=value)


def test_bond_quote_price_type_no_longer_requires_absence_of_the_other_field():
    # price_type still records the primarily-reported basis, but no longer
    # gates which of the two observed fields may be populated.
    quote = _bond_quote(
        price_type=BLIQuoteBasis.YIELD, clean_price_per_100=101.25, yield_value=0.041
    )
    assert quote.price_type is BLIQuoteBasis.YIELD
    assert quote.clean_price_per_100 == 101.25


# --- Status acceptance: only ACTIVE passes at construction (docs/23 §12) ---
#
# Codex P2 review of PR #63: STALE/INVALID/MISSING must not construct
# successfully -- a frozen snapshot with a stale/invalid/missing nested
# observation is dangerous because future bundle code may trust it.
# MANUAL_VERIFIED is also rejected for now, with a distinct message, since
# the audit policy that would make it acceptable is not implemented yet.

_NON_ACTIVE_STATUSES = [
    BLIMarketDataStatus.STALE,
    BLIMarketDataStatus.INVALID,
    BLIMarketDataStatus.MISSING,
]


@pytest.mark.parametrize("status", _NON_ACTIVE_STATUSES)
def test_snapshot_rejects_non_active_status(status):
    with pytest.raises(ValueError, match="snapshot status must be ACTIVE"):
        _snapshot(status=status)


def test_snapshot_rejects_manual_verified_status():
    with pytest.raises(ValueError, match="MANUAL_VERIFIED is not accepted yet"):
        _snapshot(status=BLIMarketDataStatus.MANUAL_VERIFIED)


@pytest.mark.parametrize("status", _NON_ACTIVE_STATUSES)
def test_bond_quote_rejects_non_active_status(status):
    with pytest.raises(ValueError, match="bond_quote status must be ACTIVE"):
        _bond_quote(status=status)


def test_bond_quote_rejects_manual_verified_status():
    with pytest.raises(ValueError, match="MANUAL_VERIFIED is not accepted yet"):
        _bond_quote(status=BLIMarketDataStatus.MANUAL_VERIFIED)


@pytest.mark.parametrize("status", _NON_ACTIVE_STATUSES)
def test_curve_point_rejects_non_active_status(status):
    with pytest.raises(ValueError, match="curve_point status must be ACTIVE"):
        _curve_point(status=status)


def test_curve_point_rejects_manual_verified_status():
    with pytest.raises(ValueError, match="MANUAL_VERIFIED is not accepted yet"):
        _curve_point(status=BLIMarketDataStatus.MANUAL_VERIFIED)


@pytest.mark.parametrize("status", _NON_ACTIVE_STATUSES)
def test_deposit_rate_observation_rejects_non_active_status(status):
    with pytest.raises(ValueError, match="deposit_rate_observation status must be ACTIVE"):
        _deposit_rate_observation(status=status)


def test_deposit_rate_observation_rejects_manual_verified_status():
    with pytest.raises(ValueError, match="MANUAL_VERIFIED is not accepted yet"):
        _deposit_rate_observation(status=BLIMarketDataStatus.MANUAL_VERIFIED)


@pytest.mark.parametrize("status", _NON_ACTIVE_STATUSES)
def test_volatility_input_rejects_non_active_status(status):
    with pytest.raises(ValueError, match="volatility_input status must be ACTIVE"):
        _volatility_input(status=status)


def test_volatility_input_rejects_manual_verified_status():
    with pytest.raises(ValueError, match="MANUAL_VERIFIED is not accepted yet"):
        _volatility_input(status=BLIMarketDataStatus.MANUAL_VERIFIED)


@pytest.mark.parametrize("status", _NON_ACTIVE_STATUSES)
def test_credit_spread_input_rejects_non_active_status(status):
    with pytest.raises(ValueError, match="credit_spread_input status must be ACTIVE"):
        _credit_spread_input(status=status)


def test_credit_spread_input_rejects_manual_verified_status():
    with pytest.raises(ValueError, match="MANUAL_VERIFIED is not accepted yet"):
        _credit_spread_input(status=BLIMarketDataStatus.MANUAL_VERIFIED)


def test_synthetic_fixture_status_is_active_everywhere():
    # The fixture must stay constructible under the new gating -- every
    # sub-observation (including nested curve points) is ACTIVE.
    snapshot = SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
    assert snapshot.status is BLIMarketDataStatus.ACTIVE
    assert snapshot.bond_quote.status is BLIMarketDataStatus.ACTIVE
    assert all(point.status is BLIMarketDataStatus.ACTIVE for point in snapshot.curve_points)
    assert snapshot.volatility_input.status is BLIMarketDataStatus.ACTIVE
    assert snapshot.credit_spread_input.status is BLIMarketDataStatus.ACTIVE
    assert snapshot.deposit_rate_observation.status is BLIMarketDataStatus.ACTIVE


# --- Frozen dataclasses ------------------------------------------------------


@pytest.mark.parametrize(
    "cls",
    [
        BLIBondQuote,
        BLICurvePoint,
        BLIDepositRateObservation,
        BLIVolatilityInput,
        BLICreditSpreadInput,
        BLIMarketDataSnapshot,
    ],
)
def test_all_bli_schema_objects_are_frozen(cls):
    assert cls.__dataclass_params__.frozen is True


# --- Class name / module boundary --------------------------------------------


def test_bli_snapshot_is_distinct_from_vanilla_rates_core_snapshot():
    from shiori_pricing_lab.data.snapshot import MarketDataSnapshot

    assert BLIMarketDataSnapshot is not MarketDataSnapshot


def test_bli_market_data_snapshot_has_no_product_or_pricing_fields():
    field_names = {f.name for f in fields(BLIMarketDataSnapshot)}
    forbidden = {"pv", "dv01", "cashflows", "product_type", "underlying_isin", "notional"}
    assert field_names.isdisjoint(forbidden)


# --- BLIForwardCleanPriceInput (Issue #94) -----------------------------------


def _forward_clean_price_input(**overrides) -> BLIForwardCleanPriceInput:
    params = dict(
        forward_clean_price_per_100=101.30,
        quote_side=TreasuryFTPQuoteSide.MID,
        source_system="TEST_FORWARD_FEED",
        status=BLIMarketDataStatus.ACTIVE,
    )
    params.update(overrides)
    return BLIForwardCleanPriceInput(**params)


def test_forward_clean_price_input_valid_construction():
    forward = _forward_clean_price_input()
    assert forward.forward_clean_price_per_100 == 101.30
    assert forward.quote_side is TreasuryFTPQuoteSide.MID
    assert forward.status is BLIMarketDataStatus.ACTIVE


def test_forward_clean_price_input_coerces_raw_quote_side_and_status():
    forward = _forward_clean_price_input(quote_side="MID", status="ACTIVE")
    assert forward.quote_side is TreasuryFTPQuoteSide.MID
    assert forward.status is BLIMarketDataStatus.ACTIVE


@pytest.mark.parametrize("value", [0.0, -1.0, -0.01])
def test_forward_clean_price_input_rejects_non_positive(value):
    with pytest.raises(ValueError, match="forward_clean_price_per_100 must be positive"):
        _forward_clean_price_input(forward_clean_price_per_100=value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True, "101.3"])
def test_forward_clean_price_input_rejects_non_finite_number(value):
    with pytest.raises(ValueError, match="forward_clean_price_per_100"):
        _forward_clean_price_input(forward_clean_price_per_100=value)


@pytest.mark.parametrize("value", ["", "   "])
def test_forward_clean_price_input_rejects_blank_source_system(value):
    with pytest.raises(ValueError, match="source_system"):
        _forward_clean_price_input(source_system=value)


@pytest.mark.parametrize("status", _NON_ACTIVE_STATUSES)
def test_forward_clean_price_input_rejects_non_active_status(status):
    with pytest.raises(ValueError, match="forward_clean_price_input status must be ACTIVE"):
        _forward_clean_price_input(status=status)


def test_forward_clean_price_input_rejects_manual_verified_status():
    with pytest.raises(ValueError, match="MANUAL_VERIFIED is not accepted yet"):
        _forward_clean_price_input(status=BLIMarketDataStatus.MANUAL_VERIFIED)


def test_snapshot_forward_clean_price_input_defaults_to_none():
    # Optional at the general snapshot level: the legacy bundle path neither
    # supplies nor reads it, so existing fixtures stay backward compatible.
    assert SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.forward_clean_price_input is None


def test_snapshot_rejects_wrong_forward_clean_price_input_type():
    from dataclasses import replace

    with pytest.raises(TypeError, match="forward_clean_price_input"):
        replace(SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT, forward_clean_price_input=object())


def test_snapshot_accepts_valid_forward_clean_price_input():
    from dataclasses import replace

    forward = _forward_clean_price_input()
    snapshot = replace(SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT, forward_clean_price_input=forward)
    assert snapshot.forward_clean_price_input is forward
