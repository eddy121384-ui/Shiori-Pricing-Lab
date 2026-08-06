"""Tests for `pricing/bli_bond_convention_profile.py` (Issue #161).

Three things are pinned here:

1. **The registry is the single source of truth for a selection.** A profile
   is looked up by name, never defaulted or inferred; an unregistered name is
   a clear error rather than a silent fallback; and a profile's provenance
   tier and export `source_system` are both derived from its own name so the
   three cannot drift.

2. **Narrowing is not choosing.** `convention_profile_candidates` removes
   profiles whose stated conventions do not cover this bond's own confirmed
   terms, and stops there. It has no `suggested` field: currency and coupon
   frequency describe a bond's cash flows, not its issuer class, and the
   Bloomberg field that would classify it (`SECURITY_TYP`) is an unprobed
   candidate. The trader selects -- which is Issue #161's whole point,
   because the alternative it replaces is hand-typing eight technical fields.

3. **"Not confirmed" is a real state, not a value to guess.** `US_CORPORATE`
   and `GERMAN_GOVT` carry the four conventions Eddy confirmed from Annex A
   and *no* ex-dividend default, because none was confirmed for either
   market. The record must permit that rather than force an int.

The UST profile's own constants are pinned too: PR #162 passed real Bloomberg
workstation UAT with those exact values, and neither the refactor that moved
them here nor the two profiles registered beside them may change one.
"""

from __future__ import annotations

import inspect

import pytest

from shiori_pricing_lab.pricing.bli_bond_convention_profile import (
    CALENDAR_TARGET,
    CALENDAR_US_SIFMA,
    CONVENTION_PROFILES,
    GERMAN_GOVT_CONVENTION_PROFILE,
    PLAIN_FIXED_COUPON_EVIDENCE_FIELDS,
    SUPPORTED_CONVENTION_PROFILE_NAMES,
    US_CORPORATE_CONVENTION_PROFILE,
    UST_CONVENTION_PROFILE,
    BLIConventionProfile,
    confirms_plain_fixed_coupon_evidence,
    convention_profile_candidates,
    get_convention_profile,
)
from shiori_pricing_lab.products.enums import Currency, DayCount, Frequency
from shiori_pricing_lab.reference_data.enums import BondStatus, BondType


def _synthetic_profile(**overrides) -> BLIConventionProfile:
    """A test-double profile. Its values are not any real market's conventions."""

    kwargs = {
        "name": "SYNTHETIC_TEST",
        "currency": Currency.GBP,
        "coupon_frequencies": (Frequency.QUARTERLY,),
        "day_count": DayCount.ACT_365_FIXED,
        "day_count_evidence": "SYNTHETIC/TEST",
        "bond_type": BondType.FIXED_COUPON_BULLET,
        "ex_dividend_days": 3,
        "status": BondStatus.ACTIVE,
        "settlement_business_days": 2,
        "settlement_calendar": CALENDAR_US_SIFMA,
        "source_system": "SHIORI_SYNTHETIC_TEST_CONVENTION_PROFILE",
    }
    kwargs.update(overrides)
    return BLIConventionProfile(**kwargs)


# --- 1. The UST profile's constants survived the refactor unchanged ----------


def test_the_ust_profile_keeps_every_constant_pr_162_shipped():
    """The UAT-passed values, pinned. A change to any one of them changes what
    a real Treasury prices at, so it must never happen as a side effect of
    moving them into a profile record or of registering profiles beside it."""

    assert UST_CONVENTION_PROFILE.name == "UST"
    assert UST_CONVENTION_PROFILE.currency is Currency.USD
    assert UST_CONVENTION_PROFILE.coupon_frequencies == (Frequency.SEMI_ANNUAL,)
    assert UST_CONVENTION_PROFILE.day_count is DayCount.ACT_ACT_BOND
    assert UST_CONVENTION_PROFILE.day_count_evidence == "ACT/ACT"
    assert UST_CONVENTION_PROFILE.bond_type is BondType.FIXED_COUPON_BULLET
    assert UST_CONVENTION_PROFILE.ex_dividend_days == 0
    assert UST_CONVENTION_PROFILE.status is BondStatus.ACTIVE
    assert UST_CONVENTION_PROFILE.settlement_business_days == 1
    assert UST_CONVENTION_PROFILE.settlement_calendar == CALENDAR_US_SIFMA


def test_the_ust_day_count_is_bond_basis_not_isda():
    """The PR #162 correction, still true after the move: ACT/ACT ISDA and
    ACT/ACT Bond are different accrual rules, and ISDA here would misprice
    accrued interest on every UST this profile applies to."""

    assert UST_CONVENTION_PROFILE.day_count is not DayCount.ACT_ACT_ISDA


def test_only_ust_is_exempt_from_the_plain_fixed_coupon_evidence_gate():
    """Eddy's explicit instruction, in both directions: UST keeps the
    behaviour that passed real workstation UAT, and every profile registered
    beside it must positively establish a bond's structure first."""

    assert UST_CONVENTION_PROFILE.plain_fixed_coupon_evidence_required is False
    for profile in CONVENTION_PROFILES.values():
        if profile is UST_CONVENTION_PROFILE:
            continue
        assert profile.plain_fixed_coupon_evidence_required is True, profile.name


# --- 2. The two profiles Eddy confirmed from Annex A -------------------------


def test_the_us_corporate_profile_matches_the_confirmed_annex_a_conventions():
    profile = US_CORPORATE_CONVENTION_PROFILE

    assert profile.name == "US_CORPORATE"
    assert profile.currency is Currency.USD
    assert profile.coupon_frequencies == (Frequency.SEMI_ANNUAL,)
    assert profile.day_count is DayCount.THIRTY_360
    assert profile.settlement_business_days == 2
    assert profile.settlement_calendar == CALENDAR_US_SIFMA
    assert profile.bond_type is BondType.FIXED_COUPON_BULLET
    assert profile.status is BondStatus.ACTIVE


def test_the_german_govt_profile_matches_the_confirmed_annex_a_conventions():
    profile = GERMAN_GOVT_CONVENTION_PROFILE

    assert profile.name == "GERMAN_GOVT"
    assert profile.currency is Currency.EUR
    assert profile.coupon_frequencies == (Frequency.ANNUAL,)
    assert profile.day_count is DayCount.ACT_ACT_BOND
    assert profile.settlement_business_days == 2
    assert profile.settlement_calendar == CALENDAR_TARGET
    assert profile.bond_type is BondType.FIXED_COUPON_BULLET
    assert profile.status is BondStatus.ACTIVE


def test_neither_new_profile_guesses_an_ex_dividend_default():
    """Eddy confirmed four conventions per market and explicitly did not
    confirm an ex-dividend rule for either. `None` is what "not confirmed"
    looks like; a zero here would be a guess that silently changes accrued
    interest inside an ex-dividend window."""

    assert US_CORPORATE_CONVENTION_PROFILE.ex_dividend_days is None
    assert GERMAN_GOVT_CONVENTION_PROFILE.ex_dividend_days is None
    # UST's confirmed zero is untouched.
    assert UST_CONVENTION_PROFILE.ex_dividend_days == 0


def test_the_new_profiles_day_count_evidence_matches_the_workstation_probe():
    """`day_count_evidence` withholds `day_count` when Bloomberg's own
    DAY_CNT_DES contradicts the profile. Both new markets' strings are now
    Bloomberg workstation evidence (Issue #161 follow-up: US023135EC69 ->
    "30/360", DE000BU2Z072 -> "ACT/ACT"), not a guess -- both happen to agree
    with the confirmed Annex A day count, so they are wired the same way
    UST's is."""

    assert US_CORPORATE_CONVENTION_PROFILE.day_count_evidence == "30/360"
    assert GERMAN_GOVT_CONVENTION_PROFILE.day_count_evidence == "ACT/ACT"
    assert UST_CONVENTION_PROFILE.day_count_evidence == "ACT/ACT"


def test_each_registered_profile_builds_its_own_reviewed_calendar():
    """Reused verbatim from QuantLib -- this repo writes no holiday table of
    its own, partial or otherwise."""

    assert UST_CONVENTION_PROFILE.calendar().name() == "US government bond market"
    # QuantLib 1.43 models the SIFMA-recommended US bond-market schedule as
    # UnitedStates::GovernmentBond and exposes no separate SIFMA market, so
    # US_CORPORATE names the same calendar object rather than a second,
    # silently-different holiday table.
    assert US_CORPORATE_CONVENTION_PROFILE.calendar().name() == "US government bond market"
    assert GERMAN_GOVT_CONVENTION_PROFILE.calendar().name() == "TARGET"


def test_no_two_profiles_share_a_conventions_record():
    """Profile pollution guard (Issue #161 acceptance 4): each profile's
    day count, settlement lag and calendar are its own."""

    assert GERMAN_GOVT_CONVENTION_PROFILE.settlement_calendar != (
        UST_CONVENTION_PROFILE.settlement_calendar
    )
    assert US_CORPORATE_CONVENTION_PROFILE.day_count is not UST_CONVENTION_PROFILE.day_count
    assert US_CORPORATE_CONVENTION_PROFILE.settlement_business_days != (
        UST_CONVENTION_PROFILE.settlement_business_days
    )


# --- 3. Selection: looked up, never defaulted --------------------------------


@pytest.mark.parametrize("name", ["UST", "US_CORPORATE", "GERMAN_GOVT"])
def test_a_registered_profile_is_returned_by_name(name):
    assert get_convention_profile(name).name == name


@pytest.mark.parametrize("bad", [None, "", "   ", 0, 1, [], {}, object()])
def test_a_missing_or_blank_selection_is_an_error_never_a_default(bad):
    with pytest.raises(ValueError) as excinfo:
        get_convention_profile(bad)
    assert "never silently falls back" in str(excinfo.value)


@pytest.mark.parametrize("unknown", ["ust", "UST ", "GILT", "EUR_GOVT", "FRENCH_GOVT"])
def test_an_unregistered_selection_is_an_error_never_a_default(unknown):
    """Including `EUR_GOVT`: Eddy chose `GERMAN_GOVT` precisely so a French or
    Italian government bond gets its own confirmed profile rather than
    quietly borrowing Germany's calendar and day count."""

    with pytest.raises(ValueError) as excinfo:
        get_convention_profile(unknown)
    assert "never silently falls back" in str(excinfo.value)


def test_the_provenance_label_is_derived_from_the_profiles_own_name():
    """So a profile's tier label and its selection token cannot drift apart."""

    assert UST_CONVENTION_PROFILE.default_provenance == "UST_PROFILE_DEFAULT"
    assert GERMAN_GOVT_CONVENTION_PROFILE.default_provenance == "GERMAN_GOVT_PROFILE_DEFAULT"
    assert _synthetic_profile(name="ANOTHER").default_provenance == "ANOTHER_PROFILE_DEFAULT"


def test_the_export_source_system_is_explicit_per_profile_not_derived():
    """The compatibility correction, pinned exactly.

    `source_system` is NOT `f"SHIORI_{name}_CONVENTION_PROFILE"` the way
    `default_provenance` is: that derivation was withdrawn because it would
    have silently changed UST's already-shipped export value (PR #162, real
    Bloomberg workstation UAT) the moment a second profile was registered.
    Every profile states its own value, and UST's is the pre-existing one,
    unchanged."""

    assert UST_CONVENTION_PROFILE.source_system == "SHIORI_UST_FIXED_COUPON_PROFILE"
    assert US_CORPORATE_CONVENTION_PROFILE.source_system == (
        "SHIORI_US_CORPORATE_CONVENTION_PROFILE"
    )
    assert GERMAN_GOVT_CONVENTION_PROFILE.source_system == (
        "SHIORI_GERMAN_GOVT_CONVENTION_PROFILE"
    )
    # None of them follows the `default_provenance` naming pattern for UST --
    # structural proof the two are independent, not the same string reused.
    assert UST_CONVENTION_PROFILE.source_system != "SHIORI_UST_CONVENTION_PROFILE"

    labels = {profile.source_system for profile in CONVENTION_PROFILES.values()}
    assert len(labels) == len(CONVENTION_PROFILES)


def test_the_supported_names_tuple_matches_the_registry():
    assert SUPPORTED_CONVENTION_PROFILE_NAMES == tuple(CONVENTION_PROFILES)
    assert set(SUPPORTED_CONVENTION_PROFILE_NAMES) == {"UST", "US_CORPORATE", "GERMAN_GOVT"}
    for name, profile in CONVENTION_PROFILES.items():
        assert profile.name == name


# --- 4. A profile record cannot be registered in a broken state --------------


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
        ({"source_system": ""}, "explicit, non-blank source_system"),
        ({"source_system": "   "}, "explicit, non-blank source_system"),
    ],
)
def test_a_malformed_profile_is_rejected_at_construction(overrides, fragment):
    with pytest.raises(ValueError) as excinfo:
        _synthetic_profile(**overrides)
    assert fragment in str(excinfo.value)


def test_an_absent_ex_dividend_default_is_accepted_not_an_error():
    """The whole point of item 3: "no approved default for this market" must
    be expressible, not forced into an int."""

    assert _synthetic_profile(ex_dividend_days=None).ex_dividend_days is None


# --- 5. Candidates: narrowing, never choosing --------------------------------


def test_candidates_read_no_identifier_of_any_kind():
    """Structural proof of the rule Eddy set on Issue #157 P1-1 and restated
    on #161: no ISIN, no CUSIP, no security name may reach this function, so
    it cannot guess an issuer even by accident."""

    parameters = set(inspect.signature(convention_profile_candidates).parameters)
    assert parameters == {"currency", "bond_master"}

    # The executable body only: the docstring legitimately *names* the three
    # identifiers precisely to say they are never read, and the returned
    # reason string says the same thing to the trader.
    source = inspect.getsource(convention_profile_candidates)
    body = source.split('"""')[2]
    executable = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("#")
    ).lower()
    for forbidden in ("isin", "cusip", "security_name"):
        assert forbidden not in executable.split("reasons=(")[0]


def test_the_result_carries_no_suggestion_field_at_all():
    """Issue #161 follow-up item 1, structurally. A `suggested` field is not
    merely unset -- it does not exist, so no caller can start reading one
    without this test failing first."""

    result = convention_profile_candidates(
        currency="EUR", bond_master={"coupon_frequency": "ANNUAL"}
    )
    assert not hasattr(result, "suggested")


def test_a_lone_candidate_is_still_never_chosen_for_the_trader():
    """The decisive case. A EUR annual bond leaves exactly one registered
    profile standing -- and Shiori still does not select it, because currency
    and coupon frequency say nothing about whether this is a German
    government bond or some other EUR annual issuer's."""

    result = convention_profile_candidates(
        currency="EUR", bond_master={"coupon_frequency": "ANNUAL"}
    )

    assert result.candidates == ("GERMAN_GOVT",)
    assert len(result.reasons) == 1
    reason = result.reasons[0]
    assert "will not choose between profiles" in reason
    assert "not its issuer class" in reason
    assert "Select the profile that applies" in reason


def test_a_usd_semi_annual_bond_fits_both_usd_profiles():
    """Exactly the ambiguity that makes auto-selection unsafe: a real UST and
    a real US corporate are indistinguishable on confirmed Bloomberg facts."""

    result = convention_profile_candidates(
        currency="USD", bond_master={"coupon_frequency": "SEMI_ANNUAL"}
    )

    assert set(result.candidates) == {"UST", "US_CORPORATE"}
    for forbidden in ("isin", "cusip", "name"):
        assert forbidden in result.reasons[0].lower()


@pytest.mark.parametrize(
    "currency, coupon_frequency",
    [
        ("GBP", "SEMI_ANNUAL"),  # no profile states conventions for GBP
        ("USD", "ANNUAL"),  # both USD profiles state semi-annual only
        ("EUR", "SEMI_ANNUAL"),  # GERMAN_GOVT states annual only
        ("USD", None),  # Bloomberg confirmed no coupon frequency at all
    ],
)
def test_a_bond_no_registered_profile_covers_leaves_nothing_to_select(
    currency, coupon_frequency
):
    """The fail-closed half: no candidates means the browser refuses rather
    than offering a selection that could not help."""

    result = convention_profile_candidates(
        currency=currency, bond_master={"coupon_frequency": coupon_frequency}
    )
    assert result.candidates == ()
    assert len(result.reasons) == 1
    assert repr(currency) in result.reasons[0]
    assert "no profile to select" in result.reasons[0]


def test_a_missing_bond_master_is_not_an_error():
    """The browser calls this straight off a Bloomberg response that may have
    returned no Bond Master fields at all; that is a no-candidate answer, not
    a crash."""

    result = convention_profile_candidates(currency="USD", bond_master=None)
    assert result.candidates == ()


# =============================================================================
# Issue #161 follow-up: confirms_plain_fixed_coupon_evidence, unit-level
# =============================================================================
#
# The Bloomberg workstation evidence log next to PLAIN_FIXED_COUPON_EVIDENCE_
# FIELDS above records exactly what was probed. These tests pin the
# resulting predicate: a *value* check, not a presence check, and a hard
# "no" for the two fields with no approved criterion at all.


@pytest.mark.parametrize(
    "field, confirming_value",
    [
        ("coupon_type", "FIXED"),
        ("inflation_linked_flag", False),
        ("convertible_flag", False),
    ],
)
def test_the_confirmed_value_positively_confirms_its_field(field, confirming_value):
    assert confirms_plain_fixed_coupon_evidence(field, confirming_value) is True


@pytest.mark.parametrize(
    "field, non_confirming_value",
    [
        ("coupon_type", "FLOATING"),
        ("coupon_type", "VARIABLE"),
        ("coupon_type", None),
        ("coupon_type", "fixed"),  # not normalized/case-folded, same discipline as Y/N flags
        ("inflation_linked_flag", True),
        ("inflation_linked_flag", None),
        ("inflation_linked_flag", "N"),  # the raw string, not the transformed bool
        ("convertible_flag", True),
        ("convertible_flag", None),
    ],
)
def test_a_present_but_wrong_value_does_not_confirm(field, non_confirming_value):
    """The exact bug a presence-only check ('is it not None?') would miss: a
    real floater's Bloomberg response has a non-None `coupon_type`, and it
    must still fail to confirm a plain fixed-coupon bullet."""

    assert confirms_plain_fixed_coupon_evidence(field, non_confirming_value) is False


@pytest.mark.parametrize("field", ["security_type", "amortizing_flag"])
@pytest.mark.parametrize(
    "value",
    [None, "US GOVERNMENT", "GLOBAL", "EURO-ZONE", True, False, 0, 1, "ANYTHING"],
)
def test_fields_with_no_approved_criterion_never_confirm_any_value(field, value):
    """`security_type` is confirmed to return a value (Bloomberg workstation
    evidence: "US GOVERNMENT" / "GLOBAL" / "EURO-ZONE") but nothing has
    established which values would mean "plain bullet" -- and no working
    amortizing-evidence mnemonic exists at all. Both therefore reject every
    value, not just absence, until a real criterion is approved."""

    assert confirms_plain_fixed_coupon_evidence(field, value) is False


def test_an_unrecognized_field_name_never_confirms():
    assert confirms_plain_fixed_coupon_evidence("not_a_real_field", "FIXED") is False


def test_every_evidence_field_is_covered_by_the_predicate():
    """Structural completeness: the predicate has an opinion (True/False,
    never an exception) for every field the resolver actually iterates."""

    for field in PLAIN_FIXED_COUPON_EVIDENCE_FIELDS:
        assert confirms_plain_fixed_coupon_evidence(field, "anything") in (True, False)
