"""Tests for `data/bloomberg_option_discount_curve.py` (Issue #165 production phase).

Covers: (1) explicit curve configuration -- USD SOFR -> curve number "0490",
never a generic currency-keyed lookup; (2) explicit, caller-supplied tenor
set, never guessed, validated via the existing `tenor_to_year_fraction`
grammar before any Bloomberg request is sent; (3) exactly `LAST_PRICE`/
`MATURITY` requested for both the `Z` and `D` synthetic security at every
tenor; (4) percent-to-decimal conversion for the `Z` (zero-rate) ticker
only, never the `D` (discount-factor) ticker; (5) `MATURITY` used as the
curve node date, driving the duplicate/non-increasing fail-closed check;
(6) `OPTION_DISCOUNT_CURVE`/`CONTINUOUS_ZERO_RATE` `BLICurvePoint` output;
(7) `D` values preserved as evidence only, never consumed into a curve
point; (8) every fail-closed condition from Eddy's own list (BAD_SEC,
missing LAST_PRICE/MATURITY, duplicate/non-increasing maturity, non-finite
zero rate, non-positive/non-finite DF); (9) a discount factor is never
required to be <= 1.

No network access, no real `blpapi`, no system clock: Bloomberg is faked
with the same minimal stand-in objects `tests/test_bloomberg_bond_quote.py`
already established, injected via `sys.modules["blpapi"]` so this loader's
lazy `import blpapi` picks them up.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

import pytest

from shiori_pricing_lab.data import bloomberg_option_discount_curve as module
from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIMarketDataStatus,
)
from shiori_pricing_lab.data.bloomberg_bond_quote import BLIBloombergDapiError
from shiori_pricing_lab.data.bloomberg_option_discount_curve import (
    DEFAULT_USD_SOFR_TENORS,
    USD_SOFR_BLOOMBERG_CURVE_NUMBER,
    USD_SOFR_CURVE_ID,
    USD_SOFR_CURVE_NAME,
    build_discount_factor_ticker,
    build_zero_rate_ticker,
    load_bloomberg_usd_sofr_option_discount_curve,
)
from shiori_pricing_lab.pricing.bli_curve_tenor import tenor_to_year_fraction
from shiori_pricing_lab.products.enums import Currency

_ZERO_1Y = "S0490Z 1Y BLC2 Curncy"
_ZERO_2Y = "S0490Z 2Y BLC2 Curncy"
_DF_1Y = "S0490D 1Y BLC2 Curncy"
_DF_2Y = "S0490D 2Y BLC2 Curncy"


# --- Fake blpapi surface -----------------------------------------------------
# Mirrors only the calls the loader actually makes -- same shape as
# tests/test_bloomberg_bond_quote.py's own fake harness.


class _FakeBlpapiException(Exception):
    pass


class _FakeBlpapiExceptionNamespace:
    Exception = _FakeBlpapiException


class _FakeElement:
    def __init__(self, sub_elements=None, values=None, string_value=None, raise_on_value=None):
        self._sub = sub_elements or {}
        self._values = values
        self._string_value = string_value
        self._raise_on_value = raise_on_value

    def hasElement(self, name):
        return name in self._sub

    def getElement(self, name):
        return self._sub[name]

    def getElementAsString(self, name):
        return self._sub[name].getValueAsString()

    def getValueAsString(self):
        if self._raise_on_value is not None:
            raise self._raise_on_value
        return self._string_value

    def numValues(self):
        return len(self._values or [])

    def getValueAsElement(self, index):
        return self._values[index]

    def __str__(self):
        return self._string_value or "<element>"


def _leaf(value) -> _FakeElement:
    if isinstance(value, BaseException):
        return _FakeElement(raise_on_value=value)
    return _FakeElement(string_value=value)


def _field_data(fields: dict) -> _FakeElement:
    return _FakeElement(sub_elements={name: _leaf(value) for name, value in fields.items()})


def _security_record(
    *,
    security,
    omit_security=False,
    last_price=None,
    maturity=None,
    security_error=None,
    field_exceptions=None,
) -> _FakeElement:
    fields = {}
    if last_price is not None:
        fields["LAST_PRICE"] = last_price
    if maturity is not None:
        fields["MATURITY"] = maturity
    sub = {"fieldData": _field_data(fields)}
    if not omit_security:
        sub["security"] = _leaf(security)
    if security_error is not None:
        sub["securityError"] = _FakeElement(string_value=security_error)
    if field_exceptions is not None:
        sub["fieldExceptions"] = _FakeElement(
            values=[_FakeElement(string_value=fe) for fe in field_exceptions]
        )
    return _FakeElement(sub_elements=sub)


class _EventType:
    TIMEOUT = "TIMEOUT"
    PARTIAL_RESPONSE = "PARTIAL_RESPONSE"
    RESPONSE = "RESPONSE"


class _FakeEvent:
    def __init__(self, event_type, messages=()):
        self._event_type = event_type
        self._messages = list(messages)

    def eventType(self):
        return self._event_type

    def __iter__(self):
        return iter(self._messages)


def _response_message(*, records=None, response_error=None) -> _FakeElement:
    if response_error is not None:
        return _FakeElement(
            sub_elements={"responseError": _FakeElement(string_value=response_error)}
        )
    return _FakeElement(sub_elements={"securityData": _FakeElement(values=list(records or []))})


def _response_event(records) -> _FakeEvent:
    return _FakeEvent(_EventType.RESPONSE, [_response_message(records=records)])


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
            raise AssertionError(f"unexpected append field {name!r}")


class _FakeService:
    def __init__(self, session):
        self._session = session

    def createRequest(self, name):
        assert name == "ReferenceDataRequest"
        request = _FakeRequest()
        self._session.last_request = request
        return request


class _FakeSession:
    def __init__(self, options, *, start_result, open_service_result, events):
        self.options = options
        self._start_result = start_result
        self._open_service_result = open_service_result
        self._events = list(events)
        self.stopped = False
        self.last_request = None

    def start(self):
        return self._start_result

    def openService(self, uri):
        return self._open_service_result

    def getService(self, uri):
        return _FakeService(self)

    def sendRequest(self, request):
        pass

    def nextEvent(self, timeout_ms):
        if not self._events:
            raise AssertionError("fake session ran out of queued events")
        return self._events.pop(0)

    def stop(self):
        self.stopped = True


class _FakeSessionOptions:
    def setServerHost(self, host):
        self.host = host

    def setServerPort(self, port):
        self.port = port


def _install_fake_blpapi(monkeypatch, *, start_result=True, open_service_result=True, events=()):
    holder: dict = {}

    def _session_factory(options):
        session = _FakeSession(
            options,
            start_result=start_result,
            open_service_result=open_service_result,
            events=list(events),
        )
        holder["session"] = session
        return session

    fake_module = type(sys)("blpapi")
    fake_module.SessionOptions = _FakeSessionOptions
    fake_module.Session = _session_factory
    fake_module.Event = _EventType
    fake_module.exception = _FakeBlpapiExceptionNamespace

    monkeypatch.setitem(sys.modules, "blpapi", fake_module)
    monkeypatch.setattr(module, "_monotonic", lambda: 0.0)
    return holder


def _both_tenors_success(
    *,
    zero_1y="1.75",
    zero_2y="3.25",
    df_1y="0.98",
    df_2y="0.94",
    maturity_1y="2027-08-10",
    maturity_2y="2028-08-10",
):
    records = [
        _security_record(security=_ZERO_1Y, last_price=zero_1y, maturity=maturity_1y),
        _security_record(security=_ZERO_2Y, last_price=zero_2y, maturity=maturity_2y),
        _security_record(security=_DF_1Y, last_price=df_1y, maturity=maturity_1y),
        _security_record(security=_DF_2Y, last_price=df_2y, maturity=maturity_2y),
    ]
    return [_response_event(records)]


# --- input validation (no blpapi needed) --------------------------------------------


def test_rejects_empty_tenors():
    with pytest.raises(ValueError, match="must not be empty"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=())


def test_rejects_a_blank_tenor():
    with pytest.raises(ValueError, match="non-blank"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y", "   "))


def test_rejects_a_duplicate_tenor():
    with pytest.raises(ValueError, match="duplicate tenor"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y", "1Y"))


@pytest.mark.parametrize("malformed_tenor", ["1D", "1w", "0W", "1Q", "1 W", "abc", "12 MO"])
def test_rejects_a_malformed_tenor_before_any_bloomberg_request(malformed_tenor):
    # No blpapi installed in this test process at all -- if this loader's
    # own local acquisition-label validation did not fail closed before
    # ever reaching Bloomberg, the lazy `import blpapi` a few lines later
    # would raise a different error (or, worse, a fake session might be
    # hit); "unsupported tenor label" proves rejection happens first, pre-DAPI.
    with pytest.raises(ValueError, match="unsupported tenor label"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=(malformed_tenor,))


_EXPECTED_DEFAULT_USD_SOFR_TENORS = (
    "1W",
    "2W",
    "3W",
    "1M",
    "2M",
    "3M",
    "4M",
    "5M",
    "6M",
    "7M",
    "8M",
    "9M",
    "10M",
    "11M",
    "1Y",
    "18M",
    "2Y",
    "3Y",
    "4Y",
    "5Y",
    "6Y",
    "7Y",
    "8Y",
    "9Y",
    "10Y",
    "12Y",
    "15Y",
    "20Y",
    "25Y",
    "30Y",
    "40Y",
    "50Y",
)


def test_default_tenors_are_eddys_workstation_verified_curve_490_universe():
    # Pinned exactly to the full 32-tenor Bloomberg Curve Construction
    # export (Issue #165 tenor decoupling restored "1W"/"2W"/"3W" to the
    # default), and "1Y" is kept rather than the export's own "12 MO"
    # spelling (Eddy separately confirmed the "1Y" ticker resolves).
    assert DEFAULT_USD_SOFR_TENORS == _EXPECTED_DEFAULT_USD_SOFR_TENORS
    assert len(DEFAULT_USD_SOFR_TENORS) == 32


def test_default_tenors_include_the_literal_week_labels_first():
    # "1W"/"2W"/"3W" are restored to the default, in that literal Bloomberg
    # spelling -- never a substituted "7D"/"14D"/"21D" day-count label.
    assert DEFAULT_USD_SOFR_TENORS[:3] == ("1W", "2W", "3W")
    for substituted in ("7D", "14D", "21D"):
        assert substituted not in DEFAULT_USD_SOFR_TENORS


def test_default_tenors_use_1y_never_the_derived_12m_spelling():
    assert "1Y" in DEFAULT_USD_SOFR_TENORS
    assert "12M" not in DEFAULT_USD_SOFR_TENORS


def test_week_tenor_ticker_builders_preserve_the_literal_bloomberg_label():
    # The loader-local acquisition validator never rewrites a week label --
    # the built ticker carries the exact "1W"/"2W"/"3W" text Eddy's export
    # named, never a converted day-count spelling.
    assert build_zero_rate_ticker("0490", "1W") == "S0490Z 1W BLC2 Curncy"
    assert build_discount_factor_ticker("0490", "2W") == "S0490D 2W BLC2 Curncy"
    assert build_zero_rate_ticker("0490", "3W") == "S0490Z 3W BLC2 Curncy"


def test_a_week_tenor_passes_loader_validation_and_reaches_bloomberg(monkeypatch):
    # "1W" is now a valid acquisition label (Issue #165 tenor decoupling) --
    # it must pass this loader's own pre-DAPI validation and actually reach
    # the (faked) Bloomberg request, unlike a genuinely malformed label.
    zero_1w = "S0490Z 1W BLC2 Curncy"
    df_1w = "S0490D 1W BLC2 Curncy"
    records = [
        _security_record(security=zero_1w, last_price="1.50", maturity="2026-08-18"),
        _security_record(security=df_1w, last_price="0.9997", maturity="2026-08-18"),
    ]
    holder = _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    result = load_bloomberg_usd_sofr_option_discount_curve(tenors=("1W",))

    assert holder["session"].last_request.securities == [zero_1w, df_1w]
    assert result.curve_points[0].tenor == "1W"
    assert result.curve_points[0].maturity_date == "2026-08-18"


def test_shared_pricing_tenor_parser_still_rejects_1w():
    # Pin: the shared, previously-reviewed pricing/bli_curve_tenor.py
    # parser is unmodified by Issue #165's tenor decoupling -- it must
    # keep rejecting "1W" exactly as before. Live pricing of a
    # week-labelled Bloomberg node never depends on this parser accepting
    # "1W" -- see test_bli_zero_curve_nodes.py's own pin for that.
    with pytest.raises(ValueError, match="unsupported tenor label"):
        tenor_to_year_fraction("1W")


# --- Codex P2 fix: exact-enumeration canonical acquisition ordering -----------------
#
# Codex correctly identified that the earlier W=7/M=30/Y=365 nominal-day
# ordering could collide across units (e.g. "60W" and "14M" both resolved to
# 420), which could make the non-increasing-maturity fail-closed check's
# outcome depend on Python's stable-sort tie-breaking -- and therefore on
# caller input order. The fix replaces that generic arithmetic with an exact
# label -> canonical-index mapping built from DEFAULT_USD_SOFR_TENORS's own
# position, which cannot collide (32 distinct labels, 32 distinct indices)
# and is fully independent of caller order. These tests pin that fix.


@pytest.mark.parametrize("unverified_tenor", ["60W", "14M", "11Y"])
def test_an_unverified_but_syntactically_valid_tenor_fails_pre_dapi(unverified_tenor):
    # "60W"/"14M"/"11Y" are shaped exactly like a valid Bloomberg tenor label
    # but are not part of the 32-label workstation-verified Curve #490
    # universe -- this loader must never generate or probe an unverified
    # Bloomberg synthetic security for them, and must reject them with the
    # same pre-DAPI ValueError a malformed label gets.
    with pytest.raises(ValueError, match="unsupported tenor label"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=(unverified_tenor,))


_PERMUTATION_TENOR_SPECS = (
    ("1W", "1.50", "0.9997", "2026-08-08"),
    ("6M", "1.60", "0.9920", "2027-02-01"),
    ("18M", "1.90", "0.9720", "2028-02-01"),
    ("2Y", "3.25", "0.9400", "2028-08-01"),
    ("10Y", "4.00", "0.6800", "2036-08-01"),
)
# The canonical acquisition order for this subset, per DEFAULT_USD_SOFR_TENORS's
# own index (1W=0, 6M=8, 18M=15, 2Y=16, 10Y=24) -- deliberately not the order
# the specs tuple above happens to be written in.
_PERMUTATION_CANONICAL_ORDER = ("1W", "6M", "18M", "2Y", "10Y")


def _permutation_subset_records() -> list:
    records = []
    for tenor, zero_pct, df, maturity in _PERMUTATION_TENOR_SPECS:
        records.append(
            _security_record(
                security=build_zero_rate_ticker(USD_SOFR_BLOOMBERG_CURVE_NUMBER, tenor),
                last_price=zero_pct,
                maturity=maturity,
            )
        )
        records.append(
            _security_record(
                security=build_discount_factor_ticker(USD_SOFR_BLOOMBERG_CURVE_NUMBER, tenor),
                last_price=df,
                maturity=maturity,
            )
        )
    return records


@pytest.mark.parametrize(
    "caller_order",
    [
        ("2Y", "1W", "6M", "18M", "10Y"),
        ("10Y", "18M", "2Y", "6M", "1W"),
        ("6M", "10Y", "1W", "2Y", "18M"),
        ("1W", "6M", "18M", "2Y", "10Y"),  # already in canonical order
    ],
)
def test_arbitrary_permutations_of_a_verified_subset_produce_identical_canonical_ordering(
    monkeypatch, caller_order
):
    _install_fake_blpapi(monkeypatch, events=[_response_event(_permutation_subset_records())])

    result = load_bloomberg_usd_sofr_option_discount_curve(tenors=caller_order)

    assert [p.tenor for p in result.curve_points] == list(_PERMUTATION_CANONICAL_ORDER)
    assert [e.tenor for e in result.discount_factor_evidence] == list(_PERMUTATION_CANONICAL_ORDER)


def test_caller_order_cannot_change_pass_fail_behavior_on_a_maturity_violation(monkeypatch):
    # "3W" (canonical index 2) must resolve before "1M" (canonical index 3).
    # Here Bloomberg's own returned MATURITY puts "3W" *after* "1M" -- a
    # violation of the canonical increasing-maturity contract that must
    # fail closed identically regardless of which order the caller happened
    # to list the two tenors in.
    records = [
        _security_record(
            security=build_zero_rate_ticker(USD_SOFR_BLOOMBERG_CURVE_NUMBER, "3W"),
            last_price="1.55",
            maturity="2026-09-01",
        ),
        _security_record(
            security=build_discount_factor_ticker(USD_SOFR_BLOOMBERG_CURVE_NUMBER, "3W"),
            last_price="0.9993",
            maturity="2026-09-01",
        ),
        _security_record(
            security=build_zero_rate_ticker(USD_SOFR_BLOOMBERG_CURVE_NUMBER, "1M"),
            last_price="1.60",
            maturity="2026-08-15",
        ),
        _security_record(
            security=build_discount_factor_ticker(USD_SOFR_BLOOMBERG_CURVE_NUMBER, "1M"),
            last_price="0.9990",
            maturity="2026-08-15",
        ),
    ]

    for caller_order in [("3W", "1M"), ("1M", "3W")]:
        _install_fake_blpapi(monkeypatch, events=[_response_event(records)])
        with pytest.raises(BLIBloombergDapiError, match="did not increase"):
            load_bloomberg_usd_sofr_option_discount_curve(tenors=caller_order)


# --- ticker construction ------------------------------------------------------------


def test_ticker_builders_use_eddys_confirmed_grammar():
    assert build_zero_rate_ticker("0490", "2Y") == "S0490Z 2Y BLC2 Curncy"
    assert build_discount_factor_ticker("0490", "1Y") == "S0490D 1Y BLC2 Curncy"


def test_usd_sofr_curve_number_is_0490():
    assert USD_SOFR_BLOOMBERG_CURVE_NUMBER == "0490"


# --- request construction -----------------------------------------------------------


def test_sends_z_and_d_ticker_per_tenor_and_only_last_price_and_maturity(monkeypatch):
    holder = _install_fake_blpapi(monkeypatch, events=_both_tenors_success())

    load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y", "2Y"))

    request = holder["session"].last_request
    assert request.securities == [_ZERO_1Y, _DF_1Y, _ZERO_2Y, _DF_2Y]
    assert request.fields == ["LAST_PRICE", "MATURITY"]


# --- per-security identity matching, not response order -----------------------------


def test_records_are_matched_by_identity_regardless_of_response_order(monkeypatch):
    records = [
        _security_record(security=_DF_2Y, last_price="0.94", maturity="2028-08-10"),
        _security_record(security=_ZERO_1Y, last_price="1.75", maturity="2027-08-10"),
        _security_record(security=_DF_1Y, last_price="0.98", maturity="2027-08-10"),
        _security_record(security=_ZERO_2Y, last_price="3.25", maturity="2028-08-10"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    result = load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y", "2Y"))

    by_tenor = {p.tenor: p for p in result.curve_points}
    assert by_tenor["1Y"].rate == pytest.approx(0.0175)
    assert by_tenor["2Y"].rate == pytest.approx(0.0325)


# --- BAD_SEC / securityError ---------------------------------------------------------


def test_bad_sec_on_zero_rate_ticker_fails_closed(monkeypatch):
    records = [
        _security_record(security=_ZERO_1Y, security_error="[BAD_SEC] Unknown/Invalid security"),
        _security_record(security=_ZERO_2Y, last_price="3.25", maturity="2028-08-10"),
        _security_record(security=_DF_1Y, last_price="0.98", maturity="2027-08-10"),
        _security_record(security=_DF_2Y, last_price="0.94", maturity="2028-08-10"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="BAD_SEC"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y", "2Y"))


def test_bad_sec_on_discount_factor_ticker_fails_closed(monkeypatch):
    records = [
        _security_record(security=_ZERO_1Y, last_price="1.75", maturity="2027-08-10"),
        _security_record(security=_DF_1Y, security_error="[BAD_SEC] Unknown/Invalid security"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="BAD_SEC"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y",))


# --- record-count problems ------------------------------------------------------------


def test_missing_security_record_fails_closed(monkeypatch):
    records = [_security_record(security=_DF_1Y, last_price="0.98", maturity="2027-08-10")]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="0 securityData record"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y",))


def test_duplicate_security_record_fails_closed(monkeypatch):
    records = [
        _security_record(security=_ZERO_1Y, last_price="1.75", maturity="2027-08-10"),
        _security_record(security=_ZERO_1Y, last_price="1.76", maturity="2027-08-10"),
        _security_record(security=_DF_1Y, last_price="0.98", maturity="2027-08-10"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="2 securityData record"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y",))


# --- field exceptions / missing fields -------------------------------------------------


def test_field_exception_on_last_price_fails_closed(monkeypatch):
    records = [
        _security_record(
            security=_ZERO_1Y,
            maturity="2027-08-10",
            field_exceptions=["[BAD_FLD] not applicable"],
        ),
        _security_record(security=_DF_1Y, last_price="0.98", maturity="2027-08-10"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="BAD_FLD"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y",))


def test_missing_last_price_fails_closed(monkeypatch):
    records = [
        _security_record(security=_ZERO_1Y, maturity="2027-08-10"),
        _security_record(security=_DF_1Y, last_price="0.98", maturity="2027-08-10"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="missing LAST_PRICE"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y",))


def test_missing_maturity_fails_closed(monkeypatch):
    records = [
        _security_record(security=_ZERO_1Y, last_price="1.75"),
        _security_record(security=_DF_1Y, last_price="0.98", maturity="2027-08-10"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="missing MATURITY"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y",))


def test_non_parseable_zero_maturity_fails_closed(monkeypatch):
    records = [
        _security_record(security=_ZERO_1Y, last_price="1.75", maturity="not-a-date"),
        _security_record(security=_DF_1Y, last_price="0.98", maturity="2027-08-10"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="YYYY-MM-DD"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y",))


def test_non_parseable_discount_factor_maturity_fails_closed(monkeypatch):
    records = [
        _security_record(security=_ZERO_1Y, last_price="1.75", maturity="2027-08-10"),
        _security_record(security=_DF_1Y, last_price="0.98", maturity="not-a-date"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="YYYY-MM-DD"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y",))


# --- Z/D maturity mismatch (same-node cross-check) ------------------------------------


def test_z_and_d_maturity_mismatch_at_the_same_tenor_fails_closed(monkeypatch):
    records = [
        _security_record(security=_ZERO_1Y, last_price="1.75", maturity="2027-08-10"),
        _security_record(security=_DF_1Y, last_price="0.98", maturity="2027-08-11"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="MATURITY mismatch"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y",))


def test_z_and_d_maturity_mismatch_error_names_both_tickers_and_dates(monkeypatch):
    records = [
        _security_record(security=_ZERO_1Y, last_price="1.75", maturity="2027-08-10"),
        _security_record(security=_DF_1Y, last_price="0.98", maturity="2027-08-11"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError) as exc_info:
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y",))

    message = str(exc_info.value)
    assert _ZERO_1Y in message
    assert _DF_1Y in message
    assert "2027-08-10" in message
    assert "2027-08-11" in message


def test_z_and_d_matching_maturity_at_the_same_tenor_succeeds(monkeypatch):
    records = [
        _security_record(security=_ZERO_1Y, last_price="1.75", maturity="2027-08-10"),
        _security_record(security=_DF_1Y, last_price="0.98", maturity="2027-08-10"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    result = load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y",))

    assert result.curve_points[0].maturity_date == "2027-08-10"


# --- non-finite zero rate / non-positive discount factor ----------------------------


def test_non_numeric_zero_rate_fails_closed(monkeypatch):
    records = [
        _security_record(security=_ZERO_1Y, last_price="N.A.", maturity="2027-08-10"),
        _security_record(security=_DF_1Y, last_price="0.98", maturity="2027-08-10"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="non-numeric"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y",))


def test_non_finite_zero_rate_fails_closed(monkeypatch):
    records = [
        _security_record(security=_ZERO_1Y, last_price="nan", maturity="2027-08-10"),
        _security_record(security=_DF_1Y, last_price="0.98", maturity="2027-08-10"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="non-finite"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y",))


def test_zero_discount_factor_fails_closed(monkeypatch):
    records = [
        _security_record(security=_ZERO_1Y, last_price="1.75", maturity="2027-08-10"),
        _security_record(security=_DF_1Y, last_price="0", maturity="2027-08-10"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="strictly positive"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y",))


def test_negative_discount_factor_fails_closed(monkeypatch):
    records = [
        _security_record(security=_ZERO_1Y, last_price="1.75", maturity="2027-08-10"),
        _security_record(security=_DF_1Y, last_price="-0.1", maturity="2027-08-10"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="strictly positive"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y",))


def test_discount_factor_above_one_is_accepted_never_assumed_le_one(monkeypatch):
    records = [
        _security_record(security=_ZERO_1Y, last_price="1.75", maturity="2027-08-10"),
        _security_record(security=_DF_1Y, last_price="1.4", maturity="2027-08-10"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    result = load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y",))

    assert result.discount_factor_evidence[0].discount_factor == pytest.approx(1.4)


# --- duplicate / non-increasing maturity ---------------------------------------------


def test_duplicate_maturity_across_tenors_fails_closed(monkeypatch):
    records = [
        _security_record(security=_ZERO_1Y, last_price="1.75", maturity="2028-08-10"),
        _security_record(security=_ZERO_2Y, last_price="3.25", maturity="2028-08-10"),
        _security_record(security=_DF_1Y, last_price="0.98", maturity="2028-08-10"),
        _security_record(security=_DF_2Y, last_price="0.94", maturity="2028-08-10"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="duplicate curve node maturity"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y", "2Y"))


def test_non_increasing_maturity_across_tenors_fails_closed(monkeypatch):
    records = [
        _security_record(security=_ZERO_1Y, last_price="1.75", maturity="2029-08-10"),
        _security_record(security=_ZERO_2Y, last_price="3.25", maturity="2028-08-10"),
        _security_record(security=_DF_1Y, last_price="0.98", maturity="2029-08-10"),
        _security_record(security=_DF_2Y, last_price="0.94", maturity="2028-08-10"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="did not increase"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y", "2Y"))


# --- successful curve construction ----------------------------------------------------


def test_successful_curve_produces_option_discount_curve_continuous_zero_rate_points(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_both_tenors_success())

    result = load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y", "2Y"))

    assert len(result.curve_points) == 2
    for point in result.curve_points:
        assert point.curve_id == USD_SOFR_CURVE_ID
        assert point.curve_name == USD_SOFR_CURVE_NAME
        assert point.currency is Currency.USD
        assert point.curve_purpose is BLICurvePurpose.OPTION_DISCOUNT_CURVE
        assert point.rate_basis is BLICurveRateBasis.CONTINUOUS_ZERO_RATE
        assert point.source_system == "BLOOMBERG_DAPI"
        assert point.status is BLIMarketDataStatus.ACTIVE


def test_curve_point_preserves_the_z_tickers_own_maturity_verbatim(monkeypatch):
    _install_fake_blpapi(
        monkeypatch,
        events=_both_tenors_success(maturity_1y="2027-08-10", maturity_2y="2028-08-10"),
    )

    result = load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y", "2Y"))

    by_tenor = {p.tenor: p for p in result.curve_points}
    assert by_tenor["1Y"].maturity_date == "2027-08-10"
    assert by_tenor["2Y"].maturity_date == "2028-08-10"


def test_curve_point_maturity_date_is_never_reconstructed_from_tenor(monkeypatch):
    # A tenor of "1Y" from an arbitrary valuation date would never itself
    # equal "2030-01-15" -- this asserts the field carries Bloomberg's own
    # returned value through unchanged, not a tenor-derived guess.
    _install_fake_blpapi(
        monkeypatch, events=_both_tenors_success(maturity_1y="2030-01-15", maturity_2y="2031-06-20")
    )

    result = load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y", "2Y"))

    by_tenor = {p.tenor: p for p in result.curve_points}
    assert by_tenor["1Y"].maturity_date == "2030-01-15"
    assert by_tenor["2Y"].maturity_date == "2031-06-20"


def test_zero_rate_percent_is_converted_to_decimal(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_both_tenors_success(zero_1y="1.75", zero_2y="3.25"))

    result = load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y", "2Y"))

    by_tenor = {p.tenor: p for p in result.curve_points}
    assert by_tenor["1Y"].rate == pytest.approx(0.0175)
    assert by_tenor["2Y"].rate == pytest.approx(0.0325)


def test_discount_factor_is_never_divided_by_100(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_both_tenors_success(df_1y="0.98", df_2y="0.94"))

    result = load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y", "2Y"))

    by_tenor = {e.tenor: e for e in result.discount_factor_evidence}
    assert by_tenor["1Y"].discount_factor == pytest.approx(0.98)
    assert by_tenor["2Y"].discount_factor == pytest.approx(0.94)


def test_discount_factor_evidence_is_never_written_into_a_curve_point(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_both_tenors_success())

    result = load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y", "2Y"))

    curve_point_field_names = {f.name for f in module.BLICurvePoint.__dataclass_fields__.values()}
    assert "discount_factor" not in curve_point_field_names
    assert len(result.discount_factor_evidence) == len(result.curve_points)


def test_result_is_sorted_by_canonical_acquisition_order_regardless_of_input_order(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_both_tenors_success())

    result = load_bloomberg_usd_sofr_option_discount_curve(tenors=("2Y", "1Y"))

    assert [p.tenor for p in result.curve_points] == ["1Y", "2Y"]
    assert [e.tenor for e in result.discount_factor_evidence] == ["1Y", "2Y"]


def test_discount_factor_evidence_preserves_ticker_and_raw_value(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_both_tenors_success(df_1y="0.98"))

    result = load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y", "2Y"))

    one_year_evidence = next(e for e in result.discount_factor_evidence if e.tenor == "1Y")
    assert one_year_evidence.security == _DF_1Y
    assert one_year_evidence.raw_last_price == "0.98"
    assert one_year_evidence.maturity == "2027-08-10"


# --- session lifecycle ---------------------------------------------------------------


def test_session_is_stopped_on_success(monkeypatch):
    holder = _install_fake_blpapi(monkeypatch, events=_both_tenors_success())

    load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y", "2Y"))

    assert holder["session"].stopped is True


def test_session_is_stopped_on_failure(monkeypatch):
    holder = _install_fake_blpapi(monkeypatch, start_result=False)

    with pytest.raises(BLIBloombergDapiError, match="failed to start"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y",))

    assert holder["session"].stopped is True


def test_blpapi_not_installed_raises_bli_bloomberg_dapi_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "blpapi", None)
    monkeypatch.delitem(sys.modules, "blpapi", raising=False)

    real_import = __import__

    def _raise_for_blpapi(name, *args, **kwargs):
        if name == "blpapi":
            raise ImportError("no module named blpapi")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _raise_for_blpapi)

    with pytest.raises(BLIBloombergDapiError, match="blpapi is not installed"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=("1Y",))


# --- N-tenor curve expansion coverage (Issue #165 full-curve expansion follow-up) ---
#
# These tenors/rates/dates are synthetic test fixture values only -- proving
# the loader's *mechanism* scales correctly to an arbitrary caller-supplied
# tenor set, not a substitute for the DEFAULT_USD_SOFR_TENORS-specific
# coverage below (which pins the real, workstation-verified default).

_MULTI_TENOR_SPECS = (
    ("1Y", "1.75", "0.98", "2027-08-01"),
    ("2Y", "3.25", "0.94", "2028-08-01"),
    ("3Y", "3.50", "0.90", "2029-08-01"),
    ("5Y", "3.75", "0.83", "2031-08-01"),
    ("10Y", "4.00", "0.68", "2036-08-01"),
)


def _multi_tenor_records(specs=_MULTI_TENOR_SPECS, *, mismatched_tenor=None):
    """Build Z+D securityData records for every (tenor, rate, df, maturity) spec.

    ``mismatched_tenor``, if given, makes that one tenor's D record report a
    MATURITY one day after its Z record's -- for the Z/D-mismatch tests.
    """

    records = []
    for tenor, zero_pct, df, maturity in specs:
        records.append(
            _security_record(
                security=module.build_zero_rate_ticker(
                    module.USD_SOFR_BLOOMBERG_CURVE_NUMBER, tenor
                ),
                last_price=zero_pct,
                maturity=maturity,
            )
        )
        df_maturity = maturity
        if tenor == mismatched_tenor:
            year, month, day = (int(part) for part in maturity.split("-"))
            df_maturity = f"{year}-{month:02d}-{day + 1:02d}"
        records.append(
            _security_record(
                security=module.build_discount_factor_ticker(
                    module.USD_SOFR_BLOOMBERG_CURVE_NUMBER, tenor
                ),
                last_price=df,
                maturity=df_maturity,
            )
        )
    return records


def _multi_tenor_events(**kwargs):
    return [_response_event(_multi_tenor_records(**kwargs))]


_MULTI_TENOR_LABELS = tuple(spec[0] for spec in _MULTI_TENOR_SPECS)


def test_every_configured_tenor_generates_the_exact_z_and_d_ticker_grammar(monkeypatch):
    holder = _install_fake_blpapi(monkeypatch, events=_multi_tenor_events())

    load_bloomberg_usd_sofr_option_discount_curve(tenors=_MULTI_TENOR_LABELS)

    expected_securities = [
        security
        for tenor in _MULTI_TENOR_LABELS
        for security in (
            module.build_zero_rate_ticker(module.USD_SOFR_BLOOMBERG_CURVE_NUMBER, tenor),
            module.build_discount_factor_ticker(module.USD_SOFR_BLOOMBERG_CURVE_NUMBER, tenor),
        )
    ]
    assert holder["session"].last_request.securities == expected_securities


def test_every_successful_tenor_produces_exactly_one_curve_point_and_one_evidence_record(
    monkeypatch,
):
    _install_fake_blpapi(monkeypatch, events=_multi_tenor_events())

    result = load_bloomberg_usd_sofr_option_discount_curve(tenors=_MULTI_TENOR_LABELS)

    assert len(result.curve_points) == len(_MULTI_TENOR_SPECS)
    assert len(result.discount_factor_evidence) == len(_MULTI_TENOR_SPECS)
    assert {p.tenor for p in result.curve_points} == set(_MULTI_TENOR_LABELS)
    assert {e.tenor for e in result.discount_factor_evidence} == set(_MULTI_TENOR_LABELS)


def test_returned_points_are_ordered_by_resolved_maturity_coordinates_for_many_tenors(
    monkeypatch,
):
    scrambled_order = ("5Y", "1Y", "10Y", "2Y", "3Y")
    _install_fake_blpapi(monkeypatch, events=_multi_tenor_events())

    result = load_bloomberg_usd_sofr_option_discount_curve(tenors=scrambled_order)

    assert [p.tenor for p in result.curve_points] == list(_MULTI_TENOR_LABELS)
    assert [e.tenor for e in result.discount_factor_evidence] == list(_MULTI_TENOR_LABELS)
    # The real Bloomberg-returned maturity dates are themselves increasing
    # in this same order -- confirms the output order tracks the actual
    # resolved maturity coordinates, not merely the caller's request order.
    maturities = [p.maturity_date for p in result.curve_points]
    assert maturities == sorted(maturities)


@pytest.mark.parametrize("mismatched_tenor", ["1Y", "3Y", "10Y"])
def test_z_d_maturity_mismatch_at_any_tenor_aborts_the_whole_curve(monkeypatch, mismatched_tenor):
    _install_fake_blpapi(monkeypatch, events=_multi_tenor_events(mismatched_tenor=mismatched_tenor))

    with pytest.raises(BLIBloombergDapiError, match="MATURITY mismatch"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=_MULTI_TENOR_LABELS)


@pytest.mark.parametrize("broken_tenor", ["1Y", "3Y", "10Y"])
def test_bad_sec_at_any_tenor_aborts_the_whole_curve(monkeypatch, broken_tenor):
    records = _multi_tenor_records()
    broken_security = module.build_zero_rate_ticker(
        module.USD_SOFR_BLOOMBERG_CURVE_NUMBER, broken_tenor
    )
    records = [
        _security_record(
            security=broken_security, security_error="[BAD_SEC] Unknown/Invalid security"
        )
        if record.hasElement("security")
        and record.getElement("security").getValueAsString() == broken_security
        else record
        for record in records
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="BAD_SEC"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=_MULTI_TENOR_LABELS)


@pytest.mark.parametrize("broken_tenor", ["1Y", "3Y", "10Y"])
def test_missing_last_price_at_any_tenor_aborts_the_whole_curve(monkeypatch, broken_tenor):
    specs = tuple(
        (tenor, None if tenor == broken_tenor else zero_pct, df, maturity)
        for tenor, zero_pct, df, maturity in _MULTI_TENOR_SPECS
    )
    records = []
    for tenor, zero_pct, df, maturity in specs:
        records.append(
            _security_record(
                security=module.build_zero_rate_ticker(
                    module.USD_SOFR_BLOOMBERG_CURVE_NUMBER, tenor
                ),
                last_price=zero_pct,
                maturity=maturity,
            )
        )
        records.append(
            _security_record(
                security=module.build_discount_factor_ticker(
                    module.USD_SOFR_BLOOMBERG_CURVE_NUMBER, tenor
                ),
                last_price=df,
                maturity=maturity,
            )
        )
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="missing LAST_PRICE"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=_MULTI_TENOR_LABELS)


@pytest.mark.parametrize("broken_tenor", ["1Y", "3Y", "10Y"])
def test_missing_maturity_at_any_tenor_aborts_the_whole_curve(monkeypatch, broken_tenor):
    records = []
    for tenor, zero_pct, df, maturity in _MULTI_TENOR_SPECS:
        records.append(
            _security_record(
                security=module.build_zero_rate_ticker(
                    module.USD_SOFR_BLOOMBERG_CURVE_NUMBER, tenor
                ),
                last_price=zero_pct,
                maturity=None if tenor == broken_tenor else maturity,
            )
        )
        records.append(
            _security_record(
                security=module.build_discount_factor_ticker(
                    module.USD_SOFR_BLOOMBERG_CURVE_NUMBER, tenor
                ),
                last_price=df,
                maturity=maturity,
            )
        )
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="missing MATURITY"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=_MULTI_TENOR_LABELS)


def test_field_exception_at_a_later_tenor_aborts_the_whole_curve(monkeypatch):
    broken_tenor = "5Y"
    records = []
    for tenor, zero_pct, df, maturity in _MULTI_TENOR_SPECS:
        if tenor == broken_tenor:
            records.append(
                _security_record(
                    security=module.build_zero_rate_ticker(
                        module.USD_SOFR_BLOOMBERG_CURVE_NUMBER, tenor
                    ),
                    maturity=maturity,
                    field_exceptions=["[BAD_FLD] not applicable"],
                )
            )
        else:
            records.append(
                _security_record(
                    security=module.build_zero_rate_ticker(
                        module.USD_SOFR_BLOOMBERG_CURVE_NUMBER, tenor
                    ),
                    last_price=zero_pct,
                    maturity=maturity,
                )
            )
        records.append(
            _security_record(
                security=module.build_discount_factor_ticker(
                    module.USD_SOFR_BLOOMBERG_CURVE_NUMBER, tenor
                ),
                last_price=df,
                maturity=maturity,
            )
        )
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    with pytest.raises(BLIBloombergDapiError, match="BAD_FLD"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=_MULTI_TENOR_LABELS)


def test_duplicate_maturity_among_many_tenors_aborts_the_whole_curve(monkeypatch):
    specs = tuple(
        (tenor, zero_pct, df, "2029-08-01" if tenor == "5Y" else maturity)
        for tenor, zero_pct, df, maturity in _MULTI_TENOR_SPECS
    )
    _install_fake_blpapi(monkeypatch, events=[_response_event(_multi_tenor_records(specs))])

    with pytest.raises(BLIBloombergDapiError, match="duplicate curve node maturity"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=_MULTI_TENOR_LABELS)


def test_non_increasing_maturity_among_many_tenors_aborts_the_whole_curve(monkeypatch):
    # "3Y"'s own MATURITY is placed before "2Y"'s -- breaks the required
    # increasing-maturity progression even though every other tenor is fine.
    specs = tuple(
        (tenor, zero_pct, df, "2027-09-01" if tenor == "3Y" else maturity)
        for tenor, zero_pct, df, maturity in _MULTI_TENOR_SPECS
    )
    _install_fake_blpapi(monkeypatch, events=[_response_event(_multi_tenor_records(specs))])

    with pytest.raises(BLIBloombergDapiError, match="did not increase"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=_MULTI_TENOR_LABELS)


def test_negative_zero_rate_remains_valid(monkeypatch):
    specs = tuple(
        (tenor, "-0.50" if tenor == "1Y" else zero_pct, df, maturity)
        for tenor, zero_pct, df, maturity in _MULTI_TENOR_SPECS
    )
    _install_fake_blpapi(monkeypatch, events=[_response_event(_multi_tenor_records(specs))])

    result = load_bloomberg_usd_sofr_option_discount_curve(tenors=_MULTI_TENOR_LABELS)

    by_tenor = {p.tenor: p for p in result.curve_points}
    assert by_tenor["1Y"].rate == pytest.approx(-0.005)


def test_non_finite_discount_factor_fails_closed(monkeypatch):
    specs = tuple(
        (tenor, zero_pct, "nan" if tenor == "1Y" else df, maturity)
        for tenor, zero_pct, df, maturity in _MULTI_TENOR_SPECS
    )
    _install_fake_blpapi(monkeypatch, events=[_response_event(_multi_tenor_records(specs))])

    with pytest.raises(BLIBloombergDapiError, match="non-finite"):
        load_bloomberg_usd_sofr_option_discount_curve(tenors=_MULTI_TENOR_LABELS)


# --- DEFAULT_USD_SOFR_TENORS full-set coverage (Issue #165 tenor decoupling) ----
#
# Exercises the loader against its own real, workstation-verified default
# tenor tuple (all 32 tenors, "1W"/"2W"/"3W" included) rather than a smaller
# synthetic subset. Rates/DFs are still synthetic fixture values (never
# claimed as real Bloomberg observations); maturities are generated
# deterministically from each tenor's own fixed canonical acquisition-order
# index (module._validate_bloomberg_tenor_label -- tenor_to_year_fraction
# cannot be used here since it still rejects "1W"/"2W"/"3W" by design) so
# they are always self-consistent (strictly increasing in the same order as
# DEFAULT_USD_SOFR_TENORS itself).


def _default_tenor_maturity(tenor: str) -> str:
    epoch = date(2026, 8, 1)
    index = module._validate_bloomberg_tenor_label(tenor)
    return (epoch + timedelta(days=30 * index)).isoformat()


def _default_tenor_records() -> list:
    records = []
    for index, tenor in enumerate(DEFAULT_USD_SOFR_TENORS):
        maturity = _default_tenor_maturity(tenor)
        zero_rate_percent = f"{2.0 + index * 0.05:.4f}"
        discount_factor = f"{0.999 - index * 0.01:.4f}"
        records.append(
            _security_record(
                security=build_zero_rate_ticker(USD_SOFR_BLOOMBERG_CURVE_NUMBER, tenor),
                last_price=zero_rate_percent,
                maturity=maturity,
            )
        )
        records.append(
            _security_record(
                security=build_discount_factor_ticker(USD_SOFR_BLOOMBERG_CURVE_NUMBER, tenor),
                last_price=discount_factor,
                maturity=maturity,
            )
        )
    return records


def test_default_tenor_set_generates_all_64_expected_z_and_d_tickers(monkeypatch):
    holder = _install_fake_blpapi(monkeypatch, events=[_response_event(_default_tenor_records())])

    load_bloomberg_usd_sofr_option_discount_curve(tenors=DEFAULT_USD_SOFR_TENORS)

    expected_securities = [
        security
        for tenor in DEFAULT_USD_SOFR_TENORS
        for security in (
            build_zero_rate_ticker(USD_SOFR_BLOOMBERG_CURVE_NUMBER, tenor),
            build_discount_factor_ticker(USD_SOFR_BLOOMBERG_CURVE_NUMBER, tenor),
        )
    ]
    assert len(expected_securities) == 64
    assert holder["session"].last_request.securities == expected_securities
    # Every one of the 64 tickers carries the approved S0490Z/S0490D grammar.
    for security in expected_securities:
        assert security.startswith(("S0490Z ", "S0490D "))
        assert security.endswith(" BLC2 Curncy")


def test_default_tenor_set_produces_32_curve_points_and_32_evidence_records(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=[_response_event(_default_tenor_records())])

    result = load_bloomberg_usd_sofr_option_discount_curve(tenors=DEFAULT_USD_SOFR_TENORS)

    assert len(result.curve_points) == 32
    assert len(result.discount_factor_evidence) == 32
    assert {p.tenor for p in result.curve_points} == set(DEFAULT_USD_SOFR_TENORS)
