"""Exceptions for the pricing engine *raise-path*.

The pricing boundary uses two channels (Issue #10):

- **Return-path** — expected *domain* failures (unsupported product, missing
  market data, valuation-date mismatch, ...) are returned as
  ``PricingResult(status=FAILED, errors=[...])`` so a batch / historical loop can
  survive a bad trade without wrapping every call in ``try/except``.
- **Raise-path (this module)** — *programming / contract violations* are bugs,
  not market outcomes, so they raise. Examples: a ``None`` product / context /
  snapshot, an object missing a required attribute, or an invalid engine
  registration.

This module imports nothing from the data, valuation, products, or UI layers.
"""

from __future__ import annotations


class PricingEngineError(Exception):
    """Base class for pricing-engine contract violations (the raise-path)."""


class PricingContractError(PricingEngineError):
    """A pricing call was made with a structurally invalid argument.

    Raised for ``None`` inputs, wrong types, or objects missing a required
    attribute (for example a product with no ``product_type``). These are
    programming errors, distinct from the domain failures returned on a
    ``PricingResult``.
    """


class EngineRegistrationError(PricingEngineError):
    """An engine was registered against the routing registry incorrectly.

    Raised for a blank product type or an engine that does not satisfy the
    :class:`~shiori_pricing_lab.pricing.engine.PricingEngine` protocol (no
    callable ``price``).
    """
