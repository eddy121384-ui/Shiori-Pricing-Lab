"""Headless JSON/manual standalone bond-option pricing workflow (Issue #97, PR A).

The offline, UI-free workflow behind the trader workbench: parse one local
standalone bond-option case (a JSON string or an already-parsed mapping),
construct the existing typed objects with their existing constructors, and
drive them through the **only** approved construction/pricing path --
``build_bli_standalone_option_request`` (#96/#106) then
``price_bli_mvp_standalone_option`` (#95/#105) -- returning the existing
``BLIStandaloneBondOptionRequest`` / ``PricingResult`` plus a bounded,
verbatim display-context dict.

**This module reimplements no pricing, curve, discounting, accrual,
volatility, validation, or error-mapping logic.** Every financial rule is
enforced by the existing schemas / builder / guard / engine; every displayed
value is a direct read from the returned ``PricingResult`` / request /
snapshot. It reads no system clock, makes no provider/network call, and
never fabricates a replacement premium.

**Approved JSON envelope (Issue #97 PR A binding decision).** The case is a
JSON object with exactly these top-level keys:

- required: ``bond_option``, ``bond_reference_data_universe``,
  ``valuation_date``, ``as_of_timestamp``, ``source_system``,
  ``snapshot_id``, ``snapshot_status``, ``bond_quote``, ``curve_points``,
  ``volatility_input``, ``credit_spread_input``;
- optional: ``deposit_rate_observation`` (may be omitted or ``null``; no
  Deposit Curve is required), ``bond_reference_source_name``.

``bond_reference_data_universe`` and ``curve_points`` are JSON **arrays** of
objects. Every nested object mirrors the existing dataclass field names
exactly, and every enum is its existing ``StrEnum.value`` string. There are
**no aliases, hidden defaults, unit conversions, fallbacks, case/timestamp
normalization, or inferred fields**: an unknown or missing top-level key is
rejected explicitly, and any nested field/enum problem raises directly from
the existing typed constructor, unremapped.

**Timestamp boundary (Issue #97, carried from #94/#96).** ``as_of_timestamp``
is the source observation / source-as-of value and flows unchanged into the
snapshot. ``retrieved_at`` is **not** part of the JSON envelope and is not
added to any pricing/snapshot/request schema: it is an explicit
caller-supplied workbench-context value only, copied verbatim into the
display context under its own key, never read from a clock, and never used
to overwrite or reinterpret ``as_of_timestamp``.

**Failure contract.** A malformed JSON string raises the standard
``json.JSONDecodeError``; a missing/unknown top-level key raises a clear
``ValueError``; nested schema / builder ``TypeError`` / ``ValueError``
propagate unremapped. A pricing ``FAILED`` remains a returned
``PricingResult`` -- the display context preserves ``pv=None`` and the
original ``errors`` and never invents a replacement value. No new
result/status dataclass or error envelope is introduced.

The workbench never labels the model fair premium a client quote.
"""

from __future__ import annotations

import json

from shiori_pricing_lab.data.bli_snapshot import (
    BLIBondQuote,
    BLICreditSpreadInput,
    BLICurvePoint,
    BLIDepositRateObservation,
    BLIVolatilityInput,
)
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.data.bli_standalone_option_request_builder import (
    build_bli_standalone_option_request,
)
from shiori_pricing_lab.pricing.bli_pricing_engine import price_bli_mvp_standalone_option
from shiori_pricing_lab.pricing.result import PricingResult
from shiori_pricing_lab.products.bond_option import BondOption
from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData

_REQUIRED_TOP_LEVEL_KEYS = frozenset(
    {
        "bond_option",
        "bond_reference_data_universe",
        "valuation_date",
        "as_of_timestamp",
        "source_system",
        "snapshot_id",
        "snapshot_status",
        "bond_quote",
        "curve_points",
        "volatility_input",
        "credit_spread_input",
    }
)
_OPTIONAL_TOP_LEVEL_KEYS = frozenset(
    {"deposit_rate_observation", "bond_reference_source_name"}
)
_ALLOWED_TOP_LEVEL_KEYS = _REQUIRED_TOP_LEVEL_KEYS | _OPTIONAL_TOP_LEVEL_KEYS


def _parse_standalone_option_case(case: str | dict) -> dict:
    """Return the validated top-level envelope mapping for ``case``.

    ``case`` is either a JSON string (parsed with ``json.loads`` -- a
    malformed string raises the standard ``json.JSONDecodeError``) or an
    already-parsed mapping. Rejects a non-object top level, any missing
    required top-level key, and any unknown top-level key -- each explicitly,
    never silently ignored. Performs no other validation: every nested
    field/enum rule belongs to the existing typed constructors.
    """

    envelope = json.loads(case) if isinstance(case, str) else case

    if not isinstance(envelope, dict):
        raise ValueError(
            "standalone option case must be a JSON object at the top level, got "
            f"{type(envelope).__name__}"
        )

    keys = set(envelope)
    missing = _REQUIRED_TOP_LEVEL_KEYS - keys
    if missing:
        raise ValueError(
            f"standalone option case is missing required top-level key(s): {sorted(missing)}"
        )
    unknown = keys - _ALLOWED_TOP_LEVEL_KEYS
    if unknown:
        raise ValueError(
            f"standalone option case has unknown top-level key(s): {sorted(unknown)}"
        )
    return envelope


def build_request_from_standalone_option_case(
    case: str | dict,
) -> BLIStandaloneBondOptionRequest:
    """Parse ``case`` and build the standalone request via the approved builder.

    Constructs each typed component with its existing constructor (field
    names mirror the dataclasses exactly; enum strings are coerced by the
    constructors), then calls ``build_bli_standalone_option_request`` -- the
    only request-construction path. Raises ``ValueError`` for envelope-level
    problems (see :func:`_parse_standalone_option_case`) and propagates every
    nested schema / builder ``TypeError`` / ``ValueError`` unremapped.
    ``bond_reference_data_universe`` and ``curve_points`` must be JSON arrays;
    a non-array raises a clear ``ValueError`` rather than being silently
    wrapped.
    """

    envelope = _parse_standalone_option_case(case)

    bond_option = BondOption(**envelope["bond_option"])

    universe_raw = envelope["bond_reference_data_universe"]
    if not isinstance(universe_raw, list):
        raise ValueError(
            "bond_reference_data_universe must be a JSON array of BondReferenceData objects"
        )
    bond_reference_data_universe = [BondReferenceData(**record) for record in universe_raw]

    curve_points_raw = envelope["curve_points"]
    if not isinstance(curve_points_raw, list):
        raise ValueError("curve_points must be a JSON array of BLICurvePoint objects")
    curve_points = [BLICurvePoint(**point) for point in curve_points_raw]

    bond_quote = BLIBondQuote(**envelope["bond_quote"])
    volatility_input = BLIVolatilityInput(**envelope["volatility_input"])
    credit_spread_input = BLICreditSpreadInput(**envelope["credit_spread_input"])

    deposit_raw = envelope.get("deposit_rate_observation")
    deposit_rate_observation = (
        BLIDepositRateObservation(**deposit_raw) if deposit_raw is not None else None
    )

    return build_bli_standalone_option_request(
        bond_option=bond_option,
        bond_reference_data_universe=bond_reference_data_universe,
        valuation_date=envelope["valuation_date"],
        as_of_timestamp=envelope["as_of_timestamp"],
        source_system=envelope["source_system"],
        snapshot_id=envelope["snapshot_id"],
        snapshot_status=envelope["snapshot_status"],
        bond_quote=bond_quote,
        curve_points=curve_points,
        volatility_input=volatility_input,
        credit_spread_input=credit_spread_input,
        deposit_rate_observation=deposit_rate_observation,
        bond_reference_source_name=envelope.get("bond_reference_source_name"),
    )


def prepare_standalone_display(
    result: PricingResult,
    request: BLIStandaloneBondOptionRequest,
    retrieved_at: str | None = None,
) -> dict:
    """Return a bounded display context read **verbatim** from ``result``/``request``.

    No pricing math, unit conversion, or error re-mapping: every value is a
    direct field read (``assumptions.get`` only tolerates keys being absent
    on a ``FAILED`` result -- it never computes a substitute). ``retrieved_at``
    is copied verbatim under its own key and defaults to ``None``; it is a
    caller-supplied workbench value, kept strictly separate from
    ``source_as_of`` (the snapshot's ``as_of_timestamp``) and never sourced
    from a clock. ``pv`` (total notional model fair premium) stays ``None`` on
    a failed result, and each original error is preserved **verbatim and
    complete** -- ``code``, ``message``, and the full structured ``detail``
    (e.g. ``product_id`` / ``reasons`` / ``exception_type``) exactly as the
    engine produced them, never interpreted or remapped and never fabricating
    a replacement value. The model fair premium is never labeled a client
    quote.
    """

    assumptions = result.assumptions
    snapshot = request.market_data_snapshot
    return {
        "status": result.status.value,
        "method": result.method,
        "product_id": result.product_id,
        "product_type": result.product_type,
        "valuation_date": result.valuation_date,
        "result_currency": result.result_currency,
        # Premium: per-100 and total notional exposed as separate fields.
        "model_fair_premium_per_100": assumptions.get("black76_pv_per_100"),
        "total_notional_model_fair_premium": result.pv,
        "forward_clean_price_per_100": assumptions.get("forward_clean_price_per_100"),
        "black76_pv_per_100": assumptions.get("black76_pv_per_100"),
        "option_discount_factor": assumptions.get("option_discount_factor"),
        "time_to_expiry_year_fraction": assumptions.get("time_to_expiry_year_fraction"),
        "pv_scaling_formula": assumptions.get("pv_scaling_formula"),
        "priced_component": assumptions.get("priced_component"),
        "priced_component_scope": assumptions.get("priced_component_scope"),
        "excluded_components": assumptions.get("excluded_components"),
        "assumptions": assumptions,
        # Provenance / reproducibility (from the request's snapshot + result).
        "source_system": snapshot.source_system,
        "source_as_of": result.market_data_as_of,
        "retrieved_at": retrieved_at,
        "snapshot_id": snapshot.snapshot_id,
        "engine_name": result.engine_name,
        "engine_version": result.engine_version,
        "errors": [
            {
                "code": message.code.value,
                "message": message.message,
                "detail": message.detail,
            }
            for message in result.errors
        ],
    }


def price_standalone_option_case(
    case: str | dict,
    *,
    retrieved_at: str | None = None,
) -> tuple[BLIStandaloneBondOptionRequest, PricingResult, dict]:
    """Parse, build, price, and prepare display for one standalone option ``case``.

    Convenience over :func:`build_request_from_standalone_option_case`,
    :func:`price_bli_mvp_standalone_option`, and
    :func:`prepare_standalone_display`. Returns the existing
    ``BLIStandaloneBondOptionRequest``, the existing ``PricingResult`` (a
    ``FAILED`` result is returned as-is, never converted to a fabricated
    success), and the bounded display context. Envelope / schema / builder
    failures propagate from the build step unremapped; ``retrieved_at`` is
    caller-supplied and flows only into the display context.
    """

    request = build_request_from_standalone_option_case(case)
    result = price_bli_mvp_standalone_option(request)
    display = prepare_standalone_display(result, request, retrieved_at=retrieved_at)
    return request, result, display
