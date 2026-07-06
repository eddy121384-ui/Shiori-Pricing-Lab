"""BLI pricing engine skeleton (docs/25 implementation slice).

Scope, per `docs/25_bli_pricing_engine_skeleton_preflight.md`: the smallest
possible callable seam a future PR can register real BLI valuation logic
behind. **No real valuation math, payoff formula, cash-flow generation,
schedule engine, yield-to-price/price-to-yield conversion, curve
interpolation, volatility surface, credit spread model, Treasury FTP
parser, ingestion, Bloomberg/API connector, QuantLib adapter, or UI is
added here.**

**Input boundary (docs/25 §3):** :func:`price_bli_mvp` accepts only an
already-validated ``BLIMVPInputBundle`` -- never a raw
``BondLinkedStructuredProduct`` / ``BondReferenceData`` /
``BLIMarketDataSnapshot`` / ISIN / curve / deposit observation. It never
calls ``resolve_bond_reference_data`` or ``build_bli_mvp_input_bundle``:
every input-readiness gate (ISIN identity, eligibility, currency
coherence, valuation-date/as-of coherence, deposit-rate consistency,
curve-purpose presence) already lives in ``BLIMVPInputBundle.
__post_init__`` and its builder (docs/24 §6, §12 step 5) -- this module's
only job is to prove the seam exists and fail predictably until real
pricing lands, not to re-derive "is this bundle valid."

**docs/25 §4's three required questions, answered here (not silently
picked):**

1. *Can the shared ``PricingResult``/``PricingStatus``/``PricingErrorCode``
   be reused as-is, with no new BLI-only result or status type?* **Yes,
   with zero changes to ``pricing/result.py``, ``pricing/errors.py``, or
   ``pricing/engine.py``.** ``PricingResult``'s field set (identity,
   dates, currency, status, structured messages, engine provenance, and
   ``pv``/``dv01``/``cashflows`` defaulting to ``None``) already fits a
   BLI "not implemented yet" outcome without inventing anything: every
   field this skeleton needs to populate has a direct source on the
   bundle (``product.product_id``/``product.product_type``,
   ``bundle.valuation_date``, ``product.bond_option.currency``,
   ``bundle.market_data_snapshot.as_of_timestamp``). ``PricingStatus.
   FAILED`` plus the existing ``PricingErrorCode.UNSUPPORTED_PRODUCT``
   member already means exactly this case: the front door returns the
   same status/code combination for OIS/CCS/FX Swap today, precisely
   because no real per-product engine exists yet for them either (docs/09
   §8). A BLI bundle with no real valuation methodology behind it yet is
   the same situation, so no new ``PricingErrorCode`` member is
   introduced. No ``BLIPricingResult``/``BLIPricingStatus`` is defined by
   this module, and none is needed.
2. *Can a ``BLIMVPInputBundle``-based entrypoint adapt to the existing
   ``PricingEngine.price(product, valuation_context, market_snapshot)``
   Protocol shape?* **No, not without fabricating structure that does not
   exist.** ``BLIMVPInputBundle`` deliberately merges product + resolved
   reference data + market snapshot into **one** pre-validated object
   (docs/24 §2); there is no BLI equivalent of the vanilla-rates-core
   ``ValuationContext`` (reporting currency, model settings, a
   `.market_snapshot` back-reference) for the front door's
   ``valuation_context`` parameter to bind to, and ``BLIMarketDataSnapshot``
   does not fill that role either. Building a synthetic
   ``valuation_context``-shaped shim purely to satisfy the Protocol's
   arity would invent a second, parallel object with no real BLI use --
   exactly the kind of fabrication the "boring, explicit" style rule
   warns against. This skeleton therefore does not register anything on
   ``PricingEngineRegistry`` and does not implement the ``PricingEngine``
   Protocol; ``pricing/engine.py`` is unmodified.
3. *Given (2), is the separate ``price_bli_mvp`` entrypoint a temporary
   adapter-shaped stopgap or a deliberate, permanent path?* ``price_bli_mvp``
   is **the explicit, direct bundle-based entrypoint for the BLI MVP
   path** -- this PR does not register it on ``PricingEngineRegistry``
   and does not implement the ``PricingEngine`` Protocol for BLI. This
   does **not** reopen docs/14 §4.5's or docs/24 §2's prior assumption:
   the shared *result value type* is still reused exactly as those docs
   required ("not a second, parallel contract") -- only the *entrypoint
   routing* differs, because BLI's natural caller already holds a
   validated ``BLIMVPInputBundle``, not a bare ``(product,
   valuation_context, market_snapshot)`` triple. **Whether a future slice
   additionally registers a bundle-unpacking adapter behind ``price(...)``
   for BLI (so the generic front door can also route to it) remains an
   open design decision** -- this skeleton takes no position on it either
   way and does not foreclose it.

**Not-implemented behavior chosen (docs/25 §6):** for a valid bundle,
return a deterministic ``PricingResult(status=FAILED,
errors=[PricingErrorCode.UNSUPPORTED_PRODUCT])`` rather than raising.
Reasoning: "not implemented yet" for an already-valid bundle is not a
contract/programming violation (the caller did nothing wrong -- the
bundle passed every one of its own construction gates), so it does not
belong on the raise-path (``pricing/errors.py``). It most closely mirrors
the existing front door's own precedent for a product type with no
registered engine yet (``FAILED + UNSUPPORTED_PRODUCT`` for OIS/CCS/FX
Swap) -- a structured, inspectable result a batch/UI/AI caller can branch
on without wrapping every call in ``try/except``, consistent with the
hybrid failure-handling model already documented in ``docs/09`` §3/§8.

**Wrong input type raises ``TypeError``** (docs/25 §6), mirroring
``BLIMVPInputBundle.__post_init__``'s own ``isinstance`` checks -- a
contract violation, not a "not implemented" outcome.
"""

from __future__ import annotations

from shiori_pricing_lab.data.bli_mvp_input_bundle import BLIMVPInputBundle
from shiori_pricing_lab.pricing.result import (
    PricingErrorCode,
    PricingMessage,
    PricingResult,
    PricingStatus,
)

ENGINE_NAME = "bli_mvp_pricing_engine_skeleton"
ENGINE_VERSION = "0.1.0"
METHOD = "not_implemented"


def price_bli_mvp(bundle: BLIMVPInputBundle) -> PricingResult:
    """Return a deterministic "not implemented" ``PricingResult`` for ``bundle``.

    Accepts only a ``BLIMVPInputBundle`` -- raises :class:`TypeError` for
    anything else, including a raw ``BondLinkedStructuredProduct``,
    ``BondReferenceData``, or ``BLIMarketDataSnapshot``. Never mutates
    ``bundle`` (it is already a frozen dataclass). Never calls
    ``resolve_bond_reference_data`` or ``build_bli_mvp_input_bundle``.
    Never computes ``pv``/``dv01``/``cashflows`` -- the returned result
    leaves them at their contract default of ``None``.
    """

    if not isinstance(bundle, BLIMVPInputBundle):
        raise TypeError(f"bundle must be a BLIMVPInputBundle, got {type(bundle).__name__}")

    product = bundle.product
    return PricingResult(
        product_id=product.product_id,
        product_type=product.product_type,
        valuation_date=bundle.valuation_date,
        result_currency=product.bond_option.currency.value,
        status=PricingStatus.FAILED,
        engine_name=ENGINE_NAME,
        engine_version=ENGINE_VERSION,
        method=METHOD,
        market_data_as_of=bundle.market_data_snapshot.as_of_timestamp,
        errors=(
            PricingMessage(
                code=PricingErrorCode.UNSUPPORTED_PRODUCT,
                message=(
                    "BLI pricing engine skeleton: no real valuation methodology is "
                    "implemented yet for this bundle"
                ),
                detail={
                    "bundle_id": bundle.bundle_id,
                    "product_id": product.product_id,
                },
            ),
        ),
    )
