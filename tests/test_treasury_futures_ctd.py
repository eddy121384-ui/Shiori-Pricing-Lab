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

# All four contracts as Eddy's live run returned them, keyed by contract code.
# Pinned as literals: these are real confirmed records, and the
# remaining-maturity guard must never reject one of them.
LIVE_STAGE_TWO: dict[str, dict[str, str]] = {
    "ZT": {
        "FUT_CTD_ISIN": "US91282CHK09",
        "FUT_CTD_CUSIP": "91282CHK0",
        "FUT_CTD_TICKER": "T 4 06/30/28",
        "FUT_CTD_CPN": "4.000000",
        "FUT_CTD_MTY": "2028-06-30",
        "FUT_CNVS_FACTOR": "0.967200",
        "FUT_DLV_DT_LAST": "2026-10-05",
    },
    "ZF": {
        "FUT_CTD_ISIN": "US91282CPN55",
        "FUT_CTD_CUSIP": "91282CPN5",
        "FUT_CTD_TICKER": "T 3.5 11/30/30",
        "FUT_CTD_CPN": "3.500000",
        "FUT_CTD_MTY": "2030-11-30",
        "FUT_CNVS_FACTOR": "0.909000",
        "FUT_DLV_DT_LAST": "2026-10-05",
    },
    "ZN": dict(LIVE_ZN_STAGE_TWO),
    "ZB": {
        "FUT_CTD_ISIN": "US912810UL07",
        "FUT_CTD_CUSIP": "912810UL0",
        "FUT_CTD_TICKER": "T 5 05/15/45",
        "FUT_CTD_CPN": "5.000000",
        "FUT_CTD_MTY": "2045-05-15",
        "FUT_CNVS_FACTOR": "0.889200",
        "FUT_DLV_DT_LAST": "2026-09-30",
    },
}
LIVE_DELIVERY_SYMBOL = {"ZT": "TUU6", "ZF": "FVU6", "ZN": "TYU6", "ZB": "USU6"}


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
@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n "])
def test_a_blank_required_ctd_field_is_as_missing_as_an_absent_one(
    monkeypatch, missing_field, blank
) -> None:
    """Codex review, PR #191 (P1).

    Whitespace is the dangerous half: it survives a truthiness check, so
    before this it reached the record as an empty identifier on an otherwise
    `is_confirmed_source: true` result -- a live-confirmed record naming no
    bond at all.
    """

    fields = dict(LIVE_ZN_STAGE_TWO, **{missing_field: blank})
    _install_fake_blpapi(monkeypatch, _two_stage_responder(stage_two_fields=fields))
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        load_bloomberg_ctd_metadata("ZN")
    assert missing_field in str(exc.value)


@pytest.mark.parametrize(
    "bogus_identifier",
    [
        "#N/A N/A",          # a Bloomberg sentinel
        "#N/A Field Not Applicable",
        "nope",              # too short
        "US91282CQT1",       # 11 characters
        "US91282CQT178",     # 13 characters
        "US91282CQT1!",      # non-alphanumeric
        "US91282C QT17",     # embedded space
        # Codex review, PR #191 (second round): 12 alphanumeric characters is
        # not enough. These all pass a shape check.
        "US91282CQT18",      # transposed/mistyped check digit
        "US91282CQT10",
        "US91282CQT16",
        "DE91282CQT17",      # a non-U.S. country prefix
        "GB0002634946",      # a real, checksum-valid, non-U.S. ISIN
    ],
)
def test_an_identifier_that_is_not_a_valid_us_isin_is_refused(
    monkeypatch, bogus_identifier
) -> None:
    """Codex review, PR #191 (P1, then P2).

    A present-but-meaningless identifier must never reach a confirmed-source
    record. Shape alone is not enough: a wrong check digit or a non-U.S.
    country prefix both pass 12-alphanumeric, and the CTD of a U.S. Treasury
    futures contract is by definition a checksum-valid U.S. Treasury ISIN.
    """

    fields = dict(LIVE_ZN_STAGE_TWO, FUT_CTD_ISIN=bogus_identifier)
    _install_fake_blpapi(monkeypatch, _two_stage_responder(stage_two_fields=fields))
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        load_bloomberg_ctd_metadata("ZN")
    assert "FUT_CTD_ISIN" in str(exc.value)


@pytest.mark.parametrize(
    "live_isin",
    ["US91282CHK09", "US91282CPN55", "US91282CQT17", "US912810UL07"],
)
def test_every_isin_the_live_run_returned_passes_the_checksum(monkeypatch, live_isin) -> None:
    """The guard against getting the ISO 6166 parity backwards.

    Doubling from the wrong digit makes *every* real ISIN look invalid, which
    would fail every live load closed. These are the four CTD ISINs Eddy's
    workstation run actually returned; all four must pass.
    """

    fields = dict(LIVE_ZN_STAGE_TWO, FUT_CTD_ISIN=live_isin)
    _install_fake_blpapi(monkeypatch, _two_stage_responder(stage_two_fields=fields))
    assert load_bloomberg_ctd_metadata("ZN").ctd_identifier == live_isin


@pytest.mark.parametrize(
    "resolved",
    [
        "USU6",     # a different contract's delivery month entirely
        "FVU6",
        "GARBAGE",
        "TY6",      # no delivery-month letter
        "TYU",      # no year digits
        "TY",       # bare root
        "TYUX",     # non-numeric year
        # Codex review, PR #191: standard futures months these contracts do
        # not list. The full twelve-month alphabet admitted these.
        "TYF7",     # January
        "TYG7",     # February
        "TYJ7",     # April
        "TYN6",     # July
        "TYV6",     # October
        # An unbounded run of year digits is a malformed answer, not a wider
        # contract. Left unbounded it makes the delivery year unresolvable and
        # the load raises a bare ValueError instead of failing closed.
        "TYU202699",
    ],
)
def test_a_delivery_month_that_is_not_this_contracts_is_refused(monkeypatch, resolved) -> None:
    """Codex review, PR #191 (P1).

    Stage one asks `TY1 Comdty` which delivery month it is. Any other answer
    would send stage two to a different contract, whose perfectly valid CTD
    would come back labelled ZN -- so pricing would apply ZN's quote
    convention to another contract's CTD metadata.
    """

    _install_fake_blpapi(
        monkeypatch,
        _two_stage_responder(
            generic_fields={"FUT_CUR_GEN_TICKER": resolved},
            delivery=f"{resolved} Comdty",
        ),
    )
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        load_bloomberg_ctd_metadata("ZN")
    assert "not a ZN delivery month" in str(exc.value)


@pytest.mark.parametrize(
    "contract_code, resolved, maturity, last_delivery",
    [
        # The four Eddy's live run returned, with their own real CTDs.
        ("ZT", "TUU6", "2028-06-30", "2026-10-05"),
        ("ZF", "FVU6", "2030-11-30", "2026-10-05"),
        ("ZN", "TYU6", "2033-05-31", "2026-09-30"),
        ("ZB", "USU6", "2045-05-15", "2026-09-30"),
        # The rest of the quarterly cycle these contracts list, so the guard
        # cannot be narrowed to the one month the live run happened to be in.
        # Each maturity is hand-computed to sit inside that month's window.
        ("ZN", "TYH7", "2034-05-15", "2027-03-31"),  # ref 2027-03-01
        ("ZN", "TYM7", "2034-05-15", "2027-06-30"),  # ref 2027-06-01
        ("ZN", "TYZ6", "2034-05-15", "2026-12-31"),  # ref 2026-12-01
        ("ZB", "USH7", "2045-05-15", "2027-03-31"),  # ref 2027-03-01
        # A Z contract whose last delivery day falls in *January of the next
        # year*: the delivery month is still 2026-12, so the year digit must be
        # resolved against the nearest matching year, not read off the last
        # delivery date's year.
        ("ZT", "TUZ6", "2028-10-31", "2027-01-05"),  # ref 2026-12-01
        # Bloomberg's two-digit year convention, so the digit-count cap that
        # keeps the year resolvable cannot be tightened to one digit only.
        ("ZN", "TYU26", "2033-05-31", "2026-09-30"),  # ref 2026-09-01
    ],
)
def test_each_contracts_own_delivery_month_is_accepted(
    monkeypatch, contract_code, resolved, maturity, last_delivery
) -> None:
    fields = dict(
        LIVE_STAGE_TWO[contract_code],
        FUT_CTD_MTY=maturity,
        FUT_DLV_DT_LAST=last_delivery,
    )
    _install_fake_blpapi(
        monkeypatch,
        _two_stage_responder(
            generic_fields={"FUT_CUR_GEN_TICKER": resolved},
            stage_two_fields=fields,
            generic=bloomberg_generic_front_contract(contract_code),
            delivery=f"{resolved} Comdty",
        ),
    )
    ctd = load_bloomberg_ctd_metadata(contract_code)
    assert ctd.contract_symbol == resolved
    assert ctd.contract_code == contract_code


# ---------------------------------------------------------------------------
# Remaining-maturity plausibility / cross-contract guard
# ---------------------------------------------------------------------------


def _load_with(monkeypatch, contract_code, *, symbol=None, stage_two=None):
    """Run the live loader for one contract with a chosen stage-two payload."""

    resolved = symbol or LIVE_DELIVERY_SYMBOL[contract_code]
    _install_fake_blpapi(
        monkeypatch,
        _two_stage_responder(
            generic_fields={"FUT_CUR_GEN_TICKER": resolved},
            stage_two_fields=dict(stage_two or LIVE_STAGE_TWO[contract_code]),
            generic=bloomberg_generic_front_contract(contract_code),
            delivery=f"{resolved} Comdty",
        ),
    )
    return load_bloomberg_ctd_metadata(contract_code)


@pytest.mark.parametrize("contract_code", ["ZT", "ZF", "ZN", "ZB"])
def test_every_confirmed_live_ctd_passes_the_remaining_maturity_guard(
    monkeypatch, contract_code
) -> None:
    """Eddy's four confirmed live CTDs must all load.

    The guard exists to reject another contract's CTD, and a guard that
    rejects real data is an outage, not a safeguard. These four are the
    evidence that the window bounds and the measurement basis are right.
    """

    ctd = _load_with(monkeypatch, contract_code)
    assert ctd.contract_code == contract_code
    assert ctd.ctd_identifier == LIVE_STAGE_TWO[contract_code]["FUT_CTD_ISIN"]


@pytest.mark.parametrize(
    "contract_code, last_delivery_month",
    [("ZT", 10), ("ZF", 10)],
)
def test_the_window_is_measured_from_the_delivery_month_not_the_last_delivery_day(
    monkeypatch, contract_code, last_delivery_month
) -> None:
    """The heart of Eddy's methodology decision (Issue #190).

    ZT and ZF deliver in September but their last delivery day falls in
    October. Measuring the window from `FUT_DLV_DT_LAST` therefore shifts it a
    month later and rejects both of these real CTDs. Measured from the first
    day of the delivery month the symbol names, both load.
    """

    live = LIVE_STAGE_TWO[contract_code]
    assert date.fromisoformat(live["FUT_DLV_DT_LAST"]).month == last_delivery_month
    assert LIVE_DELIVERY_SYMBOL[contract_code][2] == "U"  # September delivery

    ctd = _load_with(monkeypatch, contract_code)
    assert ctd.ctd_identifier == live["FUT_CTD_ISIN"]


def test_the_codex_counterexample_fails_closed(monkeypatch) -> None:
    """Codex review, PR #191 (P1).

    A ZN/`TYU6` request answered with the confirmed ZB CTD: checksum-valid
    identifier, sane numbers, delivery before maturity. Every other guard
    passes it, and it would have been stamped as a confirmed ZN record and
    priced on ZN's 1/64 tick.
    """

    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        _load_with(monkeypatch, "ZN", stage_two=LIVE_STAGE_TWO["ZB"])
    message = str(exc.value)
    assert "2045-05-15" in message
    assert "ZN's remaining-maturity window" in message
    assert "2026-09-01" in message  # first day of the TYU6 delivery month


@pytest.mark.parametrize("requested", ["ZT", "ZF", "ZN", "ZB"])
@pytest.mark.parametrize("donor", ["ZT", "ZF", "ZN", "ZB"])
def test_cross_substituted_live_ctds_fail_closed(monkeypatch, requested, donor) -> None:
    """Every off-diagonal pairing of the four real CTDs must be refused.

    Each of these is a coherent, checksum-valid, internally consistent record
    -- just the wrong contract's. The diagonal must still load, so the guard
    cannot pass this by rejecting everything.
    """

    donor_fields = dict(
        LIVE_STAGE_TWO[donor],
        # Keep the requested contract's own delivery date, so the only thing
        # that differs is the maturity being tested.
        FUT_DLV_DT_LAST=LIVE_STAGE_TWO[requested]["FUT_DLV_DT_LAST"],
    )
    if requested == donor:
        assert _load_with(monkeypatch, requested, stage_two=donor_fields) is not None
        return

    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        _load_with(monkeypatch, requested, stage_two=donor_fields)
    assert f"{requested}'s remaining-maturity window" in str(exc.value)


@pytest.mark.parametrize(
    "contract_code, maturity, accepted",
    [
        # ZN/TYU6, reference 2026-09-01, window [2033-03-01, 2036-09-01].
        ("ZN", "2033-02-28", False),  # one day below the lower bound
        ("ZN", "2033-03-01", True),  # exactly the lower bound, inclusive
        ("ZN", "2036-09-01", True),  # exactly the upper bound, inclusive
        ("ZN", "2036-09-02", False),  # one day above the upper bound
    ],
)
def test_zn_window_boundaries_measured_from_the_first_of_the_delivery_month(
    monkeypatch, contract_code, maturity, accepted
) -> None:
    fields = dict(LIVE_STAGE_TWO[contract_code], FUT_CTD_MTY=maturity)
    if accepted:
        assert _load_with(monkeypatch, contract_code, stage_two=fields) is not None
    else:
        with pytest.raises(TreasuryFuturesCTDBloombergError):
            _load_with(monkeypatch, contract_code, stage_two=fields)


@pytest.mark.parametrize(
    "maturity, accepted",
    [
        # ZB/USU6, reference 2026-09-01, window [2041-09-01, 2051-09-01).
        ("2041-08-31", False),  # one day below the lower bound
        ("2041-09-01", True),  # exactly the lower bound, inclusive
        ("2051-08-31", True),  # last day inside the exclusive upper bound
        ("2051-09-01", False),  # exactly 25 years -- excluded
    ],
)
def test_zb_upper_bound_is_exclusive_at_twenty_five_years(
    monkeypatch, maturity, accepted
) -> None:
    """ZB is `at least 15 years and less than 25 years`, so 25y exactly is out."""

    fields = dict(LIVE_STAGE_TWO["ZB"], FUT_CTD_MTY=maturity)
    if accepted:
        assert _load_with(monkeypatch, "ZB", stage_two=fields) is not None
    else:
        with pytest.raises(TreasuryFuturesCTDBloombergError):
            _load_with(monkeypatch, "ZB", stage_two=fields)


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
