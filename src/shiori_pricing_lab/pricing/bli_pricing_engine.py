"""BLI pricing engine: European price-based cash-settled Black-76 (Issue #44, MVP-8).

Scope: wire the already-reviewed MVP pieces behind the existing
`price_bli_mvp(bundle) -> PricingResult` entrypoint, in this exact order:

1. #41 `check_bli_mvp_required_inputs(bundle)` -- reject an unsupported
   product shape or missing input before attempting any pricing math.
2. #42 `forward_clean_price_per_100(...)` -- forward clean price F, per 100
   (Bond Reference Curve, per Annex A §A.5.3).
3. `year_fraction_to_expiry(bundle.valuation_date, expiry_date)` -- T,
   ACT/365F.
4. `discount_factor_from_continuous_zero_curve(..., curve_purpose=
   BLICurvePurpose.OPTION_DISCOUNT_CURVE, target_year_fraction=T)` -- DF
   for the option leg. This is the **only** place in this whole pricing
   path that selects the Option Discount Curve -- #42's own module
   selects only the Bond Reference Curve, and never the reverse (Annex A
   §A.2.2/§A.5.3: the two curves must never be mixed).
5. `bundle.market_data_snapshot.volatility_input.volatility` -- sigma,
   read directly, no conversion. #41's guard already restricts
   `volatility_basis` to `PRICE_VOL`/`EQUIVALENT_PRICE_VOL`, both
   directly usable as sigma per docs/26 §2.1 -- this module performs no
   yield-vol or equivalent-vol conversion of any kind.
6. #83 `black76_price_option_pv_per_100(...)` -- PV per 100.
7. `pv = pv_per_100 * bond_option.notional / 100.0` -- the trivial
   notional-scaling step both #42 and #83 explicitly deferred to this
   slice.

**This module adds no new pricing math of its own.** Every numeric
computation is delegated to an already-reviewed helper from #41/#42/#83;
this module's only job is composition, in the fixed order above, and
translating outcomes into a `PricingResult`.

**Result mapping:**

- Wrong ``bundle`` type: raises ``TypeError`` -- a contract violation,
  not a domain outcome (unchanged from the prior skeleton).
- Guard rejects the bundle (``supported=False``): returns
  ``PricingResult(status=FAILED, ...)`` with the guard's full
  ``reasons`` tuple preserved verbatim in the message ``detail``. The
  error code is picked by ``_classify_guard_rejection_from_fields``, a
  small local classifier reading **typed fields directly** (never
  string-sniffing the guard's plain-text ``reasons``): an unsupported
  product shape (exercise style, payoff basis, settlement type, or an
  unsupported volatility basis such as ``YIELD_VOL`` -- a basis with no
  conversion available, not merely absent data) maps to
  ``PricingErrorCode.UNSUPPORTED_PRODUCT``; a bundle whose shape is
  otherwise supported but whose ``bond_quote.clean_price_per_100`` is
  absent maps to ``PricingErrorCode.MISSING_MARKET_DATA``. When both
  kinds of problem are present at once, ``UNSUPPORTED_PRODUCT`` wins --
  a caller should not be told "just supply the missing price" when the
  product shape itself is also unsupported. No reason-code taxonomy is
  added to ``RequiredInputGuardResult``, and ``pricing/result.py`` is
  not modified.
- A downstream helper raises ``ValueError`` (covers
  ``BLIBondScheduleError``, ``BLIBondExDividendWindowError``,
  ``BLIBondMaturityCashflowUnsupportedError`` -- all ``ValueError``
  subclasses -- plus the curve-selection/interpolation chain's and
  Black-76's own ``ValueError``s): caught and returned as
  ``PricingResult(status=FAILED, errors=[PricingErrorCode.
  ENGINE_ERROR])``, with the exception's type name and message preserved
  in ``detail``. This is a genuine domain outcome for a bundle that
  already passed the shape checklist but whose underlying data still
  cannot produce a number (e.g. a curve whose range does not bracket the
  target date) -- not a crash.
- ``BLIQuantLibNotAvailableError`` (a ``RuntimeError``, not a
  ``ValueError``) is **not** caught -- it propagates. It represents an
  environment/deployment precondition ("QuantLib is not installed here"),
  not a per-bundle domain outcome; catching it into a ``FAILED`` result
  would let a batch job silently mistake a deployment gap for a bad
  trade.
- Every other outcome: ``PricingResult(status=SUCCESS, pv=..., ...)``.

**Position/sign convention:** ``pv`` is the absolute option premium after
notional scaling (``pv_per_100 * notional / 100``) -- ``bond_option.
position`` (``BUY``/``SELL``) is never read here and never flips the sign
of ``pv``. Annex A §A.2's PV formula carries no position-dependent sign
term; BUY/SELL P&L sign convention belongs to a future reporting/
settlement layer, not this engine. ``assumptions["position_sign_applied"]``
records this explicitly as ``False`` on every success result.

**Priced component is the bond option leg only** (Codex P2 review of PR
#89): ``PricingResult.product_type`` still identifies the wrapper
``BondLinkedStructuredProduct`` (unchanged, since that is what the
bundle actually holds), but this engine only values
``product.bond_option`` -- never the deposit leg, never a combined
structured-product payoff. ``assumptions["priced_component"]``,
``["priced_component_scope"]``, and ``["excluded_components"]`` make
this explicit and machine-readable on every success result, so a caller
cannot mistake the returned ``pv`` for a whole-structured-product value.

**Standalone bond-option OVME-aligned path (Issue #94):**
``price_bli_mvp_standalone_option(request)`` is now a **separate numeric
composition** from the bundle path, implementing the Eddy-approved
Bloomberg (OVME) methodology for the standalone European price-based
cash-settled option (comments 5001749998 / 5003670704). It accepts a
``BLIStandaloneBondOptionRequest`` and, unlike the legacy bundle path,
does **not** share ``_price_bli_mvp_from_fields`` and does **not**
construct a forward from a spot price and the Bond Reference Curve:
instead it reads an explicit ``forward_clean_price_input`` and composes
dirty forward / dirty strike (clean + accrued interest at the bond forward
settlement date), fractional-timestamp ACT/ACT option time, and an Option
Discount Curve discount factor to option settlement divided by the factor
to the reporting date -- all via the focused
``resolve_standalone_option_pricing_inputs`` resolver and the dirty-price
Black wrapper ``black76_dirty_price_option_pv_per_100``. It uses its own
``STANDALONE_ENGINE_NAME`` / ``STANDALONE_ENGINE_VERSION`` provenance and
its own ``product_id`` / ``product_type`` (the bare ``BondOption``'s
``"BOND_OPTION"`` discriminator). The legacy bundle path
(:func:`price_bli_mvp`) is numerically and contractually unchanged by this
slice.

**Standalone European Greeks (Issue #133, Slice A):** the same success
path additionally reports Forward Price Delta, Forward Price Gamma, Vega
per volatility point, and Black-76 Theta per calendar day, computed by
``black76_dirty_price_option_greeks_per_100`` from **exactly** the five
inputs the premium above already used -- the resolver is not run twice, no
curve is re-read, and nothing is bumped and revalued.

Each Greek appears in ``assumptions`` on **two deliberately distinct
bases**, named so they cannot be confused:

- ``*_per_100`` -- **instrument analytics**. Carries the CALL/PUT
  direction only; BUY and SELL produce identical values
  (``greeks_per_100_position_sign_applied = False``).
- ``position_*_total`` -- **trader position risk**. Adds notional *and*
  the BUY/SELL sign (``BUY = +1``, ``SELL = -1``), so an otherwise
  identical SELL total is exactly the negative of the BUY total
  (``greeks_position_total_sign_applied = True``). The ``position_``
  prefix is required: a bare ``*_total`` name would conceal whether the
  position sign is baked in.

``pv`` is unaffected by this contract -- the model fair premium remains an
unsigned fair-value magnitude and ``position_sign_applied`` stays
``False``, referring to ``pv`` alone. A ``FAILED`` result carries no
``assumptions`` at all and therefore exposes no Greek. ``price_bli_mvp``
(the bundle path) reports no Greeks and is unchanged.

**Unchanged from the prior skeleton:** ``price_bli_mvp`` still accepts
only a ``BLIMVPInputBundle`` (never calls ``resolve_bond_reference_data``
or ``build_bli_mvp_input_bundle``), never mutates ``bundle``, is not
registered on ``PricingEngineRegistry`` and does not implement the
``PricingEngine`` Protocol (``pricing/engine.py`` is unmodified), and
reuses ``PricingResult``/``PricingStatus``/``PricingErrorCode`` exactly
as-is -- no ``BLIPricingResult``/``BLIPricingStatus`` is introduced.

**Out of scope (unchanged from #41/#42/#83, restated here as the
acceptance boundary for this wiring slice):** yield-based options,
yield-vol conversion, American exercise, physical delivery, principal/
redemption delivery logic, Greeks (``dv01`` stays ``None``), a
self-validation framework, Bloomberg/FTP/warehouse/audit DB, UI/
reporting, and any portfolio/scenario engine.
"""

from __future__ import annotations

from shiori_pricing_lab.data.bli_mvp_input_bundle import BLIMVPInputBundle
from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePurpose,
    BLIMarketDataSnapshot,
    BLIVolatilityBasis,
)
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.pricing.bli_black76_price_option import (
    CALENDAR_DAYS_PER_YEAR,
    VOLATILITY_POINT,
    black76_dirty_price_option_greeks_per_100,
    black76_dirty_price_option_pv_per_100,
    black76_price_option_pv_per_100,
)
from shiori_pricing_lab.pricing.bli_curve_discount_factor import (
    discount_factor_from_continuous_zero_curve,
)
from shiori_pricing_lab.pricing.bli_forward_clean_price import (
    forward_clean_price_per_100,
)
from shiori_pricing_lab.pricing.bli_mvp_required_input_guard import (
    RequiredInputGuardResult,
    check_bli_mvp_required_inputs,
    check_bli_mvp_standalone_option_required_inputs,
)
from shiori_pricing_lab.pricing.bli_standalone_option_pricing_inputs import (
    resolve_standalone_option_pricing_inputs,
)
from shiori_pricing_lab.pricing.bli_valuation_time import year_fraction_to_expiry
from shiori_pricing_lab.pricing.result import (
    PricingErrorCode,
    PricingMessage,
    PricingResult,
    PricingStatus,
)
from shiori_pricing_lab.products.bond_option import BondOption
from shiori_pricing_lab.products.enums import (
    ExerciseStyle,
    PayoffBasis,
    Position,
    SettlementType,
)
from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData

ENGINE_NAME = "bli_mvp_black76_forward_clean_price_engine"
ENGINE_VERSION = "1.0.0"

# Separate provenance for the OVME-aligned standalone engine (Issue #94), so
# the legacy bundle engine name/version and its pinned results never change.
STANDALONE_ENGINE_NAME = "bli_standalone_bond_option_ovme_black76_engine"
STANDALONE_ENGINE_VERSION = "1.0.0"
_METHOD_STANDALONE_OVME_BLACK76 = "black76_forward_dirty_price_ovme_v1"

# Method identifiers distinguish "never attempted real pricing" (the
# guard rejected the bundle) from "pricing was attempted" (success or a
# downstream domain failure) -- both a caller and the assumptions/
# diagnostics metadata can branch on this.
_METHOD_NOT_SUPPORTED = "not_supported"
_METHOD_BLACK76_FORWARD_CLEAN_PRICE = "black76_forward_clean_price_v1"

# Path-specific value for assumptions["bond_reference_curve_purpose"]. This
# is the ONE intentional success-result difference between the two
# entrypoints (Sophira Red-zone review of PR #105): the bundle path's exact
# legacy string is preserved verbatim as an observable-result contract
# (even though the shared composition now calls forward_clean_price_per_100
# directly rather than the _for_bundle wrapper), while the standalone path
# names the primitive it actually reaches. Everything else in the
# assumptions mapping -- and every numeric output -- is identical across
# both paths. Passed explicitly into the shared composition; no pricing
# math is duplicated to vary this label.
_BUNDLE_BOND_REFERENCE_CURVE_PURPOSE_NOTE = (
    "BOND_REFERENCE_CURVE (used only for forward clean price, via "
    "forward_clean_price_per_100_for_bundle)"
)

# Volatility bases this engine can use as sigma without any conversion --
# a local mirror of #41's own (private) allowlist, kept here rather than
# imported so RequiredInputGuardResult's module stays untouched (Codex
# P2 review of PR #89: this classifier reads typed bundle fields, not
# guard reason text).
_SUPPORTED_VOLATILITY_BASES = (
    BLIVolatilityBasis.PRICE_VOL,
    BLIVolatilityBasis.EQUIVALENT_PRICE_VOL,
)

# Issue #133 Slice A: the BUY/SELL multiplier applied to the *position*
# Greek totals only -- never to ``pv`` (the model fair premium stays an
# unsigned fair-value magnitude) and never to the per-100 analytic Greeks.
_POSITION_MULTIPLIERS = {Position.BUY: 1.0, Position.SELL: -1.0}


def _classify_guard_rejection_from_fields(
    bond_option: BondOption,
    market_data_snapshot: BLIMarketDataSnapshot,
    guard_result: RequiredInputGuardResult,
) -> PricingErrorCode:
    """Return the ``PricingErrorCode`` for a guard-rejected set of fields.

    Container-agnostic (Issue #95): reads already-unpacked typed fields
    directly -- never the guard's plain-text ``reasons`` strings (Codex P2
    review of PR #89: string-sniffing the guard's human-readable reasons
    would be fragile, since a wording change in #41's module could silently
    break classification here). Shared by both the bundle and standalone
    engine entrypoints, so their error-code classification is identical.

    An unsupported product shape (exercise style, payoff basis,
    settlement type, or a volatility basis with no conversion available)
    always wins over a merely-missing market-data value: a caller should
    not be told "just supply the missing price" when the product shape
    itself is also unsupported.
    """

    volatility_basis = market_data_snapshot.volatility_input.volatility_basis

    shape_supported = (
        bond_option.exercise_style is ExerciseStyle.EUROPEAN
        and bond_option.payoff_basis is PayoffBasis.PRICE
        and bond_option.settlement_type is SettlementType.CASH
        and volatility_basis in _SUPPORTED_VOLATILITY_BASES
    )
    if not shape_supported:
        return PricingErrorCode.UNSUPPORTED_PRODUCT

    if market_data_snapshot.bond_quote.clean_price_per_100 is None:
        return PricingErrorCode.MISSING_MARKET_DATA

    # Every other guard-rejection reason still in scope for this
    # classifier (missing bond reference data, missing required curve
    # purposes, a blank valuation date/expiry/strike/notional) is
    # defensive/unreachable for a bundle or request that constructed
    # successfully at all (see #41's own module docstring) -- default to
    # UNSUPPORTED_PRODUCT, matching the prior, pre-Codex-review behavior
    # for these unreachable branches.
    return PricingErrorCode.UNSUPPORTED_PRODUCT


def _price_bli_mvp_from_fields(
    *,
    bond_option: BondOption,
    valuation_date: str,
    resolved_bond_reference_data: BondReferenceData,
    market_data_snapshot: BLIMarketDataSnapshot,
    guard_result: RequiredInputGuardResult,
    common_fields: dict,
    error_detail_base: dict,
    subject_label: str,
    bond_reference_curve_purpose_note: str,
) -> PricingResult:
    """Shared, container-agnostic pricing composition (Issue #95).

    The single reviewed guard-classification + forward-clean-price + curve
    + Black-76 + result composition, run against already-unpacked fields so
    both the ``BLIMVPInputBundle`` path (:func:`price_bli_mvp`) and the
    standalone request path (:func:`price_bli_mvp_standalone_option`) reuse
    *exactly* the same math and result-mapping, with no duplicated formula.

    ``guard_result`` is computed by the caller (via the appropriate public
    guard wrapper) and passed in. ``common_fields`` carries the
    already-assembled identity/provenance fields (product_id/product_type
    intentionally differ between the two paths -- ``BOND_OPTION`` vs the
    wrapper's ``BOND_LINKED_STRUCTURED_PRODUCT``). ``error_detail_base`` is
    merged into each failure message's ``detail`` (the bundle path adds
    ``bundle_id``; the standalone path carries only ``product_id``), and
    ``subject_label`` is the noun used in the guard-rejection message
    ("bundle" / "request"). ``bond_reference_curve_purpose_note`` is the
    one intentional success-``assumptions`` difference between the two
    paths (Sophira Red-zone review of PR #105): the bundle path passes its
    exact preserved legacy string, the standalone path names the primitive
    it actually reaches. Everything else -- every numeric output and every
    other assumption key -- is identical across both callers.
    """

    if not guard_result.supported:
        error_code = _classify_guard_rejection_from_fields(
            bond_option, market_data_snapshot, guard_result
        )
        return PricingResult(
            **common_fields,
            status=PricingStatus.FAILED,
            method=_METHOD_NOT_SUPPORTED,
            errors=(
                PricingMessage(
                    code=error_code,
                    message=(
                        f"BLI required-input guard rejected this {subject_label}: "
                        f"{'; '.join(guard_result.reasons)}"
                    ),
                    detail={**error_detail_base, "reasons": list(guard_result.reasons)},
                ),
            ),
        )

    try:
        # clean_price_per_100 is guaranteed non-None here: the guard returns
        # MISSING_MARKET_DATA (above) before pricing is ever reached when it
        # is absent. Reading it directly and calling the shared
        # forward_clean_price_per_100 primitive (rather than the bundle-only
        # forward_clean_price_per_100_for_bundle wrapper) is what lets the
        # standalone path reuse the identical formula (Issue #95).
        spot_clean_price = market_data_snapshot.bond_quote.clean_price_per_100
        forward_clean_price = forward_clean_price_per_100(
            bond=resolved_bond_reference_data,
            spot_clean_price=spot_clean_price,
            valuation_date=valuation_date,
            expiry_date=bond_option.expiry_date,
            curve_points=market_data_snapshot.curve_points,
        )
        time_to_expiry = year_fraction_to_expiry(valuation_date, bond_option.expiry_date)
        option_discount_factor = discount_factor_from_continuous_zero_curve(
            market_data_snapshot.curve_points,
            currency=bond_option.currency,
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            target_year_fraction=time_to_expiry,
            # Issue #165 live-wiring follow-up: the OPTION_DISCOUNT_CURVE's own
            # node coordinates resolve from an explicit Bloomberg maturity_date
            # (via year_fraction_to_expiry) when present, using the same
            # valuation_date this call's own time_to_expiry is already anchored
            # to -- never a separate/invented reference date. A purely
            # tenor-only curve (every curve priced today) never reads this.
            as_of_date=valuation_date,
        )
        price_volatility = market_data_snapshot.volatility_input.volatility
        pv_per_100 = black76_price_option_pv_per_100(
            forward_clean_price=forward_clean_price,
            strike_clean_price=bond_option.strike_price,
            price_volatility=price_volatility,
            time_to_expiry=time_to_expiry,
            discount_factor=option_discount_factor,
            option_type=bond_option.option_type,
        )
    except ValueError as exc:
        return PricingResult(
            **common_fields,
            status=PricingStatus.FAILED,
            method=_METHOD_BLACK76_FORWARD_CLEAN_PRICE,
            errors=(
                PricingMessage(
                    code=PricingErrorCode.ENGINE_ERROR,
                    message=str(exc),
                    detail={**error_detail_base, "exception_type": type(exc).__name__},
                ),
            ),
        )

    pv = pv_per_100 * bond_option.notional / 100.0

    return PricingResult(
        **common_fields,
        status=PricingStatus.SUCCESS,
        method=_METHOD_BLACK76_FORWARD_CLEAN_PRICE,
        pv=pv,
        assumptions={
            "forward_clean_price_per_100": forward_clean_price,
            "strike_clean_price_per_100": bond_option.strike_price,
            "time_to_expiry_year_fraction": time_to_expiry,
            "price_volatility": price_volatility,
            "volatility_basis": market_data_snapshot.volatility_input.volatility_basis.value,
            "option_discount_factor": option_discount_factor,
            "black76_pv_per_100": pv_per_100,
            "notional": bond_option.notional,
            "pv_scaling_formula": "pv = black76_pv_per_100 * notional / 100",
            "bond_reference_curve_purpose": bond_reference_curve_purpose_note,
            "option_discount_curve_purpose": "OPTION_DISCOUNT_CURVE (used only for the "
            "option PV discount factor)",
            "option_type": bond_option.option_type.value,
            "position_sign_applied": False,
            "priced_component": "bond_option_leg",
            "priced_component_scope": "option_leg_only_not_full_structured_product",
            "excluded_components": [
                "deposit_leg",
                "principal_redemption",
                "physical_delivery",
            ],
        },
    )


def price_bli_mvp(bundle: BLIMVPInputBundle) -> PricingResult:
    """Return a deterministic ``PricingResult`` for ``bundle``.

    Accepts only a ``BLIMVPInputBundle`` -- raises :class:`TypeError` for
    anything else. Never mutates ``bundle``. Never calls
    ``resolve_bond_reference_data`` or ``build_bli_mvp_input_bundle``.
    Thin public wrapper (unchanged signature and observable behavior) that
    unpacks ``bundle`` and delegates to the shared
    :func:`_price_bli_mvp_from_fields` composition. See the module
    docstring for the exact composition order and result-mapping rules.
    """

    if not isinstance(bundle, BLIMVPInputBundle):
        raise TypeError(f"bundle must be a BLIMVPInputBundle, got {type(bundle).__name__}")

    product = bundle.product
    bond_option = product.bond_option

    common_fields = dict(
        product_id=product.product_id,
        product_type=product.product_type,
        valuation_date=bundle.valuation_date,
        result_currency=bond_option.currency.value,
        engine_name=ENGINE_NAME,
        engine_version=ENGINE_VERSION,
        market_data_as_of=bundle.market_data_snapshot.as_of_timestamp,
    )

    guard_result = check_bli_mvp_required_inputs(bundle)

    return _price_bli_mvp_from_fields(
        bond_option=bond_option,
        valuation_date=bundle.valuation_date,
        resolved_bond_reference_data=bundle.resolved_bond_reference_data,
        market_data_snapshot=bundle.market_data_snapshot,
        guard_result=guard_result,
        common_fields=common_fields,
        error_detail_base={"bundle_id": bundle.bundle_id, "product_id": product.product_id},
        subject_label="bundle",
        bond_reference_curve_purpose_note=_BUNDLE_BOND_REFERENCE_CURVE_PURPOSE_NOTE,
    )


def _classify_standalone_guard_rejection_from_fields(
    bond_option: BondOption,
    market_data_snapshot: BLIMarketDataSnapshot,
) -> PricingErrorCode:
    """Return the ``PricingErrorCode`` for a guard-rejected standalone request.

    Reads typed fields directly (never the guard's plain-text reasons). An
    unsupported product shape (exercise style, payoff basis, settlement
    type, or a volatility basis with no conversion available) always wins
    over merely-missing market data; a supported shape whose explicit
    ``forward_clean_price_input`` is absent maps to
    ``MISSING_MARKET_DATA`` (the standalone path prices from that explicit
    forward, so its absence is missing market data, not an unsupported
    shape).
    """

    volatility_basis = market_data_snapshot.volatility_input.volatility_basis
    shape_supported = (
        bond_option.exercise_style is ExerciseStyle.EUROPEAN
        and bond_option.payoff_basis is PayoffBasis.PRICE
        and bond_option.settlement_type is SettlementType.CASH
        and volatility_basis in _SUPPORTED_VOLATILITY_BASES
    )
    if not shape_supported:
        return PricingErrorCode.UNSUPPORTED_PRODUCT
    if market_data_snapshot.forward_clean_price_input is None:
        return PricingErrorCode.MISSING_MARKET_DATA
    return PricingErrorCode.UNSUPPORTED_PRODUCT


def price_bli_mvp_standalone_option(
    request: BLIStandaloneBondOptionRequest,
) -> PricingResult:
    """Return a deterministic ``PricingResult`` for a standalone bond option (OVME-aligned).

    Standalone European price-based cash-settled path implementing the
    Eddy-approved Bloomberg (OVME) methodology (Issue #94, comments
    5001749998 / 5003670704). It is a **separate numeric composition** from
    the legacy bundle path :func:`price_bli_mvp` -- it does not share
    :func:`_price_bli_mvp_from_fields` and does not construct a forward from
    a spot price and the Bond Reference Curve. In order:

    1. run the OVME-aligned standalone guard
       (:func:`check_bli_mvp_standalone_option_required_inputs`);
    2. resolve the approved inputs exactly once through the focused
       :func:`resolve_standalone_option_pricing_inputs` (explicit forward
       clean price; accrued interest at the forward settlement date; dirty
       forward / dirty strike; ACT/ACT option time; Option Discount Curve DF
       to option settlement divided by DF to reporting date);
    3. read ``PRICE_VOL`` / ``EQUIVALENT_PRICE_VOL`` directly as sigma;
    4. call the dirty-price Black wrapper
       (:func:`black76_dirty_price_option_pv_per_100`);
    5. scale total PV = premium_per_100 * notional / 100.

    Uses separate ``STANDALONE_ENGINE_NAME`` / ``STANDALONE_ENGINE_VERSION``
    provenance so the legacy bundle constants and pinned results never
    change. Failure/error mapping mirrors the bundle path: a guard rejection
    is a ``FAILED`` result classified from typed fields; a downstream
    ``ValueError`` (curve out of range, invalid accrued-interest date, a
    non-positive resolved DF, ...) becomes ``FAILED / ENGINE_ERROR``; a
    ``BLIQuantLibNotAvailableError`` (``RuntimeError``) propagates unchanged,
    exactly as the bundle path lets it propagate. ``position`` (BUY/SELL) is
    never applied to the sign of ``pv``. Raises :class:`TypeError` for a
    non-``BLIStandaloneBondOptionRequest``; never mutates ``request``.
    """

    if not isinstance(request, BLIStandaloneBondOptionRequest):
        raise TypeError(
            f"request must be a BLIStandaloneBondOptionRequest, got {type(request).__name__}"
        )

    bond_option = request.bond_option
    snapshot = request.market_data_snapshot

    common_fields = dict(
        product_id=bond_option.product_id,
        product_type=bond_option.product_type,
        valuation_date=request.valuation_date,
        result_currency=bond_option.currency.value,
        engine_name=STANDALONE_ENGINE_NAME,
        engine_version=STANDALONE_ENGINE_VERSION,
        market_data_as_of=snapshot.as_of_timestamp,
    )
    error_detail_base = {"product_id": bond_option.product_id}

    guard_result = check_bli_mvp_standalone_option_required_inputs(request)
    if not guard_result.supported:
        error_code = _classify_standalone_guard_rejection_from_fields(bond_option, snapshot)
        return PricingResult(
            **common_fields,
            status=PricingStatus.FAILED,
            method=_METHOD_NOT_SUPPORTED,
            errors=(
                PricingMessage(
                    code=error_code,
                    message=(
                        "BLI standalone required-input guard rejected this request: "
                        f"{'; '.join(guard_result.reasons)}"
                    ),
                    detail={**error_detail_base, "reasons": list(guard_result.reasons)},
                ),
            ),
        )

    try:
        inputs = resolve_standalone_option_pricing_inputs(request)
        price_volatility = snapshot.volatility_input.volatility
        pv_per_100 = black76_dirty_price_option_pv_per_100(
            forward_dirty_price=inputs.forward_dirty_price_per_100,
            strike_dirty_price=inputs.strike_dirty_price_per_100,
            price_volatility=price_volatility,
            time_to_expiry=inputs.time_to_expiry_year_fraction,
            discount_factor=inputs.effective_reporting_date_discount_factor,
            option_type=bond_option.option_type,
        )
        # Issue #133 Slice A: the same five resolved inputs the premium
        # above just used -- never a second resolve, curve read, or bump.
        greeks = black76_dirty_price_option_greeks_per_100(
            forward_dirty_price=inputs.forward_dirty_price_per_100,
            strike_dirty_price=inputs.strike_dirty_price_per_100,
            price_volatility=price_volatility,
            time_to_expiry=inputs.time_to_expiry_year_fraction,
            discount_factor=inputs.effective_reporting_date_discount_factor,
            option_type=bond_option.option_type,
        )
    except ValueError as exc:
        return PricingResult(
            **common_fields,
            status=PricingStatus.FAILED,
            method=_METHOD_STANDALONE_OVME_BLACK76,
            errors=(
                PricingMessage(
                    code=PricingErrorCode.ENGINE_ERROR,
                    message=str(exc),
                    detail={**error_detail_base, "exception_type": type(exc).__name__},
                ),
            ),
        )

    pv = pv_per_100 * bond_option.notional / 100.0
    # Issue #133 Slice A position contract: per-100 analytic Greeks carry
    # CALL/PUT direction only, while the *position* totals additionally carry
    # BUY = +1 / SELL = -1. ``pv`` above is deliberately NOT multiplied --
    # the model fair premium stays an unsigned fair-value magnitude, exactly
    # as ``position_sign_applied = False`` has always promised.
    position_multiplier = _POSITION_MULTIPLIERS[bond_option.position]
    position_scale = position_multiplier * bond_option.notional / 100.0
    forward_input = snapshot.forward_clean_price_input

    return PricingResult(
        **common_fields,
        status=PricingStatus.SUCCESS,
        method=_METHOD_STANDALONE_OVME_BLACK76,
        pv=pv,
        assumptions={
            "methodology": "ovme_dirty_price_black76_act_act_option_discount_curve",
            "forward_clean_price_per_100": inputs.forward_clean_price_per_100,
            "forward_clean_price_source_system": forward_input.source_system,
            "forward_clean_price_quote_side": forward_input.quote_side.value,
            "strike_clean_price_per_100": inputs.strike_clean_price_per_100,
            "accrued_interest_at_forward_settlement_per_100": (
                inputs.accrued_interest_at_forward_settlement_per_100
            ),
            "forward_dirty_price_per_100": inputs.forward_dirty_price_per_100,
            "strike_dirty_price_per_100": inputs.strike_dirty_price_per_100,
            "pricing_timestamp": request.pricing_timestamp,
            "expiry_timestamp": request.expiry_timestamp,
            "reporting_date": request.reporting_date,
            "forward_settlement_date": request.forward_settlement_date,
            "option_settlement_date": request.option_settlement_date,
            "time_to_expiry_year_fraction": inputs.time_to_expiry_year_fraction,
            "time_to_expiry_convention": "ACT_ACT_ISDA_fractional_timestamp",
            "pricing_to_reporting_discount_factor": (inputs.pricing_to_reporting_discount_factor),
            "pricing_to_option_settlement_discount_factor": (
                inputs.pricing_to_option_settlement_discount_factor
            ),
            "effective_reporting_date_discount_factor": (
                inputs.effective_reporting_date_discount_factor
            ),
            "option_discount_curve_purpose": "OPTION_DISCOUNT_CURVE (used for the option "
            "payoff discount factor to option settlement and the reporting-date factor)",
            "price_volatility": price_volatility,
            "volatility_basis": snapshot.volatility_input.volatility_basis.value,
            "black76_pv_per_100": pv_per_100,
            "notional": bond_option.notional,
            "pv_scaling_formula": "pv = black76_pv_per_100 * notional / 100",
            # --- Issue #133 Slice A: European Black-76 Greeks ---------------
            # Per-100 = INSTRUMENT analytics (CALL/PUT direction, no BUY/SELL).
            "forward_price_delta_per_100": greeks.forward_price_delta_per_100,
            "forward_price_gamma_per_100": greeks.forward_price_gamma_per_100,
            "vega_per_vol_point_per_100": greeks.vega_per_vol_point_per_100,
            "theta_per_calendar_day_per_100": greeks.theta_per_calendar_day_per_100,
            # position_* = TRADER POSITION risk (notional AND BUY/SELL sign).
            # The ``position_`` prefix is load-bearing: a bare ``*_total``
            # name would hide whether the position sign is in the number.
            "position_forward_price_delta_total": (
                greeks.forward_price_delta_per_100 * position_scale
            ),
            "position_forward_price_gamma_total": (
                greeks.forward_price_gamma_per_100 * position_scale
            ),
            "position_vega_per_vol_point_total": (
                greeks.vega_per_vol_point_per_100 * position_scale
            ),
            "position_theta_per_calendar_day_total": (
                greeks.theta_per_calendar_day_per_100 * position_scale
            ),
            "theta_per_year_per_100": greeks.theta_per_year_per_100,
            "theta_effective_continuous_rate": greeks.theta_effective_continuous_rate,
            "greeks_methodology": "black76_forward_dirty_price_closed_form_european_v1",
            "greeks_per_100_basis": "instrument_analytics_option_type_direction_only",
            "greeks_per_100_position_sign_applied": False,
            "greeks_position_total_basis": "trader_position_risk_notional_and_buy_sell_sign",
            "greeks_position_total_sign_applied": True,
            "position": bond_option.position.value,
            "position_multiplier": position_multiplier,
            "greeks_scaling_formula": (
                "position_total = per_100 * notional / 100 * position_multiplier "
                "(BUY = +1, SELL = -1)"
            ),
            "greeks_units": {
                "forward_price_delta": "premium per 100 per +1.00 forward clean price point",
                "forward_price_gamma": (
                    "delta per 100 per +1.00 forward clean price point (per price point squared)"
                ),
                "vega": f"premium per 100 per +{VOLATILITY_POINT} absolute volatility",
                "theta": (
                    "premium per 100 per +1 calendar day "
                    f"(annual theta / {CALENDAR_DAYS_PER_YEAR} calendar days)"
                ),
                "theta_effective_continuous_rate": (
                    "r_eff = -ln(effective_reporting_date_discount_factor) / "
                    "time_to_expiry_year_fraction"
                ),
                "per_100_vs_position_total": (
                    "per_100 values are unsigned-by-position instrument analytics "
                    "(CALL/PUT direction only); position_* totals additionally "
                    "carry notional and the BUY/SELL sign"
                ),
            },
            "option_type": bond_option.option_type.value,
            # Unchanged premium contract: pv is an unsigned fair-value
            # magnitude. This key refers to pv only, never to the Greeks.
            "position_sign_applied": False,
            "priced_component": "bond_option_leg",
            "priced_component_scope": "option_leg_only_not_full_structured_product",
            "excluded_components": [
                "deposit_leg",
                "principal_redemption",
                "physical_delivery",
            ],
            "forward_construction": (
                "explicit_forward_clean_price_input_no_repo_forward_construction"
            ),
        },
    )
