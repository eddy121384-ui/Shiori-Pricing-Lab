"""``compare_bli_benchmark``: deterministic model-vs-benchmark comparison (Issue #98 PR B).

Scope, per the methodology approved in Issue #94 comment 4965245314 and the
implementation slice authorized in Issue #98 comment 4965248690: a pure,
deterministic comparison of one already-computed ``PricingResult`` against
one already-observed ``BLIBenchmarkQuote`` (the PR A contract, Issue #98 PR
A / #109). This module adds an **independent sibling result** --
``BLIBenchmarkComparisonResult`` -- and does not modify ``PricingResult``,
the pricing engine, the benchmark quote contract, or any request/snapshot
schema. It performs no calibration, no curve/vol work, no Bloomberg call,
and no persistence.

**Model premium source (Issue #94 §7).** The model side of the comparison
comes from exactly two existing ``PricingResult`` fields:
``pricing_result.pv`` (model total premium) and
``pricing_result.assumptions["black76_pv_per_100"]`` (model fair premium
per 100). Neither is ever derived from the other or from
``request``'s notional -- both are read verbatim, or the comparison
reports them as unavailable.

**Deterministic mismatch priority (Issue #94 §8).** Exactly one reason is
returned for any pre-comparison mismatch, following the fixed priority
order below; downstream checks are never evaluated once an earlier one has
already failed:

1. ``MODEL_RESULT_NOT_SUCCESS`` -- the pricing result itself is ``FAILED``.
2. ``MODEL_PREMIUM_UNAVAILABLE`` -- ``pv`` and/or
   ``assumptions["black76_pv_per_100"]`` is absent, a ``bool``,
   non-numeric, ``NaN``, or infinite.
3. ``PRODUCT_ID_MISMATCH`` -- ``pricing_result.product_id``,
   ``request.bond_option.product_id``, and ``benchmark.product_id`` do not
   all agree.
4. ``SNAPSHOT_ID_MISMATCH`` -- ``request.market_data_snapshot.snapshot_id``
   != ``benchmark.snapshot_id``.
5. ``UNDERLYING_ID_MISMATCH`` -- ``request.bond_option.underlying_isin`` !=
   ``benchmark.underlying_id``.
6. ``CURRENCY_MISMATCH`` -- ``pricing_result.result_currency``,
   ``request.bond_option.currency.value``, and ``benchmark.currency.value``
   do not all agree.
7. ``QUOTE_SIDE_MISMATCH`` -- the caller-supplied ``active_quote_side``
   (after the same local-enum coercion as ``BLIBenchmarkQuote``) is not
   ``benchmark.quote_side``.
8. ``SOURCE_DATE_MISMATCH`` -- the calendar dates of
   ``request.valuation_date``, ``request.market_data_snapshot.as_of_timestamp``,
   and ``benchmark.source_as_of`` are not all identical.
9. ``NEAR_ZERO_BENCHMARK`` -- ``abs(benchmark.premium_per_100) <=
   near_zero_threshold_per_100``.

For reasons 1-8 every residual field is ``None`` and no partial tolerance
outcome is computed. For reason 9 the signed and absolute residual per 100
are still computed and retained (the model premium is known and the
benchmark premium, though near zero, is still a real number); only
``relative_residual`` is ``None`` because dividing by a near-zero
denominator is not a meaningful ratio.

``benchmark.retrieved_at`` is stored on the result for provenance only --
Phase 1 has no stale-hour window, date normalization, fallback date, or
automatic substitution, and ``retrieved_at`` never participates in any
comparability check (Issue #94 §4).

**Comparable calculation (Issue #94 §1/§2).** When every alignment check
passes and the benchmark premium is not near zero:

.. code-block:: text

    signed_residual_per_100 = model_fair_premium_per_100 - benchmark.premium_per_100
    absolute_residual_per_100 = abs(signed_residual_per_100)
    relative_residual = absolute_residual_per_100 / abs(benchmark.premium_per_100)

``relative_residual`` (always non-negative) is the *only* metric used for
status:

- ``< pass_threshold`` -> ``PASS``
- ``pass_threshold <= relative_residual <= fail_threshold`` -> ``WARNING``
- ``> fail_threshold`` -> ``FAIL``

Thresholds (``pass_threshold``, ``fail_threshold``,
``near_zero_threshold_per_100``) are caller-configurable with Phase 1
defaults of ``0.02`` / ``0.05`` / ``0.01``; the actual values used are
always stored on the result so a comparison replays deterministically.

**Unit boundary (Issue #94 §6).** The official PASS/WARNING/FAIL status
uses per-100 values only. ``pricing_result.pv`` and
``benchmark.total_premium`` are preserved verbatim on the result for
display, but this module never compares, converts, derives, or reconciles
either total premium against a per-100 premium or against
``request``'s notional.

**Independent local quote-side enum boundary (mirrors PR A / Issue #94
decision #2).** ``active_quote_side`` accepts a native
``BLIBenchmarkQuoteSide`` member or a raw ``"BID"``/``"MID"``/``"OFFER"``
string. Because ``products.enums.coerce_enum`` coerces by *value*, a
foreign ``StrEnum`` sharing one of those string values (for example
``TreasuryFTPQuoteSide.BID``) is rejected explicitly before coercion --
the same local guard pattern already used by ``BLIBenchmarkQuote``.

**Hard non-goals (Issue #98 PR B scope cap):** no Bloomberg
adapter/field/function/export route, no golden case, no calibration or
implied-vol solver, no curve/vol/pricing changes, no pricing-engine wiring,
no UI/workbench, no persistence/versioning, no quote-ID lifecycle, no
client quote or margin, and no total-premium reconciliation. This module
imports from ``data.bli_benchmark_quote``, ``data.bli_standalone_option_request``,
``data._validation``, ``pricing.result``, and ``products.enums`` only, and
calls no pricing function, provider, or system clock.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import Enum, StrEnum

from shiori_pricing_lab.data._validation import _parse_as_of_calendar_date, _parse_iso_date
from shiori_pricing_lab.data.bli_benchmark_quote import BLIBenchmarkQuote, BLIBenchmarkQuoteSide
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.pricing.result import PricingResult
from shiori_pricing_lab.products.enums import Currency, coerce_enum


class BLIBenchmarkComparisonStatus(StrEnum):
    """Outcome of one model-vs-benchmark comparison (Issue #94 §8)."""

    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"
    NON_COMPARABLE = "NON_COMPARABLE"


class BLIBenchmarkComparisonReason(StrEnum):
    """Machine-readable reason for a comparison's status (Issue #94 §8).

    ``COMPARABLE`` is the reason for every ``PASS``/``WARNING``/``FAIL``
    result. Every other member is a ``NON_COMPARABLE`` reason, returned in
    the fixed priority order documented on :func:`compare_bli_benchmark`.
    """

    COMPARABLE = "COMPARABLE"
    MODEL_RESULT_NOT_SUCCESS = "MODEL_RESULT_NOT_SUCCESS"
    MODEL_PREMIUM_UNAVAILABLE = "MODEL_PREMIUM_UNAVAILABLE"
    PRODUCT_ID_MISMATCH = "PRODUCT_ID_MISMATCH"
    SNAPSHOT_ID_MISMATCH = "SNAPSHOT_ID_MISMATCH"
    UNDERLYING_ID_MISMATCH = "UNDERLYING_ID_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    QUOTE_SIDE_MISMATCH = "QUOTE_SIDE_MISMATCH"
    SOURCE_DATE_MISMATCH = "SOURCE_DATE_MISMATCH"
    NEAR_ZERO_BENCHMARK = "NEAR_ZERO_BENCHMARK"


class BLIBenchmarkComparisonMetric(StrEnum):
    """The metric used to decide PASS / WARNING / FAIL (Issue #94 §1).

    A single member for Phase 1: the official tolerance calculation uses
    only the per-100 relative residual, never a total-premium metric.
    """

    RELATIVE_RESIDUAL_PER_100 = "RELATIVE_RESIDUAL_PER_100"


def _reject_foreign_enum_active_quote_side(value: object) -> None:
    """Reject any ``Enum`` instance that is not already a ``BLIBenchmarkQuoteSide``.

    Mirrors the guard already used by ``BLIBenchmarkQuote.__post_init__``
    (``data/bli_benchmark_quote.py``): ``products.enums.coerce_enum``
    coerces by *value*, so a foreign ``StrEnum`` member sharing a string
    value (e.g. ``TreasuryFTPQuoteSide.BID == "BID"``) would otherwise pass
    through it silently. Duplicated locally rather than imported, matching
    the existing repo convention of duplicating small, module-local
    validation helpers instead of taking a cross-module dependency on a
    private function.
    """

    if isinstance(value, Enum) and not isinstance(value, BLIBenchmarkQuoteSide):
        raise ValueError(
            "active_quote_side must be a BLIBenchmarkQuoteSide member or one of "
            f"{{BID, MID, OFFER}}, got foreign enum {value!r}"
        )


def _require_valid_threshold(value: object, field_name: str) -> float:
    """Require a real, finite, non-bool number; return it as ``float``.

    ``bool`` is an ``int`` subclass in Python and is rejected explicitly.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    return float(value)


def _extract_finite_number_or_none(value: object) -> float | None:
    """Return ``value`` as ``float`` if it is a real, finite, non-bool number.

    Returns ``None`` for anything else -- absent (``None``), ``bool``,
    non-numeric, ``NaN``, or infinite -- exactly the set of values Issue
    #94 §7 requires ``MODEL_PREMIUM_UNAVAILABLE`` to catch. This function
    never raises: an invalid model premium is a domain outcome
    (``NON_COMPARABLE``), not a programming error.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


@dataclass(frozen=True)
class BLIBenchmarkComparisonResult:
    """One deterministic model-vs-benchmark comparison outcome.

    An independent sibling result -- never a mutation of the
    ``PricingResult``, ``BLIStandaloneBondOptionRequest``, or
    ``BLIBenchmarkQuote`` it was built from. All fields are preserved
    verbatim from their source objects except the computed residual/status
    fields, which follow exactly the methodology documented on
    :func:`compare_bli_benchmark`.
    """

    status: BLIBenchmarkComparisonStatus
    reason: BLIBenchmarkComparisonReason
    comparison_metric: BLIBenchmarkComparisonMetric
    active_quote_side: BLIBenchmarkQuoteSide
    pass_threshold: float
    fail_threshold: float
    near_zero_threshold_per_100: float
    product_id: str
    snapshot_id: str
    underlying_id: str
    currency: Currency
    valuation_date: str
    market_data_as_of: str
    benchmark_id: str
    benchmark_source_as_of: str
    benchmark_retrieved_at: str
    model_fair_premium_per_100: float | None
    model_total_premium: float | None
    benchmark_premium_per_100: float
    benchmark_total_premium: float
    signed_residual_per_100: float | None
    absolute_residual_per_100: float | None
    relative_residual: float | None
    alignment_note: str


def compare_bli_benchmark(
    pricing_result: PricingResult,
    request: BLIStandaloneBondOptionRequest,
    benchmark: BLIBenchmarkQuote,
    *,
    active_quote_side: BLIBenchmarkQuoteSide | str,
    pass_threshold: float = 0.02,
    fail_threshold: float = 0.05,
    near_zero_threshold_per_100: float = 0.01,
) -> BLIBenchmarkComparisonResult:
    """Deterministically compare ``pricing_result`` against ``benchmark``.

    Pure function: reads its three input objects and the threshold
    arguments only, never mutates them, calls no pricing function,
    provider, or system clock, and returns a fresh
    :class:`BLIBenchmarkComparisonResult`. See the module docstring for the
    exact mismatch-priority order, near-zero rule, and PASS/WARNING/FAIL
    boundaries.
    """

    if not isinstance(pricing_result, PricingResult):
        raise TypeError("pricing_result must be a PricingResult")
    if not isinstance(request, BLIStandaloneBondOptionRequest):
        raise TypeError("request must be a BLIStandaloneBondOptionRequest")
    if not isinstance(benchmark, BLIBenchmarkQuote):
        raise TypeError("benchmark must be a BLIBenchmarkQuote")

    _reject_foreign_enum_active_quote_side(active_quote_side)
    resolved_active_quote_side = coerce_enum(
        active_quote_side, BLIBenchmarkQuoteSide, "active_quote_side"
    )

    pass_threshold = _require_valid_threshold(pass_threshold, "pass_threshold")
    fail_threshold = _require_valid_threshold(fail_threshold, "fail_threshold")
    near_zero_threshold_per_100 = _require_valid_threshold(
        near_zero_threshold_per_100, "near_zero_threshold_per_100"
    )
    if not (0 <= pass_threshold < fail_threshold):
        raise ValueError(
            "pass_threshold and fail_threshold must satisfy "
            f"0 <= pass_threshold < fail_threshold, got pass_threshold={pass_threshold!r} "
            f"fail_threshold={fail_threshold!r}"
        )
    if near_zero_threshold_per_100 < 0:
        raise ValueError(
            "near_zero_threshold_per_100 must be >= 0, got "
            f"{near_zero_threshold_per_100!r}"
        )

    model_total_premium = _extract_finite_number_or_none(pricing_result.pv)
    model_fair_premium_per_100 = _extract_finite_number_or_none(
        pricing_result.assumptions.get("black76_pv_per_100")
    )

    def _build(
        *,
        status: BLIBenchmarkComparisonStatus,
        reason: BLIBenchmarkComparisonReason,
        alignment_note: str,
        signed_residual_per_100: float | None = None,
        absolute_residual_per_100: float | None = None,
        relative_residual: float | None = None,
    ) -> BLIBenchmarkComparisonResult:
        return BLIBenchmarkComparisonResult(
            status=status,
            reason=reason,
            comparison_metric=BLIBenchmarkComparisonMetric.RELATIVE_RESIDUAL_PER_100,
            active_quote_side=resolved_active_quote_side,
            pass_threshold=pass_threshold,
            fail_threshold=fail_threshold,
            near_zero_threshold_per_100=near_zero_threshold_per_100,
            product_id=benchmark.product_id,
            snapshot_id=benchmark.snapshot_id,
            underlying_id=benchmark.underlying_id,
            currency=benchmark.currency,
            valuation_date=request.valuation_date,
            market_data_as_of=request.market_data_snapshot.as_of_timestamp,
            benchmark_id=benchmark.benchmark_id,
            benchmark_source_as_of=benchmark.source_as_of,
            benchmark_retrieved_at=benchmark.retrieved_at,
            model_fair_premium_per_100=model_fair_premium_per_100,
            model_total_premium=model_total_premium,
            benchmark_premium_per_100=benchmark.premium_per_100,
            benchmark_total_premium=benchmark.total_premium,
            signed_residual_per_100=signed_residual_per_100,
            absolute_residual_per_100=absolute_residual_per_100,
            relative_residual=relative_residual,
            alignment_note=alignment_note,
        )

    # --- 1. MODEL_RESULT_NOT_SUCCESS ----------------------------------------
    if not pricing_result.is_success:
        return _build(
            status=BLIBenchmarkComparisonStatus.NON_COMPARABLE,
            reason=BLIBenchmarkComparisonReason.MODEL_RESULT_NOT_SUCCESS,
            alignment_note=(
                f"pricing_result.status is {pricing_result.status.value!r}, not SUCCESS or "
                "SUCCESS_WITH_WARNINGS -- comparison requires a successful pricing result"
            ),
        )

    # --- 2. MODEL_PREMIUM_UNAVAILABLE ---------------------------------------
    if model_total_premium is None or model_fair_premium_per_100 is None:
        return _build(
            status=BLIBenchmarkComparisonStatus.NON_COMPARABLE,
            reason=BLIBenchmarkComparisonReason.MODEL_PREMIUM_UNAVAILABLE,
            alignment_note=(
                "model premium unavailable: pricing_result.pv="
                f"{pricing_result.pv!r} (usable={model_total_premium is not None}), "
                "pricing_result.assumptions['black76_pv_per_100']="
                f"{pricing_result.assumptions.get('black76_pv_per_100')!r} "
                f"(usable={model_fair_premium_per_100 is not None})"
            ),
        )

    # --- 3. PRODUCT_ID_MISMATCH ----------------------------------------------
    result_product_id = pricing_result.product_id
    request_product_id = request.bond_option.product_id
    benchmark_product_id = benchmark.product_id
    if not (result_product_id == request_product_id == benchmark_product_id):
        return _build(
            status=BLIBenchmarkComparisonStatus.NON_COMPARABLE,
            reason=BLIBenchmarkComparisonReason.PRODUCT_ID_MISMATCH,
            alignment_note=(
                "product_id mismatch: pricing_result.product_id="
                f"{result_product_id!r}, request.bond_option.product_id="
                f"{request_product_id!r}, benchmark.product_id={benchmark_product_id!r}"
            ),
        )

    # --- 4. SNAPSHOT_ID_MISMATCH ----------------------------------------------
    request_snapshot_id = request.market_data_snapshot.snapshot_id
    if request_snapshot_id != benchmark.snapshot_id:
        return _build(
            status=BLIBenchmarkComparisonStatus.NON_COMPARABLE,
            reason=BLIBenchmarkComparisonReason.SNAPSHOT_ID_MISMATCH,
            alignment_note=(
                "snapshot_id mismatch: request.market_data_snapshot.snapshot_id="
                f"{request_snapshot_id!r}, benchmark.snapshot_id={benchmark.snapshot_id!r}"
            ),
        )

    # --- 5. UNDERLYING_ID_MISMATCH --------------------------------------------
    request_underlying_isin = request.bond_option.underlying_isin
    if request_underlying_isin != benchmark.underlying_id:
        return _build(
            status=BLIBenchmarkComparisonStatus.NON_COMPARABLE,
            reason=BLIBenchmarkComparisonReason.UNDERLYING_ID_MISMATCH,
            alignment_note=(
                "underlying_id mismatch: request.bond_option.underlying_isin="
                f"{request_underlying_isin!r}, benchmark.underlying_id="
                f"{benchmark.underlying_id!r}"
            ),
        )

    # --- 6. CURRENCY_MISMATCH -------------------------------------------------
    result_currency = pricing_result.result_currency
    request_currency = request.bond_option.currency.value
    benchmark_currency = benchmark.currency.value
    if not (result_currency == request_currency == benchmark_currency):
        return _build(
            status=BLIBenchmarkComparisonStatus.NON_COMPARABLE,
            reason=BLIBenchmarkComparisonReason.CURRENCY_MISMATCH,
            alignment_note=(
                "currency mismatch: pricing_result.result_currency="
                f"{result_currency!r}, request.bond_option.currency="
                f"{request_currency!r}, benchmark.currency={benchmark_currency!r}"
            ),
        )

    # --- 7. QUOTE_SIDE_MISMATCH -----------------------------------------------
    if resolved_active_quote_side is not benchmark.quote_side:
        return _build(
            status=BLIBenchmarkComparisonStatus.NON_COMPARABLE,
            reason=BLIBenchmarkComparisonReason.QUOTE_SIDE_MISMATCH,
            alignment_note=(
                f"active_quote_side ({resolved_active_quote_side.value}) does not match "
                f"benchmark.quote_side ({benchmark.quote_side.value})"
            ),
        )

    # --- 8. SOURCE_DATE_MISMATCH ----------------------------------------------
    valuation_calendar_date: date = _parse_iso_date(
        request.valuation_date, "request.valuation_date"
    )
    snapshot_as_of_calendar_date: date = _parse_as_of_calendar_date(
        request.market_data_snapshot.as_of_timestamp,
        "request.market_data_snapshot.as_of_timestamp",
    )
    benchmark_source_as_of_calendar_date: date = _parse_as_of_calendar_date(
        benchmark.source_as_of, "benchmark.source_as_of"
    )
    if not (
        valuation_calendar_date
        == snapshot_as_of_calendar_date
        == benchmark_source_as_of_calendar_date
    ):
        return _build(
            status=BLIBenchmarkComparisonStatus.NON_COMPARABLE,
            reason=BLIBenchmarkComparisonReason.SOURCE_DATE_MISMATCH,
            alignment_note=(
                "calendar-date mismatch: request.valuation_date date="
                f"{valuation_calendar_date.isoformat()}, "
                "request.market_data_snapshot.as_of_timestamp date="
                f"{snapshot_as_of_calendar_date.isoformat()}, benchmark.source_as_of date="
                f"{benchmark_source_as_of_calendar_date.isoformat()}"
            ),
        )

    # --- 9. NEAR_ZERO_BENCHMARK -----------------------------------------------
    if abs(benchmark.premium_per_100) <= near_zero_threshold_per_100:
        signed_residual_per_100 = model_fair_premium_per_100 - benchmark.premium_per_100
        return _build(
            status=BLIBenchmarkComparisonStatus.NON_COMPARABLE,
            reason=BLIBenchmarkComparisonReason.NEAR_ZERO_BENCHMARK,
            signed_residual_per_100=signed_residual_per_100,
            absolute_residual_per_100=abs(signed_residual_per_100),
            relative_residual=None,
            alignment_note=(
                f"benchmark.premium_per_100 ({benchmark.premium_per_100!r}) is within "
                f"near_zero_threshold_per_100 ({near_zero_threshold_per_100!r}) of zero -- "
                "relative residual is not computable"
            ),
        )

    # --- Comparable: compute residuals and PASS / WARNING / FAIL -------------
    signed_residual_per_100 = model_fair_premium_per_100 - benchmark.premium_per_100
    absolute_residual_per_100 = abs(signed_residual_per_100)
    relative_residual = absolute_residual_per_100 / abs(benchmark.premium_per_100)

    if relative_residual < pass_threshold:
        status = BLIBenchmarkComparisonStatus.PASS
    elif relative_residual <= fail_threshold:
        status = BLIBenchmarkComparisonStatus.WARNING
    else:
        status = BLIBenchmarkComparisonStatus.FAIL

    return _build(
        status=status,
        reason=BLIBenchmarkComparisonReason.COMPARABLE,
        signed_residual_per_100=signed_residual_per_100,
        absolute_residual_per_100=absolute_residual_per_100,
        relative_residual=relative_residual,
        alignment_note=(
            "product_id, snapshot_id, underlying_id, currency, active_quote_side, and "
            "calendar dates (valuation_date, market_data_snapshot.as_of_timestamp, "
            f"benchmark.source_as_of) are aligned; relative_residual={relative_residual!r} "
            f"vs pass_threshold={pass_threshold!r}, fail_threshold={fail_threshold!r}"
        ),
    )
