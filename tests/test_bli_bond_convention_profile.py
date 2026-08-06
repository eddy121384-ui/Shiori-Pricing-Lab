"""Tests for `pricing/bli_bond_convention_profile.py` (Issue #161).

Two things are pinned here, and they are the two halves of requirement A:

1. **The registry is the single source of truth for a selection.** A profile
   is looked up by name, never defaulted or inferred; an unregistered name is
   a clear error rather than a silent fallback; and a profile's provenance
   label is derived from its own name so the two cannot drift.

2. **Suggestion is registry narrowing, never issuer classification.** Shiori
   suggests a profile only when the bond's Bloomberg-confirmed currency and
   coupon frequency fit exactly one registered profile. Two candidates means
   the trader is asked for a *profile* -- which is Issue #161's whole point,
   because the alternative it replaces is asking for eight hand-typed
   technical fields.

The UST profile's own constants are pinned too: PR #162 passed real Bloomberg
workstation UAT with those exact values, and the refactor that moved them
here must not have changed one of them.

**The synthetic profiles below assert nothing about any real market.** They
exist to prove the *mechanism* reads the profile record rather than a
hardcoded constant. Registering a real market's profile needs that market's
conventions confirmed by Bloomberg evidence, an existing reviewed document,
or Eddy -- which is exactly why `CONVENTION_PROFILES` still holds only UST.
"""

from __future__ import annotations

import inspect

import pytest

from shiori_pricing_lab.pricing.bli_bond_convention_profile import (
    CALENDAR_US_GOVERNMENT_BOND,
    CONVENTION_PROFILES,
    SUPPORTED_CONVENTION_PROFILE_NAMES,
    UST_CONVENTION_PROFILE,
    BLIConventionProfile,
    get_convention_profile,
    suggest_convention_profile,
)
from shiori_pricing_lab.products.enums import Currency, DayCount, Frequency
from shiori_pricing_lab.reference_data.enums import BondStatus, BondType


def _synthetic_profile(**overrides) -> BLIConventionProfile:
    """A test-double profile. Its values are not any real market's conventions."""

    kwargs = {
        "name": "SYNTHETIC_TEST",
        "currency": Currency.EUR,
        "coupon_frequencies": (Frequency.ANNUAL,),
        "day_count": DayCount.THIRTY_360,
        "day_count_evidence": "SYNTHETIC/TEST",
        "bond_type": BondType.FIXED_COUPON_BULLET,
        "ex_dividend_days": 3,
        "status": BondStatus.ACTIVE,
        "settlement_business_days": 2,
        "settlement_calendar": CALENDAR_US_GOVERNMENT_BOND,
    }
    kwargs.update(overrides)
    return BLIConventionProfile(**kwargs)


# --- 1. The UST profile's constants survived the refactor unchanged ----------


def test_the_ust_profile_keeps_every_constant_pr_162_shipped():
    """The UAT-passed values, pinned. A change to any one of them changes what
    a real Treasury prices at, so it must never happen as a side effect of
    moving them into a profile record."""

    assert UST_CONVENTION_PROFILE.name == "UST"
    assert UST_CONVENTION_PROFILE.currency is Currency.USD
    assert UST_CONVENTION_PROFILE.coupon_frequencies == (Frequency.SEMI_ANNUAL,)
    assert UST_CONVENTION_PROFILE.day_count is DayCount.ACT_ACT_BOND
    assert UST_CONVENTION_PROFILE.day_count_evidence == "ACT/ACT"
    assert UST_CONVENTION_PROFILE.bond_type is BondType.FIXED_COUPON_BULLET
    assert UST_CONVENTION_PROFILE.ex_dividend_days == 0
    assert UST_CONVENTION_PROFILE.status is BondStatus.ACTIVE
    assert UST_CONVENTION_PROFILE.settlement_business_days == 1
    assert UST_CONVENTION_PROFILE.settlement_calendar == CALENDAR_US_GOVERNMENT_BOND


def test_the_ust_day_count_is_bond_basis_not_isda():
    """The PR #162 correction, still true after the move: ACT/ACT ISDA and
    ACT/ACT Bond are different accrual rules, and ISDA here would misprice
    accrued interest on every UST this profile applies to."""

    assert UST_CONVENTION_PROFILE.day_count is not DayCount.ACT_ACT_ISDA


def test_the_ust_calendar_is_quantlibs_own_us_government_bond_market():
    """Reused verbatim -- this repo writes no holiday table of its own."""

    assert UST_CONVENTION_PROFILE.calendar().name() == "US government bond market"


# --- 2. Selection: looked up, never defaulted --------------------------------


def test_a_registered_profile_is_returned_by_name():
    assert get_convention_profile("UST") is UST_CONVENTION_PROFILE


@pytest.mark.parametrize("bad", [None, "", "   ", 0, 1, [], {}, object()])
def test_a_missing_or_blank_selection_is_an_error_never_a_default(bad):
    with pytest.raises(ValueError) as excinfo:
        get_convention_profile(bad)
    assert "never silently falls back" in str(excinfo.value)


@pytest.mark.parametrize("unknown", ["ust", "UST ", "GILT", "US_CORPORATE", "GERMAN_GOVT"])
def test_an_unregistered_selection_is_an_error_never_a_default(unknown):
    """Including the two profiles Issue #161 asks for but that are not
    registered yet: until their conventions are confirmed, selecting one must
    fail loudly rather than quietly resolve as something else."""

    with pytest.raises(ValueError) as excinfo:
        get_convention_profile(unknown)
    assert "never silently falls back" in str(excinfo.value)


def test_the_provenance_label_is_derived_from_the_profiles_own_name():
    """So a profile's tier label and its selection token cannot drift apart."""

    assert UST_CONVENTION_PROFILE.default_provenance == "UST_PROFILE_DEFAULT"
    assert _synthetic_profile(name="ANOTHER").default_provenance == "ANOTHER_PROFILE_DEFAULT"


def test_the_supported_names_tuple_matches_the_registry():
    assert SUPPORTED_CONVENTION_PROFILE_NAMES == tuple(CONVENTION_PROFILES)
    for name, profile in CONVENTION_PROFILES.items():
        assert profile.name == name


# --- 3. A profile record cannot be registered in a broken state --------------


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"name": "  "}, "non-blank"),
        ({"coupon_frequencies": ()}, "at least one coupon frequency"),
        ({"ex_dividend_days": True}, "ex_dividend_days must be an int"),
        ({"ex_dividend_days": 1.5}, "ex_dividend_days must be an int"),
        ({"ex_dividend_days": -1}, "non-negative"),
        ({"settlement_business_days": 0}, "must be positive"),
        ({"settlement_calendar": "MADE_UP"}, "not one of the reviewed"),
    ],
)
def test_a_malformed_profile_is_rejected_at_construction(overrides, fragment):
    with pytest.raises(ValueError) as excinfo:
        _synthetic_profile(**overrides)
    assert fragment in str(excinfo.value)


# --- 4. Suggestion: narrowing, never classification --------------------------


def test_suggestion_reads_no_identifier_of_any_kind():
    """Structural proof of the rule Eddy set on Issue #157 P1-1 and #161
    restates: no ISIN, no CUSIP, no security name may reach this function, so
    it cannot guess an issuer even by accident."""

    parameters = set(inspect.signature(suggest_convention_profile).parameters)
    assert parameters == {"currency", "bond_master"}

    # The executable body only: the docstring legitimately *names* the three
    # identifiers precisely to say they are never read, and the returned
    # reason string says the same thing to the trader.
    source = inspect.getsource(suggest_convention_profile)
    body = source.split('"""')[2]
    executable = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("#")
    ).lower()
    for forbidden in ("isin", "cusip", "security_name"):
        # `reasons` is trader-facing prose, not a read of the identifier.
        assert forbidden not in executable.split("reasons=(")[0]


def test_a_bond_fitting_exactly_one_registered_profile_is_suggested():
    suggestion = suggest_convention_profile(
        currency="USD", bond_master={"coupon_frequency": "SEMI_ANNUAL"}
    )
    assert suggestion.suggested == "UST"
    assert suggestion.candidates == ("UST",)
    assert suggestion.reasons == ()


@pytest.mark.parametrize(
    "currency, coupon_frequency",
    [
        ("GBP", "SEMI_ANNUAL"),  # no profile states conventions for GBP
        ("EUR", "ANNUAL"),  # nor for EUR -- GERMAN_GOVT is not registered yet
        ("USD", "ANNUAL"),  # the UST profile states semi-annual only
        ("USD", None),  # Bloomberg confirmed no coupon frequency at all
    ],
)
def test_a_bond_no_registered_profile_covers_is_never_suggested_one(currency, coupon_frequency):
    """The fail-closed half: a bond outside every registered profile gets no
    suggestion and no candidates, so the page refuses it rather than offering
    a selection that could not help."""

    suggestion = suggest_convention_profile(
        currency=currency, bond_master={"coupon_frequency": coupon_frequency}
    )
    assert suggestion.suggested is None
    assert suggestion.candidates == ()
    assert len(suggestion.reasons) == 1
    assert repr(currency) in suggestion.reasons[0]


def test_two_fitting_profiles_ask_the_trader_rather_than_guessing(monkeypatch):
    """Issue #161 requirement D's decisive case, and the reason suggestion is
    narrowing rather than classification.

    The moment a second USD profile is registered, a USD semi-annual bond
    fits both -- and Bloomberg's confirmed currency says nothing about which
    one applies. Shiori must ask for a profile. It must **not** fall back to
    asking for the eight Advanced technical fields, and it must not break the
    tie from an ISIN, a CUSIP or a name.
    """

    second = _synthetic_profile(
        name="SECOND_USD_TEST",
        currency=Currency.USD,
        coupon_frequencies=(Frequency.SEMI_ANNUAL,),
    )
    monkeypatch.setitem(CONVENTION_PROFILES, second.name, second)

    suggestion = suggest_convention_profile(
        currency="USD", bond_master={"coupon_frequency": "SEMI_ANNUAL"}
    )
    assert suggestion.suggested is None
    assert set(suggestion.candidates) == {"UST", "SECOND_USD_TEST"}
    assert len(suggestion.reasons) == 1
    reason = suggestion.reasons[0]
    assert "UST" in reason and "SECOND_USD_TEST" in reason
    assert "choose the profile" in reason.lower()
    # The refusal to guess is stated in the trader's own terms, and names the
    # three things Shiori will never break the tie with.
    for forbidden in ("isin", "cusip", "name"):
        assert forbidden in reason.lower()


def test_a_missing_bond_master_is_not_an_error():
    """The browser calls this straight off a Bloomberg response that may have
    returned no Bond Master fields at all; that is a no-suggestion answer,
    not a crash."""

    suggestion = suggest_convention_profile(currency="USD", bond_master=None)
    assert suggestion.suggested is None
    assert suggestion.candidates == ()
