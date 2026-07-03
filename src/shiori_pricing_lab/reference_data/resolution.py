"""ISIN -> ``BondReferenceData`` resolution (docs/21 BLI MVP resolution slice).

Implements the minimal resolver `docs/21_bli_isin_resolution_preflight.md`
§8 recommended: given a `BondOption.underlying_isin` string and a caller
-supplied iterable of `BondReferenceData` records, is there an exact
matching bond, and is it MVP-pricing-eligible?

This module answers exactly the four questions `docs/21` §6 allows and no
others: found / not found; the reference-data record (if found);
eligible / ineligible (if found), via the existing
`reference_data.eligibility.is_mvp_pricing_eligible`; and the blocking
reason (if not found, or found-but-ineligible). It does **not**
re-implement any eligibility rule (callable / sinkable / zero-coupon /
`OTHER` yield convention / non-vanilla `bond_type` / inactive status all
stay defined exactly once, in `eligibility.py`), and it does not price,
generate cashflows, or build a schedule.

**Exact match only** (`docs/21` §4): comparison is plain `str ==` on
`BondReferenceData.isin`. No case normalization beyond what `==` already
does, no whitespace trimming, no check-digit correction, no partial or
prefix matching, and no fallback/first-match behavior. A typo'd ISIN is a
**not-found** result, never a near-match hit.

**Duplicate ISIN is a contract violation, not a lookup outcome**
(`docs/21` §4): if more than one record in the supplied iterable shares
the requested `isin`, this is a fixture/source data-integrity bug, so
`resolve_bond_reference_data` raises `DuplicateBondReferenceDataError`
rather than silently choosing the first or last match. This module
defines its own small exception rather than importing
`shiori_pricing_lab.pricing.errors`: `reference_data` is a sibling
package to `pricing`, not a consumer of it (mirroring the same
independence already established from `products` in
`reference_data/__init__.py`), so it does not take on a pricing-layer
dependency for one small contract-violation exception.

**Point-in-time boundary** (`docs/21` §7.1): this resolver never reasons
about a valuation date. For the current MVP, `SYNTHETIC_BOND_FIXTURES`
is static and carries no valuation-date dimension. For any future
historical valuation or real reference-data source, the caller / a
future input-resolution layer is responsible for supplying an
already point-in-time / as-of-correct `fixtures` iterable *before*
calling this resolver — the resolver itself must never, and does not,
choose "the latest" reference data, and it carries no `business_date`,
`valuation_date`, or `as_of_timestamp` field anywhere in its inputs or
result.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData
from shiori_pricing_lab.reference_data.eligibility import is_mvp_pricing_eligible
from shiori_pricing_lab.reference_data.fixtures import SYNTHETIC_BOND_FIXTURES


class DuplicateBondReferenceDataError(Exception):
    """Raised when more than one supplied ``BondReferenceData`` shares an ISIN.

    This is a fixture / reference-data-source integrity bug, not a normal
    resolution outcome -- it must never be resolved by silently returning
    the first or last matching record (docs/21 §4).
    """


class BondResolutionStatus(StrEnum):
    """Outcome of resolving one ISIN against a reference-data iterable.

    Only three states exist, matching `docs/21` §8.1. A duplicate ISIN is
    not a fourth status -- it is a raised
    :class:`DuplicateBondReferenceDataError`, not a value a caller could
    silently branch on.
    """

    FOUND_ELIGIBLE = "FOUND_ELIGIBLE"
    FOUND_INELIGIBLE = "FOUND_INELIGIBLE"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True)
class BondReferenceResolutionResult:
    """Structured outcome of one ``resolve_bond_reference_data`` call.

    Carries exactly what `docs/21` §8.1 sketches: the requested ISIN, the
    resolution status, the matched record (``None`` unless
    ``FOUND_ELIGIBLE``/``FOUND_INELIGIBLE``), every eligibility reason
    from ``is_mvp_pricing_eligible`` (empty when eligible or not found),
    a short derived block reason (``None`` only when ``FOUND_ELIGIBLE``),
    and an audit-only label for which reference-data source was searched.
    No market-data field (``docs/21`` §7's exclusion list) lives here.
    """

    requested_isin: str
    status: BondResolutionStatus
    bond_reference_data: BondReferenceData | None
    eligibility_reasons: tuple[str, ...]
    block_reason: str | None
    source_fixture_name: str


def resolve_bond_reference_data(
    underlying_isin: str,
    fixtures: Iterable[BondReferenceData] = SYNTHETIC_BOND_FIXTURES,
    *,
    source_fixture_name: str = "SYNTHETIC_BOND_FIXTURES",
) -> BondReferenceResolutionResult:
    """Resolve ``underlying_isin`` against ``fixtures`` by exact ISIN match.

    ``fixtures`` defaults to the MVP synthetic fixture but is a plain
    parameter (docs/21 §3) so a future real reference-data source can be
    substituted without changing this function. ``source_fixture_name``
    is an audit-only label describing where ``fixtures`` came from; it is
    never derived from ``fixtures`` itself (a plain iterable has no name
    of its own) -- a caller passing a non-default ``fixtures`` should
    also pass a matching ``source_fixture_name``.

    Raises :class:`DuplicateBondReferenceDataError` if more than one
    record in ``fixtures`` shares ``underlying_isin``. Never raises for a
    legitimately missing ISIN -- that is the ``NOT_FOUND`` status.
    """

    matches = [bond for bond in fixtures if bond.isin == underlying_isin]

    if len(matches) > 1:
        raise DuplicateBondReferenceDataError(
            f"{len(matches)} BondReferenceData records in {source_fixture_name} "
            f"share isin {underlying_isin!r} -- duplicate ISIN is a fixture "
            "data-integrity bug, not a normal lookup outcome (docs/21 §4)"
        )

    if not matches:
        return BondReferenceResolutionResult(
            requested_isin=underlying_isin,
            status=BondResolutionStatus.NOT_FOUND,
            bond_reference_data=None,
            eligibility_reasons=(),
            block_reason=(
                f"no BondReferenceData found for isin {underlying_isin!r} "
                f"in {source_fixture_name}"
            ),
            source_fixture_name=source_fixture_name,
        )

    bond = matches[0]
    eligibility = is_mvp_pricing_eligible(bond)

    if eligibility.eligible:
        return BondReferenceResolutionResult(
            requested_isin=underlying_isin,
            status=BondResolutionStatus.FOUND_ELIGIBLE,
            bond_reference_data=bond,
            eligibility_reasons=(),
            block_reason=None,
            source_fixture_name=source_fixture_name,
        )

    return BondReferenceResolutionResult(
        requested_isin=underlying_isin,
        status=BondResolutionStatus.FOUND_INELIGIBLE,
        bond_reference_data=bond,
        eligibility_reasons=eligibility.reasons,
        block_reason="; ".join(eligibility.reasons),
        source_fixture_name=source_fixture_name,
    )
