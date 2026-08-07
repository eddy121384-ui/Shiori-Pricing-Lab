"""Bloomberg DAPI Bond Master field probe (PR #141 third revision).

Standalone diagnostic CLI -- **not** part of the production pricing or
workbench path, and never imported by either. Sends exactly one
``ReferenceDataRequest`` for a single security and a list of candidate
Bloomberg field mnemonics, then reports, per field, exactly one of:

- the raw value Bloomberg returned (as a string, no parsing/coercion);
- ``absent`` (the field was simply not present in ``fieldData`` -- often
  because the mnemonic does not exist, does not apply to this security, or
  the account is not entitled to it);
- ``field_exception`` (Bloomberg's own ``fieldExceptions`` array named this
  field, with whatever detail it gave).

**Purpose.** Let Eddy empirically confirm or refute a candidate Bond Master
field mnemonic on his own logged-in Bloomberg Terminal, before any such
mnemonic is ever wired into
``shiori_pricing_lab.data.bloomberg_bond_quote._BOND_MASTER_FIELD_MAP`` or
``_BOND_MASTER_RAW_DISPLAY_FIELD_MAP``. This script *proves or disproves* a
candidate; it never asserts one is correct on its own, and this file changes
no production mapping by itself.

**Resolution history (PR #141).** Eddy probed all 12 of this script's
original candidates against a real US Treasury (``US91282CLJ89``) and a
real UK Gilt (``GB00BFX0ZL78``) on his own Bloomberg Terminal. Every one of
them is now resolved, so the default candidate list below is empty:

- Confirmed and wired into ``_BOND_MASTER_FIELD_MAP`` (typed schema, with an
  explicit value transform): ``CPN``, ``CPN_FREQ``, ``ISSUE_DT``,
  ``MATURITY``, ``FIRST_CPN_DT``, ``CALLABLE``, ``SINKABLE``.
- Confirmed to return a value, and wired into
  ``_BOND_MASTER_RAW_DISPLAY_FIELD_MAP`` (display-only -- never safe to
  coerce into a typed enum): ``DAY_CNT_DES``, ``MTY_TYP``, ``CALC_TYP_DES``.
  ``MTY_TYP`` is *also* wired a second time, as typed structural evidence --
  see the Issue #161 follow-up resolution history below.
- Confirmed ``BAD_FLD`` against both test securities -- must not be
  re-added without a fresh, separately-approved confirmation:
  ``PENULTIMATE_COUPON_DATE``, ``REDEMPTION_VALUE``.

``business_day_convention``, ``bond_type``, ``yield_convention``, and
``status`` still have no confirmed-safe mnemonic at all (their earlier
candidate mnemonics were rejected above, for ``bond_type``/
``yield_convention``, or never had one to begin with) -- probe a new
candidate for these explicitly via ``--fields``.

``ex_dividend_days`` is a special case (Issue #161 follow-up: ex-dividend
convention convergence): every market registered in
``bli_bond_convention_profile.CONVENTION_PROFILES`` today (``UST``,
``US_CORPORATE``, ``GERMAN_GOVT``) carries its own confirmed value as a
*profile convention*, not a probed Bloomberg field -- so none of them needs
a mnemonic here. That is not true of every market Shiori might register
later: Eddy's own workstation search turned up
``EX_DIVIDEND_DATE_GILTS_REALTIME``, a real, Gilt-specific field name,
which is itself evidence that a UK Gilt has a genuine, non-zero pre-payment
ex-dividend window (conventionally quoted as 7 business days) rather than
one that happens to be zero. This mnemonic is recorded here as evidence
only -- it has not been probed against a live DAPI response, its returned
value is not confirmed, and it is not wired into any profile. A future Gilt
profile must probe and confirm it first, and must not collapse "7 business
days" into "7 calendar days" when converting it into ``ex_dividend_days``
(see ``bli_bond_convention_profile``'s own "Deliberately not registered:
Gilt" note for why the two are not interchangeable here).

**Resolution history (Issue #161 follow-up: plain fixed-coupon structural
evidence).** Eddy probed ``US91282CMC28`` (UST, already admitted),
``US023135EC69`` (a real USD fixed-rate corporate bond candidate),
``DE000BU2Z072`` (a real EUR fixed-rate German government bond candidate),
``GB00BFX0ZL78`` (a UK Gilt -- redemption-structure evidence only, this
market is not a registered profile) and AMZN (a real USD corporate bond) on
his own Bloomberg Terminal:

- Confirmed and wired into
  ``shiori_pricing_lab.data.bloomberg_bond_quote._BOND_MASTER_EVIDENCE_FIELD_MAP``
  as *admission* evidence (each field's confirmed value is what the pricing
  convention-profile gate now requires -- see
  ``pricing.bli_bond_convention_profile.confirms_plain_fixed_coupon_evidence``):
  ``CPN_TYP`` (``US91282CMC28``/``US023135EC69``/``DE000BU2Z072``, all three:
  ``"FIXED"``), ``INFLATION_LINKED_INDICATOR`` (all three: ``"N"``),
  ``CONVERTIBLE`` (all three: ``"N"``).
- ``MTY_TYP`` (DS092) -> ``maturity_refund_type`` (Issue #161 follow-up:
  redemption-structure gate, replacing ``amortizing_flag`` below). Confirmed
  and wired as *admission* evidence via an exact-value allowlist, per
  Bloomberg's own MTY_TYP Full Definition / Enumerations:
  ``US91282CMC28`` (UST) returned ``"NORMAL"``; ``GB00BFX0ZL78`` (evidence
  only) returned ``"AT MATURITY"`` -- both are the allowlist's positive
  evidence. AMZN returned ``"CALLABLE"`` -- confirmed *negative* evidence
  that the allowlist correctly refuses a real callable bond; AMZN is a
  rejection fixture, never a candidate positive UAT security for
  ``US_CORPORATE``.
- Confirmed to return a value and wired into the same map, but as
  display/evidence data only -- **not** an admission criterion (Eddy's
  explicit correction: it cannot safely classify a profile, and Shiori
  never auto-selects one anyway, since the trader always picks explicitly):
  ``SECURITY_TYP`` (``"US GOVERNMENT"`` / ``"GLOBAL"`` / ``"EURO-ZONE"``
  respectively).

**Superseded, not re-added** (same standing as ``PENULTIMATE_COUPON_DATE``/
``REDEMPTION_VALUE`` above -- confirmed ``BAD_FLD``/not-applicable/rejected,
must not be re-added without a fresh, separately-approved confirmation):
the earlier ``amortizing_flag`` candidates ``IS_AMORTIZING``, ``AMORT_TYP``,
``REDEMP_TYP``, ``SCHED_TYP`` (``BAD_FLD`` against the corporate and German
government candidates), ``MTG_TYP`` (not applicable to either), and
``PRINCIPAL_FACTOR`` (returned ``1.000000`` on both, but a factor of 1.0
only proves the principal has not yet paid down, never that no future
amortization schedule exists -- never approved as evidence). ``MTY_TYP``
replaces this whole line of investigation as the gate's redemption-structure
evidence rather than resolving it.

Also confirmed against ``US91282CMC28``, ``US023135EC69`` and
``DE000BU2Z072`` (not a new field, but new observations of an
already-confirmed one): ``DAY_CNT_DES`` reads ``"30/360"`` for the corporate
candidate and ``"ACT/ACT"`` for the German government candidate, both
matching Annex A's confirmed day count for that market -- see
``bli_bond_convention_profile.US_CORPORATE_CONVENTION_PROFILE``/
``GERMAN_GOVT_CONVENTION_PROFILE``'s own ``day_count_evidence``.

**Still needed: a real, confirmed non-callable USD corporate bond and German
government bond.** The redemption-structure gate itself is now resolved
(``MTY_TYP``, above) -- what remains is a genuine end-to-end pricing UAT
candidate for each of ``US_CORPORATE``/``GERMAN_GOVT``: a real security
whose confirmed ``MTY_TYP`` reads ``"NORMAL"`` or ``"AT MATURITY"``,
alongside the three other confirmed structural-evidence fields, priced
through this path start to finish. AMZN proved the gate rejects a real
callable bond correctly; it does not, and cannot, prove the gate admits
correctly, since it is deliberately a rejected security. The next step is
probing a new candidate security (via ``--identifier``/``--fields``, exactly
like the securities above) on Eddy's own Bloomberg Terminal, not a new
field mnemonic.

**Usage** (on a Bloomberg-networked workstation, Terminal running and
logged in locally)::

    python tools/bloomberg_dapi_probe.py --identifier US91282CLJ89 \\
        --fields CPN,CPN_FREQ,MATURITY,ISSUE_DT,DAY_CNT_DES
    python tools/bloomberg_dapi_probe.py --identifier GB00BFX0ZL78 \\
        --fields CALLABLE,SINKABLE

``--identifier`` accepts a plain 12-character ISIN or 9-character CUSIP --
reuses the workbench's own bounded parser (``parse_bond_identifier``),
never a Bloomberg yellow-key ticker. ``--fields`` is a required-in-practice
comma-separated list of Bloomberg mnemonics to probe for a new,
not-yet-confirmed destination field (the default candidate list is now
empty -- see the resolution history above).

This script performs no pricing, no schema construction, no workbench/UI
wiring, and writes nothing to disk. A bad/unentitled/inapplicable field
mnemonic is reported, never allowed to abort the whole probe run -- that is
exactly the point of running it.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass

from shiori_pricing_lab.data.bloomberg_bond_quote import parse_bond_identifier

_DAPI_HOST = "localhost"
_DAPI_PORT = 8194
_REFDATA_SERVICE = "//blp/refdata"
_APIFLDS_SERVICE = "//blp/apiflds"
_REQUEST_TIMEOUT_MS = 10_000

# Candidate Bond Master field mnemonics -- UNCONFIRMED against any live DAPI
# response. Empty: every candidate this script has ever proposed is now
# resolved one way or another (see the module docstring's two resolution-
# history sections above) -- CPN/CPN_FREQ/ISSUE_DT/MATURITY/FIRST_CPN_DT/
# CALLABLE/SINKABLE and CPN_TYP/INFLATION_LINKED_INDICATOR/CONVERTIBLE/
# MTY_TYP are confirmed and wired into _BOND_MASTER_FIELD_MAP /
# _BOND_MASTER_EVIDENCE_FIELD_MAP as admission evidence; SECURITY_TYP is
# confirmed and wired into _BOND_MASTER_EVIDENCE_FIELD_MAP too, but as
# display/evidence data only, not an admission criterion; DAY_CNT_DES/
# MTY_TYP/CALC_TYP_DES are confirmed and wired into
# _BOND_MASTER_RAW_DISPLAY_FIELD_MAP too (display-only -- MTY_TYP is
# deliberately wired to both maps, see that map's own comment for why);
# PENULTIMATE_COUPON_DATE/REDEMPTION_VALUE/IS_AMORTIZING/AMORT_TYP/
# REDEMP_TYP/SCHED_TYP/MTG_TYP are confirmed rejected and must not be
# re-added without a fresh confirmation; PRINCIPAL_FACTOR is confirmed to
# return a value but has no approved use. Add a new entry here
# only for a genuinely new, not-yet-probed candidate mnemonic -- see the
# "still needed" section above for what Eddy should look for next (a real,
# confirmed non-callable USD corporate/German government UAT security) and
# where.
_CANDIDATE_BOND_MASTER_FIELDS: dict[str, str] = {}
# business_day_convention, bond_type, yield_convention, ex_dividend_days,
# status, amortizing_flag: deliberately no candidate here at all -- pass
# --fields explicitly if you have one.


@dataclass(frozen=True)
class ProbeFieldResult:
    """One field's raw probe outcome -- never a parsed/coerced value."""

    field: str
    status: str  # "returned" | "absent" | "field_exception"
    value: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class FieldDescription:
    """One field's Bloomberg-published metadata -- never a value, never a guess.

    ``overrides`` is the override field-id list *Bloomberg itself* documents
    for the field, so a caller can record what a field demands instead of
    asking a human to look up an override mnemonic by hand.
    """

    field: str
    status: str  # "described" | "absent" | "field_error"
    mnemonic: str | None = None
    description: str | None = None
    datatype: str | None = None
    overrides: tuple[str, ...] = ()
    documentation: str | None = None
    detail: str | None = None


def _send_request(
    *,
    service_uri: str,
    request_name: str,
    configure,
    collect,
    context: str,
) -> None:
    """Open one DAPI session, send one request, and feed each response message to ``collect``.

    The single place this file starts a session, opens a service, runs the
    one-deadline event loop and stops the session again -- shared by
    :func:`probe_fields` and :func:`describe_fields` so neither grows a
    second copy of that handling. ``configure`` fills the request;
    ``collect`` receives every ``PARTIAL_RESPONSE``/``RESPONSE`` message
    that carries no ``responseError``. Raises ``ImportError`` if ``blpapi``
    is missing and ``RuntimeError`` for any session/connectivity/envelope
    failure; per-field outcomes are the caller's business, not this
    function's.
    """

    import blpapi

    session_options = blpapi.SessionOptions()
    session_options.setServerHost(_DAPI_HOST)
    session_options.setServerPort(_DAPI_PORT)
    session = blpapi.Session(session_options)

    try:
        if not session.start():
            raise RuntimeError(
                f"Bloomberg DAPI session failed to start against {_DAPI_HOST}:{_DAPI_PORT} "
                "-- confirm a Bloomberg Terminal is running and logged in locally"
            )
        if not session.openService(service_uri):
            raise RuntimeError(f"Bloomberg DAPI failed to open service {service_uri}")

        service = session.getService(service_uri)
        request = service.createRequest(request_name)
        configure(request)
        session.sendRequest(request)

        deadline = time.monotonic() + _REQUEST_TIMEOUT_MS / 1000.0
        done = False
        while not done:
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                raise RuntimeError(f"Bloomberg DAPI request timed out for {context}")
            remaining_ms = max(1, int(remaining_seconds * 1000))
            event = session.nextEvent(remaining_ms)

            if event.eventType() == blpapi.Event.TIMEOUT:
                raise RuntimeError(f"Bloomberg DAPI request timed out for {context}")
            if event.eventType() not in (blpapi.Event.PARTIAL_RESPONSE, blpapi.Event.RESPONSE):
                continue

            for msg in event:
                if msg.hasElement("responseError"):
                    raise RuntimeError(
                        f"Bloomberg DAPI responseError for {context}: "
                        f"{msg.getElement('responseError')}"
                    )
                collect(msg)

            if event.eventType() == blpapi.Event.RESPONSE:
                done = True
    finally:
        session.stop()


def describe_fields(fields: list[str]) -> list[FieldDescription]:
    """Ask ``//blp/apiflds`` what Bloomberg publishes about each of ``fields``.

    One ``FieldInfoRequest`` with ``returnFieldDocumentation`` set, reported
    per requested mnemonic as ``described`` / ``absent`` / ``field_error``.
    This is metadata discovery, not a value probe: it is how an override
    field-id list is *obtained from Bloomberg* rather than guessed by this
    repo or looked up by hand. It reads no security, prices nothing, and
    promotes nothing -- an unknown mnemonic simply comes back
    ``field_error``/``absent``.

    Raises ``ImportError`` if ``blpapi`` is not installed and
    ``RuntimeError`` for a session/connectivity/envelope failure; one
    unknown field never raises.
    """

    import blpapi

    def _configure(request) -> None:
        for field in fields:
            request.append("id", field)
        request.set("returnFieldDocumentation", True)

    records: list = []

    def _collect(message) -> None:
        if not message.hasElement("fieldData"):
            return
        field_data_array = message.getElement("fieldData")
        for i in range(field_data_array.numValues()):
            records.append(field_data_array.getValueAsElement(i))

    _send_request(
        service_uri=_APIFLDS_SERVICE,
        request_name="FieldInfoRequest",
        configure=_configure,
        collect=_collect,
        context="field metadata request",
    )

    def _string(element, name: str) -> str | None:
        if not element.hasElement(name):
            return None
        try:
            return element.getElementAsString(name)
        except blpapi.exception.Exception:
            return None

    described: dict[str, FieldDescription] = {}
    positional: list[FieldDescription] = []
    for record in records:
        record_id = _string(record, "id")
        if record.hasElement("fieldError"):
            description = FieldDescription(
                field=record_id or "",
                status="field_error",
                detail=str(record.getElement("fieldError")),
            )
        elif record.hasElement("fieldInfo"):
            info = record.getElement("fieldInfo")
            override_ids: list[str] = []
            if info.hasElement("overrides"):
                overrides_element = info.getElement("overrides")
                for i in range(overrides_element.numValues()):
                    try:
                        override_ids.append(overrides_element.getValueAsString(i))
                    except blpapi.exception.Exception:
                        continue
            description = FieldDescription(
                field=record_id or "",
                status="described",
                mnemonic=_string(info, "mnemonic"),
                description=_string(info, "description"),
                datatype=_string(info, "datatype"),
                overrides=tuple(override_ids),
                documentation=_string(info, "documentation"),
            )
        else:
            continue
        positional.append(description)
        for key in (record_id, description.mnemonic):
            if key:
                described.setdefault(key, description)

    results: list[FieldDescription] = []
    for index, field in enumerate(fields):
        match = described.get(field)
        # Bloomberg answers a FieldInfoRequest in request order and may echo
        # a numeric field id rather than the mnemonic that was asked for, so
        # fall back to position only when the response is exactly as long as
        # the request -- never a fuzzy name match.
        if match is None and len(positional) == len(fields):
            match = positional[index]
        if match is None:
            results.append(FieldDescription(field=field, status="absent"))
            continue
        results.append(
            FieldDescription(
                field=field,
                status=match.status,
                mnemonic=match.mnemonic,
                description=match.description,
                datatype=match.datatype,
                overrides=match.overrides,
                documentation=match.documentation,
                detail=match.detail,
            )
        )
    return results


def probe_fields(
    identifier: str,
    fields: list[str],
    overrides: list[tuple[str, str]] | None = None,
) -> list[ProbeFieldResult]:
    """Send one ``ReferenceDataRequest`` for ``identifier``/``fields`` and
    report each field's raw outcome. ``identifier`` must already be a
    Bloomberg symbology-qualified string (``/isin/...`` or ``/cusip/...``);
    build it with :func:`parse_bond_identifier`, exactly like the workbench
    lookup does. Raises ``ImportError`` if ``blpapi`` is not installed, and
    ``RuntimeError`` for a connectivity/session/response-envelope failure --
    but never for an individual field being wrong, unentitled, or
    inapplicable; that is reported per-field, not raised.

    ``overrides`` is an optional list of ``(fieldId, value)`` pairs sent
    verbatim in the request's ``overrides`` element -- needed to probe a
    field that only answers inside an option context (e.g.
    ``OPT_UNDL_FORWARD_PX``). No override name or value is ever guessed or
    defaulted here: the caller passes exactly what it was told to send, and
    an empty/omitted list sends the request bare so Bloomberg's own response
    shows what it demands.
    """

    import blpapi

    def _configure(request) -> None:
        request.append("securities", identifier)
        for field in fields:
            request.append("fields", field)
        if overrides:
            overrides_element = request.getElement("overrides")
            for override_field, override_value in overrides:
                override_element = overrides_element.appendElement()
                override_element.setElement("fieldId", override_field)
                override_element.setElement("value", override_value)

    security_data_records: list = []

    def _collect(message) -> None:
        security_data_array = message.getElement("securityData")
        for i in range(security_data_array.numValues()):
            security_data_records.append(security_data_array.getValueAsElement(i))

    _send_request(
        service_uri=_REFDATA_SERVICE,
        request_name="ReferenceDataRequest",
        configure=_configure,
        collect=_collect,
        context=repr(identifier),
    )

    if len(security_data_records) != 1:
        raise RuntimeError(
            f"Bloomberg DAPI returned {len(security_data_records)} securityData record(s) for "
            f"{identifier!r}, expected exactly one"
        )

    security_data = security_data_records[0]
    if security_data.hasElement("securityError"):
        raise RuntimeError(
            f"Bloomberg DAPI securityError for {identifier!r}: "
            f"{security_data.getElement('securityError')}"
        )

    # Standard DAPI response-envelope structure (fieldId/errorInfo inside
    # each fieldExceptions entry) -- not a bond-content mnemonic guess, the
    # same structural trust this repo's production loader already places in
    # securityData/fieldData/securityError.
    field_exception_details: dict[str, str] = {}
    if security_data.hasElement("fieldExceptions"):
        field_exceptions = security_data.getElement("fieldExceptions")
        for i in range(field_exceptions.numValues()):
            exception_element = field_exceptions.getValueAsElement(i)
            field_id = None
            if exception_element.hasElement("fieldId"):
                field_id = exception_element.getElementAsString("fieldId")
            detail = str(exception_element)
            if exception_element.hasElement("errorInfo"):
                detail = str(exception_element.getElement("errorInfo"))
            if field_id:
                field_exception_details[field_id] = detail

    field_data = security_data.getElement("fieldData")

    results = []
    for field in fields:
        if field_data.hasElement(field):
            try:
                value = field_data.getElementAsString(field)
            except blpapi.exception.Exception as exc:
                results.append(
                    ProbeFieldResult(field=field, status="field_exception", detail=str(exc))
                )
                continue
            results.append(ProbeFieldResult(field=field, status="returned", value=value))
        elif field in field_exception_details:
            results.append(
                ProbeFieldResult(
                    field=field,
                    status="field_exception",
                    detail=field_exception_details[field],
                )
            )
        else:
            results.append(ProbeFieldResult(field=field, status="absent"))
    return results


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe candidate Bloomberg Bond Master field mnemonics against one security."
    )
    parser.add_argument(
        "--identifier", required=True, help="12-character ISIN or 9-character CUSIP"
    )
    parser.add_argument(
        "--fields",
        default=None,
        help="Comma-separated Bloomberg field mnemonics (default: candidate Bond Master fields)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        _, qualified_identifier = parse_bond_identifier(args.identifier)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    fields = (
        [f.strip() for f in args.fields.split(",") if f.strip()]
        if args.fields
        else list(_CANDIDATE_BOND_MASTER_FIELDS)
    )

    print(f"Probing {args.identifier!r} -> {qualified_identifier!r}")
    print(f"{'FIELD':<26}{'STATUS':<16}VALUE / DETAIL")
    print("-" * 90)
    try:
        results = probe_fields(qualified_identifier, fields)
    except ImportError as exc:
        print(
            "error: blpapi is not installed -- run this on a Bloomberg-networked "
            f"workstation with Bloomberg's official blpapi package installed ({exc})",
            file=sys.stderr,
        )
        return 2
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for result in results:
        detail = result.value if result.status == "returned" else (result.detail or "")
        print(f"{result.field:<26}{result.status:<16}{detail}")
        note = _CANDIDATE_BOND_MASTER_FIELDS.get(result.field)
        if note:
            print(f"{'':<26}{'':<16}(candidate destination: {note})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
