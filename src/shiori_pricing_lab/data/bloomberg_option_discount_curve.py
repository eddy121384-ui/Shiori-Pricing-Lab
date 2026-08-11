"""``load_bloomberg_usd_sofr_option_discount_curve``: production Bloomberg
Curve #490 (USD SOFR) synthetic-ticker ingestion (Issue #165 production
phase).

**Scope, per Eddy's explicit Issue #165 production-phase instruction.**
Discovery (rounds 1-11) is over. Workstation evidence -- Bloomberg's own
exported Excel formulas from Curve #490 -- established the ticker grammar
this loader uses::

    S<curve number>Z <tenor> BLC2 Curncy  -> continuously compounded zero rate
    S<curve number>D <tenor> BLC2 Curncy  -> discount factor

Both are acquired with ``LAST_PRICE``; round 11's diagnostic probe
(``tools/bloomberg_curve_ticker_last_price_probe.py``) proved DAPI
acquisition succeeds against ``S0490Z 2Y BLC2 Curncy``/
``S0490D 1Y BLC2 Curncy``. This module is the smallest production ingestion
slice built on that evidence -- USD SOFR only.

**Rate-basis decision (docs/28 §5/§6; docs/bloomberg_ovme_source_mapping.md).**
``BLICurvePoint.rate_basis`` is required with no default, and
``docs/bloomberg_ovme_source_mapping.md``'s "SWDF S490 Stripped Curve --
Zero Rate" row explicitly warns that a Bloomberg-displayed zero rate "must
not be treated as Shiori ``CONTINUOUS_ZERO_RATE`` without a separately
approved RED basis/conversion decision." That warning is about the
``SW173``/``SW174`` "Stripped Curve" bulk-field acquisition route (rounds
7-10), which round 10 already abandoned outright (it returned a curve
materially different from terminal Curve #490 under every tested
override). **This module uses a different, later-discovered route** -- the
``S0490Z``/``S0490D`` synthetic-ticker grammar -- for which Eddy's own
Issue #165 production-phase instruction is the separately approved
decision this warning asked for: he stated explicitly that the ``Z``
ticker is a "continuously compounded zero rate" and instructed
``rate_basis=CONTINUOUS_ZERO_RATE``. That statement already matches Annex
A §A.10.2's own required compounding convention verbatim, so no
par-to-zero conversion is needed or performed here (Annex A §A.10.3's
"FTP directly gives a zero curve -> use directly" case) -- the Bloomberg
``LAST_PRICE`` value is only converted from percent to decimal, never
reinterpreted.

**Explicit curve configuration, not a generic mapping.** ``USD_SOFR_
BLOOMBERG_CURVE_NUMBER = "0490"`` is the one explicit USD SOFR -> curve
number association this module knows; there is no currency-keyed lookup
table and no ``currency`` parameter anywhere in this module -- USD SOFR is
the only thing it can ever produce, by construction. EUR ESTR, a bond
reference curve, a repo curve, and any OIS bootstrap are all out of scope
here and require a separate, later slice.

**Explicit tenor set, never guessed.** ``tenors`` is a caller-supplied
sequence of tenor labels (default: ``DEFAULT_USD_SOFR_TENORS``). This
module never generates, extrapolates, or guesses a tenor; every tenor
requested is exactly one Eddy named, validated against this module's own
``_validate_bloomberg_tenor_label`` grammar before any Bloomberg request
is sent (see the "Acquisition-label validation/order is decoupled from
pricing's tenor parser" section below).

**Full-curve tenor universe (Issue #165 tenor decoupling, applying Eddy's
workstation-verified evidence).** Eddy supplied Bloomberg's own Curve #490
Curve Construction export naming its full tenor universe. That export's
``USOSFR*`` rows are the *construction* route's OIS swap *input*
instruments -- never ingested or referenced by this module, only read as
evidence for which tenor labels the curve actually has.
``DEFAULT_USD_SOFR_TENORS`` below now carries all **32** of that export's
tenors, ``"1W"``/``"2W"``/``"3W"`` included, using ``"1Y"`` rather than the
export's own ``"12 MO"`` spelling for the one-year point (Eddy separately,
directly confirmed ``S0490Z 1Y BLC2 Curncy``/``S0490D 1Y BLC2 Curncy``
resolve -- that proven spelling is kept, never replaced with a derived
``"12M"``).

**Acquisition-label validation/order is decoupled from pricing's tenor
parser.** ``pricing/bli_curve_tenor.py::tenor_to_year_fraction`` -- the
existing, shared, previously-reviewed curve-coordinate parser every other
consumer of ``BLICurvePoint.tenor`` in this codebase relies on --
deliberately does not accept week tenors (see that module's own
docstring: "no other tenor vocabulary (week tenors, ...)"), and Issue #165
does not widen that shared parser's vocabulary; doing so would touch every
other consumer of that module, not just this one. Instead, every tenor
label this loader is asked to acquire from Bloomberg -- including
``"1W"``/``"2W"``/``"3W"`` -- is validated and ordered by this module's own
:func:`_validate_bloomberg_tenor_label`, a small, loader-local, strict
``<int>W``/``<int>M``/``<int>Y`` grammar that exists purely for
acquisition-time validation and Bloomberg-request ordering. It never
computes a year fraction and is never imported by, or substituted into,
``tenor_to_year_fraction`` or any pricing module. Every week-labelled node
this loader returns still prices entirely from its own Bloomberg-returned
``maturity_date`` -- ``pricing/bli_zero_curve_nodes.py`` never calls
``tenor_to_year_fraction`` on a row that carries an explicit
``maturity_date`` (see that module's own docstring), so ``tenor_to_year_
fraction`` continuing to reject ``"1W"`` never blocks live pricing of a
Bloomberg-sourced week-labelled node. The literal Bloomberg label
(``"1W"``, never a substituted ``"7D"``) is preserved verbatim on both the
built ticker and the returned ``BLICurvePoint.tenor``.

**Fields requested: exactly ``LAST_PRICE`` and ``MATURITY``, for both the
``Z`` and ``D`` synthetic security at every requested tenor**, in one
``ReferenceDataRequest``. ``LAST_PRICE`` is the field Eddy's own Excel
formulas use verbatim; ``MATURITY`` is Bloomberg's own returned node date,
parsed and validated as a real ``YYYY-MM-DD`` calendar date for *both*
securities -- never invented from the nominal tenor label.

**``MATURITY`` is preserved, not discarded.** The ``Z`` security's own
``MATURITY`` is the authoritative curve node date: it is written verbatim
into ``BLICurvePoint.maturity_date`` (Issue #165 production-phase schema
addition, see ``data/bli_snapshot.py``) -- never reconstructed from
``tenor`` -- and also drives the duplicate/non-increasing-maturity
fail-closed check below. The ``D`` security's own ``MATURITY`` is
required to be **identical** to the ``Z`` security's at the same tenor
(both are evidence for the same curve node); a mismatch fails closed with
a precise error naming both tickers and both dates rather than silently
keeping only one. ``D``'s own maturity is also carried on
``BloombergDiscountFactorEvidence.maturity`` for transparency, but it is
never the *curve point's* maturity -- callers must read
``BLICurvePoint.maturity_date`` for that.

**Percent-to-decimal conversion (Z only).** The ``Z`` ticker's
``LAST_PRICE`` is divided by 100 (Bloomberg convention: a displayed
``"3.25"`` means 3.25%) to produce ``BLICurvePoint.rate``. The ``D``
ticker's ``LAST_PRICE`` is never divided -- discount factors are not a
percent quote.

**``D`` (discount factor) is preserved as evidence only, never bootstrapped
or derived into anything.** Each tenor's discount-factor value is returned
in a separate ``BloombergDiscountFactorEvidence`` record alongside its own
raw ``LAST_PRICE``/``MATURITY`` -- it is not written into any
``BLICurvePoint`` and this module performs no OIS bootstrap of any kind.
Per Eddy's explicit instruction, a discount factor is **not** assumed to
be ``<= 1`` -- only finite and strictly positive is enforced.

**Fail-closed conditions (Eddy's explicit list), every one raising
:class:`~shiori_pricing_lab.data.bloomberg_bond_quote.BLIBloombergDapiError`
(reused directly -- this module defines no new exception type) naming the
offending security/tenor:**

- ``BAD_SEC``/any Bloomberg ``securityError`` on either the ``Z`` or ``D``
  security at any tenor;
- a ``securityData`` record count other than exactly one for any requested
  security (covers a response that silently drops or duplicates a
  security);
- a ``fieldExceptions`` entry on either security;
- a missing/blank ``LAST_PRICE`` or ``MATURITY`` on either security;
- a ``MATURITY`` value that does not parse as a strict ``YYYY-MM-DD``
  calendar date on either security (reuses
  ``data._validation._parse_iso_date``);
- a ``Z``/``D`` ``MATURITY`` mismatch at the same tenor;
- two different tenors whose ``Z`` security resolves to the same
  ``MATURITY`` (duplicate curve node date);
- a ``Z`` ``MATURITY`` that does not strictly increase, in ascending
  ``_validate_bloomberg_tenor_label`` order, from one tenor to the next
  (non-increasing curve node date);
- a converted zero rate that is not finite;
- a discount factor that is not finite or not strictly positive.

Every one of these aborts the whole call -- this loader never returns a
partial curve.

**Session lifecycle and reuse.** Follows exactly the same lazy ``import
blpapi``, session/event-loop, and error-conversion conventions
``bloomberg_bond_quote.py`` already established as this repository's one
production Bloomberg DAPI pattern -- ``_DAPI_HOST``/``_DAPI_PORT``/
``_REFDATA_SERVICE``/``_REQUEST_TIMEOUT_MS``,
:class:`~shiori_pricing_lab.data.bloomberg_bond_quote.BLIBloombergDapiError`,
and the malformed-element/non-numeric-value helpers
(``_get_element_as_string``, ``_parse_finite_float``) are all imported and
reused directly from that module rather than reimplemented. ``session.
stop()`` is called on every post-start path. ``blpapi`` is imported lazily
inside this module's own function, so importing this module never requires
``blpapi`` to be installed.

**Not in this slice.** No ``BLIMarketDataSnapshot``/pricing-engine wiring,
no MVP input bundle, no interpolation, no discount-factor calculation from
the zero curve, no forward price, no OVME premium, no EUR/€STR, no bond
reference curve, no repo curve, no OIS bootstrap, no provider interface,
base class, factory, registry, cache, or configuration framework.
"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from shiori_pricing_lab.data._validation import _parse_iso_date
from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIMarketDataStatus,
)
from shiori_pricing_lab.data.bloomberg_bond_quote import (
    _DAPI_HOST,
    _DAPI_PORT,
    _REFDATA_SERVICE,
    _REQUEST_TIMEOUT_MS,
    BLIBloombergDapiError,
    _get_element_as_string,
    _parse_finite_float,
)
from shiori_pricing_lab.products.enums import Currency

_LAST_PRICE_FIELD = "LAST_PRICE"
_MATURITY_FIELD = "MATURITY"
_SECURITY_IDENTIFIER_FIELD = "security"

# Confirmed verbatim from Eddy's own Curve #490 Excel formulas (Issue #165
# round 11): =BDP("S0490Z 2Y BLC2 Curncy","LAST_PRICE") /
# =BDP("S0490D 1Y BLC2 Curncy","LAST_PRICE").
_TICKER_SUFFIX = "BLC2 Curncy"

# Explicit curve configuration (Eddy's Issue #165 production-phase
# instruction): USD SOFR -> Bloomberg Curve #490. Not a generic
# currency-keyed table -- USD SOFR is the only mapping this module has.
USD_SOFR_BLOOMBERG_CURVE_NUMBER = "0490"
USD_SOFR_CURVE_ID = "USD_SOFR_OPTION_DISCOUNT_CURVE"
USD_SOFR_CURVE_NAME = "USD SOFR Option Discount Curve (Bloomberg Curve #490)"
USD_SOFR_SOURCE_SYSTEM = "BLOOMBERG_DAPI"

# Bloomberg-loader-local strict acquisition-label grammar (Issue #165 tenor
# decoupling). Accepts exactly `<positive integer><W|M|Y>` -- Bloomberg's
# own Curve #490 short/belly/long-end vocabulary -- and nothing else. This
# is deliberately separate from `pricing/bli_curve_tenor.py::
# tenor_to_year_fraction` (which accepts D/M/Y, never W, and is never
# touched by Issue #165): this grammar exists only to validate/order
# labels before/while building Bloomberg requests, never to compute a
# year fraction or feed pricing math. Every Bloomberg-sourced BLICurvePoint
# always carries its own Bloomberg-returned maturity_date, so live pricing
# (pricing/bli_zero_curve_nodes.py) prices every node -- week-labelled or
# not -- from that date directly and never calls tenor_to_year_fraction on
# it.
_BLOOMBERG_TENOR_LABEL_SHAPE = re.compile(r"(?P<value>[1-9][0-9]*)(?P<unit>[WMY])")

# Nominal calendar-day multiplier per unit, used only to rank acquisition
# labels against each other in approximate calendar order (e.g. so "18M"
# ranks after "1Y", not before it merely because "M" < "Y"). This is a
# coarse, W=7/M=30/Y=365 ordering convenience -- not a day-count
# convention, never used as a time-to-expiry or year-fraction value, and
# never fed to pricing (which always uses the node's own Bloomberg-returned
# maturity_date, never this ranking).
_BLOOMBERG_TENOR_UNIT_NOMINAL_DAYS = {"W": 7, "M": 30, "Y": 365}


def _validate_bloomberg_tenor_label(tenor: str) -> int:
    """Validate a strict Bloomberg acquisition tenor label; return its order key.

    Accepts exactly a positive integer followed by ``W``/``M``/``Y`` (e.g.
    ``"1W"``, ``"18M"``, ``"50Y"``) -- no whitespace, no lowercase, no
    zero/negative values, no other unit (``D`` included -- Bloomberg's own
    Curve #490 export never uses day tenors). Raises ``ValueError`` with
    the same ``"unsupported tenor label"`` message
    ``tenor_to_year_fraction`` raises for its own rejections, so every
    caller of this loader sees one consistent error shape regardless of
    which parser rejected the label.

    The returned integer is ``value * nominal_days_per_unit`` (``W=7``,
    ``M=30``, ``Y=365``) -- a coarse, monotonic acquisition-ordering key,
    correct for every label in ``DEFAULT_USD_SOFR_TENORS`` (e.g. it orders
    ``"18M"`` after ``"1Y"``, and ``"3W"`` before ``"1M"``). It is a
    label-ordering convenience only, never a year fraction or day-count --
    never compared against, mixed with, or substituted for
    ``tenor_to_year_fraction``'s own output.
    """

    if not isinstance(tenor, str):
        raise ValueError(f"unsupported tenor label: {tenor!r}")
    match = _BLOOMBERG_TENOR_LABEL_SHAPE.fullmatch(tenor)
    if not match:
        raise ValueError(f"unsupported tenor label: {tenor!r}")
    value = int(match.group("value"))
    unit = match.group("unit")
    return value * _BLOOMBERG_TENOR_UNIT_NOMINAL_DAYS[unit]


# Eddy's own workstation-verified Curve #490 tenor universe (Bloomberg's
# Curve Construction export), all 32 tenors -- "1W"/"2W"/"3W" restored
# (Issue #165 tenor decoupling) now that this module validates/orders
# acquisition labels via its own local `_validate_bloomberg_tenor_label`
# rather than the shared `tenor_to_year_fraction`. "1Y" is kept in place
# of the export's "12 MO" spelling (Eddy directly confirmed the "1Y"
# ticker spelling resolves; never replaced with a derived "12M"). Override
# with an explicit tenors= argument for any other tenor (this loader's own
# grammar still applies to it).
DEFAULT_USD_SOFR_TENORS: tuple[str, ...] = (
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

# Testable seam for the whole-request deadline, mirroring
# bloomberg_bond_quote.py's own `_monotonic` pattern -- this module's own
# alias, independently monkeypatchable in this module's tests.
_monotonic = time.monotonic


def build_zero_rate_ticker(curve_number: str, tenor: str) -> str:
    """Build the ``Z`` (continuously compounded zero rate) synthetic ticker.

    Pure string construction from Eddy's own confirmed grammar -- never a
    guess. ``curve_number``/``tenor`` are used verbatim, with no format
    validation beyond simple substitution (callers validate ``tenor``
    themselves via ``_validate_bloomberg_tenor_label`` before this is used
    to build a request).
    """

    return f"S{curve_number}Z {tenor} {_TICKER_SUFFIX}"


def build_discount_factor_ticker(curve_number: str, tenor: str) -> str:
    """Build the ``D`` (discount factor) synthetic ticker -- see :func:`build_zero_rate_ticker`."""

    return f"S{curve_number}D {tenor} {_TICKER_SUFFIX}"


@dataclass(frozen=True)
class BloombergDiscountFactorEvidence:
    """One tenor's preserved ``D`` (discount-factor) evidence.

    Cross-check evidence only (Eddy's explicit instruction) -- never
    written into a ``BLICurvePoint``, never bootstrapped, never used to
    derive or adjust the ``Z``-sourced zero rate. ``discount_factor`` is
    only required to be finite and strictly positive; it is **not**
    assumed to be ``<= 1``.
    """

    tenor: str
    security: str
    raw_last_price: str
    discount_factor: float
    maturity: str


@dataclass(frozen=True)
class BloombergUsdSofrOptionDiscountCurveResult:
    """One complete USD SOFR option discount curve ingestion result.

    ``curve_points`` are ready-to-use ``OPTION_DISCOUNT_CURVE``/
    ``CONTINUOUS_ZERO_RATE`` rows (see the module docstring's rate-basis
    decision), sorted by ascending ``_validate_bloomberg_tenor_label`` order.
    ``discount_factor_evidence`` is the same-length, same-order ``D``-ticker
    evidence -- present for cross-check only.
    """

    curve_points: tuple[BLICurvePoint, ...]
    discount_factor_evidence: tuple[BloombergDiscountFactorEvidence, ...]


def load_bloomberg_usd_sofr_option_discount_curve(
    tenors: Sequence[str] = DEFAULT_USD_SOFR_TENORS,
) -> BloombergUsdSofrOptionDiscountCurveResult:
    """Load a live Bloomberg Curve #490 USD SOFR option discount curve.

    ``tenors`` must be a non-empty sequence of distinct, non-blank tenor
    labels, each accepted by ``_validate_bloomberg_tenor_label`` --
    validated before any Bloomberg request is sent, so a malformed tenor
    never reaches Bloomberg at all. Defaults to ``DEFAULT_USD_SOFR_TENORS``.

    Raises ``ValueError`` for a caller-input problem (empty/duplicate/
    malformed tenor). Raises ``BLIBloombergDapiError`` for every
    Bloomberg-side failure -- see the module docstring's fail-closed list.
    A successful call always returns a complete curve for every requested
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
        _validate_bloomberg_tenor_label(tenor)  # fail closed on a malformed tenor label

    try:
        import blpapi
    except ImportError as exc:
        raise BLIBloombergDapiError(
            "blpapi is not installed -- Bloomberg's official blpapi package is "
            "required in this environment to load a live Bloomberg USD SOFR option "
            "discount curve"
        ) from exc

    zero_rate_tickers = {
        tenor: build_zero_rate_ticker(USD_SOFR_BLOOMBERG_CURVE_NUMBER, tenor) for tenor in tenors
    }
    discount_factor_tickers = {
        tenor: build_discount_factor_ticker(USD_SOFR_BLOOMBERG_CURVE_NUMBER, tenor)
        for tenor in tenors
    }
    all_securities = [
        security
        for tenor in tenors
        for security in (zero_rate_tickers[tenor], discount_factor_tickers[tenor])
    ]

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
        request.append("fields", _MATURITY_FIELD)

        session.sendRequest(request)

        security_data_records: list = []

        deadline = _monotonic() + _REQUEST_TIMEOUT_MS / 1000.0
        done = False
        while not done:
            remaining_seconds = deadline - _monotonic()
            if remaining_seconds <= 0:
                raise BLIBloombergDapiError(
                    "Bloomberg DAPI request timed out waiting for a USD SOFR option "
                    "discount curve response"
                )
            remaining_ms = max(1, int(remaining_seconds * 1000))
            event = session.nextEvent(remaining_ms)

            if event.eventType() == blpapi.Event.TIMEOUT:
                raise BLIBloombergDapiError(
                    "Bloomberg DAPI request timed out waiting for a USD SOFR option "
                    "discount curve response"
                )
            if event.eventType() not in (blpapi.Event.PARTIAL_RESPONSE, blpapi.Event.RESPONSE):
                continue

            for msg in event:
                if msg.hasElement("responseError"):
                    raise BLIBloombergDapiError(
                        f"Bloomberg DAPI responseError for the USD SOFR option discount "
                        f"curve request: {msg.getElement('responseError')}"
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

    def _resolve(security: str) -> tuple[str, str]:
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

        if not field_data.hasElement(_MATURITY_FIELD):
            raise BLIBloombergDapiError(
                f"Bloomberg DAPI response for {security!r} is missing {_MATURITY_FIELD}"
            )
        raw_maturity = _get_element_as_string(blpapi, field_data, _MATURITY_FIELD, security)
        if not raw_maturity:
            raise BLIBloombergDapiError(
                f"Bloomberg DAPI response for {security!r} is missing {_MATURITY_FIELD}"
            )

        return raw_last_price, raw_maturity

    def _parse_maturity(security: str, raw_maturity: str) -> date:
        try:
            return _parse_iso_date(raw_maturity, f"{security} MATURITY")
        except ValueError as exc:
            raise BLIBloombergDapiError(
                f"Bloomberg DAPI returned a non-parseable MATURITY for {security!r}: {exc}"
            ) from exc

    curve_points: list[BLICurvePoint] = []
    evidence: list[BloombergDiscountFactorEvidence] = []
    node_maturities: list[tuple[int, str, date]] = []

    for tenor in tenors:
        zero_security = zero_rate_tickers[tenor]
        discount_factor_security = discount_factor_tickers[tenor]

        raw_zero_last_price, raw_zero_maturity = _resolve(zero_security)
        raw_df_last_price, raw_df_maturity = _resolve(discount_factor_security)

        # _parse_finite_float already rejects a non-finite raw LAST_PRICE (inf/nan);
        # dividing a finite float by 100 can never itself become non-finite, so the
        # "non-finite zero rate" fail-closed condition is fully covered here.
        zero_rate_percent = _parse_finite_float(raw_zero_last_price, _LAST_PRICE_FIELD)
        zero_rate = zero_rate_percent / 100.0

        discount_factor = _parse_finite_float(raw_df_last_price, _LAST_PRICE_FIELD)
        if not discount_factor > 0:
            raise BLIBloombergDapiError(
                f"Bloomberg DAPI discount factor for {discount_factor_security!r} must be "
                f"strictly positive, got {discount_factor!r}"
            )

        zero_maturity_date = _parse_maturity(zero_security, raw_zero_maturity)
        df_maturity_date = _parse_maturity(discount_factor_security, raw_df_maturity)

        # D is presented as same-node cross-check evidence for Z -- if Bloomberg's
        # own returned MATURITY disagrees between the two tickers at this tenor,
        # they are not evidence for the same curve node, and this must fail closed
        # rather than silently keep only Z's date.
        if zero_maturity_date != df_maturity_date:
            raise BLIBloombergDapiError(
                f"Bloomberg DAPI MATURITY mismatch for tenor {tenor!r}: "
                f"{zero_security!r} returned {zero_maturity_date.isoformat()!r}, "
                f"{discount_factor_security!r} returned {df_maturity_date.isoformat()!r} -- "
                "Z and D do not evidence the same curve node"
            )

        order_key = _validate_bloomberg_tenor_label(tenor)
        node_maturities.append((order_key, tenor, zero_maturity_date))

        curve_points.append(
            BLICurvePoint(
                curve_id=USD_SOFR_CURVE_ID,
                curve_name=USD_SOFR_CURVE_NAME,
                currency=Currency.USD,
                curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
                tenor=tenor,
                rate=zero_rate,
                rate_basis=BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
                source_system=USD_SOFR_SOURCE_SYSTEM,
                status=BLIMarketDataStatus.ACTIVE,
                maturity_date=raw_zero_maturity,
            )
        )
        evidence.append(
            BloombergDiscountFactorEvidence(
                tenor=tenor,
                security=discount_factor_security,
                raw_last_price=raw_df_last_price,
                discount_factor=discount_factor,
                maturity=raw_df_maturity,
            )
        )

    ordered = sorted(node_maturities, key=lambda item: item[0])
    seen_maturity_dates: dict[date, str] = {}
    previous_maturity_date: date | None = None
    previous_tenor: str | None = None
    for _order_key, tenor, maturity_date in ordered:
        if maturity_date in seen_maturity_dates:
            raise BLIBloombergDapiError(
                f"Bloomberg DAPI returned the same MATURITY {maturity_date.isoformat()!r} "
                f"for tenor {seen_maturity_dates[maturity_date]!r} and tenor {tenor!r} -- "
                "duplicate curve node maturity"
            )
        seen_maturity_dates[maturity_date] = tenor
        if previous_maturity_date is not None and maturity_date <= previous_maturity_date:
            raise BLIBloombergDapiError(
                f"Bloomberg DAPI MATURITY did not increase from tenor {previous_tenor!r} "
                f"({previous_maturity_date.isoformat()}) to tenor {tenor!r} "
                f"({maturity_date.isoformat()})"
            )
        previous_maturity_date = maturity_date
        previous_tenor = tenor

    order_by_tenor = {tenor: index for index, (_, tenor, _) in enumerate(ordered)}
    curve_points_sorted = tuple(sorted(curve_points, key=lambda point: order_by_tenor[point.tenor]))
    evidence_sorted = tuple(sorted(evidence, key=lambda item: order_by_tenor[item.tenor]))

    return BloombergUsdSofrOptionDiscountCurveResult(
        curve_points=curve_points_sorted,
        discount_factor_evidence=evidence_sorted,
    )
