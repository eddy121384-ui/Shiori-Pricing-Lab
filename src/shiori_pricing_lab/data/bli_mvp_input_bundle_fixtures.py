"""Small, manually reviewed synthetic ``BLIMVPInputBundle`` fixture.

Per `docs/24_bli_mvp_input_bundle_preflight.md` §8.3: one positive
``BLIMVPInputBundle`` combining the three existing fixtures --
``products.fixtures.SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT``,
``reference_data.fixtures.SYNTHETIC_BOND_FIXTURES`` (resolved via
``resolve_bond_reference_data``), and
``data.bli_snapshot_fixtures.SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT`` -- all
three already share the eligible ISIN ``"XS0000000001"``.

``resolve_bond_reference_data`` is called directly here (module-level,
at import time) rather than hand-constructing a
``BondReferenceResolutionResult`` -- this fixture module is not a
bundle builder (docs/24 §10's non-goal): it defines no reusable
function for turning an arbitrary product into a bundle, it only wires
together three already-existing fixtures for this one, hand-picked,
manually-reviewed case, the same way `docs/23`'s
``bli_snapshot_fixtures.py`` hand-wires its own component fixtures.

**Explicitly not built here:** a bundle builder, a pricing engine, or any
other code beyond this one fixture (docs/24 §10/§17).
"""

from __future__ import annotations

from shiori_pricing_lab.data.bli_mvp_input_bundle import BLIMVPInputBundle
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.products.fixtures import SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT
from shiori_pricing_lab.reference_data.resolution import resolve_bond_reference_data

_SYNTHETIC_RESOLUTION = resolve_bond_reference_data(
    SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT.bond_option.underlying_isin
)

SYNTHETIC_BLI_MVP_INPUT_BUNDLE = BLIMVPInputBundle(
    bundle_id="SYNTHETIC_BLI_MVP_INPUT_BUNDLE_0001",
    valuation_date=SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.valuation_date,
    product=SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT,
    resolved_bond_reference_data=_SYNTHETIC_RESOLUTION.bond_reference_data,
    resolution_status=_SYNTHETIC_RESOLUTION.status,
    eligibility_reasons=_SYNTHETIC_RESOLUTION.eligibility_reasons,
    market_data_snapshot=SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT,
)
