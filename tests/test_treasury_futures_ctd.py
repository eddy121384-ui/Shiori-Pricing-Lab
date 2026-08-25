"""Tests for `data/treasury_futures_ctd.py` (Issue #190).

Two things this file is really about:

1. **Fail closed.** A CTD record missing any of coupon / maturity /
   conversion factor / last delivery date is rejected outright. There is no
   default, no zero, no "today", and no partially-populated record.
2. **An unconfirmed source is never silently treated as current.** Manual
   entry always carries ``MANUAL_UNCONFIRMED`` and reports
   ``is_confirmed_source`` false, and the automatic Bloomberg path raises
   with the exact unresolved field names rather than falling back to it.

The CTD values used below are arbitrary test inputs chosen to exercise the
validation rules. They are not, and must never be read as, real current
market data for any contract.
"""

from __future__ import annotations

from datetime import date

import pytest

from shiori_pricing_lab.data.treasury_futures_ctd import (
    BLOOMBERG_CTD_FIELD_MAP,
    REQUIRED_BLOOMBERG_CTD_FIELDS,
    TreasuryFuturesCTDError,
    TreasuryFuturesCTDFieldsUnconfirmedError,
    TreasuryFuturesCTDSource,
    load_bloomberg_ctd_metadata,
    treasury_futures_ctd_from_manual_entry,
    unresolved_bloomberg_ctd_fields,
)

VALID_ENTRY = {
    "contract_code": "ZN",
    "contract_symbol": "TYZ6",
    "ctd_identifier": "US91282CTEST",
    "ctd_coupon_percent": 4.25,
    "ctd_maturity_date": "2034-05-15",
    "conversion_factor": 0.8012,
    "last_delivery_date": "2026-12-31",
    "as_of": "2026-08-25T14:00:00Z",
}


def test_a_complete_manual_entry_is_accepted_and_typed() -> None:
    ctd = treasury_futures_ctd_from_manual_entry(dict(VALID_ENTRY))
    assert ctd.contract_code == "ZN"
    assert ctd.ctd_coupon_percent == 4.25
    assert ctd.ctd_maturity_date == date(2034, 5, 15)
    assert ctd.conversion_factor == 0.8012
    assert ctd.last_delivery_date == date(2026, 12, 31)


def test_manual_entry_is_always_reported_as_an_unconfirmed_source() -> None:
    ctd = treasury_futures_ctd_from_manual_entry(dict(VALID_ENTRY))
    assert ctd.source is TreasuryFuturesCTDSource.MANUAL_UNCONFIRMED
    assert ctd.is_confirmed_source is False
    assert ctd.as_display_payload()["is_confirmed_source"] is False
    assert ctd.as_display_payload()["source"] == "MANUAL_UNCONFIRMED"


def test_the_source_cannot_be_claimed_as_confirmed_by_the_caller() -> None:
    # A payload asserting its own provenance must not be believed: the
    # builder ignores it entirely and stamps MANUAL_UNCONFIRMED.
    ctd = treasury_futures_ctd_from_manual_entry(
        dict(VALID_ENTRY, source="BLOOMBERG_DAPI", is_confirmed_source=True)
    )
    assert ctd.source is TreasuryFuturesCTDSource.MANUAL_UNCONFIRMED
    assert ctd.is_confirmed_source is False


def test_the_display_payload_carries_the_full_ctd_small_print() -> None:
    payload = treasury_futures_ctd_from_manual_entry(dict(VALID_ENTRY)).as_display_payload()
    assert set(payload) == {
        "contract_code",
        "contract_symbol",
        "ctd_identifier",
        "ctd_coupon_percent",
        "ctd_maturity_date",
        "conversion_factor",
        "last_delivery_date",
        "source",
        "as_of",
        "is_confirmed_source",
    }


@pytest.mark.parametrize("field", sorted({*REQUIRED_BLOOMBERG_CTD_FIELDS, "as_of"}))
def test_a_missing_required_field_fails_closed(field) -> None:
    entry = dict(VALID_ENTRY)
    entry.pop(field)
    with pytest.raises(TreasuryFuturesCTDError) as exc:
        treasury_futures_ctd_from_manual_entry(entry)
    assert field in str(exc.value)


@pytest.mark.parametrize("field", sorted({*REQUIRED_BLOOMBERG_CTD_FIELDS, "as_of"}))
def test_an_explicit_null_is_as_missing_as_an_absent_key(field) -> None:
    with pytest.raises(TreasuryFuturesCTDError):
        treasury_futures_ctd_from_manual_entry(dict(VALID_ENTRY, **{field: None}))


@pytest.mark.parametrize(
    "overrides",
    [
        {"conversion_factor": 0.0},
        {"conversion_factor": -0.8},
        {"ctd_coupon_percent": -1.0},
        {"ctd_coupon_percent": "4.25"},
        {"conversion_factor": float("nan")},
        {"ctd_maturity_date": "15/05/2034"},
        {"ctd_maturity_date": "20340515"},
        {"last_delivery_date": "not-a-date"},
        {"contract_symbol": "   "},
        {"ctd_identifier": ""},
    ],
)
def test_a_structurally_invalid_field_is_rejected(overrides) -> None:
    with pytest.raises(TreasuryFuturesCTDError):
        treasury_futures_ctd_from_manual_entry(dict(VALID_ENTRY, **overrides))


def test_a_delivery_date_at_or_after_the_ctd_maturity_is_rejected() -> None:
    with pytest.raises(TreasuryFuturesCTDError):
        treasury_futures_ctd_from_manual_entry(
            dict(VALID_ENTRY, last_delivery_date="2034-05-15")
        )
    with pytest.raises(TreasuryFuturesCTDError):
        treasury_futures_ctd_from_manual_entry(
            dict(VALID_ENTRY, last_delivery_date="2035-01-31")
        )


def test_a_non_object_payload_is_rejected() -> None:
    for payload in (None, [], "ZN", 4.25):
        with pytest.raises(TreasuryFuturesCTDError):
            treasury_futures_ctd_from_manual_entry(payload)


def test_no_bloomberg_ctd_field_mnemonic_is_confirmed_yet() -> None:
    # The RED gate of Issue #190. If this ever fails, a mnemonic was wired in
    # -- which is only legitimate alongside recorded workstation evidence,
    # and this test must be updated in the same reviewed change.
    assert BLOOMBERG_CTD_FIELD_MAP == {}
    assert unresolved_bloomberg_ctd_fields() == REQUIRED_BLOOMBERG_CTD_FIELDS


def test_the_automatic_path_fails_closed_and_names_the_unresolved_fields() -> None:
    with pytest.raises(TreasuryFuturesCTDFieldsUnconfirmedError) as exc:
        load_bloomberg_ctd_metadata("ZN")
    message = str(exc.value)
    for field in REQUIRED_BLOOMBERG_CTD_FIELDS:
        assert field in message
    # It must point at the probe, not at a workaround that invents data.
    assert "bloomberg_treasury_futures_ctd_probe.py" in message


def test_the_automatic_path_never_falls_back_to_manual_or_cached_data() -> None:
    # There is exactly one way to get a CTD record today, and it is visibly
    # unconfirmed. No synthetic contract cache exists to fall back to.
    for contract_code in ("ZT", "ZF", "ZN", "ZB"):
        with pytest.raises(TreasuryFuturesCTDFieldsUnconfirmedError):
            load_bloomberg_ctd_metadata(contract_code)
