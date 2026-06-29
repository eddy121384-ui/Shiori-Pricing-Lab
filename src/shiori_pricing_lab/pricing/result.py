"""The pricing result contract: status, structured messages, and PricingResult.

This module is the *value-type* half of the deterministic pricing boundary
(Issue #10). It defines what an engine returns, not how anything is priced.
There is deliberately no pricing maths here, and no PV / DV01 is computed — the
numeric fields exist in the schema so the contract is stable for downstream
consumers (UI, historical valuation, AI), but they default to ``None`` until a
future per-product engine fills them in.

Strict boundary: this module imports nothing from the data, valuation, products,
or UI layers. It is pure, frozen, and serializable (``str``-valued enums plus
frozen dataclasses round-trip cleanly through ``dataclasses.asdict`` and
``json.dumps`` without a custom encoder), exactly like the product schemas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class PricingStatus(StrEnum):
    """Outcome of a pricing call.

    Only three states exist. "Unsupported product", "missing market data", and
    similar domain failures are *not* separate statuses; they are ``FAILED``
    results whose ``errors`` carry a specific :class:`PricingErrorCode`.
    """

    SUCCESS = "SUCCESS"
    SUCCESS_WITH_WARNINGS = "SUCCESS_WITH_WARNINGS"
    FAILED = "FAILED"


class PricingErrorCode(StrEnum):
    """Machine-readable reason a result is ``FAILED``.

    These label *expected domain failures* returned on a ``PricingResult`` (the
    return-path). Programming / contract violations are raised as exceptions
    instead (see ``pricing/errors.py``).
    """

    UNSUPPORTED_PRODUCT = "UNSUPPORTED_PRODUCT"
    MISSING_MARKET_DATA = "MISSING_MARKET_DATA"
    VALUATION_DATE_MISMATCH = "VALUATION_DATE_MISMATCH"
    INVALID_PRODUCT = "INVALID_PRODUCT"
    ENGINE_ERROR = "ENGINE_ERROR"


class PricingWarningCode(StrEnum):
    """Machine-readable reason a successful result carries a warning.

    Kept intentionally small. New codes are added only when a concrete engine
    needs one.
    """

    TRADE_MATURED = "TRADE_MATURED"
    FORWARD_STARTING = "FORWARD_STARTING"
    DATA_QUALITY = "DATA_QUALITY"


@dataclass(frozen=True)
class PricingMessage:
    """A single structured warning or error.

    ``code`` is a controlled enum value so tests and the future AI layer can
    branch on it; ``message`` is a human-readable explanation. ``detail`` is an
    optional small dict for extra context (for example which currency's market
    data was missing). Messages are never bare strings.
    """

    code: str
    message: str
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PricingResult:
    """Structured output of a pricing call.

    A result is returned for every *domain* outcome — success, success with
    warnings, or an expected failure. Contract / programming violations raise
    exceptions instead of producing a result.

    Field groups:

    - **MVP fields** (always populated): identity, dates, currency, status,
      messages, and engine provenance.
    - **Future numeric/structured fields** (``pv``, ``dv01``, ``cashflows``,
      ``scenario_results``): part of the contract from day one but default to
      ``None`` because no product pricing exists yet.
    - **Diagnostics**: a small free-form dict for engine notes.

    ``warnings``, ``errors`` (and ``cashflows`` when used) are tuples so the
    frozen result is genuinely immutable.
    """

    # --- MVP fields ----------------------------------------------------------
    product_id: str
    product_type: str
    valuation_date: str
    result_currency: str
    status: PricingStatus
    engine_name: str
    engine_version: str
    method: str
    market_data_as_of: str
    warnings: tuple[PricingMessage, ...] = ()
    errors: tuple[PricingMessage, ...] = ()
    assumptions: dict = field(default_factory=dict)

    # --- Future fields: in the contract now, computed later ------------------
    pv: float | None = None
    dv01: float | None = None
    cashflows: tuple | None = None
    scenario_results: object | None = None

    # --- Diagnostics ---------------------------------------------------------
    diagnostics: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Keep ``status`` a real enum member even if a raw string was passed, so
        # a result always serializes to a known status value.
        if not isinstance(self.status, PricingStatus):
            object.__setattr__(self, "status", PricingStatus(self.status))

    @property
    def is_success(self) -> bool:
        """True for SUCCESS or SUCCESS_WITH_WARNINGS (i.e. not FAILED)."""

        return self.status is not PricingStatus.FAILED
