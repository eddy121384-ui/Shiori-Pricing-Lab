"""``solve_implied_price_vol`` / ``solve_implied_dirty_price_vol``: bounded implied
PRICE_VOL solvers (Issue #99 PR A; Issue #P1-06 dirty-price extension).

Scope, per the methodology approved in Issue #99 comment 4965749214 and the
implementation slice authorized in Issue #99 comment 4965760662: a pure,
deterministic, stdlib-only root-find over a Black-76 trial-pricing call,
varying only price volatility (sigma) to match one already-known target
premium per 100.

**Two public wrappers, one price-basis-neutral core (Issue #P1-06).** The
clean-basis :func:`solve_implied_price_vol` (unchanged public contract and
pinned behavior, used by the legacy bundle path) and the dirty-basis
:func:`solve_implied_dirty_price_vol` (the OVME-aligned standalone
calibration path solves for implied vol on dirty forward/strike, matching
``price_bli_mvp_standalone_option``'s own dirty-basis pricing) both delegate
to the single private, price-basis-neutral ``_solve_bounded_implied_price_vol_core``.
The bisection/arbitrage-bound/boundary-convergence logic exists in exactly
one place. **The shared core never blurs the price basis**: each public
wrapper supplies its own already-basis-correct trial-pricing closure (the
clean wrapper calls ``black76_price_option_pv_per_100``; the dirty wrapper
calls ``black76_dirty_price_option_pv_per_100``) and echoes its own
honestly-named F/K fields (``forward_clean_price``/``strike_clean_price`` vs
``forward_dirty_price``/``strike_dirty_price``) on its own frozen result
type -- a dirty value is never passed through an argument or field whose
name claims it is clean.

**Pure numerical primitive only.** Neither wrapper has any notion of a
``BLIStandaloneBondOptionRequest``, ``BLIBenchmarkQuote``, ``PricingResult``,
market-data snapshot, notional, or total premium. Every one of F, K, T, DF,
option type, and the target premium is a plain, already-resolved argument;
resolving those from a request/benchmark is calibration wiring's job
(``pricing/bli_implied_price_vol_calibration.py``), not this module's.

**No formula duplication.** The only pricing computation either wrapper
performs is its own Black-76 trial-pricing call at each candidate
volatility; ``d1``/``d2``/the standard normal CDF are never reimplemented
here.

**Target is per-100 only.** ``target_premium_per_100`` is a bare float; no
notional or total-premium parameter exists anywhere in either public
signature, and none is ever derived internally.

**Volatility domain.** ``sigma = 0`` is unsupported -- the reused Black-76
primitives themselves require ``sigma > 0`` and are not modified. Phase 1
defaults: ``lower_price_vol = 0.000001``, ``upper_price_vol = 5.0``, both
caller-configurable but always finite and ``0 < lower_price_vol <
upper_price_vol``.

**Solver.** Deterministic, stdlib-only bisection. Black-76 premium is
treated as monotonic increasing in positive price volatility for valid
inputs (a mathematical property of the model, not something this module
verifies at runtime), so a valid configured bracket has at most one root --
no multiple-root detector or multiple-root failure reason exists.

**Failure/solve order (Issue #99 comment 4965760662), identical for both
wrappers:**

1. target strictly below the Black-76 theoretical arbitrage lower bound
   -> ``BELOW_ARBITRAGE_LOWER_BOUND``;
2. target exactly at the theoretical lower bound -> ``AT_ARBITRAGE_LOWER_BOUND``;
3. target exactly at the theoretical upper bound -> ``AT_ARBITRAGE_UPPER_BOUND``;
4. target strictly above the theoretical upper bound -> ``ABOVE_ARBITRAGE_UPPER_BOUND``;
5. target within ``premium_tolerance_per_100`` of the model premium at
   ``lower_price_vol`` -> ``SUCCESS`` / ``CONVERGED`` at the lower bound,
   ``iterations = 0``;
6. same check at ``upper_price_vol`` -> ``SUCCESS`` / ``CONVERGED`` at the
   upper bound, ``iterations = 0``;
7. target outside the inclusive interval formed by the two configured-bound
   model premiums -> ``ROOT_NOT_BRACKETED``;
8. bisection converges within ``max_iterations`` -> ``SUCCESS`` / ``CONVERGED``;
9. ``max_iterations`` exhausted without both tolerances met ->
   ``MAX_ITERATIONS_REACHED``.

Theoretical-bound failures (1-4) never evaluate the configured-bound
premiums: those fields, the implied/model/residual fields, and the final
bracket all stay ``None``, with ``iterations = 0``.

Bisection convergence requires **both**, inclusively, at the same
iteration: ``abs(premium_residual_per_100) <= premium_tolerance_per_100``
and ``final_bracket_upper_price_vol - final_bracket_lower_price_vol <=
price_vol_tolerance``. Neither tolerance alone is sufficient. On exhausting
``max_iterations`` without both holding simultaneously, the last midpoint,
model premium, signed residual, and bracket are preserved as diagnostics on
a ``FAILED`` / ``MAX_ITERATIONS_REACHED`` result -- never presented as a
converged/calibrated value, never clamped, never widened.

**Contract violations vs. economic failures.** Invalid arguments (wrong
type, ``bool`` where a number/integer is required, ``NaN``/infinite,
non-positive F/K/T/DF, a negative target, a malformed volatility-bound or
tolerance/iteration configuration) raise a field-specific ``ValueError`` --
these are programming/contract violations, not domain outcomes. An economic
inability to calibrate within the approved bounds (any of the seven
non-``CONVERGED`` reasons) is returned as a frozen, immutable result with
``status = FAILED`` -- never an exception.

**Golden-case boundary.** Every fixture in this module's test suite is
explicitly synthetic. None represents Bloomberg output, a golden case,
market calibration evidence, or UAT; Issue #94's sanitized Bloomberg golden
case gates only licensed-environment UAT evidence, not this synthetic,
deterministic solver implementation (Issue #99 methodology comment §9).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from shiori_pricing_lab.pricing.bli_black76_price_option import (
    black76_dirty_price_option_pv_per_100,
    black76_price_option_pv_per_100,
)
from shiori_pricing_lab.products.enums import OptionType, coerce_enum

_DEFAULT_LOWER_PRICE_VOL = 0.000001
_DEFAULT_UPPER_PRICE_VOL = 5.0
_DEFAULT_PREMIUM_TOLERANCE_PER_100 = 1e-8
_DEFAULT_PRICE_VOL_TOLERANCE = 1e-8
_DEFAULT_MAX_ITERATIONS = 100


class BLIImpliedPriceVolSolverStatus(StrEnum):
    """Outcome of one implied-PRICE_VOL solve (shared by both wrappers)."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class BLIImpliedPriceVolSolverReason(StrEnum):
    """Machine-readable reason for a solver result's status (shared by both wrappers).

    Only ``CONVERGED`` may pair with ``SUCCESS``; every other member is a
    ``FAILED`` reason, returned in the fixed priority order documented on
    :func:`solve_implied_price_vol` / :func:`solve_implied_dirty_price_vol`.
    """

    CONVERGED = "CONVERGED"
    BELOW_ARBITRAGE_LOWER_BOUND = "BELOW_ARBITRAGE_LOWER_BOUND"
    AT_ARBITRAGE_LOWER_BOUND = "AT_ARBITRAGE_LOWER_BOUND"
    AT_ARBITRAGE_UPPER_BOUND = "AT_ARBITRAGE_UPPER_BOUND"
    ABOVE_ARBITRAGE_UPPER_BOUND = "ABOVE_ARBITRAGE_UPPER_BOUND"
    ROOT_NOT_BRACKETED = "ROOT_NOT_BRACKETED"
    MAX_ITERATIONS_REACHED = "MAX_ITERATIONS_REACHED"


@dataclass(frozen=True)
class BLIImpliedPriceVolSolverResult:
    """One deterministic implied-PRICE_VOL solve outcome (clean basis).

    Every input echoed back (F/K/T/DF, option type, target, bounds,
    tolerances, max iterations) is preserved verbatim so the call is
    self-describing and replayable. The theoretical arbitrage bounds and,
    once evaluated, the configured-bound model premiums are always stored.
    ``implied_price_vol``/``model_premium_per_100``/
    ``premium_residual_per_100``/the final bracket are ``None`` only for
    the four theoretical-bound failure reasons; every other reason
    (including ``MAX_ITERATIONS_REACHED``) preserves the last computed
    values as diagnostics.
    """

    status: BLIImpliedPriceVolSolverStatus
    reason: BLIImpliedPriceVolSolverReason
    option_type: OptionType
    forward_clean_price: float
    strike_clean_price: float
    time_to_expiry: float
    discount_factor: float
    target_premium_per_100: float
    lower_price_vol: float
    upper_price_vol: float
    premium_tolerance_per_100: float
    price_vol_tolerance: float
    max_iterations: int
    arbitrage_lower_bound_per_100: float
    arbitrage_upper_bound_per_100: float
    lower_bound_model_premium_per_100: float | None
    upper_bound_model_premium_per_100: float | None
    implied_price_vol: float | None
    model_premium_per_100: float | None
    premium_residual_per_100: float | None
    iterations: int
    final_bracket_lower_price_vol: float | None
    final_bracket_upper_price_vol: float | None
    diagnostic_note: str


@dataclass(frozen=True)
class BLIImpliedDirtyPriceVolSolverResult:
    """One deterministic implied-PRICE_VOL solve outcome, dirty basis (Issue #P1-06).

    Same solve semantics and field roles as :class:`BLIImpliedPriceVolSolverResult`
    (see its docstring), but ``forward_dirty_price``/``strike_dirty_price``
    are dirty prices (clean plus accrued interest at the bond forward
    settlement date) -- never passed through a field whose name claims they
    are clean. Every trial premium behind this result was computed by
    ``black76_dirty_price_option_pv_per_100``, not the clean wrapper.
    """

    status: BLIImpliedPriceVolSolverStatus
    reason: BLIImpliedPriceVolSolverReason
    option_type: OptionType
    forward_dirty_price: float
    strike_dirty_price: float
    time_to_expiry: float
    discount_factor: float
    target_premium_per_100: float
    lower_price_vol: float
    upper_price_vol: float
    premium_tolerance_per_100: float
    price_vol_tolerance: float
    max_iterations: int
    arbitrage_lower_bound_per_100: float
    arbitrage_upper_bound_per_100: float
    lower_bound_model_premium_per_100: float | None
    upper_bound_model_premium_per_100: float | None
    implied_price_vol: float | None
    model_premium_per_100: float | None
    premium_residual_per_100: float | None
    iterations: int
    final_bracket_lower_price_vol: float | None
    final_bracket_upper_price_vol: float | None
    diagnostic_note: str


def _require_finite_non_bool_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    return float(value)


def _require_positive(value: float, field_name: str) -> float:
    if not value > 0:
        raise ValueError(f"{field_name} must be positive, got {value!r}")
    return value


def _require_positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be a non-bool int, got {value!r}")
    if not value > 0:
        raise ValueError(f"{field_name} must be positive, got {value!r}")
    return value


def _validate_common_solver_arguments(
    *,
    forward_price: float,
    strike_price: float,
    time_to_expiry: float,
    discount_factor: float,
    target_premium_per_100: float,
    lower_price_vol: float,
    upper_price_vol: float,
    premium_tolerance_per_100: float,
    price_vol_tolerance: float,
    max_iterations: int,
    forward_field_name: str,
    strike_field_name: str,
) -> tuple[float, float, float, float, float, float, float, float, float, int]:
    """Validate the argument set shared by both public solver wrappers.

    Returns the coerced values in the same order they were passed in.
    ``forward_field_name``/``strike_field_name`` let each wrapper raise
    ``ValueError`` under its own honestly-named argument (``forward_clean_price``
    vs ``forward_dirty_price``, etc.); every other field name (the
    volatility bounds, tolerances, target, and iteration count) is the same
    across both wrappers.
    """

    forward_price = _require_positive(
        _require_finite_non_bool_number(forward_price, forward_field_name), forward_field_name
    )
    strike_price = _require_positive(
        _require_finite_non_bool_number(strike_price, strike_field_name), strike_field_name
    )
    time_to_expiry = _require_positive(
        _require_finite_non_bool_number(time_to_expiry, "time_to_expiry"), "time_to_expiry"
    )
    discount_factor = _require_positive(
        _require_finite_non_bool_number(discount_factor, "discount_factor"), "discount_factor"
    )
    target_premium_per_100 = _require_finite_non_bool_number(
        target_premium_per_100, "target_premium_per_100"
    )
    if target_premium_per_100 < 0:
        raise ValueError(
            f"target_premium_per_100 must be non-negative, got {target_premium_per_100!r}"
        )

    lower_price_vol = _require_finite_non_bool_number(lower_price_vol, "lower_price_vol")
    if not lower_price_vol > 0:
        raise ValueError(f"lower_price_vol must be positive, got {lower_price_vol!r}")
    upper_price_vol = _require_finite_non_bool_number(upper_price_vol, "upper_price_vol")
    if not lower_price_vol < upper_price_vol:
        raise ValueError(
            "lower_price_vol must be strictly less than upper_price_vol, got "
            f"lower_price_vol={lower_price_vol!r} upper_price_vol={upper_price_vol!r}"
        )

    premium_tolerance_per_100 = _require_positive(
        _require_finite_non_bool_number(premium_tolerance_per_100, "premium_tolerance_per_100"),
        "premium_tolerance_per_100",
    )
    price_vol_tolerance = _require_positive(
        _require_finite_non_bool_number(price_vol_tolerance, "price_vol_tolerance"),
        "price_vol_tolerance",
    )
    max_iterations = _require_positive_int(max_iterations, "max_iterations")

    return (
        forward_price,
        strike_price,
        time_to_expiry,
        discount_factor,
        target_premium_per_100,
        lower_price_vol,
        upper_price_vol,
        premium_tolerance_per_100,
        price_vol_tolerance,
        max_iterations,
    )


@dataclass(frozen=True)
class _BoundedImpliedVolOutcome:
    """Price-basis-neutral bisection outcome, internal to this module only.

    Carries none of F/K/T/DF/option_type/bounds/tolerances/max_iterations
    (the caller already has and echoes those); only the values the shared
    core actually computes.
    """

    status: BLIImpliedPriceVolSolverStatus
    reason: BLIImpliedPriceVolSolverReason
    diagnostic_note: str
    arbitrage_lower_bound_per_100: float
    arbitrage_upper_bound_per_100: float
    lower_bound_model_premium_per_100: float | None
    upper_bound_model_premium_per_100: float | None
    implied_price_vol: float | None
    model_premium_per_100: float | None
    premium_residual_per_100: float | None
    iterations: int
    final_bracket_lower_price_vol: float | None
    final_bracket_upper_price_vol: float | None


def _solve_bounded_implied_price_vol_core(
    *,
    price_at,
    forward_price: float,
    strike_price: float,
    discount_factor: float,
    option_type: OptionType,
    target_premium_per_100: float,
    lower_price_vol: float,
    upper_price_vol: float,
    premium_tolerance_per_100: float,
    price_vol_tolerance: float,
    max_iterations: int,
) -> _BoundedImpliedVolOutcome:
    """Price-basis-neutral bisection core shared by both public solver wrappers.

    Carries **no price-basis assumption of its own**: ``forward_price``/
    ``strike_price`` are whatever basis (clean or dirty) the caller already
    resolved, and ``price_at`` is the caller's own already-basis-correct
    trial-pricing closure (``black76_price_option_pv_per_100`` for the clean
    wrapper, ``black76_dirty_price_option_pv_per_100`` for the dirty
    wrapper) -- this function never calls a Black-76 wrapper itself. Every
    argument has already been validated by the public wrapper; this
    function performs no further input validation. See the module
    docstring for the exact failure/solve order and bisection semantics.
    """

    if option_type is OptionType.CALL:
        arbitrage_lower_bound_per_100 = discount_factor * max(forward_price - strike_price, 0.0)
        arbitrage_upper_bound_per_100 = discount_factor * forward_price
    else:
        arbitrage_lower_bound_per_100 = discount_factor * max(strike_price - forward_price, 0.0)
        arbitrage_upper_bound_per_100 = discount_factor * strike_price

    def _price_at_configured_bound(sigma: float, bound_field_name: str) -> float:
        """Price ``sigma`` at a configured vol bound, or raise a field-specific error.

        A finite, correctly-ordered ``lower_price_vol``/``upper_price_vol``
        can still sit outside the numeric domain the Black-76 trial-pricing
        closure can evaluate (e.g. ``sigma**2`` overflowing for an extreme
        ``upper_price_vol``). That is a configuration problem with the named
        bound, not a domain outcome, so it is raised as a field-specific
        ``ValueError`` here rather than leaking whatever raw exception the
        primitive happened to raise, and rather than being silently caught
        into a structured failure result. No new cap, clamping, or bound
        narrowing/expansion is introduced -- the caller-supplied bound is
        only ever evaluated as given.
        """

        try:
            price = price_at(sigma)
        except (OverflowError, ValueError) as exc:
            raise ValueError(
                f"{bound_field_name} ({sigma!r}) is outside the numeric domain the "
                "Black-76 trial-pricing primitive can evaluate: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if not math.isfinite(price):
            raise ValueError(
                f"{bound_field_name} ({sigma!r}) produced a non-finite model premium "
                f"({price!r}) from the Black-76 trial-pricing primitive"
            )
        return price

    def _outcome(
        *,
        status: BLIImpliedPriceVolSolverStatus,
        reason: BLIImpliedPriceVolSolverReason,
        diagnostic_note: str,
        lower_bound_model_premium_per_100: float | None = None,
        upper_bound_model_premium_per_100: float | None = None,
        implied_price_vol: float | None = None,
        model_premium_per_100: float | None = None,
        premium_residual_per_100: float | None = None,
        iterations: int = 0,
        final_bracket_lower_price_vol: float | None = None,
        final_bracket_upper_price_vol: float | None = None,
    ) -> _BoundedImpliedVolOutcome:
        return _BoundedImpliedVolOutcome(
            status=status,
            reason=reason,
            diagnostic_note=diagnostic_note,
            arbitrage_lower_bound_per_100=arbitrage_lower_bound_per_100,
            arbitrage_upper_bound_per_100=arbitrage_upper_bound_per_100,
            lower_bound_model_premium_per_100=lower_bound_model_premium_per_100,
            upper_bound_model_premium_per_100=upper_bound_model_premium_per_100,
            implied_price_vol=implied_price_vol,
            model_premium_per_100=model_premium_per_100,
            premium_residual_per_100=premium_residual_per_100,
            iterations=iterations,
            final_bracket_lower_price_vol=final_bracket_lower_price_vol,
            final_bracket_upper_price_vol=final_bracket_upper_price_vol,
        )

    # --- 1-4. Theoretical arbitrage bounds (before any configured-vol work) --
    if target_premium_per_100 < arbitrage_lower_bound_per_100:
        return _outcome(
            status=BLIImpliedPriceVolSolverStatus.FAILED,
            reason=BLIImpliedPriceVolSolverReason.BELOW_ARBITRAGE_LOWER_BOUND,
            diagnostic_note=(
                f"target_premium_per_100 ({target_premium_per_100!r}) is below the "
                f"theoretical arbitrage lower bound ({arbitrage_lower_bound_per_100!r}) -- "
                "no finite positive volatility can produce this premium"
            ),
        )
    if target_premium_per_100 == arbitrage_lower_bound_per_100:
        return _outcome(
            status=BLIImpliedPriceVolSolverStatus.FAILED,
            reason=BLIImpliedPriceVolSolverReason.AT_ARBITRAGE_LOWER_BOUND,
            diagnostic_note=(
                f"target_premium_per_100 ({target_premium_per_100!r}) exactly equals the "
                f"theoretical arbitrage lower bound ({arbitrage_lower_bound_per_100!r}) -- "
                "this requires the disallowed sigma=0 limit, not a valid finite "
                "positive-volatility calibration result"
            ),
        )
    if target_premium_per_100 == arbitrage_upper_bound_per_100:
        return _outcome(
            status=BLIImpliedPriceVolSolverStatus.FAILED,
            reason=BLIImpliedPriceVolSolverReason.AT_ARBITRAGE_UPPER_BOUND,
            diagnostic_note=(
                f"target_premium_per_100 ({target_premium_per_100!r}) exactly equals the "
                f"theoretical arbitrage upper bound ({arbitrage_upper_bound_per_100!r}) -- "
                "this requires an infinite-volatility limit, not a valid finite "
                "positive-volatility calibration result"
            ),
        )
    if target_premium_per_100 > arbitrage_upper_bound_per_100:
        return _outcome(
            status=BLIImpliedPriceVolSolverStatus.FAILED,
            reason=BLIImpliedPriceVolSolverReason.ABOVE_ARBITRAGE_UPPER_BOUND,
            diagnostic_note=(
                f"target_premium_per_100 ({target_premium_per_100!r}) is above the "
                f"theoretical arbitrage upper bound ({arbitrage_upper_bound_per_100!r}) -- "
                "no finite positive volatility can produce this premium"
            ),
        )

    # --- 5-6. Configured-bound boundary evaluation -----------------------------
    lower_bound_model_premium_per_100 = _price_at_configured_bound(
        lower_price_vol, "lower_price_vol"
    )
    upper_bound_model_premium_per_100 = _price_at_configured_bound(
        upper_price_vol, "upper_price_vol"
    )

    lower_residual = lower_bound_model_premium_per_100 - target_premium_per_100
    if abs(lower_residual) <= premium_tolerance_per_100:
        return _outcome(
            status=BLIImpliedPriceVolSolverStatus.SUCCESS,
            reason=BLIImpliedPriceVolSolverReason.CONVERGED,
            diagnostic_note=(
                f"target_premium_per_100 ({target_premium_per_100!r}) is within "
                f"premium_tolerance_per_100 ({premium_tolerance_per_100!r}) of the model "
                f"premium at lower_price_vol ({lower_price_vol!r}) -- converged at the "
                "configured lower volatility bound"
            ),
            lower_bound_model_premium_per_100=lower_bound_model_premium_per_100,
            upper_bound_model_premium_per_100=upper_bound_model_premium_per_100,
            implied_price_vol=lower_price_vol,
            model_premium_per_100=lower_bound_model_premium_per_100,
            premium_residual_per_100=lower_residual,
            iterations=0,
            final_bracket_lower_price_vol=lower_price_vol,
            final_bracket_upper_price_vol=lower_price_vol,
        )

    upper_residual = upper_bound_model_premium_per_100 - target_premium_per_100
    if abs(upper_residual) <= premium_tolerance_per_100:
        return _outcome(
            status=BLIImpliedPriceVolSolverStatus.SUCCESS,
            reason=BLIImpliedPriceVolSolverReason.CONVERGED,
            diagnostic_note=(
                f"target_premium_per_100 ({target_premium_per_100!r}) is within "
                f"premium_tolerance_per_100 ({premium_tolerance_per_100!r}) of the model "
                f"premium at upper_price_vol ({upper_price_vol!r}) -- converged at the "
                "configured upper volatility bound"
            ),
            lower_bound_model_premium_per_100=lower_bound_model_premium_per_100,
            upper_bound_model_premium_per_100=upper_bound_model_premium_per_100,
            implied_price_vol=upper_price_vol,
            model_premium_per_100=upper_bound_model_premium_per_100,
            premium_residual_per_100=upper_residual,
            iterations=0,
            final_bracket_lower_price_vol=upper_price_vol,
            final_bracket_upper_price_vol=upper_price_vol,
        )

    # --- 7. Root-bracketing check ------------------------------------------------
    if (
        target_premium_per_100 < lower_bound_model_premium_per_100
        or target_premium_per_100 > upper_bound_model_premium_per_100
    ):
        return _outcome(
            status=BLIImpliedPriceVolSolverStatus.FAILED,
            reason=BLIImpliedPriceVolSolverReason.ROOT_NOT_BRACKETED,
            diagnostic_note=(
                f"target_premium_per_100 ({target_premium_per_100!r}) is outside the "
                "inclusive interval formed by the model premiums at the configured "
                f"volatility bounds (lower={lower_bound_model_premium_per_100!r}, "
                f"upper={upper_bound_model_premium_per_100!r}) -- not bracketed, and "
                "the configured bounds are never clamped or widened"
            ),
            lower_bound_model_premium_per_100=lower_bound_model_premium_per_100,
            upper_bound_model_premium_per_100=upper_bound_model_premium_per_100,
            iterations=0,
            final_bracket_lower_price_vol=lower_price_vol,
            final_bracket_upper_price_vol=upper_price_vol,
        )

    # --- 8-9. Bisection ------------------------------------------------------------
    bracket_lower = lower_price_vol
    bracket_upper = upper_price_vol
    midpoint = model_premium = residual = None

    for iteration in range(1, max_iterations + 1):
        midpoint = (bracket_lower + bracket_upper) / 2.0
        model_premium = price_at(midpoint)
        residual = model_premium - target_premium_per_100

        if residual < 0:
            bracket_lower = midpoint
        elif residual > 0:
            bracket_upper = midpoint
        else:
            bracket_lower = midpoint
            bracket_upper = midpoint

        if (
            abs(residual) <= premium_tolerance_per_100
            and (bracket_upper - bracket_lower) <= price_vol_tolerance
        ):
            return _outcome(
                status=BLIImpliedPriceVolSolverStatus.SUCCESS,
                reason=BLIImpliedPriceVolSolverReason.CONVERGED,
                diagnostic_note=(
                    f"bisection converged after {iteration} iteration(s): "
                    f"abs(premium_residual_per_100)={abs(residual)!r} <= "
                    f"premium_tolerance_per_100 ({premium_tolerance_per_100!r}) and "
                    f"bracket width={bracket_upper - bracket_lower!r} <= "
                    f"price_vol_tolerance ({price_vol_tolerance!r})"
                ),
                lower_bound_model_premium_per_100=lower_bound_model_premium_per_100,
                upper_bound_model_premium_per_100=upper_bound_model_premium_per_100,
                implied_price_vol=midpoint,
                model_premium_per_100=model_premium,
                premium_residual_per_100=residual,
                iterations=iteration,
                final_bracket_lower_price_vol=bracket_lower,
                final_bracket_upper_price_vol=bracket_upper,
            )

    return _outcome(
        status=BLIImpliedPriceVolSolverStatus.FAILED,
        reason=BLIImpliedPriceVolSolverReason.MAX_ITERATIONS_REACHED,
        diagnostic_note=(
            f"bisection did not converge within max_iterations ({max_iterations!r}) -- "
            f"last midpoint={midpoint!r}, last premium_residual_per_100={residual!r}; "
            "preserved as diagnostics only, never presented as a calibrated success"
        ),
        lower_bound_model_premium_per_100=lower_bound_model_premium_per_100,
        upper_bound_model_premium_per_100=upper_bound_model_premium_per_100,
        implied_price_vol=midpoint,
        model_premium_per_100=model_premium,
        premium_residual_per_100=residual,
        iterations=max_iterations,
        final_bracket_lower_price_vol=bracket_lower,
        final_bracket_upper_price_vol=bracket_upper,
    )


def solve_implied_price_vol(
    *,
    forward_clean_price: float,
    strike_clean_price: float,
    time_to_expiry: float,
    discount_factor: float,
    option_type: OptionType | str,
    target_premium_per_100: float,
    lower_price_vol: float = _DEFAULT_LOWER_PRICE_VOL,
    upper_price_vol: float = _DEFAULT_UPPER_PRICE_VOL,
    premium_tolerance_per_100: float = _DEFAULT_PREMIUM_TOLERANCE_PER_100,
    price_vol_tolerance: float = _DEFAULT_PRICE_VOL_TOLERANCE,
    max_iterations: int = _DEFAULT_MAX_ITERATIONS,
) -> BLIImpliedPriceVolSolverResult:
    """Solve for the single implied ``PRICE_VOL`` matching ``target_premium_per_100`` (clean basis).

    Pure function: ``forward_clean_price``/``strike_clean_price``/
    ``time_to_expiry``/``discount_factor`` are already-resolved plain
    floats (never a request, bundle, or curve lookup); ``option_type`` is
    coerced via the existing ``products.enums.OptionType`` vocabulary. Every
    trial premium is priced by ``black76_price_option_pv_per_100`` (clean
    basis). See the module docstring for the exact failure/solve order,
    bisection semantics, and the contract-violation vs. economic-failure
    boundary -- identical for the dirty-basis
    :func:`solve_implied_dirty_price_vol`.
    """

    option_type = coerce_enum(option_type, OptionType, "option_type")

    (
        forward_clean_price,
        strike_clean_price,
        time_to_expiry,
        discount_factor,
        target_premium_per_100,
        lower_price_vol,
        upper_price_vol,
        premium_tolerance_per_100,
        price_vol_tolerance,
        max_iterations,
    ) = _validate_common_solver_arguments(
        forward_price=forward_clean_price,
        strike_price=strike_clean_price,
        time_to_expiry=time_to_expiry,
        discount_factor=discount_factor,
        target_premium_per_100=target_premium_per_100,
        lower_price_vol=lower_price_vol,
        upper_price_vol=upper_price_vol,
        premium_tolerance_per_100=premium_tolerance_per_100,
        price_vol_tolerance=price_vol_tolerance,
        max_iterations=max_iterations,
        forward_field_name="forward_clean_price",
        strike_field_name="strike_clean_price",
    )

    def _price_at(sigma: float) -> float:
        return black76_price_option_pv_per_100(
            forward_clean_price=forward_clean_price,
            strike_clean_price=strike_clean_price,
            price_volatility=sigma,
            time_to_expiry=time_to_expiry,
            discount_factor=discount_factor,
            option_type=option_type,
        )

    outcome = _solve_bounded_implied_price_vol_core(
        price_at=_price_at,
        forward_price=forward_clean_price,
        strike_price=strike_clean_price,
        discount_factor=discount_factor,
        option_type=option_type,
        target_premium_per_100=target_premium_per_100,
        lower_price_vol=lower_price_vol,
        upper_price_vol=upper_price_vol,
        premium_tolerance_per_100=premium_tolerance_per_100,
        price_vol_tolerance=price_vol_tolerance,
        max_iterations=max_iterations,
    )

    return BLIImpliedPriceVolSolverResult(
        status=outcome.status,
        reason=outcome.reason,
        option_type=option_type,
        forward_clean_price=forward_clean_price,
        strike_clean_price=strike_clean_price,
        time_to_expiry=time_to_expiry,
        discount_factor=discount_factor,
        target_premium_per_100=target_premium_per_100,
        lower_price_vol=lower_price_vol,
        upper_price_vol=upper_price_vol,
        premium_tolerance_per_100=premium_tolerance_per_100,
        price_vol_tolerance=price_vol_tolerance,
        max_iterations=max_iterations,
        arbitrage_lower_bound_per_100=outcome.arbitrage_lower_bound_per_100,
        arbitrage_upper_bound_per_100=outcome.arbitrage_upper_bound_per_100,
        lower_bound_model_premium_per_100=outcome.lower_bound_model_premium_per_100,
        upper_bound_model_premium_per_100=outcome.upper_bound_model_premium_per_100,
        implied_price_vol=outcome.implied_price_vol,
        model_premium_per_100=outcome.model_premium_per_100,
        premium_residual_per_100=outcome.premium_residual_per_100,
        iterations=outcome.iterations,
        final_bracket_lower_price_vol=outcome.final_bracket_lower_price_vol,
        final_bracket_upper_price_vol=outcome.final_bracket_upper_price_vol,
        diagnostic_note=outcome.diagnostic_note,
    )


def solve_implied_dirty_price_vol(
    *,
    forward_dirty_price: float,
    strike_dirty_price: float,
    time_to_expiry: float,
    discount_factor: float,
    option_type: OptionType | str,
    target_premium_per_100: float,
    lower_price_vol: float = _DEFAULT_LOWER_PRICE_VOL,
    upper_price_vol: float = _DEFAULT_UPPER_PRICE_VOL,
    premium_tolerance_per_100: float = _DEFAULT_PREMIUM_TOLERANCE_PER_100,
    price_vol_tolerance: float = _DEFAULT_PRICE_VOL_TOLERANCE,
    max_iterations: int = _DEFAULT_MAX_ITERATIONS,
) -> BLIImpliedDirtyPriceVolSolverResult:
    """Solve for the single implied ``PRICE_VOL`` matching ``target_premium_per_100`` (dirty basis).

    Issue #P1-06: the OVME-aligned standalone calibration path prices
    Black-76 on dirty forward/strike (matching
    ``price_bli_mvp_standalone_option``'s own composition), so its implied
    PRICE_VOL calibration must solve on the same dirty basis.
    ``forward_dirty_price``/``strike_dirty_price`` are already-resolved
    dirty prices (clean plus accrued interest at the bond forward
    settlement date) -- plain floats, never a request/bundle/curve lookup,
    and never confused with a clean price by argument name. Every trial
    premium is priced by ``black76_dirty_price_option_pv_per_100`` (dirty
    basis) -- the clean wrapper is never called here. See the module
    docstring for the exact failure/solve order, bisection semantics, and
    the contract-violation vs. economic-failure boundary -- identical to
    the clean-basis :func:`solve_implied_price_vol`.
    """

    option_type = coerce_enum(option_type, OptionType, "option_type")

    (
        forward_dirty_price,
        strike_dirty_price,
        time_to_expiry,
        discount_factor,
        target_premium_per_100,
        lower_price_vol,
        upper_price_vol,
        premium_tolerance_per_100,
        price_vol_tolerance,
        max_iterations,
    ) = _validate_common_solver_arguments(
        forward_price=forward_dirty_price,
        strike_price=strike_dirty_price,
        time_to_expiry=time_to_expiry,
        discount_factor=discount_factor,
        target_premium_per_100=target_premium_per_100,
        lower_price_vol=lower_price_vol,
        upper_price_vol=upper_price_vol,
        premium_tolerance_per_100=premium_tolerance_per_100,
        price_vol_tolerance=price_vol_tolerance,
        max_iterations=max_iterations,
        forward_field_name="forward_dirty_price",
        strike_field_name="strike_dirty_price",
    )

    def _price_at(sigma: float) -> float:
        return black76_dirty_price_option_pv_per_100(
            forward_dirty_price=forward_dirty_price,
            strike_dirty_price=strike_dirty_price,
            price_volatility=sigma,
            time_to_expiry=time_to_expiry,
            discount_factor=discount_factor,
            option_type=option_type,
        )

    outcome = _solve_bounded_implied_price_vol_core(
        price_at=_price_at,
        forward_price=forward_dirty_price,
        strike_price=strike_dirty_price,
        discount_factor=discount_factor,
        option_type=option_type,
        target_premium_per_100=target_premium_per_100,
        lower_price_vol=lower_price_vol,
        upper_price_vol=upper_price_vol,
        premium_tolerance_per_100=premium_tolerance_per_100,
        price_vol_tolerance=price_vol_tolerance,
        max_iterations=max_iterations,
    )

    return BLIImpliedDirtyPriceVolSolverResult(
        status=outcome.status,
        reason=outcome.reason,
        option_type=option_type,
        forward_dirty_price=forward_dirty_price,
        strike_dirty_price=strike_dirty_price,
        time_to_expiry=time_to_expiry,
        discount_factor=discount_factor,
        target_premium_per_100=target_premium_per_100,
        lower_price_vol=lower_price_vol,
        upper_price_vol=upper_price_vol,
        premium_tolerance_per_100=premium_tolerance_per_100,
        price_vol_tolerance=price_vol_tolerance,
        max_iterations=max_iterations,
        arbitrage_lower_bound_per_100=outcome.arbitrage_lower_bound_per_100,
        arbitrage_upper_bound_per_100=outcome.arbitrage_upper_bound_per_100,
        lower_bound_model_premium_per_100=outcome.lower_bound_model_premium_per_100,
        upper_bound_model_premium_per_100=outcome.upper_bound_model_premium_per_100,
        implied_price_vol=outcome.implied_price_vol,
        model_premium_per_100=outcome.model_premium_per_100,
        premium_residual_per_100=outcome.premium_residual_per_100,
        iterations=outcome.iterations,
        final_bracket_lower_price_vol=outcome.final_bracket_lower_price_vol,
        final_bracket_upper_price_vol=outcome.final_bracket_upper_price_vol,
        diagnostic_note=outcome.diagnostic_note,
    )
