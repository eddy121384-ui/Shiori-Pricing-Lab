"""The deterministic pricing engine boundary: Protocol, registry, front door.

This is the *behavioural* half of the pricing contract (Issue #10). It defines
**how a product gets routed to an engine**, not how anything is priced. No
product PV / DV01 is computed here, and no engine is registered yet — every
product currently routes to the ``UNSUPPORTED_PRODUCT`` path until a future
per-product engine is added.

The front door:

- validates the basic call contract (raises on ``None`` / missing attributes);
- defensively checks valuation-date consistency between context and snapshot;
- routes by ``product.product_type`` through a registry;
- returns ``FAILED + UNSUPPORTED_PRODUCT`` when no engine is registered;
- wraps a delegated engine call so an internal failure becomes
  ``FAILED + ENGINE_ERROR`` rather than escaping.

Boundary rules (must stay true):

- It never fetches market data (no providers / CSV / Bloomberg / web).
- It never uses the system date / ``today``.
- It never mutates the product, context, or snapshot.
- At runtime it imports no data-provider, valuation, UI, or AI module. Product /
  context / snapshot types are referenced only for annotations under
  ``TYPE_CHECKING``; at runtime the front door duck-types the few attributes it
  reads, which also avoids a ``pricing <-> valuation`` import cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from shiori_pricing_lab.pricing.errors import (
    EngineRegistrationError,
    PricingContractError,
)
from shiori_pricing_lab.pricing.result import (
    PricingErrorCode,
    PricingMessage,
    PricingResult,
    PricingStatus,
)

if TYPE_CHECKING:  # annotations only — no runtime import of other layers
    from shiori_pricing_lab.data.snapshot import MarketDataSnapshot
    from shiori_pricing_lab.valuation.context import ValuationContext

# Simple module constant for now; package versioning is not wired yet.
ENGINE_CONTRACT_VERSION = "0.1.0"

# Provenance label used by the front door's own (non-product) results.
_FRONT_DOOR_NAME = "front_door"


@runtime_checkable
class PricingEngine(Protocol):
    """Structural interface a per-product pricing engine must satisfy.

    An engine is anything with a ``price(product, valuation_context,
    market_snapshot)`` method returning a :class:`PricingResult`. Using a
    ``Protocol`` (not an ABC) lets a plain function-wrapping object, a class, or
    a future compiled-backend adapter all qualify without inheritance.
    """

    def price(
        self,
        product: object,
        valuation_context: ValuationContext,
        market_snapshot: MarketDataSnapshot,
    ) -> PricingResult: ...


class PricingEngineRegistry:
    """Routing table from ``product_type`` to a :class:`PricingEngine`.

    Kept deliberately small. In this slice no engines are registered, so every
    lookup misses and the front door returns ``UNSUPPORTED_PRODUCT``. Each future
    PR registers one product engine here.
    """

    def __init__(self) -> None:
        self._engines: dict[str, PricingEngine] = {}

    def register(self, product_type: str, engine: PricingEngine) -> None:
        """Register ``engine`` for ``product_type``.

        Invalid registration is a contract violation and raises
        :class:`EngineRegistrationError`: the product type must be a non-blank
        string and the engine must expose a callable ``price``.
        """

        if not isinstance(product_type, str) or not product_type.strip():
            raise EngineRegistrationError("product_type must be a non-blank string")
        if not callable(getattr(engine, "price", None)):
            raise EngineRegistrationError(
                f"engine for {product_type!r} must have a callable 'price' method"
            )
        self._engines[product_type] = engine

    def get(self, product_type: str) -> PricingEngine | None:
        return self._engines.get(product_type)

    def is_registered(self, product_type: str) -> bool:
        return product_type in self._engines


# The default registry used by the module-level front door. Empty in this slice.
_DEFAULT_REGISTRY = PricingEngineRegistry()


def register_engine(product_type: str, engine: PricingEngine) -> None:
    """Register an engine on the default registry (module-level helper)."""

    _DEFAULT_REGISTRY.register(product_type, engine)


def _require_attr(obj: object, name: str, what: str) -> object:
    """Return ``obj.name`` or raise ``PricingContractError`` if it is absent.

    Used by the front door's contract guards. A missing required attribute is a
    programming error (wrong call shape), so it raises rather than returning a
    failed result.
    """

    if obj is None:
        raise PricingContractError(f"{what} must not be None")
    try:
        return getattr(obj, name)
    except AttributeError as exc:
        raise PricingContractError(f"{what} is missing required attribute {name!r}") from exc


def _failed(
    *,
    product_id: str,
    product_type: str,
    valuation_date: str,
    result_currency: str,
    market_data_as_of: str,
    code: PricingErrorCode,
    message: str,
    method: str,
    detail: dict | None = None,
) -> PricingResult:
    """Build a ``FAILED`` result carrying one structured error message."""

    return PricingResult(
        product_id=product_id,
        product_type=product_type,
        valuation_date=valuation_date,
        result_currency=result_currency,
        status=PricingStatus.FAILED,
        engine_name=_FRONT_DOOR_NAME,
        engine_version=ENGINE_CONTRACT_VERSION,
        method=method,
        market_data_as_of=market_data_as_of,
        errors=(PricingMessage(code=code, message=message, detail=detail or {}),),
    )


def price(
    product: object,
    valuation_context: ValuationContext,
    market_snapshot: MarketDataSnapshot,
    *,
    registry: PricingEngineRegistry | None = None,
) -> PricingResult:
    """Route ``product`` to its registered engine and return a ``PricingResult``.

    Contract violations (``None`` inputs, missing required attributes) raise
    :class:`PricingContractError`. Domain failures (date mismatch, unsupported
    product, an engine raising internally) are returned as ``FAILED`` results.
    """

    # --- Contract guards (raise-path): basic call shape must be valid. -------
    product_id = _require_attr(product, "product_id", "product")
    product_type = _require_attr(product, "product_type", "product")
    ctx_valuation_date = _require_attr(valuation_context, "valuation_date", "valuation_context")
    result_currency = _require_attr(
        valuation_context, "reporting_currency", "valuation_context"
    )
    snapshot_valuation_date = _require_attr(
        market_snapshot, "valuation_date", "market_snapshot"
    )

    active_registry = registry if registry is not None else _DEFAULT_REGISTRY

    # --- Defensive valuation-date consistency (return-path). ----------------
    # ValuationContext already enforces this at construction, so reaching here
    # means a hand-built context bypassed that check. Never uses the system date.
    if ctx_valuation_date != snapshot_valuation_date:
        return _failed(
            product_id=product_id,
            product_type=product_type,
            valuation_date=ctx_valuation_date,
            result_currency=result_currency,
            market_data_as_of=snapshot_valuation_date,
            code=PricingErrorCode.VALUATION_DATE_MISMATCH,
            message=(
                "valuation_context.valuation_date does not match "
                "market_snapshot.valuation_date: "
                f"{ctx_valuation_date} != {snapshot_valuation_date}"
            ),
            method="unrouted",
        )

    # --- Defensive market-snapshot identity (return-path). ------------------
    # The context carries its own snapshot (used by build_curve()), and the
    # engine also receives an explicit market_snapshot. If they are not the same
    # object, two different market states would be silently mixed even when their
    # valuation dates happen to agree. Require the context to expose its snapshot
    # and reject a mismatch before routing.
    context_snapshot = _require_attr(
        valuation_context, "market_snapshot", "valuation_context"
    )
    if context_snapshot is not market_snapshot:
        return _failed(
            product_id=product_id,
            product_type=product_type,
            valuation_date=ctx_valuation_date,
            result_currency=result_currency,
            market_data_as_of=snapshot_valuation_date,
            code=PricingErrorCode.MARKET_SNAPSHOT_MISMATCH,
            message=(
                "valuation_context.market_snapshot and the explicit "
                "market_snapshot argument are not the same object, so two "
                "different market states would be mixed"
            ),
            method="unrouted",
            detail={
                "context_valuation_date": ctx_valuation_date,
                "passed_snapshot_valuation_date": snapshot_valuation_date,
            },
        )

    # --- Routing (return-path): no engine registered -> unsupported. --------
    engine = active_registry.get(product_type)
    if engine is None:
        return _failed(
            product_id=product_id,
            product_type=product_type,
            valuation_date=ctx_valuation_date,
            result_currency=result_currency,
            market_data_as_of=snapshot_valuation_date,
            code=PricingErrorCode.UNSUPPORTED_PRODUCT,
            message=f"no pricing engine registered for product_type {product_type!r}",
            method="unsupported",
        )

    # --- Delegate; a raising engine becomes a FAILED result, not an escape. -
    try:
        return engine.price(product, valuation_context, market_snapshot)
    except Exception as exc:  # noqa: BLE001 - deliberately convert to a domain failure
        return _failed(
            product_id=product_id,
            product_type=product_type,
            valuation_date=ctx_valuation_date,
            result_currency=result_currency,
            market_data_as_of=snapshot_valuation_date,
            code=PricingErrorCode.ENGINE_ERROR,
            message=f"engine for {product_type!r} raised: {exc}",
            method="engine_error",
            detail={"exception_type": type(exc).__name__},
        )
