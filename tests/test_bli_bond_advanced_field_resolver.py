"""Tests for `pricing/bli_bond_advanced_field_resolver.py` (Issues #157, #161).

Covers the four-tier provenance contract, the eight resolved field values,
the required `convention_profile` browser-state input (Issue #157 P1-1
correction: never inferred, defaulted, or fabricated -- missing/blank/unknown
is a clear `ValueError`, never a silent fallback to "UST"), the fail-closed
product-shape gate (currency, callable/sinkable, zero coupon, coupon
frequency, maturity -- deliberately *not* an issuer-identity check; a
non-Treasury-looking bond is admitted when its shape fits and "UST" is
selected), the whole-profile refusal of an irregular coupon grid, the
expiry-dependent settlement dates and their U.S. government-bond business-day
roll, and the module boundaries that keep this resolver out of every pricing,
curve, discounting, volatility and Greek path.

**Issue #161** adds the field-level contract these tests now pin: a real UST
returning `MTY_TYP = NORMAL` must be admitted (the whole-profile
description-string gate is gone), and a field the resolver cannot fill comes
back in `unresolved_fields` while every other field keeps its resolved value.
The two remaining kinds of refusal are kept apart deliberately, because the
browser renders them differently: `rejection_reasons` is a product/schedule
refusal no trader override can repair, `unresolved_fields` is a per-field
blocker an Advanced override genuinely fixes.

The derived `last_coupon_date` is not merely asserted against a literal: it
is fed back into the reviewed coupon adapter through a real
`BLIStandaloneBondReferenceData`, which is exactly the consumer that rejects
a wrong one.

**Issue #161 requirement A** splits this module in two: the common resolver
tested here, and the convention profiles in `bli_bond_convention_profile.py`
(tested in `test_bli_bond_convention_profile.py`). Everything above the
"market-agnostic" section at the end of this file is the UST regression that
must not move -- PR #162 passed real Bloomberg workstation UAT with exactly
these values. The section at the end pins the other half: that the resolver
reads every market-varying value from the selected profile record rather than
from a UST constant, which is what lets a second market be a data change
instead of a second resolver.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import fields as dataclass_fields
from datetime import date

import pytest

from shiori_pricing_lab.data.bli_standalone_contract import BLIStandaloneBondReferenceData
from shiori_pricing_lab.pricing import bli_bond_advanced_field_resolver as profile_module
from shiori_pricing_lab.pricing.bli_bond_advanced_field_resolver import (
    ADVANCED_FIELD_PATHS,
    EXPIRY_DEPENDENT_FIELD_PATHS,
    PATH_BOND_TYPE,
    PATH_DAY_COUNT,
    PATH_EX_DIVIDEND_DAYS,
    PATH_FORWARD_SETTLEMENT_DATE,
    PATH_LAST_COUPON_DATE,
    PATH_OPTION_SETTLEMENT_DATE,
    PATH_REPORTING_DATE,
    PATH_STATUS,
    PROVENANCE_BLOOMBERG_AUTO,
    PROVENANCE_SHIORI_DERIVED,
    BLIBondAdvancedFieldProfile,
    advance_settlement_business_days,
    resolve_bond_advanced_field_profile,
)
from shiori_pricing_lab.pricing.bli_bond_convention_profile import (
    CALENDAR_US_SIFMA,
    PLAIN_FIXED_COUPON_EVIDENCE_FIELDS,
    UST_CONVENTION_PROFILE,
    BLIConventionProfile,
)
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import (
    BLIBondScheduleError,
    BLIQuantLibNotAvailableError,
    accrued_interest_per_100,
    derive_last_coupon_date,
)
from shiori_pricing_lab.products.enums import Currency, DayCount, Frequency
from shiori_pricing_lab.reference_data.enums import BondStatus, BondType

# A UST-shaped fixture: a regular semi-annual grid from 2024-01-31 to
# 2031-01-31 (14 whole periods). The numbers are synthetic test values, not a
# claim about any real security's terms.
_TREASURY_BOND_MASTER = {
    "coupon": 0.0375,
    "coupon_frequency": "SEMI_ANNUAL",
    "issue_date": "2024-01-31",
    "maturity_date": "2031-01-31",
    "first_coupon_date": "2024-07-31",
    "callable_flag": False,
    "sinkable_flag": False,
    "day_count": None,
    "bond_type": None,
    "last_coupon_date": None,
}

# Exactly the three confirmed display-only description strings the profile
# requires as a necessary condition. They are never mapped into a typed value.
_TREASURY_BOND_MASTER_RAW = {
    "day_count": "ACT/ACT",
    "maturity_type": "AT MATURITY",
    "calc_type": "STREET CONVENTION",
}

_ISIN = "US91282CLJ89"
_VALUATION_DATE = "2026-07-20"
_EXPIRY_DATE = "2026-10-20"  # a Tuesday


def _resolve(**overrides):
    kwargs = {
        "convention_profile": UST_CONVENTION_PROFILE.name,
        "isin": _ISIN,
        "currency": "USD",
        "bond_master": dict(_TREASURY_BOND_MASTER),
        "bond_master_raw": dict(_TREASURY_BOND_MASTER_RAW),
        "valuation_date": _VALUATION_DATE,
        "expiry_date": _EXPIRY_DATE,
    }
    kwargs.update(overrides)
    return resolve_bond_advanced_field_profile(**kwargs)


def _values(profile) -> dict:
    return {field.path: field.value for field in profile.fields}


def _provenance(profile) -> dict:
    return {field.path: field.provenance for field in profile.fields}


# --- 1. The supported case: all eight fields, each with its own tier ---------


def test_supported_ust_resolves_every_advanced_field():
    profile = _resolve()

    assert profile.supported is True
    assert profile.convention_profile == "UST"
    assert profile.rejection_reasons == ()
    assert profile.pending_field_paths == ()
    assert profile.unresolved_fields == ()
    assert tuple(field.path for field in profile.fields) == ADVANCED_FIELD_PATHS


def test_supported_ust_field_values_come_from_the_approved_profile():
    values = _values(_resolve())

    # The distinct US Treasury bond-basis convention, not ISDA's Actual/Actual
    # (Issue #157 correction) -- see the module docstring's day-count section.
    assert values[PATH_DAY_COUNT] == "ACT_ACT_BOND"
    assert values[PATH_BOND_TYPE] == "FIXED_COUPON_BULLET"
    assert values[PATH_EX_DIVIDEND_DAYS] == 0
    assert values[PATH_STATUS] == "ACTIVE"
    # The final scheduled coupon before maturity, not a mid-life "previous"
    # coupon relative to the valuation date -- see the module docstring.
    assert values[PATH_LAST_COUPON_DATE] == "2030-07-31"
    assert values[PATH_REPORTING_DATE] == _VALUATION_DATE
    assert values[PATH_FORWARD_SETTLEMENT_DATE] == "2026-10-21"
    assert values[PATH_OPTION_SETTLEMENT_DATE] == "2026-10-21"


def test_every_field_declares_which_tier_it_came_from():
    provenance = _provenance(_resolve())

    assert provenance[PATH_DAY_COUNT] == UST_CONVENTION_PROFILE.default_provenance
    assert provenance[PATH_BOND_TYPE] == UST_CONVENTION_PROFILE.default_provenance
    assert provenance[PATH_EX_DIVIDEND_DAYS] == UST_CONVENTION_PROFILE.default_provenance
    assert provenance[PATH_STATUS] == UST_CONVENTION_PROFILE.default_provenance
    assert provenance[PATH_LAST_COUPON_DATE] == PROVENANCE_SHIORI_DERIVED
    assert provenance[PATH_REPORTING_DATE] == PROVENANCE_SHIORI_DERIVED
    assert provenance[PATH_FORWARD_SETTLEMENT_DATE] == PROVENANCE_SHIORI_DERIVED
    assert provenance[PATH_OPTION_SETTLEMENT_DATE] == PROVENANCE_SHIORI_DERIVED
    # No value is ever emitted without one.
    assert all(field.provenance for field in _resolve().fields)


def test_a_confirmed_typed_bloomberg_value_outranks_the_profile_default():
    """The BLOOMBERG_AUTO tier is real, not decorative: if a Bond Master
    *destination* field ever carries a typed value, it wins -- and it does so
    regardless of convention_profile, since a confirmed external fact is not
    a profile-owned default (see the module docstring)."""

    profile = _resolve(
        bond_master={
            **_TREASURY_BOND_MASTER,
            "day_count": "ACT_365_FIXED",
            "bond_type": "AMORTIZING",
            "last_coupon_date": "2030-07-31",
        }
    )
    values = _values(profile)
    provenance = _provenance(profile)

    assert values[PATH_DAY_COUNT] == "ACT_365_FIXED"
    assert values[PATH_BOND_TYPE] == "AMORTIZING"
    assert values[PATH_LAST_COUPON_DATE] == "2030-07-31"
    for path in (PATH_DAY_COUNT, PATH_BOND_TYPE, PATH_LAST_COUPON_DATE):
        assert provenance[path] == PROVENANCE_BLOOMBERG_AUTO
    # Fields that are not Bond Master destination fields have no Bloomberg
    # tier at all, so they stay on the profile default.
    assert provenance[PATH_EX_DIVIDEND_DAYS] == UST_CONVENTION_PROFILE.default_provenance
    assert provenance[PATH_STATUS] == UST_CONVENTION_PROFILE.default_provenance


def test_a_mismatched_confirmed_last_coupon_date_rejects_the_whole_profile():
    profile = _resolve(
        bond_master={**_TREASURY_BOND_MASTER, "last_coupon_date": "2030-01-31"}
    )

    assert profile.supported is False
    assert profile.fields == ()
    assert profile.unresolved_fields == ()
    assert "regular coupon schedules only" in profile.rejection_reasons[0]
    assert "editing last_coupon_date cannot repair" in profile.rejection_reasons[0]


def test_a_bloomberg_description_string_never_becomes_a_typed_value():
    """`bond_master_raw` gates admission and nothing else: 'ACT/ACT' does not
    produce the day count, the approved profile constant does."""

    profile = _resolve(bond_master_raw={**_TREASURY_BOND_MASTER_RAW, "day_count": "ACT/ACT"})
    day_count = next(f for f in profile.fields if f.path == PATH_DAY_COUNT)
    assert day_count.provenance == UST_CONVENTION_PROFILE.default_provenance
    assert day_count.value != "ACT/ACT"


# --- 2. The derived last coupon date is accepted by the reviewed adapter ----


def test_derived_last_coupon_date_is_accepted_by_the_reviewed_coupon_adapter():
    """The strongest available check: build the real typed reference record
    with the derived value and run the reviewed accrued-interest path, which
    raises `BLIBondScheduleError` for a `last_coupon_date` off the grid."""

    values = _values(_resolve())
    bond = BLIStandaloneBondReferenceData(
        isin=_ISIN,
        issuer="Synthetic UST-shaped Test Issuer",
        currency="USD",
        coupon=_TREASURY_BOND_MASTER["coupon"],
        coupon_frequency=_TREASURY_BOND_MASTER["coupon_frequency"],
        maturity_date=_TREASURY_BOND_MASTER["maturity_date"],
        issue_date=_TREASURY_BOND_MASTER["issue_date"],
        day_count=values[PATH_DAY_COUNT],
        callable_flag=False,
        sinkable_flag=False,
        bond_type=values[PATH_BOND_TYPE],
        ex_dividend_days=values[PATH_EX_DIVIDEND_DAYS],
        first_coupon_date=_TREASURY_BOND_MASTER["first_coupon_date"],
        last_coupon_date=values[PATH_LAST_COUPON_DATE],
        status=values[PATH_STATUS],
    )

    accrued = accrued_interest_per_100(bond, as_of_date=_VALUATION_DATE)
    assert accrued > 0


def test_derive_last_coupon_date_handles_an_end_of_month_schedule():
    # 2026-02-28 is not a month end in a leap-adjacent sense; use a genuine
    # EOM pair whose day-of-month arithmetic cannot work (no "February 31st").
    assert (
        derive_last_coupon_date(
            issue_date="2024-01-31",
            maturity_date="2027-01-31",
            first_coupon_date="2024-04-30",
            coupon_frequency="QUARTERLY",
        )
        == "2026-10-31"
    )


def test_derive_last_coupon_date_refuses_an_irregular_grid():
    with pytest.raises(BLIBondScheduleError):
        derive_last_coupon_date(
            issue_date="2018-06-05",
            maturity_date="2028-10-22",
            first_coupon_date="2018-10-22",
            coupon_frequency="SEMI_ANNUAL",
        )


def test_derive_last_coupon_date_refuses_a_single_period_bond():
    with pytest.raises(BLIBondScheduleError):
        derive_last_coupon_date(
            issue_date="2026-01-31",
            maturity_date="2026-07-31",
            first_coupon_date="2026-07-31",
            coupon_frequency="SEMI_ANNUAL",
        )


# --- 3. Settlement dates: expiry + 1 U.S. government-bond business day ------


def test_settlement_dates_are_pending_until_an_expiry_exists():
    profile = _resolve(expiry_date=None)

    assert profile.supported is True
    assert profile.pending_field_paths == EXPIRY_DEPENDENT_FIELD_PATHS
    assert profile.unresolved_fields == ()
    # Six of eight are still resolved, so the ordinary workflow is unblocked
    # immediately after the Bloomberg load.
    assert len(profile.fields) == 6
    assert PATH_FORWARD_SETTLEMENT_DATE not in _values(profile)
    assert PATH_OPTION_SETTLEMENT_DATE not in _values(profile)


@pytest.mark.parametrize(
    ("expiry", "expected"),
    [
        ("2026-10-20", "2026-10-21"),  # Tuesday -> Wednesday
        ("2026-10-16", "2026-10-19"),  # Friday -> Monday
        ("2026-10-17", "2026-10-19"),  # Saturday -> Monday
        ("2026-11-10", "2026-11-12"),  # day before Veterans Day -> Thursday
        ("2026-12-24", "2026-12-28"),  # Christmas Day + weekend
    ],
)
def test_settlement_dates_roll_on_the_us_government_bond_calendar(expiry, expected):
    values = _values(_resolve(expiry_date=expiry))
    assert values[PATH_FORWARD_SETTLEMENT_DATE] == expected
    assert values[PATH_OPTION_SETTLEMENT_DATE] == expected


def test_the_calendar_is_quantlibs_own_us_government_bond_market():
    assert UST_CONVENTION_PROFILE.calendar().name() == "US government bond market"


def test_advancing_requires_a_positive_business_day_count():
    for bad in (0, -1, 1.0, True):
        with pytest.raises(ValueError):
            advance_settlement_business_days(date(2026, 10, 20), bad, UST_CONVENTION_PROFILE)


# --- 4. convention_profile: required browser-state input (Issue #157 P1-1) --
#
# convention_profile is never inferred, defaulted, or fabricated by this
# module -- a missing, blank, or unrecognized selection is a clear ValueError,
# never a silent fallback to "UST".


def test_missing_convention_profile_raises_clearly():
    with pytest.raises(ValueError, match="convention_profile"):
        _resolve(convention_profile=None)


def test_blank_convention_profile_raises_clearly():
    with pytest.raises(ValueError, match="convention_profile"):
        _resolve(convention_profile="")


def test_unknown_convention_profile_raises_clearly_rather_than_falling_back_to_ust():
    with pytest.raises(ValueError, match="convention_profile") as exc_info:
        _resolve(convention_profile="GILT")
    # The rejection names the actual unsupported value, and never silently
    # substitutes "UST" for it anywhere in the message or the outcome.
    assert "GILT" in str(exc_info.value)


def test_convention_profile_is_echoed_back_on_every_outcome():
    """The response always confirms which profile it was actually resolved
    against -- both on a supported and an unsupported outcome."""

    assert _resolve().convention_profile == "UST"
    assert _resolve(currency="GBP").convention_profile == "UST"


def test_no_treasury_identity_claim_exists_anywhere_in_the_result_shape():
    """Issue #157 P1-1, second correction: this module must never assert
    that it has verified an issuer's identity. Structural proof: no field on
    the result dataclass even names such a concept."""

    field_names = {f.name for f in dataclass_fields(BLIBondAdvancedFieldProfile)}
    for forbidden in ("identity", "issuer_classification", "treasury_verified", "is_treasury"):
        assert forbidden not in field_names
    assert field_names == {
        "supported",
        "convention_profile",
        "rejection_reasons",
        "fields",
        "pending_field_paths",
        "unresolved_fields",
    }


# --- 5. Product-shape gate: never an issuer-identity check ------------------
#
# A USD, non-callable, non-sinkable, positive semi-annual fixed-coupon bond
# is admitted whenever "UST" is the selected profile, regardless of whether
# it actually is a Treasury -- that is the accepted design (see the module
# docstring), not an oversight. These tests prove the gate is shape-only.


def test_a_shape_compatible_bond_is_admitted_even_when_not_a_treasury_issuer():
    """The positive regression the correction asked for: nothing about ISIN
    or CUSIP is checked at all -- a bond whose ISIN carries no US country
    prefix, and which is not any real Treasury's identifier, still gets the
    UST profile because its shape fits and "UST" was explicitly selected."""

    profile = _resolve(isin="XS0999999999")  # not a US-prefixed ISIN at all
    assert profile.supported is True
    assert profile.rejection_reasons == ()
    assert len(profile.fields) == 8


def test_rejection_reasons_never_mention_isin_country_or_cusip():
    """Every remaining product-shape rejection reason is about the bond's own
    terms, never about the ISIN's country prefix or a CUSIP issuer block --
    the withdrawn identity checks are gone, not merely relabeled."""

    profile = _resolve(
        isin="XS0999999999",
        currency="GBP",
        bond_master={**_TREASURY_BOND_MASTER, "callable_flag": True},
    )
    assert profile.supported is False
    for reason in profile.rejection_reasons:
        assert "isin" not in reason.lower()
        assert "cusip" not in reason.lower()
        assert "issuer" not in reason.lower()


def test_a_non_usd_bond_is_refused():
    _assert_refused(_resolve(currency="GBP"), expected_fragment="is not USD")


def test_a_callable_bond_is_refused():
    _assert_refused(
        _resolve(bond_master={**_TREASURY_BOND_MASTER, "callable_flag": True}),
        expected_fragment="callable_flag",
    )


def test_a_sinkable_bond_is_refused():
    _assert_refused(
        _resolve(bond_master={**_TREASURY_BOND_MASTER, "sinkable_flag": True}),
        expected_fragment="sinkable_flag",
    )


def test_an_unknown_callable_flag_is_refused_rather_than_assumed_false():
    _assert_refused(
        _resolve(bond_master={**_TREASURY_BOND_MASTER, "callable_flag": None}),
        expected_fragment="callable_flag",
    )


def test_a_zero_coupon_bond_is_refused():
    _assert_refused(
        _resolve(bond_master={**_TREASURY_BOND_MASTER, "coupon": 0.0}),
        expected_fragment="zero-coupon",
    )


def test_a_non_semi_annual_bond_is_refused():
    _assert_refused(
        _resolve(bond_master={**_TREASURY_BOND_MASTER, "coupon_frequency": "ANNUAL"}),
        expected_fragment="coupon_frequency",
    )


def test_a_matured_bond_is_never_pre_filled_as_active():
    _assert_refused(
        _resolve(valuation_date="2031-02-01"), expected_fragment="is not after valuation_date"
    )


def test_a_bond_maturing_on_the_valuation_date_is_refused_too():
    _assert_refused(
        _resolve(valuation_date="2031-01-31"), expected_fragment="is not after valuation_date"
    )


# --- 5b. Issue #161: description strings no longer gate the whole profile ----
#
# The regression this issue exists for: an ordinary Treasury note
# (US91282CMC28, T 4 1/2 12/31/31) returns MTY_TYP = NORMAL on a real
# Bloomberg workstation, and the first revision refused the entire profile
# for it -- eight fields blank, and a form to fill by hand.


_REAL_UST_BOND_MASTER = {
    "coupon": 0.045,
    "coupon_frequency": "SEMI_ANNUAL",
    "issue_date": "2024-12-31",
    "maturity_date": "2031-12-31",
    "first_coupon_date": "2025-06-30",
    "callable_flag": False,
    "sinkable_flag": False,
}


def test_a_real_ust_returning_maturity_type_normal_is_admitted():
    profile = _resolve(
        isin="US91282CMC28",
        bond_master=dict(_REAL_UST_BOND_MASTER),
        bond_master_raw={
            "day_count": "ACT/ACT",
            "maturity_type": "NORMAL",
            "calc_type": "STREET CONVENTION",
        },
    )

    assert profile.supported is True
    assert profile.rejection_reasons == ()
    assert profile.unresolved_fields == ()
    assert set(_values(profile)) == set(ADVANCED_FIELD_PATHS)


@pytest.mark.parametrize("value", ["NORMAL", "AT MATURITY", None, "", "ANYTHING ELSE"])
def test_maturity_type_evidence_decides_nothing(value):
    """MTY_TYP is display-only evidence: it can neither admit nor refuse."""

    profile = _resolve(bond_master_raw={**_TREASURY_BOND_MASTER_RAW, "maturity_type": value})

    assert profile.supported is True
    assert profile.unresolved_fields == ()
    assert not any("maturity_type" in reason for reason in profile.rejection_reasons)


@pytest.mark.parametrize("value", ["STREET CONVENTION", "UK:BUMP/DMO METHOD", None, ""])
def test_calc_type_evidence_decides_nothing(value):
    profile = _resolve(bond_master_raw={**_TREASURY_BOND_MASTER_RAW, "calc_type": value})

    assert profile.supported is True
    assert profile.unresolved_fields == ()


def test_a_missing_day_count_description_blocks_nothing():
    """A Bloomberg miss is not a contradiction, so the profile default stands."""

    profile = _resolve(bond_master_raw={**_TREASURY_BOND_MASTER_RAW, "day_count": None})

    assert profile.supported is True
    assert profile.unresolved_fields == ()
    assert _provenance(profile)[PATH_DAY_COUNT] == UST_CONVENTION_PROFILE.default_provenance


def test_a_contradicting_day_count_description_blocks_only_the_day_count():
    """The one remaining use of a description string: it withholds its own field."""

    profile = _resolve(bond_master_raw={**_TREASURY_BOND_MASTER_RAW, "day_count": "ISMA-30/360"})

    assert profile.supported is True
    assert profile.rejection_reasons == ()
    assert [item.path for item in profile.unresolved_fields] == [PATH_DAY_COUNT]
    assert "ISMA-30/360" in profile.unresolved_fields[0].reason
    # The other seven are untouched -- the whole point of Issue #161.
    resolved = _values(profile)
    assert PATH_DAY_COUNT not in resolved
    assert set(resolved) == set(ADVANCED_FIELD_PATHS) - {PATH_DAY_COUNT}


def test_a_gilt_shaped_bond_collects_every_reason_at_once():
    profile = resolve_bond_advanced_field_profile(
        convention_profile=UST_CONVENTION_PROFILE.name,
        isin="GB00BFX0ZL78",
        currency="GBP",
        bond_master={
            **_TREASURY_BOND_MASTER,
            "coupon": 0.0,
            "coupon_frequency": "ANNUAL",
            "issue_date": "2018-04-22",
            "maturity_date": "2028-10-22",
            "first_coupon_date": "2018-10-22",
        },
        bond_master_raw={
            "day_count": "ACT/ACT",
            "maturity_type": "NORMAL",
            "calc_type": "UK:BUMP/DMO METHOD",
        },
        valuation_date=_VALUATION_DATE,
        expiry_date=_EXPIRY_DATE,
    )

    assert profile.supported is False
    assert profile.fields == ()
    # Every failing condition is reported together, not one at a time.
    assert len(profile.rejection_reasons) >= 3


# --- 5c. Issue #161: one missing input blocks one field, not eight -----------


@pytest.mark.parametrize("date_field", ["issue_date", "maturity_date", "first_coupon_date"])
def test_a_missing_schedule_date_leaves_only_the_fields_that_need_it_unresolved(date_field):
    """Issue #161 P2 correction: `issue_date`/`maturity_date`/`first_coupon_date`
    have no Advanced override anywhere on this route, so a field that cannot
    resolve because one of them is missing is left unresolved -- absent from
    both `fields` and `unresolved_fields` -- rather than a repairable
    `BLOCKED`. `BLOCKED` would falsely promise the browser a trader override
    is a genuine route past it; here there is none. See
    `test_a_contradicting_day_count_description_blocks_only_the_day_count`
    for a field that genuinely is BLOCKED-and-repairable, for contrast."""

    profile = _resolve(bond_master={**_TREASURY_BOND_MASTER, date_field: None})

    assert profile.supported is True
    assert profile.rejection_reasons == ()
    assert profile.unresolved_fields == ()
    # maturity_date additionally carries the "has it matured?" question, so it
    # leaves `status` unresolved too. Nothing else is ever affected.
    expected_unresolved = (
        {PATH_LAST_COUPON_DATE, PATH_STATUS}
        if date_field == "maturity_date"
        else {PATH_LAST_COUPON_DATE}
    )
    resolved = _values(profile)
    assert set(resolved) == set(ADVANCED_FIELD_PATHS) - expected_unresolved
    for path in expected_unresolved:
        assert path not in resolved
    # The fields that did resolve carry real values, not placeholders.
    assert resolved[PATH_DAY_COUNT] == UST_CONVENTION_PROFILE.day_count.value
    assert resolved[PATH_REPORTING_DATE] == _VALUATION_DATE


def test_an_unusable_valuation_date_blocks_only_its_own_two_fields():
    profile = _resolve(valuation_date="not-a-date")

    assert profile.supported is True
    assert {item.path for item in profile.unresolved_fields} == {
        PATH_REPORTING_DATE,
        PATH_STATUS,
    }
    # Shiori will not assert ACTIVE without being able to compare maturity to
    # a valuation date, but the profile-owned constants are unaffected.
    resolved = _values(profile)
    assert resolved[PATH_BOND_TYPE] == UST_CONVENTION_PROFILE.bond_type.value
    assert resolved[PATH_LAST_COUPON_DATE] == "2030-07-31"


def test_a_blocked_field_is_never_reported_as_a_product_rejection():
    """The two lists are the browser's repairable/not-repairable signal."""

    profile = _resolve(bond_master_raw={**_TREASURY_BOND_MASTER_RAW, "day_count": "ISMA-30/360"})

    assert profile.rejection_reasons == ()
    assert profile.unresolved_fields != ()


def test_a_missing_bond_master_field_is_never_reported_as_repairable_blocked():
    """The inverse of the test above: a field the trader genuinely cannot fix
    in Advanced (because its input has no Advanced control at all) must not
    appear in `unresolved_fields` -- that list is a promise of a real route,
    and a missing `first_coupon_date` has none (Issue #161 P2 correction)."""

    profile = _resolve(bond_master={**_TREASURY_BOND_MASTER, "first_coupon_date": None})

    assert profile.supported is True
    assert profile.rejection_reasons == ()
    assert profile.unresolved_fields == ()
    assert PATH_LAST_COUPON_DATE not in _values(profile)


# --- 5d. Issue #161 P3: a malformed (non-empty) schedule date is not
# "present" -- it is treated exactly like a missing one -------------------
#
# Independent-review finding: Bloomberg's own ISSUE_DT/MATURITY/FIRST_CPN_DT
# mnemonics pass through with no format validation
# (data/bloomberg_bond_quote.py's `_passthrough_bloomberg_string`), so a
# real Bloomberg response can hand this resolver a non-blank string that is
# not a valid ISO date. `_optional_iso_date` already treats that the same
# as missing -- these tests pin that behaviour explicitly, since the
# browser-side regression this issue actually exposed was a UI check that
# used a bare non-blank-string test instead of the resolver's own
# grammar-plus-calendar validity rule.


@pytest.mark.parametrize(
    "malformed_value",
    [
        "20241231",  # no dashes -- the exact shape a mis-configured
        # Bloomberg date format returns
        "31/12/2031",  # DD/MM/YYYY
        "2031-13-01",  # month out of range
        "2031-02-30",  # correct shape, impossible calendar date
        "not-a-date",
        "",
    ],
)
@pytest.mark.parametrize("date_field", ["issue_date", "maturity_date", "first_coupon_date"])
def test_a_malformed_schedule_date_is_treated_exactly_like_a_missing_one(
    date_field, malformed_value
):
    profile = _resolve(bond_master={**_TREASURY_BOND_MASTER, date_field: malformed_value})

    assert profile.supported is True
    assert profile.rejection_reasons == ()
    assert profile.unresolved_fields == ()
    resolved = _values(profile)
    assert PATH_LAST_COUPON_DATE not in resolved
    if date_field == "maturity_date":
        assert PATH_STATUS not in resolved
    else:
        assert resolved[PATH_STATUS] == UST_CONVENTION_PROFILE.status.value
    # Every field that does not depend on the malformed date is unaffected.
    assert resolved[PATH_DAY_COUNT] == UST_CONVENTION_PROFILE.day_count.value
    assert resolved[PATH_REPORTING_DATE] == _VALUATION_DATE


def _assert_refused(profile, *, expected_fragment: str) -> None:
    assert profile.supported is False
    assert profile.fields == ()
    assert profile.pending_field_paths == ()
    assert profile.unresolved_fields == ()
    assert any(expected_fragment in reason for reason in profile.rejection_reasons), (
        f"{expected_fragment!r} not in {profile.rejection_reasons}"
    )


# --- 6. Irregular schedules fail closed ------------------------------------


def test_an_irregular_schedule_rejects_the_whole_profile():
    profile = _resolve(
        bond_master={
            **_TREASURY_BOND_MASTER,
            "issue_date": "2024-03-05",
            "maturity_date": "2031-01-31",
            "first_coupon_date": "2024-07-31",
        }
    )

    assert profile.supported is False
    assert profile.fields == ()
    assert profile.unresolved_fields == ()
    assert profile.rejection_reasons == (
        "current pricing adapter supports regular coupon schedules only; "
        "editing last_coupon_date cannot repair the underlying schedule",
    )


def test_an_irregular_schedule_applies_none_of_the_eight_fields():
    profile = _resolve(
        bond_master={
            **_TREASURY_BOND_MASTER,
            "issue_date": "2024-03-05",
            "maturity_date": "2031-01-31",
            "first_coupon_date": "2024-07-31",
        }
    )
    assert profile.fields == ()


# --- 7. Environment and module boundaries -----------------------------------


def test_missing_quantlib_raises_the_existing_clear_error(monkeypatch):
    monkeypatch.setattr(profile_module, "ql", None)
    with pytest.raises(BLIQuantLibNotAvailableError):
        _resolve()


def test_module_wraps_quantlib_import_in_try_except_import_error():
    source = inspect.getsource(profile_module)
    assert "try:\n    import QuantLib as ql" in source
    assert "except ImportError:" in source


def test_module_imports_no_pricing_curve_or_volatility_machinery():
    """This resolver must never grow into a second pricing path: it may reuse
    the reviewed coupon-schedule adapter and the typed enums, and nothing else
    from the pricing chain."""

    import_lines = [
        line
        for line in inspect.getsource(profile_module).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    forbidden = (
        "bli_black76",
        "bli_pricing_engine",
        "bli_forward_clean_price",
        "bli_curve_tenor",
        "bli_curve_selector",
        "bli_zero_curve_nodes",
        "bli_zero_rate_interpolation",
        "bli_discount_factor",
        "bli_curve_discount_factor",
        "bli_implied_price_vol",
        "bli_valuation_time",
        "bli_benchmark_comparison",
    )
    for line in import_lines:
        for name in forbidden:
            assert name not in line, f"{name} must not be imported here: {line!r}"


def test_module_reads_no_clock():
    source = inspect.getsource(profile_module)
    for forbidden in ("datetime.now", "date.today", "time.time"):
        assert forbidden not in source


# =============================================================================
# Issue #161 requirement A: the resolver is market-agnostic
# =============================================================================
#
# Everything above pins the UST profile's behaviour, which must not regress.
# What follows pins the property that makes a second and third market
# possible without a second and third resolver: **nothing in this module is
# UST-specific**. Every market-varying value is read from the profile record,
# so registering a profile is a data change.
#
# The synthetic profile below asserts nothing about any real market. Its
# values are deliberately different from UST's in every field precisely so a
# hardcoded UST constant anywhere in the resolver would show up as a failure
# here. Registering a *real* market's profile needs that market's conventions
# confirmed by Bloomberg evidence, an existing reviewed document, or Eddy --
# which is why `CONVENTION_PROFILES` still holds only UST.

_SYNTHETIC_PROFILE = BLIConventionProfile(
    name="SYNTHETIC_TEST",
    currency=Currency.EUR,
    coupon_frequencies=(Frequency.ANNUAL,),
    day_count=DayCount.THIRTY_360,
    day_count_evidence="SYNTHETIC/TEST",
    bond_type=BondType.FIXED_COUPON_BULLET,
    ex_dividend_days=3,
    status=BondStatus.ACTIVE,
    settlement_business_days=2,
    settlement_calendar=CALENDAR_US_SIFMA,
    source_system="SHIORI_SYNTHETIC_TEST_CONVENTION_PROFILE",
)

# Presence markers for the plain fixed-coupon structural evidence every
# profile but UST requires. **These are not confirmed Bloomberg values and
# assert nothing about any mnemonic's semantics**: the gate checks only that
# the facts were established at all, and no confirmed mnemonic supplies them
# yet (they are the candidates `tools/bloomberg_dapi_probe.py` proposes). A
# real bond therefore never reaches these tests' state today -- which is
# exactly what `test_a_real_bloomberg_bond_never_passes_...` below pins.
_STRUCTURAL_EVIDENCE = dict.fromkeys(PLAIN_FIXED_COUPON_EVIDENCE_FIELDS, "SYNTHETIC_EVIDENCE")

# An annual grid: 2024-01-31 to 2031-01-31, first coupon 2025-01-31.
_ANNUAL_BOND_MASTER = {
    "coupon": 0.0275,
    "coupon_frequency": "ANNUAL",
    "issue_date": "2024-01-31",
    "maturity_date": "2031-01-31",
    "first_coupon_date": "2025-01-31",
    "callable_flag": False,
    "sinkable_flag": False,
    "day_count": None,
    "bond_type": None,
    "last_coupon_date": None,
    **_STRUCTURAL_EVIDENCE,
}


@pytest.fixture()
def synthetic_registry(monkeypatch):
    monkeypatch.setitem(
        profile_module.__dict__["get_convention_profile"].__globals__["CONVENTION_PROFILES"],
        _SYNTHETIC_PROFILE.name,
        _SYNTHETIC_PROFILE,
    )
    return _SYNTHETIC_PROFILE


def _resolve_synthetic(**overrides):
    kwargs = {
        "convention_profile": _SYNTHETIC_PROFILE.name,
        "isin": "XS0000000042",
        "currency": "EUR",
        "bond_master": dict(_ANNUAL_BOND_MASTER),
        "bond_master_raw": {},
        "valuation_date": _VALUATION_DATE,
        "expiry_date": _EXPIRY_DATE,
    }
    kwargs.update(overrides)
    return resolve_bond_advanced_field_profile(**kwargs)


def test_every_profile_owned_value_comes_from_the_selected_profile(synthetic_registry):
    """The decisive anti-hardcoding test: with a profile whose every constant
    differs from UST's, every resolved value must be that profile's."""

    profile = _resolve_synthetic()

    assert profile.supported is True
    assert profile.convention_profile == "SYNTHETIC_TEST"
    values = _values(profile)
    assert values[PATH_DAY_COUNT] == DayCount.THIRTY_360.value
    assert values[PATH_BOND_TYPE] == BondType.FIXED_COUPON_BULLET.value
    assert values[PATH_EX_DIVIDEND_DAYS] == 3
    assert values[PATH_STATUS] == BondStatus.ACTIVE.value
    # None of them carries a UST value or a UST label.
    assert values[PATH_DAY_COUNT] != UST_CONVENTION_PROFILE.day_count.value
    assert values[PATH_EX_DIVIDEND_DAYS] != UST_CONVENTION_PROFILE.ex_dividend_days
    provenance = _provenance(profile)
    for path in (PATH_DAY_COUNT, PATH_BOND_TYPE, PATH_EX_DIVIDEND_DAYS, PATH_STATUS):
        assert provenance[path] == "SYNTHETIC_TEST_PROFILE_DEFAULT"
        assert provenance[path] != UST_CONVENTION_PROFILE.default_provenance


def test_the_settlement_lag_is_the_selected_profiles_own(synthetic_registry):
    """UST's lag is one business day; this profile's is two. A hardcoded 1
    anywhere in the resolver fails here."""

    values = _values(_resolve_synthetic())
    expected = advance_settlement_business_days(
        date(2026, 10, 20), 2, _SYNTHETIC_PROFILE
    ).isoformat()
    assert values[PATH_FORWARD_SETTLEMENT_DATE] == expected
    assert values[PATH_OPTION_SETTLEMENT_DATE] == expected
    ust_settlement = advance_settlement_business_days(
        date(2026, 10, 20), 1, UST_CONVENTION_PROFILE
    ).isoformat()
    assert values[PATH_FORWARD_SETTLEMENT_DATE] != ust_settlement


def test_the_coupon_grid_uses_bloombergs_confirmed_frequency_not_a_constant(synthetic_registry):
    """`last_coupon_date` on an *annual* grid proves the schedule is generated
    with the frequency Bloomberg confirmed for this bond, not with a
    semi-annual constant left over from the UST slice."""

    values = _values(_resolve_synthetic())
    assert values[PATH_LAST_COUPON_DATE] == "2030-01-31"
    # And the reviewed adapter agrees, asked independently.
    assert values[PATH_LAST_COUPON_DATE] == derive_last_coupon_date(
        issue_date="2024-01-31",
        maturity_date="2031-01-31",
        first_coupon_date="2025-01-31",
        coupon_frequency=Frequency.ANNUAL,
    )


def test_a_frequency_the_profile_states_no_conventions_for_fails_closed(synthetic_registry):
    """The profile covers annual only, so a semi-annual bond is refused for
    the whole profile rather than silently accrued on annual conventions."""

    profile = _resolve_synthetic(
        bond_master={**_ANNUAL_BOND_MASTER, "coupon_frequency": "SEMI_ANNUAL"}
    )

    assert profile.supported is False
    assert profile.fields == ()
    assert any("SEMI_ANNUAL" in reason for reason in profile.rejection_reasons)
    assert any("SYNTHETIC_TEST" in reason for reason in profile.rejection_reasons)


def test_a_currency_outside_the_profile_fails_closed_naming_that_profile(synthetic_registry):
    profile = _resolve_synthetic(currency="USD")

    assert profile.supported is False
    assert any(
        "is not EUR" in reason and "SYNTHETIC_TEST" in reason
        for reason in profile.rejection_reasons
    )


def test_the_day_count_evidence_string_is_the_selected_profiles_own(synthetic_registry):
    """Bloomberg's DAY_CNT_DES is compared against *this* profile's expected
    description, not UST's. "ACT/ACT" contradicts this profile, and blocks
    only `day_count` -- every other field keeps its value."""

    profile = _resolve_synthetic(bond_master_raw={"day_count": "ACT/ACT"})

    assert profile.supported is True
    assert [item.path for item in profile.unresolved_fields] == [PATH_DAY_COUNT]
    assert "'ACT/ACT'" in profile.unresolved_fields[0].reason
    assert "'SYNTHETIC/TEST'" in profile.unresolved_fields[0].reason
    # The seven other fields are unaffected.
    values = _values(profile)
    assert PATH_DAY_COUNT not in values
    assert values[PATH_BOND_TYPE] == BondType.FIXED_COUPON_BULLET.value
    assert values[PATH_EX_DIVIDEND_DAYS] == 3
    assert values[PATH_STATUS] == BondStatus.ACTIVE.value
    assert values[PATH_REPORTING_DATE] == _VALUATION_DATE


def test_the_matching_evidence_string_still_never_produces_the_value(synthetic_registry):
    """Issue #145's prohibition, per profile: a matching description string
    withholds nothing, but the value still comes from the approved profile
    constant rather than from the string."""

    profile = _resolve_synthetic(bond_master_raw={"day_count": "SYNTHETIC/TEST"})

    assert profile.unresolved_fields == ()
    assert _values(profile)[PATH_DAY_COUNT] == DayCount.THIRTY_360.value
    assert _provenance(profile)[PATH_DAY_COUNT] == "SYNTHETIC_TEST_PROFILE_DEFAULT"


def test_the_market_independent_derivations_never_carry_a_profile_label(synthetic_registry):
    """`reporting_date` and the settlement dates are mechanical derivations,
    not any market's conventions, so they stay SHIORI_DERIVED whichever
    profile is selected."""

    for profile in (_resolve(), _resolve_synthetic()):
        provenance = _provenance(profile)
        for path in (
            PATH_REPORTING_DATE,
            PATH_LAST_COUPON_DATE,
            PATH_FORWARD_SETTLEMENT_DATE,
            PATH_OPTION_SETTLEMENT_DATE,
        ):
            assert provenance[path] == PROVENANCE_SHIORI_DERIVED


def test_the_product_shape_refusals_are_market_independent(synthetic_registry):
    """Callable, sinkable, non-positive coupon and matured are properties of
    the bond, not of a market, so they refuse under any profile."""

    for overrides, fragment in (
        ({"callable_flag": True}, "callable"),
        ({"sinkable_flag": True}, "sinkable"),
        ({"coupon": 0.0}, "zero-coupon"),
        ({"coupon": None}, "fixed coupon is required"),
        ({"maturity_date": "2020-01-31"}, "matured bond is never pre-filled"),
    ):
        profile = _resolve_synthetic(bond_master={**_ANNUAL_BOND_MASTER, **overrides})
        assert profile.supported is False, overrides
        assert profile.fields == ()
        assert any(fragment in reason for reason in profile.rejection_reasons), overrides


def test_the_resolver_names_no_profile_and_no_market_anywhere_in_its_code():
    """Structural proof of requirement A: the resolver branches on no profile
    name and no market. Adding a market is a profile record, never an `if`
    here -- which is what stops three markets becoming three drifting
    resolvers."""

    source = inspect.getsource(profile_module)
    tree = ast.parse(source)

    # Prose is excluded, code is not: the docstrings legitimately explain the
    # UST history this split came from, while a market name surviving in the
    # *code* would mean the resolver still branches on one. Every remaining
    # string literal is deliberately still checked -- `if name == "UST"` is
    # exactly the kind of thing this test exists to catch.
    docstring_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                docstring_lines.update(range(first.lineno, first.end_lineno + 1))

    code = "\n".join(
        line.split("#")[0]
        for number, line in enumerate(source.splitlines(), start=1)
        if number not in docstring_lines
    )
    for market in ("UST", "GILT", "CORPORATE", "GERMAN", "TREASURY"):
        assert market not in code, f"the common resolver must not name {market!r}"


# =============================================================================
# Issue #161 follow-up: fail-closed until the structure is established
# =============================================================================
#
# The exposure this closes: a positive coupon at some frequency, on a bond
# that is neither callable nor sinkable, does not make a bond a fixed-coupon
# bullet. A floating-rate note returns its *current* coupon in CPN and its
# reset frequency in CPN_FREQ and passes every one of those checks. So does a
# linker, a convertible and an amortizer. UST is exempt by Eddy's explicit
# instruction (its behaviour passed real workstation UAT); every other
# profile refuses until Bloomberg has confirmed what the bond actually is.


def _real_bloomberg_bond_master(**overrides) -> dict:
    """A bond master shaped exactly like a real Bloomberg response today.

    That is: every confirmed field populated, and **none** of the structural
    evidence fields, because no mnemonic for any of them is confirmed yet.
    """

    master = {
        key: value
        for key, value in _ANNUAL_BOND_MASTER.items()
        if key not in PLAIN_FIXED_COUPON_EVIDENCE_FIELDS
    }
    master.update(overrides)
    return master


def test_a_real_bloomberg_bond_never_passes_a_non_ust_profile_today(synthetic_registry):
    """Item 5, the decisive one: selecting a profile by hand does not
    establish what the bond is, so it cannot push one through."""

    profile = _resolve_synthetic(bond_master=_real_bloomberg_bond_master())

    assert profile.supported is False
    assert profile.fields == ()
    assert profile.unresolved_fields == ()
    reason = " ".join(profile.rejection_reasons)
    assert "has not confirmed this bond is a plain fixed-coupon bullet" in reason
    assert "floating-rate, inflation-linked, convertible or amortizing" in reason
    assert "selecting it by hand does not establish it" in reason


def test_the_refusal_names_every_missing_piece_of_evidence(synthetic_registry):
    """So a probe run has a checklist, rather than one field at a time."""

    profile = _resolve_synthetic(bond_master=_real_bloomberg_bond_master())

    reason = " ".join(profile.rejection_reasons)
    for field in PLAIN_FIXED_COUPON_EVIDENCE_FIELDS:
        assert field in reason


@pytest.mark.parametrize("missing", PLAIN_FIXED_COUPON_EVIDENCE_FIELDS)
def test_one_missing_evidence_field_is_enough_to_refuse(synthetic_registry, missing):
    """Fail-closed means *every* piece, not a majority of them."""

    profile = _resolve_synthetic(bond_master={**_ANNUAL_BOND_MASTER, missing: None})

    assert profile.supported is False
    assert missing in " ".join(profile.rejection_reasons)


def test_ust_is_exempt_so_its_uat_passed_behaviour_does_not_regress():
    """The other half of Eddy's instruction. A real Treasury carries none of
    the structural evidence fields either, and must still resolve every
    Advanced field exactly as PR #162 shipped."""

    assert all(
        field not in _TREASURY_BOND_MASTER for field in PLAIN_FIXED_COUPON_EVIDENCE_FIELDS
    )
    profile = _resolve()

    assert profile.supported is True
    assert profile.rejection_reasons == ()
    assert tuple(field.path for field in profile.fields) == ADVANCED_FIELD_PATHS


def test_the_evidence_gate_is_checked_server_side_not_left_to_the_browser(
    synthetic_registry,
):
    """Structural proof of *where* item 5's guarantee lives. The refusal comes
    out of the resolver itself, so no browser state -- including a
    hand-selected profile -- can route around it."""

    source = inspect.getsource(profile_module._product_rejection_reasons)
    assert "plain_fixed_coupon_evidence_required" in source
    assert "PLAIN_FIXED_COUPON_EVIDENCE_FIELDS" in source


# =============================================================================
# Issue #161 follow-up: ex-dividend days are never guessed
# =============================================================================


def _no_ex_dividend_profile() -> BLIConventionProfile:
    return BLIConventionProfile(
        name="NO_EX_DIV_TEST",
        currency=Currency.EUR,
        coupon_frequencies=(Frequency.ANNUAL,),
        day_count=DayCount.THIRTY_360,
        bond_type=BondType.FIXED_COUPON_BULLET,
        status=BondStatus.ACTIVE,
        settlement_business_days=2,
        settlement_calendar=CALENDAR_US_SIFMA,
        source_system="SHIORI_NO_EX_DIV_TEST_CONVENTION_PROFILE",
    )


@pytest.fixture()
def no_ex_dividend_registry(monkeypatch):
    profile = _no_ex_dividend_profile()
    monkeypatch.setitem(
        profile_module.__dict__["get_convention_profile"].__globals__["CONVENTION_PROFILES"],
        profile.name,
        profile,
    )
    return profile


def test_a_profile_with_no_ex_dividend_default_blocks_only_that_field(
    no_ex_dividend_registry,
):
    """Item 3: the one unknown field blocks, and the other seven resolve. A
    blocked field is a genuine route past it -- `ex-dividend-days-input` is a
    real Advanced control the trader can fill."""

    profile = _resolve_synthetic(convention_profile="NO_EX_DIV_TEST")

    assert profile.supported is True
    assert [item.path for item in profile.unresolved_fields] == [PATH_EX_DIVIDEND_DAYS]
    assert "no approved ex-dividend default" in profile.unresolved_fields[0].reason

    values = _values(profile)
    assert PATH_EX_DIVIDEND_DAYS not in values
    assert values[PATH_DAY_COUNT] == DayCount.THIRTY_360.value
    assert values[PATH_BOND_TYPE] == BondType.FIXED_COUPON_BULLET.value
    assert values[PATH_STATUS] == BondStatus.ACTIVE.value
    assert values[PATH_LAST_COUPON_DATE] == "2030-01-31"
    assert values[PATH_REPORTING_DATE] == _VALUATION_DATE
    assert values[PATH_FORWARD_SETTLEMENT_DATE] is not None
    assert values[PATH_OPTION_SETTLEMENT_DATE] is not None


def test_a_typed_bloomberg_ex_dividend_value_is_used_when_one_exists(
    no_ex_dividend_registry,
):
    """"Use a typed Bond Master value if there is one" -- so the day a
    mnemonic is confirmed, the block lifts on its own."""

    profile = _resolve_synthetic(
        convention_profile="NO_EX_DIV_TEST",
        bond_master={**_ANNUAL_BOND_MASTER, "ex_dividend_days": 7},
    )

    assert profile.unresolved_fields == ()
    assert _values(profile)[PATH_EX_DIVIDEND_DAYS] == 7
    assert _provenance(profile)[PATH_EX_DIVIDEND_DAYS] == PROVENANCE_BLOOMBERG_AUTO


def test_a_typed_bloomberg_zero_is_a_value_not_an_absence(no_ex_dividend_registry):
    """Zero ex-dividend days is a real convention, not "nothing returned"."""

    profile = _resolve_synthetic(
        convention_profile="NO_EX_DIV_TEST",
        bond_master={**_ANNUAL_BOND_MASTER, "ex_dividend_days": 0},
    )

    assert profile.unresolved_fields == ()
    assert _values(profile)[PATH_EX_DIVIDEND_DAYS] == 0
    assert _provenance(profile)[PATH_EX_DIVIDEND_DAYS] == PROVENANCE_BLOOMBERG_AUTO


def test_a_confirmed_profile_default_still_wins_over_nothing(synthetic_registry):
    """The synthetic profile *does* have a confirmed default, so it resolves
    -- the blocking above is about absence, not about profiles in general."""

    profile = _resolve_synthetic()

    assert profile.unresolved_fields == ()
    assert _values(profile)[PATH_EX_DIVIDEND_DAYS] == 3


# =============================================================================
# Issue #161 follow-up: an unconfirmed day-count description blocks nothing
# =============================================================================


def test_a_profile_without_a_confirmed_description_string_ignores_day_cnt_des(
    no_ex_dividend_registry,
):
    """`day_count_evidence=None` means Bloomberg's DAY_CNT_DES for this market
    has not been observed. Comparing against a guessed expected string would
    block `day_count` on every ordinary bond in that market, so it is not
    read at all -- and `day_count` still comes from the confirmed profile
    constant, never from the string."""

    profile = _resolve_synthetic(
        convention_profile="NO_EX_DIV_TEST",
        bond_master_raw={"day_count": "ACT/ACT"},
    )

    blocked = [item.path for item in profile.unresolved_fields]
    assert PATH_DAY_COUNT not in blocked
    assert _values(profile)[PATH_DAY_COUNT] == DayCount.THIRTY_360.value
    assert _provenance(profile)[PATH_DAY_COUNT] == "NO_EX_DIV_TEST_PROFILE_DEFAULT"
