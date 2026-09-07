"""``load_bloomberg_bond_yield_history``: raw Bloomberg historical bond-Yield
observations (Issue #196).

**What this module is.** One canonical, read-only loader that asks Bloomberg
Desktop API for one bond's own historical Yield series over one explicit date
range, and returns exactly the dated observations Bloomberg answered with,
plus the provenance needed to audit them later. It is the raw market-data
foundation the future Historical Volatility work will read from.

**What this module is deliberately not.** It computes no Yield Change, no
standard deviation, no annualization, and no volatility of any kind. It does
not touch pricing, the VCUB store or resolver, PRICE_VOL/YIELD_VOL, Forward,
or Discounting, and it never selects a benchmark or proxy series when the
bond's own history is thin or absent. Issue #196 stops at the observations;
the statistical methodology is a later, separately approved issue.

**The Yield field is never guessed here.** ``yield_field`` is a required,
caller-supplied Bloomberg mnemonic with no default anywhere in this module,
and no candidate list, no fallback chain, and no "closest-looking" search.
The mnemonic a caller passes must come from workstation evidence --
``tools/bloomberg_bond_yield_field_probe.py`` is the narrow discovery path
that produces it from Bloomberg's own ``//blp/apiflds`` documentation and a
bounded historical-availability check. This module only validates the
*shape* of the mnemonic (uppercase ``A-Z``/``0-9``/``_``), which is a
request-hygiene check, never a claim about what the field means.

``field_meaning``/``field_unit`` are likewise optional, caller-supplied
passthrough provenance -- carried verbatim onto the result and never
inferred, defaulted, or derived from the mnemonic. ``None`` means "not
established by this request", which is what a consumer must display rather
than assuming percent, decimal, or basis points.

**Bloomberg observations are preserved exactly (Issue #196 §B).** The
request is pinned so Bloomberg itself never fills anything in:

- ``periodicitySelection = DAILY`` / ``periodicityAdjustment = ACTUAL`` --
  the natural daily series, not a resampled or calendar-aligned one;
- ``nonTradingDayFillOption = ACTIVE_DAYS_ONLY`` -- Bloomberg returns only
  the days it actually holds an observation for, so a non-trading day is
  simply absent from the response rather than manufactured;
- ``nonTradingDayFillMethod = NIL_VALUE`` -- and if a row is returned
  anyway, it carries nothing rather than the previous day's value.

That last pair matters: Bloomberg's own ``HistoricalDataRequest`` defaults
carry a *previous value* forward onto non-trading days. Leaving those
defaults in place would silently forward-fill the series this issue exists
to keep raw, so both are set explicitly on every request. They are request
options published by Bloomberg, not a market-data interpretation.

Nothing on this side of the wire fills either: this loader never
interpolates, forward-fills, back-fills, smooths, winsorizes, resamples,
rounds, or invents a date. A day Bloomberg did not answer for is a gap, and
stays a gap. A row Bloomberg returned with no value at all is preserved as
an explicit ``yield_value=None`` observation on its own date -- a visible
hole, never a zero and never a neighbour's number.

That last case is read with ``hasElement(field, True)``, not a bare
``hasElement``. Bloomberg's own null element is *present* under the bare
call, and reading it as a string raises -- so the bare call would abort the
whole series on precisely the row this loader exists to keep. Excluding null
elements makes a returned null indistinguishable from an absent one, which
is what it means: no value on that date.

**An empty series is a valid answer.** A bond with no observations in the
requested window returns ``observations=()`` with full provenance -- never a
synthetic zero series, never a widened date range, never a substituted
security.

**Fail-closed conditions**, every one raising
:class:`~shiori_pricing_lab.data.bloomberg_bond_quote.BLIBloombergDapiError`
and aborting the whole call (there is no partial-series return):

- ``blpapi`` missing, session-start failure, ``//blp/refdata`` open failure,
  request timeout, or a DAPI ``responseError``;
- a native ``blpapi`` exception raised anywhere in the request lifecycle --
  Bloomberg dropping the connection after the service opened, so
  ``sendRequest``/``nextEvent``/a response accessor throws blpapi's own type.
  It is converted rather than propagated, because callers act on this
  module's promise: the workbench route answers a Bloomberg-side failure with
  HTTP 502, and the acceptance CLI records a failed run instead of dying;
- no ``securityData`` in the response at all;
- ``securityData`` records naming more than one distinct security (a
  paginated response must be about the one security requested);
- a ``securityError`` on the security;
- any ``fieldExceptions`` entry (an unrecognised or unentitled mnemonic
  fails here, visibly, rather than returning an empty series that looks
  like a bond with no history);
- an observation row with no ``date``, or a ``date`` that is not a strict
  ``YYYY-MM-DD`` calendar date;
- an observation date outside the requested ``[start_date, end_date]``
  window (Bloomberg answering outside the window is a semantics surprise,
  not something to trim silently);
- a duplicate observation date -- there is no documented Bloomberg rule for
  which of two same-dated observations wins, so this module refuses to
  pick one (Issue #196 §B);
- a present but non-numeric or non-finite Yield value.

**Session lifecycle and reuse.** Same lazy ``import blpapi``, session/
event-loop and error-conversion conventions ``bloomberg_bond_quote.py``
established as this repository's one production Bloomberg DAPI pattern --
``_DAPI_HOST``/``_DAPI_PORT``/``_REFDATA_SERVICE``/``_REQUEST_TIMEOUT_MS``,
``BLIBloombergDapiError``, ``_get_element_as_string`` and
``_parse_finite_float`` are imported and reused from that module rather
than reimplemented, and ``session.stop()`` runs on every post-start path.
Importing this module never requires ``blpapi`` to be installed.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime

from shiori_pricing_lab.data._validation import _parse_iso_date, _require_non_blank
from shiori_pricing_lab.data.bloomberg_bond_quote import (
    _DAPI_HOST,
    _DAPI_PORT,
    _REFDATA_SERVICE,
    _REQUEST_TIMEOUT_MS,
    BLIBloombergDapiError,
    _get_element_as_string,
    _parse_finite_float,
)

SOURCE_SYSTEM = "BLOOMBERG_DAPI"

_HISTORICAL_REQUEST_NAME = "HistoricalDataRequest"
_BLOOMBERG_REQUEST_DATE_FORMAT = "%Y%m%d"
_OBSERVATION_DATE_ELEMENT = "date"

# Pinned so Bloomberg itself never fills a non-trading day in -- see the
# module docstring. These are published request options, not a reading of
# what any value means.
_PERIODICITY_SELECTION = "DAILY"
_PERIODICITY_ADJUSTMENT = "ACTUAL"
_NON_TRADING_DAY_FILL_OPTION = "ACTIVE_DAYS_ONLY"
_NON_TRADING_DAY_FILL_METHOD = "NIL_VALUE"

_HISTORICAL_REQUEST_OPTIONS: tuple[tuple[str, str], ...] = (
    ("periodicitySelection", _PERIODICITY_SELECTION),
    ("periodicityAdjustment", _PERIODICITY_ADJUSTMENT),
    ("nonTradingDayFillOption", _NON_TRADING_DAY_FILL_OPTION),
    ("nonTradingDayFillMethod", _NON_TRADING_DAY_FILL_METHOD),
)

# Request hygiene only: the shape a Bloomberg field mnemonic takes. Never a
# claim that a shape-valid mnemonic exists, means a Yield, or is the right
# one -- an unknown mnemonic comes back as a fieldException and fails closed.
_FIELD_MNEMONIC_RE = re.compile(r"^[A-Z0-9_]+$")

# Testable seams, mirroring bloomberg_bond_quote.py's own `_monotonic`
# pattern: this module's own aliases, monkeypatchable in this module's tests
# so no real clock is read in CI.
_monotonic = time.monotonic


def _acquisition_now() -> datetime:
    """One offset-aware acquisition timestamp, read from the platform clock.

    Called only after a response has been fully validated, so a failed
    acquisition never stamps a time onto anything.
    """

    return datetime.now().astimezone()


@dataclass(frozen=True)
class BondYieldObservation:
    """One dated Bloomberg Yield observation, exactly as returned.

    ``raw_value`` is Bloomberg's own string for the value, preserved so a
    consumer can show every digit Bloomberg sent without a float-formatting
    round trip. ``yield_value`` is that same string parsed to a finite float
    for charting -- never rescaled, never unit-converted (this module does
    not know the field's unit; see ``field_unit``).

    Both are ``None`` together when Bloomberg returned the row but no value
    for it: an explicit hole on a real date, never a zero.
    """

    observation_date: date
    yield_value: float | None
    raw_value: str | None


@dataclass(frozen=True)
class BloombergBondYieldHistory:
    """One bond's raw historical Yield series plus its acquisition provenance.

    ``requested_identifier`` is the string this loader sent to Bloomberg;
    ``security`` is the identifier Bloomberg itself echoed back on the
    response. They are recorded separately on purpose -- the second is
    Bloomberg's own answer about what it resolved, not this repo's request.

    ``observations`` is sorted ascending by ``observation_date`` and holds no
    duplicate date. It may be empty.
    """

    requested_identifier: str
    security: str
    yield_field: str
    field_meaning: str | None
    field_unit: str | None
    requested_start_date: date
    requested_end_date: date
    observations: tuple[BondYieldObservation, ...]
    source_system: str
    acquired_at: str


def _validate_yield_field(yield_field: object) -> str:
    _require_non_blank(yield_field, "yield_field")
    assert isinstance(yield_field, str)
    candidate = yield_field.strip()
    if not _FIELD_MNEMONIC_RE.match(candidate):
        raise ValueError(
            "yield_field must be a Bloomberg field mnemonic (uppercase A-Z, digits "
            f"and underscores only), got {yield_field!r}"
        )
    return candidate


def _coerce_request_date(value: object, field_name: str) -> date:
    if isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a calendar date, not a datetime: {value!r}")
    if isinstance(value, date):
        return value
    return _parse_iso_date(value, field_name)


def load_bloomberg_bond_yield_history(
    *,
    identifier: str,
    yield_field: str,
    start_date: str | date,
    end_date: str | date,
    field_meaning: str | None = None,
    field_unit: str | None = None,
) -> BloombergBondYieldHistory:
    """Load one bond's raw Bloomberg historical Yield series.

    ``identifier`` is the Bloomberg security string to request -- normally
    the symbology-qualified form
    :func:`~shiori_pricing_lab.data.bloomberg_bond_quote.parse_bond_identifier`
    builds from a trader-entered ISIN/CUSIP (``"/isin/<ISIN>"``). This
    loader never parses, infers, or decorates an identifier itself.

    ``yield_field`` is the workstation-confirmed Bloomberg Yield mnemonic;
    there is no default and no candidate list (see the module docstring).
    ``start_date``/``end_date`` are inclusive and may be ``date`` objects or
    strict ``YYYY-MM-DD`` strings; ``start_date`` must not be after
    ``end_date``. ``field_meaning``/``field_unit`` are optional provenance
    strings carried through verbatim.

    Raises ``ValueError`` for a caller-input problem (blank identifier,
    malformed mnemonic, malformed or inverted date range) -- always before
    any Bloomberg request is sent. Raises ``BLIBloombergDapiError`` for
    every Bloomberg-side failure; see the module docstring's fail-closed
    list.
    """

    _require_non_blank(identifier, "identifier")
    security = identifier.strip()
    field = _validate_yield_field(yield_field)
    start = _coerce_request_date(start_date, "start_date")
    end = _coerce_request_date(end_date, "end_date")
    if start > end:
        raise ValueError(
            f"start_date {start.isoformat()} must not be after end_date {end.isoformat()}"
        )
    for name, value in (("field_meaning", field_meaning), ("field_unit", field_unit)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{name} must be a non-blank string when supplied, got {value!r}")

    try:
        import blpapi
    except ImportError as exc:
        raise BLIBloombergDapiError(
            "blpapi is not installed -- Bloomberg's official blpapi package is "
            "required in this environment to load a live Bloomberg historical bond "
            "Yield series"
        ) from exc

    # One conversion boundary around every line that touches blpapi -- the
    # session lifecycle AND the response validation after it. The invariant is
    # "every Bloomberg-side failure raises BLIBloombergDapiError", so the
    # boundary has to be the whole region that can produce one; guarding only
    # the block that was last reported leaves the next unguarded access to be
    # found later (Codex review rounds 11-12, PR #198). `_resolved_security`
    # and `_observations_from_records` call hasElement/getElement/numValues/
    # getValueAsElement on Bloomberg's own elements, all of which can throw
    # natively, and they run after `session.stop()`.
    try:
        session_options = blpapi.SessionOptions()
        session_options.setServerHost(_DAPI_HOST)
        session_options.setServerPort(_DAPI_PORT)
        session = blpapi.Session(session_options)

        security_data_records: list = []

        try:
            if not session.start():
                raise BLIBloombergDapiError(
                    f"Bloomberg DAPI session failed to start against {_DAPI_HOST}:{_DAPI_PORT} "
                    "-- confirm a Bloomberg Terminal is running and logged in locally"
                )
            if not session.openService(_REFDATA_SERVICE):
                raise BLIBloombergDapiError(
                    f"Bloomberg DAPI failed to open service {_REFDATA_SERVICE}"
                )

            service = session.getService(_REFDATA_SERVICE)
            request = service.createRequest(_HISTORICAL_REQUEST_NAME)
            request.append("securities", security)
            request.append("fields", field)
            request.set("startDate", start.strftime(_BLOOMBERG_REQUEST_DATE_FORMAT))
            request.set("endDate", end.strftime(_BLOOMBERG_REQUEST_DATE_FORMAT))
            for option_name, option_value in _HISTORICAL_REQUEST_OPTIONS:
                request.set(option_name, option_value)

            session.sendRequest(request)

            deadline = _monotonic() + _REQUEST_TIMEOUT_MS / 1000.0
            done = False
            while not done:
                remaining_seconds = deadline - _monotonic()
                if remaining_seconds <= 0:
                    raise BLIBloombergDapiError(
                        "Bloomberg DAPI request timed out waiting for a historical bond "
                        f"Yield response for {security!r}"
                    )
                remaining_ms = max(1, int(remaining_seconds * 1000))
                event = session.nextEvent(remaining_ms)

                if event.eventType() == blpapi.Event.TIMEOUT:
                    raise BLIBloombergDapiError(
                        "Bloomberg DAPI request timed out waiting for a historical bond "
                        f"Yield response for {security!r}"
                    )
                if event.eventType() not in (blpapi.Event.PARTIAL_RESPONSE, blpapi.Event.RESPONSE):
                    continue

                for msg in event:
                    if msg.hasElement("responseError"):
                        raise BLIBloombergDapiError(
                            f"Bloomberg DAPI responseError for the historical bond Yield "
                            f"request for {security!r}: {msg.getElement('responseError')}"
                        )
                    if not msg.hasElement("securityData"):
                        continue
                    security_data_records.append(msg.getElement("securityData"))

                if event.eventType() == blpapi.Event.RESPONSE:
                    done = True
        finally:
            session.stop()

        if not security_data_records:
            raise BLIBloombergDapiError(
                f"Bloomberg DAPI returned no securityData for the historical bond Yield "
                f"request for {security!r}"
            )

        # The envelope question -- "is this whole answer about the one security
        # we asked for?" -- is settled before any row in it is read.
        resolved_security = _resolved_security(blpapi, security_data_records, security)
        observations = _observations_from_records(
            blpapi,
            security_data_records,
            security=security,
            field=field,
            start=start,
            end=end,
        )
    except blpapi.exception.Exception as exc:
        # Everything raised deliberately above is already a BLIBloombergDapiError
        # (a RuntimeError), so it passes through here untouched. This catches the
        # native failures nobody raises on purpose -- Bloomberg dropping the
        # connection after the service opened, or a response element whose
        # structural accessor throws while the answer is being validated.
        #
        # Without this the module's promise -- BLIBloombergDapiError for every
        # Bloomberg-side failure -- was overstated, and two callers act on that
        # promise: the workbench route answers a Bloomberg-side failure with HTTP
        # 502 and would have returned 500, and the acceptance CLI catches this
        # error type to record a failed run and would have died on a traceback
        # with neither report written.
        raise BLIBloombergDapiError(
            "Bloomberg DAPI failed during the historical bond Yield request for "
            f"{security!r}: {type(exc).__name__}: {exc}"
        ) from exc

    return BloombergBondYieldHistory(
        requested_identifier=security,
        security=resolved_security,
        yield_field=field,
        field_meaning=field_meaning,
        field_unit=field_unit,
        requested_start_date=start,
        requested_end_date=end,
        observations=observations,
        source_system=SOURCE_SYSTEM,
        acquired_at=_acquisition_now().isoformat(timespec="seconds"),
    )


def _resolved_security(blpapi, records: list, requested_security: str) -> str:
    """Return the one security identifier every ``securityData`` record names.

    A ``HistoricalDataRequest`` for one security may answer across several
    ``PARTIAL_RESPONSE`` messages, each carrying its own ``securityData`` for
    that same security. Two records naming *different* securities is not a
    paginated answer to this request, so it fails closed rather than being
    merged into one series. A record with no ``security`` element at all
    falls back to the requested identifier only when no record named one.
    """

    named: list[str] = []
    for record in records:
        if not record.hasElement("security"):
            continue
        named.append(_get_element_as_string(blpapi, record, "security", requested_security))

    distinct = sorted(set(named))
    if len(distinct) > 1:
        raise BLIBloombergDapiError(
            f"Bloomberg DAPI returned historical data for {len(distinct)} different "
            f"securities ({', '.join(repr(name) for name in distinct)}) in answer to the "
            f"request for {requested_security!r}"
        )
    return distinct[0] if distinct else requested_security


def _observations_from_records(
    blpapi,
    records: list,
    *,
    security: str,
    field: str,
    start: date,
    end: date,
) -> tuple[BondYieldObservation, ...]:
    """Validate every returned row and return them sorted ascending by date.

    Every fail-closed condition in the module docstring that concerns an
    individual row lives here. Nothing is dropped, trimmed, deduplicated,
    or filled: a row that cannot be trusted aborts the whole series.
    """

    by_date: dict[date, BondYieldObservation] = {}

    for record in records:
        if record.hasElement("securityError"):
            raise BLIBloombergDapiError(
                f"Bloomberg DAPI securityError for {security!r}: "
                f"{record.getElement('securityError')}"
            )
        if record.hasElement("fieldExceptions"):
            field_exceptions = record.getElement("fieldExceptions")
            if field_exceptions.numValues() > 0:
                raise BLIBloombergDapiError(
                    f"Bloomberg DAPI field exception for {field} on {security!r}: "
                    f"{field_exceptions.getValueAsElement(0)}"
                )
        if not record.hasElement("fieldData"):
            continue

        field_data = record.getElement("fieldData")
        for index in range(field_data.numValues()):
            row = field_data.getValueAsElement(index)

            if not row.hasElement(_OBSERVATION_DATE_ELEMENT):
                raise BLIBloombergDapiError(
                    f"Bloomberg DAPI returned a historical row with no "
                    f"{_OBSERVATION_DATE_ELEMENT} for {security!r}"
                )
            raw_date = _get_element_as_string(blpapi, row, _OBSERVATION_DATE_ELEMENT, security)
            try:
                observation_date = _parse_iso_date(raw_date, f"{security} observation date")
            except ValueError as exc:
                raise BLIBloombergDapiError(
                    f"Bloomberg DAPI returned a non-parseable observation date for "
                    f"{security!r}: {exc}"
                ) from exc

            if observation_date < start or observation_date > end:
                raise BLIBloombergDapiError(
                    f"Bloomberg DAPI returned an observation dated "
                    f"{observation_date.isoformat()} for {security!r}, outside the "
                    f"requested range {start.isoformat()}..{end.isoformat()}"
                )

            # `hasElement(field)` on its own is true for an element Bloomberg
            # returned as *null* -- which is exactly what NIL_VALUE produces on
            # a day it holds no observation -- and reading a null element as a
            # string raises, which would fail the whole series on the very case
            # this loader exists to preserve. `excludeNullElements=True` makes a
            # returned null read as the hole it is (Codex review, PR #198).
            raw_value: str | None = None
            if row.hasElement(field, True):
                raw_value = _get_element_as_string(blpapi, row, field, security)
                if not raw_value.strip():
                    raw_value = None

            # A row Bloomberg returned with no value is a visible hole on a real
            # date, not a number to invent -- and never the previous day's value.
            yield_value = (
                None if raw_value is None else _parse_finite_float(raw_value, f"{field} historical")
            )

            if observation_date in by_date:
                raise BLIBloombergDapiError(
                    f"Bloomberg DAPI returned two observations dated "
                    f"{observation_date.isoformat()} for {field} on {security!r} -- "
                    "no documented Bloomberg rule resolves a duplicate observation date, "
                    "so this series is refused rather than one of them being chosen"
                )
            by_date[observation_date] = BondYieldObservation(
                observation_date=observation_date,
                yield_value=yield_value,
                raw_value=raw_value,
            )

    return tuple(by_date[key] for key in sorted(by_date))
