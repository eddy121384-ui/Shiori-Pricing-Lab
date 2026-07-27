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
**no aliases, hidden defaults, unit conversions, fallbacks, or inferred
fields**: an unknown or missing top-level key is rejected explicitly, and any
nested field/enum problem raises directly from the existing typed
constructor, unremapped.

**The one normalization performed here (Issue #143).** ``as_of_timestamp``,
``pricing_timestamp``, and ``expiry_timestamp`` denote datetime *instants*.
If one is supplied as an offset-aware ISO-8601 datetime with a genuinely
non-zero offset (e.g. a trader-entered ``2026-07-20T11:28:00+08:00``), the
same instant is respelled in UTC (``2026-07-20T03:28:00Z``) before the typed
constructors see it -- see :func:`_normalize_datetime_instant_to_utc`. A
value already in UTC, a bare date, a naive datetime, and an unparseable
string are all passed through completely unchanged, as is every explicit
calendar-date field (``valuation_date``, ``reporting_date``,
``forward_settlement_date``, ``option_settlement_date``, and every date
nested inside ``bond_option`` / ``bond_reference_data_universe``), which
remain independent inputs. This changes spelling only, never the instant --
no timezone, market close, holiday, business-day adjustment, or settlement
date is inferred anywhere.

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

**Issue #6: live Bloomberg bond-quote wiring, Eddy-approved acquisition-time
contract (issue #6 comment 5028876767, PR #129 comment 5028878866).** Two
bounded headless workflows -- :func:`price_standalone_option_case_with_bloomberg_quote`
and :func:`price_standalone_option_case_with_bloomberg_quote_and_benchmark`
-- replace a case's ``bond_quote`` and ``pricing_timestamp`` with one live
quote and one acquisition timestamp, then price through the same approved
path as the manual-JSON workflow. Bloomberg mode is one explicit action:
refresh the live quote and immediately price the current run -- there is no
quote cache, polling, background refresh, or stale reuse.

No Bloomberg quote-observation timestamp is required or requested; the live
workflow accepts no caller-supplied ``source_as_of`` or live ``retrieved_at``.
The expected ISIN comes from the case's own ``bond_option.underlying_isin``;
Bloomberg's ``ID_ISIN`` is verified against it by the loader itself
(``data/bloomberg_bond_quote.py``, PR #130). Immediately after a successful
loader return, :func:`_shiori_acquisition_now` -- the only clock read
anywhere in this module -- captures one offset-aware system-local **Shiori
acquisition timestamp**. This is never labeled a Bloomberg update time,
exchange time, or quote-observation time. Its local calendar date must
equal the case's ``valuation_date`` exactly; a mismatch is caught by the
existing, unmodified ``pricing_timestamp.date() != valuation_date`` builder
invariant (``data/bli_standalone_option_request.py``) once the acquisition
timestamp is placed on the copied envelope as ``pricing_timestamp`` --
raising ``ValueError`` after retrieval and strictly before any pricing,
comparison, or calibration call, with no fallback to the case's original
``bond_quote`` or a stale prior result.

On a matching date, a new copied envelope replaces only ``bond_quote`` and
``pricing_timestamp`` -- ``as_of_timestamp``, curve points, forward clean
price, volatility, credit spread, reference data, and every other case
field are carried unchanged and never relabeled as current. The original
case JSON's ``bond_quote`` is never read, never used as a fallback, and
never merged field-by-field with the live quote. In benchmark mode, the one
live-quote ``quote_side`` is reused verbatim as ``active_quote_side`` for
both ``compare_bli_benchmark`` and ``calibrate_bli_implied_price_vol`` -- no
second side, override, or fallback. This slice adds no forward, repo,
``PRICE_VOL``, OVME, curve, or pricing-methodology logic of its own.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

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

# The three envelope keys that denote a *datetime instant* -- a specific
# moment in time, which is the same moment however it is spelled. Every
# other date-bearing key in the envelope (valuation_date, reporting_date,
# forward_settlement_date, option_settlement_date, and every date inside
# bond_option / bond_reference_data_universe) is an explicit *calendar
# date*, an independent input in its own right, and is never touched by
# the normalization below (Issue #143).
_DATETIME_INSTANT_ENVELOPE_KEYS = ("as_of_timestamp", "pricing_timestamp", "expiry_timestamp")


def _normalize_datetime_instant_to_utc(value: object) -> object:
    """Return ``value`` as a canonical ``Z``-suffixed UTC instant, or unchanged.

    Issue #143's approved timestamp rule: a trader may enter a datetime with
    an explicit local offset (``2026-07-20T11:28:00+08:00``), and Shiori
    normalizes the *instant* to UTC (``2026-07-20T03:28:00Z``) at this
    contract boundary. This is a pure change of spelling for one identical
    moment -- never a timezone guess, a market-close assumption, a holiday
    or business-day adjustment, or a settlement-date derivation.

    Resolves the #142 defect where ``pricing_timestamp`` / ``expiry_timestamp``
    *require* an explicit offset while ``as_of_timestamp`` (via
    ``_parse_as_of_calendar_date``) *rejects* every non-UTC offset: after
    normalization all three carry ``+00:00``, so one consistently-entered
    ``+08:00`` set is accepted by all three instead of exactly one of them
    failing.

    **Anything that is not an offset-aware ISO-8601 datetime is returned
    completely unchanged** -- a non-string, a bare date, a naive datetime, a
    malformed separator, or an unparseable string. Normalization never
    repairs, defaults, or swallows a bad value; each of those is still
    reported by the existing field-level validator that owns it, with its
    own unchanged error message.

    **A value already in UTC is returned unchanged too**, whichever way it is
    spelled (``Z`` or ``+00:00``): it is already normalized, and re-spelling
    it would gratuitously change existing observable behavior -- notably the
    Bloomberg-refresh path's invariant that ``pricing_timestamp`` is string-
    equal to the recorded acquisition timestamp. Only a genuinely non-zero
    offset is rewritten.
    """

    if not isinstance(value, str):
        return value
    if len(value) < 11 or value[10] != "T":
        # A bare date or a non-canonical separator: leave it for the
        # field's own validator (each has a different rule about which of
        # those it accepts).
        return value
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset == timedelta(0):
        return value

    normalized = parsed.astimezone(UTC).isoformat()
    if normalized.endswith("+00:00"):
        normalized = f"{normalized[:-6]}Z"
    return normalized


def _normalize_case_datetime_instants_to_utc(envelope: dict) -> dict:
    """Return a copy of ``envelope`` with its datetime instants in UTC.

    Only the three keys in ``_DATETIME_INSTANT_ENVELOPE_KEYS`` are rewritten,
    and only when they already parse as an offset-aware ISO-8601 datetime
    (see :func:`_normalize_datetime_instant_to_utc`). Explicit calendar-date
    fields are left exactly as supplied. Never mutates the caller's mapping.
    """

    normalized = dict(envelope)
    for key in _DATETIME_INSTANT_ENVELOPE_KEYS:
        if key in normalized:
            normalized[key] = _normalize_datetime_instant_to_utc(normalized[key])
    return normalized


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

    envelope = _normalize_case_datetime_instants_to_utc(_parse_standalone_option_case(case))

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
    from a clock. The Issue #133 Greek fields are read the same verbatim way
    and therefore stay ``None`` on a ``FAILED`` result, which carries no
    ``assumptions``: this display context never computes, rescales, or
    re-signs a Greek. Their two bases keep the engine's own names --
    ``*_per_100`` (instrument analytics, CALL/PUT direction only) and
    ``position_*_total`` (trader position risk, notional and the BUY/SELL
    sign) -- alongside ``position`` / ``position_multiplier`` and the two
    explicit ``*_sign_applied`` flags, so a consumer can never mistake one
    basis for the other.
    ``pv`` (total notional model fair premium) stays ``None`` on
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
        # Greeks on two separately-named bases: ``*_per_100`` instrument
        # analytics (CALL/PUT direction only) and ``position_*_total``
        # trader position risk (notional AND the BUY/SELL sign). Every one
        # stays ``None`` on a FAILED result (no assumptions exist).
        "forward_price_delta_per_100": assumptions.get("forward_price_delta_per_100"),
        "forward_price_gamma_per_100": assumptions.get("forward_price_gamma_per_100"),
        "vega_per_vol_point_per_100": assumptions.get("vega_per_vol_point_per_100"),
        "theta_per_calendar_day_per_100": assumptions.get("theta_per_calendar_day_per_100"),
        "position_forward_price_delta_total": assumptions.get(
            "position_forward_price_delta_total"
        ),
        "position_forward_price_gamma_total": assumptions.get(
            "position_forward_price_gamma_total"
        ),
        "position_vega_per_vol_point_total": assumptions.get(
            "position_vega_per_vol_point_total"
        ),
        "position_theta_per_calendar_day_total": assumptions.get(
            "position_theta_per_calendar_day_total"
        ),
        "position": assumptions.get("position"),
        "position_multiplier": assumptions.get("position_multiplier"),
        "greeks_per_100_position_sign_applied": assumptions.get(
            "greeks_per_100_position_sign_applied"
        ),
        "greeks_position_total_sign_applied": assumptions.get(
            "greeks_position_total_sign_applied"
        ),
        "greeks_units": assumptions.get("greeks_units"),
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


# --- Issue #6: live Bloomberg bond-quote wiring, acquisition-time contract ---


def _shiori_acquisition_now() -> datetime:
    """Return one offset-aware Shiori acquisition timestamp via the platform clock.

    The only clock read anywhere in this module -- called exactly once, by
    :func:`price_standalone_option_case_with_bloomberg_quote`, immediately
    after a successful ``load_bloomberg_bond_quote`` return. Uses the
    platform-native local-clock-then-attach-offset call below (never a
    fixed or naive time, never a UTC-now/today-only reading). This is the
    Shiori acquisition time only -- never a Bloomberg quote-observation
    time, ``source_as_of``, or exchange time. Tests monkeypatch this exact
    function directly so no real clock is read in CI.
    """

    return datetime.now().astimezone()


def _format_acquisition_timestamp(acquired_at: datetime) -> str:
    """Serialize ``acquired_at`` with an uppercase ``T``, explicit offset, second precision."""

    return acquired_at.isoformat(timespec="seconds")


def prepare_live_bloomberg_quote_display(
    bloomberg_security: str,
    live_quote: BLIBondQuote,
    acquired_at: str,
    case_as_of_timestamp: str,
) -> dict:
    """Return a bounded display context read **verbatim** from ``live_quote``.

    No calculated values, no quote ID, no inferred metadata -- every field
    is a direct read of ``live_quote`` or a caller-supplied provenance value
    (``bloomberg_security``, ``acquired_at``, ``case_as_of_timestamp``).
    ``timestamp_basis`` is always ``"SHIORI_ACQUISITION_TIME"``;
    ``bloomberg_quote_observation_time`` is always ``None`` (this DAPI path
    does not provide one); ``refreshed_scope`` is always
    ``"BOND_QUOTE_ONLY"``; ``other_market_inputs`` is always
    ``"CASE_JSON_UNCHANGED"`` -- curve, forward, volatility, credit-spread,
    and reference-data inputs all remain from the case, untouched here.
    There is no ``source_as_of`` field in this section.
    """

    return {
        "security": bloomberg_security,
        "verified_isin": live_quote.isin,
        "source_system": live_quote.source_system,
        "quote_side": live_quote.quote_side.value,
        "currency": live_quote.currency.value,
        "clean_price_per_100": live_quote.clean_price_per_100,
        "accrued_interest_per_100": live_quote.accrued_interest_per_100,
        "acquired_at": acquired_at,
        "timestamp_basis": "SHIORI_ACQUISITION_TIME",
        "bloomberg_quote_observation_time": None,
        "case_as_of_timestamp": case_as_of_timestamp,
        "refreshed_scope": "BOND_QUOTE_ONLY",
        "other_market_inputs": "CASE_JSON_UNCHANGED",
    }


def price_standalone_option_case_with_bloomberg_quote(
    case: str | dict,
    *,
    bloomberg_security: str,
    quote_side: TreasuryFTPQuoteSide,
) -> tuple[BLIStandaloneBondOptionRequest, PricingResult, BLIBondQuote, dict]:
    """Price ``case`` with its ``bond_quote`` replaced by one live Bloomberg quote.

    Bloomberg mode is one explicit action: refresh the live quote and
    immediately price the current run -- no cache, no polling, no stale
    reuse. Parses ``case`` through the existing envelope rules first (no
    Bloomberg call yet) and requires ``bloomberg_security`` non-blank; only
    then is ``load_bloomberg_bond_quote`` called, exactly once, with the
    expected ISIN read from the case's own ``bond_option.underlying_isin``
    (via the existing ``BondOption`` constructor, not raw dict indexing)
    and the caller's explicit ``quote_side`` (required, no default). This
    function accepts no caller-supplied ``source_as_of`` or live
    ``retrieved_at``.

    Immediately after a successful loader return, :func:`_shiori_acquisition_now`
    captures one offset-aware Shiori acquisition timestamp. A new copied
    envelope is built with only ``bond_quote`` and ``pricing_timestamp``
    replaced (a shallow top-level dict copy) -- the input ``case`` mapping
    and every other envelope value, including ``as_of_timestamp``, are
    never mutated or relabeled. The copied envelope is then priced through
    the unmodified :func:`price_standalone_option_case`, reusing the same
    request builder and pricing engine as the manual-JSON workflow, with
    the acquisition timestamp passed as its ``retrieved_at`` display value.
    If the acquisition timestamp's local calendar date does not equal
    ``valuation_date``, the existing, unmodified
    ``pricing_timestamp.date() != valuation_date`` builder invariant
    (``data/bli_standalone_option_request.py``) raises ``ValueError`` from
    inside that build step -- after retrieval, strictly before pricing, and
    with no fallback to the case's original ``bond_quote``.

    Returns the existing ``request``/``PricingResult``, the live
    ``BLIBondQuote``, and the price-only display dict with one added
    ``"live_bloomberg_quote"`` section (see
    :func:`prepare_live_bloomberg_quote_display`). Raises ``ValueError`` for
    envelope/input/date problems, propagates ``BLIBloombergDapiError``
    unchanged on any Bloomberg failure (the original ``bond_quote`` is
    never used as a fallback), and propagates every nested schema/builder
    error unremapped, exactly as :func:`price_standalone_option_case`
    already does.
    """

    envelope = _parse_standalone_option_case(case)

    if not isinstance(bloomberg_security, str) or not bloomberg_security.strip():
        raise ValueError("bloomberg_security must be a non-blank string")

    expected_isin = BondOption(**envelope["bond_option"]).underlying_isin

    live_quote = load_bloomberg_bond_quote(
        security=bloomberg_security, isin=expected_isin, quote_side=quote_side
    )

    acquired_at = _format_acquisition_timestamp(_shiori_acquisition_now())

    bloomberg_case = {
        **envelope,
        "bond_quote": asdict(live_quote),
        "pricing_timestamp": acquired_at,
    }
    request, result, display = price_standalone_option_case(
        bloomberg_case, retrieved_at=acquired_at
    )

    live_quote_display = prepare_live_bloomberg_quote_display(
        bloomberg_security, live_quote, acquired_at, envelope["as_of_timestamp"]
    )
    merged_display = {**display, "live_bloomberg_quote": live_quote_display}
    return request, result, live_quote, merged_display


def price_standalone_option_case_with_bloomberg_quote_and_benchmark(
    case: str | dict,
    benchmark_case: str | dict,
    *,
    bloomberg_security: str,
    quote_side: TreasuryFTPQuoteSide,
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
    unchanged for pricing/acquisition/date-guard (one Bloomberg call, one
    clock read, one copied envelope) -- a date mismatch or Bloomberg
    failure raises there, before any comparison or calibration call. The
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
