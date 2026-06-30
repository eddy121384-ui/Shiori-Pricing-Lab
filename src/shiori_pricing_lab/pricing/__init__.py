"""Pricing and risk calculation modules.

Includes the simple curve / scenario prototypes and the deterministic pricing
engine *contract* (Issue #10): the ``PricingResult`` value type, structured
messages, and the routing front door ``price(...)``. No product PV / DV01 is
implemented yet — the front door routes every product to the
``UNSUPPORTED_PRODUCT`` path unless a per-product engine is registered.
"""

from shiori_pricing_lab.pricing.engine import (
    ENGINE_CONTRACT_VERSION,
    PricingEngine,
    PricingEngineRegistry,
    price,
    register_engine,
)
from shiori_pricing_lab.pricing.errors import (
    EngineRegistrationError,
    PricingContractError,
    PricingEngineError,
)
from shiori_pricing_lab.pricing.irs_engine import IRSReferenceEngine
from shiori_pricing_lab.pricing.result import (
    PricingErrorCode,
    PricingMessage,
    PricingResult,
    PricingStatus,
    PricingWarningCode,
)

__all__ = [
    "ENGINE_CONTRACT_VERSION",
    "EngineRegistrationError",
    "PricingContractError",
    "PricingEngine",
    "PricingEngineError",
    "PricingEngineRegistry",
    "PricingErrorCode",
    "PricingMessage",
    "PricingResult",
    "PricingStatus",
    "IRSReferenceEngine",
    "PricingWarningCode",
    "price",
    "register_engine",
]

# Register the first real per-product deterministic engine.
register_engine("IRS", IRSReferenceEngine())
