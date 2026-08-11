"""``load_bloomberg_usd_sofr_par_rate_curve``: Bloomberg USD SOFR OIS SWAP
(Par Rate) acquisition -- ``USOSFR*`` / ``LAST_PRICE`` (Issue #168).

**Scope, per Issue #168.** Market-data acquisition, normalization, and
tests only -- the Markets Curve Viewer UI (#167), pricing, and OIS
bootstrap are explicitly out of scope. This module's output is never wired
into ``load_bloomberg_usd_sofr_option_discount_curve``
(``data/bloomberg_option_discount_curve.py``, Issue #165's ``S0490Z``/
``S0490D`` loader -- unmodified by this module) or into any pricing code;
it exists purely as display-only market context for #167 to later consume.

**Explicit, reviewed 32-tenor ``USOSFR*`` mapping (Eddy's Issue #168
table), never a generic string-formatting rule.** Unlike the ``S<curve>Z``/
``S<curve>D`` grammar (a uniform ``f"S{curve_number}{suffix} {tenor} BLC2
Curncy"`` template), the ``USOSFR*`` universe has no single formula --
month tenors use letters (``USOSFRA``..``USOSFRK``), week tenors use a
``Z`` suffix (``USOSFR1Z``/``USOSFR2Z``/``USOSFR3Z``), and year tenors are
mostly bare numbers with one irregular ``18M -> USOSFR1F`` label. Every one
of the 32 approved securities is therefore named verbatim in
``USD_SOFR_PAR_RATE_TICKERS`` below -- an explicit ``tenor -> security``
mapping, not a derived one -- exactly Eddy's Issue #168 table. This module
never constructs or guesses a ``USOSFR*`` ticker outside it.

**Same canonical 32-tenor universe as Curve #490 (Issue #165).** Eddy's
Issue #168 table is explicitly "the same canonical 32-tenor universe
already used by the Curve #490 ``S0490Z``/``S0490D`` production path" --
this module imports ``DEFAULT_USD_SOFR_TENORS`` from
``data/bloomberg_option_discount_curve.py`` (read-only; that module is
otherwise untouched) as the one shared source of truth for tenor labels
and canonical acquisition order, and raises at import time if
``USD_SOFR_PAR_RATE_TICKERS``'s keys are not exactly that same 32-label
set -- so the two universes can never silently drift apart.

**Field requested: exactly ``LAST_PRICE``, per security.** Interpreted
directly as the SOFR OIS SWAP Par Rate **in percent** (Issue #168
Acceptance Criteria A: "normalized as SWAP (Par Rate) in percent") --
unlike the ``Z`` zero-rate ticker in ``bloomberg_option_discount_curve.py``,
this value is never divided by 100; a displayed ``"3.75"`` stays ``3.75``
(percent), never converted to a ``0.0375`` decimal fraction. No
``MATURITY`` or any other field is requested -- this is a small,
provenance-only observation, not a curve node with its own maturity date.

**Normalized data contract (Issue #168 Acceptance Criteria A).**
:class:`BloombergUsdSofrParRatePoint` carries, per tenor: the canonical
``tenor`` label, the Bloomberg ``security``/provenance, the raw
``LAST_PRICE`` string as acquired, and the parsed ``par_rate_percent``.
This is a standalone dataclass, deliberately **not** ``BLICurvePoint``
(``data/bli_snapshot.py``) -- Issue #168's own boundary section requires
this data "must not become an Option Discount Curve pricing input", and
``BLICurvePoint``/``BLIMarketDataSnapshot`` carry pricing-eligibility
semantics (``curve_purpose``, active-status gating, curve-node dedup
rules) this display-only observation has no business claiming.

**Fail-closed conditions (Issue #168 Acceptance Criteria B), every one
raising**
:class:`~shiori_pricing_lab.data.bloomberg_bond_quote.BLIBloombergDapiError`
(reused directly -- no new exception type):

- a ``securityData`` record count other than exactly one for any requested
  security (covers ``BAD_SEC``, a security silently dropped from the
  response, and a duplicated record alike);
- a ``securityError`` on any requested security;
- a ``fieldExceptions`` entry on any requested security;
- a missing/blank ``LAST_PRICE``;
- a ``LAST_PRICE`` that does not parse as a finite number (malformed or
  non-finite alike, via the same ``_parse_finite_float`` every other
  production Bloomberg loader in this repository already uses).

Every one of these aborts the whole call -- exactly like
``load_bloomberg_usd_sofr_option_discount_curve``, this loader never
returns a partial curve; a caller either gets all validated points for
every requested tenor, or an exception naming the offending
security/tenor. There is no partial-success return.

**No ticker guessing, no derivation, no pricing wiring (Issue #168
boundary section).** No security outside ``USD_SOFR_PAR_RATE_TICKERS`` is
ever requested. No bootstrap, interpolation, or synthetic fill of any
kind. No zero-rate or discount-factor derivation from this par rate, and
nothing in this module reads from or writes into
``load_bloomberg_usd_sofr_option_discount_curve``,
``pricing/bli_curve_discount_factor.py``, or any other pricing module.

**Session lifecycle and reuse (Issue #168 requirement #1: reuse existing
Bloomberg DAPI infrastructure).** Follows exactly the same lazy ``import
blpapi``, session/event-loop, and error-conversion conventions
``bloomberg_bond_quote.py`` established and
``bloomberg_option_discount_curve.py`` already reuses --
``_DAPI_HOST``/``_DAPI_PORT``/``_REFDATA_SERVICE``/``_REQUEST_TIMEOUT_MS``,
``BLIBloombergDapiError``, ``_get_element_as_string``, and
``_parse_finite_float`` are all imported and reused directly, never
reimplemented. ``session.stop()`` is called on every post-start path.
``blpapi`` is imported lazily inside this module's own function, so
importing this module never requires ``blpapi`` to be installed.

**Acquisition timestamp -- not carried (Issue #168's own "if supported by
the existing Bloomberg adapter pattern" qualifier).** Neither
``bloomberg_bond_quote.py`` nor ``bloomberg_option_discount_curve.py``
attaches a wall-clock acquisition timestamp to any returned record today --
there is no existing pattern to reuse, and adding one here would mean this
module inventing a new convention (``datetime.now()`` at acquisition time)
none of this repository's other Bloomberg loaders follow. A future,
separately-reviewed slice can add one consistently across all three
loaders if #167 or a later issue needs it.

**Not in this slice.** No Markets Curve Viewer UI (#167), no
``BLIMarketDataSnapshot``/pricing-engine wiring, no OIS bootstrap, no
par-to-zero or zero-to-par conversion, no historical curves, no EUR
€STR, no Bond Reference Curve, no provider interface, base class,
factory, registry, cache, or configuration framework.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from shiori_pricing_lab.data.bloomberg_bond_quote import (
    _DAPI_HOST,
    _DAPI_PORT,
    _REFDATA_SERVICE,
    _REQUEST_TIMEOUT_MS,
    BLIBloombergDapiError,
    _get_element_as_string,
    _parse_finite_float,
)
from shiori_pricing_lab.data.bloomberg_option_discount_curve import DEFAULT_USD_SOFR_TENORS

_LAST_PRICE_FIELD = "LAST_PRICE"
_SECURITY_IDENTIFIER_FIELD = "security"

USD_SOFR_PAR_RATE_SOURCE_SYSTEM = "BLOOMBERG_DAPI"

# Eddy's exact, approved Issue #168 32-tenor USOSFR* mapping -- explicit
# enumeration, never a generated/derived ticker. Keys are exactly
# DEFAULT_USD_SOFR_TENORS (checked below); values are Eddy's own Curve #490
# Curve Construction USOSFR* Curncy tickers verbatim.
USD_SOFR_PAR_RATE_TICKERS: dict[str, str] = {
    "1W": "USOSFR1Z Curncy",
    "2W": "USOSFR2Z Curncy",
    "3W": "USOSFR3Z Curncy",
    "1M": "USOSFRA Curncy",
    "2M": "USOSFRB Curncy",
    "3M": "USOSFRC Curncy",
    "4M": "USOSFRD Curncy",
    "5M": "USOSFRE Curncy",
    "6M": "USOSFRF Curncy",
    "7M": "USOSFRG Curncy",
    "8M": "USOSFRH Curncy",
    "9M": "USOSFRI Curncy",
    "10M": "USOSFRJ Curncy",
    "11M": "USOSFRK Curncy",
    "1Y": "USOSFR1 Curncy",
    "18M": "USOSFR1F Curncy",
    "2Y": "USOSFR2 Curncy",
    "3Y": "USOSFR3 Curncy",
    "4Y": "USOSFR4 Curncy",
    "5Y": "USOSFR5 Curncy",
    "6Y": "USOSFR6 Curncy",
    "7Y": "USOSFR7 Curncy",
    "8Y": "USOSFR8 Curncy",
    "9Y": "USOSFR9 Curncy",
    "10Y": "USOSFR10 Curncy",
    "12Y": "USOSFR12 Curncy",
    "15Y": "USOSFR15 Curncy",
    "20Y": "USOSFR20 Curncy",
    "25Y": "USOSFR25 Curncy",
    "30Y": "USOSFR30 Curncy",
    "40Y": "USOSFR40 Curncy",
    "50Y": "USOSFR50 Curncy",
}

if set(USD_SOFR_PAR_RATE_TICKERS) != set(DEFAULT_USD_SOFR_TENORS):
    raise RuntimeError(
        "USD_SOFR_PAR_RATE_TICKERS tenor keys must exactly match "
        "bloomberg_option_discount_curve.DEFAULT_USD_SOFR_TENORS -- the same canonical "
        "32-tenor Curve #490 universe (Issue #168)"
    )

# Canonical acquisition order (Issue #165 Codex P2 fix precedent, reused
# here): each label's own fixed index in DEFAULT_USD_SOFR_TENORS, never a
# generic W/M/Y arithmetic that could collide across units.
_TENOR_ACQUISITION_ORDER: dict[str, int] = {
    tenor: index for index, tenor in enumerate(DEFAULT_USD_SOFR_TENORS)
}


def _validate_par_rate_tenor_label(tenor: str) -> int:
    """Validate a tenor against the exact 32-label approved ``USOSFR*`` universe.

    Accepts only a label that is exactly one of ``USD_SOFR_PAR_RATE_TICKERS``
    -- never a generic ``<int>W``/``<int>M``/``<int>Y`` grammar and never a
    guessed ``USOSFR*`` ticker. Returns that label's canonical acquisition
    order index (a label-ordering convenience only, never a year fraction).
    """

    if not isinstance(tenor, str) or tenor not in _TENOR_ACQUISITION_ORDER:
        raise ValueError(f"unsupported tenor label: {tenor!r}")
    return _TENOR_ACQUISITION_ORDER[tenor]


# Testable seam for the whole-request deadline, mirroring
# bloomberg_bond_quote.py's/bloomberg_option_discount_curve.py's own
# `_monotonic` pattern -- this module's own alias, independently
# monkeypatchable in this module's tests.
_monotonic = time.monotonic


@dataclass(frozen=True)
class BloombergUsdSofrParRatePoint:
    """One tenor's USD SOFR OIS SWAP (Par Rate) observation (Issue #168).

    Display-only market context for #167 -- never an Option Discount Curve
    pricing input, never bootstrapped, never interpolated.
    ``par_rate_percent`` is Bloomberg's own ``LAST_PRICE`` in percent,
    unconverted (a displayed ``"3.75"`` stays ``3.75``, never divided to a
    ``0.0375`` decimal).
    """

    tenor: str
    security: str
    raw_last_price: str
    par_rate_percent: float
    source_system: str


@dataclass(frozen=True)
class BloombergUsdSofrParRateCurveResult:
    """One complete USD SOFR OIS SWAP (Par Rate) acquisition result.

    ``points`` are sorted by ascending ``_validate_par_rate_tenor_label``
    (canonical Curve #490 tenor) order, regardless of the order ``tenors``
    was supplied in.
    """

    points: tuple[BloombergUsdSofrParRatePoint, ...]


def load_bloomberg_usd_sofr_par_rate_curve(
    tenors: Sequence[str] = DEFAULT_USD_SOFR_TENORS,
) -> BloombergUsdSofrParRateCurveResult:
    """Load live Bloomberg USD SOFR OIS SWAP (Par Rate) points -- ``USOSFR*``.

    ``tenors`` must be a non-empty sequence of distinct, non-blank tenor
    labels, each accepted by ``_validate_par_rate_tenor_label`` -- validated
    before any Bloomberg request is sent, so a malformed or unapproved
    tenor never reaches Bloomberg at all. Defaults to the full
    ``DEFAULT_USD_SOFR_TENORS`` 32-tenor universe.

    Raises ``ValueError`` for a caller-input problem (empty/duplicate/
    malformed/unapproved tenor). Raises ``BLIBloombergDapiError`` for every
    Bloomberg-side failure -- see the module docstring's fail-closed list.
    A successful call always returns a complete result for every requested
    tenor; there is no partial-success return.
    """

    tenors = tuple(tenors)
    if not tenors:
        raise ValueError("tenors must not be empty")

    seen_tenors: set[str] = set()
    for tenor in tenors:
        if not isinstance(tenor, str) or not tenor.strip():
            raise ValueError(f"tenor must be a non-blank string, got {tenor!r}")
        if tenor in seen_tenors:
            raise ValueError(f"duplicate tenor {tenor!r} in tenors")
        seen_tenors.add(tenor)
        _validate_par_rate_tenor_label(tenor)  # fail closed on a malformed/unapproved tenor

    try:
        import blpapi
    except ImportError as exc:
        raise BLIBloombergDapiError(
            "blpapi is not installed -- Bloomberg's official blpapi package is "
            "required in this environment to load live Bloomberg USD SOFR par rate data"
        ) from exc

    securities_by_tenor = {tenor: USD_SOFR_PAR_RATE_TICKERS[tenor] for tenor in tenors}
    all_securities = list(securities_by_tenor.values())

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
        for security in all_securities:
            request.append("securities", security)
        request.append("fields", _LAST_PRICE_FIELD)

        session.sendRequest(request)

        security_data_records: list = []

        deadline = _monotonic() + _REQUEST_TIMEOUT_MS / 1000.0
        done = False
        while not done:
            remaining_seconds = deadline - _monotonic()
            if remaining_seconds <= 0:
                raise BLIBloombergDapiError(
                    "Bloomberg DAPI request timed out waiting for a USD SOFR par rate response"
                )
            remaining_ms = max(1, int(remaining_seconds * 1000))
            event = session.nextEvent(remaining_ms)

            if event.eventType() == blpapi.Event.TIMEOUT:
                raise BLIBloombergDapiError(
                    "Bloomberg DAPI request timed out waiting for a USD SOFR par rate response"
                )
            if event.eventType() not in (blpapi.Event.PARTIAL_RESPONSE, blpapi.Event.RESPONSE):
                continue

            for msg in event:
                if msg.hasElement("responseError"):
                    raise BLIBloombergDapiError(
                        "Bloomberg DAPI responseError for the USD SOFR par rate request: "
                        f"{msg.getElement('responseError')}"
                    )
                security_data_array = msg.getElement("securityData")
                for i in range(security_data_array.numValues()):
                    security_data_records.append(security_data_array.getValueAsElement(i))

            if event.eventType() == blpapi.Event.RESPONSE:
                done = True
    finally:
        session.stop()

    records_by_identifier: dict[str, list] = {}
    for record in security_data_records:
        if not record.hasElement(_SECURITY_IDENTIFIER_FIELD):
            continue
        identifier = _get_element_as_string(
            blpapi, record, _SECURITY_IDENTIFIER_FIELD, "<unidentified securityData record>"
        )
        records_by_identifier.setdefault(identifier, []).append(record)

    def _resolve(security: str) -> str:
        matches = records_by_identifier.get(security, [])
        if len(matches) != 1:
            raise BLIBloombergDapiError(
                f"Bloomberg DAPI returned {len(matches)} securityData record(s) for "
                f"{security!r}, expected exactly one"
            )
        record = matches[0]

        if record.hasElement("securityError"):
            raise BLIBloombergDapiError(
                f"Bloomberg DAPI securityError for {security!r}: "
                f"{record.getElement('securityError')}"
            )
        if record.hasElement("fieldExceptions"):
            field_exceptions = record.getElement("fieldExceptions")
            if field_exceptions.numValues() > 0:
                raise BLIBloombergDapiError(
                    f"Bloomberg DAPI field exception for {security!r}: "
                    f"{field_exceptions.getValueAsElement(0)}"
                )

        field_data = record.getElement("fieldData")

        if not field_data.hasElement(_LAST_PRICE_FIELD):
            raise BLIBloombergDapiError(
                f"Bloomberg DAPI response for {security!r} is missing {_LAST_PRICE_FIELD}"
            )
        raw_last_price = _get_element_as_string(blpapi, field_data, _LAST_PRICE_FIELD, security)
        if not raw_last_price:
            raise BLIBloombergDapiError(
                f"Bloomberg DAPI response for {security!r} is missing {_LAST_PRICE_FIELD}"
            )
        return raw_last_price

    points: list[BloombergUsdSofrParRatePoint] = []
    for tenor in tenors:
        security = securities_by_tenor[tenor]
        raw_last_price = _resolve(security)
        par_rate_percent = _parse_finite_float(raw_last_price, _LAST_PRICE_FIELD)

        points.append(
            BloombergUsdSofrParRatePoint(
                tenor=tenor,
                security=security,
                raw_last_price=raw_last_price,
                par_rate_percent=par_rate_percent,
                source_system=USD_SOFR_PAR_RATE_SOURCE_SYSTEM,
            )
        )

    points_sorted = tuple(
        sorted(points, key=lambda point: _TENOR_ACQUISITION_ORDER[point.tenor])
    )

    return BloombergUsdSofrParRateCurveResult(points=points_sorted)
