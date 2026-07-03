"""Tests for the ISIN -> BondReferenceData resolver (docs/21 BLI resolution slice).

Resolution tests only. No pricing, cash-flow generation, schedule
engine, or market data is involved -- ``resolve_bond_reference_data``
looks a record up by exact ISIN match and calls the existing
``is_mvp_pricing_eligible`` once; it never re-implements an eligibility
rule and never computes PV, DV01, cashflows, or a schedule.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from shiori_pricing_lab.products.enums import (
    BondYieldConvention,
    BusinessDayConvention,
    Currency,
    DayCount,
    Frequency,
)
from shiori_pricing_lab.reference_data import (
    BondReferenceData,
    BondReferenceResolutionResult,
    BondResolutionStatus,
    BondStatus,
    BondType,
    DuplicateBondReferenceDataError,
    resolve_bond_reference_data,
)
from shiori_pricing_lab.reference_data.fixtures import SYNTHETIC_BOND_FIXTURES

_VANILLA_BULLET_ISIN = "XS0000000001"  # eligible, per fixtures.py
_ZERO_COUPON_ISIN = "XS0000000002"  # valid reference data, ineligible (zero-coupon)
_CALLABLE_ISIN = "XS0000000003"  # valid reference data, ineligible (callable)
_FLOATING_RATE_NOTE_ISIN = "XS0000000004"  # valid reference data, ineligible (bond_type)


def _bond(**overrides) -> BondReferenceData:
    """Build a synthetic, valid, MVP-pricing-eligible fixed-coupon bullet bond."""

    params = dict(
        isin="XS1234500000",
        issuer="Synthetic Resolution Test Issuer",
        currency=Currency.USD,
        coupon=0.04,
        coupon_frequency=Frequency.SEMI_ANNUAL,
        maturity_date="2031-05-01",
        issue_date="2025-05-01",
        day_count=DayCount.ACT_ACT_ISDA,
        business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
        redemption_amount=100.0,
        callable_flag=False,
        sinkable_flag=False,
        bond_type=BondType.FIXED_COUPON_BULLET,
        yield_convention=BondYieldConvention.SEMI_ANNUAL_COMPOUND,
        ex_dividend_days=1,
        first_coupon_date="2025-11-01",
        last_coupon_date="2030-11-01",
        status=BondStatus.ACTIVE,
    )
    params.update(overrides)
    return BondReferenceData(**params)


# --- Exact match, eligible ---------------------------------------------------


def test_exact_match_eligible_fixture_returns_found_eligible():
    result = resolve_bond_reference_data(_VANILLA_BULLET_ISIN, SYNTHETIC_BOND_FIXTURES)

    assert result.status is BondResolutionStatus.FOUND_ELIGIBLE
    assert result.requested_isin == _VANILLA_BULLET_ISIN
    assert result.bond_reference_data is not None
    assert result.bond_reference_data.isin == _VANILLA_BULLET_ISIN
    assert result.eligibility_reasons == ()
    assert result.block_reason is None


def test_default_fixtures_argument_resolves_against_synthetic_fixtures():
    # fixtures defaults to SYNTHETIC_BOND_FIXTURES (docs/21 §3/§8).
    result = resolve_bond_reference_data(_VANILLA_BULLET_ISIN)
    assert result.status is BondResolutionStatus.FOUND_ELIGIBLE


# --- Missing ISIN: not-found, never an exception -----------------------------


def test_missing_isin_returns_not_found_not_exception():
    result = resolve_bond_reference_data("XS_DOES_NOT_EXIST", SYNTHETIC_BOND_FIXTURES)

    assert result.status is BondResolutionStatus.NOT_FOUND
    assert result.requested_isin == "XS_DOES_NOT_EXIST"
    assert result.bond_reference_data is None
    assert result.eligibility_reasons == ()
    assert result.block_reason is not None


# --- No fuzzy / partial / case-insensitive matching --------------------------


def test_lowercase_isin_does_not_match_exact_case():
    result = resolve_bond_reference_data(_VANILLA_BULLET_ISIN.lower(), SYNTHETIC_BOND_FIXTURES)
    assert result.status is BondResolutionStatus.NOT_FOUND


def test_partial_isin_prefix_does_not_match():
    result = resolve_bond_reference_data(_VANILLA_BULLET_ISIN[:6], SYNTHETIC_BOND_FIXTURES)
    assert result.status is BondResolutionStatus.NOT_FOUND


# --- Duplicate ISIN: contract violation, not first/last match ---------------


def test_duplicate_isin_raises_contract_violation():
    duplicate_a = _bond(isin="XS_DUPLICATE")
    duplicate_b = _bond(isin="XS_DUPLICATE", issuer="A Different Issuer")

    with pytest.raises(DuplicateBondReferenceDataError, match="XS_DUPLICATE"):
        resolve_bond_reference_data("XS_DUPLICATE", (duplicate_a, duplicate_b))


# --- Callable / sinkable / zero-coupon / bond_type: found-ineligible --------


def test_callable_fixture_returns_found_ineligible_with_callable_reason():
    result = resolve_bond_reference_data(_CALLABLE_ISIN, SYNTHETIC_BOND_FIXTURES)

    assert result.status is BondResolutionStatus.FOUND_INELIGIBLE
    assert result.bond_reference_data is not None
    assert result.bond_reference_data.isin == _CALLABLE_ISIN
    assert any("callable" in reason for reason in result.eligibility_reasons)
    assert result.block_reason is not None
    assert "callable" in result.block_reason


def test_zero_coupon_fixture_returns_found_ineligible_with_zero_coupon_reason():
    result = resolve_bond_reference_data(_ZERO_COUPON_ISIN, SYNTHETIC_BOND_FIXTURES)

    assert result.status is BondResolutionStatus.FOUND_INELIGIBLE
    assert any("zero-coupon" in reason for reason in result.eligibility_reasons)


def test_floating_rate_note_returns_found_ineligible_with_bond_type_reason():
    result = resolve_bond_reference_data(_FLOATING_RATE_NOTE_ISIN, SYNTHETIC_BOND_FIXTURES)

    assert result.status is BondResolutionStatus.FOUND_INELIGIBLE
    assert any("bond_type" in reason for reason in result.eligibility_reasons)


def test_inactive_bond_returns_found_ineligible_with_status_reason():
    inactive_bond = _bond(isin="XS_INACTIVE_0001", status=BondStatus.INACTIVE)

    result = resolve_bond_reference_data("XS_INACTIVE_0001", (inactive_bond,))

    assert result.status is BondResolutionStatus.FOUND_INELIGIBLE
    assert any("status INACTIVE" in reason for reason in result.eligibility_reasons)


# --- Multiple ineligibility reasons preserved, not collapsed ----------------


def test_multiple_ineligibility_reasons_are_all_preserved():
    doubly_ineligible = _bond(
        isin="XS_DOUBLY_INELIGIBLE",
        callable_flag=True,
        coupon=0.0,
        first_coupon_date="2031-05-01",
        last_coupon_date="2031-05-01",
    )

    result = resolve_bond_reference_data("XS_DOUBLY_INELIGIBLE", (doubly_ineligible,))

    assert result.status is BondResolutionStatus.FOUND_INELIGIBLE
    assert len(result.eligibility_reasons) == 2
    assert any("callable" in reason for reason in result.eligibility_reasons)
    assert any("zero-coupon" in reason for reason in result.eligibility_reasons)
    # block_reason must not collapse to only the first reason.
    assert "callable" in result.block_reason
    assert "zero-coupon" in result.block_reason


# --- The resolver must not re-implement eligibility rules --------------------


def test_other_yield_convention_returns_found_ineligible_via_existing_rule():
    other_yield_bond = _bond(isin="XS_OTHER_YIELD", yield_convention=BondYieldConvention.OTHER)

    result = resolve_bond_reference_data("XS_OTHER_YIELD", (other_yield_bond,))

    assert result.status is BondResolutionStatus.FOUND_INELIGIBLE
    assert any("yield_convention OTHER" in reason for reason in result.eligibility_reasons)


def test_sinkable_bond_returns_found_ineligible_via_existing_rule():
    sinkable_bond = _bond(isin="XS_SINKABLE", sinkable_flag=True)

    result = resolve_bond_reference_data("XS_SINKABLE", (sinkable_bond,))

    assert result.status is BondResolutionStatus.FOUND_INELIGIBLE
    assert any("sinkable" in reason for reason in result.eligibility_reasons)


# --- The resolver must not mutate its input ----------------------------------


def test_resolver_does_not_mutate_input_fixtures_tuple():
    fixtures_before = tuple(SYNTHETIC_BOND_FIXTURES)

    resolve_bond_reference_data(_VANILLA_BULLET_ISIN, SYNTHETIC_BOND_FIXTURES)
    resolve_bond_reference_data("XS_DOES_NOT_EXIST", SYNTHETIC_BOND_FIXTURES)

    assert tuple(SYNTHETIC_BOND_FIXTURES) == fixtures_before
    assert SYNTHETIC_BOND_FIXTURES is fixtures_before or SYNTHETIC_BOND_FIXTURES == fixtures_before


def test_resolver_does_not_mutate_input_list():
    bond = _bond(isin="XS_LIST_INPUT")
    fixtures_list = [bond]

    resolve_bond_reference_data("XS_LIST_INPUT", fixtures_list)

    assert fixtures_list == [bond]
    assert len(fixtures_list) == 1


# --- Result shape: no market-data field -------------------------------------


def test_resolution_result_has_no_market_data_or_pricing_fields():
    field_names = {f.name for f in fields(BondReferenceResolutionResult)}
    forbidden = {
        "business_date",
        "valuation_date",
        "as_of_timestamp",
        "clean_price",
        "dirty_price",
        "yield",
        "spread",
        "volatility",
        "curve",
        "discount_rate",
        "funding_rate",
        "quote_side",
        "ftp_rate",
        "market_data_snapshot",
        "pv",
        "option_premium",
        "customer_return",
        "bank_margin",
        "pricing_result",
    }
    assert field_names.isdisjoint(forbidden)


def test_resolution_result_requested_isin_preserved_on_every_status():
    found_eligible = resolve_bond_reference_data(_VANILLA_BULLET_ISIN, SYNTHETIC_BOND_FIXTURES)
    found_ineligible = resolve_bond_reference_data(_CALLABLE_ISIN, SYNTHETIC_BOND_FIXTURES)
    not_found = resolve_bond_reference_data("XS_MISSING", SYNTHETIC_BOND_FIXTURES)

    assert found_eligible.requested_isin == _VANILLA_BULLET_ISIN
    assert found_ineligible.requested_isin == _CALLABLE_ISIN
    assert not_found.requested_isin == "XS_MISSING"


# --- source_fixture_name: audit-only label, must not mislabel a custom
# iterable as SYNTHETIC_BOND_FIXTURES (Codex P2 review of PR #60) ------------


def test_source_fixture_name_defaults_to_synthetic_bond_fixtures_with_explicit_default_fixtures():
    result = resolve_bond_reference_data(_VANILLA_BULLET_ISIN, SYNTHETIC_BOND_FIXTURES)
    assert result.source_fixture_name == "SYNTHETIC_BOND_FIXTURES"


def test_source_fixture_name_defaults_to_synthetic_bond_fixtures_when_fixtures_argument_omitted():
    # fixtures omitted entirely -> uses the SYNTHETIC_BOND_FIXTURES default,
    # and the label must reflect that truthfully.
    result = resolve_bond_reference_data(_VANILLA_BULLET_ISIN)
    assert result.source_fixture_name == "SYNTHETIC_BOND_FIXTURES"


def test_custom_iterable_without_explicit_label_uses_generic_caller_supplied_label():
    bond = _bond(isin="XS_GENERIC_LABEL")
    result = resolve_bond_reference_data("XS_GENERIC_LABEL", (bond,))
    # Must NOT be mislabeled as "SYNTHETIC_BOND_FIXTURES" -- this is a
    # different, caller-supplied iterable.
    assert result.source_fixture_name == "caller_supplied_fixtures"


def test_custom_iterable_with_explicit_source_fixture_name_preserves_it():
    bond = _bond(isin="XS_CUSTOM_SOURCE")
    result = resolve_bond_reference_data(
        "XS_CUSTOM_SOURCE", (bond,), source_fixture_name="as_of_2026_07_03_fixture"
    )
    assert result.source_fixture_name == "as_of_2026_07_03_fixture"


def test_duplicate_isin_error_uses_resolved_label_not_synthetic_default():
    duplicate_a = _bond(isin="XS_DUP_LABEL")
    duplicate_b = _bond(isin="XS_DUP_LABEL", issuer="A Different Issuer")

    with pytest.raises(DuplicateBondReferenceDataError, match="caller_supplied_fixtures"):
        resolve_bond_reference_data("XS_DUP_LABEL", (duplicate_a, duplicate_b))


def test_duplicate_isin_error_with_explicit_label_uses_it_not_the_default():
    duplicate_a = _bond(isin="XS_DUP_LABEL_2")
    duplicate_b = _bond(isin="XS_DUP_LABEL_2", issuer="A Different Issuer")

    with pytest.raises(DuplicateBondReferenceDataError, match="my_custom_source"):
        resolve_bond_reference_data(
            "XS_DUP_LABEL_2",
            (duplicate_a, duplicate_b),
            source_fixture_name="my_custom_source",
        )


# --- BondReferenceResolutionResult direct construction validation
# (Codex P2 review of PR #60): block_reason must never contradict
# status/eligibility_reasons, even for a hand-built result. -----------------


def test_direct_construction_found_eligible_with_block_reason_rejected():
    bond = _bond(isin="XS_DIRECT_1")
    with pytest.raises(
        ValueError, match="block_reason must be None when status is FOUND_ELIGIBLE"
    ):
        BondReferenceResolutionResult(
            requested_isin="XS_DIRECT_1",
            status=BondResolutionStatus.FOUND_ELIGIBLE,
            bond_reference_data=bond,
            eligibility_reasons=(),
            block_reason="unexpected",
            source_fixture_name="test",
        )


def test_direct_construction_found_eligible_without_bond_reference_data_rejected():
    with pytest.raises(
        ValueError, match="bond_reference_data must not be None when status is FOUND_ELIGIBLE"
    ):
        BondReferenceResolutionResult(
            requested_isin="XS_DIRECT_1B",
            status=BondResolutionStatus.FOUND_ELIGIBLE,
            bond_reference_data=None,
            eligibility_reasons=(),
            block_reason=None,
            source_fixture_name="test",
        )


def test_direct_construction_found_ineligible_missing_reasons_rejected():
    bond = _bond(isin="XS_DIRECT_2", callable_flag=True)
    with pytest.raises(
        ValueError, match="eligibility_reasons must be non-empty when status is FOUND_INELIGIBLE"
    ):
        BondReferenceResolutionResult(
            requested_isin="XS_DIRECT_2",
            status=BondResolutionStatus.FOUND_INELIGIBLE,
            bond_reference_data=bond,
            eligibility_reasons=(),
            block_reason=None,
            source_fixture_name="test",
        )


def test_direct_construction_found_ineligible_mismatched_block_reason_rejected():
    bond = _bond(isin="XS_DIRECT_3", callable_flag=True, sinkable_flag=True)
    # eligibility_reasons has two reasons, but block_reason silently drops
    # the sinkable one -- this must be rejected, not accepted.
    with pytest.raises(ValueError, match="block_reason must equal"):
        BondReferenceResolutionResult(
            requested_isin="XS_DIRECT_3",
            status=BondResolutionStatus.FOUND_INELIGIBLE,
            bond_reference_data=bond,
            eligibility_reasons=(
                "callable bonds are not MVP-pricing-eligible",
                "sinkable bonds are not MVP-pricing-eligible",
            ),
            block_reason="callable bonds are not MVP-pricing-eligible",
            source_fixture_name="test",
        )


def test_direct_construction_not_found_with_bond_reference_data_rejected():
    bond = _bond(isin="XS_DIRECT_4")
    with pytest.raises(
        ValueError, match="bond_reference_data must be None when status is NOT_FOUND"
    ):
        BondReferenceResolutionResult(
            requested_isin="XS_DIRECT_4",
            status=BondResolutionStatus.NOT_FOUND,
            bond_reference_data=bond,
            eligibility_reasons=(),
            block_reason="no bond found",
            source_fixture_name="test",
        )


def test_direct_construction_not_found_with_blank_block_reason_rejected():
    with pytest.raises(
        ValueError, match="block_reason must be a non-blank string when status is NOT_FOUND"
    ):
        BondReferenceResolutionResult(
            requested_isin="XS_DIRECT_5",
            status=BondResolutionStatus.NOT_FOUND,
            bond_reference_data=None,
            eligibility_reasons=(),
            block_reason=None,
            source_fixture_name="test",
        )


def test_resolver_created_results_still_construct_and_pass_validation():
    # Sanity: the validation added for direct construction must not reject
    # results the resolver itself builds.
    eligible = resolve_bond_reference_data(_VANILLA_BULLET_ISIN, SYNTHETIC_BOND_FIXTURES)
    ineligible = resolve_bond_reference_data(_CALLABLE_ISIN, SYNTHETIC_BOND_FIXTURES)
    not_found = resolve_bond_reference_data("XS_DOES_NOT_EXIST_2", SYNTHETIC_BOND_FIXTURES)

    assert eligible.status is BondResolutionStatus.FOUND_ELIGIBLE
    assert ineligible.status is BondResolutionStatus.FOUND_INELIGIBLE
    assert not_found.status is BondResolutionStatus.NOT_FOUND


# --- Export -------------------------------------------------------------


def test_resolver_importable_from_reference_data_package():
    from shiori_pricing_lab.reference_data import resolve_bond_reference_data as imported

    assert imported is resolve_bond_reference_data


def test_resolver_not_exported_from_products_package():
    import shiori_pricing_lab.products as products

    assert not hasattr(products, "resolve_bond_reference_data")
