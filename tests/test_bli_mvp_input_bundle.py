"""Tests for ``BLIMVPInputBundle`` (docs/24, MVP input bundle implementation
slice).

Schema-construction and validation tests only. No bundle builder,
pricing engine, curve interpolation, or yield/price conversion is
involved here -- ``BLIMVPInputBundle`` only binds an already-validated
``BondLinkedStructuredProduct``, a resolved ``BondReferenceData``, and a
``BLIMarketDataSnapshot`` by reference and cross-checks what they agree
or disagree on.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from shiori_pricing_lab.data import bli_mvp_input_bundle as bli_mvp_input_bundle_module
from shiori_pricing_lab.data.bli_mvp_input_bundle import BLIMVPInputBundle
from shiori_pricing_lab.data.bli_mvp_input_bundle_fixtures import (
    SYNTHETIC_BLI_MVP_INPUT_BUNDLE,
)
from shiori_pricing_lab.data.bli_snapshot import (
    BLIBondQuote,
    BLICreditSpreadInput,
    BLICreditSpreadTreatment,
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIDepositRateObservation,
    BLIMarketDataSnapshot,
    BLIMarketDataStatus,
    BLIQuoteBasis,
    BLIVolatilityBasis,
    BLIVolatilityInput,
)
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
from shiori_pricing_lab.reference_data.eligibility import is_mvp_pricing_eligible
from shiori_pricing_lab.reference_data.resolution import (
    BondResolutionStatus,
    resolve_bond_reference_data,
)

_ELIGIBLE_ISIN = "XS0000000001"
_CALLABLE_INELIGIBLE_ISIN = "XS0000000003"


# --- Product-side helpers -----------------------------------------------


def _bond_option(**overrides) -> BondOption:
    params = dict(
        product_id="BONDOPT-TEST-0001",
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
        deposit_leg_id="DEP-TEST-0001",
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


def _fixed_rate_deposit_leg(**overrides) -> DepositLeg:
    params = dict(
        deposit_leg_id="DEP-TEST-FIXED-0001",
        deposit_notional=100.0,
        currency=Currency.USD,
        start_date="2026-07-01",
        maturity_date="2026-10-01",
        deposit_rate_mode=DepositRateMode.FIXED_RATE,
        principal_repayment_rule=PrincipalRepaymentRule.FULL_PRINCIPAL_AT_MATURITY,
        fixed_deposit_rate=0.03,
    )
    params.update(overrides)
    return DepositLeg(**params)


def _manual_verified_deposit_leg(**overrides) -> DepositLeg:
    params = dict(
        deposit_leg_id="DEP-TEST-MANUAL-0001",
        deposit_notional=100.0,
        currency=Currency.USD,
        start_date="2026-07-01",
        maturity_date="2026-10-01",
        deposit_rate_mode=DepositRateMode.MANUAL_VERIFIED_RATE,
        principal_repayment_rule=PrincipalRepaymentRule.FULL_PRINCIPAL_AT_MATURITY,
        manual_input_reference="MANUAL-INPUT-REF-0001",
    )
    params.update(overrides)
    return DepositLeg(**params)


def _product(**overrides) -> BondLinkedStructuredProduct:
    deposit_leg = overrides.pop("deposit_leg", None) or _deposit_leg()
    bond_option = overrides.pop("bond_option", None) or _bond_option()
    params = dict(product_id="BLI-TEST-0001", deposit_leg=deposit_leg, bond_option=bond_option)
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
        rate_basis=BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
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


# --- Bundle helper --------------------------------------------------------


def _bundle(**overrides) -> BLIMVPInputBundle:
    product = overrides.pop("product", None) or _product()
    resolution = overrides.pop(
        "resolution", None
    ) or resolve_bond_reference_data(product.bond_option.underlying_isin)
    market_data_snapshot = overrides.pop("market_data_snapshot", None) or _snapshot()
    params = dict(
        bundle_id="TEST-BUNDLE-0001",
        valuation_date="2026-07-01",
        product=product,
        resolved_bond_reference_data=resolution.bond_reference_data,
        resolution_status=resolution.status,
        eligibility_reasons=resolution.eligibility_reasons,
        market_data_snapshot=market_data_snapshot,
    )
    params.update(overrides)
    return BLIMVPInputBundle(**params)


# --- 1. Happy path --------------------------------------------------------


def test_synthetic_fixture_constructs_successfully():
    bundle = SYNTHETIC_BLI_MVP_INPUT_BUNDLE
    assert bundle.resolution_status is BondResolutionStatus.FOUND_ELIGIBLE
    assert bundle.resolved_bond_reference_data.isin == _ELIGIBLE_ISIN
    assert bundle.product.bond_option.underlying_isin == _ELIGIBLE_ISIN
    assert bundle.market_data_snapshot.bond_quote.isin == _ELIGIBLE_ISIN


def test_synthetic_product_fixture_isin_matches_other_synthetic_fixtures():
    # Guards against the exact regression docs/24 found: the product
    # fixture's ISIN must line up with the reference-data and market-data
    # fixtures, or a combined positive bundle fixture cannot exist.
    assert SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT.bond_option.underlying_isin == _ELIGIBLE_ISIN


def test_bundle_helper_constructs_successfully():
    bundle = _bundle()
    assert bundle.bundle_id == "TEST-BUNDLE-0001"
    assert bundle.valuation_date == "2026-07-01"


def test_bundle_is_frozen():
    bundle = _bundle()
    with pytest.raises(AttributeError):
        bundle.bundle_id = "OTHER"


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_bundle_id_rejected(value):
    with pytest.raises(ValueError, match="bundle_id must be a non-blank string"):
        _bundle(bundle_id=value)


# --- 2. Product / component type gates -----------------------------------


def test_product_wrong_type_rejected():
    with pytest.raises(TypeError, match="product must be a BondLinkedStructuredProduct"):
        _bundle(product=_bond_option(), resolution=resolve_bond_reference_data(_ELIGIBLE_ISIN))


def test_resolved_bond_reference_data_wrong_type_rejected():
    with pytest.raises(TypeError, match="resolved_bond_reference_data must be a BondReferenceData"):
        _bundle(resolved_bond_reference_data=object())


def test_market_data_snapshot_wrong_type_rejected():
    with pytest.raises(TypeError, match="market_data_snapshot must be a BLIMarketDataSnapshot"):
        _bundle(market_data_snapshot=object())


# --- 3. ISIN gates ---------------------------------------------------------


def test_product_reference_isin_mismatch_rejected():
    mismatched_product = _product(bond_option=_bond_option(underlying_isin="XS9999999999"))
    resolution = resolve_bond_reference_data(_ELIGIBLE_ISIN)
    with pytest.raises(ValueError, match="does not exactly match"):
        _bundle(product=mismatched_product, resolution=resolution)


def test_market_data_reference_isin_mismatch_rejected():
    mismatched_snapshot = _snapshot(bond_quote=_bond_quote(isin="XS9999999999"))
    with pytest.raises(ValueError, match="does not exactly match"):
        _bundle(market_data_snapshot=mismatched_snapshot)


def test_no_fuzzy_or_prefix_isin_matching():
    # A prefix/near-match ISIN must still be rejected -- exact string
    # equality only (docs/21 §4, docs/24 §6).
    mismatched_product = _product(bond_option=_bond_option(underlying_isin="XS00000000"))
    resolution = resolve_bond_reference_data(_ELIGIBLE_ISIN)
    with pytest.raises(ValueError, match="does not exactly match"):
        _bundle(product=mismatched_product, resolution=resolution)


# --- 4. Reference-data eligibility gates -----------------------------------


def test_ineligible_reference_data_rejected():
    ineligible_product = _product(
        bond_option=_bond_option(underlying_isin=_CALLABLE_INELIGIBLE_ISIN)
    )
    resolution = resolve_bond_reference_data(_CALLABLE_INELIGIBLE_ISIN)
    assert resolution.status is BondResolutionStatus.FOUND_INELIGIBLE
    with pytest.raises(ValueError, match="resolution_status must be FOUND_ELIGIBLE"):
        _bundle(product=ineligible_product, resolution=resolution)


def test_non_found_eligible_resolution_status_rejected():
    # Isolates the "resolution_status must equal FOUND_ELIGIBLE" gate on
    # its own, independent of whichever bond_reference_data happens to be
    # attached (a NOT_FOUND resolution naturally has no bond_reference_data
    # at all -- see test_not_found_resolution_has_no_reference_data_to_bind
    # below for why that case fails at the type gate instead). The
    # ``resolution`` default is discarded here since resolution_status /
    # eligibility_reasons / resolved_bond_reference_data are all overridden
    # directly.
    eligible_bond = resolve_bond_reference_data(_ELIGIBLE_ISIN).bond_reference_data
    with pytest.raises(ValueError, match="resolution_status must be FOUND_ELIGIBLE"):
        _bundle(
            resolved_bond_reference_data=eligible_bond,
            resolution_status=BondResolutionStatus.NOT_FOUND,
            eligibility_reasons=(),
        )


def test_not_found_resolution_has_no_reference_data_to_bind():
    resolution = resolve_bond_reference_data("XS_DOES_NOT_EXIST")
    assert resolution.status is BondResolutionStatus.NOT_FOUND
    assert resolution.bond_reference_data is None
    with pytest.raises(TypeError, match="resolved_bond_reference_data must be a BondReferenceData"):
        _bundle(resolution=resolution)


def test_eligibility_reasons_must_be_empty_when_found_eligible():
    with pytest.raises(ValueError, match="eligibility_reasons must be empty"):
        _bundle(eligibility_reasons=("some stale reason",))


def test_stale_found_eligible_status_cannot_override_actually_ineligible_bond():
    # Codex P1 review of PR #65: resolution_status/eligibility_reasons are
    # caller-supplied metadata, not re-derived by the bundle -- a stale or
    # hand-assembled resolver result could claim FOUND_ELIGIBLE for a bond
    # that is not actually MVP-pricing eligible (here, a real callable
    # fixture bond). The bundle must independently re-verify eligibility
    # from resolved_bond_reference_data itself via is_mvp_pricing_eligible,
    # never trust the supplied status/reasons alone.
    callable_bond = resolve_bond_reference_data(_CALLABLE_INELIGIBLE_ISIN).bond_reference_data
    assert not is_mvp_pricing_eligible(callable_bond).eligible
    callable_product = _product(bond_option=_bond_option(underlying_isin=_CALLABLE_INELIGIBLE_ISIN))
    with pytest.raises(ValueError, match="is not actually MVP-pricing eligible"):
        _bundle(
            product=callable_product,
            resolved_bond_reference_data=callable_bond,
            resolution_status=BondResolutionStatus.FOUND_ELIGIBLE,
            eligibility_reasons=(),
        )


# --- 5. Valuation-date gates ------------------------------------------------


def test_snapshot_valuation_date_mismatch_rejected():
    mismatched_snapshot = _snapshot(valuation_date="2026-07-02")
    with pytest.raises(ValueError, match="must equal bundle"):
        _bundle(market_data_snapshot=mismatched_snapshot, valuation_date="2026-07-01")


# --- 6. Market-data as-of / no-look-ahead gates -----------------------------


def test_as_of_timestamp_after_valuation_date_rejected():
    look_ahead_snapshot = _snapshot(as_of_timestamp="2026-07-02T00:00:01Z")
    with pytest.raises(ValueError, match="no-look-ahead"):
        _bundle(market_data_snapshot=look_ahead_snapshot)


def test_as_of_timestamp_same_calendar_date_passes():
    same_date_snapshot = _snapshot(as_of_timestamp="2026-07-01T23:59:59Z")
    bundle = _bundle(market_data_snapshot=same_date_snapshot)
    assert bundle.market_data_snapshot.as_of_timestamp == "2026-07-01T23:59:59Z"


def test_as_of_timestamp_earlier_than_valuation_date_passes():
    earlier_snapshot = _snapshot(as_of_timestamp="2026-06-30T10:00:00Z")
    bundle = _bundle(market_data_snapshot=earlier_snapshot)
    assert bundle.market_data_snapshot.as_of_timestamp == "2026-06-30T10:00:00Z"


def test_unparseable_as_of_timestamp_rejected():
    unparseable_snapshot = _snapshot(as_of_timestamp="not-a-timestamp")
    with pytest.raises(ValueError, match="must be an ISO-8601 date"):
        _bundle(market_data_snapshot=unparseable_snapshot)


def test_as_of_timestamp_explicit_utc_offset_same_date_passes():
    # "+00:00" is UTC, exactly like "Z" -- must be accepted, not rejected as
    # a "non-UTC offset".
    utc_offset_snapshot = _snapshot(as_of_timestamp="2026-07-01T16:00:00+00:00")
    bundle = _bundle(market_data_snapshot=utc_offset_snapshot)
    assert bundle.market_data_snapshot.as_of_timestamp == "2026-07-01T16:00:00+00:00"


def test_as_of_timestamp_naive_datetime_passes():
    naive_snapshot = _snapshot(as_of_timestamp="2026-07-01T16:00:00")
    bundle = _bundle(market_data_snapshot=naive_snapshot)
    assert bundle.market_data_snapshot.as_of_timestamp == "2026-07-01T16:00:00"


def test_as_of_timestamp_non_utc_negative_offset_rejected():
    # 2026-07-01T23:30:00-05:00 is already 2026-07-02 in UTC -- accepting
    # its *local* calendar date (2026-07-01) would silently violate the
    # no-look-ahead policy for a valuation_date of 2026-07-01.
    non_utc_snapshot = _snapshot(as_of_timestamp="2026-07-01T23:30:00-05:00")
    with pytest.raises(ValueError, match="non-UTC timezone offsets are not supported"):
        _bundle(market_data_snapshot=non_utc_snapshot)


def test_as_of_timestamp_non_utc_positive_offset_rejected():
    non_utc_snapshot = _snapshot(as_of_timestamp="2026-07-02T08:00:00+08:00")
    with pytest.raises(ValueError, match="non-UTC timezone offsets are not supported"):
        _bundle(market_data_snapshot=non_utc_snapshot)


# --- 7. Product-specific deposit-rate market-data gates ---------------------


def test_treasury_ftp_reference_missing_deposit_rate_observation_rejected():
    snapshot_without_ftp = _snapshot(deposit_rate_observation=None)
    with pytest.raises(ValueError, match="deposit_rate_observation is required"):
        _bundle(market_data_snapshot=snapshot_without_ftp)


def test_fixed_rate_deposit_leg_does_not_require_deposit_rate_observation():
    fixed_rate_product = _product(deposit_leg=_fixed_rate_deposit_leg())
    snapshot_without_ftp = _snapshot(deposit_rate_observation=None)
    bundle = _bundle(product=fixed_rate_product, market_data_snapshot=snapshot_without_ftp)
    assert bundle.market_data_snapshot.deposit_rate_observation is None


def test_treasury_ftp_reference_currency_mismatch_rejected():
    mismatched_snapshot = _snapshot(
        deposit_rate_observation=_deposit_rate_observation(currency=Currency.EUR)
    )
    with pytest.raises(ValueError, match="deposit_rate_observation.currency"):
        _bundle(market_data_snapshot=mismatched_snapshot)


def test_treasury_ftp_reference_tenor_mismatch_rejected():
    mismatched_snapshot = _snapshot(
        deposit_rate_observation=_deposit_rate_observation(tenor=TreasuryFTPTenor.SIX_MONTH)
    )
    with pytest.raises(ValueError, match="deposit_rate_observation.tenor"):
        _bundle(market_data_snapshot=mismatched_snapshot)


def test_treasury_ftp_reference_quote_side_mismatch_rejected():
    mismatched_snapshot = _snapshot(
        deposit_rate_observation=_deposit_rate_observation(quote_side=TreasuryFTPQuoteSide.BID)
    )
    with pytest.raises(ValueError, match="deposit_rate_observation.quote_side"):
        _bundle(market_data_snapshot=mismatched_snapshot)


def test_manual_verified_rate_mode_rejected():
    manual_product = _product(deposit_leg=_manual_verified_deposit_leg())
    with pytest.raises(ValueError, match="MANUAL_VERIFIED_RATE is not supported"):
        _bundle(product=manual_product)


# --- 8. Required MVP curve-purpose gates ------------------------------------


def test_missing_required_curve_purpose_rejected():
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
        _bundle(market_data_snapshot=missing_deposit_curve_snapshot)


def test_wrong_purpose_only_curve_points_rejected():
    funding_only_snapshot = _snapshot(
        curve_points=(
            _curve_point(curve_id="USD_FUNDING_CURVE", curve_purpose=BLICurvePurpose.FUNDING_CURVE),
        )
    )
    with pytest.raises(ValueError, match="missing required curve purpose"):
        _bundle(market_data_snapshot=funding_only_snapshot)


def test_all_required_curve_purposes_present_passes():
    bundle = _bundle(market_data_snapshot=_snapshot(curve_points=_all_required_curve_points()))
    present_purposes = {point.curve_purpose for point in bundle.market_data_snapshot.curve_points}
    assert BLICurvePurpose.BOND_REFERENCE_CURVE in present_purposes
    assert BLICurvePurpose.OPTION_DISCOUNT_CURVE in present_purposes
    assert BLICurvePurpose.DEPOSIT_CURVE in present_purposes


def test_funding_curve_not_required():
    # FUNDING_CURVE is only required if a future mapping calls for it
    # (docs/24 §6) -- absence alongside the three required purposes must
    # still pass.
    bundle = _bundle(market_data_snapshot=_snapshot(curve_points=_all_required_curve_points()))
    present_purposes = {point.curve_purpose for point in bundle.market_data_snapshot.curve_points}
    assert BLICurvePurpose.FUNDING_CURVE not in present_purposes


# --- 9. Currency coherence gates ---------------------------------------------


def test_product_currency_mismatch_vs_reference_data_rejected():
    # The eligible fixture bond (XS0000000001) is USD; build an otherwise
    # internally-consistent EUR product referencing the same ISIN.
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
        _bundle(product=eur_product)


def test_market_data_quote_currency_mismatch_rejected():
    eur_quote_snapshot = _snapshot(bond_quote=_bond_quote(currency=Currency.EUR))
    with pytest.raises(ValueError, match="bond_quote.currency .* does not match"):
        _bundle(market_data_snapshot=eur_quote_snapshot)


def test_required_curve_purpose_currency_mismatch_rejected():
    # All three required purposes are present, but only in EUR -- the
    # product/reference data are USD, so this must still be rejected as
    # "missing" for the product's actual currency, not silently accepted
    # because *some* row of each purpose exists.
    eur_curve_points = tuple(
        _curve_point(
            curve_id=f"EUR_{point.curve_purpose.value}",
            curve_purpose=point.curve_purpose,
            currency=Currency.EUR,
        )
        for point in _all_required_curve_points()
    )
    eur_curve_snapshot = _snapshot(curve_points=eur_curve_points)
    with pytest.raises(ValueError, match="missing required curve purpose.*currency USD"):
        _bundle(market_data_snapshot=eur_curve_snapshot)


def test_synthetic_bundle_fixture_still_passes_currency_gates():
    bundle = SYNTHETIC_BLI_MVP_INPUT_BUNDLE
    assert bundle.product.bond_option.currency is Currency.USD
    assert bundle.resolved_bond_reference_data.currency is Currency.USD
    assert bundle.market_data_snapshot.bond_quote.currency is Currency.USD


# --- 10. Scope: no pricing engine, no builder, no conversion -----------------


def test_bundle_has_no_pricing_or_duplicated_fields():
    field_names = {f.name for f in fields(BLIMVPInputBundle)}
    forbidden = {
        "pv",
        "dv01",
        "cashflows",
        "customer_return",
        "bank_margin",
        "option_premium",
        "payoff_amount",
        "clean_price_per_100",
        "yield_value",
        "curve_rate",
        "volatility",
        "credit_spread",
        "discount_factor",
        "interpolated_rate",
    }
    assert field_names.isdisjoint(forbidden)


def test_bundle_both_price_and_yield_pass_through_unconverted():
    # A bond quote may carry both an observed price and an observed
    # yield (docs/23's Codex-P2 fix); the bundle must not compute or
    # store a converted/derived value from either.
    both_snapshot = _snapshot(
        bond_quote=_bond_quote(clean_price_per_100=101.25, yield_value=0.041)
    )
    bundle = _bundle(market_data_snapshot=both_snapshot)
    assert bundle.market_data_snapshot.bond_quote.clean_price_per_100 == 101.25
    assert bundle.market_data_snapshot.bond_quote.yield_value == 0.041


def test_module_defines_no_bundle_builder():
    module_names = set(dir(bli_mvp_input_bundle_module))
    forbidden_builder_names = {
        "build_bundle",
        "build",
        "builder",
        "BundleBuilder",
        "construct_bundle",
    }
    assert module_names.isdisjoint(forbidden_builder_names)


def test_bundle_class_has_no_builder_classmethod():
    assert not hasattr(BLIMVPInputBundle, "build")
    assert not hasattr(BLIMVPInputBundle, "from_resolution")
