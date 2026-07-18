"""Tests for ``build_bli_standalone_option_request`` (Issue #96 + OVME #94).

The builder is a mechanical composition of reviewed types: resolve the bond
option's underlying against a reference-data universe, assemble a
``BLIMarketDataSnapshot`` from already-typed components + explicit metadata
(now including the explicit ``forward_clean_price_input`` the OVME-aligned
standalone path requires), and construct the standalone
``BLIStandaloneBondOptionRequest`` that ``price_bli_mvp_standalone_option``
consumes. It adds no financial validation of its own and follows the existing
``build_bli_mvp_input_bundle`` caller-remediation convention (return the
object on success; raise a clear ``ValueError`` for a non-eligible
resolution; propagate every other constructor error unchanged).

The market case below is a **clearly labeled sanitized synthetic**
market-shaped case, derived from existing reviewed fixture economics
(``SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT`` / ``SYNTHETIC_BOND_FIXTURES`` /
``SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT``) plus the short-tenor Option
Discount Curve nodes the standalone engine tests use. **It is not real-market
or Bloomberg validation.**
"""

from __future__ import annotations

import inspect
from dataclasses import replace

import pytest

from shiori_pricing_lab.data import bli_standalone_option_request_builder as builder_module
from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIForwardCleanPriceInput,
    BLIMarketDataStatus,
    BLIVolatilityBasis,
)
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.data.bli_standalone_option_request_builder import (
    build_bli_standalone_option_request,
)
from shiori_pricing_lab.pricing.bli_pricing_engine import price_bli_mvp_standalone_option
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available
from shiori_pricing_lab.pricing.result import PricingErrorCode, PricingStatus
from shiori_pricing_lab.products.enums import Currency, TreasuryFTPQuoteSide
from shiori_pricing_lab.products.fixtures import SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT
from shiori_pricing_lab.reference_data.fixtures import SYNTHETIC_BOND_FIXTURES
from shiori_pricing_lab.reference_data.resolution import DuplicateBondReferenceDataError

_QUANTLIB_AVAILABLE = is_quantlib_available()
_requires_quantlib = pytest.mark.skipif(
    not _QUANTLIB_AVAILABLE, reason="QuantLib is not installed in this environment"
)

# --- Sanitized synthetic market-shaped case (NOT real-market/Bloomberg) ------

_BOND_OPTION = SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT.bond_option
_BOND_QUOTE = SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.bond_quote
_VOLATILITY_INPUT = SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.volatility_input
_CREDIT_SPREAD_INPUT = SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.credit_spread_input

_VALUATION_DATE = "2026-07-01"
_AS_OF_TIMESTAMP = "2026-07-01T16:00:00Z"
_SOURCE_SYSTEM = "SANITIZED_SYNTHETIC_MARKET_SOURCE"
_SNAPSHOT_ID = "SANITIZED_SYNTHETIC_STANDALONE_SNAPSHOT_0001"

# Synthetic timing/date contract values (Issue #94 human methodology
# approval, comment 5001749998). _BOND_OPTION.expiry_date is "2026-09-29".
_PRICING_TIMESTAMP = "2026-07-01T16:00:00Z"
_EXPIRY_TIMESTAMP = "2026-09-29T16:00:00Z"
_REPORTING_DATE = "2026-07-01"
_FORWARD_SETTLEMENT_DATE = "2026-10-01"
_OPTION_SETTLEMENT_DATE = "2026-10-02"

# Pinned expected OVME values -- identical to the standalone engine tests for
# the same shared bundle-fixture economics and explicit MID forward of 101.30.
_EXPECTED_FORWARD_CLEAN_PRICE_PER_100 = 101.30
_EXPECTED_TIME_TO_EXPIRY = 0.2465753424657534
_EXPECTED_EFFECTIVE_DF = 0.9927018791932419
_EXPECTED_BLACK76_PV_PER_100 = 4.550638980692688
_EXPECTED_PV = 2.275319490346344

_FORWARD_INPUT = BLIForwardCleanPriceInput(
    forward_clean_price_per_100=_EXPECTED_FORWARD_CLEAN_PRICE_PER_100,
    quote_side=TreasuryFTPQuoteSide.MID,
    source_system=_SOURCE_SYSTEM,
    status=BLIMarketDataStatus.ACTIVE,
)


def _option_discount_curve_points() -> tuple[BLICurvePoint, ...]:
    """Short-tenor Option Discount Curve nodes (no Bond Reference / Deposit Curve).

    Same node economics as the standalone engine tests, so the built request
    reproduces the pinned premium. The OVME-aligned standalone path requires
    only the Option Discount Curve -- no BOND_REFERENCE_CURVE (the forward is
    explicit) and no DEPOSIT_CURVE.
    """

    common = {
        "currency": Currency.USD,
        "rate_basis": BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
        "source_system": _SOURCE_SYSTEM,
        "status": BLIMarketDataStatus.ACTIVE,
    }
    return tuple(
        BLICurvePoint(
            curve_id="SANITIZED_OPTION_DISCOUNT_CURVE",
            curve_name="SANITIZED_OPTION_DISCOUNT_CURVE",
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            tenor=tenor,
            rate=rate,
            **common,
        )
        for tenor, rate in (("1M", 0.028), ("1Y", 0.032))
    )


def _supported_kwargs(**overrides) -> dict:
    params = dict(
        bond_option=_BOND_OPTION,
        bond_reference_data_universe=SYNTHETIC_BOND_FIXTURES,
        valuation_date=_VALUATION_DATE,
        as_of_timestamp=_AS_OF_TIMESTAMP,
        source_system=_SOURCE_SYSTEM,
        snapshot_id=_SNAPSHOT_ID,
        snapshot_status=BLIMarketDataStatus.ACTIVE,
        bond_quote=_BOND_QUOTE,
        curve_points=_option_discount_curve_points(),
        volatility_input=_VOLATILITY_INPUT,
        credit_spread_input=_CREDIT_SPREAD_INPUT,
        forward_clean_price_input=_FORWARD_INPUT,
        pricing_timestamp=_PRICING_TIMESTAMP,
        expiry_timestamp=_EXPIRY_TIMESTAMP,
        reporting_date=_REPORTING_DATE,
        forward_settlement_date=_FORWARD_SETTLEMENT_DATE,
        option_settlement_date=_OPTION_SETTLEMENT_DATE,
    )
    params.update(overrides)
    return params


# --- 1. Successful build + exact provenance/identity preservation ------------


def test_successful_build_returns_standalone_request():
    request = build_bli_standalone_option_request(**_supported_kwargs())
    assert isinstance(request, BLIStandaloneBondOptionRequest)


def test_build_preserves_provenance_quote_side_and_curve_identity():
    curve_points = _option_discount_curve_points()
    request = build_bli_standalone_option_request(**_supported_kwargs(curve_points=curve_points))

    snapshot = request.market_data_snapshot
    # Provenance / snapshot identity preserved unchanged.
    assert snapshot.source_system == _SOURCE_SYSTEM
    assert snapshot.as_of_timestamp == _AS_OF_TIMESTAMP  # source-as-of, unchanged
    assert snapshot.snapshot_id == _SNAPSHOT_ID
    assert request.valuation_date == _VALUATION_DATE
    assert snapshot.valuation_date == _VALUATION_DATE

    # Object semantics: typed components passed through by reference.
    assert request.bond_option is _BOND_OPTION
    assert snapshot.bond_quote is _BOND_QUOTE
    assert snapshot.volatility_input is _VOLATILITY_INPUT
    assert snapshot.credit_spread_input is _CREDIT_SPREAD_INPUT
    assert snapshot.curve_points == tuple(curve_points)

    # Explicit forward clean price input preserved with provenance + side.
    assert snapshot.forward_clean_price_input is _FORWARD_INPUT
    assert snapshot.forward_clean_price_input.quote_side == snapshot.bond_quote.quote_side

    # Quote side + units/values preserved (unchanged from the passed quote).
    assert snapshot.bond_quote.quote_side == _BOND_QUOTE.quote_side
    assert snapshot.bond_quote.clean_price_per_100 == _BOND_QUOTE.clean_price_per_100

    # Curve identity / purpose preserved: only the Option Discount Curve.
    by_id = {(p.curve_id, p.curve_purpose) for p in snapshot.curve_points}
    assert ("SANITIZED_OPTION_DISCOUNT_CURVE", BLICurvePurpose.OPTION_DISCOUNT_CURVE) in by_id
    assert all(
        p.curve_purpose is BLICurvePurpose.OPTION_DISCOUNT_CURVE for p in snapshot.curve_points
    )

    # Resolved reference data matches the option's underlying ISIN.
    assert request.resolved_bond_reference_data.isin == _BOND_OPTION.underlying_isin


def test_build_requires_no_deposit_curve():
    request = build_bli_standalone_option_request(**_supported_kwargs())
    present = {p.curve_purpose for p in request.market_data_snapshot.curve_points}
    assert BLICurvePurpose.DEPOSIT_CURVE not in present
    assert request.market_data_snapshot.deposit_rate_observation is None


def test_repeated_build_is_deterministic():
    curve_points = _option_discount_curve_points()
    first = build_bli_standalone_option_request(**_supported_kwargs(curve_points=curve_points))
    second = build_bli_standalone_option_request(**_supported_kwargs(curve_points=curve_points))
    assert first == second


# --- 2. Built request reaches the engine with pinned OVME outputs ------------


@_requires_quantlib
def test_built_request_prices_to_pinned_premium():
    request = build_bli_standalone_option_request(**_supported_kwargs())
    result = price_bli_mvp_standalone_option(request)

    assert result.status is PricingStatus.SUCCESS
    assert result.assumptions["forward_clean_price_per_100"] == pytest.approx(
        _EXPECTED_FORWARD_CLEAN_PRICE_PER_100
    )
    assert result.assumptions["time_to_expiry_year_fraction"] == pytest.approx(
        _EXPECTED_TIME_TO_EXPIRY
    )
    assert result.assumptions["effective_reporting_date_discount_factor"] == pytest.approx(
        _EXPECTED_EFFECTIVE_DF
    )
    assert result.assumptions["black76_pv_per_100"] == pytest.approx(
        _EXPECTED_BLACK76_PV_PER_100
    )
    assert result.pv == pytest.approx(_EXPECTED_PV)


# --- 3. Resolver failure mapping ---------------------------------------------


def test_not_found_maps_to_value_error_with_status_and_block_reason():
    not_found_option = replace(_BOND_OPTION, underlying_isin="XS0000000099")
    with pytest.raises(ValueError) as exc_info:
        build_bli_standalone_option_request(
            **_supported_kwargs(
                bond_option=not_found_option,
                bond_reference_source_name="MY_SANITIZED_SOURCE",
            )
        )
    message = str(exc_info.value)
    assert "NOT_FOUND" in message
    assert "MY_SANITIZED_SOURCE" in message


def test_found_ineligible_maps_to_value_error_with_status_and_block_reason():
    ineligible_option = replace(_BOND_OPTION, underlying_isin="XS0000000002")
    with pytest.raises(ValueError) as exc_info:
        build_bli_standalone_option_request(
            **_supported_kwargs(bond_option=ineligible_option)
        )
    message = str(exc_info.value)
    assert "FOUND_INELIGIBLE" in message


def test_duplicate_reference_data_isin_propagates_unchanged():
    duplicate_universe = list(SYNTHETIC_BOND_FIXTURES) + [SYNTHETIC_BOND_FIXTURES[0]]
    with pytest.raises(DuplicateBondReferenceDataError):
        build_bli_standalone_option_request(
            **_supported_kwargs(bond_reference_data_universe=duplicate_universe)
        )


# --- 4. Existing request-contract validation propagates unchanged ------------


def test_wrong_currency_propagates_from_request_contract():
    eur_option = replace(_BOND_OPTION, currency=Currency.EUR)
    with pytest.raises(ValueError, match="does not match"):
        build_bli_standalone_option_request(**_supported_kwargs(bond_option=eur_option))


def test_wrong_snapshot_isin_propagates_from_request_contract():
    mismatched_quote = replace(_BOND_QUOTE, isin="XS0000000009")
    with pytest.raises(ValueError, match="does not exactly match"):
        build_bli_standalone_option_request(**_supported_kwargs(bond_quote=mismatched_quote))


def test_future_as_of_propagates_from_request_contract():
    with pytest.raises(ValueError, match="no-look-ahead"):
        build_bli_standalone_option_request(
            **_supported_kwargs(as_of_timestamp="2026-07-02T00:00:00Z")
        )


def test_invalid_valuation_date_propagates_from_snapshot_contract():
    with pytest.raises(ValueError, match="valuation_date"):
        build_bli_standalone_option_request(**_supported_kwargs(valuation_date="not-a-date"))


# --- 5. Existing snapshot curve validation propagates unchanged --------------


def test_duplicate_curve_node_propagates_from_snapshot_contract():
    points = _option_discount_curve_points()
    duplicated = points + (points[0],)  # exact duplicate node (same id+tenor+rate)
    with pytest.raises(ValueError, match="duplicate curve node"):
        build_bli_standalone_option_request(**_supported_kwargs(curve_points=duplicated))


def test_conflicting_curve_node_propagates_from_snapshot_contract():
    points = _option_discount_curve_points()
    conflicting = points + (replace(points[0], rate=points[0].rate + 0.01),)
    with pytest.raises(ValueError, match="conflicting rate"):
        build_bli_standalone_option_request(**_supported_kwargs(curve_points=conflicting))


def test_ambiguous_curve_identity_propagates_from_snapshot_contract():
    points = _option_discount_curve_points()
    # A second curve_id for the same currency + OPTION_DISCOUNT_CURVE purpose.
    ambiguous = points + (
        BLICurvePoint(
            curve_id="OTHER_OPTION_DISCOUNT_CURVE",
            curve_name="OTHER_OPTION_DISCOUNT_CURVE",
            currency=Currency.USD,
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            tenor="2Y",
            rate=0.036,
            rate_basis=BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
            source_system=_SOURCE_SYSTEM,
            status=BLIMarketDataStatus.ACTIVE,
        ),
    )
    with pytest.raises(ValueError, match="ambiguous curve_id"):
        build_bli_standalone_option_request(**_supported_kwargs(curve_points=ambiguous))


# --- 6. Construction-vs-pricing boundary preserved (build ok, pricing fails) --


def test_yield_vol_builds_then_fails_at_pricing_guard():
    yield_vol_input = replace(_VOLATILITY_INPUT, volatility_basis=BLIVolatilityBasis.YIELD_VOL)
    request = build_bli_standalone_option_request(
        **_supported_kwargs(volatility_input=yield_vol_input)
    )
    assert isinstance(request, BLIStandaloneBondOptionRequest)

    result = price_bli_mvp_standalone_option(request)
    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.UNSUPPORTED_PRODUCT


@_requires_quantlib
def test_unsupported_rate_basis_builds_then_fails_at_pricing_engine():
    # A PAR_RATE Option Discount Curve is constructible (the schema allows any
    # BLICurveRateBasis) but the pricing engine's discount-factor helper
    # rejects a non-CONTINUOUS_ZERO_RATE basis -> FAILED / ENGINE_ERROR.
    common = {
        "currency": Currency.USD,
        "source_system": _SOURCE_SYSTEM,
        "status": BLIMarketDataStatus.ACTIVE,
    }
    par_option_discount = tuple(
        BLICurvePoint(
            curve_id="SANITIZED_OPTION_DISCOUNT_CURVE",
            curve_name="SANITIZED_OPTION_DISCOUNT_CURVE",
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            tenor=tenor,
            rate=rate,
            rate_basis=BLICurveRateBasis.PAR_RATE,
            **common,
        )
        for tenor, rate in (("1M", 0.028), ("1Y", 0.032))
    )
    request = build_bli_standalone_option_request(
        **_supported_kwargs(curve_points=par_option_discount)
    )
    assert isinstance(request, BLIStandaloneBondOptionRequest)

    result = price_bli_mvp_standalone_option(request)
    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.ENGINE_ERROR


# --- 7. Contract guards: type-check, no mutation, no clock/provider ----------


def test_rejects_non_bond_option():
    with pytest.raises(TypeError, match="bond_option must be a BondOption"):
        build_bli_standalone_option_request(**_supported_kwargs(bond_option=object()))


def test_does_not_mutate_supplied_inputs():
    curve_points_list = list(_option_discount_curve_points())
    before_len = len(curve_points_list)
    before_first = curve_points_list[0]

    request = build_bli_standalone_option_request(
        **_supported_kwargs(curve_points=curve_points_list)
    )

    # Supplied list is untouched; passed components are the same objects.
    assert len(curve_points_list) == before_len
    assert curve_points_list[0] is before_first
    assert request.bond_option is _BOND_OPTION
    assert request.market_data_snapshot.bond_quote is _BOND_QUOTE
    assert request.market_data_snapshot.volatility_input is _VOLATILITY_INPUT


def test_module_reads_no_system_clock_and_no_provider():
    source = inspect.getsource(builder_module)
    for forbidden in (
        "date.today(",
        "datetime.now(",
        "utcnow(",
        "read_csv",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "pandas",
    ):
        assert forbidden not in source, f"unexpected reference to {forbidden!r}"
