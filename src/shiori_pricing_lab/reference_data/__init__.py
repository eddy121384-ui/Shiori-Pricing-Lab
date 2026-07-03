"""Bond Reference Data (Bond Master): static, security-level bond terms.

This package is the **package-decision resolution** `docs/20
<bli_bond_reference_data_preflight.md>`_ §7/§11/§12 left open: Bond
Reference Data is a sibling to ``shiori_pricing_lab.products``, not a
member of it, because it describes the underlying bond's own static
terms (what the issuer promised), not a traded deal's terms (what was
agreed at execution) and not market data (what the market says right
now).

Strict boundaries, mirroring the ``products`` package's own rules:

- No market observation, valuation date, resolved rate, or pricing
  output lives here (docs/20 §3).
- No product schema (``BondOption``, ``DepositLeg``,
  ``BondLinkedStructuredProduct``) is modified or duplicated here; a
  future pricing engine is responsible for resolving
  ``BondOption.underlying_isin`` against this package's fixture, not this
  package itself (docs/20 §8, §11 -- the lookup/resolution mechanism is
  explicitly deferred).
- No pricing, cash-flow generation, schedule engine, QuantLib,
  ``MarketDataSnapshot``, MVP input bundle, file parser, ingestion, or
  Bloomberg/API connector is implemented here.

Provides ``BondReferenceData`` (the schema), ``BondType`` / ``BondStatus``
(the controlled vocabulary this slice adds), ``is_mvp_pricing_eligible`` /
``EligibilityResult`` (the plain-vanilla MVP eligibility check, kept
separate from reference-data validation), and
``SYNTHETIC_BOND_FIXTURES`` (the small, manually reviewed fixture).
"""

from __future__ import annotations

from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData
from shiori_pricing_lab.reference_data.eligibility import EligibilityResult, is_mvp_pricing_eligible
from shiori_pricing_lab.reference_data.enums import BondStatus, BondType
from shiori_pricing_lab.reference_data.fixtures import SYNTHETIC_BOND_FIXTURES

__all__ = [
    "SYNTHETIC_BOND_FIXTURES",
    "BondReferenceData",
    "BondStatus",
    "BondType",
    "EligibilityResult",
    "is_mvp_pricing_eligible",
]
