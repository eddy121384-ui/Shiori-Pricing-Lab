"""``build_bli_mvp_input_bundle``: the minimal BLI MVP input bundle builder.

Scope, per `docs/24_bli_mvp_input_bundle_preflight.md` §12 step 5: a small
function that resolves a product's underlying bond against a supplied
reference-data universe (via the existing
``reference_data.resolution.resolve_bond_reference_data``) and then
constructs a ``BLIMVPInputBundle`` from the result. This is the first
normal, callable construction path into ``BLIMVPInputBundle`` -- previously
a caller had to call ``resolve_bond_reference_data`` and unpack its result
into ``BLIMVPInputBundle``'s fields by hand (as
``data/bli_mvp_input_bundle_fixtures.py`` did before this module existed).

**What this builder does and does not do:**

- It extracts ``product.bond_option.underlying_isin`` and calls the
  existing ``resolve_bond_reference_data`` -- it does not re-implement
  ISIN matching, eligibility, or reference-data lookup of any kind.
- It maps a non-``FOUND_ELIGIBLE`` resolver outcome (``NOT_FOUND`` or
  ``FOUND_INELIGIBLE``) to a single, clear ``ValueError`` -- it never
  silently returns ``None``, never fabricates a bundle from a resolver
  result that was not actually eligible, and never coerces an ineligible
  result into an eligible one.
- It performs **no re-validation of ``BLIMVPInputBundle``'s own gates**
  (ISIN cross-checks, currency coherence, valuation-date /
  as-of-no-look-ahead, product-specific deposit-rate consistency, required
  MVP curve-purpose presence, `docs/24` §6) -- ``BLIMVPInputBundle.
  __post_init__`` already enforces every one of those (PR #65 and its
  Codex-review fixes), and this builder does not duplicate any of them.
  A failure of any of those gates raises directly from
  ``BLIMVPInputBundle``'s own constructor and is **not** caught or
  translated here -- this resolves `docs/24` §11's "raise vs. structured
  result" open question the same way PR #65 resolved it for the dataclass
  itself: raise, do not return a structured found/not-found object,
  since every other construction helper in this codebase (the dataclasses
  themselves) already raises on invalid input.
- It performs no pricing, cash-flow generation, schedule generation,
  yield-to-price/price-to-yield conversion, curve interpolation or curve
  *selection* beyond what ``BLIMVPInputBundle`` itself already gates on,
  volatility surface, credit spread model, Treasury FTP parsing,
  ingestion, Bloomberg/API connector, QuantLib adapter, or UI work.

``BondOption``, ``DepositLeg``, ``BondLinkedStructuredProduct``,
``BondReferenceData``, ``resolve_bond_reference_data``,
``is_mvp_pricing_eligible``, ``BLIMarketDataSnapshot``, and
``BLIMVPInputBundle`` are all unmodified by this module.
"""

from __future__ import annotations

from collections.abc import Iterable

from shiori_pricing_lab.data.bli_mvp_input_bundle import BLIMVPInputBundle
from shiori_pricing_lab.data.bli_snapshot import BLIMarketDataSnapshot
from shiori_pricing_lab.products.bond_linked_structured_product import (
    BondLinkedStructuredProduct,
)
from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData
from shiori_pricing_lab.reference_data.resolution import (
    BondResolutionStatus,
    resolve_bond_reference_data,
)


def build_bli_mvp_input_bundle(
    *,
    bundle_id: str,
    valuation_date: str,
    product: BondLinkedStructuredProduct,
    bond_reference_data_universe: Iterable[BondReferenceData],
    market_data_snapshot: BLIMarketDataSnapshot,
) -> BLIMVPInputBundle:
    """Resolve ``product``'s underlying bond and construct a ``BLIMVPInputBundle``.

    ``bond_reference_data_universe`` is passed straight through to
    ``resolve_bond_reference_data`` as its ``fixtures`` argument -- this
    function does not filter, sort, or otherwise pre-process it, and does
    not assume it is the module-level ``SYNTHETIC_BOND_FIXTURES`` default.

    Raises :class:`TypeError` if ``product`` is not a
    ``BondLinkedStructuredProduct`` (checked here, before the ISIN it
    carries is even read, so the failure is clear rather than an
    ``AttributeError`` from deep inside the resolver call).

    Raises :class:`ValueError` if ``resolve_bond_reference_data`` does not
    return ``BondResolutionStatus.FOUND_ELIGIBLE`` -- covering both
    ``NOT_FOUND`` (no matching reference data) and ``FOUND_INELIGIBLE``
    (a matching bond that is not MVP-pricing eligible). The resolver's own
    ``block_reason`` is included in the message. This function never
    silently returns ``None``, never builds a partial bundle, and never
    coerces an ineligible or missing result into an eligible one.

    Raises whatever :class:`ValueError`/:class:`TypeError`
    ``BLIMVPInputBundle.__post_init__`` itself raises for every other gate
    (ISIN cross-checks, currency coherence, valuation-date / as-of
    no-look-ahead, product-specific deposit-rate consistency, required MVP
    curve-purpose presence) -- this function does not catch or re-wrap
    those; see the module docstring for why.

    Also propagates
    :class:`reference_data.resolution.DuplicateBondReferenceDataError`
    unchanged if ``bond_reference_data_universe`` contains more than one
    record sharing the resolved ISIN -- a reference-data-source integrity
    bug, not something this builder resolves on the caller's behalf.
    """

    if not isinstance(product, BondLinkedStructuredProduct):
        raise TypeError("product must be a BondLinkedStructuredProduct")

    underlying_isin = product.bond_option.underlying_isin
    resolution = resolve_bond_reference_data(underlying_isin, bond_reference_data_universe)

    if resolution.status is not BondResolutionStatus.FOUND_ELIGIBLE:
        raise ValueError(
            f"cannot build BLIMVPInputBundle for underlying_isin {underlying_isin!r}: "
            f"resolve_bond_reference_data returned {resolution.status.value} "
            f"({resolution.block_reason})"
        )

    return BLIMVPInputBundle(
        bundle_id=bundle_id,
        valuation_date=valuation_date,
        product=product,
        resolved_bond_reference_data=resolution.bond_reference_data,
        resolution_status=resolution.status,
        eligibility_reasons=resolution.eligibility_reasons,
        market_data_snapshot=market_data_snapshot,
    )
