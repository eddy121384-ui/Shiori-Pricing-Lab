"""Tests for `data/treasury_futures_ctd.py` (Issue #190).

Three things this file is about:

1. **The confirmed Bloomberg mapping is pinned.** Every mnemonic Eddy
   confirmed on his workstation is asserted as a literal here, so a later
   edit cannot silently re-point a CTD field at a different Bloomberg field.
2. **The two-stage live lookup fails closed.** A missing, blank, malformed or
   domain-invalid value on any required field aborts the whole load with the
   field named. There is no partial record and no fallback to manual, cached
   or synthetic data.
3. **An unconfirmed source is never presented as current.** Manual entry
   always carries `MANUAL_UNCONFIRMED`; only the live path is confirmed.

No network access and no real `blpapi`: Bloomberg is faked with minimal
stand-in objects mirroring only the small slice of the real API this module
uses -- the same technique `tests/test_bloomberg_bond_quote.py` uses, kept as
its own slim copy here rather than shared, consistent with this repo's
practice of not coupling test harnesses across independent modules.

The CTD values below are the shapes Eddy's live run returned. They are test
inputs and mapping evidence, never a data source.
"""

from __future__ import annotations

import sys
from datetime import date

import pytest

import shiori_pricing_lab.data.treasury_futures_ctd as module
from shiori_pricing_lab.data.treasury_futures_ctd import (
    BLOOMBERG_CTD_DISPLAY_FIELD_MAP,
    BLOOMBERG_CTD_FIELD_MAP,
    BLOOMBERG_FUTURES_TICKER_ROOTS,
    REQUIRED_BLOOMBERG_CTD_FIELDS,
    TreasuryFuturesCTDBloombergError,
    TreasuryFuturesCTDError,
    TreasuryFuturesCTDSource,
    bloomberg_delivery_month_security,
    bloomberg_generic_front_contract,
    load_bloomberg_ctd_metadata,
    treasury_futures_ctd_from_manual_entry,
    unresolved_bloomberg_ctd_fields,
)

VALID_ENTRY = {
    "contract_code": "ZN",
    "contract_symbol": "TYU6",
    "ctd_identifier": "US91282CQT17",
    "ctd_coupon_percent": 4.25,
    "ctd_maturity_date": "2033-05-31",
    "conversion_factor": 0.9069,
    "last_delivery_date": "2026-09-30",
    "as_of": "2026-08-25T14:00:00Z",
}

# The shape Eddy's live ZN run returned, as raw Bloomberg strings.
LIVE_ZN_STAGE_TWO = {
    "FUT_CTD_ISIN": "US91282CQT17",
    "FUT_CTD_CUSIP": "91282CQT1",
    "FUT_CTD_TICKER": "T 4.25 05/31/33",
    "FUT_CTD_CPN": "4.250000",
    "FUT_CTD_MTY": "2033-05-31",
    "FUT_CNVS_FACTOR": "0.906900",
    "FUT_DLV_DT_LAST": "2026-09-30",
}
GENERIC_ZN = "TY1 Comdty"
DELIVERY_ZN = "TYU6 Comdty"


# ---------------------------------------------------------------------------
# Fake blpapi
# ---------------------------------------------------------------------------


class _FakeBlpapiException(Exception):
    """Stand-in for `blpapi.exception.Exception`."""


class _FakeBlpapiExceptionNamespace:
    Exception = _FakeBlpapiException


class _EventType:
    TIMEOUT = "TIMEOUT"
    PARTIAL_RESPONSE = "PARTIAL_RESPONSE"
    RESPONSE = "RESPONSE"


class _FakeElement:
    def __init__(self, sub=None, values=None, string_value=None, raise_on_string=None):
        self._sub = sub or {}
        self._values = values
        self._string_value = string_value
        self._raise_on_string = raise_on_string

    def hasElement(self, name):
        return name in self._sub

    def getElement(self, name):
        return self._sub[name]

    def getElementAsString(self, name):
        element = self._sub[name]
        if element._raise_on_string is not None:
            raise element._raise_on_string
        return element._string_value

    def numValues(self):
        return len(self._values or [])

    def getValueAsElement(self, index):
        return self._values[index]

    def __str__(self):
        return self._string_value or ""


class _FakeEvent:
    def __init__(self, event_type, messages=()):
        self._event_type = event_type
        self._messages = list(messages)

    def eventType(self):
        return self._event_type

    def __iter__(self):
        return iter(self._messages)


def _field_data(fields: dict) -> _FakeElement:
    sub = {}
    for name, value in fields.items():
        if isinstance(value, BaseException):
            sub[name] = _FakeElement(raise_on_string=value)
        else:
            sub[name] = _FakeElement(string_value=value)
    return _FakeElement(sub=sub)


def _security_data(security, fields, *, security_error=None, omit_security=False):
    sub = {"fieldData": _field_data(fields)}
    if not omit_security:
        sub["security"] = _FakeElement(string_value=security)
    if security_error is not None:
        sub["securityError"] = _FakeElement(string_value=security_error)
    return _FakeElement(sub=sub)


def _response_event(records, *, response_error=None) -> _FakeEvent:
    if response_error is not None:
        message = _FakeElement(sub={"responseError": _FakeElement(string_value=response_error)})
    else:
        message = _FakeElement(sub={"securityData": _FakeElement(values=records)})
    return _FakeEvent(_EventType.RESPONSE, [message])


class _FakeRequest:
    def __init__(self):
        self.securities: list[str] = []
        self.fields: list[str] = []

    def append(self, name, value):
        if name == "securities":
            self.securities.append(value)
        elif name == "fields":
            self.fields.append(value)
        else:  # pragma: no cover - defensive
            raise AssertionError(f"unexpected append {name!r}")


class _FakeService:
    def __init__(self, session):
        self._session = session

    def createRequest(self, name):
        assert name == "ReferenceDataRequest"
        request = _FakeRequest()
        self._session.request = request
        return request


class _FakeSession:
    def __init__(self, options, harness):
        self.options = options
        self._harness = harness
        self.request = None
        self.stopped = False

    def start(self):
        return self._harness["start_result"]

    def openService(self, uri):
        return self._harness["open_service_result"]

    def getService(self, uri):
        return _FakeService(self)

    def sendRequest(self, request):
        pass

    def nextEvent(self, timeout_ms):
        security = self.request.securities[0]
        self._harness["requests"].append((security, list(self.request.fields)))
        responder = self._harness["responder"]
        return responder(security)

    def stop(self):
        self.stopped = True
        self._harness["stopped"].append(self)


def _install_fake_blpapi(monkeypatch, responder, *, start_result=True, open_service_result=True):
    harness = {
        "requests": [],
        "stopped": [],
        "responder": responder,
        "start_result": start_result,
        "open_service_result": open_service_result,
    }

    class _FakeSessionOptions:
        def setServerHost(self, host):
            self.host = host

        def setServerPort(self, port):
            self.port = port

    fake_module = type(sys)("blpapi")
    fake_module.SessionOptions = _FakeSessionOptions
    fake_module.Session = lambda options: _FakeSession(options, harness)
    fake_module.Event = _EventType
    fake_module.exception = _FakeBlpapiExceptionNamespace
    monkeypatch.setitem(sys.modules, "blpapi", fake_module)
    monkeypatch.setattr(module, "_monotonic", lambda: 0.0)
    return harness


def _two_stage_responder(
    *, generic_fields=None, stage_two_fields=None, generic=GENERIC_ZN, delivery=DELIVERY_ZN
):
    generic_fields = (
        {"FUT_CUR_GEN_TICKER": "TYU6"} if generic_fields is None else generic_fields
    )
    stage_two_fields = dict(LIVE_ZN_STAGE_TWO) if stage_two_fields is None else stage_two_fields

    def _respond(security):
        if security == generic:
            return _response_event([_security_data(security, generic_fields)])
        if security == delivery:
            return _response_event([_security_data(security, stage_two_fields)])
        raise AssertionError(f"unexpected security requested: {security!r}")

    return _respond


# ---------------------------------------------------------------------------
# The confirmed mapping
# ---------------------------------------------------------------------------


def test_every_required_field_has_a_confirmed_mnemonic() -> None:
    assert unresolved_bloomberg_ctd_fields() == ()


def test_the_confirmed_mnemonics_are_exactly_the_ones_eddy_verified() -> None:
    # Pinned as literals: these are the mnemonics confirmed against all four
    # active contracts. Re-pointing one is a market-data change, not a
    # refactor, and must fail here first.
    assert BLOOMBERG_CTD_FIELD_MAP == {
        "contract_symbol": "FUT_CUR_GEN_TICKER",
        "ctd_identifier": "FUT_CTD_ISIN",
        "ctd_coupon_percent": "FUT_CTD_CPN",
        "ctd_maturity_date": "FUT_CTD_MTY",
        "conversion_factor": "FUT_CNVS_FACTOR",
        "last_delivery_date": "FUT_DLV_DT_LAST",
    }


def test_cusip_and_ticker_are_display_only_never_the_identifier() -> None:
    assert BLOOMBERG_CTD_DISPLAY_FIELD_MAP == {
        "ctd_cusip": "FUT_CTD_CUSIP",
        "ctd_description": "FUT_CTD_TICKER",
    }
    # The canonical identifier is the ISIN, and no display mnemonic may also
    # be wired as a required field.
    assert BLOOMBERG_CTD_FIELD_MAP["ctd_identifier"] == "FUT_CTD_ISIN"
    assert not set(BLOOMBERG_CTD_DISPLAY_FIELD_MAP) & set(REQUIRED_BLOOMBERG_CTD_FIELDS)


def test_the_confirmed_ticker_roots_cover_the_four_mvp_contracts() -> None:
    assert BLOOMBERG_FUTURES_TICKER_ROOTS == {"ZT": "TU", "ZF": "FV", "ZN": "TY", "ZB": "US"}


@pytest.mark.parametrize(
    "contract_code, expected",
    [("ZT", "TU1 Comdty"), ("ZF", "FV1 Comdty"), ("ZN", "TY1 Comdty"), ("ZB", "US1 Comdty")],
)
def test_stage_one_asks_the_generic_front_contract(contract_code, expected) -> None:
    assert bloomberg_generic_front_contract(contract_code) == expected


def test_an_unsupported_contract_has_no_generic_ticker() -> None:
    with pytest.raises(TreasuryFuturesCTDError):
        bloomberg_generic_front_contract("ZQ")


@pytest.mark.parametrize(
    "symbol, expected",
    [
        ("TUU6", "TUU6 Comdty"),
        ("FVU6", "FVU6 Comdty"),
        ("TYU6 Comdty", "TYU6 Comdty"),  # already qualified, never doubled
        ("  USU6  ", "USU6 Comdty"),
    ],
)
def test_stage_two_qualifies_the_delivery_month_without_doubling(symbol, expected) -> None:
    assert bloomberg_delivery_month_security(symbol) == expected


# ---------------------------------------------------------------------------
# The live two-stage lookup
# ---------------------------------------------------------------------------


def test_a_successful_two_stage_load_returns_a_confirmed_record(monkeypatch) -> None:
    _install_fake_blpapi(monkeypatch, _two_stage_responder())
    ctd = load_bloomberg_ctd_metadata("ZN")

    assert ctd.contract_code == "ZN"
    assert ctd.contract_symbol == "TYU6"
    assert ctd.ctd_identifier == "US91282CQT17"
    assert ctd.ctd_coupon_percent == 4.25
    assert ctd.ctd_maturity_date == date(2033, 5, 31)
    assert ctd.conversion_factor == 0.9069
    assert ctd.last_delivery_date == date(2026, 9, 30)
    assert ctd.source is TreasuryFuturesCTDSource.BLOOMBERG_DAPI
    assert ctd.is_confirmed_source is True
    # Display extras carried, never used as the identifier.
    assert ctd.ctd_cusip == "91282CQT1"
    assert ctd.ctd_description == "T 4.25 05/31/33"


def test_the_two_stages_ask_the_right_securities_for_the_right_fields(monkeypatch) -> None:
    harness = _install_fake_blpapi(monkeypatch, _two_stage_responder())
    load_bloomberg_ctd_metadata("ZN")

    assert [security for security, _ in harness["requests"]] == [GENERIC_ZN, DELIVERY_ZN]
    generic_fields = harness["requests"][0][1]
    delivery_fields = harness["requests"][1][1]
    # Stage one asks for exactly the resolver field -- nothing else.
    assert generic_fields == ["FUT_CUR_GEN_TICKER"]
    # Stage two asks for every required CTD field plus the display extras,
    # and never re-asks for the resolver.
    assert set(delivery_fields) == set(LIVE_ZN_STAGE_TWO)
    assert "FUT_CUR_GEN_TICKER" not in delivery_fields
    # No field is requested twice in one call.
    assert len(delivery_fields) == len(set(delivery_fields))


def test_both_sessions_are_stopped(monkeypatch) -> None:
    harness = _install_fake_blpapi(monkeypatch, _two_stage_responder())
    load_bloomberg_ctd_metadata("ZN")
    assert len(harness["stopped"]) == 2
    assert all(session.stopped for session in harness["stopped"])


def test_the_as_of_is_an_acquisition_instant_not_a_market_timestamp(monkeypatch) -> None:
    _install_fake_blpapi(monkeypatch, _two_stage_responder())
    ctd = load_bloomberg_ctd_metadata("ZN")
    assert ctd.as_of.endswith("Z")
    assert "T" in ctd.as_of


@pytest.mark.parametrize("contract_code", ["zn", " ZN ", "ZN"])
def test_the_contract_code_is_normalized(monkeypatch, contract_code) -> None:
    _install_fake_blpapi(monkeypatch, _two_stage_responder())
    assert load_bloomberg_ctd_metadata(contract_code).contract_code == "ZN"


def test_an_unsupported_contract_never_reaches_bloomberg(monkeypatch) -> None:
    harness = _install_fake_blpapi(monkeypatch, _two_stage_responder())
    with pytest.raises(TreasuryFuturesCTDError):
        load_bloomberg_ctd_metadata("ZQ")
    assert harness["requests"] == []


# ---------------------------------------------------------------------------
# Fail-closed on the live response
# ---------------------------------------------------------------------------


def test_blpapi_not_installed_is_reported_as_a_workstation_prerequisite(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "blpapi", None)
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        load_bloomberg_ctd_metadata("ZN")
    assert "blpapi is not installed" in str(exc.value)


@pytest.mark.parametrize("generic_fields", [{}, {"FUT_CUR_GEN_TICKER": ""}])
def test_a_missing_delivery_month_fails_closed(monkeypatch, generic_fields) -> None:
    _install_fake_blpapi(monkeypatch, _two_stage_responder(generic_fields=generic_fields))
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        load_bloomberg_ctd_metadata("ZN")
    assert "FUT_CUR_GEN_TICKER" in str(exc.value)


@pytest.mark.parametrize(
    "missing_field",
    ["FUT_CTD_ISIN", "FUT_CTD_CPN", "FUT_CTD_MTY", "FUT_CNVS_FACTOR", "FUT_DLV_DT_LAST"],
)
def test_a_missing_required_ctd_field_fails_closed(monkeypatch, missing_field) -> None:
    fields = dict(LIVE_ZN_STAGE_TWO)
    fields.pop(missing_field)
    _install_fake_blpapi(monkeypatch, _two_stage_responder(stage_two_fields=fields))
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        load_bloomberg_ctd_metadata("ZN")
    assert missing_field in str(exc.value)


@pytest.mark.parametrize(
    "missing_field",
    ["FUT_CTD_ISIN", "FUT_CTD_CPN", "FUT_CTD_MTY", "FUT_CNVS_FACTOR", "FUT_DLV_DT_LAST"],
)
def test_a_blank_required_ctd_field_is_as_missing_as_an_absent_one(
    monkeypatch, missing_field
) -> None:
    fields = dict(LIVE_ZN_STAGE_TWO, **{missing_field: ""})
    _install_fake_blpapi(monkeypatch, _two_stage_responder(stage_two_fields=fields))
    with pytest.raises(TreasuryFuturesCTDBloombergError):
        load_bloomberg_ctd_metadata("ZN")


def test_a_display_only_field_is_optional_and_never_blocks_the_load(monkeypatch) -> None:
    fields = dict(LIVE_ZN_STAGE_TWO)
    fields.pop("FUT_CTD_CUSIP")
    fields.pop("FUT_CTD_TICKER")
    _install_fake_blpapi(monkeypatch, _two_stage_responder(stage_two_fields=fields))
    ctd = load_bloomberg_ctd_metadata("ZN")
    assert ctd.ctd_cusip is None
    assert ctd.ctd_description is None
    assert ctd.ctd_identifier == "US91282CQT17"  # the answer is unaffected


@pytest.mark.parametrize("field", ["FUT_CTD_CPN", "FUT_CNVS_FACTOR"])
@pytest.mark.parametrize("raw", ["n/a", "#N/A Field Not Applicable", "", "1.2.3"])
def test_a_non_numeric_number_fails_closed(monkeypatch, field, raw) -> None:
    fields = dict(LIVE_ZN_STAGE_TWO, **{field: raw})
    _install_fake_blpapi(monkeypatch, _two_stage_responder(stage_two_fields=fields))
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        load_bloomberg_ctd_metadata("ZN")
    assert field in str(exc.value)


@pytest.mark.parametrize("field", ["FUT_CTD_MTY", "FUT_DLV_DT_LAST"])
@pytest.mark.parametrize("raw", ["05/31/2033", "20330531", "31-MAY-2033", "not-a-date"])
def test_a_date_in_any_other_format_fails_closed_rather_than_being_guessed(
    monkeypatch, field, raw
) -> None:
    # A misread maturity or delivery date moves every number the converter
    # produces, so an unrecognized format is refused, never inferred.
    fields = dict(LIVE_ZN_STAGE_TWO, **{field: raw})
    _install_fake_blpapi(monkeypatch, _two_stage_responder(stage_two_fields=fields))
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        load_bloomberg_ctd_metadata("ZN")
    assert field in str(exc.value)


@pytest.mark.parametrize(
    "overrides, expected_fragment",
    [
        ({"FUT_CNVS_FACTOR": "0"}, "non-positive conversion factor"),
        ({"FUT_CNVS_FACTOR": "-0.9"}, "non-positive conversion factor"),
        ({"FUT_CTD_CPN": "-1.0"}, "negative coupon"),
        ({"FUT_DLV_DT_LAST": "2033-05-31"}, "on or after"),
        ({"FUT_DLV_DT_LAST": "2040-01-31"}, "on or after"),
    ],
)
def test_a_live_response_is_held_to_the_same_domain_rules_as_manual_entry(
    monkeypatch, overrides, expected_fragment
) -> None:
    fields = dict(LIVE_ZN_STAGE_TWO, **overrides)
    _install_fake_blpapi(monkeypatch, _two_stage_responder(stage_two_fields=fields))
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        load_bloomberg_ctd_metadata("ZN")
    assert expected_fragment in str(exc.value)


def test_a_session_that_cannot_start_fails_closed(monkeypatch) -> None:
    _install_fake_blpapi(monkeypatch, _two_stage_responder(), start_result=False)
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        load_bloomberg_ctd_metadata("ZN")
    assert "session failed to start" in str(exc.value)


def test_a_service_that_cannot_be_opened_fails_closed(monkeypatch) -> None:
    _install_fake_blpapi(monkeypatch, _two_stage_responder(), open_service_result=False)
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        load_bloomberg_ctd_metadata("ZN")
    assert "failed to open service" in str(exc.value)


def test_a_response_error_fails_closed_and_stops_the_session(monkeypatch) -> None:
    harness = _install_fake_blpapi(
        monkeypatch, lambda security: _response_event([], response_error="Bad request")
    )
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        load_bloomberg_ctd_metadata("ZN")
    assert "responseError" in str(exc.value)
    assert harness["stopped"]


def test_a_security_error_fails_closed(monkeypatch) -> None:
    def _respond(security):
        return _response_event(
            [_security_data(security, {}, security_error="Unknown security")]
        )

    _install_fake_blpapi(monkeypatch, _respond)
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        load_bloomberg_ctd_metadata("ZN")
    assert "securityError" in str(exc.value)


@pytest.mark.parametrize("record_count", [0, 2])
def test_anything_other_than_exactly_one_record_fails_closed(monkeypatch, record_count) -> None:
    def _respond(security):
        record = _security_data(security, {"FUT_CUR_GEN_TICKER": "TYU6"})
        return _response_event([record] * record_count)

    _install_fake_blpapi(monkeypatch, _respond)
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        load_bloomberg_ctd_metadata("ZN")
    assert "expected exactly one" in str(exc.value)


def test_a_record_for_a_different_security_fails_closed(monkeypatch) -> None:
    def _respond(security):
        # Bloomberg answers about a security nobody asked for.
        return _response_event(
            [_security_data("SOMETHING ELSE", {"FUT_CUR_GEN_TICKER": "TYU6"})]
        )

    _install_fake_blpapi(monkeypatch, _respond)
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        load_bloomberg_ctd_metadata("ZN")
    assert "does not match requested identifier" in str(exc.value)


def test_a_record_missing_its_own_identifier_fails_closed(monkeypatch) -> None:
    def _respond(security):
        return _response_event(
            [_security_data(security, {"FUT_CUR_GEN_TICKER": "TYU6"}, omit_security=True)]
        )

    _install_fake_blpapi(monkeypatch, _respond)
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        load_bloomberg_ctd_metadata("ZN")
    assert "missing its own" in str(exc.value)


def test_a_malformed_element_fails_closed(monkeypatch) -> None:
    fields = dict(LIVE_ZN_STAGE_TWO, FUT_CNVS_FACTOR=_FakeBlpapiException("bad conversion"))
    _install_fake_blpapi(monkeypatch, _two_stage_responder(stage_two_fields=fields))
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        load_bloomberg_ctd_metadata("ZN")
    assert "malformed value" in str(exc.value)


def test_a_timeout_fails_closed(monkeypatch) -> None:
    _install_fake_blpapi(monkeypatch, lambda security: _FakeEvent(_EventType.TIMEOUT))
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        load_bloomberg_ctd_metadata("ZN")
    assert "timed out" in str(exc.value)


def test_the_live_path_never_falls_back_to_manual_or_cached_data(monkeypatch) -> None:
    # Every failure above raises. None returns a partial or substituted
    # record -- there is no contract cache in this repository to fall back to.
    _install_fake_blpapi(monkeypatch, _two_stage_responder(stage_two_fields={}))
    with pytest.raises(TreasuryFuturesCTDBloombergError):
        load_bloomberg_ctd_metadata("ZN")


# ---------------------------------------------------------------------------
# Manual entry
# ---------------------------------------------------------------------------


def test_a_complete_manual_entry_is_accepted_and_typed() -> None:
    ctd = treasury_futures_ctd_from_manual_entry(dict(VALID_ENTRY))
    assert ctd.contract_code == "ZN"
    assert ctd.ctd_coupon_percent == 4.25
    assert ctd.ctd_maturity_date == date(2033, 5, 31)
    assert ctd.conversion_factor == 0.9069
    assert ctd.last_delivery_date == date(2026, 9, 30)


def test_manual_entry_is_always_reported_as_an_unconfirmed_source() -> None:
    ctd = treasury_futures_ctd_from_manual_entry(dict(VALID_ENTRY))
    assert ctd.source is TreasuryFuturesCTDSource.MANUAL_UNCONFIRMED
    assert ctd.is_confirmed_source is False
    assert ctd.as_display_payload()["is_confirmed_source"] is False


def test_the_source_cannot_be_claimed_as_confirmed_by_the_caller() -> None:
    ctd = treasury_futures_ctd_from_manual_entry(
        dict(VALID_ENTRY, source="BLOOMBERG_DAPI", is_confirmed_source=True)
    )
    assert ctd.source is TreasuryFuturesCTDSource.MANUAL_UNCONFIRMED
    assert ctd.is_confirmed_source is False


def test_manual_entry_may_carry_the_display_extras_but_does_not_require_them() -> None:
    bare = treasury_futures_ctd_from_manual_entry(dict(VALID_ENTRY))
    assert bare.ctd_cusip is None and bare.ctd_description is None
    filled = treasury_futures_ctd_from_manual_entry(
        dict(VALID_ENTRY, ctd_cusip="91282CQT1", ctd_description="T 4.25 05/31/33")
    )
    assert filled.ctd_cusip == "91282CQT1"
    assert filled.ctd_description == "T 4.25 05/31/33"


def test_the_display_payload_carries_the_full_ctd_small_print() -> None:
    payload = treasury_futures_ctd_from_manual_entry(dict(VALID_ENTRY)).as_display_payload()
    assert set(payload) == {
        "contract_code",
        "contract_symbol",
        "ctd_identifier",
        "ctd_cusip",
        "ctd_description",
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
        {"ctd_maturity_date": "31/05/2033"},
        {"ctd_maturity_date": "20330531"},
        {"last_delivery_date": "not-a-date"},
        {"contract_symbol": "   "},
        {"ctd_identifier": ""},
    ],
)
def test_a_structurally_invalid_field_is_rejected(overrides) -> None:
    with pytest.raises(TreasuryFuturesCTDError):
        treasury_futures_ctd_from_manual_entry(dict(VALID_ENTRY, **overrides))


def test_a_delivery_date_at_or_after_the_ctd_maturity_is_rejected() -> None:
    for bad in ("2033-05-31", "2035-01-31"):
        with pytest.raises(TreasuryFuturesCTDError):
            treasury_futures_ctd_from_manual_entry(dict(VALID_ENTRY, last_delivery_date=bad))


def test_a_non_object_payload_is_rejected() -> None:
    for payload in (None, [], "ZN", 4.25):
        with pytest.raises(TreasuryFuturesCTDError):
            treasury_futures_ctd_from_manual_entry(payload)
