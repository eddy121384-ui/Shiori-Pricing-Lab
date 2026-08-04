"""Tests for `pricing/bli_ust_fixed_coupon_profile.py` (Issue #157).

Covers the four-tier provenance contract, the eight resolved field values,
the required `convention_profile` browser-state input (Issue #157 P1-1
correction: never inferred, defaulted, or fabricated -- missing/blank/unknown
is a clear `ValueError`, never a silent fallback to "UST"), the fail-closed
product-shape gate (currency, the confirmed display-only Bloomberg evidence,
callable/sinkable, zero coupon, coupon frequency, maturity, and missing
schedule dates -- deliberately *not* an issuer-identity check; a
non-Treasury-looking bond is admitted when its shape fits and "UST" is
selected), the field-level handling of an irregular coupon grid (blocks only
`last_coupon_date`, not the other seven fields), the expiry-dependent
settlement dates and their U.S. government-bond business-day roll, and the
module boundaries that keep this resolver out of every pricing, curve,
discounting, volatility and Greek path.

The derived `last_coupon_date` is not merely asserted against a literal: it
is fed back into the reviewed coupon adapter through a real
`BLIStandaloneBondReferenceData`, which is exactly the consumer that rejects
a wrong one.
"""

from __future__ import annotations

import inspect
from dataclasses import fields as dataclass_fields
from datetime import date

import pytest

from shiori_pricing_lab.data.bli_standalone_contract import BLIStandaloneBondReferenceData
from shiori_pricing_lab.pricing import bli_ust_fixed_coupon_profile as profile_module
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import (
    BLIBondScheduleError,
    BLIQuantLibNotAvailableError,
    accrued_interest_per_100,
    derive_last_coupon_date,
)
from shiori_pricing_lab.pricing.bli_ust_fixed_coupon_profile import (
    CONVENTION_PROFILE_UST,
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
    PROVENANCE_UST_PROFILE_DEFAULT,
    UST_ADVANCED_FIELD_PATHS,
    BLIUstAdvancedFieldProfile,
    advance_ust_government_bond_business_days,
    resolve_ust_advanced_field_profile,
    ust_government_bond_calendar,
)

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
        "convention_profile": CONVENTION_PROFILE_UST,
        "isin": _ISIN,
        "currency": "USD",
        "bond_master": dict(_TREASURY_BOND_MASTER),
        "bond_master_raw": dict(_TREASURY_BOND_MASTER_RAW),
        "valuation_date": _VALUATION_DATE,
        "expiry_date": _EXPIRY_DATE,
    }
    kwargs.update(overrides)
    return resolve_ust_advanced_field_profile(**kwargs)


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
    assert tuple(field.path for field in profile.fields) == UST_ADVANCED_FIELD_PATHS


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

    assert provenance[PATH_DAY_COUNT] == PROVENANCE_UST_PROFILE_DEFAULT
    assert provenance[PATH_BOND_TYPE] == PROVENANCE_UST_PROFILE_DEFAULT
    assert provenance[PATH_EX_DIVIDEND_DAYS] == PROVENANCE_UST_PROFILE_DEFAULT
    assert provenance[PATH_STATUS] == PROVENANCE_UST_PROFILE_DEFAULT
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
    assert provenance[PATH_EX_DIVIDEND_DAYS] == PROVENANCE_UST_PROFILE_DEFAULT
    assert provenance[PATH_STATUS] == PROVENANCE_UST_PROFILE_DEFAULT


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
    assert day_count.provenance == PROVENANCE_UST_PROFILE_DEFAULT
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
    assert ust_government_bond_calendar().name() == "US government bond market"


def test_advancing_requires_a_positive_business_day_count():
    for bad in (0, -1, 1.0, True):
        with pytest.raises(ValueError):
            advance_ust_government_bond_business_days(date(2026, 10, 20), bad)


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

    field_names = {f.name for f in dataclass_fields(BLIUstAdvancedFieldProfile)}
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


@pytest.mark.parametrize(
    ("evidence_key", "value"),
    [
        ("day_count", "ISMA-30/360"),
        ("maturity_type", "NORMAL"),
        ("calc_type", "UK:BUMP/DMO METHOD"),
        ("day_count", None),
    ],
)
def test_bloomberg_evidence_that_does_not_match_the_profile_blocks_it(evidence_key, value):
    _assert_refused(
        _resolve(bond_master_raw={**_TREASURY_BOND_MASTER_RAW, evidence_key: value}),
        expected_fragment=f"Bloomberg {evidence_key} evidence",
    )


def test_a_gilt_shaped_bond_collects_every_reason_at_once():
    profile = resolve_ust_advanced_field_profile(
        convention_profile=CONVENTION_PROFILE_UST,
        isin="GB00BFX0ZL78",
        currency="GBP",
        bond_master={
            **_TREASURY_BOND_MASTER,
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


def test_missing_schedule_dates_are_refused_rather_than_guessed():
    _assert_refused(
        _resolve(bond_master={**_TREASURY_BOND_MASTER, "first_coupon_date": None}),
        expected_fragment="first_coupon_date",
    )


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
