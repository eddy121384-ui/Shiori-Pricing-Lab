"""BLI pricing engine: European price-based cash-settled Black-76 (Issue #44, MVP-8).

Scope: wire the already-reviewed MVP pieces behind the existing
`price_bli_mvp(bundle) -> PricingResult` entrypoint, in this exact order:

1. #41 `check_bli_mvp_required_inputs(bundle)` -- reject an unsupported
   product shape or missing input before attempting any pricing math.
2. #42 `forward_clean_price_per_100_for_bundle(bundle)` -- forward clean
   price F, per 100 (Bond Reference Curve, per Annex A §A.5.3).
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

**Result mapping (docs/09's hybrid failure model, unchanged):**

- Wrong ``bundle`` type: raises ``TypeError`` -- a contract violation,
  not a domain outcome (unchanged from the prior skeleton).
- Guard rejects the bundle (``supported=False``): returns
  ``PricingResult(status=FAILED, errors=[PricingErrorCode.
  UNSUPPORTED_PRODUCT])``, with the guard's full ``reasons`` tuple
  preserved verbatim in the message ``detail`` -- never string-sniffed
  or split across multiple error codes. Every guard rejection, whatever
  the specific reason, maps to this one code; no reason-code taxonomy is
  added to ``RequiredInputGuardResult``, and ``pricing/result.py`` is not
  modified.
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
from shiori_pricing_lab.data.bli_snapshot import BLICurvePurpose
from shiori_pricing_lab.pricing.bli_black76_price_option import (
    black76_price_option_pv_per_100,
)
from shiori_pricing_lab.pricing.bli_curve_discount_factor import (
    discount_factor_from_continuous_zero_curve,
)
from shiori_pricing_lab.pricing.bli_forward_clean_price import (
    forward_clean_price_per_100_for_bundle,
)
from shiori_pricing_lab.pricing.bli_mvp_required_input_guard import (
    check_bli_mvp_required_inputs,
)
from shiori_pricing_lab.pricing.bli_valuation_time import year_fraction_to_expiry
from shiori_pricing_lab.pricing.result import (
    PricingErrorCode,
    PricingMessage,
    PricingResult,
    PricingStatus,
)

ENGINE_NAME = "bli_mvp_black76_forward_clean_price_engine"
ENGINE_VERSION = "1.0.0"

# Method identifiers distinguish "never attempted real pricing" (the
# guard rejected the bundle) from "pricing was attempted" (success or a
# downstream domain failure) -- both a caller and the assumptions/
# diagnostics metadata can branch on this.
_METHOD_NOT_SUPPORTED = "not_supported"
_METHOD_BLACK76_FORWARD_CLEAN_PRICE = "black76_forward_clean_price_v1"


def price_bli_mvp(bundle: BLIMVPInputBundle) -> PricingResult:
    """Return a deterministic ``PricingResult`` for ``bundle``.

    Accepts only a ``BLIMVPInputBundle`` -- raises :class:`TypeError` for
    anything else. Never mutates ``bundle``. Never calls
    ``resolve_bond_reference_data`` or ``build_bli_mvp_input_bundle``.
    See the module docstring for the exact composition order and
    result-mapping rules.
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
    if not guard_result.supported:
        return PricingResult(
            **common_fields,
            status=PricingStatus.FAILED,
            method=_METHOD_NOT_SUPPORTED,
            errors=(
                PricingMessage(
                    code=PricingErrorCode.UNSUPPORTED_PRODUCT,
                    message=(
                        "BLI required-input guard rejected this bundle: "
                        f"{'; '.join(guard_result.reasons)}"
                    ),
                    detail={
                        "bundle_id": bundle.bundle_id,
                        "product_id": product.product_id,
                        "reasons": list(guard_result.reasons),
                    },
                ),
            ),
        )

    try:
        forward_clean_price = forward_clean_price_per_100_for_bundle(bundle)
        time_to_expiry = year_fraction_to_expiry(bundle.valuation_date, bond_option.expiry_date)
        option_discount_factor = discount_factor_from_continuous_zero_curve(
            bundle.market_data_snapshot.curve_points,
            currency=bond_option.currency,
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            target_year_fraction=time_to_expiry,
        )
        price_volatility = bundle.market_data_snapshot.volatility_input.volatility
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
                    detail={
                        "bundle_id": bundle.bundle_id,
                        "product_id": product.product_id,
                        "exception_type": type(exc).__name__,
                    },
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
            "volatility_basis": bundle.market_data_snapshot.volatility_input.volatility_basis.value,
            "option_discount_factor": option_discount_factor,
            "black76_pv_per_100": pv_per_100,
            "notional": bond_option.notional,
            "pv_scaling_formula": "pv = black76_pv_per_100 * notional / 100",
            "bond_reference_curve_purpose": "BOND_REFERENCE_CURVE (used only for forward "
            "clean price, via forward_clean_price_per_100_for_bundle)",
            "option_discount_curve_purpose": "OPTION_DISCOUNT_CURVE (used only for the "
            "option PV discount factor)",
            "option_type": bond_option.option_type.value,
            "position_sign_applied": False,
        },
    )
