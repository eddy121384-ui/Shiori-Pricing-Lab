"""``BondOptionPriceBasis``: which bond price state a model is expressed in.

**What this is.** One explicit, selectable model convention -- ``DIRTY`` or
``CLEAN`` -- naming the price state that a bond option's forward, strike,
price volatility and duration denominator are all expressed in.

**Why it exists (Trading Desk decision, Issue #211).** The bank's internal
pricing system has historically used **clean** bond forward/strike while
Numerix used **dirty**, and that difference was itself a source of
reconciliation breaks. Shiori therefore makes the basis an explicit,
audited choice rather than hard-coding one globally: a reconciliation tool
that can only speak one of the two conventions cannot reconcile them.

**This is a financial model convention, not a vendor switch.** The members
are deliberately named after the price state itself and never after
Bloomberg, Numerix, or the bank. Two systems may happen to sit on opposite
bases today, but the convention is the modelling choice, and naming it after
whoever currently picks it would make the next system's arrival a rename.

**Neither basis is "correct".**

- ``DIRTY`` is the **default**, because it preserves the already-approved
  OVME-aligned standalone behaviour ratified in Issue #94 / PR #122. It is
  also what market-standard modified duration uses (QuantLib's
  ``BondFunctions.duration(..., Duration.Modified, ...)`` matches a
  dirty-denominator derivative).
- ``CLEAN`` is a **first-class approved alternate basis** for internal-model
  reconciliation. It is not legacy, not deprecated, and not a fallback.

**Never inferred, never silently switched.** The basis is not derived from a
source system, a vendor name, a model name, or a price's magnitude. A caller
states it; an unknown, blank or missing value fails closed. Configuration and
(later) the Workbench may *default* to ``DIRTY``, but the pure calculation
primitives require it explicitly, because a default buried in a pure function
is exactly how a mixed-basis result gets produced without anyone choosing it.

**The end-to-end consistency invariant.** A pricing composition must use one
basis for every leg of the model. Accrued interest is yield-independent, so
the two bases share a price *derivative* but never a proportional quantity::

    CLEAN:
        F_model     = Forward Clean
        K_model     = Strike Clean
        D_B         = -(1 / P_clean) x dP/dY
        sigma_model = |D_B_clean| x sigma_hist_abs
        Black-76      clean-price wrapper

    DIRTY:
        F_model     = Forward Clean + AI_forward
        K_model     = Strike Clean  + AI_forward
        D_B         = -(1 / P_dirty) x dP/dY
        sigma_model = |D_B_dirty| x sigma_hist_abs
        Black-76      dirty-price wrapper

These mixed states are **forbidden** and must never be constructible:

- dirty ``F``/``K`` with a clean-derived ``sigma_P``;
- clean ``F``/``K`` with a dirty-derived ``sigma_P``.

Both wrappers delegate to the single shared Black-76 core
(``pricing/bli_black76_price_option.py``). Selecting a basis picks which
already-existing wrapper a composition uses; it is **not** a change to the
Black-76 formula, and it must never become a second pricing engine.

**Current wiring status, stated truthfully.** As of Issue #211 Phase 2/3 the
mode is approved and the duration and Equivalent-Price-Vol producers are
basis-aware, but the runtime pricing path and Workbench still wire ``DIRTY``
only. Trader-selectable basis end-to-end is a later slice of #211.
"""

from __future__ import annotations

from enum import StrEnum

from shiori_pricing_lab.products.enums import coerce_enum


class BondOptionPriceBasis(StrEnum):
    """Which bond price state a bond-option model is expressed in.

    ``CLEAN`` excludes accrued interest; ``DIRTY`` includes it. Named for the
    price state, never for whichever system currently prefers it.
    """

    DIRTY = "DIRTY"
    CLEAN = "CLEAN"


# What configuration and the later Workbench selector may default to. The
# pure primitives deliberately do not read it -- see the module docstring.
DEFAULT_BOND_OPTION_PRICE_BASIS = BondOptionPriceBasis.DIRTY

SUPPORTED_BOND_OPTION_PRICE_BASES: tuple[str, ...] = tuple(
    member.value for member in BondOptionPriceBasis
)


def require_bond_option_price_basis(
    value: object, field_name: str = "price_basis"
) -> BondOptionPriceBasis:
    """Coerce ``value`` to a :class:`BondOptionPriceBasis` or refuse it.

    Fail-closed on ``None``, a blank string, and any value outside the
    vocabulary -- there is no default here on purpose. ``coerce_enum`` is the
    repository's existing coercion helper, reused rather than reimplemented,
    so the refusal message lists the allowed values the same way every other
    enum-typed field's does.
    """

    if value is None:
        raise ValueError(
            f"{field_name} is required and must be one of "
            f"{SUPPORTED_BOND_OPTION_PRICE_BASES!r}; it is never inferred from a source "
            "system, a model name, or a price's magnitude, and never defaulted inside a "
            "calculation"
        )
    return coerce_enum(value, BondOptionPriceBasis, field_name)


def basis_price_per_100(
    clean_price_per_100: float,
    accrued_interest_per_100: float,
    price_basis: BondOptionPriceBasis,
) -> float:
    """Return the price state ``price_basis`` names.

    The one place the basis selects a price, so a proportional quantity can
    never pick its denominator anywhere else::

        CLEAN -> P_clean
        DIRTY -> P_clean + AI
    """

    basis = require_bond_option_price_basis(price_basis)
    if basis is BondOptionPriceBasis.CLEAN:
        return clean_price_per_100
    return clean_price_per_100 + accrued_interest_per_100
