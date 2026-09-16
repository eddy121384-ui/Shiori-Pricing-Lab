"""``HISTORICAL_YIELD_VOL_MO`` as a live Workbench vol source (Issue #214).

Issue #211 / PR #212 built every primitive this source needs and stopped at
the trader's edge: the trader could calculate a Middle-Office-parity
Historical Yield Vol in Markets, and Shiori could convert one into an
``EQUIVALENT_PRICE_VOL``, but nothing connected the two, so the converted
volatility had to be carried across the screen by hand. This module is that
connection, and nothing else.

**What it is.** One bounded case transform, shaped exactly like Issue #177's
``apply_effective_forward_to_case``: a case whose
``volatility_input.source_system`` declares this source has its volatility
**re-derived on every run** from the case's own inputs, and the derived value
is written back into the same reviewed ``BLIVolatilityInput`` field the
manual path already fills. Every other case is returned unchanged, with
``None`` provenance and no Bloomberg call.

**What it never does.** It computes no statistic, no duration, no unit
conversion and no volatility of its own. In order it calls, each exactly
once and each unmodified:

1. ``bloomberg_bond_yield_history.load_bloomberg_bond_yield_history`` -- the
   one #196 Yield-series loader, reached through the same
   ``parse_bond_identifier`` bridge the Markets routes use;
2. ``historical_yield_volatility.calculate_historical_yield_volatility`` --
   the one #197 calculator, at its own confirmed convention;
3. ``bli_bond_modified_duration.calculate_bond_modified_duration`` -- the one
   approved current-time ``D_B``, on the selected price basis;
4. ``bli_historical_equivalent_price_vol.historical_equivalent_price_vol``
   -- the one approved ``sigma_P = |D_B| x sigma_hist_abs`` conversion;
5. ``...historical_equivalent_price_vol_volatility_input`` -- the one
   publication step, told the basis of the composition it is entering.

**Re-derived, never reused.** The Yield series is re-fetched and the whole
chain re-run on every priced run, for the same reason the Shiori Derived
Forward is: a number left in the envelope describes the inputs it was
computed from, not the ones about to be priced. So a new bond, a changed
clean price, a changed settlement or convention profile, a changed Historical
Yield query and -- above all -- a changed ``BOND_OPTION_PRICE_BASIS`` cannot
leave a stale volatility behind. There is nothing to invalidate, because
nothing is kept.

**Fail closed, with the real reason.** Every refusal raises
:class:`HistoricalVolSourceUnavailableError` carrying the composed
primitive's own text. Nothing is substituted: not ``DIRECT_PRICE_VOL``, not
VCUB, not another window, not a flat vol, not the last successful value and
not the previous bond's. A trader may explicitly select a different source;
this module never selects one.

**The window may not reach past the valuation being priced.** The query is
bounded by the case's own ``valuation_date`` before any Bloomberg request, so
asking for observations that did not exist at ``t0`` is refused as the bad
question it is rather than after a round trip. The conversion producer
separately refuses observations dated after ``t0`` in whatever Bloomberg
actually returned, so a future-informed ``sigma_P`` was never priceable; this
bound makes the refusal cheap and names the real cause.

**Every bond in the chain must be the same bond.** The Historical Yield query
names one, the ticket names one, and the spot quote the duration is taken at
names one. All three are compared by exact ISIN -- the query before any
Bloomberg call -- because a volatility calculated for one bond, or
differentiated against another bond's price, is a perfectly ordinary number
that nothing downstream could catch.

**Realized, not implied.** What this source produces is a historical/realized
proxy approved for internal-model reconciliation, and it is labelled that way
end to end -- never as Bloomberg implied vol and never as VCUB.
"""

from __future__ import annotations

from datetime import date

from shiori_pricing_lab.data.bli_snapshot import BLIBondQuote
from shiori_pricing_lab.data.bli_standalone_contract import BLIStandaloneBondReferenceData
from shiori_pricing_lab.data.bli_standalone_option_request_builder import (
    resolve_standalone_bond_reference_by_isin,
)
from shiori_pricing_lab.data.bloomberg_bond_quote import parse_bond_identifier
from shiori_pricing_lab.data.bloomberg_bond_yield_history import (
    load_bloomberg_bond_yield_history,
)
from shiori_pricing_lab.data.historical_yield_volatility import (
    HISTORICAL_YIELD_VOL_MO_SOURCE,
    MIDDLE_OFFICE_6M_OBSERVATION_COUNT,
    PUBLISHED_VOLATILITY_UNIT,
    calculate_historical_yield_volatility,
    validate_requested_observation_count,
)
from shiori_pricing_lab.pricing.bli_bond_modified_duration import (
    BLIBondDurationError,
    calculate_bond_modified_duration,
)
from shiori_pricing_lab.pricing.bli_bond_option_price_basis import BondOptionPriceBasis
from shiori_pricing_lab.pricing.bli_historical_equivalent_price_vol import (
    VOLATILITY_KIND,
    BLIHistoricalEquivalentPriceVolError,
    historical_equivalent_price_vol,
    historical_equivalent_price_vol_volatility_input,
)
from shiori_pricing_lab.pricing.treasury_futures_implied_yield import IrregularFirstCoupon
from shiori_pricing_lab.products.enums import Currency, coerce_enum

#: The ``volatility_input.source_system`` token that selects this source.
#: Deliberately #197's own canonical name rather than a Workbench alias, so
#: the token a trader selects, the token the conversion stamps and the token
#: the exported run reports are one string.
HISTORICAL_YIELD_VOL_SOURCE = HISTORICAL_YIELD_VOL_MO_SOURCE

#: The envelope key carrying the Historical Yield query this run derives
#: from. It lives on the case, beside ``spot_settlement_date`` and
#: ``convention_profile``, for the same Issue #177 reason: a saved or re-sent
#: case must reproduce its own volatility from the case alone rather than
#: from live browser state.
HISTORICAL_YIELD_VOL_REQUEST_KEY = "historical_yield_vol_request"

_REQUIRED_QUERY_KEYS = ("bond_identifier", "yield_field", "start_date", "end_date")
_OPTIONAL_QUERY_KEYS = ("field_meaning", "field_unit")

#: Stated on every provenance payload so a reader never has to infer from the
#: source name that this is a backward-looking statistic.
HISTORICAL_VOL_DISCLOSURE = (
    "Historical / realized proxy for internal-model reconciliation, derived from this "
    "bond's own past Yield observations. It is not a current market-implied volatility, "
    "not Bloomberg implied vol and not VCUB."
)


class HistoricalVolSourceUnavailableError(ValueError):
    """One fail-closed refusal type for every condition in this module."""


def case_declares_historical_vol_source(case: object) -> bool:
    """Whether ``case`` asks for its volatility to be derived by this source."""

    if not isinstance(case, dict):
        return False
    volatility_input = case.get("volatility_input")
    if not isinstance(volatility_input, dict):
        return False
    return volatility_input.get("source_system") == HISTORICAL_YIELD_VOL_SOURCE


def _require_mapping(value: object, field_name: str) -> dict:
    if not isinstance(value, dict):
        raise HistoricalVolSourceUnavailableError(
            f"{field_name} must be a JSON object for the {HISTORICAL_YIELD_VOL_SOURCE} "
            f"volatility source, got {type(value).__name__}"
        )
    return value


def _require_iso_date(value: object, field_name: str) -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise HistoricalVolSourceUnavailableError(
            f"{field_name} must be a YYYY-MM-DD date, got {type(value).__name__}"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HistoricalVolSourceUnavailableError(
            f"{field_name} must be a YYYY-MM-DD date: {exc}"
        ) from exc


def historical_yield_vol_query(case: dict) -> dict:
    """Return the validated Historical Yield query ``case`` carries.

    The four identifying keys are required and none is inferred: there is no
    default Yield mnemonic, no expiry-to-lookback mapping and no date range
    derived from the ticket -- Issue #197 forbids all three, so a trader who
    wants a window states it. ``requested_observation_count`` defaults to
    Middle Office's confirmed 181-observation / 180-Yield-Change horizon and
    goes through #197's own validator.
    """

    query = _require_mapping(
        case.get(HISTORICAL_YIELD_VOL_REQUEST_KEY), HISTORICAL_YIELD_VOL_REQUEST_KEY
    )
    missing = [
        key
        for key in _REQUIRED_QUERY_KEYS
        if not isinstance(query.get(key), str) or not query[key].strip()
    ]
    if missing:
        raise HistoricalVolSourceUnavailableError(
            f"{HISTORICAL_YIELD_VOL_REQUEST_KEY} is missing {', '.join(missing)} -- the "
            f"{HISTORICAL_YIELD_VOL_SOURCE} source needs the bond, the "
            "workstation-confirmed Bloomberg Yield field and the date range stated "
            "explicitly; none of the four is inferred from the ticket"
        )
    try:
        requested_observation_count = validate_requested_observation_count(
            query.get("requested_observation_count", MIDDLE_OFFICE_6M_OBSERVATION_COUNT)
        )
    except ValueError as exc:
        raise HistoricalVolSourceUnavailableError(str(exc)) from exc

    resolved = {key: query[key].strip() for key in _REQUIRED_QUERY_KEYS}
    resolved["requested_observation_count"] = requested_observation_count
    for key in _OPTIONAL_QUERY_KEYS:
        value = query.get(key)
        if value is not None:
            resolved[key] = value
    _require_window_ends_by_the_valuation_date(case, resolved["end_date"])
    return resolved


def _require_window_ends_by_the_valuation_date(case: dict, end_date: str) -> None:
    """Refuse a Yield window reaching past the valuation this run prices.

    A realized volatility is only honest about ``t0`` if every observation it
    was measured over existed at ``t0``. The conversion producer already
    enforces that on the observations Bloomberg actually returned
    (``_require_no_look_ahead`` in
    ``pricing/bli_historical_equivalent_price_vol``, which compares the last
    observation used against ``t0`` and refuses) -- so a future-informed
    sigma_P cannot price, and this check adds no protection that was missing.

    What it adds is *where* and *why* the refusal happens (Codex review, PR
    #215). Asking Bloomberg for observations dated after the valuation being
    priced is a bad question, not a bad answer, and this module's ordering
    rule is that an input this run could never price is refused for its own
    deterministic reason rather than from behind a DAPI round trip. Left to
    the downstream gate, a backdated case spent a Bloomberg request and then
    reported the *observations* as the problem, when the window asked for them.

    The bound is the case's own ``valuation_date``, and a window ending **on**
    it is the ordinary same-day case and is allowed. A ``valuation_date`` that
    is missing or malformed is left entirely to the request constructor, whose
    refusal for it is the reviewed one; this check simply does not apply.
    """

    valuation_date = case.get("valuation_date")
    if not isinstance(valuation_date, str):
        return
    try:
        valuation = date.fromisoformat(valuation_date)
        window_end = date.fromisoformat(end_date)
    except ValueError:
        # Both are the owning contracts' to refuse -- the request constructor
        # for the valuation date, the #196 loader for the window.
        return
    if window_end > valuation:
        raise HistoricalVolSourceUnavailableError(
            f"the Historical Yield window ends {window_end.isoformat()}, which is after "
            f"this ticket's valuation date {valuation.isoformat()} -- a realized "
            "volatility is measured over observations that existed at the moment being "
            "priced, so the window may not reach past it. End the window on or before "
            "the valuation date"
        )


def _resolved_reference_record(case: dict) -> BLIStandaloneBondReferenceData:
    bond_option = _require_mapping(case.get("bond_option"), "bond_option")
    universe_raw = case.get("bond_reference_data_universe")
    if not isinstance(universe_raw, list):
        raise HistoricalVolSourceUnavailableError(
            "bond_reference_data_universe must be a JSON array of "
            "BLIStandaloneBondReferenceData objects"
        )
    try:
        universe = [BLIStandaloneBondReferenceData(**record) for record in universe_raw]
        return resolve_standalone_bond_reference_by_isin(
            bond_option.get("underlying_isin"), universe
        )
    except (TypeError, ValueError) as exc:
        raise HistoricalVolSourceUnavailableError(
            f"the ticket's own bond reference data cannot be resolved, so no duration can "
            f"be calculated for it: {exc}"
        ) from exc


def _require_same_bond(case: dict, bond_identifier: str) -> str:
    """Return the Bloomberg identifier, having checked it is the ticket's bond.

    Compared on exact ISIN, the same plain string equality every other
    identity gate in this repository uses. A Historical Yield Vol calculated
    for a different security multiplied by this ticket's duration is a finite,
    ordinary-looking number, and no later step could recognise it.
    """

    bond_option = _require_mapping(case.get("bond_option"), "bond_option")
    ticket_isin = bond_option.get("underlying_isin")
    try:
        _kind, bloomberg_identifier = parse_bond_identifier(bond_identifier)
    except ValueError as exc:
        raise HistoricalVolSourceUnavailableError(
            f"the Historical Yield query names {bond_identifier!r}, which is not a bond "
            f"identifier this repository can resolve: {exc}"
        ) from exc
    try:
        _ticket_kind, ticket_identifier = parse_bond_identifier(ticket_isin)
    except ValueError as exc:
        raise HistoricalVolSourceUnavailableError(
            f"this ticket's underlying ({ticket_isin!r}) is not a bond identifier this "
            f"repository can resolve, so the Historical Yield Vol requested for "
            f"{bond_identifier!r} cannot be shown to be for the same bond: {exc}"
        ) from exc
    # Both sides through the one existing parser, then compared as the
    # symbology-qualified strings it produces. A CUSIP query against an ISIN
    # ticket therefore does not match -- and correctly so: without a lookup
    # nothing here can prove the two name the same security, and this source
    # refuses rather than assuming it.
    if bloomberg_identifier != ticket_identifier:
        raise HistoricalVolSourceUnavailableError(
            f"the Historical Yield Vol was requested for {bond_identifier!r} but this "
            f"ticket prices {ticket_isin!r} -- one bond's realized Yield volatility is "
            "never converted through another bond's duration, and the product identifies "
            "no instrument at all"
        )
    return bloomberg_identifier


def require_historical_vol_query_names_this_ticket(case: dict) -> str:
    """Validate the Historical Yield query and that it names the ticket's bond.

    Returns the symbology-qualified Bloomberg identifier the #196 loader will
    be asked for. Purely offline -- no Bloomberg call, no clock -- so the
    readiness route and the pricing routes can both run it before spending a
    round trip on a run that could never price.
    """

    query = historical_yield_vol_query(case)
    return _require_same_bond(case, query["bond_identifier"])


def _coupon_schedule(record: BLIStandaloneBondReferenceData) -> IrregularFirstCoupon:
    """The bond's own stated first-coupon frame, not a guess.

    ``issue_date`` and ``first_coupon_date`` are required, already-validated
    fields of the reviewed reference-data contract, so passing them is
    reporting the bond's terms rather than inferring a schedule. The duration
    producer holds them to the owning module's own shape rule and classifies a
    first period that is exactly one regular period as identical to the
    maturity-anchored grid -- so an ordinary bond's answer is unchanged, and a
    genuine stub is priced on its own ICMA cashflows instead of silently on
    the regular grid.
    """

    return IrregularFirstCoupon(
        accrual_start=_require_iso_date(record.issue_date, "issue_date"),
        first_coupon=_require_iso_date(record.first_coupon_date, "first_coupon_date"),
    )


def _clean_price_per_100(case: dict, record: BLIStandaloneBondReferenceData) -> float:
    """Return the spot clean price ``D_B`` is taken at, having checked whose it is.

    The quote is held to its own reviewed ``BLIBondQuote`` contract and to the
    same two coherence facts the typed pricing request enforces -- exact ISIN
    against the resolved reference data, and matching currency (Codex review,
    PR #215). Without them this derivation would take one bond's clean price,
    differentiate it against another bond's cashflows, and publish the result
    as this ticket's ``sigma_P`` -- a perfectly ordinary number that the review
    route would display as priceable while no run built from the same case
    could ever succeed.
    """

    raw_quote = _require_mapping(case.get("bond_quote"), "bond_quote")
    bond_option = _require_mapping(case.get("bond_option"), "bond_option")
    try:
        bond_quote = BLIBondQuote(**raw_quote)
        currency = coerce_enum(bond_option.get("currency"), Currency, "bond_option.currency")
    except (TypeError, ValueError) as exc:
        raise HistoricalVolSourceUnavailableError(
            f"this ticket's own spot quote is not a usable market observation, so no "
            f"current-time duration can be taken at it: {exc}"
        ) from exc
    if bond_quote.isin != record.isin:
        raise HistoricalVolSourceUnavailableError(
            f"bond_quote.isin ({bond_quote.isin!r}) does not exactly match this ticket's "
            f"resolved bond ({record.isin!r}) -- one bond's clean price is never "
            "differentiated against another bond's cashflows"
        )
    if bond_quote.currency is not currency:
        raise HistoricalVolSourceUnavailableError(
            f"bond_quote.currency ({bond_quote.currency.value}) does not match "
            f"bond_option currency ({currency.value})"
        )

    clean_price = bond_quote.clean_price_per_100
    if not isinstance(clean_price, (int, float)) or isinstance(clean_price, bool):
        raise HistoricalVolSourceUnavailableError(
            "bond_quote.clean_price_per_100 is required by the duration this source "
            f"converts through, and this ticket carries {clean_price!r} -- a yield-only "
            "quote states no price to differentiate"
        )
    return float(clean_price)


def resolve_historical_equivalent_price_vol(
    case: dict,
    price_basis: BondOptionPriceBasis,
    *,
    calculated_at: str,
) -> tuple[object, object, dict]:
    """Derive this case's Equivalent Price Vol and return it with its lineage.

    Returns ``(conversion, published_volatility_input, provenance)``. The
    conversion and the published input are the unmodified objects the
    approved producers returned; ``provenance`` is a JSON-serializable
    flattening of them for the Workbench display, the priced-run display and
    the exported run -- it is read *from* those objects and computes nothing.

    ``calculated_at`` is supplied by the caller rather than read from a
    clock here, the same contract the duration producer states: it is stamped
    on the duration and inherited by the conversion.
    """

    query = historical_yield_vol_query(case)
    bloomberg_identifier = _require_same_bond(case, query["bond_identifier"])
    record = _resolved_reference_record(case)
    clean_price = _clean_price_per_100(case, record)

    pricing_timestamp = case.get("pricing_timestamp")
    convention_profile = case.get("convention_profile")
    if not isinstance(convention_profile, str) or not convention_profile.strip():
        raise HistoricalVolSourceUnavailableError(
            "convention_profile must be selected before a current-time duration can be "
            "calculated -- Shiori never falls back to a default market convention"
        )

    provenance_kwargs = {
        key: query[key] for key in _OPTIONAL_QUERY_KEYS if key in query
    }
    # A Bloomberg-side failure is a ``BLIBloombergDapiError`` (a
    # ``RuntimeError``) and deliberately passes through untouched, so the HTTP
    # layer can still answer 502 rather than reporting an outage as a bad
    # input. Every *input* refusal these two raise -- a malformed mnemonic or
    # date range, a window with no usable Yield Changes -- is a refusal of
    # this source, and is re-raised as one with its own reason preserved
    # verbatim.
    try:
        history = load_bloomberg_bond_yield_history(
            identifier=bloomberg_identifier,
            yield_field=query["yield_field"],
            start_date=query["start_date"],
            end_date=query["end_date"],
            **provenance_kwargs,
        )
        statistic = calculate_historical_yield_volatility(
            history, requested_observation_count=query["requested_observation_count"]
        )
    except HistoricalVolSourceUnavailableError:
        raise
    except ValueError as exc:
        raise HistoricalVolSourceUnavailableError(
            f"no Historical Yield Vol for this ticket, so the "
            f"{HISTORICAL_YIELD_VOL_SOURCE} source cannot produce a price volatility: "
            f"{exc}"
        ) from exc

    try:
        duration = calculate_bond_modified_duration(
            security=statistic.security,
            convention_profile=convention_profile,
            price_basis=price_basis,
            clean_price_per_100=clean_price,
            maturity_date=_require_iso_date(record.maturity_date, "maturity_date"),
            # The reviewed reference contract states the coupon as a decimal
            # rate; the duration producer states it in percent. One named
            # conversion, at the one boundary between the two contracts.
            coupon_percent=record.coupon * 100.0,
            pricing_timestamp=pricing_timestamp,
            calculated_at=calculated_at,
            schedule=_coupon_schedule(record),
        )
    except BLIBondDurationError as exc:
        raise HistoricalVolSourceUnavailableError(
            f"no current-time duration for this ticket, so the {HISTORICAL_YIELD_VOL_SOURCE} "
            f"source cannot produce a price volatility: {exc}"
        ) from exc

    try:
        conversion = historical_equivalent_price_vol(
            statistic, duration, yield_history=history, calculated_at=calculated_at
        )
        published = historical_equivalent_price_vol_volatility_input(
            conversion, pricing_price_basis=price_basis
        )
    except BLIHistoricalEquivalentPriceVolError as exc:
        raise HistoricalVolSourceUnavailableError(str(exc)) from exc

    return conversion, published, _provenance(conversion, published, statistic, query)


def _provenance(conversion, published, statistic, query: dict) -> dict:
    """Flatten both lineages for display and export. Reads only; derives nothing."""

    duration = conversion.duration
    return {
        "vol_source": conversion.bond_vol_source_mode,
        "volatility_kind": conversion.volatility_kind,
        "volatility_basis": published.volatility_basis.value,
        "price_basis": conversion.price_basis.value,
        "disclosure": HISTORICAL_VOL_DISCLOSURE,
        "security": conversion.security,
        "requested_identifier": statistic.requested_identifier,
        # --- #197 Historical Yield Vol ----------------------------------
        "yield_field": statistic.yield_field,
        "field_meaning": statistic.field_meaning,
        "historical_yield_vol_field_unit": conversion.historical_yield_vol_field_unit,
        "historical_yield_vol_in_field_unit": conversion.historical_yield_vol_in_field_unit,
        "historical_yield_vol_in_field_unit_text": repr(
            conversion.historical_yield_vol_in_field_unit
        ),
        "historical_yield_vol_decimal_annual": conversion.historical_yield_vol_decimal_annual,
        "historical_yield_vol_decimal_annual_text": repr(
            conversion.historical_yield_vol_decimal_annual
        ),
        "historical_yield_vol_normalization_factor": (
            conversion.historical_yield_vol_normalization_factor
        ),
        "historical_yield_vol_window_status": conversion.historical_yield_vol_window_status,
        "historical_yield_vol_observation_count": (
            conversion.historical_yield_vol_observation_count
        ),
        "historical_yield_vol_requested_observation_count": (
            conversion.historical_yield_vol_requested_observation_count
        ),
        "historical_yield_vol_change_count": conversion.historical_yield_vol_change_count,
        "historical_yield_vol_convention": conversion.historical_yield_vol_convention,
        "historical_yield_vol_annualization_trading_days": (
            conversion.historical_yield_vol_annualization_trading_days
        ),
        "historical_yield_vol_requested_start_date": (
            statistic.requested_start_date.isoformat()
        ),
        "historical_yield_vol_requested_end_date": statistic.requested_end_date.isoformat(),
        "historical_yield_vol_first_observation_date": (
            None
            if statistic.first_observation_date is None
            else statistic.first_observation_date.isoformat()
        ),
        "historical_yield_vol_last_observation_date": (
            None
            if statistic.last_observation_date is None
            else statistic.last_observation_date.isoformat()
        ),
        "historical_yield_vol_source_system": statistic.source_system,
        "historical_yield_vol_acquired_at": statistic.acquired_at,
        "historical_yield_vol_calculated_at": conversion.historical_yield_vol_calculated_at,
        # --- Current-time duration D_B ----------------------------------
        "duration_convention_profile": duration.convention_profile,
        "duration_pricing_timestamp": duration.pricing_timestamp,
        "duration_settlement_date": duration.settlement_date.isoformat(),
        "duration_maturity_date": duration.maturity_date.isoformat(),
        "duration_coupon_percent": duration.coupon_percent,
        "duration_coupons_per_year": duration.coupons_per_year,
        "duration_day_count": duration.day_count,
        "duration_clean_price_per_100": duration.clean_price_per_100,
        "duration_accrued_interest_per_100": duration.accrued_interest_per_100,
        "duration_dirty_price_per_100": duration.dirty_price_per_100,
        "duration_basis_price_per_100": duration.basis_price_per_100,
        "duration_base_yield_percent": duration.base_yield_percent,
        "duration_price_derivative_per_unit_yield": duration.price_derivative_per_unit_yield,
        "modified_duration": duration.modified_duration,
        "absolute_modified_duration": duration.absolute_modified_duration,
        "absolute_modified_duration_text": repr(duration.absolute_modified_duration),
        "duration_type": duration.duration_type,
        "duration_source": duration.source,
        "duration_methodology_version": duration.methodology_version,
        # --- The conversion itself --------------------------------------
        "equivalent_price_vol": conversion.equivalent_price_vol,
        "equivalent_price_vol_text": repr(conversion.equivalent_price_vol),
        "equivalent_price_vol_unit": PUBLISHED_VOLATILITY_UNIT,
        "equivalent_price_vol_formula": "sigma_P = |D_B| x sigma_hist_abs",
        "equivalent_price_vol_methodology_version": conversion.methodology_version,
        "calculated_at": conversion.calculated_at,
        "warnings": list(conversion.warnings),
        "override_or_fallback_audit": published.override_or_fallback_audit,
        "requested_observation_count": query["requested_observation_count"],
    }


def apply_historical_equivalent_price_vol_to_case(
    case: dict, price_basis: BondOptionPriceBasis, *, calculated_at: str
) -> tuple[dict, dict | None]:
    """Return ``(case priced with the derived volatility, provenance payload)``.

    A case that does not declare this source is returned **unchanged**, with
    ``None`` provenance and no Bloomberg call -- exactly the contract
    :func:`apply_effective_forward_to_case` keeps for a case outside the two
    Issue #177 Forward modes.

    For a case that does declare it, the volatility is re-derived and written
    back wholesale, so whatever number the envelope arrived carrying is
    replaced rather than trusted. A refusal anywhere in the chain raises
    :class:`HistoricalVolSourceUnavailableError` and nothing is priced: this
    source has no fallback value, and the envelope's previous number is never
    one.

    **This is called by the pricing entry point itself**, not by a caller who
    then hands the result on (Codex review, PR #215, rounds 1-3). Two earlier
    designs tried to let a caller derive and then present evidence of having
    done so -- first the provenance mapping, then a single-use licence object
    -- and both had the same hole in a different place, because any value a
    caller holds is a value a caller can hold *again*. Deriving where the
    pricing happens removes the question rather than answering it.
    """

    if not case_declares_historical_vol_source(case):
        return case, None

    _conversion, published, provenance = resolve_historical_equivalent_price_vol(
        case, price_basis, calculated_at=calculated_at
    )
    derived_case = {
        **case,
        "volatility_input": {
            # The reviewed BLIVolatilityInput contract's own field names,
            # constructed from the published input rather than merged
            # field-by-field with whatever the envelope held: a derived
            # observation replaces its predecessor whole, the same way a
            # Bloomberg refresh replaces ``bond_quote``.
            "volatility": published.volatility,
            "volatility_basis": published.volatility_basis.value,
            "source_system": published.source_system,
            "status": published.status.value,
            "override_or_fallback_audit": published.override_or_fallback_audit,
        },
    }
    return derived_case, provenance


__all__ = [
    "HISTORICAL_VOL_DISCLOSURE",
    "HISTORICAL_YIELD_VOL_REQUEST_KEY",
    "HISTORICAL_YIELD_VOL_SOURCE",
    "VOLATILITY_KIND",
    "HistoricalVolSourceUnavailableError",
    "apply_historical_equivalent_price_vol_to_case",
    "case_declares_historical_vol_source",
    "historical_yield_vol_query",
    "require_historical_vol_query_names_this_ticket",
    "resolve_historical_equivalent_price_vol",
]
