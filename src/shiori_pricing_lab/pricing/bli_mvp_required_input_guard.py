"""BLI MVP required-input guard (Issue #41, MVP-5 under #82).

Scope: the single, reviewable checklist function that decides whether an
already-valid ``BLIMVPInputBundle`` is *also* the narrow European,
price-based, cash-settled bond option case this MVP slice is scoped to
price with Black-76 on forward clean price (docs/26 §2). This module adds
no pricing math, no forward clean price, no Black-76, and no engine
wiring -- :func:`price_bli_mvp` (``pricing/bli_pricing_engine.py``) is not
modified, imported by, or modified to call this module. A future slice
(#44, MVP-8) is where the engine actually calls this guard before
attempting real valuation.

**Why a standalone function, not an engine-local pre-check:** this
mirrors the existing ``reference_data.eligibility.is_mvp_pricing_eligible``
precedent -- a pure function, separate from any dataclass's own
``__post_init__``, that decides "is this narrower case in scope," collecting
every failing reason rather than stopping at the first (the same pattern
used by ``EligibilityResult``).

**Most of the checklist below is already structurally guaranteed** by the
time a ``BLIMVPInputBundle`` exists at all -- ``BLIMVPInputBundle.
__post_init__`` (docs/24 §6) and ``BondLinkedStructuredProduct.
__post_init__`` (docs/19) already enforce cash settlement, required
non-``None`` valuation date / expiry / strike / notional, a present and
eligible ``BondReferenceData``, and presence of both the Bond Reference
Curve and Option Discount Curve in the product's currency. Those checks
are re-stated here anyway, as explicit, defense-in-depth items on one
readable checklist (the same "re-verify rather than only trust an
upstream guarantee" discipline already used by ``BLIMVPInputBundle``
itself for bond-reference-data eligibility) -- not because they are
believed to be independently reachable for a bundle that already
constructed successfully. Each such item's docstring/comment states
exactly which upstream validator already guarantees it and why no test
forces a fabricated frozen-dataclass state to exercise that branch (see
``tests/test_bli_mvp_required_input_guard.py``'s module docstring for the
full acceptance-criteria-to-test/citation mapping).

**Two checks are genuinely reachable** for a bundle that already
constructed successfully, because neither ``BondOption`` nor
``BondLinkedStructuredProduct`` nor ``BLIMVPInputBundle`` constrains them:

- ``bond_option.exercise_style`` may legally be ``AMERICAN``.
- ``bond_option.payoff_basis`` may legally be ``YIELD``.

Two more are reachable because ``BLIBondQuote`` and ``BLIVolatilityInput``
each accept a wider range of shapes than this MVP slice can use:

- ``bond_quote.clean_price_per_100`` may legally be ``None`` (only *one*
  of clean price / yield is required by ``BLIBondQuote``).
- ``volatility_input.volatility_basis`` may legally be ``YIELD_VOL``.

**Volatility-basis scope (explicit decision for this slice, docs/26
§2.1):** only ``BLIVolatilityBasis.PRICE_VOL`` and
``BLIVolatilityBasis.EQUIVALENT_PRICE_VOL`` pass. docs/26 §2.1 states
both "can... be used directly as sigma in [Annex A] §A.2's formula as-is"
-- a ``PRICE_VOL`` observation needs no conversion, and an
``EQUIVALENT_PRICE_VOL`` value "is, by definition, already in the right
units if one has already been supplied upstream." ``YIELD_VOL`` is, per
the same section, "the one basis that is not directly usable here": Annex
A §A.8's yield-to-price-vol conversion is a separate, not-yet-implemented
dependency. This module performs **no** conversion of any kind -- it only
checks which basis label is already present and rejects ``YIELD_VOL``
outright.
"""

from __future__ import annotations

from dataclasses import dataclass

from shiori_pricing_lab.data.bli_mvp_input_bundle import BLIMVPInputBundle
from shiori_pricing_lab.data.bli_snapshot import BLIVolatilityBasis
from shiori_pricing_lab.products.enums import ExerciseStyle, PayoffBasis, SettlementType
from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData

# Curve purposes this MVP slice needs to compute forward clean price and
# discount the option leg (docs/26 §3, §5). Deliberately narrower than
# BLIMVPInputBundle's own _REQUIRED_MVP_CURVE_PURPOSES (which also
# requires DEPOSIT_CURVE): this slice prices only the embedded bond
# option leg, not the deposit leg (docs/26 §2.2), so only these two are
# re-stated here. Presence of both is already guaranteed by
# BLIMVPInputBundle.__post_init__ for any bundle that exists at all; this
# frozenset exists so the guard's own checklist is self-contained and
# does not need to import BLIMVPInputBundle's private constant.
_REQUIRED_BOND_OPTION_CURVE_PURPOSES = frozenset(
    {"BOND_REFERENCE_CURVE", "OPTION_DISCOUNT_CURVE"}
)

# Volatility bases this MVP slice can use as sigma without any conversion
# (docs/26 §2.1). YIELD_VOL is deliberately excluded -- see module
# docstring.
_SUPPORTED_VOLATILITY_BASES = frozenset(
    {BLIVolatilityBasis.PRICE_VOL, BLIVolatilityBasis.EQUIVALENT_PRICE_VOL}
)


@dataclass(frozen=True)
class RequiredInputGuardResult:
    """Outcome of the BLI MVP required-input guard.

    ``reasons`` is empty when ``supported`` is ``True``, and otherwise
    lists every checklist item that failed -- a bundle can fail more than
    one item at once (e.g. American exercise *and* a yield-based payoff),
    mirroring ``reference_data.eligibility.EligibilityResult``.
    """

    supported: bool
    reasons: tuple[str, ...]


def check_bli_mvp_required_inputs(bundle: BLIMVPInputBundle) -> RequiredInputGuardResult:
    """Return whether ``bundle`` is the narrow MVP-supported bond option case.

    Pure function: never mutates ``bundle`` (it is already frozen), never
    looks at market data or reference data beyond what ``bundle`` already
    carries, never calls ``resolve_bond_reference_data`` or
    ``build_bli_mvp_input_bundle``, and computes no forward price, no
    Black-76 premium, and no other pricing output. Raises ``TypeError``
    for a non-``BLIMVPInputBundle`` argument -- a contract violation, not
    a domain "unsupported case" outcome.
    """

    if not isinstance(bundle, BLIMVPInputBundle):
        raise TypeError(f"bundle must be a BLIMVPInputBundle, got {type(bundle).__name__}")

    reasons: list[str] = []

    bond_option = bundle.product.bond_option

    # --- Supported product shape only (reachable: neither BondOption nor
    # BondLinkedStructuredProduct constrains exercise_style/payoff_basis) --
    if bond_option.exercise_style is not ExerciseStyle.EUROPEAN:
        reasons.append(
            "bond_option.exercise_style must be EUROPEAN, got "
            f"{bond_option.exercise_style.value}"
        )
    if bond_option.payoff_basis is not PayoffBasis.PRICE:
        reasons.append(
            f"bond_option.payoff_basis must be PRICE, got {bond_option.payoff_basis.value}"
        )
    # Defensive: BondLinkedStructuredProduct.__post_init__ already rejects
    # a PHYSICAL-settled bond_option (docs/19), so this cannot fail for a
    # bundle that exists at all -- see
    # tests/test_bond_linked_structured_product.py::test_wrapper_rejects_physical_settlement.
    if bond_option.settlement_type is not SettlementType.CASH:
        reasons.append(
            f"bond_option.settlement_type must be CASH, got {bond_option.settlement_type.value}"
        )

    # --- Explicit deal terms (defensive: all required, validated,
    # non-None fields already -- see BondOption.__post_init__ and
    # tests/test_bond_option.py) -----------------------------------------
    if not bundle.valuation_date:
        reasons.append("bundle.valuation_date must be explicit and non-blank")
    if not bond_option.expiry_date:
        reasons.append("bond_option.expiry_date must be explicit and non-blank")
    if bond_option.strike_price is None:
        reasons.append("bond_option.strike_price must be explicit (non-None)")
    if bond_option.notional is None or not bond_option.notional > 0:
        reasons.append("bond_option.notional must be explicit and positive")

    # --- Bond reference data present (defensive: BLIMVPInputBundle.
    # __post_init__ already type-checks and re-verifies eligibility for
    # this field -- see tests/test_bli_mvp_input_bundle.py) --------------
    if not isinstance(bundle.resolved_bond_reference_data, BondReferenceData):
        reasons.append("bundle.resolved_bond_reference_data must be a BondReferenceData")

    # --- Bond clean price present (reachable: BLIBondQuote only requires
    # *one* of clean_price_per_100 / yield_value) -------------------------
    if bundle.market_data_snapshot.bond_quote.clean_price_per_100 is None:
        reasons.append(
            "market_data_snapshot.bond_quote.clean_price_per_100 must be present "
            "(a yield-only bond quote is not usable by this MVP slice)"
        )

    # --- Required curves present (defensive: BLIMVPInputBundle.
    # __post_init__ already requires both in the product's currency --
    # see tests/test_bli_mvp_input_bundle.py) -----------------------------
    product_currency = bond_option.currency
    present_purposes = {
        point.curve_purpose.value
        for point in bundle.market_data_snapshot.curve_points
        if point.currency is product_currency
    }
    missing_curve_purposes = _REQUIRED_BOND_OPTION_CURVE_PURPOSES - present_purposes
    if missing_curve_purposes:
        reasons.append(
            "market_data_snapshot.curve_points is missing required curve "
            f"purpose(s) in currency {product_currency.value}: "
            f"{sorted(missing_curve_purposes)}"
        )

    # --- Explicit price volatility present (reachable: BLIVolatilityInput
    # permits YIELD_VOL, which this slice cannot use without a conversion
    # that does not exist yet -- docs/26 §2.1) ----------------------------
    volatility_basis = bundle.market_data_snapshot.volatility_input.volatility_basis
    if volatility_basis not in _SUPPORTED_VOLATILITY_BASES:
        reasons.append(
            "market_data_snapshot.volatility_input.volatility_basis must be one of "
            f"{{PRICE_VOL, EQUIVALENT_PRICE_VOL}}, got {volatility_basis.value} "
            "(YIELD_VOL requires a yield-to-price-vol conversion not implemented yet)"
        )

    return RequiredInputGuardResult(supported=not reasons, reasons=tuple(reasons))
