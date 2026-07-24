"""``load_bloomberg_bond_quote``: production Bloomberg DAPI bond-quote loader (Issue #6).

Scope, per Eddy's first production Bloomberg implementation slice
authorization: one small function that connects to a local, logged-in
Bloomberg Terminal's Desktop API (DAPI) at ``localhost:8194``, requests
exactly one explicitly-selected clean price side plus currency and accrued
interest for one security, and returns exactly one validated
``BLIBondQuote`` (``data/bli_snapshot.py``) built from the live response.

**Approved live field mapping (Issue #6):**

- ``CRNCY``           -> ``BLIBondQuote.currency``
- ``ID_ISIN``         -> verified equal to the caller-supplied ``isin`` (Issue #6)
- BID  -> ``PX_BID``  -> ``BLIBondQuote.clean_price_per_100``
- MID  -> ``PX_MID``  -> ``BLIBondQuote.clean_price_per_100``
- OFFER -> ``PX_ASK`` -> ``BLIBondQuote.clean_price_per_100``
- ``INT_ACC``         -> ``BLIBondQuote.accrued_interest_per_100``

Only the one price field matching the caller's explicit ``quote_side`` is
ever requested -- this loader never requests all three sides and picks one
afterward, never infers BID/MID/OFFER from anything else, and never
requests or falls back to ``PX_LAST``.

**Canonical ISIN verification (Issue #6 ISIN verification slice):**
``ID_ISIN`` was identified as the canonical Bloomberg ISIN field via a live
``//blp/apiflds`` field-metadata search (not a guessed mnemonic) and
confirmed against ``91282CQX Govt`` / ``US91282CQX29`` before this slice was
implemented. The caller still supplies ``isin`` explicitly and it is still
what gets stored on the returned quote -- this loader now also requests
``ID_ISIN`` and requires it to equal the caller-supplied ``isin`` exactly
(no normalization, no partial match), rejecting a missing, blank,
malformed, field-exceptioned, or mismatched value. This is verification
only, never a source of the ISIN.

**Exactly one matching record (PR #128 Codex finding #1):** every
``securityData`` record received across *all* ``PARTIAL_RESPONSE`` and
``RESPONSE`` events for the request is collected before any of it is used.
Zero records, more than one record, a record missing its own security
identifier, or a record whose identifier does not equal the requested
``security`` are all rejected. Fields are read from that single validated
record only -- never combined across records.

**Whole-request deadline (PR #128 Codex finding #2):** ``_REQUEST_TIMEOUT_MS``
is one deadline for the entire request, computed once via a monotonic clock
before the event loop starts. Each ``session.nextEvent`` call receives only
the time remaining until that deadline, not a fresh full timeout every call;
once the deadline has passed -- whether from one slow response or many
non-terminal events -- the loop raises without waiting further.

**Malformed element access (PR #128 Codex finding #3):** reading a
requested field's or the security identifier's string value from a
``blpapi`` element can itself raise a native ``blpapi`` exception (e.g. a
field present but of an unexpected underlying type). That native exception
is caught narrowly (only ``blpapi.exception.Exception``, never a bare
``except Exception``) and converted to ``BLIBloombergDapiError`` naming the
affected field/security -- it never propagates as a raw ``blpapi``
exception and never gets silently treated as "missing".

**Caller-remediation contract:** a successful call returns a validated
``BLIBondQuote`` directly. Every failure raises ``BLIBloombergDapiError``
(a ``RuntimeError``) with a message identifying which stage failed: blpapi
not installed, session-start (connection) failure, ``//blp/refdata``
service-open failure, request timeout, a DAPI ``responseError``, a
record-count/identity mismatch, a security-level error, any field
exception, a malformed element, an ``ID_ISIN`` mismatch, or a
missing/non-numeric/non-finite value for a requested field. This loader
never silently returns partial data --
every field that is missing, exceptional, or malformed aborts the whole
call. ``security``/``isin`` blank checks and an invalid ``quote_side``
raise ``ValueError`` directly (a caller-input problem, not a
Bloomberg-response problem). Domain rules already enforced by
``BLIBondQuote`` itself (positive clean price, non-negative accrued
interest, valid ``Currency`` member) are not duplicated here and propagate
as whatever ``ValueError`` ``BLIBondQuote.__post_init__`` raises.

**Session lifecycle:** ``blpapi`` is imported lazily, inside this function,
so importing this module (or any module that imports it) never requires
``blpapi`` to be installed -- only calling this function does.
``session.stop()`` is called on every path once a ``Session`` object
exists, whether the call succeeds or fails.

**Hard non-goals (Issue #6 scope cap):** no ISIN retrieval or inference --
the caller still supplies ``isin`` explicitly and it is what gets stored on
the returned quote; Bloomberg's ``ID_ISIN`` is used only to verify that
supplied value, never to source, replace, or normalize it. No
``BondReferenceData``, no wiring into ``BLIMarketDataSnapshot``, the
request builder, the pricing engine, the workbench, or the UI (this loader
is not currently called by any of them), no forward price, no
``PRICE_VOL``, no OVME premium, no curve/repo/yield-conversion/option
calculation of any kind, no provider interface, base class, factory,
registry, cache, configuration framework, persistence, or compatibility
wrapper. ``BLIBondQuote`` and its sibling schemas are unmodified.

**Instrument-first Bloomberg lookup (added alongside, not in place of, the
above):** :func:`load_bloomberg_bond_identity_and_quote` resolves one bond's
own identity (``ID_ISIN``, ``ID_CUSIP``, name) and one quote side's price
via a symbology-qualified identifier (``/isin/...`` or ``/cusip/...``) the
caller has already validated, with no expected-ISIN verification (there is
no case yet to verify against) -- the entry point for a trader who wants to
find a bond by ISIN/CUSIP before any Case JSON exists. Deliberately a
separate, self-contained implementation rather than sharing
:func:`load_bloomberg_bond_quote`'s internals, so that function's production
behavior is never put at risk by this one's needs.

**Bond Master fields (PR #141 second revision).** The same lookup also
returns a best-effort ``"bond_master"`` dict covering every
``BondReferenceData`` field (coupon, coupon frequency, issue/maturity/first/
last coupon dates, redemption amount, callable/sinkable flags, bond type,
yield convention, business-day convention). No Bloomberg mnemonic is guessed
into production use: ``_BOND_MASTER_FIELD_MAP`` starts empty and every field
is ``None`` until Eddy confirms its mnemonic on a real Bloomberg workstation
via ``tools/bloomberg_dapi_probe.py`` (see that script and PR #141's body for
the exact probe command). A Bond Master field's absence, field exception, or
simply not being mapped yet never fails the request -- unlike the seven
required identity/quote fields above, which still make this call succeed or
fail as a whole exactly as before.
"""

from __future__ import annotations

import math
import time

from shiori_pricing_lab.data._validation import _require_non_blank
from shiori_pricing_lab.data.bli_snapshot import BLIBondQuote, BLIMarketDataStatus, BLIQuoteBasis
from shiori_pricing_lab.products.enums import TreasuryFTPQuoteSide, coerce_enum

_DAPI_HOST = "localhost"
_DAPI_PORT = 8194
_REFDATA_SERVICE = "//blp/refdata"
_REQUEST_TIMEOUT_MS = 10_000

_CURRENCY_FIELD = "CRNCY"
_ACCRUED_INTEREST_FIELD = "INT_ACC"
_SECURITY_IDENTIFIER_FIELD = "security"
_ISIN_FIELD = "ID_ISIN"

# Approved live field mapping (Issue #6) -- the only three
# TreasuryFTPQuoteSide members that exist, so this mapping is exhaustive
# and needs no fallback/default branch.
_PRICE_FIELD_BY_QUOTE_SIDE = {
    TreasuryFTPQuoteSide.BID: "PX_BID",
    TreasuryFTPQuoteSide.MID: "PX_MID",
    TreasuryFTPQuoteSide.OFFER: "PX_ASK",
}

_SOURCE_SYSTEM = "BLOOMBERG_DAPI"

# Testable seam for the whole-request deadline (PR #128 Codex finding #2):
# a plain module-level alias, monkeypatchable in tests without touching the
# real `time` module.
_monotonic = time.monotonic


class BLIBloombergDapiError(RuntimeError):
    """Raised for any Bloomberg DAPI connectivity, session, or response failure.

    Covers: ``blpapi`` not installed, session-start (connection) failure,
    ``//blp/refdata`` service-open failure, request timeout, a DAPI
    ``responseError``, a record-count or security-identity mismatch, a
    security-level error, a field exception, a malformed element, a
    missing required field, or a non-numeric/non-finite returned value.
    Deliberately one exception type -- these are all the same
    caller-remediation outcome ("Bloomberg DAPI did not return one usable
    quote"), not distinct conditions a caller would remediate differently.
    """


def parse_bond_identifier(raw: str) -> tuple[str, str]:
    """Parse a trader-entered bond identifier into a Bloomberg symbology-qualified string.

    Instrument-first Bloomberg lookup: the trader enters a plain ISIN or
    CUSIP -- never a Bloomberg yellow-key ticker (e.g. ``"... Govt"``), and
    this function never guesses, infers, or appends one. ``raw`` is trimmed
    of outer whitespace and uppercased; exactly 12 remaining alphanumeric
    characters is treated as an ISIN, exactly 9 as a CUSIP. Every other
    length or any non-alphanumeric character is rejected outright with a
    ``ValueError`` naming the original input -- there is no fallback format.

    Returns ``(kind, bloomberg_identifier)`` where ``kind`` is ``"ISIN"`` or
    ``"CUSIP"`` and ``bloomberg_identifier`` is the symbology-qualified
    string Bloomberg DAPI accepts directly as a "securities" entry
    (``"/isin/<ISIN>"`` or ``"/cusip/<CUSIP>"``) -- this internal request
    string is meant for :func:`load_bloomberg_bond_identity_and_quote` only,
    never for display in an editable UI field.
    """

    if not isinstance(raw, str):
        raise ValueError("bond identifier must be a string")
    normalized = raw.strip().upper()
    if len(normalized) == 12 and normalized.isalnum():
        return "ISIN", f"/isin/{normalized}"
    if len(normalized) == 9 and normalized.isalnum():
        return "CUSIP", f"/cusip/{normalized}"
    raise ValueError(
        "bond identifier must be a 12-character ISIN or a 9-character CUSIP "
        f"(alphanumeric only), got {raw!r}"
    )


def load_bloomberg_bond_quote(
    *,
    security: str,
    isin: str,
    quote_side: TreasuryFTPQuoteSide,
) -> BLIBondQuote:
    """Load one live Bloomberg DAPI bond quote into a validated ``BLIBondQuote``.

    ``security`` is the Bloomberg security identifier requested from DAPI
    (e.g. ``"91282CQX Govt"``). ``isin`` is stored on the returned quote
    verbatim and is also verified against Bloomberg's ``ID_ISIN`` field for
    ``security`` -- this function never retrieves or infers an ISIN, only
    confirms the caller-supplied one matches exactly. ``quote_side`` selects
    exactly one price field (see the module docstring's field mapping); it
    is required and has no default.

    Raises ``ValueError`` if ``security``/``isin`` is blank or ``quote_side``
    is not a valid ``TreasuryFTPQuoteSide``. Raises ``BLIBloombergDapiError``
    for every Bloomberg-side failure (see the module docstring's
    caller-remediation contract). Propagates unchanged whatever
    ``ValueError`` ``BLIBondQuote.__post_init__`` itself raises for a
    domain rule (e.g. a non-positive returned clean price) -- this function
    duplicates none of those checks.
    """

    _require_non_blank(security, "security")
    _require_non_blank(isin, "isin")
    quote_side = coerce_enum(quote_side, TreasuryFTPQuoteSide, "quote_side")
    price_field = _PRICE_FIELD_BY_QUOTE_SIDE[quote_side]

    try:
        import blpapi
    except ImportError as exc:
        raise BLIBloombergDapiError(
            "blpapi is not installed -- Bloomberg's official blpapi package is "
            "required in this environment to load a live Bloomberg DAPI bond quote"
        ) from exc

    session_options = blpapi.SessionOptions()
    session_options.setServerHost(_DAPI_HOST)
    session_options.setServerPort(_DAPI_PORT)
    session = blpapi.Session(session_options)

    try:
        if not session.start():
            raise BLIBloombergDapiError(
                f"Bloomberg DAPI session failed to start against {_DAPI_HOST}:{_DAPI_PORT} "
                "-- confirm a Bloomberg Terminal is running and logged in locally"
            )

        if not session.openService(_REFDATA_SERVICE):
            raise BLIBloombergDapiError(f"Bloomberg DAPI failed to open service {_REFDATA_SERVICE}")

        service = session.getService(_REFDATA_SERVICE)
        request = service.createRequest("ReferenceDataRequest")
        request.append("securities", security)
        request.append("fields", _CURRENCY_FIELD)
        request.append("fields", _ISIN_FIELD)
        request.append("fields", price_field)
        request.append("fields", _ACCRUED_INTEREST_FIELD)

        session.sendRequest(request)

        # Collected across every PARTIAL_RESPONSE/RESPONSE event for the
        # whole request -- validated as a single group only after the loop
        # ends, so "exactly one record" can never be checked prematurely
        # against only the first event received (finding #1).
        security_data_records = []

        deadline = _monotonic() + _REQUEST_TIMEOUT_MS / 1000.0
        done = False
        while not done:
            remaining_seconds = deadline - _monotonic()
            if remaining_seconds <= 0:
                raise BLIBloombergDapiError(
                    f"Bloomberg DAPI request timed out waiting for a response for {security!r}"
                )
            remaining_ms = max(1, int(remaining_seconds * 1000))
            event = session.nextEvent(remaining_ms)

            if event.eventType() == blpapi.Event.TIMEOUT:
                raise BLIBloombergDapiError(
                    f"Bloomberg DAPI request timed out waiting for a response for {security!r}"
                )

            if event.eventType() not in (blpapi.Event.PARTIAL_RESPONSE, blpapi.Event.RESPONSE):
                continue

            for msg in event:
                if msg.hasElement("responseError"):
                    raise BLIBloombergDapiError(
                        f"Bloomberg DAPI responseError for {security!r}: "
                        f"{msg.getElement('responseError')}"
                    )

                security_data_array = msg.getElement("securityData")
                for i in range(security_data_array.numValues()):
                    security_data_records.append(security_data_array.getValueAsElement(i))

            if event.eventType() == blpapi.Event.RESPONSE:
                done = True
    finally:
        session.stop()

    if len(security_data_records) == 0:
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI returned zero securityData records for {security!r}, "
            "expected exactly one"
        )
    if len(security_data_records) > 1:
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI returned {len(security_data_records)} securityData records for "
            f"{security!r}, expected exactly one"
        )

    security_data = security_data_records[0]

    if not security_data.hasElement(_SECURITY_IDENTIFIER_FIELD):
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI securityData record for {security!r} is missing its own "
            "security identifier"
        )
    returned_security = _get_element_as_string(
        blpapi, security_data, _SECURITY_IDENTIFIER_FIELD, security
    )
    if returned_security != security:
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI securityData record identifier {returned_security!r} does not "
            f"match requested security {security!r}"
        )

    if security_data.hasElement("securityError"):
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI securityError for {security!r}: "
            f"{security_data.getElement('securityError')}"
        )

    if security_data.hasElement("fieldExceptions"):
        field_exceptions = security_data.getElement("fieldExceptions")
        if field_exceptions.numValues() > 0:
            raise BLIBloombergDapiError(
                f"Bloomberg DAPI field exception for {security!r}: "
                f"{field_exceptions.getValueAsElement(0)}"
            )

    field_data = security_data.getElement("fieldData")

    if not field_data.hasElement(_CURRENCY_FIELD):
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI response for {security!r} is missing {_CURRENCY_FIELD}"
        )
    raw_currency = _get_element_as_string(blpapi, field_data, _CURRENCY_FIELD, security)

    if not field_data.hasElement(_ISIN_FIELD):
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI response for {security!r} is missing {_ISIN_FIELD}"
        )
    raw_isin = _get_element_as_string(blpapi, field_data, _ISIN_FIELD, security)

    if not field_data.hasElement(price_field):
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI response for {security!r} is missing {price_field}"
        )
    raw_price = _get_element_as_string(blpapi, field_data, price_field, security)

    if not field_data.hasElement(_ACCRUED_INTEREST_FIELD):
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI response for {security!r} is missing {_ACCRUED_INTEREST_FIELD}"
        )
    raw_accrued = _get_element_as_string(blpapi, field_data, _ACCRUED_INTEREST_FIELD, security)

    if not raw_currency:
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI response for {security!r} is missing {_CURRENCY_FIELD}"
        )
    if not raw_isin:
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI response for {security!r} is missing {_ISIN_FIELD}"
        )
    if raw_isin != isin:
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI {_ISIN_FIELD} {raw_isin!r} does not match requested isin {isin!r}"
        )
    if not raw_price:
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI response for {security!r} is missing {price_field}"
        )
    if not raw_accrued:
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI response for {security!r} is missing {_ACCRUED_INTEREST_FIELD}"
        )

    clean_price_per_100 = _parse_finite_float(raw_price, price_field)
    accrued_interest_per_100 = _parse_finite_float(raw_accrued, _ACCRUED_INTEREST_FIELD)

    return BLIBondQuote(
        isin=isin,
        currency=raw_currency,
        price_type=BLIQuoteBasis.PRICE,
        quote_side=quote_side,
        source_system=_SOURCE_SYSTEM,
        status=BLIMarketDataStatus.ACTIVE,
        clean_price_per_100=clean_price_per_100,
        accrued_interest_per_100=accrued_interest_per_100,
    )


_NAME_FIELD = "NAME"
_SECURITY_DESCRIPTION_FIELD = "SECURITY_DES"
_CUSIP_FIELD = "ID_CUSIP"

# --- Bond Master (bond_reference_data_universe) fields, PR #141 second
# revision -------------------------------------------------------------------
#
# Empty until a candidate Bloomberg mnemonic has been confirmed against a
# live DAPI response (see tools/bloomberg_dapi_probe.py and PR #141's body
# for the exact probe command). No mnemonic is guessed into production use
# here -- add a `destination_field: "MNEMONIC"` entry only after Eddy
# confirms it on his own Bloomberg workstation; it is then automatically
# requested and populated below with no other code change required.
#
# Keys are BondReferenceData's own field names verbatim (never invented);
# every one of them is always a key in the returned "bond_master" dict
# regardless of how many entries this map currently has, so a caller never
# has to guess which keys might be present -- an unconfirmed, not-yet-
# requested, absent, or field-exceptioned value is simply None, exactly
# like a genuine Bloomberg-side miss (requirement: never let one bad/
# unconfirmed field take down the rest of an otherwise-successful lookup).
_BOND_MASTER_FIELD_MAP: dict[str, str] = {}

_BOND_MASTER_DESTINATION_FIELDS = (
    "coupon",
    "coupon_frequency",
    "issue_date",
    "maturity_date",
    "day_count",
    "first_coupon_date",
    "last_coupon_date",
    "redemption_amount",
    "callable_flag",
    "sinkable_flag",
    "bond_type",
    "yield_convention",
    "business_day_convention",
)


def load_bloomberg_bond_identity_and_quote(
    *,
    identifier: str,
    quote_side: TreasuryFTPQuoteSide,
) -> dict:
    """Load one live Bloomberg bond's identity and quote via a symbology-qualified identifier.

    Instrument-first Bloomberg lookup entry point: ``identifier`` must
    already be a Bloomberg symbology-qualified security string --
    ``/isin/<12-character ISIN>`` or ``/cusip/<9-character CUSIP>`` --
    constructed by the caller from a bounded, validated identifier (see the
    workbench's own bond-identifier parser). This function never guesses,
    appends, or falls back to a yellow-key ticker (e.g. ``"... Govt"``):
    whatever Bloomberg DAPI reports for the exact identifier given is
    reported back unchanged, success or failure, with no fallback chain.

    Unlike :func:`load_bloomberg_bond_quote`, this function takes no
    caller-supplied expected ISIN to verify against -- there is no active
    pricing case yet at this point in the workflow; it exists purely to
    resolve one bond's own identity (its canonical ``ID_ISIN``, ``ID_CUSIP``,
    and a human-readable name) alongside one quote side's clean price and
    accrued interest, before any Case JSON has been loaded.

    Returns one complete result as a dict with keys ``"isin"``, ``"cusip"``,
    ``"name"``, ``"currency"``, ``"quote_side"``, ``"clean_price_per_100"``,
    ``"accrued_interest_per_100"`` -- never a partial one for these seven --
    plus ``"bond_master"`` (PR #141 second revision): a dict with one key per
    ``BondReferenceData`` field this lookup can ever populate
    (``_BOND_MASTER_DESTINATION_FIELDS``), each ``None`` unless
    ``_BOND_MASTER_FIELD_MAP`` has a confirmed Bloomberg mnemonic for it (see
    that map's own docstring -- it is empty until Eddy confirms a mnemonic via
    ``tools/bloomberg_dapi_probe.py``). Unlike the seven required fields
    above, a Bond Master field that is absent, field-exceptioned, or simply
    not yet mapped is reported as ``None`` and never fails the request --
    the identity/quote result this function has always returned is never put
    at risk by an unconfirmed or unentitled Bond Master field. ``"name"``
    prefers Bloomberg's ``SECURITY_DES`` (a human-readable security
    description), falling back to ``NAME`` only if ``SECURITY_DES`` itself is
    blank.

    Raises ``ValueError`` if ``identifier`` is blank or ``quote_side`` is not
    a valid ``TreasuryFTPQuoteSide``. Raises ``BLIBloombergDapiError`` for
    every Bloomberg-side failure on the seven required fields, with the same
    session-lifecycle, exactly-one-record, security-identity, security-
    level-error, and malformed-element handling as
    :func:`load_bloomberg_bond_quote` -- deliberately a separate, self-
    contained implementation rather than a shared one, so that function's
    own production behavior and extensive existing test coverage are never
    put at risk by this one's needs.
    """

    _require_non_blank(identifier, "identifier")
    quote_side = coerce_enum(quote_side, TreasuryFTPQuoteSide, "quote_side")
    price_field = _PRICE_FIELD_BY_QUOTE_SIDE[quote_side]

    try:
        import blpapi
    except ImportError as exc:
        raise BLIBloombergDapiError(
            "blpapi is not installed -- Bloomberg's official blpapi package is "
            "required in this environment to load a live Bloomberg bond identity/quote"
        ) from exc

    session_options = blpapi.SessionOptions()
    session_options.setServerHost(_DAPI_HOST)
    session_options.setServerPort(_DAPI_PORT)
    session = blpapi.Session(session_options)

    try:
        if not session.start():
            raise BLIBloombergDapiError(
                f"Bloomberg DAPI session failed to start against {_DAPI_HOST}:{_DAPI_PORT} "
                "-- confirm a Bloomberg Terminal is running and logged in locally"
            )

        if not session.openService(_REFDATA_SERVICE):
            raise BLIBloombergDapiError(f"Bloomberg DAPI failed to open service {_REFDATA_SERVICE}")

        service = session.getService(_REFDATA_SERVICE)
        request = service.createRequest("ReferenceDataRequest")
        request.append("securities", identifier)
        request.append("fields", _ISIN_FIELD)
        request.append("fields", _CUSIP_FIELD)
        request.append("fields", _NAME_FIELD)
        request.append("fields", _SECURITY_DESCRIPTION_FIELD)
        request.append("fields", _CURRENCY_FIELD)
        request.append("fields", price_field)
        request.append("fields", _ACCRUED_INTEREST_FIELD)
        # Bond Master fields: only ever the confirmed subset of
        # _BOND_MASTER_FIELD_MAP (empty by default -- see its own docstring
        # above). Requesting zero extra fields is a complete no-op.
        for bloomberg_mnemonic in _BOND_MASTER_FIELD_MAP.values():
            request.append("fields", bloomberg_mnemonic)

        session.sendRequest(request)

        security_data_records = []

        deadline = _monotonic() + _REQUEST_TIMEOUT_MS / 1000.0
        done = False
        while not done:
            remaining_seconds = deadline - _monotonic()
            if remaining_seconds <= 0:
                raise BLIBloombergDapiError(
                    f"Bloomberg DAPI request timed out waiting for a response for {identifier!r}"
                )
            remaining_ms = max(1, int(remaining_seconds * 1000))
            event = session.nextEvent(remaining_ms)

            if event.eventType() == blpapi.Event.TIMEOUT:
                raise BLIBloombergDapiError(
                    f"Bloomberg DAPI request timed out waiting for a response for {identifier!r}"
                )

            if event.eventType() not in (blpapi.Event.PARTIAL_RESPONSE, blpapi.Event.RESPONSE):
                continue

            for msg in event:
                if msg.hasElement("responseError"):
                    raise BLIBloombergDapiError(
                        f"Bloomberg DAPI responseError for {identifier!r}: "
                        f"{msg.getElement('responseError')}"
                    )

                security_data_array = msg.getElement("securityData")
                for i in range(security_data_array.numValues()):
                    security_data_records.append(security_data_array.getValueAsElement(i))

            if event.eventType() == blpapi.Event.RESPONSE:
                done = True
    finally:
        session.stop()

    if len(security_data_records) == 0:
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI returned zero securityData records for {identifier!r}, "
            "expected exactly one"
        )
    if len(security_data_records) > 1:
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI returned {len(security_data_records)} securityData records for "
            f"{identifier!r}, expected exactly one"
        )

    security_data = security_data_records[0]

    if not security_data.hasElement(_SECURITY_IDENTIFIER_FIELD):
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI securityData record for {identifier!r} is missing its own "
            "security identifier"
        )
    returned_security = _get_element_as_string(
        blpapi, security_data, _SECURITY_IDENTIFIER_FIELD, identifier
    )
    if returned_security != identifier:
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI securityData record identifier {returned_security!r} does not "
            f"match requested identifier {identifier!r}"
        )

    if security_data.hasElement("securityError"):
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI securityError for {identifier!r}: "
            f"{security_data.getElement('securityError')}"
        )

    # No blanket "any field exception fails the whole request" check here
    # (unlike load_bloomberg_bond_quote, whose fields are all required and
    # size-of-seven): the Bond Master fields added below are best-effort and
    # must be able to fail individually -- a field exception on one of them
    # must never take down the already-reliable identity/quote fields. Each
    # required field's own presence in fieldData is still independently
    # enforced by _require_field immediately below (a field exception always
    # means the field is simply absent from fieldData), so required-field
    # strictness is unchanged.
    field_data = security_data.getElement("fieldData")

    def _require_field(field_name: str) -> str:
        if not field_data.hasElement(field_name):
            raise BLIBloombergDapiError(
                f"Bloomberg DAPI response for {identifier!r} is missing {field_name}"
            )
        raw_value = _get_element_as_string(blpapi, field_data, field_name, identifier)
        if not raw_value:
            raise BLIBloombergDapiError(
                f"Bloomberg DAPI response for {identifier!r} is missing {field_name}"
            )
        return raw_value

    def _optional_field(field_name: str) -> str | None:
        """Best-effort read: absent, blank, or a malformed value all become
        ``None`` rather than aborting the request -- unlike ``_require_field``,
        used only for the not-yet-confirmed Bond Master fields in
        ``_BOND_MASTER_FIELD_MAP``. Returns the raw Bloomberg string verbatim
        (no numeric/enum coercion) -- deciding how to parse a field's value
        is a separate, explicit step for once its mnemonic is confirmed."""

        if not field_data.hasElement(field_name):
            return None
        try:
            raw_value = _get_element_as_string(blpapi, field_data, field_name, identifier)
        except BLIBloombergDapiError:
            return None
        return raw_value or None

    raw_isin = _require_field(_ISIN_FIELD)
    raw_cusip = _require_field(_CUSIP_FIELD)
    raw_currency = _require_field(_CURRENCY_FIELD)
    raw_price = _require_field(price_field)
    raw_accrued = _require_field(_ACCRUED_INTEREST_FIELD)

    # SECURITY_DES (a human-readable security description) is preferred for
    # display; NAME is used only if SECURITY_DES itself is blank. Both are
    # still required fields overall -- a response missing both fails
    # outright rather than silently substituting an empty name.
    raw_name = ""
    if field_data.hasElement(_SECURITY_DESCRIPTION_FIELD):
        raw_name = _get_element_as_string(
            blpapi, field_data, _SECURITY_DESCRIPTION_FIELD, identifier
        )
    if not raw_name:
        raw_name = _require_field(_NAME_FIELD)

    clean_price_per_100 = _parse_finite_float(raw_price, price_field)
    accrued_interest_per_100 = _parse_finite_float(raw_accrued, _ACCRUED_INTEREST_FIELD)

    # Every BondReferenceData destination is always a key here, whether or
    # not _BOND_MASTER_FIELD_MAP currently has a confirmed mnemonic for it --
    # an unmapped, absent, or field-exceptioned one is simply None, exactly
    # like a genuine Bloomberg-side miss (never a fabricated/synthetic value).
    bond_master: dict[str, str | None] = {}
    for destination_field in _BOND_MASTER_DESTINATION_FIELDS:
        bloomberg_mnemonic = _BOND_MASTER_FIELD_MAP.get(destination_field)
        bond_master[destination_field] = (
            _optional_field(bloomberg_mnemonic) if bloomberg_mnemonic is not None else None
        )

    return {
        "isin": raw_isin,
        "cusip": raw_cusip,
        "name": raw_name,
        "currency": raw_currency,
        "quote_side": quote_side.value,
        "clean_price_per_100": clean_price_per_100,
        "accrued_interest_per_100": accrued_interest_per_100,
        "bond_master": bond_master,
    }


def _get_element_as_string(blpapi, element, field_name: str, security: str) -> str:
    """Read ``field_name`` as a string, converting a native ``blpapi`` failure.

    Catches only ``blpapi.exception.Exception`` -- the base of every native
    ``blpapi`` element-access error (e.g. reading a field whose actual
    underlying type cannot convert to a string) -- never a bare
    ``except Exception``, so an unrelated programming error in this module
    still propagates as itself rather than being mislabeled as a Bloomberg
    response problem.
    """

    try:
        return element.getElementAsString(field_name)
    except blpapi.exception.Exception as exc:
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI returned a malformed value for {field_name} on {security!r}: {exc}"
        ) from exc


def _parse_finite_float(raw_value: str, field_name: str) -> float:
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI field {field_name} returned a non-numeric value: {raw_value!r}"
        ) from exc
    if not math.isfinite(value):
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI field {field_name} returned a non-finite value: {raw_value!r}"
        )
    return value
