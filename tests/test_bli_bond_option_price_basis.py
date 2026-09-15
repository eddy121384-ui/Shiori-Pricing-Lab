"""Pins for the ``BOND_OPTION_PRICE_BASIS`` model convention (Issue #211).

The basis exists because the bank's internal model has historically used
clean bond forward/strike while Numerix used dirty, and that difference was
itself a source of reconciliation breaks. So the two rules worth pinning are
that neither basis is privileged in the vocabulary, and that the value is
never inferred or defaulted inside a calculation.
"""

from __future__ import annotations

import pytest

from shiori_pricing_lab.pricing.bli_bond_option_price_basis import (
    DEFAULT_BOND_OPTION_PRICE_BASIS,
    SUPPORTED_BOND_OPTION_PRICE_BASES,
    BondOptionPriceBasis,
    basis_price_per_100,
    require_bond_option_price_basis,
)


def test_the_vocabulary_is_exactly_dirty_and_clean():
    assert SUPPORTED_BOND_OPTION_PRICE_BASES == ("DIRTY", "CLEAN")
    assert [member.value for member in BondOptionPriceBasis] == ["DIRTY", "CLEAN"]


def test_dirty_is_the_configuration_default():
    # Default because it preserves the approved OVME-aligned standalone
    # behaviour (Issue #94 / PR #122) -- not because it is more correct.
    assert DEFAULT_BOND_OPTION_PRICE_BASIS is BondOptionPriceBasis.DIRTY


def test_the_members_are_named_for_the_price_state_not_for_a_vendor():
    # A model convention, not a vendor switch: naming it after whoever
    # currently picks it would make the next system's arrival a rename.
    for member in BondOptionPriceBasis:
        lowered = member.value.lower()
        for vendor in ("bloomberg", "numerix", "ovme", "bank", "internal", "vendor"):
            assert vendor not in lowered


@pytest.mark.parametrize("basis", list(BondOptionPriceBasis))
def test_an_enum_member_round_trips(basis):
    assert require_bond_option_price_basis(basis) is basis


@pytest.mark.parametrize("raw", ["DIRTY", "CLEAN"])
def test_an_exact_string_is_coerced(raw):
    assert require_bond_option_price_basis(raw) is BondOptionPriceBasis(raw)


@pytest.mark.parametrize(
    "bad", [None, "", "   ", "dirty", "Clean", "GROSS", "NET", 0, 1, True, 101.5]
)
def test_anything_else_fails_closed(bad):
    # Including case variants: a basis is never guessed, and never inferred
    # from a source system, a model name, or a price's magnitude.
    with pytest.raises(ValueError):
        require_bond_option_price_basis(bad)


def test_the_refusal_names_the_field_so_a_caller_can_find_it():
    with pytest.raises(ValueError) as excinfo:
        require_bond_option_price_basis(None, "duration.price_basis")
    assert "duration.price_basis" in str(excinfo.value)


def test_the_basis_selects_the_price_state_and_nothing_else_does():
    clean, accrued = 101.0, 2.5

    assert basis_price_per_100(clean, accrued, BondOptionPriceBasis.CLEAN) == 101.0
    assert basis_price_per_100(clean, accrued, BondOptionPriceBasis.DIRTY) == 103.5


def test_the_two_price_states_coincide_when_accrued_is_zero():
    assert basis_price_per_100(101.0, 0.0, BondOptionPriceBasis.CLEAN) == (
        basis_price_per_100(101.0, 0.0, BondOptionPriceBasis.DIRTY)
    )


def test_selecting_a_price_state_also_fails_closed_on_an_unknown_basis():
    with pytest.raises(ValueError):
        basis_price_per_100(101.0, 2.5, "GROSS")


def test_the_module_documents_the_end_to_end_consistency_invariant():
    # Requirement 4 of the refactor: the forbidden mixed states must be
    # written down where the convention itself lives, because Phase 4/5 will
    # be composed by someone reading this module rather than the issue.
    from pathlib import Path

    import shiori_pricing_lab.pricing.bli_bond_option_price_basis as module

    # Whitespace-normalized: the assertion is about what the module says,
    # not about where its lines happen to wrap today.
    text = " ".join(Path(module.__file__).read_text(encoding="utf-8").split())
    assert "dirty ``F``/``K`` with a clean-derived ``sigma_P``" in text
    assert "clean ``F``/``K`` with a dirty-derived ``sigma_P``" in text
    assert "**not** a change to the Black-76 formula" in text
    assert "must never become a second pricing engine" in text
