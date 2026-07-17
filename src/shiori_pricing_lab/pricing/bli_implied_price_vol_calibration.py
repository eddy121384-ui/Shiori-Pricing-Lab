"""``calibrate_bli_implied_price_vol``: standalone calibration wiring (Issue #99 PR B).

**Issue #94 methodology-alignment block (comment 5003670704).** The
standalone fair-premium path is now OVME-aligned (dirty forward/strike,
fractional ACT/ACT option time, Option-Discount-Curve reporting-date
discount -- see ``price_bli_mvp_standalone_option``), but the
implied-``PRICE_VOL`` solver's public contract is still clean-price/ACT-365F.
This module therefore no longer runs the legacy clean-price/ACT-365
composition: after its methodology-neutral coherence gates (request
support, identity, source-date) it returns an explicit
``METHODOLOGY_NOT_ALIGNED`` outcome **before** any forward/strike/T/DF
resolution or solver call, so no plausible-but-inconsistent implied vol is
ever emitted. Safely aligning it would require either pushing dirty prices
through the solver's ``forward_clean_price`` / ``strike_clean_price``
arguments (whose contracts label them clean) or a broad public solver/result
migration -- both out of bounds for this slice; a dedicated dirty-price
solver contract is a separate future slice. The historical description of
the F/T/DF composition below is retained for context but is not currently
reached.

Scope, per the methodology approved in Issue #99 comments 4966208056,
4966209368, 4966210598, 4966211726, and the implementation authorization in
comment 4966213294: one isolated function that wires a
``BLIStandaloneBondOptionRequest`` and a ``BLIBenchmarkQuote`` (Issue #98 PR
A / #109) to the bounded implied-``PRICE_VOL`` solver (Issue #99 PR A /
#111), reusing only already-reviewed production pieces:

- ``check_bli_mvp_standalone_option_required_inputs``
  (``pricing/bli_mvp_required_input_guard.py``, #41/#95) -- the exact
  request-support checklist the standalone pricing engine itself uses;
- ``forward_clean_price_per_100`` (``pricing/bli_forward_clean_price.py``,
  #42), ``year_fraction_to_expiry`` (``pricing/bli_valuation_time.py``,
  docs/26), and ``discount_factor_from_continuous_zero_curve``
  (``pricing/bli_curve_discount_factor.py``, docs/28) -- the exact F/T/DF
  composition the standalone engine uses (``pricing/bli_pricing_engine.py``
  ``_price_bli_mvp_from_fields``), in the same order, called exactly once
  each;
- ``solve_implied_price_vol`` (``pricing/bli_implied_price_vol_solver.py``,
  #99 PR A) -- called exactly once, with ``benchmark.premium_per_100`` as
  its per-100-only target.

**This module creates no ``PricingResult`` and calls no pricing engine.**
Calibration occurs *before* a repricing ``PricingResult`` exists for the
calibrated volatility (Issue #99 methodology comment 4965749214 §8) --
``price_bli_mvp`` / ``price_bli_mvp_standalone_option`` are never invoked
here. ``ENGINE_NAME`` / ``ENGINE_VERSION`` are imported from
``pricing/bli_pricing_engine.py`` only as the two existing provenance
string constants stamped on the result -- no new model name or version is
introduced.

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
total-premium argument at all (mirrors #99 PR A's own boundary).

**Fixed gate priority (methodology comment 4966209368).** Evaluated in
exactly this order, short-circuiting on the first applicable outcome:
request support, product id, snapshot id, underlying id, currency, quote
side, source date, input resolution, solver outcome. See
:func:`calibrate_bli_implied_price_vol` for the full mapping.

**Contract violations vs. structured outcomes.** A non-``BLIStandaloneBondOptionRequest``
or non-``BLIBenchmarkQuote`` argument raises ``TypeError``; a malformed
``active_quote_side`` (a foreign ``Enum``/``StrEnum`` instance, or a string
that does not coerce to ``BLIBenchmarkQuoteSide``) raises ``ValueError`` --
both immediately, before any gate is evaluated. A ``ValueError`` raised by
one of the three F/T/DF resolution helpers is caught individually and
mapped to a structured ``FAILED / INPUT_RESOLUTION_FAILED`` result
(exception type/message and every already-resolved intermediate preserved,
later ones left ``None``); a ``ValueError`` raised by
``solve_implied_price_vol`` itself (a malformed solver-configuration
argument) is never caught here and propagates unchanged, and neither is a
``RuntimeError`` from any composed helper (e.g. QuantLib unavailable) --
both are environment/contract problems, not domain outcomes this module
maps.

**Hard non-goals (Issue #99 PR B scope cap):** no existing-file change, no
pricing-engine wiring, no request/snapshot/benchmark/solver/comparison
schema change, no yield-vol conversion, no volatility surface/
interpolation/SABR/American-exercise/multi-quote calibration, no provider/
Bloomberg adapter, no UI/workbench, no persistence/versioning/replay, no
client-margin calibration, and no golden-case work. This module imports
from ``data._validation``, ``data.bli_benchmark_quote``, ``data.bli_snapshot``,
``data.bli_standalone_option_request``, ``pricing.bli_curve_discount_factor``,
``pricing.bli_forward_clean_price``, ``pricing.bli_implied_price_vol_solver``,
``pricing.bli_mvp_required_input_guard``, ``pricing.bli_pricing_engine``
(constants only), ``pricing.bli_valuation_time``, and ``products.enums``
only, and never mutates ``request`` or ``benchmark``.

**Golden-case boundary.** Every fixture in this module's test suite is
explicitly synthetic. None represents Bloomberg output, a golden case,
market calibration evidence, or UAT.
"""

from __future__ import annotations

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
    BLIImpliedPriceVolSolverResult,
)
from shiori_pricing_lab.pricing.bli_mvp_required_input_guard import (
    check_bli_mvp_standalone_option_required_inputs,
)
from shiori_pricing_lab.pricing.bli_pricing_engine import ENGINE_NAME, ENGINE_VERSION
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
    from ``BLIImpliedPriceVolSolverReason`` (#99 PR A) -- the solver's own
    reason is never duplicated here; it is preserved unchanged inside
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
    # Issue #94: the standalone fair-premium path is now OVME-aligned (dirty
    # forward/strike, ACT/ACT option time, Option-Discount-Curve reporting-date
    # discount), but the implied-PRICE_VOL solver's public contract is still
    # clean-price/ACT-365. Rather than silently keep calibrating on the stale
    # composition (or fake alignment by pushing dirty prices through the
    # solver's clean-labelled arguments), calibration returns this explicit
    # outcome before any legacy F/T/DF resolution -- no plausible-but-
    # inconsistent implied vol is ever emitted. A dedicated dirty-price solver
    # contract is a separate future slice.
    METHODOLOGY_NOT_ALIGNED = "METHODOLOGY_NOT_ALIGNED"


def _reject_foreign_enum_active_quote_side(value: object) -> None:
    """Reject any ``Enum`` instance that is not already a ``BLIBenchmarkQuoteSide``.

    Duplicated locally rather than imported -- mirrors the identical guard
    already reviewed in ``data/bli_benchmark_quote.py`` (Issue #98 PR A)
    and ``pricing/bli_benchmark_comparison.py`` (Issue #98 PR B): both
    exist because ``products.enums.coerce_enum`` coerces by *value*, so a
    foreign ``StrEnum`` member sharing a string value (e.g.
    ``TreasuryFTPQuoteSide.BID == "BID"``) would otherwise pass through it
    silently. This module does not call or import from
    ``pricing/bli_benchmark_comparison.py`` (Issue #99 methodology comment
    4966208056: no comparison-function dependency).
    """

    if isinstance(value, Enum) and not isinstance(value, BLIBenchmarkQuoteSide):
        raise ValueError(
            "active_quote_side must be a BLIBenchmarkQuoteSide member or one of "
            f"{{BID, MID, OFFER}}, got foreign enum {value!r}"
        )


@dataclass(frozen=True)
class BLIImpliedPriceVolCalibrationResult:
    """One deterministic standalone implied-``PRICE_VOL`` calibration outcome.

    Every field is a verbatim read from ``request``/``benchmark`` or an
    already-reviewed helper's return value -- no derivation, no unit
    conversion, no mutation of either input. ``forward_clean_price``,
    ``strike_clean_price``, ``time_to_expiry``, and
    ``option_discount_factor`` stay ``None`` until (and unless) actually
    resolved; ``solver_result`` stays ``None`` until the solver is actually
    called. ``resolution_error_type``/``resolution_error_message`` are
    populated only for ``INPUT_RESOLUTION_FAILED``.
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
    forward_clean_price: float | None
    strike_clean_price: float | None
    time_to_expiry: float | None
    option_discount_factor: float | None
    solver_result: BLIImpliedPriceVolSolverResult | None
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
    """Calibrate the implied ``PRICE_VOL`` matching ``benchmark`` for ``request``.

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
        forward_clean_price: float | None = None,
        strike_clean_price: float | None = None,
        time_to_expiry: float | None = None,
        option_discount_factor: float | None = None,
        solver_result: BLIImpliedPriceVolSolverResult | None = None,
        resolution_error_type: str | None = None,
        resolution_error_message: str | None = None,
    ) -> BLIImpliedPriceVolCalibrationResult:
        return BLIImpliedPriceVolCalibrationResult(
            status=status,
            reason=reason,
            active_quote_side=resolved_active_quote_side,
            pricing_engine_name=ENGINE_NAME,
            pricing_engine_version=ENGINE_VERSION,
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
            forward_clean_price=forward_clean_price,
            strike_clean_price=strike_clean_price,
            time_to_expiry=time_to_expiry,
            option_discount_factor=option_discount_factor,
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

    # --- 8. Methodology-alignment gate (Issue #94) ------------------------------
    # The identity/date/support gates above are methodology-neutral coherence
    # checks and still run. But the actual calibration composition -- the
    # legacy clean-price forward from spot/Bond Reference Curve, ACT/365F
    # option time, clean-F/K Black, and expiry discount factor -- is now
    # inconsistent with the OVME-aligned standalone fair-premium path
    # (price_bli_mvp_standalone_option: dirty forward/strike, fractional
    # ACT/ACT time, Option-Discount-Curve reporting-date discount).
    #
    # Per Issue #94 comment 5003670704, this must not silently continue on the
    # stale composition, and must not emit a plausible-but-inconsistent
    # implied vol. Safely aligning it would require passing dirty prices
    # through solve_implied_price_vol's public forward_clean_price /
    # strike_clean_price arguments (whose contracts label them clean) or a
    # broad public solver/result migration -- both out of bounds for this
    # slice. So calibration returns an explicit METHODOLOGY_NOT_ALIGNED
    # outcome here, before any legacy F/T/DF resolution or solver call. No
    # forward/strike/T/DF is resolved and no solver runs, so solver_result and
    # all resolved-input fields stay None and no implied vol is produced.
    return _build(
        status=BLIImpliedPriceVolCalibrationStatus.FAILED,
        reason=BLIImpliedPriceVolCalibrationReason.METHODOLOGY_NOT_ALIGNED,
        diagnostic_note=(
            "implied PRICE_VOL calibration is blocked: the standalone fair-premium path "
            "is now OVME-aligned (dirty forward/strike, fractional ACT/ACT option time, "
            "Option-Discount-Curve reporting-date discount), but the implied-vol solver's "
            "public contract is still clean-price/ACT-365F. No implied vol is emitted -- "
            "a dedicated dirty-price solver contract is required before calibration can "
            "resume. Request/benchmark identity and source-date coherence were checked "
            "first and passed; only the pricing-methodology alignment blocks this call."
        ),
    )
