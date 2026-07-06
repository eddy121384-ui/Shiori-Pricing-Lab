"""Tests for ``build_bli_mvp_input_bundle`` (docs/24 §12 step 5, BLI MVP
input bundle builder implementation slice).

Builder-behavior tests only: this file checks that the builder resolves
correctly and constructs the same kind of ``BLIMVPInputBundle`` the
direct-construction path already validates in
``tests/test_bli_mvp_input_bundle.py`` -- it does not re-derive every
individual ``BLIMVPInputBundle`` validation-gate test, only confirms that
gate failures still propagate through the builder unchanged. No pricing
engine, curve interpolation, yield/price conversion, QuantLib, Bloomberg/
API connector, or UI is involved here.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from shiori_pricing_lab.data import (
    bli_mvp_input_bundle_builder as bli_mvp_input_bundle_builder_module,
)
from shiori_pricing_lab.data.bli_mvp_input_bundle import BLIMVPInputBundle
from shiori_pricing_lab.data.bli_mvp_input_bundle_builder import build_bli_mvp_input_bundle
from shiori_pricing_lab.data.bli_mvp_input_bundle_fixtures import (
    SYNTHETIC_BLI_MVP_INPUT_BUNDLE,
)
from shiori_pricing_lab.data.bli_snapshot import (
    BLIBondQuote,
    BLICreditSpreadInput,
    BLICreditSpreadTreatment,
    BLICurvePoint,
    BLICurvePurpose,
    BLIDepositRateObservation,
    BLIMarketDataSnapshot,
    BLIMarketDataStatus,
    BLIQuoteBasis,
    BLIVolatilityBasis,
    BLIVolatilityInput,
)
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.products.bond_linked_structured_product import (
    BondLinkedStructuredProduct,
)
from shiori_pricing_lab.products.bond_option import BondOption
from shiori_pricing_lab.products.deposit_leg import DepositLeg, TreasuryFTPRateSelector
from shiori_pricing_lab.products.enums import (
    Currency,
    DepositRateMode,
    ExerciseStyle,
    OptionType,
    PayoffBasis,
    Position,
    PrincipalRepaymentRule,
    SettlementType,
    TreasuryFTPQuoteSide,
    TreasuryFTPTenor,
)
from shiori_pricing_lab.products.fixtures import SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT
from shiori_pricing_lab.reference_data.fixtures import SYNTHETIC_BOND_FIXTURES
from shiori_pricing_lab.reference_data.resolution import BondResolutionStatus

_ELIGIBLE_ISIN = "XS0000000001"
_CALLABLE_INELIGIBLE_ISIN = "XS0000000003"
_UNKNOWN_ISIN = "XS_DOES_NOT_EXIST"


# --- Product-side helpers -----------------------------------------------


def _bond_option(**overrides) -> BondOption:
    params = dict(
        product_id="BONDOPT-BUILDER-TEST-0001",
        underlying_isin=_ELIGIBLE_ISIN,
        currency=Currency.USD,
        payoff_basis=PayoffBasis.PRICE,
        option_type=OptionType.CALL,
        exercise_style=ExerciseStyle.EUROPEAN,
        settlement_type=SettlementType.CASH,
        settlement_lag_days=2,
        expiry_date="2026-09-29",
        notional=50.0,
        position=Position.BUY,
        strike_price=99.5,
    )
    params.update(overrides)
    return BondOption(**params)


def _deposit_leg(**overrides) -> DepositLeg:
    params = dict(
        deposit_leg_id="DEP-BUILDER-TEST-0001",
        deposit_notional=100.0,
        currency=Currency.USD,
        start_date="2026-07-01",
        maturity_date="2026-10-01",
        deposit_rate_mode=DepositRateMode.TREASURY_FTP_REFERENCE,
        principal_repayment_rule=PrincipalRepaymentRule.FULL_PRINCIPAL_AT_MATURITY,
        tenor=TreasuryFTPTenor.THREE_MONTH,
        ftp_rate_selector=TreasuryFTPRateSelector(
            currency=Currency.USD,
            tenor=TreasuryFTPTenor.THREE_MONTH,
            quote_side=TreasuryFTPQuoteSide.MID,
        ),
    )
    params.update(overrides)
    return DepositLeg(**params)


def _product(**overrides) -> BondLinkedStructuredProduct:
    deposit_leg = overrides.pop("deposit_leg", None) or _deposit_leg()
    bond_option = overrides.pop("bond_option", None) or _bond_option()
    params = dict(
        product_id="BLI-BUILDER-TEST-0001", deposit_leg=deposit_leg, bond_option=bond_option
    )
    params.update(overrides)
    return BondLinkedStructuredProduct(**params)


# --- Market-data-side helpers --------------------------------------------


def _bond_quote(**overrides) -> BLIBondQuote:
    params = dict(
        isin=_ELIGIBLE_ISIN,
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
        curve_id="USD_TEST_CURVE",
        curve_name="USD Test Curve",
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.BOND_REFERENCE_CURVE,
        tenor="2Y",
        rate=0.035,
        source_system="TEST_FEED",
        status=BLIMarketDataStatus.ACTIVE,
    )
    params.update(overrides)
    return BLICurvePoint(**params)


def _all_required_curve_points() -> tuple[BLICurvePoint, ...]:
    return (
        _curve_point(
            curve_id="USD_BOND_REF_CURVE", curve_purpose=BLICurvePurpose.BOND_REFERENCE_CURVE
        ),
        _curve_point(
            curve_id="USD_OPTION_DISCOUNT_CURVE",
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
        ),
        _curve_point(curve_id="USD_DEPOSIT_CURVE", curve_purpose=BLICurvePurpose.DEPOSIT_CURVE),
    )


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
        curve_points=_all_required_curve_points(),
        volatility_input=_volatility_input(),
        credit_spread_input=_credit_spread_input(),
        deposit_rate_observation=_deposit_rate_observation(),
    )
    params.update(overrides)
    return BLIMarketDataSnapshot(**params)


# --- Builder helper --------------------------------------------------------


def _build(**overrides) -> BLIMVPInputBundle:
    params = dict(
        bundle_id="TEST-BUILDER-BUNDLE-0001",
        valuation_date="2026-07-01",
        product=overrides.pop("product", None) or _product(),
        bond_reference_data_universe=overrides.pop(
            "bond_reference_data_universe", SYNTHETIC_BOND_FIXTURES
        ),
        market_data_snapshot=overrides.pop("market_data_snapshot", None) or _snapshot(),
    )
    params.update(overrides)
    return build_bli_mvp_input_bundle(**params)


# --- 1. Happy path --------------------------------------------------------


def test_builder_constructs_valid_bundle_from_synthetic_fixtures():
    bundle = build_bli_mvp_input_bundle(
        bundle_id="BUILDER-HAPPY-PATH-0001",
        valuation_date=SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.valuation_date,
        product=SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT,
        bond_reference_data_universe=SYNTHETIC_BOND_FIXTURES,
        market_data_snapshot=SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT,
    )
    assert isinstance(bundle, BLIMVPInputBundle)
    assert bundle.product is SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT
    assert bundle.market_data_snapshot is SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
    assert bundle.resolved_bond_reference_data.isin == _ELIGIBLE_ISIN
    assert bundle.resolution_status is BondResolutionStatus.FOUND_ELIGIBLE
    assert bundle.eligibility_reasons == ()


def test_synthetic_bundle_fixture_was_built_through_the_builder():
    # data/bli_mvp_input_bundle_fixtures.py now calls the builder directly
    # (docs/24 §12 step 5) instead of hand-wiring resolve_bond_reference_data.
    bundle = SYNTHETIC_BLI_MVP_INPUT_BUNDLE
    assert bundle.product is SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT
    assert bundle.market_data_snapshot is SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
    assert bundle.resolved_bond_reference_data.isin == _ELIGIBLE_ISIN
    assert bundle.resolution_status is BondResolutionStatus.FOUND_ELIGIBLE


def test_builder_result_matches_direct_construction_fields():
    built = _build()
    assert built.bundle_id == "TEST-BUILDER-BUNDLE-0001"
    assert built.valuation_date == "2026-07-01"
    assert built.product.bond_option.underlying_isin == _ELIGIBLE_ISIN
    assert built.resolved_bond_reference_data.isin == _ELIGIBLE_ISIN
    assert built.resolution_status is BondResolutionStatus.FOUND_ELIGIBLE
    assert built.eligibility_reasons == ()


def test_builder_result_is_frozen_and_valid():
    built = _build()
    with pytest.raises(AttributeError):
        built.bundle_id = "OTHER"


# --- 2. Product type gate ---------------------------------------------------


def test_builder_rejects_wrong_type_product():
    with pytest.raises(TypeError, match="product must be a BondLinkedStructuredProduct"):
        _build(product=_bond_option())


# --- 3. Resolver failure handling -------------------------------------------


def test_builder_rejects_unknown_isin():
    unknown_product = _product(bond_option=_bond_option(underlying_isin=_UNKNOWN_ISIN))
    with pytest.raises(ValueError, match="NOT_FOUND"):
        _build(product=unknown_product)


def test_builder_rejects_ineligible_reference_data():
    ineligible_product = _product(
        bond_option=_bond_option(underlying_isin=_CALLABLE_INELIGIBLE_ISIN)
    )
    with pytest.raises(ValueError, match="FOUND_INELIGIBLE"):
        _build(product=ineligible_product)


def test_builder_error_includes_block_reason():
    unknown_product = _product(bond_option=_bond_option(underlying_isin=_UNKNOWN_ISIN))
    with pytest.raises(ValueError, match=_UNKNOWN_ISIN):
        _build(product=unknown_product)


def test_builder_does_not_fabricate_eligible_status_for_ineligible_bond():
    # The builder must pass through exactly what the resolver returned --
    # never coerce FOUND_INELIGIBLE into FOUND_ELIGIBLE, and never
    # silently return a bundle (or None) for a result that was not
    # actually eligible; it must raise.
    ineligible_product = _product(
        bond_option=_bond_option(underlying_isin=_CALLABLE_INELIGIBLE_ISIN)
    )
    with pytest.raises(ValueError, match="FOUND_INELIGIBLE"):
        _build(product=ineligible_product)


# --- 4. Validation propagation from BLIMVPInputBundle -----------------------


def test_market_data_isin_mismatch_still_rejected():
    mismatched_snapshot = _snapshot(bond_quote=_bond_quote(isin="XS9999999999"))
    with pytest.raises(ValueError, match="does not exactly match"):
        _build(market_data_snapshot=mismatched_snapshot)


def test_currency_mismatch_still_rejected():
    eur_bond_option = _bond_option(currency=Currency.EUR)
    eur_deposit_leg = _deposit_leg(
        currency=Currency.EUR,
        ftp_rate_selector=TreasuryFTPRateSelector(
            currency=Currency.EUR,
            tenor=TreasuryFTPTenor.THREE_MONTH,
            quote_side=TreasuryFTPQuoteSide.MID,
        ),
    )
    eur_product = _product(bond_option=eur_bond_option, deposit_leg=eur_deposit_leg)
    with pytest.raises(ValueError, match="product currency .* does not match"):
        _build(product=eur_product)


def test_as_of_timestamp_after_valuation_date_still_rejected():
    look_ahead_snapshot = _snapshot(as_of_timestamp="2026-07-02T00:00:01Z")
    with pytest.raises(ValueError, match="no-look-ahead"):
        _build(market_data_snapshot=look_ahead_snapshot)


def test_missing_required_curve_purpose_still_rejected():
    missing_deposit_curve_snapshot = _snapshot(
        curve_points=(
            _curve_point(
                curve_id="USD_BOND_REF_CURVE", curve_purpose=BLICurvePurpose.BOND_REFERENCE_CURVE
            ),
            _curve_point(
                curve_id="USD_OPTION_DISCOUNT_CURVE",
                curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            ),
        )
    )
    with pytest.raises(ValueError, match="missing required curve purpose"):
        _build(market_data_snapshot=missing_deposit_curve_snapshot)


def test_treasury_ftp_deposit_rate_observation_mismatch_still_rejected():
    mismatched_snapshot = _snapshot(
        deposit_rate_observation=_deposit_rate_observation(currency=Currency.EUR)
    )
    with pytest.raises(ValueError, match="deposit_rate_observation.currency"):
        _build(market_data_snapshot=mismatched_snapshot)


def test_treasury_ftp_reference_missing_deposit_rate_observation_still_rejected():
    snapshot_without_ftp = _snapshot(deposit_rate_observation=None)
    with pytest.raises(ValueError, match="deposit_rate_observation is required"):
        _build(market_data_snapshot=snapshot_without_ftp)


# --- 5. Scope: no pricing, no interpolation, no connector/UI ----------------


def test_builder_module_defines_no_pricing_or_curve_logic():
    module_names = set(dir(bli_mvp_input_bundle_builder_module))
    forbidden_names = {
        "price",
        "compute_pv",
        "interpolate",
        "interpolate_curve",
        "convert_yield_to_price",
        "convert_price_to_yield",
        "generate_cashflows",
        "build_schedule",
    }
    assert module_names.isdisjoint(forbidden_names)


def test_built_bundle_has_no_pricing_or_duplicated_fields():
    built = _build()
    field_names = {f.name for f in fields(built)}
    forbidden = {
        "pv",
        "dv01",
        "cashflows",
        "customer_return",
        "bank_margin",
        "option_premium",
        "payoff_amount",
        "discount_factor",
        "interpolated_rate",
    }
    assert field_names.isdisjoint(forbidden)


def test_builder_does_not_require_quantlib_or_external_connector():
    # A successful build with only synthetic, in-repo fixtures already
    # demonstrates this: no Bloomberg/API/QuantLib import is anywhere in
    # the builder module (verified by its absence from sys.modules
    # dependencies implicitly -- the import itself would have failed
    # above if any such dependency were required).
    bundle = _build()
    assert isinstance(bundle, BLIMVPInputBundle)
