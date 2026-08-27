"""Current cheapest-to-deliver metadata for a Treasury futures contract (Issue #190).

Scope: the *sourcing* half of the desk's futures <-> CTD implied-yield
converter -- the validated record of which cash Treasury is currently
cheapest to deliver into one futures contract, and where that record came
from. No pricing, no yield, no schedule, no quote parsing lives here.

**Automatic Bloomberg sourcing, in two stages.** Bloomberg does not publish
CTD metadata against the generic front-contract ticker's own delivery month
directly, so one lookup is two requests:

1. ``<root>1 Comdty`` -> ``FUT_CUR_GEN_TICKER`` resolves the generic front
   contract to the actual delivery month (``TU1 Comdty`` -> ``TUU6``).
2. ``<actual> Comdty`` -> the CTD fields for that specific contract.

Both stages fail closed. A missing, blank, malformed or unparseable value on
any required field aborts the whole load with the field named -- there is no
partial record, no fallback to manual entry, and no contract cache anywhere
in this repository.

**Confirmed field evidence (Eddy's Bloomberg workstation, Issue #190).**
Every mnemonic below returned a value on all four current active contracts.
The live values are recorded here as the evidence for the mapping, exactly
as ``bloomberg_bond_quote``'s own field maps record theirs -- they are
*evidence of the mnemonic*, never a cache: nothing in this module ever reads
them back as data.

===== ============== ============= ============ ========== ======== ==============
Code  Generic ticker Contract      CTD ISIN     Coupon %   CF       Last delivery
===== ============== ============= ============ ========== ======== ==============
ZT    TU1 Comdty     TUU6          US91282CHK09 4.00       0.967200 2026-10-05
ZF    FV1 Comdty     FVU6          US91282CPN55 3.50       0.909000 2026-10-05
ZN    TY1 Comdty     TYU6          US91282CQT17 4.25       0.906900 2026-09-30
ZB    US1 Comdty     USU6          US912810UL07 5.00       0.889200 2026-09-30
===== ============== ============= ============ ========== ======== ==============

CTD maturities returned alongside: 2028-06-30 (ZT), 2030-11-30 (ZF),
2033-05-31 (ZN), 2045-05-15 (ZB). Two of the four are month-end maturities,
which is exactly the coupon-grid case ``pricing/treasury_futures_implied_yield``
anchors for.

**``FUT_CTD_ISIN`` is the canonical CTD identifier.** ``FUT_CTD_CUSIP`` and
``FUT_CTD_TICKER`` are confirmed to return values too and are carried as
*display* metadata only -- never as the identifier a calculation keys on,
and never coerced into one. This mirrors ``bloomberg_bond_quote``'s
separation of typed fields from display-only ones.

**Superseded candidates.** The probe's original list carried several
candidates per destination so that one workstation run could be conclusive.
It was: for the contract symbol, ``FUT_ACT_DEF_GEN_TICKER`` and
``PARSEKYABLE_DES``; for the identifier, ``CTD_ISIN`` and ``CTD_CUSIP``; for
the coupon, ``CTD_CPN``; for the maturity, ``CTD_MTY`` and
``FUT_CTD_MATURITY``; for the conversion factor, ``CTD_CONVERSION_FACTOR``
and ``FUT_CTD_CNVS_FACTOR``; for the last delivery date, ``LAST_DELIVERY_DT``
and ``FUT_LAST_DLV_DT``. A confirmed mnemonic was found for every required
field, so none of these is wired. They are recorded as **superseded, not as
confirmed rejections** -- the run reported no per-field ``BAD_FLD`` evidence
for them individually, so this module does not claim any of them is invalid.
Re-adding one still requires its own confirmation.

**Deliberately NOT validated: the CTD's deliverable-maturity window.** A
coherent CTD belonging to a *different* Treasury contract would still pass
every guard above -- the identifier is checksum-valid, the numbers are sane,
the delivery date precedes maturity -- because nothing here ties the CTD's
maturity to the contract it is supposed to be deliverable into. That gap is
real (Codex review, PR #191). It is left open on purpose, because closing it
on inferred conventions demonstrably breaks live data:

The CBOT windows are measured from the **first day of the delivery month**,
not from the last delivery day. ``FUT_DLV_DT_LAST`` is the only date this
module receives, and for ZT and ZF the last delivery day falls in the month
*after* the delivery month. Measuring from it is therefore off by one month,
and against Eddy's own confirmed CTDs that one month is decisive:

=====  =========================  =========================  ==============
Code   From 1st of delivery month  From last delivery day     Window
=====  =========================  =========================  ==============
ZT     1y 9m  (in window)          1y 8m  (REJECTED)          >= 1y 9m
ZF     4y 2m  (in window)          4y 1m  (REJECTED)          >= 4y 2m
ZN     6y 8m  (in window)          6y 8m  (in window)         6.5y - 10y
ZB     18y 8m (in window)          18y 7m (in window)         15y - 25y
=====  =========================  =========================  ==============

Two of the four real CTDs sit exactly on their window's lower bound, so any
error in the bound, the measurement basis, or the month arithmetic turns a
correct live load into a hard failure -- an outage on a desk tool, from a
guard meant to prevent a rarer fault. Adding it needs Eddy to confirm, per
contract: the exact window bounds, the reference date they are measured from,
and whether the original-issue-maturity leg applies. That is a pricing-method
input under AGENTS.md rule 7, not something this module may infer.

What already narrows the gap: stage one's resolved symbol must carry this
contract's own root and a quarterly delivery month, and every response's
``security`` element must equal the security requested -- so the record is
tied to the delivery-month contract that was asked about. What is not caught
is Bloomberg answering that specific contract with another contract's CTD.

**Manual entry remains a first-class debug/fallback path, and is always
visibly unconfirmed.** A record built that way carries
``TreasuryFuturesCTDSource.MANUAL_UNCONFIRMED`` and its own operator-supplied
``as_of``, and every consumer renders that status next to the answer.

**Clock.** Manual entry never reads a clock -- ``as_of`` is caller-supplied.
The automatic path stamps the acquisition instant, using the same
``datetime.now(UTC)`` ISO-seconds-with-``Z`` form ``data/vol_surface_store``
and ``data/bloomberg_vcub_ocr`` already use. That is when Shiori received the
data, not a market as-of, and it is labelled as such wherever it is shown.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

from shiori_pricing_lab.data._validation import (
    _parse_iso_date,
    _require_finite_number,
    _require_non_blank,
)

_DAPI_HOST = "localhost"
_DAPI_PORT = 8194
_REFDATA_SERVICE = "//blp/refdata"
_REQUEST_TIMEOUT_MS = 10_000
_SECURITY_IDENTIFIER_FIELD = "security"

# Testable seam for the whole-request deadline: a plain module-level alias,
# monkeypatchable in tests without touching the real `time` module --
# the same seam `bloomberg_bond_quote` already uses.
_monotonic = time.monotonic


class TreasuryFuturesCTDSource(StrEnum):
    """Where one CTD record came from, and whether it is confirmed."""

    BLOOMBERG_DAPI = "BLOOMBERG_DAPI"
    MANUAL_UNCONFIRMED = "MANUAL_UNCONFIRMED"


#: Sources whose data is confirmed to be current automatic market data. A
#: record whose source is not in here must be shown as unconfirmed wherever
#: its numbers are shown (Issue #190).
CONFIRMED_TREASURY_FUTURES_CTD_SOURCES = frozenset({TreasuryFuturesCTDSource.BLOOMBERG_DAPI})

#: The six CTD facts the converter cannot compute without.
REQUIRED_BLOOMBERG_CTD_FIELDS = (
    "contract_symbol",
    "ctd_identifier",
    "ctd_coupon_percent",
    "ctd_maturity_date",
    "conversion_factor",
    "last_delivery_date",
)

#: Confirmed logical field -> Bloomberg mnemonic. See the module docstring
#: for the live evidence behind every entry. ``contract_symbol`` is resolved
#: in stage one, against the generic front-contract ticker; the rest are
#: stage two, against the actual delivery month that stage one returns.
BLOOMBERG_CTD_FIELD_MAP: dict[str, str] = {
    "contract_symbol": "FUT_CUR_GEN_TICKER",
    "ctd_identifier": "FUT_CTD_ISIN",
    "ctd_coupon_percent": "FUT_CTD_CPN",
    "ctd_maturity_date": "FUT_CTD_MTY",
    "conversion_factor": "FUT_CNVS_FACTOR",
    "last_delivery_date": "FUT_DLV_DT_LAST",
}

#: Confirmed to return a value, carried for display only -- never the
#: identifier a calculation keys on, never coerced into a typed field.
BLOOMBERG_CTD_DISPLAY_FIELD_MAP: dict[str, str] = {
    "ctd_cusip": "FUT_CTD_CUSIP",
    "ctd_description": "FUT_CTD_TICKER",
}

#: The mnemonic that resolves a generic front contract to its delivery month.
BLOOMBERG_GENERIC_CONTRACT_FIELD = BLOOMBERG_CTD_FIELD_MAP["contract_symbol"]

#: Shiori contract code -> Bloomberg ticker root, and the yellow key both
#: stages use. Confirmed against all four roots (see the module docstring).
BLOOMBERG_FUTURES_TICKER_ROOTS: dict[str, str] = {
    "ZT": "TU",
    "ZF": "FV",
    "ZN": "TY",
    "ZB": "US",
}
BLOOMBERG_FUTURES_YELLOW_KEY = "Comdty"
BLOOMBERG_GENERIC_FRONT_CONTRACT_SUFFIX = "1"


class TreasuryFuturesCTDError(ValueError):
    """A CTD metadata record is missing a required field or is internally invalid."""


class TreasuryFuturesCTDFieldsUnconfirmedError(RuntimeError):
    """Automatic Bloomberg CTD sourcing was asked for before its fields were confirmed."""


class TreasuryFuturesCTDBloombergError(RuntimeError):
    """Bloomberg DAPI did not return one usable CTD record.

    Covers ``blpapi`` not installed, session/service failure, a request
    timeout, a ``responseError``, a record-count or security-identity
    mismatch, a ``securityError``, a missing or blank required field, and a
    required value that cannot be parsed. Deliberately one exception type --
    these are all the same caller remediation ("Bloomberg did not give us a
    usable CTD"), not distinct conditions a caller would handle differently.
    """


@dataclass(frozen=True)
class TreasuryFuturesCTD:
    """The current CTD for one futures contract, as the converter needs it.

    ``ctd_identifier`` is the CTD's ISIN -- the canonical identifier.
    ``ctd_cusip`` and ``ctd_description`` are display-only extras the
    automatic path fills in and the manual path may leave unset; nothing
    keyed on them ever reaches a calculation.
    """

    contract_code: str
    contract_symbol: str
    ctd_identifier: str
    ctd_coupon_percent: float
    ctd_maturity_date: date
    conversion_factor: float
    last_delivery_date: date
    source: TreasuryFuturesCTDSource
    as_of: str
    ctd_cusip: str | None = None
    ctd_description: str | None = None

    @property
    def is_confirmed_source(self) -> bool:
        """Whether these numbers came from a confirmed automatic market-data path."""

        return self.source in CONFIRMED_TREASURY_FUTURES_CTD_SOURCES

    def as_display_payload(self) -> dict[str, object]:
        """The small print the desk panel shows under every answer."""

        return {
            "contract_code": self.contract_code,
            "contract_symbol": self.contract_symbol,
            "ctd_identifier": self.ctd_identifier,
            "ctd_cusip": self.ctd_cusip,
            "ctd_description": self.ctd_description,
            "ctd_coupon_percent": self.ctd_coupon_percent,
            "ctd_maturity_date": self.ctd_maturity_date.isoformat(),
            "conversion_factor": self.conversion_factor,
            "last_delivery_date": self.last_delivery_date.isoformat(),
            "source": str(self.source),
            "as_of": self.as_of,
            "is_confirmed_source": self.is_confirmed_source,
        }


def unresolved_bloomberg_ctd_fields() -> tuple[str, ...]:
    """Required CTD fields that still have no confirmed Bloomberg mnemonic.

    Empty since Issue #190's field discovery. Kept as the single place any
    consumer asks whether the automatic path is available, so adding a new
    required field automatically re-closes that path until it is confirmed.
    """

    return tuple(
        field for field in REQUIRED_BLOOMBERG_CTD_FIELDS if field not in BLOOMBERG_CTD_FIELD_MAP
    )


def bloomberg_generic_front_contract(contract_code: str) -> str:
    """The stage-one Bloomberg security for ``contract_code`` (``"TU1 Comdty"``)."""

    normalized = str(contract_code).strip().upper()
    root = BLOOMBERG_FUTURES_TICKER_ROOTS.get(normalized)
    if root is None:
        raise TreasuryFuturesCTDError(
            f"no Bloomberg ticker root is confirmed for contract {contract_code!r} -- "
            f"confirmed: {', '.join(sorted(BLOOMBERG_FUTURES_TICKER_ROOTS))}"
        )
    return (
        f"{root}{BLOOMBERG_GENERIC_FRONT_CONTRACT_SUFFIX} {BLOOMBERG_FUTURES_YELLOW_KEY}"
    )


def bloomberg_delivery_month_security(contract_symbol: str) -> str:
    """The stage-two Bloomberg security for a delivery month (``"TUU6 Comdty"``).

    ``FUT_CUR_GEN_TICKER`` returns the bare ticker (``"TUU6"``); DAPI needs a
    yellow key, and it is the same one the generic ticker already carries.
    A symbol that already ends in that yellow key is passed through rather
    than doubled.
    """

    symbol = str(contract_symbol).strip()
    if not symbol:
        raise TreasuryFuturesCTDBloombergError(
            f"Bloomberg DAPI returned a blank {BLOOMBERG_GENERIC_CONTRACT_FIELD}"
        )
    if symbol.upper().endswith(BLOOMBERG_FUTURES_YELLOW_KEY.upper()):
        return symbol
    return f"{symbol} {BLOOMBERG_FUTURES_YELLOW_KEY}"


def _acquisition_now() -> str:
    """When Shiori received the data -- never a market as-of.

    Same form as ``data/vol_surface_store`` and ``data/bloomberg_vcub_ocr``
    already use, rather than a third timestamp convention.
    """

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _reference_data_fields(security: str, fields: list[str]) -> dict[str, str]:
    """Send one ``ReferenceDataRequest`` and return the raw strings it answered.

    Returns only the fields Bloomberg actually populated -- an absent or
    field-exceptioned field is simply missing from the result, and it is the
    caller that decides whether that field was required. Every envelope-level
    problem (session, service, timeout, ``responseError``, record count,
    identity mismatch, ``securityError``) raises.

    Deliberately local to this module rather than shared with
    ``bloomberg_bond_quote``: that module's own note says its two loaders are
    separate self-contained implementations so neither puts the other's
    production behavior and test coverage at risk, and the same reasoning
    applies here. Within *this* module both stages share this one helper, so
    there is still only one request implementation for the CTD path.
    """

    import blpapi

    monotonic = _monotonic

    session_options = blpapi.SessionOptions()
    session_options.setServerHost(_DAPI_HOST)
    session_options.setServerPort(_DAPI_PORT)
    session = blpapi.Session(session_options)

    security_data_records: list = []
    try:
        if not session.start():
            raise TreasuryFuturesCTDBloombergError(
                f"Bloomberg DAPI session failed to start against {_DAPI_HOST}:{_DAPI_PORT} "
                "-- confirm a Bloomberg Terminal is running and logged in locally"
            )
        if not session.openService(_REFDATA_SERVICE):
            raise TreasuryFuturesCTDBloombergError(
                f"Bloomberg DAPI failed to open service {_REFDATA_SERVICE}"
            )

        service = session.getService(_REFDATA_SERVICE)
        request = service.createRequest("ReferenceDataRequest")
        request.append("securities", security)
        for field in dict.fromkeys(fields):
            request.append("fields", field)
        session.sendRequest(request)

        deadline = monotonic() + _REQUEST_TIMEOUT_MS / 1000.0
        done = False
        while not done:
            remaining_seconds = deadline - monotonic()
            if remaining_seconds <= 0:
                raise TreasuryFuturesCTDBloombergError(
                    f"Bloomberg DAPI request timed out waiting for a response for {security!r}"
                )
            event = session.nextEvent(max(1, int(remaining_seconds * 1000)))

            if event.eventType() == blpapi.Event.TIMEOUT:
                raise TreasuryFuturesCTDBloombergError(
                    f"Bloomberg DAPI request timed out waiting for a response for {security!r}"
                )
            if event.eventType() not in (blpapi.Event.PARTIAL_RESPONSE, blpapi.Event.RESPONSE):
                continue

            for message in event:
                if message.hasElement("responseError"):
                    raise TreasuryFuturesCTDBloombergError(
                        f"Bloomberg DAPI responseError for {security!r}: "
                        f"{message.getElement('responseError')}"
                    )
                security_data_array = message.getElement("securityData")
                for index in range(security_data_array.numValues()):
                    security_data_records.append(security_data_array.getValueAsElement(index))

            if event.eventType() == blpapi.Event.RESPONSE:
                done = True
    finally:
        session.stop()

    if len(security_data_records) != 1:
        raise TreasuryFuturesCTDBloombergError(
            f"Bloomberg DAPI returned {len(security_data_records)} securityData records for "
            f"{security!r}, expected exactly one"
        )
    security_data = security_data_records[0]

    if not security_data.hasElement(_SECURITY_IDENTIFIER_FIELD):
        raise TreasuryFuturesCTDBloombergError(
            f"Bloomberg DAPI securityData record for {security!r} is missing its own "
            "security identifier"
        )
    returned_security = security_data.getElementAsString(_SECURITY_IDENTIFIER_FIELD)
    if returned_security != security:
        raise TreasuryFuturesCTDBloombergError(
            f"Bloomberg DAPI securityData record identifier {returned_security!r} does not "
            f"match requested identifier {security!r}"
        )
    if security_data.hasElement("securityError"):
        raise TreasuryFuturesCTDBloombergError(
            f"Bloomberg DAPI securityError for {security!r}: "
            f"{security_data.getElement('securityError')}"
        )

    field_data = security_data.getElement("fieldData")
    answered: dict[str, str] = {}
    for field in dict.fromkeys(fields):
        if not field_data.hasElement(field):
            continue
        try:
            raw_value = field_data.getElementAsString(field)
        except blpapi.exception.Exception as exc:
            raise TreasuryFuturesCTDBloombergError(
                f"Bloomberg DAPI returned a malformed value for {field} on {security!r}: {exc}"
            ) from exc
        if raw_value:
            answered[field] = raw_value
    return answered


def _require_answered(answered: dict[str, str], field: str, security: str) -> str:
    """Return a required field's value, stripped, or raise.

    Whitespace is as missing as an absent element (Codex review, PR #191). A
    field that is *present but semantically empty* is the more dangerous of
    the two: it survives a truthiness check and reaches the record as an
    empty string on an otherwise confirmed-source result.
    """

    raw_value = answered.get(field)
    if raw_value is None or not raw_value.strip():
        raise TreasuryFuturesCTDBloombergError(
            f"Bloomberg DAPI response for {security!r} is missing {field}"
        )
    return raw_value.strip()


#: The delivery months the four supported CBOT Treasury futures actually
#: list: the quarterly March/June/September/December cycle (Codex review, PR
#: #191). Deliberately narrower than the standard twelve-month futures
#: alphabet, which admitted symbols such as ``TYF7`` that are not ZN delivery
#: contracts at all.
#:
#: This is a *guard against a malformed stage-one response*, not an
#: authoritative statement of the listing cycle -- all four confirmed samples
#: are ``U`` (September), so the quarterly restriction rests on the contracts'
#: published cycle rather than on four observations. If Bloomberg ever
#: legitimately answers with a serial month, this fails closed naming that
#: month, which is a visible error to act on rather than a silent wrong
#: answer.
TREASURY_FUTURES_DELIVERY_MONTH_CODES = "HMUZ"

_ISIN_LENGTH = 12

#: Every U.S. Treasury carries a ``US``-prefixed ISIN, and the CTD of a U.S.
#: Treasury futures contract is by definition a U.S. Treasury. All four
#: confirmed CTDs are ``US...``.
_US_ISIN_COUNTRY_PREFIX = "US"


def _isin_check_digit_is_valid(identifier: str) -> bool:
    """ISO 6166 check digit: expand letters to two-digit values, then Luhn.

    Doubling starts at the second digit **from the right** -- the check digit
    itself is never doubled. (Getting that parity backwards makes every real
    ISIN look invalid, which is how this implementation was verified: against
    the four CTD ISINs Eddy's live run returned, all of which must pass.)
    """

    digits = "".join(str(int(character, 36)) for character in identifier)
    total = 0
    double = False
    for character in reversed(digits):
        value = int(character)
        if double:
            value *= 2
            if value > 9:
                value -= 9
        total += value
        double = not double
    return total % 10 == 0


def _require_isin(raw_value: str, field: str, security: str) -> str:
    """Require a genuine U.S. ISIN, not merely an ISIN-shaped string.

    Bloomberg can answer a field with a sentinel (``#N/A N/A``) or a
    placeholder that is neither absent nor blank, and a 12-alphanumeric shape
    check alone still admits a transposed or mistyped identifier whose check
    digit is wrong (Codex review, PR #191). Any of those would reach the
    record as the CTD's identity on an ``is_confirmed_source: true`` result.
    Three checks, in order of how much they narrow:

    1. 12 alphanumeric characters -- the shape
       ``bloomberg_bond_quote.parse_bond_identifier`` already requires;
    2. the ``US`` country prefix -- the CTD of a U.S. Treasury futures
       contract is a U.S. Treasury;
    3. the ISO 6166 check digit.

    Deliberately stricter than the trader-entry parser, which checks only the
    shape. A trader's typo is visible to them and correctable on the spot; a
    silently wrong identifier arriving from a live feed is stamped confirmed
    and shown as market data.
    """

    identifier = raw_value.strip().upper()
    if len(identifier) != _ISIN_LENGTH or not identifier.isalnum():
        raise TreasuryFuturesCTDBloombergError(
            f"Bloomberg DAPI field {field} on {security!r} did not return a 12-character "
            f"alphanumeric ISIN: {raw_value!r}"
        )
    if not identifier.startswith(_US_ISIN_COUNTRY_PREFIX):
        raise TreasuryFuturesCTDBloombergError(
            f"Bloomberg DAPI field {field} on {security!r} returned a non-U.S. ISIN "
            f"{identifier!r} -- the CTD of a U.S. Treasury futures contract must be a "
            "U.S. Treasury"
        )
    if not _isin_check_digit_is_valid(identifier):
        raise TreasuryFuturesCTDBloombergError(
            f"Bloomberg DAPI field {field} on {security!r} returned {identifier!r}, whose "
            "ISO 6166 check digit is invalid"
        )
    return identifier


def _require_delivery_ticker(contract_symbol: str, contract_code: str, security: str) -> str:
    """Require the resolved delivery month to belong to the contract asked for.

    Stage one asks ``TY1 Comdty`` which delivery month it currently is. If it
    answered anything else -- a different root, a malformed symbol -- stage two
    would fetch that *other* contract's perfectly valid CTD and this module
    would return it labelled with the requested ``contract_code``, so pricing
    would apply one contract's quote convention to another's CTD metadata
    (Codex review, PR #191). The root is the check that matters; the delivery
    month must be one these contracts actually list -- the quarterly
    ``HMUZ`` cycle, not the full twelve-month futures alphabet, which admitted
    symbols such as ``TYF7``.
    """

    symbol = contract_symbol.strip().upper()
    expected_root = BLOOMBERG_FUTURES_TICKER_ROOTS[contract_code]
    remainder = symbol[len(expected_root) :]
    if (
        not symbol.startswith(expected_root)
        or len(remainder) < 2
        or remainder[0] not in TREASURY_FUTURES_DELIVERY_MONTH_CODES
        or not remainder[1:].isdigit()
    ):
        raise TreasuryFuturesCTDBloombergError(
            f"Bloomberg DAPI resolved {security!r} to {contract_symbol!r}, which is not a "
            f"{contract_code} delivery month (expected the {expected_root} root followed by "
            f"one of the quarterly delivery months {TREASURY_FUTURES_DELIVERY_MONTH_CODES} "
            "and year digits)"
        )
    return symbol


def _parse_bloomberg_float(raw_value: str, field: str, security: str) -> float:
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise TreasuryFuturesCTDBloombergError(
            f"Bloomberg DAPI field {field} on {security!r} returned a non-numeric "
            f"value: {raw_value!r}"
        ) from exc
    if value != value or value in (float("inf"), float("-inf")):
        raise TreasuryFuturesCTDBloombergError(
            f"Bloomberg DAPI field {field} on {security!r} returned a non-finite "
            f"value: {raw_value!r}"
        )
    return value


def _parse_bloomberg_date(raw_value: str, field: str, security: str) -> date:
    """Parse a Bloomberg date string strictly as ``YYYY-MM-DD``.

    No alternative format is attempted. A date this module cannot read is a
    date it must not guess at -- a misread maturity or delivery date moves
    every number the converter produces.
    """

    try:
        return _parse_iso_date(raw_value, field)
    except ValueError as exc:
        raise TreasuryFuturesCTDBloombergError(
            f"Bloomberg DAPI field {field} on {security!r} returned a value that is not a "
            f"YYYY-MM-DD date: {raw_value!r}"
        ) from exc


def load_bloomberg_ctd_metadata(contract_code: str) -> TreasuryFuturesCTD:
    """Load current CTD metadata for ``contract_code`` from Bloomberg DAPI.

    Two requests (see the module docstring): the generic front contract
    resolves the delivery month, then that delivery month answers the CTD
    fields. Fails closed on anything missing, blank or unparseable, and never
    falls back to manual, cached or synthetic data.
    """

    unresolved = unresolved_bloomberg_ctd_fields()
    if unresolved:
        raise TreasuryFuturesCTDFieldsUnconfirmedError(
            "automatic Bloomberg CTD sourcing is not available: no confirmed Bloomberg "
            f"field mnemonic for {', '.join(unresolved)}. Run "
            "tools/bloomberg_treasury_futures_ctd_probe.py on a Bloomberg-networked "
            "workstation to confirm the candidate mnemonics, then wire the confirmed "
            "ones into data/treasury_futures_ctd.BLOOMBERG_CTD_FIELD_MAP."
        )

    normalized_code = str(contract_code).strip().upper()
    generic_security = bloomberg_generic_front_contract(normalized_code)

    try:
        import blpapi  # noqa: F401
    except ImportError as exc:
        raise TreasuryFuturesCTDBloombergError(
            "blpapi is not installed -- automatic CTD sourcing needs a Bloomberg-networked "
            f"workstation with Bloomberg's official blpapi package installed ({exc})"
        ) from exc

    # Stage one: which delivery month is the generic front contract today?
    generic_answered = _reference_data_fields(
        generic_security, [BLOOMBERG_GENERIC_CONTRACT_FIELD]
    )
    contract_symbol = _require_delivery_ticker(
        _require_answered(
            generic_answered, BLOOMBERG_GENERIC_CONTRACT_FIELD, generic_security
        ),
        normalized_code,
        generic_security,
    )
    delivery_security = bloomberg_delivery_month_security(contract_symbol)

    # Stage two: that delivery month's CTD.
    stage_two_fields = [
        BLOOMBERG_CTD_FIELD_MAP[field]
        for field in REQUIRED_BLOOMBERG_CTD_FIELDS
        if field != "contract_symbol"
    ] + list(BLOOMBERG_CTD_DISPLAY_FIELD_MAP.values())
    ctd_answered = _reference_data_fields(delivery_security, stage_two_fields)

    def _required(logical_field: str) -> str:
        return _require_answered(
            ctd_answered, BLOOMBERG_CTD_FIELD_MAP[logical_field], delivery_security
        )

    coupon_field = BLOOMBERG_CTD_FIELD_MAP["ctd_coupon_percent"]
    factor_field = BLOOMBERG_CTD_FIELD_MAP["conversion_factor"]
    maturity_field = BLOOMBERG_CTD_FIELD_MAP["ctd_maturity_date"]
    delivery_field = BLOOMBERG_CTD_FIELD_MAP["last_delivery_date"]

    ctd_identifier = _require_isin(
        _required("ctd_identifier"),
        BLOOMBERG_CTD_FIELD_MAP["ctd_identifier"],
        delivery_security,
    )
    coupon_percent = _parse_bloomberg_float(
        _required("ctd_coupon_percent"), coupon_field, delivery_security
    )
    conversion_factor = _parse_bloomberg_float(
        _required("conversion_factor"), factor_field, delivery_security
    )
    ctd_maturity_date = _parse_bloomberg_date(
        _required("ctd_maturity_date"), maturity_field, delivery_security
    )
    last_delivery_date = _parse_bloomberg_date(
        _required("last_delivery_date"), delivery_field, delivery_security
    )

    # The same domain rules the manual path enforces. A live response is not
    # exempt from them: a negative coupon, a non-positive conversion factor or
    # a delivery date at/after maturity is unusable whoever supplied it.
    if coupon_percent < 0:
        raise TreasuryFuturesCTDBloombergError(
            f"Bloomberg DAPI field {coupon_field} on {delivery_security!r} returned a "
            f"negative coupon: {coupon_percent}"
        )
    if conversion_factor <= 0:
        raise TreasuryFuturesCTDBloombergError(
            f"Bloomberg DAPI field {factor_field} on {delivery_security!r} returned a "
            f"non-positive conversion factor: {conversion_factor}"
        )
    if last_delivery_date >= ctd_maturity_date:
        raise TreasuryFuturesCTDBloombergError(
            f"Bloomberg DAPI returned last delivery {last_delivery_date.isoformat()} on or "
            f"after the CTD's maturity {ctd_maturity_date.isoformat()} for "
            f"{delivery_security!r}"
        )

    return TreasuryFuturesCTD(
        contract_code=normalized_code,
        contract_symbol=contract_symbol,
        ctd_identifier=ctd_identifier,
        ctd_coupon_percent=coupon_percent,
        ctd_maturity_date=ctd_maturity_date,
        conversion_factor=conversion_factor,
        last_delivery_date=last_delivery_date,
        source=TreasuryFuturesCTDSource.BLOOMBERG_DAPI,
        as_of=_acquisition_now(),
        ctd_cusip=ctd_answered.get(BLOOMBERG_CTD_DISPLAY_FIELD_MAP["ctd_cusip"]),
        ctd_description=ctd_answered.get(BLOOMBERG_CTD_DISPLAY_FIELD_MAP["ctd_description"]),
    )


def treasury_futures_ctd_from_manual_entry(payload: dict[str, object]) -> TreasuryFuturesCTD:
    """Build a validated, explicitly-unconfirmed CTD record from operator input.

    Every field in :data:`REQUIRED_BLOOMBERG_CTD_FIELDS` plus ``contract_code``
    and ``as_of`` is required: an answer built on a missing conversion factor,
    maturity or delivery date would be wrong rather than approximate, so this
    fails closed instead of defaulting anything. The display-only extras
    (``ctd_cusip``, ``ctd_description``) are optional.
    """

    if not isinstance(payload, dict):
        raise TreasuryFuturesCTDError("CTD metadata must be a JSON object")

    missing = [
        field
        for field in (*REQUIRED_BLOOMBERG_CTD_FIELDS, "contract_code", "as_of")
        if payload.get(field) is None
    ]
    if missing:
        raise TreasuryFuturesCTDError(f"CTD metadata is missing required field(s): {missing}")

    try:
        for field in ("contract_code", "contract_symbol", "ctd_identifier", "as_of"):
            _require_non_blank(payload[field], field)
        _require_finite_number(payload["ctd_coupon_percent"], "ctd_coupon_percent")
        _require_finite_number(payload["conversion_factor"], "conversion_factor")
        ctd_maturity_date = _parse_iso_date(payload["ctd_maturity_date"], "ctd_maturity_date")
        last_delivery_date = _parse_iso_date(payload["last_delivery_date"], "last_delivery_date")
    except ValueError as exc:
        raise TreasuryFuturesCTDError(str(exc)) from exc

    coupon_percent = float(payload["ctd_coupon_percent"])  # type: ignore[arg-type]
    conversion_factor = float(payload["conversion_factor"])  # type: ignore[arg-type]
    if coupon_percent < 0:
        raise TreasuryFuturesCTDError(
            f"ctd_coupon_percent must not be negative, got {coupon_percent}"
        )
    if conversion_factor <= 0:
        raise TreasuryFuturesCTDError(
            f"conversion_factor must be positive, got {conversion_factor}"
        )
    if last_delivery_date >= ctd_maturity_date:
        raise TreasuryFuturesCTDError(
            f"last_delivery_date {last_delivery_date.isoformat()} must be before the CTD's "
            f"maturity {ctd_maturity_date.isoformat()}"
        )

    def _optional_text(field: str) -> str | None:
        value = payload.get(field)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    return TreasuryFuturesCTD(
        contract_code=str(payload["contract_code"]).strip().upper(),
        contract_symbol=str(payload["contract_symbol"]).strip(),
        ctd_identifier=str(payload["ctd_identifier"]).strip().upper(),
        ctd_coupon_percent=coupon_percent,
        ctd_maturity_date=ctd_maturity_date,
        conversion_factor=conversion_factor,
        last_delivery_date=last_delivery_date,
        source=TreasuryFuturesCTDSource.MANUAL_UNCONFIRMED,
        as_of=str(payload["as_of"]).strip(),
        ctd_cusip=_optional_text("ctd_cusip"),
        ctd_description=_optional_text("ctd_description"),
    )
