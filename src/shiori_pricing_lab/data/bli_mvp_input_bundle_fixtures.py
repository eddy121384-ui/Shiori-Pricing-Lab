"""Small, manually reviewed synthetic ``BLIMVPInputBundle`` fixture.

Per `docs/24_bli_mvp_input_bundle_preflight.md` §8.3: one positive
``BLIMVPInputBundle`` combining the three existing fixtures --
``products.fixtures.SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT``,
``reference_data.fixtures.SYNTHETIC_BOND_FIXTURES``, and
``data.bli_snapshot_fixtures.SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT`` -- all
three already share the eligible ISIN ``"XS0000000001"``.

Built via ``build_bli_mvp_input_bundle`` (`docs/24` §12 step 5,
`bli_mvp_input_bundle_builder.py`) rather than hand-wiring a
``resolve_bond_reference_data`` call and unpacking its result -- the
builder is now the normal construction path into ``BLIMVPInputBundle``,
so this fixture exercises it directly instead of duplicating what it
does. This module still defines no reusable function of its own for
turning an arbitrary product into a bundle: it only wires together three
already-existing fixtures for this one, hand-picked, manually-reviewed
case, the same way `docs/23`'s ``bli_snapshot_fixtures.py`` hand-wires
its own component fixtures.

**Explicitly not built here:** a pricing engine, or any other code
beyond this one fixture (docs/24 §10/§17).
"""

from __future__ import annotations

from shiori_pricing_lab.data.bli_mvp_input_bundle_builder import build_bli_mvp_input_bundle
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.products.fixtures import SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT
from shiori_pricing_lab.reference_data.fixtures import SYNTHETIC_BOND_FIXTURES

SYNTHETIC_BLI_MVP_INPUT_BUNDLE = build_bli_mvp_input_bundle(
    bundle_id="SYNTHETIC_BLI_MVP_INPUT_BUNDLE_0001",
    valuation_date=SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.valuation_date,
    product=SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT,
    bond_reference_data_universe=SYNTHETIC_BOND_FIXTURES,
    market_data_snapshot=SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT,
)
