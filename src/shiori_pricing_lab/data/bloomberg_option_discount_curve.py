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
sequence of tenor labels (default: ``DEFAULT_USD_SOFR_TENORS``, exactly
the two tenors round 11 already confirmed acquisition for -- ``"1Y"``,
``"2Y"``). This module never generates, extrapolates, or guesses a tenor;
every tenor requested is exactly one the caller named, validated against
the existing, reviewed ``pricing/bli_curve_tenor.py::
tenor_to_year_fraction`` grammar before any Bloomberg request is sent.

**Fields requested: exactly ``LAST_PRICE`` and ``MATURITY``, for both the
``Z`` and ``D`` synthetic security at every requested tenor**, in one
``ReferenceDataRequest``. ``LAST_PRICE`` is the field Eddy's own Excel
formulas use verbatim; ``MATURITY`` is Bloomberg's own returned node date
-- used as this curve's authoritative node date for the duplicate/
non-increasing-maturity fail-closed check below, never invented from the
nominal tenor label.

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
  calendar date (reuses ``data._validation._parse_iso_date``);
- two different tenors whose ``Z`` security resolves to the same
  ``MATURITY`` (duplicate curve node date);
- a ``Z`` ``MATURITY`` that does not strictly increase, in ascending
  ``tenor_to_year_fraction`` order, from one tenor to the next
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
from shiori_pricing_lab.pricing.bli_curve_tenor import tenor_to_year_fraction
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

# Eddy's own two round-11-confirmed tenors -- not a guessed/generated set.
# Override with an explicit tenors= argument for any other tenor.
DEFAULT_USD_SOFR_TENORS: tuple[str, ...] = ("1Y", "2Y")

# Testable seam for the whole-request deadline, mirroring
# bloomberg_bond_quote.py's own `_monotonic` pattern -- this module's own
# alias, independently monkeypatchable in this module's tests.
_monotonic = time.monotonic


def build_zero_rate_ticker(curve_number: str, tenor: str) -> str:
    """Build the ``Z`` (continuously compounded zero rate) synthetic ticker.

    Pure string construction from Eddy's own confirmed grammar -- never a
    guess. ``curve_number``/``tenor`` are used verbatim, with no format
    validation beyond simple substitution (callers validate ``tenor``
    themselves via ``tenor_to_year_fraction`` before this is used to build
    a request).
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
    decision), sorted by ascending ``tenor_to_year_fraction``.
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
    labels, each accepted by ``tenor_to_year_fraction`` -- validated before
    any Bloomberg request is sent, so a malformed tenor never reaches
    Bloomberg at all. Defaults to ``DEFAULT_USD_SOFR_TENORS``.

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
        tenor_to_year_fraction(tenor)  # fail closed on a malformed tenor label

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

    curve_points: list[BLICurvePoint] = []
    evidence: list[BloombergDiscountFactorEvidence] = []
    node_maturities: list[tuple[float, str, date]] = []

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

        try:
            zero_maturity_date = _parse_iso_date(raw_zero_maturity, f"{zero_security} MATURITY")
        except ValueError as exc:
            raise BLIBloombergDapiError(
                f"Bloomberg DAPI returned a non-parseable MATURITY for {zero_security!r}: {exc}"
            ) from exc
        year_fraction = tenor_to_year_fraction(tenor)
        node_maturities.append((year_fraction, tenor, zero_maturity_date))

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
    for _year_fraction, tenor, maturity_date in ordered:
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
