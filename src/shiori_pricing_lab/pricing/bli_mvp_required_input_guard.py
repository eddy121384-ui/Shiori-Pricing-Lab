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
from shiori_pricing_lab.data.bli_snapshot import BLIMarketDataSnapshot, BLIVolatilityBasis
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.products.bond_option import BondOption
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


def _check_bli_mvp_required_inputs_from_fields(
    *,
    bond_option: BondOption,
    valuation_date: str,
    resolved_bond_reference_data: BondReferenceData,
    market_data_snapshot: BLIMarketDataSnapshot,
) -> RequiredInputGuardResult:
    """Shared, container-agnostic MVP required-input checklist.

    The single reviewable checklist body, run against already-unpacked
    fields so both the ``BLIMVPInputBundle`` path
    (:func:`check_bli_mvp_required_inputs`) and the standalone request path
    (:func:`check_bli_mvp_standalone_option_required_inputs`) reuse *exactly*
    the same reasons, with no duplicated logic (Issue #95). Pure: never
    mutates its arguments, never looks at anything beyond the fields
    passed, and computes no pricing output.

    Some items below are defensive for the bundle path (already guaranteed
    by ``BLIMVPInputBundle`` / ``BondLinkedStructuredProduct`` construction)
    but genuinely reachable for the standalone path, where a bare
    ``BondOption`` is not wrapped by ``BondLinkedStructuredProduct`` and so
    may legally be PHYSICAL-settled, etc. The checklist rejects those cases
    identically regardless of which caller supplied the fields.
    """

    reasons: list[str] = []

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
    # For the bundle path this is defensive: BondLinkedStructuredProduct.
    # __post_init__ already rejects a PHYSICAL-settled bond_option (docs/19),
    # so it cannot fail for a bundle that exists at all (see
    # tests/test_bond_linked_structured_product.py::test_wrapper_rejects_physical_settlement).
    # For the standalone path it is genuinely reachable: a bare BondOption is
    # not wrapped, so PHYSICAL settlement is constructible and must be
    # rejected here as an explicit unsupported-case reason (Issue #95).
    if bond_option.settlement_type is not SettlementType.CASH:
        reasons.append(
            f"bond_option.settlement_type must be CASH, got {bond_option.settlement_type.value}"
        )

    # --- Explicit deal terms (defensive: all required, validated,
    # non-None fields already -- see BondOption.__post_init__ and
    # tests/test_bond_option.py) -----------------------------------------
    if not valuation_date:
        reasons.append("valuation_date must be explicit and non-blank")
    if not bond_option.expiry_date:
        reasons.append("bond_option.expiry_date must be explicit and non-blank")
    if bond_option.strike_price is None:
        reasons.append("bond_option.strike_price must be explicit (non-None)")
    if bond_option.notional is None or not bond_option.notional > 0:
        reasons.append("bond_option.notional must be explicit and positive")

    # --- Bond reference data present (defensive: both containers already
    # type-check and re-verify eligibility for this field) ---------------
    if not isinstance(resolved_bond_reference_data, BondReferenceData):
        reasons.append("resolved_bond_reference_data must be a BondReferenceData")

    # --- Bond clean price present (reachable: BLIBondQuote only requires
    # *one* of clean_price_per_100 / yield_value) -------------------------
    if market_data_snapshot.bond_quote.clean_price_per_100 is None:
        reasons.append(
            "market_data_snapshot.bond_quote.clean_price_per_100 must be present "
            "(a yield-only bond quote is not usable by this MVP slice)"
        )

    # --- Required curves present (defensive: both containers already
    # require both option-leg purposes in the product's currency) --------
    product_currency = bond_option.currency
    present_purposes = {
        point.curve_purpose.value
        for point in market_data_snapshot.curve_points
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
    volatility_basis = market_data_snapshot.volatility_input.volatility_basis
    if volatility_basis not in _SUPPORTED_VOLATILITY_BASES:
        reasons.append(
            "market_data_snapshot.volatility_input.volatility_basis must be one of "
            f"{{PRICE_VOL, EQUIVALENT_PRICE_VOL}}, got {volatility_basis.value} "
            "(YIELD_VOL requires a yield-to-price-vol conversion not implemented yet)"
        )

    return RequiredInputGuardResult(supported=not reasons, reasons=tuple(reasons))


def check_bli_mvp_required_inputs(bundle: BLIMVPInputBundle) -> RequiredInputGuardResult:
    """Return whether ``bundle`` is the narrow MVP-supported bond option case.

    Thin public wrapper (unchanged signature and observable behavior) that
    unpacks ``bundle`` and delegates to the shared
    :func:`_check_bli_mvp_required_inputs_from_fields` checklist. Pure:
    never mutates ``bundle`` (it is already frozen), never looks at market
    data or reference data beyond what ``bundle`` already carries, never
    calls ``resolve_bond_reference_data`` or ``build_bli_mvp_input_bundle``,
    and computes no pricing output. Raises ``TypeError`` for a
    non-``BLIMVPInputBundle`` argument -- a contract violation, not a domain
    "unsupported case" outcome.
    """

    if not isinstance(bundle, BLIMVPInputBundle):
        raise TypeError(f"bundle must be a BLIMVPInputBundle, got {type(bundle).__name__}")

    return _check_bli_mvp_required_inputs_from_fields(
        bond_option=bundle.product.bond_option,
        valuation_date=bundle.valuation_date,
        resolved_bond_reference_data=bundle.resolved_bond_reference_data,
        market_data_snapshot=bundle.market_data_snapshot,
    )


# Curve purpose the OVME-aligned standalone path needs (Issue #94): only
# the Option Discount Curve. Unlike the bundle path it does NOT require a
# BOND_REFERENCE_CURVE, because the forward clean price is supplied
# explicitly (BLIForwardCleanPriceInput) rather than constructed from a
# spot price and the Bond Reference Curve.
_REQUIRED_STANDALONE_CURVE_PURPOSES = frozenset({"OPTION_DISCOUNT_CURVE"})


def check_bli_mvp_standalone_option_required_inputs(
    request: BLIStandaloneBondOptionRequest,
) -> RequiredInputGuardResult:
    """Return whether ``request`` is the OVME-aligned standalone bond option case.

    Standalone-request checklist for the OVME-aligned fair-premium path
    (Issue #94). It shares the product-shape and volatility-basis checks
    with the bundle guard (European / PRICE / CASH; ``PRICE_VOL`` /
    ``EQUIVALENT_PRICE_VOL`` only) but differs where the standalone
    methodology differs, so it deliberately does **not** reuse
    :func:`_check_bli_mvp_required_inputs_from_fields` (that shared body
    stays exactly as-is for the bundle path):

    - an explicit ``forward_clean_price_input`` is **required** (its own
      presence + spot-side coherence are already enforced at request
      construction; re-verified here as a defense-in-depth checklist item);
    - only ``OPTION_DISCOUNT_CURVE`` is required (no
      ``BOND_REFERENCE_CURVE``);
    - a **yield-only** spot ``bond_quote`` is allowed -- the forward no
      longer depends on the spot clean price, so a missing
      ``clean_price_per_100`` is not a rejection reason here.

    Pure; never mutates ``request``; raises ``TypeError`` for a
    non-``BLIStandaloneBondOptionRequest`` argument.
    """

    if not isinstance(request, BLIStandaloneBondOptionRequest):
        raise TypeError(
            f"request must be a BLIStandaloneBondOptionRequest, got {type(request).__name__}"
        )

    bond_option = request.bond_option
    snapshot = request.market_data_snapshot
    reasons: list[str] = []

    # --- Supported product shape only (same as the bundle path) ----------
    if bond_option.exercise_style is not ExerciseStyle.EUROPEAN:
        reasons.append(
            "bond_option.exercise_style must be EUROPEAN, got "
            f"{bond_option.exercise_style.value}"
        )
    if bond_option.payoff_basis is not PayoffBasis.PRICE:
        reasons.append(
            f"bond_option.payoff_basis must be PRICE, got {bond_option.payoff_basis.value}"
        )
    if bond_option.settlement_type is not SettlementType.CASH:
        reasons.append(
            f"bond_option.settlement_type must be CASH, got {bond_option.settlement_type.value}"
        )

    # --- Explicit deal terms (defensive; all validated at construction) --
    if not request.valuation_date:
        reasons.append("valuation_date must be explicit and non-blank")
    if not bond_option.expiry_date:
        reasons.append("bond_option.expiry_date must be explicit and non-blank")
    if bond_option.strike_price is None:
        reasons.append("bond_option.strike_price must be explicit (non-None)")
    if bond_option.notional is None or not bond_option.notional > 0:
        reasons.append("bond_option.notional must be explicit and positive")

    # --- Bond reference data present (for accrued interest at forward
    # settlement -- defensive, re-verified at construction) ---------------
    if not isinstance(request.resolved_bond_reference_data, BondReferenceData):
        reasons.append("resolved_bond_reference_data must be a BondReferenceData")

    # --- Explicit forward clean price input required (Issue #94) ---------
    # No spot-clean-price requirement: the forward is supplied explicitly,
    # so a yield-only spot bond_quote is allowed on this path.
    if snapshot.forward_clean_price_input is None:
        reasons.append(
            "market_data_snapshot.forward_clean_price_input must be present "
            "(the OVME-aligned standalone path prices from an explicit forward clean price)"
        )

    # --- Only the Option Discount Curve required (no Bond Reference Curve) -
    product_currency = bond_option.currency
    present_purposes = {
        point.curve_purpose.value
        for point in snapshot.curve_points
        if point.currency is product_currency
    }
    missing_curve_purposes = _REQUIRED_STANDALONE_CURVE_PURPOSES - present_purposes
    if missing_curve_purposes:
        reasons.append(
            "market_data_snapshot.curve_points is missing required curve "
            f"purpose(s) in currency {product_currency.value}: "
            f"{sorted(missing_curve_purposes)}"
        )

    # --- Explicit price volatility present (same as the bundle path) -----
    volatility_basis = snapshot.volatility_input.volatility_basis
    if volatility_basis not in _SUPPORTED_VOLATILITY_BASES:
        reasons.append(
            "market_data_snapshot.volatility_input.volatility_basis must be one of "
            f"{{PRICE_VOL, EQUIVALENT_PRICE_VOL}}, got {volatility_basis.value} "
            "(YIELD_VOL requires a yield-to-price-vol conversion not implemented yet)"
        )

    return RequiredInputGuardResult(supported=not reasons, reasons=tuple(reasons))
