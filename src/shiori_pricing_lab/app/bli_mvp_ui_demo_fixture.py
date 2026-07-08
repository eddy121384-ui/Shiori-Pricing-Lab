"""UI-only demo ``BLIMVPInputBundle`` fixtures for the BLI MVP Streamlit page (Issue #84).

Two demo cases, both built by ``dataclasses.replace`` on the existing,
already-reviewed shared fixtures -- no shared fixture, schema, or pricing
module is modified:

- ``SYNTHETIC_UI_DEMO_BUNDLE``: the shared
  ``SYNTHETIC_BLI_MVP_INPUT_BUNDLE`` with its ``BOND_REFERENCE_CURVE`` /
  ``OPTION_DISCOUNT_CURVE`` nodes swapped for short-tenor nodes that
  actually bracket the fixture's own ~0.24-year bond option expiry. This
  mirrors, node-for-node, the test-only ``_local_supported_bundle`` /
  ``_local_short_tenor_curve_points`` pattern in
  ``tests/test_bli_pricing_engine.py`` (Issue #44) -- the only known
  deterministic *success* path for this product shape. That pattern is
  deliberately kept test-local there (out of scope for #44's PR); this
  module gives the UI its own copy so the happy-path demo does not depend
  on test code, without touching the shared fixture itself.
- ``SYNTHETIC_UI_DEMO_BUNDLE_CURVE_RANGE_ERROR``: an alias for the
  unmodified ``SYNTHETIC_BLI_MVP_INPUT_BUNDLE``, reused as-is. Its
  ``"2Y"``/``"5Y"`` curve nodes do not bracket its own short expiry, so
  ``price_bli_mvp`` deterministically returns ``FAILED`` /
  ``ENGINE_ERROR`` (see
  ``test_shared_mvp_fixture_curve_range_gap_maps_to_engine_error``). The
  product shape itself is fully supported -- this is a downstream data
  gap, not an unsupported product, so it must not be labeled
  "unsupported" in the UI.

**Explicitly not built here:** any pricing engine, helper, or schema
change. This module only assembles existing fixtures.
"""

from __future__ import annotations

from dataclasses import replace

from shiori_pricing_lab.data.bli_mvp_input_bundle import BLIMVPInputBundle
from shiori_pricing_lab.data.bli_mvp_input_bundle_fixtures import (
    SYNTHETIC_BLI_MVP_INPUT_BUNDLE,
)
from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIMarketDataStatus,
)
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.products.enums import Currency


def _short_tenor_curve_points(currency: Currency) -> tuple[BLICurvePoint, ...]:
    """Short-tenor Bond Reference + Option Discount Curve nodes for the UI demo.

    Identical values to ``tests/test_bli_pricing_engine.py``'s
    ``_local_short_tenor_curve_points`` -- this is a UI-owned copy of that
    same test-only technique, not a new curve-construction method. The
    existing ``DEPOSIT_CURVE`` rows are reused unchanged, since
    ``BLIMVPInputBundle`` still requires all three curve purposes.
    """

    common = {
        "currency": currency,
        "rate_basis": BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
        "source_system": "UI_DEMO_LOCAL_CURVE",
        "status": BLIMarketDataStatus.ACTIVE,
    }
    bond_reference_nodes = tuple(
        BLICurvePoint(
            curve_id="UI_DEMO_LOCAL_BOND_REFERENCE_CURVE",
            curve_name="UI_DEMO_LOCAL_BOND_REFERENCE_CURVE",
            curve_purpose=BLICurvePurpose.BOND_REFERENCE_CURVE,
            tenor=tenor,
            rate=rate,
            **common,
        )
        for tenor, rate in (("1M", 0.030), ("1Y", 0.035))
    )
    option_discount_nodes = tuple(
        BLICurvePoint(
            curve_id="UI_DEMO_LOCAL_OPTION_DISCOUNT_CURVE",
            curve_name="UI_DEMO_LOCAL_OPTION_DISCOUNT_CURVE",
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            tenor=tenor,
            rate=rate,
            **common,
        )
        for tenor, rate in (("1M", 0.028), ("1Y", 0.032))
    )
    existing_deposit_curve_nodes = tuple(
        point
        for point in SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.curve_points
        if point.curve_purpose is BLICurvePurpose.DEPOSIT_CURVE
    )
    return bond_reference_nodes + option_discount_nodes + existing_deposit_curve_nodes


def _build_ui_demo_bundle() -> BLIMVPInputBundle:
    local_snapshot = replace(
        SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT,
        curve_points=_short_tenor_curve_points(
            SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option.currency
        ),
    )
    return replace(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, market_data_snapshot=local_snapshot)


# Happy-path demo case: deterministically prices to SUCCESS.
SYNTHETIC_UI_DEMO_BUNDLE = _build_ui_demo_bundle()

# Error-path demo case: the unmodified shared fixture, reused as-is. Its
# curve nodes do not bracket its own expiry, so pricing deterministically
# fails with ENGINE_ERROR -- a downstream data gap, not an unsupported
# product shape.
SYNTHETIC_UI_DEMO_BUNDLE_CURVE_RANGE_ERROR = SYNTHETIC_BLI_MVP_INPUT_BUNDLE
