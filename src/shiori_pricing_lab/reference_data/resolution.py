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
    a block reason consistent with ``status``/``eligibility_reasons``
    (``None`` only when ``FOUND_ELIGIBLE``), and an audit-only label for
    which reference-data source was searched. No market-data field
    (``docs/21`` §7's exclusion list) lives here.

    ``BondReferenceResolutionResult`` is a public, directly constructible
    type (Codex P2 review of PR #60): ``__post_init__`` enforces that
    ``block_reason`` cannot contradict ``status``/``eligibility_reasons``
    even when a caller builds one by hand rather than through
    :func:`resolve_bond_reference_data` -- e.g. it rejects a
    ``FOUND_INELIGIBLE`` result whose ``block_reason`` silently drops a
    reason.
    """

    requested_isin: str
    status: BondResolutionStatus
    bond_reference_data: BondReferenceData | None
    eligibility_reasons: tuple[str, ...]
    block_reason: str | None
    source_fixture_name: str

    def __post_init__(self) -> None:
        if self.status == BondResolutionStatus.FOUND_ELIGIBLE:
            if self.bond_reference_data is None:
                raise ValueError(
                    "bond_reference_data must not be None when status is FOUND_ELIGIBLE"
                )
            if self.eligibility_reasons != ():
                raise ValueError(
                    "eligibility_reasons must be empty when status is FOUND_ELIGIBLE, "
                    f"got {self.eligibility_reasons!r}"
                )
            if self.block_reason is not None:
                raise ValueError(
                    f"block_reason must be None when status is FOUND_ELIGIBLE, "
                    f"got {self.block_reason!r}"
                )
        elif self.status == BondResolutionStatus.FOUND_INELIGIBLE:
            if self.bond_reference_data is None:
                raise ValueError(
                    "bond_reference_data must not be None when status is FOUND_INELIGIBLE"
                )
            if not self.eligibility_reasons:
                raise ValueError(
                    "eligibility_reasons must be non-empty when status is FOUND_INELIGIBLE"
                )
            expected_block_reason = "; ".join(self.eligibility_reasons)
            if self.block_reason != expected_block_reason:
                raise ValueError(
                    "block_reason must equal '; '.join(eligibility_reasons) "
                    f"({expected_block_reason!r}) when status is FOUND_INELIGIBLE, "
                    f"got {self.block_reason!r}"
                )
        else:  # BondResolutionStatus.NOT_FOUND
            if self.bond_reference_data is not None:
                raise ValueError(
                    "bond_reference_data must be None when status is NOT_FOUND"
                )
            if self.eligibility_reasons != ():
                raise ValueError(
                    f"eligibility_reasons must be empty when status is NOT_FOUND, "
                    f"got {self.eligibility_reasons!r}"
                )
            if not self.block_reason:
                raise ValueError(
                    "block_reason must be a non-blank string when status is NOT_FOUND"
                )


def _resolve_source_fixture_name(
    fixtures: Iterable[BondReferenceData],
    source_fixture_name: str | None,
) -> str:
    """Return a truthful audit label for ``fixtures`` (Codex P2 review of PR #60).

    An explicit ``source_fixture_name`` always wins. Otherwise, the label
    is only ``"SYNTHETIC_BOND_FIXTURES"`` when ``fixtures`` genuinely *is*
    that module-level object (identity, not equality, since a caller
    could coincidentally pass an equal-valued but different iterable);
    any other caller-supplied iterable is labeled generically rather than
    mislabeled as the synthetic MVP fixture.
    """

    if source_fixture_name is not None:
        return source_fixture_name
    if fixtures is SYNTHETIC_BOND_FIXTURES:
        return "SYNTHETIC_BOND_FIXTURES"
    return "caller_supplied_fixtures"


def resolve_bond_reference_data(
    underlying_isin: str,
    fixtures: Iterable[BondReferenceData] = SYNTHETIC_BOND_FIXTURES,
    *,
    source_fixture_name: str | None = None,
) -> BondReferenceResolutionResult:
    """Resolve ``underlying_isin`` against ``fixtures`` by exact ISIN match.

    ``fixtures`` defaults to the MVP synthetic fixture but is a plain
    parameter (docs/21 §3) so a future real reference-data source can be
    substituted without changing this function. ``source_fixture_name``
    is an audit-only label describing where ``fixtures`` came from; it is
    never derived from ``fixtures``' *contents* (a plain iterable has no
    name of its own). Leaving ``source_fixture_name`` unset resolves to
    ``"SYNTHETIC_BOND_FIXTURES"`` only when ``fixtures`` is genuinely the
    module-level default; any other unlabeled iterable resolves to the
    generic ``"caller_supplied_fixtures"`` rather than being mislabeled as
    the synthetic MVP fixture (Codex P2 review of PR #60) -- a caller
    resolving against a real or point-in-time-versioned source should pass
    its own ``source_fixture_name``.

    Raises :class:`DuplicateBondReferenceDataError` if more than one
    record in ``fixtures`` shares ``underlying_isin``. Never raises for a
    legitimately missing ISIN -- that is the ``NOT_FOUND`` status.
    """

    resolved_source_fixture_name = _resolve_source_fixture_name(fixtures, source_fixture_name)

    matches = [bond for bond in fixtures if bond.isin == underlying_isin]

    if len(matches) > 1:
        raise DuplicateBondReferenceDataError(
            f"{len(matches)} BondReferenceData records in {resolved_source_fixture_name} "
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
                f"in {resolved_source_fixture_name}"
            ),
            source_fixture_name=resolved_source_fixture_name,
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
            source_fixture_name=resolved_source_fixture_name,
        )

    return BondReferenceResolutionResult(
        requested_isin=underlying_isin,
        status=BondResolutionStatus.FOUND_INELIGIBLE,
        bond_reference_data=bond,
        eligibility_reasons=eligibility.reasons,
        block_reason="; ".join(eligibility.reasons),
        source_fixture_name=resolved_source_fixture_name,
    )
