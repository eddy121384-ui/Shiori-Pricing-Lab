"""Tests for the BLI MVP required-input guard (Issue #41, MVP-5 under #82).

Scope: this is a checklist-boundary test suite for
``check_bli_mvp_required_inputs``, not a pricing-methodology test suite --
there is no forward clean price, Black-76, or any other pricing math to pin
down here (see the guard module's own docstring for the full non-goals
list).

**Acceptance-criteria-to-test/citation mapping (Issue #41):**

- happy path (supported MVP case):
  ``test_supported_case_returns_no_reasons``.
- unsupported exercise style: ``test_american_exercise_rejected``.
- unsupported payoff basis: ``test_yield_payoff_basis_rejected``.
- unsupported settlement (physical): **not reachable here** --
  ``BondLinkedStructuredProduct.__post_init__`` already rejects a
  PHYSICAL-settled ``bond_option`` before a bundle can exist; see
  ``tests/test_bond_linked_structured_product.py::
  test_wrapper_rejects_physical_settlement`` (existing, cited, not
  duplicated here).
- missing ``clean_price_per_100`` (``yield_value`` present instead):
  ``test_missing_clean_price_with_yield_value_present_rejected``.
- unsupported volatility basis ``YIELD_VOL``: ``test_yield_vol_rejected``.
- volatility basis ``EQUIVALENT_PRICE_VOL``: **not rejected** --
  explicitly approved as supported per docs/26 §2.1 (no conversion
  required); see ``test_equivalent_price_vol_accepted`` instead of a
  rejection test.
- missing ``volatility_input`` (no vol object at all, distinct from an
  unsupported ``volatility_basis`` on a *present* vol object): **not
  reachable here** -- ``BLIMarketDataSnapshot.__post_init__`` already
  rejects a ``None``/wrong-type ``volatility_input`` before a
  ``BLIMVPInputBundle`` can exist; see
  ``test_missing_volatility_input_cannot_reach_guard``.
- missing required curve (Bond Reference / Option Discount): **not
  reachable here** -- ``BLIMVPInputBundle.__post_init__`` already
  requires both in the product's currency before a bundle can exist; see
  the existing curve-purpose-presence tests in
  ``tests/test_bli_mvp_input_bundle.py`` (cited, not duplicated here).
- missing bond reference data / wrong type: **not reachable here** --
  ``BLIMVPInputBundle.__post_init__`` already type-checks
  ``resolved_bond_reference_data`` before a bundle can exist; see the
  existing ``TypeError`` tests in ``tests/test_bli_mvp_input_bundle.py``
  (cited, not duplicated here).
- missing valuation date / expiry / strike / notional: **not reachable
  here** -- all are required, validated, non-``None`` fields already
  enforced by ``BondOption.__post_init__`` / ``BLIMVPInputBundle.
  __post_init__`` before either object can exist; see
  ``tests/test_bond_option.py`` and ``tests/test_bli_mvp_input_bundle.py``
  (existing tests, cited, not duplicated here).
- combined reachable failures collect all reasons:
  ``test_combined_failures_collect_all_reasons``.
- wrong input type raises ``TypeError``:
  ``test_wrong_input_type_raises_type_error``.
- pure-function / no mutation: ``test_does_not_mutate_bundle``.

No fabricated frozen-dataclass states (e.g. ``object.__setattr__`` bypassing
``__post_init__``) are used anywhere in this file: every "not reachable"
row above is proven by an existing, already-passing test elsewhere in this
repo, not asserted here without evidence.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from shiori_pricing_lab.data.bli_mvp_input_bundle import BLIMVPInputBundle
from shiori_pricing_lab.data.bli_mvp_input_bundle_fixtures import SYNTHETIC_BLI_MVP_INPUT_BUNDLE
from shiori_pricing_lab.data.bli_snapshot import BLIVolatilityBasis
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.pricing.bli_mvp_required_input_guard import (
    RequiredInputGuardResult,
    check_bli_mvp_required_inputs,
)
from shiori_pricing_lab.products.enums import ExerciseStyle, PayoffBasis
from shiori_pricing_lab.products.fixtures import SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT

# --- Happy path --------------------------------------------------------------


def test_supported_case_returns_no_reasons():
    result = check_bli_mvp_required_inputs(SYNTHETIC_BLI_MVP_INPUT_BUNDLE)
    assert isinstance(result, RequiredInputGuardResult)
    assert result.supported is True
    assert result.reasons == ()


# --- Unsupported product shape: exercise style / payoff basis ---------------


def test_american_exercise_rejected():
    bond_option = SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option
    american_option = replace(
        bond_option,
        exercise_style=ExerciseStyle.AMERICAN,
        exercise_start_date="2026-06-01",
    )
    product = replace(SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT, bond_option=american_option)
    bundle = replace(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, product=product)

    result = check_bli_mvp_required_inputs(bundle)

    assert result.supported is False
    assert any("exercise_style" in reason for reason in result.reasons)


def test_yield_payoff_basis_rejected():
    bond_option = SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option
    yield_option = replace(
        bond_option,
        payoff_basis=PayoffBasis.YIELD,
        strike_price=None,
        strike_yield=0.035,
    )
    product = replace(SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT, bond_option=yield_option)
    bundle = replace(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, product=product)

    result = check_bli_mvp_required_inputs(bundle)

    assert result.supported is False
    assert any("payoff_basis" in reason for reason in result.reasons)


# --- Missing clean price ------------------------------------------------------


def test_missing_clean_price_with_yield_value_present_rejected():
    yield_only_quote = replace(
        SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.bond_quote,
        clean_price_per_100=None,
        yield_value=0.035,
    )
    snapshot = replace(SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT, bond_quote=yield_only_quote)
    bundle = replace(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, market_data_snapshot=snapshot)

    result = check_bli_mvp_required_inputs(bundle)

    assert result.supported is False
    assert any("clean_price_per_100" in reason for reason in result.reasons)


# --- Volatility basis ---------------------------------------------------------


def test_yield_vol_rejected():
    yield_vol_input = replace(
        SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.volatility_input,
        volatility_basis=BLIVolatilityBasis.YIELD_VOL,
    )
    snapshot = replace(SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT, volatility_input=yield_vol_input)
    bundle = replace(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, market_data_snapshot=snapshot)

    result = check_bli_mvp_required_inputs(bundle)

    assert result.supported is False
    assert any("volatility_basis" in reason for reason in result.reasons)


def test_equivalent_price_vol_accepted():
    # docs/26 SS2.1: an EQUIVALENT_PRICE_VOL value is, by definition,
    # already in the right units if one has already been supplied
    # upstream -- no conversion is performed or implied here.
    equivalent_vol_input = replace(
        SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.volatility_input,
        volatility_basis=BLIVolatilityBasis.EQUIVALENT_PRICE_VOL,
    )
    snapshot = replace(SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT, volatility_input=equivalent_vol_input)
    bundle = replace(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, market_data_snapshot=snapshot)

    result = check_bli_mvp_required_inputs(bundle)

    assert result.supported is True
    assert result.reasons == ()


def test_price_vol_accepted():
    assert (
        SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.volatility_input.volatility_basis
        is BLIVolatilityBasis.PRICE_VOL
    )
    result = check_bli_mvp_required_inputs(SYNTHETIC_BLI_MVP_INPUT_BUNDLE)
    assert result.supported is True


def test_missing_volatility_input_cannot_reach_guard():
    # Distinct from test_yield_vol_rejected: this proves a missing vol
    # *object* (not merely an unsupported basis on a present object)
    # cannot reach the guard at all -- BLIMarketDataSnapshot.__post_init__
    # already rejects it, so no BLIMVPInputBundle carrying it can exist
    # for check_bli_mvp_required_inputs to be called on.
    with pytest.raises(TypeError, match="volatility_input"):
        replace(SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT, volatility_input=None)


# --- Combined reachable failures ---------------------------------------------


def test_combined_failures_collect_all_reasons():
    bond_option = SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option
    american_yield_option = replace(
        bond_option,
        exercise_style=ExerciseStyle.AMERICAN,
        exercise_start_date="2026-06-01",
        payoff_basis=PayoffBasis.YIELD,
        strike_price=None,
        strike_yield=0.035,
    )
    product = replace(
        SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT, bond_option=american_yield_option
    )

    yield_vol_input = replace(
        SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.volatility_input,
        volatility_basis=BLIVolatilityBasis.YIELD_VOL,
    )
    snapshot = replace(SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT, volatility_input=yield_vol_input)

    bundle = replace(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, product=product, market_data_snapshot=snapshot)

    result = check_bli_mvp_required_inputs(bundle)

    assert result.supported is False
    assert len(result.reasons) >= 3
    assert any("exercise_style" in reason for reason in result.reasons)
    assert any("payoff_basis" in reason for reason in result.reasons)
    assert any("volatility_basis" in reason for reason in result.reasons)


# --- Wrong input type ---------------------------------------------------------


def test_wrong_input_type_raises_type_error():
    with pytest.raises(TypeError, match="BLIMVPInputBundle"):
        check_bli_mvp_required_inputs(SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT)


def test_none_input_raises_type_error():
    with pytest.raises(TypeError, match="BLIMVPInputBundle"):
        check_bli_mvp_required_inputs(None)


# --- Pure function / no mutation ----------------------------------------------


def test_does_not_mutate_bundle():
    bundle = SYNTHETIC_BLI_MVP_INPUT_BUNDLE
    before_bundle_id = bundle.bundle_id
    before_product = bundle.product
    before_snapshot = bundle.market_data_snapshot

    check_bli_mvp_required_inputs(bundle)

    assert bundle.bundle_id == before_bundle_id
    assert bundle.product is before_product
    assert bundle.market_data_snapshot is before_snapshot


def test_result_is_deterministic_across_repeated_calls():
    first = check_bli_mvp_required_inputs(SYNTHETIC_BLI_MVP_INPUT_BUNDLE)
    second = check_bli_mvp_required_inputs(SYNTHETIC_BLI_MVP_INPUT_BUNDLE)
    assert first == second


# --- Scope boundary: no pricing/interpolation/schedule/connector -------------


def test_module_defines_no_valuation_or_conversion_logic():
    from shiori_pricing_lab.pricing import bli_mvp_required_input_guard as guard_module

    module_names = set(dir(guard_module))
    forbidden_names = {
        "compute_pv",
        "forward_clean_price",
        "black_76",
        "black76",
        "interpolate",
        "interpolate_curve",
        "convert_yield_to_price_vol",
        "generate_cashflows",
        "build_schedule",
        "price_bli_mvp",
    }
    assert module_names.isdisjoint(forbidden_names)


def test_bundle_is_still_the_bli_mvp_input_bundle_type():
    assert isinstance(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, BLIMVPInputBundle)


# --- Standalone OVME-aligned guard (Issue #94) -------------------------------
#
# check_bli_mvp_standalone_option_required_inputs shares the product-shape and
# volatility-basis checks with the bundle guard but differs where the OVME
# methodology differs: it requires an explicit forward_clean_price_input,
# requires only OPTION_DISCOUNT_CURVE (no BOND_REFERENCE_CURVE), and allows a
# yield-only spot bond_quote.

from shiori_pricing_lab.data.bli_snapshot import (  # noqa: E402
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIForwardCleanPriceInput,
    BLIMarketDataStatus,
)
from shiori_pricing_lab.data.bli_standalone_option_request import (  # noqa: E402
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.pricing.bli_mvp_required_input_guard import (  # noqa: E402
    check_bli_mvp_standalone_option_required_inputs,
)
from shiori_pricing_lab.products.enums import TreasuryFTPQuoteSide  # noqa: E402

_STANDALONE_FORWARD_INPUT = BLIForwardCleanPriceInput(
    forward_clean_price_per_100=101.30,
    quote_side=TreasuryFTPQuoteSide.MID,
    source_system="TEST_LOCAL_FORWARD_FEED",
    status=BLIMarketDataStatus.ACTIVE,
)


def _standalone_option_discount_curve():
    common = {
        "currency": SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option.currency,
        "rate_basis": BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
        "source_system": "TEST_LOCAL_CURVE",
        "status": BLIMarketDataStatus.ACTIVE,
    }
    return tuple(
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


def _standalone_snapshot(**overrides):
    params = dict(
        snapshot_id="TEST_LOCAL_STANDALONE_SNAPSHOT",
        source_system="TEST_LOCAL_CURVE",
        curve_points=_standalone_option_discount_curve(),
        forward_clean_price_input=_STANDALONE_FORWARD_INPUT,
    )
    params.update(overrides)
    return replace(SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT, **params)


def _standalone_request(**overrides) -> BLIStandaloneBondOptionRequest:
    params = dict(
        bond_option=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        resolved_bond_reference_data=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.resolved_bond_reference_data,
        valuation_date=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.valuation_date,
        market_data_snapshot=_standalone_snapshot(),
        pricing_timestamp="2026-07-01T16:00:00Z",
        expiry_timestamp="2026-09-29T16:00:00Z",
        reporting_date="2026-07-01",
        forward_settlement_date="2026-10-01",
        option_settlement_date="2026-10-02",
    )
    params.update(overrides)
    return BLIStandaloneBondOptionRequest(**params)


def test_standalone_guard_supported_case_returns_no_reasons():
    # Only an Option Discount Curve (no Bond Reference Curve) + explicit
    # forward input: the standalone checklist accepts it.
    result = check_bli_mvp_standalone_option_required_inputs(_standalone_request())
    assert isinstance(result, RequiredInputGuardResult)
    assert result.supported is True
    assert result.reasons == ()


def test_standalone_guard_allows_yield_only_spot_quote():
    # The forward is explicit, so a yield-only spot bond_quote is supported.
    yield_only_snapshot = _standalone_snapshot(
        bond_quote=replace(
            SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.bond_quote,
            clean_price_per_100=None,
            yield_value=0.035,
        )
    )
    request = _standalone_request(market_data_snapshot=yield_only_snapshot)
    result = check_bli_mvp_standalone_option_required_inputs(request)
    assert result.supported is True
    assert result.reasons == ()


def test_standalone_guard_rejects_american_exercise():
    american_option = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        exercise_style=ExerciseStyle.AMERICAN,
        exercise_start_date="2026-06-01",
    )
    result = check_bli_mvp_standalone_option_required_inputs(
        _standalone_request(bond_option=american_option)
    )
    assert result.supported is False
    assert any("exercise_style" in reason for reason in result.reasons)


def test_standalone_guard_rejects_yield_vol():
    yield_vol_snapshot = _standalone_snapshot(
        volatility_input=replace(
            SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.volatility_input,
            volatility_basis=BLIVolatilityBasis.YIELD_VOL,
        ),
    )
    result = check_bli_mvp_standalone_option_required_inputs(
        _standalone_request(market_data_snapshot=yield_vol_snapshot)
    )
    assert result.supported is False
    assert any("volatility_basis" in reason for reason in result.reasons)


def test_standalone_guard_rejects_wrong_input_type():
    with pytest.raises(TypeError, match="BLIStandaloneBondOptionRequest"):
        check_bli_mvp_standalone_option_required_inputs(SYNTHETIC_BLI_MVP_INPUT_BUNDLE)
