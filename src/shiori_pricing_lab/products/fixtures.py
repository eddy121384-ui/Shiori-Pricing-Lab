"""Small, manually reviewed synthetic ``BondLinkedStructuredProduct`` fixture.

Per `docs/24_bli_mvp_input_bundle_preflight.md` §8.2/§8.3/§11: there was, until
this module, no synthetic ``BondLinkedStructuredProduct`` whose
``bond_option.underlying_isin`` lined up with the eligible ISIN
(``"XS0000000001"``) already used by
``reference_data.fixtures.SYNTHETIC_BOND_FIXTURES`` and
``data.bli_snapshot_fixtures.SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT`` --
``tests/test_bond_linked_structured_product.py``'s inline helper uses a
different, unrelated ISIN (``"US912828ZZ11"``), which was fine for that
test's own purpose (schema validation only) but cannot be combined with
the other two fixtures into one positive ``BLIMVPInputBundle`` fixture.

This module adds exactly that missing piece: one synthetic, importable
``BondLinkedStructuredProduct`` instance referencing ``"XS0000000001"``.
Dates and notionals otherwise mirror the existing
``tests/test_bond_linked_structured_product.py`` default helper (expiry
``2026-09-29`` + ``settlement_lag_days=2`` = effective settlement
``2026-10-01``, exactly the deposit leg's maturity date) so this fixture
is recognizable, not a new ad hoc shape.

``deposit_rate_mode`` is deliberately ``TREASURY_FTP_REFERENCE`` (not
``FIXED_RATE``): the ``ftp_rate_selector`` (currency ``USD``, tenor
``3M``, quote side ``MID``) is built to match
``data.bli_snapshot_fixtures.SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT``'s own
``deposit_rate_observation`` exactly, so the combined positive
``BLIMVPInputBundle`` fixture exercises the product-specific
deposit-rate-observation gate (`docs/24` §6) meaningfully, rather than
only the trivially-satisfied ``FIXED_RATE`` path.

No pricing, cash-flow generation, schedule engine, market data, or input
bundle is implemented here -- this is a product-term fixture only.
"""

from __future__ import annotations

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

# The eligible FIXED_COUPON_BULLET bond's ISIN, reused from
# reference_data.fixtures.SYNTHETIC_BOND_FIXTURES (docs/20/PR #58) and
# data.bli_snapshot_fixtures.SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT (docs/23/
# PR #63) -- not re-declared here as a literal duplicate on purpose, since
# doing so would risk the three fixtures drifting apart again exactly the
# way docs/24 found them to have drifted before this module existed. The
# literal string is intentionally still used directly (rather than an
# import) to avoid a products -> reference_data / data cross-package
# import for a single ISIN string; the fixture-consistency test in
# tests/test_bli_mvp_input_bundle.py is what actually guards against
# drift going forward.
_SYNTHETIC_ELIGIBLE_ISIN = "XS0000000001"

_SYNTHETIC_BOND_OPTION = BondOption(
    product_id="BONDOPT-SYNTHETIC-0001",
    underlying_isin=_SYNTHETIC_ELIGIBLE_ISIN,
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

_SYNTHETIC_DEPOSIT_LEG = DepositLeg(
    deposit_leg_id="DEP-SYNTHETIC-0001",
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

SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT = BondLinkedStructuredProduct(
    product_id="BLI-SYNTHETIC-0001",
    deposit_leg=_SYNTHETIC_DEPOSIT_LEG,
    bond_option=_SYNTHETIC_BOND_OPTION,
)
