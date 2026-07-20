"""Headless JSON/manual standalone bond-option pricing workflow (Issue #97, PR A;
Issue #125 benchmark comparison / implied PRICE_VOL extension).

The offline, UI-free workflow behind the trader workbench: parse one local
standalone bond-option case (a JSON string or an already-parsed mapping),
construct the existing typed objects with their existing constructors, and
drive them through the **only** approved construction/pricing path --
``build_bli_standalone_option_request`` (#96/#106) then
``price_bli_mvp_standalone_option`` (#95/#105) -- returning the existing
``BLIStandaloneBondOptionRequest`` / ``PricingResult`` plus a bounded,
verbatim display-context dict.

**Issue #125: bounded benchmark-comparison / implied-PRICE_VOL orchestration.**
A second, entirely separate JSON envelope -- mirroring ``BLIBenchmarkQuote``'s
fields exactly, never merged into the pricing-case envelope -- can be parsed
into the existing ``BLIBenchmarkQuote`` via
:func:`build_benchmark_from_standalone_option_benchmark_case`. The bounded
:func:`price_standalone_option_case_with_benchmark` workflow reuses
:func:`price_standalone_option_case` unchanged for pricing, then calls the
existing, unmodified ``compare_bli_benchmark`` (#98 PR B) and
``calibrate_bli_implied_price_vol`` (#99/#P1-06) exactly once each, requiring
an explicit ``active_quote_side`` with no hidden default. This module adds no
pricing, comparison, calibration, resolver, solver, curve, discounting,
accrued-interest, or volatility math of its own -- every financial rule and
every displayed value still comes verbatim from the three existing reviewed
functions.

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
  ``volatility_input``, ``credit_spread_input``,
  ``forward_clean_price_input`` (Issue #94 -- a nested object mirroring
  ``BLIForwardCleanPriceInput``: the explicit forward clean price the
  OVME-aligned standalone path prices from, never reconstructed from a
  spot price and a Bond Reference Curve), ``pricing_timestamp``,
  ``expiry_timestamp``, ``reporting_date``, ``forward_settlement_date``,
  ``option_settlement_date`` (Issue #94 human methodology approval,
  comment 5001749998 -- forwarded verbatim to
  ``BLIStandaloneBondOptionRequest``, computed/derived/defaulted by
  neither this envelope parser nor the builder);
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

**Issue #6 Phase 3: live Bloomberg bond-quote wiring.** Two bounded headless
workflows -- :func:`price_standalone_option_case_with_bloomberg_quote` and
:func:`price_standalone_option_case_with_bloomberg_quote_and_benchmark` --
replace only a case's ``bond_quote`` with one live quote from the existing,
unmodified ``load_bloomberg_bond_quote`` (``data/bloomberg_bond_quote.py``)
before pricing through the same approved path as the manual-JSON workflow.
``source_as_of`` must equal the case envelope's ``as_of_timestamp`` exactly
before any Bloomberg call is made -- no normalization, no timezone
conversion, no system clock, no "close enough" rule. The expected ISIN
comes from the case's own ``bond_option.underlying_isin``; Bloomberg's
``ID_ISIN`` is verified against it by the loader itself (Issue #6 Phase 2).
The original case JSON's ``bond_quote`` is never read, never used as a
fallback, and never merged field-by-field with the live quote -- it is
unconditionally replaced on success and never touched on failure, since a
Bloomberg failure raises before the case's ``bond_quote`` is ever
referenced. In benchmark mode, the one live-quote ``quote_side`` is reused
verbatim as ``active_quote_side`` for both ``compare_bli_benchmark`` and
``calibrate_bli_implied_price_vol`` -- no second side, override, or
fallback. This slice adds no forward, repo, ``PRICE_VOL``, OVME, curve, or
pricing-methodology logic of its own.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from shiori_pricing_lab.data.bli_benchmark_quote import BLIBenchmarkQuote, BLIBenchmarkQuoteSide
from shiori_pricing_lab.data.bli_snapshot import (
    BLIBondQuote,
    BLICreditSpreadInput,
    BLICurvePoint,
    BLIDepositRateObservation,
    BLIForwardCleanPriceInput,
    BLIVolatilityInput,
)
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.data.bli_standalone_option_request_builder import (
    build_bli_standalone_option_request,
)
from shiori_pricing_lab.data.bloomberg_bond_quote import load_bloomberg_bond_quote
from shiori_pricing_lab.pricing.bli_benchmark_comparison import (
    BLIBenchmarkComparisonResult,
    compare_bli_benchmark,
)
from shiori_pricing_lab.pricing.bli_implied_price_vol_calibration import (
    BLIImpliedPriceVolCalibrationResult,
    calibrate_bli_implied_price_vol,
)
from shiori_pricing_lab.pricing.bli_pricing_engine import price_bli_mvp_standalone_option
from shiori_pricing_lab.pricing.result import PricingResult
from shiori_pricing_lab.products.bond_option import BondOption
from shiori_pricing_lab.products.enums import TreasuryFTPQuoteSide
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
        "forward_clean_price_input",
        "pricing_timestamp",
        "expiry_timestamp",
        "reporting_date",
        "forward_settlement_date",
        "option_settlement_date",
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
    forward_clean_price_input = BLIForwardCleanPriceInput(
        **envelope["forward_clean_price_input"]
    )

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
        forward_clean_price_input=forward_clean_price_input,
        pricing_timestamp=envelope["pricing_timestamp"],
        expiry_timestamp=envelope["expiry_timestamp"],
        reporting_date=envelope["reporting_date"],
        forward_settlement_date=envelope["forward_settlement_date"],
        option_settlement_date=envelope["option_settlement_date"],
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
        "effective_reporting_date_discount_factor": assumptions.get(
            "effective_reporting_date_discount_factor"
        ),
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


# --- Issue #125: benchmark comparison / implied PRICE_VOL orchestration ----------

# Exactly the BLIBenchmarkQuote dataclass field names (data/bli_benchmark_quote.py)
# -- including the optional-on-the-dataclass ``notes`` field, which this envelope
# still requires present (``null`` is an accepted value for it, exactly like the
# dataclass itself accepts ``None``). No alias, no default, no field this
# envelope adds or drops relative to the dataclass.
_BLI_BENCHMARK_QUOTE_KEYS = frozenset(
    {
        "benchmark_id",
        "source_type",
        "source_system",
        "source_as_of",
        "retrieved_at",
        "quote_side",
        "premium_per_100",
        "total_premium",
        "currency",
        "product_id",
        "snapshot_id",
        "underlying_id",
        "source_reference",
        "notes",
    }
)


def _parse_standalone_option_benchmark_case(benchmark_case: str | dict) -> dict:
    """Return the validated top-level envelope mapping for ``benchmark_case``.

    ``benchmark_case`` is either a JSON string (parsed with ``json.loads`` --
    a malformed string raises the standard ``json.JSONDecodeError``) or an
    already-parsed mapping. The top-level key set must equal
    ``BLIBenchmarkQuote``'s field names **exactly** -- any missing or unknown
    key raises a clear ``ValueError``. Performs no other validation: every
    enum/format/numeric rule belongs to ``BLIBenchmarkQuote`` itself.
    """

    envelope = json.loads(benchmark_case) if isinstance(benchmark_case, str) else benchmark_case

    if not isinstance(envelope, dict):
        raise ValueError(
            "standalone option benchmark case must be a JSON object at the top level, got "
            f"{type(envelope).__name__}"
        )

    keys = set(envelope)
    missing = _BLI_BENCHMARK_QUOTE_KEYS - keys
    if missing:
        raise ValueError(
            "standalone option benchmark case is missing required top-level key(s): "
            f"{sorted(missing)}"
        )
    unknown = keys - _BLI_BENCHMARK_QUOTE_KEYS
    if unknown:
        raise ValueError(
            f"standalone option benchmark case has unknown top-level key(s): {sorted(unknown)}"
        )
    return envelope


def build_benchmark_from_standalone_option_benchmark_case(
    benchmark_case: str | dict,
) -> BLIBenchmarkQuote:
    """Parse ``benchmark_case`` and construct the existing ``BLIBenchmarkQuote``.

    Raises ``ValueError`` for envelope-level problems (see
    :func:`_parse_standalone_option_benchmark_case`) and propagates every
    ``BLIBenchmarkQuote`` constructor ``TypeError`` / ``ValueError``
    unremapped (malformed enum, blank string, non-finite/negative premium,
    bad as-of format, ...). No alias, default, normalization, inference, or
    fallback is applied anywhere in this function.
    """

    envelope = _parse_standalone_option_benchmark_case(benchmark_case)
    return BLIBenchmarkQuote(**envelope)


def prepare_standalone_benchmark_display(
    benchmark: BLIBenchmarkQuote,
    comparison: BLIBenchmarkComparisonResult,
    calibration: BLIImpliedPriceVolCalibrationResult,
) -> dict:
    """Return a bounded display context read **verbatim** from the three results.

    No pricing, residual, discounting, or volatility math of any kind --
    every value is a direct field read (nested-enum fields are read via
    ``.value`` only, never reinterpreted). ``calibration.solver_result`` is
    ``None`` for every gate-blocked or input-resolution-failed outcome (see
    ``pricing/bli_implied_price_vol_calibration.py``); every solver-derived
    display field then stays ``None`` too -- never a fabricated placeholder.
    """

    solver_result = calibration.solver_result
    return {
        "benchmark": {
            "benchmark_id": benchmark.benchmark_id,
            "source_type": benchmark.source_type.value,
            "source_system": benchmark.source_system,
            "source_as_of": benchmark.source_as_of,
            "retrieved_at": benchmark.retrieved_at,
            "quote_side": benchmark.quote_side.value,
            "premium_per_100": benchmark.premium_per_100,
            "total_premium": benchmark.total_premium,
            "currency": benchmark.currency.value,
            "product_id": benchmark.product_id,
            "snapshot_id": benchmark.snapshot_id,
            "underlying_id": benchmark.underlying_id,
            "source_reference": benchmark.source_reference,
            "notes": benchmark.notes,
        },
        "comparison": {
            "status": comparison.status.value,
            "reason": comparison.reason.value,
            "comparison_metric": comparison.comparison_metric.value,
            "active_quote_side": comparison.active_quote_side.value,
            "pass_threshold": comparison.pass_threshold,
            "fail_threshold": comparison.fail_threshold,
            "near_zero_threshold_per_100": comparison.near_zero_threshold_per_100,
            "model_fair_premium_per_100": comparison.model_fair_premium_per_100,
            "model_total_premium": comparison.model_total_premium,
            "benchmark_premium_per_100": comparison.benchmark_premium_per_100,
            "benchmark_total_premium": comparison.benchmark_total_premium,
            "signed_residual_per_100": comparison.signed_residual_per_100,
            "absolute_residual_per_100": comparison.absolute_residual_per_100,
            "relative_residual": comparison.relative_residual,
            "alignment_note": comparison.alignment_note,
        },
        "calibration": {
            "status": calibration.status.value,
            "reason": calibration.reason.value,
            "diagnostic_note": calibration.diagnostic_note,
            "resolution_error_type": calibration.resolution_error_type,
            "resolution_error_message": calibration.resolution_error_message,
            "forward_clean_price_per_100": calibration.forward_clean_price_per_100,
            "strike_clean_price_per_100": calibration.strike_clean_price_per_100,
            "forward_dirty_price_per_100": calibration.forward_dirty_price_per_100,
            "strike_dirty_price_per_100": calibration.strike_dirty_price_per_100,
            "time_to_expiry_year_fraction": calibration.time_to_expiry_year_fraction,
            "effective_reporting_date_discount_factor": (
                calibration.effective_reporting_date_discount_factor
            ),
            "solver_status": solver_result.status.value if solver_result else None,
            "solver_reason": solver_result.reason.value if solver_result else None,
            "implied_price_vol": solver_result.implied_price_vol if solver_result else None,
            "model_premium_per_100": (
                solver_result.model_premium_per_100 if solver_result else None
            ),
            "premium_residual_per_100": (
                solver_result.premium_residual_per_100 if solver_result else None
            ),
            "lower_price_vol": solver_result.lower_price_vol if solver_result else None,
            "upper_price_vol": solver_result.upper_price_vol if solver_result else None,
            "premium_tolerance_per_100": (
                solver_result.premium_tolerance_per_100 if solver_result else None
            ),
            "price_vol_tolerance": solver_result.price_vol_tolerance if solver_result else None,
            "max_iterations": solver_result.max_iterations if solver_result else None,
            "iterations": solver_result.iterations if solver_result else None,
            "final_bracket_lower_price_vol": (
                solver_result.final_bracket_lower_price_vol if solver_result else None
            ),
            "final_bracket_upper_price_vol": (
                solver_result.final_bracket_upper_price_vol if solver_result else None
            ),
            "solver_diagnostic_note": solver_result.diagnostic_note if solver_result else None,
        },
    }


def price_standalone_option_case_with_benchmark(
    case: str | dict,
    benchmark_case: str | dict,
    *,
    active_quote_side: BLIBenchmarkQuoteSide | str,
    retrieved_at: str | None = None,
) -> tuple[
    BLIStandaloneBondOptionRequest,
    PricingResult,
    BLIBenchmarkQuote,
    BLIBenchmarkComparisonResult,
    BLIImpliedPriceVolCalibrationResult,
    dict,
]:
    """Price ``case``, then compare and calibrate against ``benchmark_case`` once each.

    Bounded orchestration only (Issue #125): prices exclusively through the
    unmodified :func:`price_standalone_option_case`; parses ``benchmark_case``
    into the existing ``BLIBenchmarkQuote`` via
    :func:`build_benchmark_from_standalone_option_benchmark_case`; calls the
    existing, unmodified ``compare_bli_benchmark`` exactly once and
    ``calibrate_bli_implied_price_vol`` exactly once, both with the caller's
    explicit ``active_quote_side`` (no hidden BID/MID/OFFER default) and both
    with their own Phase 1 default thresholds/solver bounds -- this function
    introduces no new tolerance or solver-configuration policy of its own.

    Returns the existing ``request``, ``PricingResult``, ``BLIBenchmarkQuote``,
    ``BLIBenchmarkComparisonResult``, and ``BLIImpliedPriceVolCalibrationResult``
    objects unchanged, plus one merged display dict: the pricing display from
    :func:`prepare_standalone_display` merged with
    :func:`prepare_standalone_benchmark_display`'s ``benchmark`` /
    ``comparison`` / ``calibration`` sections. Never mutates any input or
    result object.
    """

    request, result, display = price_standalone_option_case(case, retrieved_at=retrieved_at)
    benchmark = build_benchmark_from_standalone_option_benchmark_case(benchmark_case)
    comparison = compare_bli_benchmark(
        result, request, benchmark, active_quote_side=active_quote_side
    )
    calibration = calibrate_bli_implied_price_vol(
        request, benchmark, active_quote_side=active_quote_side
    )
    benchmark_display = prepare_standalone_benchmark_display(benchmark, comparison, calibration)
    merged_display = {**display, **benchmark_display}
    return request, result, benchmark, comparison, calibration, merged_display


# --- Issue #6 Phase 3: live Bloomberg bond-quote wiring ---------------------


def prepare_live_bloomberg_quote_display(
    bloomberg_security: str,
    live_quote: BLIBondQuote,
    source_as_of: str,
    retrieved_at: str | None,
) -> dict:
    """Return a bounded display context read **verbatim** from ``live_quote``.

    No calculated values, no clock-sourced timestamp, no quote ID, no
    inferred metadata -- every field is a direct read of ``live_quote`` or a
    caller-supplied provenance value (``bloomberg_security``, ``source_as_of``,
    ``retrieved_at``).
    """

    return {
        "security": bloomberg_security,
        "verified_isin": live_quote.isin,
        "source_system": live_quote.source_system,
        "source_as_of": source_as_of,
        "retrieved_at": retrieved_at,
        "quote_side": live_quote.quote_side.value,
        "currency": live_quote.currency.value,
        "clean_price_per_100": live_quote.clean_price_per_100,
        "accrued_interest_per_100": live_quote.accrued_interest_per_100,
    }


def price_standalone_option_case_with_bloomberg_quote(
    case: str | dict,
    *,
    bloomberg_security: str,
    quote_side: TreasuryFTPQuoteSide,
    source_as_of: str,
    retrieved_at: str | None = None,
) -> tuple[BLIStandaloneBondOptionRequest, PricingResult, BLIBondQuote, dict]:
    """Price ``case`` with its ``bond_quote`` replaced by one live Bloomberg quote.

    Parses ``case`` through the existing envelope rules first (no Bloomberg
    call yet), requires ``bloomberg_security``/``source_as_of`` non-blank,
    and requires ``source_as_of`` to equal the envelope's ``as_of_timestamp``
    exactly -- only then is ``load_bloomberg_bond_quote`` called, exactly
    once, with the expected ISIN read from the case's own
    ``bond_option.underlying_isin`` (via the existing ``BondOption``
    constructor, not raw dict indexing) and the caller's explicit
    ``quote_side`` (required, no default). A new copied envelope is built
    with only ``bond_quote`` replaced (a shallow top-level dict copy, exactly
    like this module's existing display-merge pattern) -- the input ``case``
    mapping and every other envelope value are never mutated. The copied
    envelope is then priced through the unmodified
    :func:`price_standalone_option_case`, reusing the same request builder
    and pricing engine as the manual-JSON workflow.

    Returns the existing ``request``/``PricingResult``, the live
    ``BLIBondQuote``, and the price-only display dict with one added
    ``"live_bloomberg_quote"`` section (see
    :func:`prepare_live_bloomberg_quote_display`). Raises ``ValueError`` for
    envelope/input problems, propagates ``BLIBloombergDapiError`` unchanged
    on any Bloomberg failure (the original ``bond_quote`` is never used as a
    fallback), and propagates every nested schema/builder error unremapped,
    exactly as :func:`price_standalone_option_case` already does.
    """

    envelope = _parse_standalone_option_case(case)

    if not isinstance(bloomberg_security, str) or not bloomberg_security.strip():
        raise ValueError("bloomberg_security must be a non-blank string")
    if not isinstance(source_as_of, str) or not source_as_of.strip():
        raise ValueError("source_as_of must be a non-blank string")
    if source_as_of != envelope["as_of_timestamp"]:
        raise ValueError(
            "source_as_of must equal the case envelope's as_of_timestamp exactly: "
            f"{source_as_of!r} != {envelope['as_of_timestamp']!r}"
        )

    expected_isin = BondOption(**envelope["bond_option"]).underlying_isin

    live_quote = load_bloomberg_bond_quote(
        security=bloomberg_security, isin=expected_isin, quote_side=quote_side
    )

    bloomberg_case = {**envelope, "bond_quote": asdict(live_quote)}
    request, result, display = price_standalone_option_case(
        bloomberg_case, retrieved_at=retrieved_at
    )

    live_quote_display = prepare_live_bloomberg_quote_display(
        bloomberg_security, live_quote, source_as_of, retrieved_at
    )
    merged_display = {**display, "live_bloomberg_quote": live_quote_display}
    return request, result, live_quote, merged_display


def price_standalone_option_case_with_bloomberg_quote_and_benchmark(
    case: str | dict,
    benchmark_case: str | dict,
    *,
    bloomberg_security: str,
    quote_side: TreasuryFTPQuoteSide,
    source_as_of: str,
    retrieved_at: str | None = None,
) -> tuple[
    BLIStandaloneBondOptionRequest,
    PricingResult,
    BLIBondQuote,
    BLIBenchmarkQuote,
    BLIBenchmarkComparisonResult,
    BLIImpliedPriceVolCalibrationResult,
    dict,
]:
    """Price ``case`` with a live Bloomberg quote, then compare/calibrate once each.

    Reuses :func:`price_standalone_option_case_with_bloomberg_quote`
    unchanged for pricing (one Bloomberg call, one copied envelope). The
    live quote's own ``quote_side`` -- the same explicit side the Bloomberg
    price field was requested with -- is reused verbatim as
    ``active_quote_side`` for both ``compare_bli_benchmark`` and
    ``calibrate_bli_implied_price_vol``, each called exactly once: no
    second side, hidden override, or fallback. Mirrors
    :func:`price_standalone_option_case_with_benchmark`'s existing
    orchestration shape exactly, with the live quote inserted as an
    additional returned value.
    """

    request, result, live_quote, display = price_standalone_option_case_with_bloomberg_quote(
        case,
        bloomberg_security=bloomberg_security,
        quote_side=quote_side,
        source_as_of=source_as_of,
        retrieved_at=retrieved_at,
    )
    benchmark = build_benchmark_from_standalone_option_benchmark_case(benchmark_case)
    active_quote_side = live_quote.quote_side.value
    comparison = compare_bli_benchmark(
        result, request, benchmark, active_quote_side=active_quote_side
    )
    calibration = calibrate_bli_implied_price_vol(
        request, benchmark, active_quote_side=active_quote_side
    )
    benchmark_display = prepare_standalone_benchmark_display(benchmark, comparison, calibration)
    merged_display = {**display, **benchmark_display}
    return request, result, live_quote, benchmark, comparison, calibration, merged_display
