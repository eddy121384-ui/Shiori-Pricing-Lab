"""Bounded, non-mutating overlay of exactly six trader-editable fields onto a
standalone bond-option case (manual functional prototype, PR #136).

Scope: one small, pure function -- :func:`apply_standalone_option_case_overlay`
-- that relocates six raw values into their existing envelope slots on a copy
of a base standalone-option-case mapping (the same envelope shape
``standalone_option_workbench.price_standalone_option_case`` accepts). It
performs no pricing, discounting, accrual, scaling, sign, or unit-conversion
logic of any kind, and it never mutates its inputs. The only approved pricing
entry point remains ``standalone_option_workbench.price_standalone_option_case``,
called by this module's caller (the HTTP bridge), never reimplemented here.

**The six overlay fields (Eddy-approved, this round only):**
``bond_option.option_type``, ``bond_option.position``,
``bond_option.strike_price``, ``bond_option.notional``,
``volatility_input.volatility``, and
``forward_clean_price_input.forward_clean_price_per_100``. Every other case
field -- timing, curve, quote side, reference data, settlement, and
methodology -- is carried through unchanged from the base case. The overlay
mapping must supply exactly these six keys; a missing or unknown key raises a
clear ``ValueError`` rather than silently defaulting or ignoring it. Every
value's own finiteness/positivity/enum validation still happens later, inside
the existing typed constructors (``BondOption``, ``BLIVolatilityInput``,
``BLIForwardCleanPriceInput``) when the resulting case is built -- this module
adds no validation of its own beyond the exact-key-set check.
"""

from __future__ import annotations

OVERLAY_FIELDS = frozenset(
    {
        "option_type",
        "position",
        "strike_price",
        "notional",
        "volatility",
        "forward_clean_price_per_100",
    }
)


def apply_standalone_option_case_overlay(base_case: dict, overlay: dict) -> dict:
    """Return a new case mapping with exactly the six overlay fields replaced.

    ``base_case`` is an already-parsed standalone-option-case envelope
    mapping (e.g. ``json.loads(examples/standalone_option_case.json)``).
    ``overlay`` must contain exactly the keys in :data:`OVERLAY_FIELDS` --
    a missing or unknown key raises ``ValueError`` naming the offending
    key(s). Only ``bond_option``, ``volatility_input``, and
    ``forward_clean_price_input`` are shallow-copied (with the relevant
    field replaced); every other top-level key, and every field the caller
    did not name, is the exact same value already on ``base_case``. Neither
    ``base_case`` nor any of its nested mappings is mutated.
    """

    if not isinstance(base_case, dict):
        raise ValueError(f"base_case must be a mapping, got {type(base_case).__name__}")
    if not isinstance(overlay, dict):
        raise ValueError(f"overlay must be a mapping, got {type(overlay).__name__}")

    keys = set(overlay)
    missing = OVERLAY_FIELDS - keys
    if missing:
        raise ValueError(f"overlay is missing required field(s): {sorted(missing)}")
    unknown = keys - OVERLAY_FIELDS
    if unknown:
        raise ValueError(f"overlay has unknown field(s): {sorted(unknown)}")

    for required_section in ("bond_option", "volatility_input", "forward_clean_price_input"):
        if required_section not in base_case:
            raise ValueError(f"base_case is missing required section: {required_section!r}")

    bond_option = {
        **base_case["bond_option"],
        "option_type": overlay["option_type"],
        "position": overlay["position"],
        "strike_price": overlay["strike_price"],
        "notional": overlay["notional"],
    }
    volatility_input = {
        **base_case["volatility_input"],
        "volatility": overlay["volatility"],
    }
    forward_clean_price_input = {
        **base_case["forward_clean_price_input"],
        "forward_clean_price_per_100": overlay["forward_clean_price_per_100"],
    }

    return {
        **base_case,
        "bond_option": bond_option,
        "volatility_input": volatility_input,
        "forward_clean_price_input": forward_clean_price_input,
    }


def extract_standalone_option_case_overlay(case: dict) -> dict:
    """Return the current value of the six overlay fields read off ``case``.

    The inverse projection of :func:`apply_standalone_option_case_overlay`:
    given a full case mapping (typically the base case), return exactly the
    six-key overlay dict describing its current editable values, verbatim --
    no unit conversion, rounding, or relabeling. Used by the HTTP bridge to
    tell a freshly-loaded page what the base case's editable fields already
    are, so the form starts in sync with what will actually be priced.
    """

    if not isinstance(case, dict):
        raise ValueError(f"case must be a mapping, got {type(case).__name__}")

    bond_option = case["bond_option"]
    volatility_input = case["volatility_input"]
    forward_clean_price_input = case["forward_clean_price_input"]

    return {
        "option_type": bond_option["option_type"],
        "position": bond_option["position"],
        "strike_price": bond_option["strike_price"],
        "notional": bond_option["notional"],
        "volatility": volatility_input["volatility"],
        "forward_clean_price_per_100": forward_clean_price_input["forward_clean_price_per_100"],
    }
