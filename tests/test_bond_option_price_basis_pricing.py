"""End-to-end ``BOND_OPTION_PRICE_BASIS`` wiring through the standalone path
(Issue #214).

Issue #211 / PR #212 approved ``DIRTY``/``CLEAN`` and made the duration and
Equivalent-Price-Vol producers basis-aware, but the runtime priced ``DIRTY``
only. What this file pins is the wiring that closed that gap, and above all
the two things it must never become:

* a second Black-76 -- both bases reach the same shared core, so on identical
  ``F``/``K``/sigma/T/DF numbers the two representations agree exactly;
* a way to mix bases -- one selected basis drives ``F``, ``K``, the duration
  denominator, sigma_P and the wrapper, and any disagreement is refused.

``DIRTY`` remains the default, so the Issue #94 OVME-aligned composition is
what an unset selection still prices; that is pinned here too rather than
left to the older files to imply.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from shiori_pricing_lab.data.bli_mvp_input_bundle_fixtures import (
    SYNTHETIC_BLI_MVP_INPUT_BUNDLE,
)
from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIForwardCleanPriceInput,
    BLIMarketDataStatus,
)
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.pricing.bli_black76_price_option import (
    black76_clean_price_option_greeks_per_100,
    black76_dirty_price_option_greeks_per_100,
    black76_dirty_price_option_pv_per_100,
    black76_price_option_pv_per_100,
)
from shiori_pricing_lab.pricing.bli_bond_option_price_basis import (
    DEFAULT_BOND_OPTION_PRICE_BASIS,
    BondOptionPriceBasis,
)
from shiori_pricing_lab.pricing.bli_pricing_engine import price_bli_mvp_standalone_option
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available
from shiori_pricing_lab.pricing.bli_standalone_option_pricing_inputs import (
    resolve_standalone_option_pricing_inputs,
)
from shiori_pricing_lab.pricing.result import PricingStatus
from shiori_pricing_lab.products.enums import TreasuryFTPQuoteSide

_requires_quantlib = pytest.mark.skipif(
    not is_quantlib_available(), reason="QuantLib is not installed in this environment"
)

_PRICING_TIMESTAMP = "2026-07-01T16:00:00Z"
_EXPIRY_TIMESTAMP = "2026-09-29T16:00:00Z"
_REPORTING_DATE = "2026-07-01"
_FORWARD_SETTLEMENT_DATE = "2026-10-01"
_OPTION_SETTLEMENT_DATE = "2026-10-02"

_FORWARD_INPUT = BLIForwardCleanPriceInput(
    forward_clean_price_per_100=101.30,
    quote_side=TreasuryFTPQuoteSide.MID,
    source_system="TEST_LOCAL_FORWARD_FEED",
    status=BLIMarketDataStatus.ACTIVE,
)


def _curve(currency) -> tuple[BLICurvePoint, ...]:
    common = {
        "currency": currency,
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


def _request() -> BLIStandaloneBondOptionRequest:
    return BLIStandaloneBondOptionRequest(
        bond_option=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        resolved_bond_reference_data=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.resolved_bond_reference_data,
        valuation_date=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.valuation_date,
        market_data_snapshot=replace(
            SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT,
            snapshot_id="TEST_LOCAL_SUPPORTED_SNAPSHOT",
            source_system="TEST_LOCAL_CURVE",
            curve_points=_curve(
                SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option.currency
            ),
            forward_clean_price_input=_FORWARD_INPUT,
        ),
        pricing_timestamp=_PRICING_TIMESTAMP,
        expiry_timestamp=_EXPIRY_TIMESTAMP,
        reporting_date=_REPORTING_DATE,
        forward_settlement_date=_FORWARD_SETTLEMENT_DATE,
        option_settlement_date=_OPTION_SETTLEMENT_DATE,
    )


# --- The default is DIRTY, and it is the unchanged Issue #94 composition -----


def test_the_default_basis_is_dirty():
    assert DEFAULT_BOND_OPTION_PRICE_BASIS is BondOptionPriceBasis.DIRTY


@_requires_quantlib
def test_not_selecting_a_basis_prices_exactly_what_selecting_dirty_prices():
    implicit = price_bli_mvp_standalone_option(_request())
    explicit = price_bli_mvp_standalone_option(
        _request(), price_basis=BondOptionPriceBasis.DIRTY
    )

    assert implicit.status is PricingStatus.SUCCESS
    assert implicit.pv == explicit.pv
    assert implicit.method == explicit.method == "black76_forward_dirty_price_ovme_v1"
    assert implicit.assumptions == explicit.assumptions
    assert implicit.assumptions["bond_option_price_basis"] == "DIRTY"


# --- One basis drives F, K and the wrapper together --------------------------


@_requires_quantlib
@pytest.mark.parametrize("basis", list(BondOptionPriceBasis))
def test_the_model_forward_and_strike_are_the_pair_the_basis_names(basis):
    request = _request()
    inputs = resolve_standalone_option_pricing_inputs(request, price_basis=basis)

    assert inputs.price_basis is basis
    if basis is BondOptionPriceBasis.CLEAN:
        assert inputs.model_forward_price_per_100 == inputs.forward_clean_price_per_100
        assert inputs.model_strike_price_per_100 == inputs.strike_clean_price_per_100
    else:
        assert inputs.model_forward_price_per_100 == inputs.forward_dirty_price_per_100
        assert inputs.model_strike_price_per_100 == inputs.strike_dirty_price_per_100

    # Both pairs stay available whichever basis was selected, so a reviewer
    # can see what the other basis would have priced.
    assert inputs.forward_dirty_price_per_100 == (
        inputs.forward_clean_price_per_100
        + inputs.accrued_interest_at_forward_settlement_per_100
    )


@_requires_quantlib
def test_the_basis_independent_inputs_do_not_move_with_the_basis():
    # Accrued interest, option time and all three discount factors are
    # basis-independent; only F and K are selected.
    dirty = resolve_standalone_option_pricing_inputs(
        _request(), price_basis=BondOptionPriceBasis.DIRTY
    )
    clean = resolve_standalone_option_pricing_inputs(
        _request(), price_basis=BondOptionPriceBasis.CLEAN
    )

    for field in (
        "accrued_interest_at_forward_settlement_per_100",
        "time_to_expiry_year_fraction",
        "pricing_to_reporting_discount_factor",
        "pricing_to_option_settlement_discount_factor",
        "effective_reporting_date_discount_factor",
        "forward_clean_price_per_100",
        "strike_clean_price_per_100",
        "forward_dirty_price_per_100",
        "strike_dirty_price_per_100",
    ):
        assert getattr(dirty, field) == getattr(clean, field)


@_requires_quantlib
@pytest.mark.parametrize("basis", list(BondOptionPriceBasis))
def test_the_priced_premium_is_the_existing_wrapper_on_the_selected_pair(basis):
    # No new pricing math: the engine's answer is exactly what the already
    # reviewed wrapper for that basis returns on the resolved inputs.
    request = _request()
    inputs = resolve_standalone_option_pricing_inputs(request, price_basis=basis)
    volatility = request.market_data_snapshot.volatility_input.volatility

    if basis is BondOptionPriceBasis.CLEAN:
        expected_pv = black76_price_option_pv_per_100(
            forward_clean_price=inputs.model_forward_price_per_100,
            strike_clean_price=inputs.model_strike_price_per_100,
            price_volatility=volatility,
            time_to_expiry=inputs.time_to_expiry_year_fraction,
            discount_factor=inputs.effective_reporting_date_discount_factor,
            option_type=request.bond_option.option_type,
        )
        expected_greeks = black76_clean_price_option_greeks_per_100(
            forward_clean_price=inputs.model_forward_price_per_100,
            strike_clean_price=inputs.model_strike_price_per_100,
            price_volatility=volatility,
            time_to_expiry=inputs.time_to_expiry_year_fraction,
            discount_factor=inputs.effective_reporting_date_discount_factor,
            option_type=request.bond_option.option_type,
        )
    else:
        expected_pv = black76_dirty_price_option_pv_per_100(
            forward_dirty_price=inputs.model_forward_price_per_100,
            strike_dirty_price=inputs.model_strike_price_per_100,
            price_volatility=volatility,
            time_to_expiry=inputs.time_to_expiry_year_fraction,
            discount_factor=inputs.effective_reporting_date_discount_factor,
            option_type=request.bond_option.option_type,
        )
        expected_greeks = black76_dirty_price_option_greeks_per_100(
            forward_dirty_price=inputs.model_forward_price_per_100,
            strike_dirty_price=inputs.model_strike_price_per_100,
            price_volatility=volatility,
            time_to_expiry=inputs.time_to_expiry_year_fraction,
            discount_factor=inputs.effective_reporting_date_discount_factor,
            option_type=request.bond_option.option_type,
        )

    result = price_bli_mvp_standalone_option(request, price_basis=basis)
    assert result.status is PricingStatus.SUCCESS
    assert result.assumptions["black76_pv_per_100"] == expected_pv
    assert result.assumptions["vega_per_vol_point_per_100"] == (
        expected_greeks.vega_per_vol_point_per_100
    )
    assert result.assumptions["forward_price_delta_per_100"] == (
        expected_greeks.forward_price_delta_per_100
    )


@_requires_quantlib
def test_the_two_bases_price_differently_when_accrued_interest_is_non_zero():
    # They must not collapse: a CLEAN run prices a different F and K.
    dirty = price_bli_mvp_standalone_option(
        _request(), price_basis=BondOptionPriceBasis.DIRTY
    )
    clean = price_bli_mvp_standalone_option(
        _request(), price_basis=BondOptionPriceBasis.CLEAN
    )

    assert dirty.assumptions["accrued_interest_at_forward_settlement_per_100"] > 0
    assert clean.status is PricingStatus.SUCCESS
    assert clean.pv != dirty.pv
    assert clean.method == "black76_forward_clean_price_ovme_v1"
    assert clean.assumptions["bond_option_price_basis"] == "CLEAN"
    assert clean.assumptions["methodology"] == (
        "ovme_clean_price_black76_act_act_option_discount_curve"
    )
    assert clean.assumptions["greeks_methodology"] == (
        "black76_forward_clean_price_closed_form_european_v1"
    )


def test_selecting_a_basis_selects_a_wrapper_and_never_a_second_formula():
    # The clean and dirty wrappers are the same core: on identical numbers
    # they return identical premium and identical Greeks. What differs
    # between two *runs* is only which F/K pair reaches them.
    shared = dict(
        price_volatility=0.18,
        time_to_expiry=0.2465753424657534,
        discount_factor=0.9927018791932419,
        option_type="CALL",
    )
    assert black76_price_option_pv_per_100(
        forward_clean_price=102.259, strike_clean_price=100.459, **shared
    ) == black76_dirty_price_option_pv_per_100(
        forward_dirty_price=102.259, strike_dirty_price=100.459, **shared
    )
    assert black76_clean_price_option_greeks_per_100(
        forward_clean_price=102.259, strike_clean_price=100.459, **shared
    ) == black76_dirty_price_option_greeks_per_100(
        forward_dirty_price=102.259, strike_dirty_price=100.459, **shared
    )


# --- An unknown basis is a refusal, never a default --------------------------


@pytest.mark.parametrize("bad", [None, "", "   ", "DIRTY_PRICE", "Numerix"])
def test_an_unknown_basis_is_refused_rather_than_defaulted(bad):
    with pytest.raises(ValueError):
        price_bli_mvp_standalone_option(_request(), price_basis=bad)
