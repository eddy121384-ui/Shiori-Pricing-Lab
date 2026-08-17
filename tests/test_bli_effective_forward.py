"""Deterministic tests for the Issue #177 effective-Forward selection.

Covers the whole of ``pricing/bli_effective_forward.py``: the two canonical
Forward source tokens, the derived-by-default / override-wins rule, the
fail-closed refusal when the derived source is active and unavailable, and
the ``BLIForwardCleanPriceInput`` construction the pricing chain actually
reads. No Bloomberg call, no clock, no curve, no QuantLib -- this module is
pure selection over numbers somebody else produced.
"""

from __future__ import annotations

import math

import pytest

from shiori_pricing_lab.data.bli_snapshot import BLIForwardCleanPriceInput
from shiori_pricing_lab.pricing.bli_effective_forward import (
    EFFECTIVE_FORWARD_SOURCES,
    SHIORI_DERIVED_S490_FORWARD_SOURCE,
    TRADER_FORWARD_OVERRIDE_FORWARD_SOURCE,
    ForwardSourceUnavailableError,
    TraderForwardOverrideInvalidError,
    forward_clean_price_input_dict,
    select_effective_forward,
)
from shiori_pricing_lab.products.enums import TreasuryFTPQuoteSide


def test_the_two_canonical_forward_sources_are_exactly_the_published_pair() -> None:
    assert SHIORI_DERIVED_S490_FORWARD_SOURCE == "SHIORI_DERIVED_S490"
    assert TRADER_FORWARD_OVERRIDE_FORWARD_SOURCE == "TRADER_FORWARD_OVERRIDE"
    assert EFFECTIVE_FORWARD_SOURCES == {
        "SHIORI_DERIVED_S490",
        "TRADER_FORWARD_OVERRIDE",
    }


# --- Derived by default ---------------------------------------------------


def test_the_derived_forward_is_the_default_source_when_no_override_is_active() -> None:
    effective = select_effective_forward(
        requested_forward_source=SHIORI_DERIVED_S490_FORWARD_SOURCE,
        shiori_derived_forward_clean_price_per_100=99.25,
    )

    assert effective.forward_source == SHIORI_DERIVED_S490_FORWARD_SOURCE
    assert effective.forward_clean_price_per_100 == 99.25
    assert effective.shiori_derived_forward_clean_price_per_100 == 99.25
    assert effective.trader_forward_override_per_100 is None
    assert effective.shiori_derived_forward_error is None


def test_an_int_derived_forward_is_carried_as_a_float() -> None:
    effective = select_effective_forward(
        requested_forward_source=SHIORI_DERIVED_S490_FORWARD_SOURCE,
        shiori_derived_forward_clean_price_per_100=100,
    )

    assert isinstance(effective.forward_clean_price_per_100, float)
    assert effective.forward_clean_price_per_100 == 100.0


# --- Trader Forward Override wins ----------------------------------------


def test_a_declared_override_wins_over_the_derived_forward_and_still_carries_it() -> None:
    effective = select_effective_forward(
        requested_forward_source=TRADER_FORWARD_OVERRIDE_FORWARD_SOURCE,
        shiori_derived_forward_clean_price_per_100=99.25,
        trader_forward_override_per_100=98.5,
    )

    assert effective.forward_source == TRADER_FORWARD_OVERRIDE_FORWARD_SOURCE
    assert effective.forward_clean_price_per_100 == 98.5
    assert effective.trader_forward_override_per_100 == 98.5
    # The comparison value a trader with an override actually wants: the
    # override does not hide what Shiori would have derived.
    assert effective.shiori_derived_forward_clean_price_per_100 == 99.25


def test_an_override_prices_even_when_the_derivation_failed_and_keeps_the_reason() -> None:
    # Issue #177: "if the trader has explicitly enabled a valid Forward
    # Override, price under the existing override contract" -- a failed
    # derivation is recorded, not raised, in that mode.
    effective = select_effective_forward(
        requested_forward_source=TRADER_FORWARD_OVERRIDE_FORWARD_SOURCE,
        shiori_derived_forward_clean_price_per_100=None,
        trader_forward_override_per_100=98.5,
        shiori_derived_forward_error="BLIBloombergDapiError: Curve #490 unavailable",
    )

    assert effective.forward_source == TRADER_FORWARD_OVERRIDE_FORWARD_SOURCE
    assert effective.forward_clean_price_per_100 == 98.5
    assert effective.shiori_derived_forward_clean_price_per_100 is None
    assert (
        effective.shiori_derived_forward_error
        == "BLIBloombergDapiError: Curve #490 unavailable"
    )


@pytest.mark.parametrize(
    "not_an_override",
    [None, 0, 0.0, -1.0, float("nan"), float("inf"), "98.5", True, False, [], {}],
)
def test_a_declared_override_that_is_not_a_usable_price_is_refused(
    not_an_override: object,
) -> None:
    # Codex P2 review of PR #178: once the run declares TRADER_FORWARD_OVERRIDE,
    # an unusable override is an error on that source's own terms -- it is
    # never silently replaced by the perfectly usable derived Forward, which
    # would quietly change the run's own stated Forward source. ``True`` is
    # finite and positive as an int and is rejected explicitly.
    with pytest.raises(TraderForwardOverrideInvalidError) as excinfo:
        select_effective_forward(
            requested_forward_source=TRADER_FORWARD_OVERRIDE_FORWARD_SOURCE,
            shiori_derived_forward_clean_price_per_100=99.25,
            trader_forward_override_per_100=not_an_override,
        )

    assert "TRADER_FORWARD_OVERRIDE" in str(excinfo.value)
    assert "never" in str(excinfo.value)


@pytest.mark.parametrize(
    "ignored_override",
    [None, 0, 0.0, -1.0, float("nan"), 98.5, "98.5", True],
)
def test_an_override_value_is_ignored_entirely_under_the_derived_source(
    ignored_override: object,
) -> None:
    # In derived mode the number the case carries is the *previous* derived
    # value, replaced on every run -- presence never promotes it to an
    # override.
    effective = select_effective_forward(
        requested_forward_source=SHIORI_DERIVED_S490_FORWARD_SOURCE,
        shiori_derived_forward_clean_price_per_100=99.25,
        trader_forward_override_per_100=ignored_override,
    )

    assert effective.forward_source == SHIORI_DERIVED_S490_FORWARD_SOURCE
    assert effective.forward_clean_price_per_100 == 99.25
    assert effective.trader_forward_override_per_100 is None


@pytest.mark.parametrize("bad_source", ["", None, "MANUAL_TRADER_ENTRY", "shiori_derived_s490"])
def test_an_unrecognized_requested_source_is_never_mapped_onto_a_default(
    bad_source: object,
) -> None:
    with pytest.raises(ValueError, match="requested_forward_source"):
        select_effective_forward(
            requested_forward_source=bad_source,
            shiori_derived_forward_clean_price_per_100=99.25,
        )


# --- Fail closed ----------------------------------------------------------


@pytest.mark.parametrize(
    "unusable_derived",
    [None, 0, 0.0, -1.0, float("nan"), float("inf"), "99.25", True, False],
)
def test_the_derived_source_with_no_usable_derived_forward_fails_closed(
    unusable_derived: object,
) -> None:
    with pytest.raises(ForwardSourceUnavailableError) as excinfo:
        select_effective_forward(
            requested_forward_source=SHIORI_DERIVED_S490_FORWARD_SOURCE,
            shiori_derived_forward_clean_price_per_100=unusable_derived,
        )

    message = str(excinfo.value)
    assert "no effective Forward is available" in message
    # The refusal names every substitute it is refusing to make, so the
    # fail-closed contract is legible from the error itself.
    assert "spot clean price" in message
    assert "zero repo" in message
    assert "flat carry" in message


def test_the_fail_closed_error_preserves_the_derivations_own_reason() -> None:
    # Issue #177: "the Forward source error must keep its real reason" -- it
    # is never replaced by a generic "forward missing".
    with pytest.raises(ForwardSourceUnavailableError) as excinfo:
        select_effective_forward(
            requested_forward_source=SHIORI_DERIVED_S490_FORWARD_SOURCE,
            shiori_derived_forward_clean_price_per_100=None,
            shiori_derived_forward_error=(
                "BLIBondMaturityCashflowUnsupportedError: the window reaches maturity"
            ),
        )

    assert "BLIBondMaturityCashflowUnsupportedError" in str(excinfo.value)
    assert "the window reaches maturity" in str(excinfo.value)


def test_select_effective_forward_with_no_candidates_at_all_fails_closed() -> None:
    with pytest.raises(ForwardSourceUnavailableError):
        select_effective_forward(
            requested_forward_source=SHIORI_DERIVED_S490_FORWARD_SOURCE
        )


# --- The forward input the pricing chain actually reads -------------------


def test_forward_clean_price_input_dict_stamps_the_derived_source() -> None:
    effective = select_effective_forward(
        requested_forward_source=SHIORI_DERIVED_S490_FORWARD_SOURCE,
        shiori_derived_forward_clean_price_per_100=99.25,
    )

    payload = forward_clean_price_input_dict(effective, quote_side="MID")

    assert payload == {
        "forward_clean_price_per_100": 99.25,
        "quote_side": TreasuryFTPQuoteSide.MID,
        "source_system": SHIORI_DERIVED_S490_FORWARD_SOURCE,
        "status": "ACTIVE",
    }
    # Round-trips through the reviewed typed contract unchanged -- this is the
    # same object the case envelope parser builds, not a parallel shape.
    assert BLIForwardCleanPriceInput(**payload).forward_clean_price_per_100 == 99.25


def test_forward_clean_price_input_dict_stamps_the_override_source() -> None:
    effective = select_effective_forward(
        requested_forward_source=TRADER_FORWARD_OVERRIDE_FORWARD_SOURCE,
        shiori_derived_forward_clean_price_per_100=99.25,
        trader_forward_override_per_100=98.5,
    )

    payload = forward_clean_price_input_dict(effective, quote_side="BID")

    assert payload["source_system"] == TRADER_FORWARD_OVERRIDE_FORWARD_SOURCE
    assert payload["forward_clean_price_per_100"] == 98.5
    assert payload["quote_side"] == TreasuryFTPQuoteSide.BID


def test_forward_clean_price_input_dict_defers_quote_side_validation_to_the_typed_contract() -> (
    None
):
    effective = select_effective_forward(
        requested_forward_source=SHIORI_DERIVED_S490_FORWARD_SOURCE,
        shiori_derived_forward_clean_price_per_100=99.25,
    )

    with pytest.raises(ValueError):
        forward_clean_price_input_dict(effective, quote_side="NOT_A_SIDE")


def test_a_derived_forward_that_is_finite_and_positive_is_never_rounded() -> None:
    # The effective Forward is carried at full precision: the number the
    # trader sees in the ticket has to be the number Black-76 prices from.
    derived = 100.09895833333334
    effective = select_effective_forward(
        requested_forward_source=SHIORI_DERIVED_S490_FORWARD_SOURCE,
        shiori_derived_forward_clean_price_per_100=derived,
    )

    assert effective.forward_clean_price_per_100 == derived
    assert math.isclose(
        forward_clean_price_input_dict(effective, quote_side="MID")[
            "forward_clean_price_per_100"
        ],
        derived,
        rel_tol=0.0,
        abs_tol=0.0,
    )
