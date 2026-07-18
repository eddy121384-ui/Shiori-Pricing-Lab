"""``calibrate_bli_implied_price_vol``: standalone OVME-aligned calibration wiring
(Issue #99 PR B; Issue #P1-06 dirty-price realignment).

**Issue #P1-06: calibration restored on the dirty-price basis.** PR #122
(Issue #94) moved the standalone fair-premium path
(``price_bli_mvp_standalone_option``) to dirty forward/strike, fractional
ACT/ACT option time, and an Option-Discount-Curve reporting-date discount
factor, and -- because the Issue #99 solver's public contract was still
clean-price/ACT-365F -- blocked this module with an explicit
``METHODOLOGY_NOT_ALIGNED`` outcome rather than emit a
plausible-but-inconsistent implied vol. That block is now lifted: this
module resolves the exact same OVME-aligned inputs the fair-premium path
uses, via the same focused resolver, and calibrates on the dirty-price
implied-vol solver (``solve_implied_dirty_price_vol``,
``pricing/bli_implied_price_vol_solver.py``) that solver's own Issue #P1-06
extension added alongside the unchanged clean-price solver.

Scope: one isolated function that wires a
``BLIStandaloneBondOptionRequest`` and a ``BLIBenchmarkQuote`` (Issue #98 PR
A / #109) to the dirty-price implied-``PRICE_VOL`` solver, reusing only
already-reviewed production pieces:

- ``check_bli_mvp_standalone_option_required_inputs``
  (``pricing/bli_mvp_required_input_guard.py``, #41/#95) -- the exact
  request-support checklist the standalone pricing engine itself uses;
- ``resolve_standalone_option_pricing_inputs``
  (``pricing/bli_standalone_option_pricing_inputs.py``, Issue #94) -- the
  exact dirty forward/strike, ACT/ACT option time, and effective
  reporting-date discount factor the standalone fair-premium engine itself
  uses (``pricing/bli_pricing_engine.py``
  ``price_bli_mvp_standalone_option``), called exactly once;
- ``solve_implied_dirty_price_vol`` (``pricing/bli_implied_price_vol_solver.py``,
  Issue #P1-06) -- called exactly once, with ``benchmark.premium_per_100``
  as its per-100-only target.

**This module creates no ``PricingResult`` and calls no pricing engine.**
Calibration occurs *before* a repricing ``PricingResult`` exists for the
calibrated volatility -- ``price_bli_mvp`` / ``price_bli_mvp_standalone_option``
are never invoked here. ``STANDALONE_ENGINE_NAME`` / ``STANDALONE_ENGINE_VERSION``
are imported from ``pricing/bli_pricing_engine.py`` only as the two existing
provenance string constants stamped on the result -- no new model name or
version is introduced; they identify the standalone OVME pricing
methodology this calibration is calibrating against.

**This module does not call ``compare_bli_benchmark``** (Issue #98 PR B /
#110) and does not require an existing ``PricingResult`` as a prerequisite.
The comparison module's already-approved quote-side-coercion and
calendar-date-equality pattern is mirrored here (duplicated locally, per
the existing repo convention of small module-local validation helpers --
see ``data/_validation.py``'s own docstring), not imported or called.

**Provenance-only fields, never used in the solve.** ``request_notional``,
``original_volatility``/``original_volatility_basis``,
``benchmark_total_premium``, ``benchmark_retrieved_at``, and the benchmark
notes/source-reference fields are all preserved verbatim on the result but
never read by the solver call -- the target is always
``benchmark.premium_per_100`` alone, and the solver receives no notional or
total-premium argument at all (mirrors the dirty solver's own boundary).

**Fixed gate priority.** Evaluated in exactly this order, short-circuiting
on the first applicable outcome: request support, product id, snapshot id,
underlying id, currency, quote side, source date, solver-configuration
validation + input resolution, solver outcome. See
:func:`calibrate_bli_implied_price_vol` for the full mapping.

**Contract violations vs. structured outcomes.** A non-``BLIStandaloneBondOptionRequest``
or non-``BLIBenchmarkQuote`` argument raises ``TypeError``; a malformed
``active_quote_side`` (a foreign ``Enum``/``StrEnum`` instance, or a string
that does not coerce to ``BLIBenchmarkQuoteSide``) raises ``ValueError`` --
both immediately, before any gate is evaluated. After every coherence gate
passes, the five public solver-configuration arguments are validated in the
solver's own accepted/rejected domain (a malformed configuration, e.g.
``lower_price_vol=-1.0``, raises ``ValueError`` here, before
``resolve_standalone_option_pricing_inputs`` is ever called). A
``ValueError`` raised by the resolver is caught and mapped to a structured
``FAILED / INPUT_RESOLUTION_FAILED`` result (exception type/message
preserved; every resolved-input field stays ``None`` since the resolver is
atomic). A ``ValueError`` raised by ``solve_implied_dirty_price_vol`` itself
(which cannot happen for a configuration already validated above) and a
``RuntimeError`` from any composed helper (e.g. QuantLib unavailable) are
never caught here and propagate unchanged -- both are environment/contract
problems, not domain outcomes this module maps.

**Hard non-goals:** no existing-file change beyond this module and the
dirty solver it consumes, no pricing-engine wiring, no request/snapshot/
benchmark/solver/comparison schema change, no yield-vol conversion, no
volatility surface/interpolation/SABR/American-exercise/multi-quote
calibration, no provider/Bloomberg adapter, no UI/workbench, no
persistence/versioning/replay, no client-margin calibration, and no
golden-case fixture duplication (the golden case is reused from
``tests/test_bli_ovme_golden_case.py``). This module imports from
``data._validation``, ``data.bli_benchmark_quote``, ``data.bli_snapshot``,
``data.bli_standalone_option_request``, ``pricing.bli_implied_price_vol_solver``,
``pricing.bli_mvp_required_input_guard``, ``pricing.bli_pricing_engine``
(constants only), ``pricing.bli_standalone_option_pricing_inputs``, and
``products.enums`` only, and never mutates ``request`` or ``benchmark``.

**Golden-case boundary.** Every synthetic fixture in this module's own test
suite is explicitly synthetic. None represents Bloomberg output, a golden
case, market calibration evidence, or UAT.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum, StrEnum

from shiori_pricing_lab.data._validation import _parse_as_of_calendar_date, _parse_iso_date
from shiori_pricing_lab.data.bli_benchmark_quote import (
    BLIBenchmarkQuote,
    BLIBenchmarkQuoteSide,
    BLIBenchmarkSourceType,
)
from shiori_pricing_lab.data.bli_snapshot import BLIVolatilityBasis
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.pricing.bli_implied_price_vol_solver import (
    BLIImpliedDirtyPriceVolSolverResult,
    BLIImpliedPriceVolSolverStatus,
    solve_implied_dirty_price_vol,
)
from shiori_pricing_lab.pricing.bli_mvp_required_input_guard import (
    check_bli_mvp_standalone_option_required_inputs,
)
from shiori_pricing_lab.pricing.bli_pricing_engine import (
    STANDALONE_ENGINE_NAME,
    STANDALONE_ENGINE_VERSION,
)
from shiori_pricing_lab.pricing.bli_standalone_option_pricing_inputs import (
    resolve_standalone_option_pricing_inputs,
)
from shiori_pricing_lab.products.enums import Currency, OptionType, coerce_enum


class BLIImpliedPriceVolCalibrationStatus(StrEnum):
    """Outcome of one ``calibrate_bli_implied_price_vol`` call."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class BLIImpliedPriceVolCalibrationReason(StrEnum):
    """Machine-readable reason for a calibration result's status.

    Only ``CALIBRATED`` may pair with ``SUCCESS``. Every other member is a
    ``FAILED`` reason, returned in the fixed gate priority documented on
    :func:`calibrate_bli_implied_price_vol`. This is a distinct vocabulary
    from ``BLIImpliedPriceVolSolverReason`` -- the dirty solver's own reason
    is never duplicated here; it is preserved unchanged inside
    ``solver_result``.
    """

    CALIBRATED = "CALIBRATED"
    REQUEST_NOT_SUPPORTED = "REQUEST_NOT_SUPPORTED"
    PRODUCT_ID_MISMATCH = "PRODUCT_ID_MISMATCH"
    SNAPSHOT_ID_MISMATCH = "SNAPSHOT_ID_MISMATCH"
    UNDERLYING_ID_MISMATCH = "UNDERLYING_ID_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    QUOTE_SIDE_MISMATCH = "QUOTE_SIDE_MISMATCH"
    SOURCE_DATE_MISMATCH = "SOURCE_DATE_MISMATCH"
    INPUT_RESOLUTION_FAILED = "INPUT_RESOLUTION_FAILED"
    SOLVER_FAILED = "SOLVER_FAILED"


def _reject_foreign_enum_active_quote_side(value: object) -> None:
    """Reject any ``Enum`` instance that is not already a ``BLIBenchmarkQuoteSide``.

    Duplicated locally rather than imported -- mirrors the identical guard
    already reviewed in ``data/bli_benchmark_quote.py`` (Issue #98 PR A)
    and ``pricing/bli_benchmark_comparison.py`` (Issue #98 PR B): both
    exist because ``products.enums.coerce_enum`` coerces by *value*, so a
    foreign ``StrEnum`` member sharing a string value (e.g.
    ``TreasuryFTPQuoteSide.BID == "BID"``) would otherwise pass through it
    silently. This module does not call or import from
    ``pricing/bli_benchmark_comparison.py`` (no comparison-function
    dependency).
    """

    if isinstance(value, Enum) and not isinstance(value, BLIBenchmarkQuoteSide):
        raise ValueError(
            "active_quote_side must be a BLIBenchmarkQuoteSide member or one of "
            f"{{BID, MID, OFFER}}, got foreign enum {value!r}"
        )


def _require_finite_non_bool_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    return float(value)


def _validate_solver_configuration(
    *,
    lower_price_vol: float,
    upper_price_vol: float,
    premium_tolerance_per_100: float,
    price_vol_tolerance: float,
    max_iterations: int,
) -> None:
    """Validate the five public solver-configuration arguments.

    Validated here -- in the same accepted/rejected domain and order
    ``solve_implied_dirty_price_vol`` itself uses -- so a malformed caller
    configuration (e.g. ``lower_price_vol=-1.0``) is a ``ValueError`` raised
    before ``resolve_standalone_option_pricing_inputs`` or the solver ever
    run. No normalization, clamping, fallback, widening, or default repair
    is applied.
    """

    lower_price_vol = _require_finite_non_bool_number(lower_price_vol, "lower_price_vol")
    if not lower_price_vol > 0:
        raise ValueError(f"lower_price_vol must be positive, got {lower_price_vol!r}")
    upper_price_vol = _require_finite_non_bool_number(upper_price_vol, "upper_price_vol")
    if not lower_price_vol < upper_price_vol:
        raise ValueError(
            "lower_price_vol must be strictly less than upper_price_vol, got "
            f"lower_price_vol={lower_price_vol!r} upper_price_vol={upper_price_vol!r}"
        )
    premium_tolerance_per_100 = _require_finite_non_bool_number(
        premium_tolerance_per_100, "premium_tolerance_per_100"
    )
    if not premium_tolerance_per_100 > 0:
        raise ValueError(
            f"premium_tolerance_per_100 must be positive, got {premium_tolerance_per_100!r}"
        )
    price_vol_tolerance = _require_finite_non_bool_number(
        price_vol_tolerance, "price_vol_tolerance"
    )
    if not price_vol_tolerance > 0:
        raise ValueError(f"price_vol_tolerance must be positive, got {price_vol_tolerance!r}")
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int):
        raise ValueError(f"max_iterations must be a non-bool int, got {max_iterations!r}")
    if not max_iterations > 0:
        raise ValueError(f"max_iterations must be positive, got {max_iterations!r}")


@dataclass(frozen=True)
class BLIImpliedPriceVolCalibrationResult:
    """One deterministic standalone implied-``PRICE_VOL`` calibration outcome (dirty basis).

    Every field is a verbatim read from ``request``/``benchmark`` or an
    already-reviewed helper's return value -- no derivation, no unit
    conversion, no mutation of either input. The resolved-input fields
    (``forward_clean_price_per_100`` through
    ``effective_reporting_date_discount_factor``) and ``solver_result``
    stay ``None`` until (and unless) actually resolved/solved --
    ``resolve_standalone_option_pricing_inputs`` is atomic, so a gate
    short-circuit or an ``INPUT_RESOLUTION_FAILED`` outcome leaves every one
    of them ``None``. ``resolution_error_type``/``resolution_error_message``
    are populated only for ``INPUT_RESOLUTION_FAILED``.
    """

    status: BLIImpliedPriceVolCalibrationStatus
    reason: BLIImpliedPriceVolCalibrationReason
    active_quote_side: BLIBenchmarkQuoteSide
    pricing_engine_name: str
    pricing_engine_version: str
    product_id: str
    snapshot_id: str
    underlying_id: str
    currency: Currency
    valuation_date: str
    market_data_as_of: str
    option_type: OptionType
    expiry_date: str
    request_notional: float
    original_volatility: float
    original_volatility_basis: BLIVolatilityBasis
    benchmark_id: str
    benchmark_source_type: BLIBenchmarkSourceType
    benchmark_source_system: str
    benchmark_source_as_of: str
    benchmark_retrieved_at: str
    benchmark_quote_side: BLIBenchmarkQuoteSide
    benchmark_premium_per_100: float
    benchmark_total_premium: float
    benchmark_source_reference: str
    benchmark_notes: str | None
    request_support_reasons: tuple[str, ...]
    forward_clean_price_per_100: float | None
    strike_clean_price_per_100: float | None
    accrued_interest_at_forward_settlement_per_100: float | None
    forward_dirty_price_per_100: float | None
    strike_dirty_price_per_100: float | None
    time_to_expiry_year_fraction: float | None
    pricing_to_reporting_discount_factor: float | None
    pricing_to_option_settlement_discount_factor: float | None
    effective_reporting_date_discount_factor: float | None
    solver_result: BLIImpliedDirtyPriceVolSolverResult | None
    resolution_error_type: str | None
    resolution_error_message: str | None
    diagnostic_note: str


def calibrate_bli_implied_price_vol(
    request: BLIStandaloneBondOptionRequest,
    benchmark: BLIBenchmarkQuote,
    *,
    active_quote_side: BLIBenchmarkQuoteSide | str,
    lower_price_vol: float = 0.000001,
    upper_price_vol: float = 5.0,
    premium_tolerance_per_100: float = 1e-8,
    price_vol_tolerance: float = 1e-8,
    max_iterations: int = 100,
) -> BLIImpliedPriceVolCalibrationResult:
    """Calibrate the implied ``PRICE_VOL`` matching ``benchmark`` for ``request`` (dirty basis).

    Never mutates ``request`` or ``benchmark``. See the module docstring
    for the exact gate priority, resolution order, and solver-result
    mapping.
    """

    if not isinstance(request, BLIStandaloneBondOptionRequest):
        raise TypeError(
            f"request must be a BLIStandaloneBondOptionRequest, got {type(request).__name__}"
        )
    if not isinstance(benchmark, BLIBenchmarkQuote):
        raise TypeError(f"benchmark must be a BLIBenchmarkQuote, got {type(benchmark).__name__}")

    _reject_foreign_enum_active_quote_side(active_quote_side)
    resolved_active_quote_side = coerce_enum(
        active_quote_side, BLIBenchmarkQuoteSide, "active_quote_side"
    )

    bond_option = request.bond_option
    snapshot = request.market_data_snapshot
    volatility_input = snapshot.volatility_input

    def _build(
        *,
        status: BLIImpliedPriceVolCalibrationStatus,
        reason: BLIImpliedPriceVolCalibrationReason,
        diagnostic_note: str,
        request_support_reasons: tuple[str, ...] = (),
        forward_clean_price_per_100: float | None = None,
        strike_clean_price_per_100: float | None = None,
        accrued_interest_at_forward_settlement_per_100: float | None = None,
        forward_dirty_price_per_100: float | None = None,
        strike_dirty_price_per_100: float | None = None,
        time_to_expiry_year_fraction: float | None = None,
        pricing_to_reporting_discount_factor: float | None = None,
        pricing_to_option_settlement_discount_factor: float | None = None,
        effective_reporting_date_discount_factor: float | None = None,
        solver_result: BLIImpliedDirtyPriceVolSolverResult | None = None,
        resolution_error_type: str | None = None,
        resolution_error_message: str | None = None,
    ) -> BLIImpliedPriceVolCalibrationResult:
        return BLIImpliedPriceVolCalibrationResult(
            status=status,
            reason=reason,
            active_quote_side=resolved_active_quote_side,
            pricing_engine_name=STANDALONE_ENGINE_NAME,
            pricing_engine_version=STANDALONE_ENGINE_VERSION,
            product_id=benchmark.product_id,
            snapshot_id=benchmark.snapshot_id,
            underlying_id=benchmark.underlying_id,
            currency=benchmark.currency,
            valuation_date=request.valuation_date,
            market_data_as_of=snapshot.as_of_timestamp,
            option_type=bond_option.option_type,
            expiry_date=bond_option.expiry_date,
            request_notional=bond_option.notional,
            original_volatility=volatility_input.volatility,
            original_volatility_basis=volatility_input.volatility_basis,
            benchmark_id=benchmark.benchmark_id,
            benchmark_source_type=benchmark.source_type,
            benchmark_source_system=benchmark.source_system,
            benchmark_source_as_of=benchmark.source_as_of,
            benchmark_retrieved_at=benchmark.retrieved_at,
            benchmark_quote_side=benchmark.quote_side,
            benchmark_premium_per_100=benchmark.premium_per_100,
            benchmark_total_premium=benchmark.total_premium,
            benchmark_source_reference=benchmark.source_reference,
            benchmark_notes=benchmark.notes,
            request_support_reasons=request_support_reasons,
            forward_clean_price_per_100=forward_clean_price_per_100,
            strike_clean_price_per_100=strike_clean_price_per_100,
            accrued_interest_at_forward_settlement_per_100=(
                accrued_interest_at_forward_settlement_per_100
            ),
            forward_dirty_price_per_100=forward_dirty_price_per_100,
            strike_dirty_price_per_100=strike_dirty_price_per_100,
            time_to_expiry_year_fraction=time_to_expiry_year_fraction,
            pricing_to_reporting_discount_factor=pricing_to_reporting_discount_factor,
            pricing_to_option_settlement_discount_factor=(
                pricing_to_option_settlement_discount_factor
            ),
            effective_reporting_date_discount_factor=effective_reporting_date_discount_factor,
            solver_result=solver_result,
            resolution_error_type=resolution_error_type,
            resolution_error_message=resolution_error_message,
            diagnostic_note=diagnostic_note,
        )

    # --- 1. Request support (called exactly once, always evaluated first) ---
    guard_result = check_bli_mvp_standalone_option_required_inputs(request)
    if not guard_result.supported:
        return _build(
            status=BLIImpliedPriceVolCalibrationStatus.FAILED,
            reason=BLIImpliedPriceVolCalibrationReason.REQUEST_NOT_SUPPORTED,
            diagnostic_note=(
                "check_bli_mvp_standalone_option_required_inputs rejected this request: "
                f"{'; '.join(guard_result.reasons)}"
            ),
            request_support_reasons=guard_result.reasons,
        )

    # --- 2. PRODUCT_ID_MISMATCH ------------------------------------------------
    if bond_option.product_id != benchmark.product_id:
        return _build(
            status=BLIImpliedPriceVolCalibrationStatus.FAILED,
            reason=BLIImpliedPriceVolCalibrationReason.PRODUCT_ID_MISMATCH,
            diagnostic_note=(
                "product_id mismatch: request.bond_option.product_id="
                f"{bond_option.product_id!r}, benchmark.product_id={benchmark.product_id!r}"
            ),
        )

    # --- 3. SNAPSHOT_ID_MISMATCH -----------------------------------------------
    if snapshot.snapshot_id != benchmark.snapshot_id:
        return _build(
            status=BLIImpliedPriceVolCalibrationStatus.FAILED,
            reason=BLIImpliedPriceVolCalibrationReason.SNAPSHOT_ID_MISMATCH,
            diagnostic_note=(
                "snapshot_id mismatch: request.market_data_snapshot.snapshot_id="
                f"{snapshot.snapshot_id!r}, benchmark.snapshot_id={benchmark.snapshot_id!r}"
            ),
        )

    # --- 4. UNDERLYING_ID_MISMATCH ----------------------------------------------
    if bond_option.underlying_isin != benchmark.underlying_id:
        return _build(
            status=BLIImpliedPriceVolCalibrationStatus.FAILED,
            reason=BLIImpliedPriceVolCalibrationReason.UNDERLYING_ID_MISMATCH,
            diagnostic_note=(
                "underlying_id mismatch: request.bond_option.underlying_isin="
                f"{bond_option.underlying_isin!r}, benchmark.underlying_id="
                f"{benchmark.underlying_id!r}"
            ),
        )

    # --- 5. CURRENCY_MISMATCH ---------------------------------------------------
    if bond_option.currency is not benchmark.currency:
        return _build(
            status=BLIImpliedPriceVolCalibrationStatus.FAILED,
            reason=BLIImpliedPriceVolCalibrationReason.CURRENCY_MISMATCH,
            diagnostic_note=(
                "currency mismatch: request.bond_option.currency="
                f"{bond_option.currency.value!r}, benchmark.currency="
                f"{benchmark.currency.value!r}"
            ),
        )

    # --- 6. QUOTE_SIDE_MISMATCH -------------------------------------------------
    if resolved_active_quote_side is not benchmark.quote_side:
        return _build(
            status=BLIImpliedPriceVolCalibrationStatus.FAILED,
            reason=BLIImpliedPriceVolCalibrationReason.QUOTE_SIDE_MISMATCH,
            diagnostic_note=(
                f"active_quote_side ({resolved_active_quote_side.value}) does not match "
                f"benchmark.quote_side ({benchmark.quote_side.value})"
            ),
        )

    # --- 7. SOURCE_DATE_MISMATCH ------------------------------------------------
    valuation_calendar_date = _parse_iso_date(request.valuation_date, "request.valuation_date")
    snapshot_as_of_calendar_date = _parse_as_of_calendar_date(
        snapshot.as_of_timestamp, "request.market_data_snapshot.as_of_timestamp"
    )
    benchmark_source_as_of_calendar_date = _parse_as_of_calendar_date(
        benchmark.source_as_of, "benchmark.source_as_of"
    )
    if not (
        valuation_calendar_date
        == snapshot_as_of_calendar_date
        == benchmark_source_as_of_calendar_date
    ):
        return _build(
            status=BLIImpliedPriceVolCalibrationStatus.FAILED,
            reason=BLIImpliedPriceVolCalibrationReason.SOURCE_DATE_MISMATCH,
            diagnostic_note=(
                "calendar-date mismatch: request.valuation_date date="
                f"{valuation_calendar_date.isoformat()}, "
                "request.market_data_snapshot.as_of_timestamp date="
                f"{snapshot_as_of_calendar_date.isoformat()}, benchmark.source_as_of date="
                f"{benchmark_source_as_of_calendar_date.isoformat()} -- benchmark.retrieved_at "
                "is provenance only and never participates in this check"
            ),
        )

    # --- 8. Solver-configuration validation + input resolution ------------------
    # The five public solver-configuration arguments are validated first, in
    # solve_implied_dirty_price_vol's own accepted/rejected domain, so a
    # malformed configuration stays a ValueError rather than a resolved-but-
    # discarded computation. resolve_standalone_option_pricing_inputs is then
    # called exactly once -- the same resolver price_bli_mvp_standalone_option
    # itself uses -- so calibration is always evaluated against the identical
    # dirty forward/strike, ACT/ACT option time, and effective reporting-date
    # discount factor the fair-premium path prices with.
    _validate_solver_configuration(
        lower_price_vol=lower_price_vol,
        upper_price_vol=upper_price_vol,
        premium_tolerance_per_100=premium_tolerance_per_100,
        price_vol_tolerance=price_vol_tolerance,
        max_iterations=max_iterations,
    )

    try:
        inputs = resolve_standalone_option_pricing_inputs(request)
    except ValueError as exc:
        return _build(
            status=BLIImpliedPriceVolCalibrationStatus.FAILED,
            reason=BLIImpliedPriceVolCalibrationReason.INPUT_RESOLUTION_FAILED,
            diagnostic_note=(
                "resolve_standalone_option_pricing_inputs raised "
                f"{type(exc).__name__}: {exc}"
            ),
            resolution_error_type=type(exc).__name__,
            resolution_error_message=str(exc),
        )

    # --- 9. Solver outcome (dirty basis, Issue #P1-06) --------------------------
    solver_result = solve_implied_dirty_price_vol(
        forward_dirty_price=inputs.forward_dirty_price_per_100,
        strike_dirty_price=inputs.strike_dirty_price_per_100,
        time_to_expiry=inputs.time_to_expiry_year_fraction,
        discount_factor=inputs.effective_reporting_date_discount_factor,
        option_type=bond_option.option_type,
        target_premium_per_100=benchmark.premium_per_100,
        lower_price_vol=lower_price_vol,
        upper_price_vol=upper_price_vol,
        premium_tolerance_per_100=premium_tolerance_per_100,
        price_vol_tolerance=price_vol_tolerance,
        max_iterations=max_iterations,
    )

    resolved_input_fields = dict(
        forward_clean_price_per_100=inputs.forward_clean_price_per_100,
        strike_clean_price_per_100=inputs.strike_clean_price_per_100,
        accrued_interest_at_forward_settlement_per_100=(
            inputs.accrued_interest_at_forward_settlement_per_100
        ),
        forward_dirty_price_per_100=inputs.forward_dirty_price_per_100,
        strike_dirty_price_per_100=inputs.strike_dirty_price_per_100,
        time_to_expiry_year_fraction=inputs.time_to_expiry_year_fraction,
        pricing_to_reporting_discount_factor=inputs.pricing_to_reporting_discount_factor,
        pricing_to_option_settlement_discount_factor=(
            inputs.pricing_to_option_settlement_discount_factor
        ),
        effective_reporting_date_discount_factor=inputs.effective_reporting_date_discount_factor,
    )

    if solver_result.status is BLIImpliedPriceVolSolverStatus.SUCCESS:
        return _build(
            status=BLIImpliedPriceVolCalibrationStatus.SUCCESS,
            reason=BLIImpliedPriceVolCalibrationReason.CALIBRATED,
            diagnostic_note=(
                "solve_implied_dirty_price_vol converged: implied_price_vol="
                f"{solver_result.implied_price_vol!r} after {solver_result.iterations} "
                "iteration(s)"
            ),
            solver_result=solver_result,
            **resolved_input_fields,
        )

    return _build(
        status=BLIImpliedPriceVolCalibrationStatus.FAILED,
        reason=BLIImpliedPriceVolCalibrationReason.SOLVER_FAILED,
        diagnostic_note=(
            f"solve_implied_dirty_price_vol did not converge: reason={solver_result.reason.value}"
            f" -- {solver_result.diagnostic_note}"
        ),
        solver_result=solver_result,
        **resolved_input_fields,
    )
