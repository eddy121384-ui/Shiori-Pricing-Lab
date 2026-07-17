"""``build_bli_standalone_option_request``: typed standalone pricing-input builder (Issue #96).

Scope, per Issue #96's "approve one typed-builder implementation slice"
binding decision: one small function that resolves a bond option's
underlying bond against a supplied reference-data universe (via the existing
``reference_data.resolution.resolve_bond_reference_data``), assembles a
``BLIMarketDataSnapshot`` from already-typed component objects and explicit
snapshot metadata, and constructs the standalone
``BLIStandaloneBondOptionRequest`` that ``price_bli_mvp_standalone_option``
consumes (merged #105). It is the typed pricing-input construction boundary
for manual/local sources -- provider-specific code and raw dict/JSON parsing
stay outside it (that is #97's adapter concern, not this slice).

**Caller-remediation contract (Issue #96 decision §1) mirrors the existing
``build_bli_mvp_input_bundle`` convention exactly -- no new result dataclass
or status enum is introduced:**

- a successful build returns a ``BLIStandaloneBondOptionRequest`` directly;
- a ``NOT_FOUND`` / ``FOUND_INELIGIBLE`` resolver outcome is mapped to one
  clear ``ValueError`` carrying the resolver's own ``status`` and
  ``block_reason`` -- this function never silently returns ``None``, never
  fabricates a request from a non-eligible resolution, and never coerces an
  ineligible/missing result into an eligible one;
- ``reference_data.resolution.DuplicateBondReferenceDataError`` propagates
  unchanged (a reference-data-source integrity bug, not a lookup outcome);
- every other failure -- ``BLIMarketDataSnapshot`` construction
  (duplicate/conflicting curve node, ambiguous curve identity, stale/invalid
  status, ...) and ``BLIStandaloneBondOptionRequest`` construction
  (reference-data eligibility re-check, ISIN/currency/valuation-date
  coherence, as-of no-look-ahead, required option-leg curve purposes) --
  raises **directly from those existing constructors** and is neither caught
  nor re-wrapped here.

**This module adds no financial validation of its own.** Every unit, quote
side, curve-identity, duplicate-node, ambiguous-curve, stale-status,
rate-basis, and vol-basis rule is already enforced by the reviewed
``BLIMarketDataSnapshot`` / component schemas and by the standalone request
guard/engine; this builder only composes those reviewed types.

**Timestamp boundary (Issue #96 decision §2):** ``as_of_timestamp`` is the
source observation / source-as-of value and is stored unchanged in
``BLIMarketDataSnapshot.as_of_timestamp``. This builder performs no
retrieval, reads no system clock, and neither invents nor stores a
``retrieved_at`` -- that separate provenance field remains a later
acquisition/quote-lifecycle concern (#97), not dropped and not folded into
``as_of_timestamp``.

**Construction-vs-pricing boundary is unchanged from #95/#105 (decision
§4):** the builder does not narrow a valid general ``BondOption`` /
component to the supported *pricing* shape. A ``YIELD_VOL`` volatility
input, a non-``CONTINUOUS_ZERO_RATE`` curve basis, American exercise, a
yield payoff, physical settlement, and a yield-only bond quote all remain
**constructible** here where their own schemas allow them, and are then
rejected downstream by the existing guard/engine as explicit ``FAILED``
results -- exactly as #105 pinned.

**Hard non-goals (Issue #96 scope cap):** no new normalized-record schema,
no raw dict/JSON parsing, no Bloomberg names/API, no UI, no persistence, no
benchmark comparison, no calibration, no curve construction/extrapolation,
no yield-vol conversion, no fallback/hidden default, no system clock.
``BondOption``, ``BondReferenceData``, ``resolve_bond_reference_data``,
``BLIMarketDataSnapshot`` (and its component classes), and
``BLIStandaloneBondOptionRequest`` are all unmodified.
"""

from __future__ import annotations

from collections.abc import Iterable

from shiori_pricing_lab.data.bli_snapshot import (
    BLIBondQuote,
    BLICreditSpreadInput,
    BLICurvePoint,
    BLIDepositRateObservation,
    BLIMarketDataSnapshot,
    BLIMarketDataStatus,
    BLIVolatilityInput,
)
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.products.bond_option import BondOption
from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData
from shiori_pricing_lab.reference_data.resolution import (
    BondResolutionStatus,
    resolve_bond_reference_data,
)


def build_bli_standalone_option_request(
    *,
    bond_option: BondOption,
    bond_reference_data_universe: Iterable[BondReferenceData],
    valuation_date: str,
    as_of_timestamp: str,
    source_system: str,
    snapshot_id: str,
    snapshot_status: BLIMarketDataStatus,
    bond_quote: BLIBondQuote,
    curve_points: Iterable[BLICurvePoint],
    volatility_input: BLIVolatilityInput,
    credit_spread_input: BLICreditSpreadInput,
    pricing_timestamp: str,
    expiry_timestamp: str,
    reporting_date: str,
    forward_settlement_date: str,
    option_settlement_date: str,
    deposit_rate_observation: BLIDepositRateObservation | None = None,
    bond_reference_source_name: str | None = None,
) -> BLIStandaloneBondOptionRequest:
    """Resolve ``bond_option``'s underlying bond and build a standalone request.

    Accepts only already-typed contracts and explicit snapshot metadata (see
    the module docstring). ``bond_reference_data_universe`` is passed
    straight through to ``resolve_bond_reference_data`` -- this function does
    not filter, sort, normalize, or apply any fallback to it.
    ``bond_reference_source_name`` is forwarded only as the resolver's
    audit/source label (its ``source_fixture_name``); it never affects
    matching.

    ``pricing_timestamp``, ``expiry_timestamp``, ``reporting_date``,
    ``forward_settlement_date``, and ``option_settlement_date`` (Issue #94
    human methodology approval, comment 5001749998) are forwarded verbatim
    to ``BLIStandaloneBondOptionRequest`` -- this builder computes, derives,
    or defaults none of them; its own coherence/format validation raises
    directly from that constructor.

    Raises :class:`TypeError` if ``bond_option`` is not a ``BondOption`` --
    checked before its ``underlying_isin`` is read, so the failure is clear
    rather than an ``AttributeError`` from deep inside the resolver call.

    Raises :class:`ValueError` if ``resolve_bond_reference_data`` does not
    return ``BondResolutionStatus.FOUND_ELIGIBLE`` -- covering both
    ``NOT_FOUND`` and ``FOUND_INELIGIBLE`` -- with the resolver's own status
    and ``block_reason`` in the message.

    Propagates :class:`reference_data.resolution.DuplicateBondReferenceDataError`
    unchanged if ``bond_reference_data_universe`` contains more than one
    record sharing the resolved ISIN.

    Propagates unchanged whatever :class:`ValueError` / :class:`TypeError`
    ``BLIMarketDataSnapshot.__post_init__`` and
    ``BLIStandaloneBondOptionRequest.__post_init__`` themselves raise for
    every other gate -- this builder duplicates none of those checks.
    """

    if not isinstance(bond_option, BondOption):
        raise TypeError("bond_option must be a BondOption")

    underlying_isin = bond_option.underlying_isin
    resolution = resolve_bond_reference_data(
        underlying_isin,
        bond_reference_data_universe,
        source_fixture_name=bond_reference_source_name,
    )

    if resolution.status is not BondResolutionStatus.FOUND_ELIGIBLE:
        raise ValueError(
            f"cannot build BLIStandaloneBondOptionRequest for underlying_isin "
            f"{underlying_isin!r}: resolve_bond_reference_data returned "
            f"{resolution.status.value} ({resolution.block_reason})"
        )

    market_data_snapshot = BLIMarketDataSnapshot(
        valuation_date=valuation_date,
        as_of_timestamp=as_of_timestamp,
        source_system=source_system,
        snapshot_id=snapshot_id,
        status=snapshot_status,
        bond_quote=bond_quote,
        curve_points=tuple(curve_points),
        volatility_input=volatility_input,
        credit_spread_input=credit_spread_input,
        deposit_rate_observation=deposit_rate_observation,
    )

    return BLIStandaloneBondOptionRequest(
        bond_option=bond_option,
        resolved_bond_reference_data=resolution.bond_reference_data,
        valuation_date=valuation_date,
        market_data_snapshot=market_data_snapshot,
        pricing_timestamp=pricing_timestamp,
        expiry_timestamp=expiry_timestamp,
        reporting_date=reporting_date,
        forward_settlement_date=forward_settlement_date,
        option_settlement_date=option_settlement_date,
    )
