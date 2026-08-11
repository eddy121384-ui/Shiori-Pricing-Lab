"""Tests for `pricing/bli_standalone_option_pricing_inputs.py` (Issue #165
live-wiring follow-up).

This module had no pre-existing dedicated test file (it is otherwise
exercised indirectly through
`tests/test_bli_implied_price_vol_calibration.py` and the workbench/UI
suites). Focused, minimal coverage added here: proves
`resolve_standalone_option_pricing_inputs` (via its internal
`_option_discount_factor_to_date` helper) now consumes an explicit
Bloomberg-sourced `BLICurvePoint.maturity_date` end-to-end, using
`as_of_date=request.valuation_date`, so the resolved option-settlement
discount factor differs from what a tenor-only curve of the same rates
would give; a tenor-only curve stays numerically unchanged; and
out-of-coverage rejection remains fail-closed. Fixture-construction
helpers here mirror the established local pattern already used in
`tests/test_bli_pricing_engine.py` / `tests/test_bli_implied_price_vol_calibration.py`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from math import exp

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
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available
from shiori_pricing_lab.pricing.bli_standalone_option_pricing_inputs import (
    resolve_standalone_option_pricing_inputs,
)
from shiori_pricing_lab.products.enums import TreasuryFTPQuoteSide

_requires_quantlib = pytest.mark.skipif(
    not is_quantlib_available(), reason="QuantLib is not installed in this environment"
)

# Synthetic timing/date contract values, mirroring
# tests/test_bli_implied_price_vol_calibration.py's own fixture (Issue #94
# human methodology approval, comment 5001749998).
_VALUATION_DATE = SYNTHETIC_BLI_MVP_INPUT_BUNDLE.valuation_date  # "2026-07-01"
_PRICING_TIMESTAMP = "2026-07-01T16:00:00Z"
_EXPIRY_TIMESTAMP = "2026-09-29T16:00:00Z"
_REPORTING_DATE = "2026-07-01"  # == valuation_date -> DF == 1.0, no curve read
_FORWARD_SETTLEMENT_DATE = "2026-10-01"
_OPTION_SETTLEMENT_DATE = "2026-10-02"  # 93 days after valuation_date

_CURVE_COMMON = {
    "curve_id": "TEST_LOCAL_OPTION_DISCOUNT_CURVE",
    "curve_name": "TEST_LOCAL_OPTION_DISCOUNT_CURVE",
    "curve_purpose": BLICurvePurpose.OPTION_DISCOUNT_CURVE,
    "rate_basis": BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
    "source_system": "TEST_LOCAL_CURVE",
    "status": BLIMarketDataStatus.ACTIVE,
}


def _tenor_only_option_discount_curve_points(currency) -> tuple[BLICurvePoint, ...]:
    common = {**_CURVE_COMMON, "currency": currency}
    return (
        BLICurvePoint(tenor="1M", rate=0.028, **common),
        BLICurvePoint(tenor="1Y", rate=0.032, **common),
    )


def _explicit_date_option_discount_curve_points(currency) -> tuple[BLICurvePoint, ...]:
    """Same rates (0.028/0.032) as the tenor-only fixture, but with explicit
    maturity_dates deliberately different from what "1M"/"1Y" would imply --
    45 and 274 days after valuation_date 2026-07-01, still bracketing the
    option_settlement_date target (93 days after valuation_date)."""

    common = {**_CURVE_COMMON, "currency": currency}
    return (
        BLICurvePoint(tenor="1M", rate=0.028, maturity_date="2026-08-15", **common),
        BLICurvePoint(tenor="1Y", rate=0.032, maturity_date="2027-04-01", **common),
    )


def _deposit_curve_points() -> tuple[BLICurvePoint, ...]:
    return tuple(
        point
        for point in SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.curve_points
        if point.curve_purpose is BLICurvePurpose.DEPOSIT_CURVE
    )


def _request_with_option_discount_curve(
    points: tuple[BLICurvePoint, ...],
) -> BLIStandaloneBondOptionRequest:
    snapshot = replace(
        SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT,
        snapshot_id="TEST_LOCAL_SUPPORTED_SNAPSHOT",
        source_system="TEST_LOCAL_CURVE",
        curve_points=points + _deposit_curve_points(),
        forward_clean_price_input=BLIForwardCleanPriceInput(
            forward_clean_price_per_100=101.30,
            quote_side=TreasuryFTPQuoteSide.MID,
            source_system="TEST_LOCAL_FORWARD_FEED",
            status=BLIMarketDataStatus.ACTIVE,
        ),
    )
    return BLIStandaloneBondOptionRequest(
        bond_option=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        resolved_bond_reference_data=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.resolved_bond_reference_data,
        valuation_date=_VALUATION_DATE,
        market_data_snapshot=snapshot,
        pricing_timestamp=_PRICING_TIMESTAMP,
        expiry_timestamp=_EXPIRY_TIMESTAMP,
        reporting_date=_REPORTING_DATE,
        forward_settlement_date=_FORWARD_SETTLEMENT_DATE,
        option_settlement_date=_OPTION_SETTLEMENT_DATE,
    )


@_requires_quantlib
def test_explicit_maturity_date_changes_the_option_settlement_discount_factor():
    currency = SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option.currency

    tenor_only_request = _request_with_option_discount_curve(
        _tenor_only_option_discount_curve_points(currency)
    )
    explicit_date_request = _request_with_option_discount_curve(
        _explicit_date_option_discount_curve_points(currency)
    )

    tenor_only_inputs = resolve_standalone_option_pricing_inputs(tenor_only_request)
    explicit_date_inputs = resolve_standalone_option_pricing_inputs(explicit_date_request)

    # Same rates (0.028/0.032), same requested date -- only the node
    # coordinates differ (tenor label vs. explicit date), so if this live
    # path is genuinely consuming the explicit dates, the two results
    # must differ.
    assert explicit_date_inputs.pricing_to_option_settlement_discount_factor != pytest.approx(
        tenor_only_inputs.pricing_to_option_settlement_discount_factor
    )

    # Independently derive the expected value from the explicit dates
    # themselves (plain date arithmetic + the same piecewise-linear/
    # exp(-rT) formula), never by calling
    # discount_factor_from_continuous_zero_curve/build_continuous_zero_
    # curve_nodes/interpolate_continuous_zero_rate directly.
    valuation_date = date(2026, 7, 1)
    node_a_year_fraction = (date(2026, 8, 15) - valuation_date).days / 365.0
    node_b_year_fraction = (date(2027, 4, 1) - valuation_date).days / 365.0
    target_year_fraction = (date(2026, 10, 2) - valuation_date).days / 365.0
    expected_zero_rate = 0.028 + (0.032 - 0.028) * (target_year_fraction - node_a_year_fraction) / (
        node_b_year_fraction - node_a_year_fraction
    )
    expected_discount_factor = exp(-expected_zero_rate * target_year_fraction)

    assert explicit_date_inputs.pricing_to_option_settlement_discount_factor == pytest.approx(
        expected_discount_factor
    )


@_requires_quantlib
def test_tenor_only_curve_discount_factors_are_numerically_unchanged():
    currency = SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option.currency
    request = _request_with_option_discount_curve(
        _tenor_only_option_discount_curve_points(currency)
    )

    inputs = resolve_standalone_option_pricing_inputs(request)

    # reporting_date == valuation_date -> exactly 1.0, no curve read at all.
    assert inputs.pricing_to_reporting_discount_factor == 1.0

    valuation_date = date(2026, 7, 1)
    target_year_fraction = (date(2026, 10, 2) - valuation_date).days / 365.0
    node_1m_year_fraction = 1 / 12
    node_1y_year_fraction = 1.0
    expected_zero_rate = 0.028 + (0.032 - 0.028) * (
        target_year_fraction - node_1m_year_fraction
    ) / (node_1y_year_fraction - node_1m_year_fraction)
    expected_discount_factor = exp(-expected_zero_rate * target_year_fraction)

    assert inputs.pricing_to_option_settlement_discount_factor == pytest.approx(
        expected_discount_factor
    )


@_requires_quantlib
def test_explicit_maturity_date_out_of_coverage_still_fails_closed():
    # Both explicit-date nodes fall strictly after option_settlement_date
    # (93 days after valuation_date) -- out of coverage, must fail closed
    # rather than extrapolate.
    currency = SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option.currency
    common = {**_CURVE_COMMON, "currency": currency}
    out_of_coverage_points = (
        BLICurvePoint(tenor="1Y", rate=0.028, maturity_date="2027-04-01", **common),
        BLICurvePoint(tenor="2Y", rate=0.032, maturity_date="2028-04-01", **common),
    )
    request = _request_with_option_discount_curve(out_of_coverage_points)

    with pytest.raises(ValueError, match="outside the node range"):
        resolve_standalone_option_pricing_inputs(request)
