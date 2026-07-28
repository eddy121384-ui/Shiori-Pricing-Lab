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
- Confirmed to return a value, but wired only into
  ``_BOND_MASTER_RAW_DISPLAY_FIELD_MAP`` (display-only -- never safe to
  coerce into a typed enum): ``DAY_CNT_DES``, ``MTY_TYP``, ``CALC_TYP_DES``.
- Confirmed ``BAD_FLD`` against both test securities -- must not be
  re-added without a fresh, separately-approved confirmation:
  ``PENULTIMATE_COUPON_DATE``, ``REDEMPTION_VALUE``.

``business_day_convention``, ``bond_type``, ``yield_convention``,
``ex_dividend_days``, and ``status`` still have no confirmed-safe mnemonic
at all (their earlier candidate mnemonics were rejected above, for
``bond_type``/``yield_convention``, or never had one to begin with) --
probe a new candidate for these explicitly via ``--fields``.

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
_REQUEST_TIMEOUT_MS = 10_000

# Candidate Bond Master field mnemonics -- UNCONFIRMED against any live DAPI
# response. Empty: all 12 of this script's original candidates are now
# resolved one way or another (see the module docstring's resolution history
# above) -- CPN/CPN_FREQ/ISSUE_DT/MATURITY/FIRST_CPN_DT/CALLABLE/SINKABLE are
# confirmed and wired into _BOND_MASTER_FIELD_MAP; DAY_CNT_DES/MTY_TYP/
# CALC_TYP_DES are confirmed and wired into _BOND_MASTER_RAW_DISPLAY_FIELD_MAP
# (display-only); PENULTIMATE_COUPON_DATE/REDEMPTION_VALUE are confirmed
# BAD_FLD and must not be re-added without a fresh confirmation. Add a new
# entry here only for a genuinely new, not-yet-probed candidate mnemonic.
_CANDIDATE_BOND_MASTER_FIELDS: dict[str, str] = {}
# business_day_convention, bond_type, yield_convention, ex_dividend_days,
# status: deliberately no candidate here at all -- pass --fields explicitly
# if you have one.


@dataclass(frozen=True)
class ProbeFieldResult:
    """One field's raw probe outcome -- never a parsed/coerced value."""

    field: str
    status: str  # "returned" | "absent" | "field_exception"
    value: str | None = None
    detail: str | None = None


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
        if not session.openService(_REFDATA_SERVICE):
            raise RuntimeError(f"Bloomberg DAPI failed to open service {_REFDATA_SERVICE}")

        service = session.getService(_REFDATA_SERVICE)
        request = service.createRequest("ReferenceDataRequest")
        request.append("securities", identifier)
        for field in fields:
            request.append("fields", field)
        if overrides:
            overrides_element = request.getElement("overrides")
            for override_field, override_value in overrides:
                override_element = overrides_element.appendElement()
                override_element.setElement("fieldId", override_field)
                override_element.setElement("value", override_value)

        session.sendRequest(request)

        security_data_records = []
        deadline = time.monotonic() + _REQUEST_TIMEOUT_MS / 1000.0
        done = False
        while not done:
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                raise RuntimeError(f"Bloomberg DAPI request timed out for {identifier!r}")
            remaining_ms = max(1, int(remaining_seconds * 1000))
            event = session.nextEvent(remaining_ms)

            if event.eventType() == blpapi.Event.TIMEOUT:
                raise RuntimeError(f"Bloomberg DAPI request timed out for {identifier!r}")
            if event.eventType() not in (blpapi.Event.PARTIAL_RESPONSE, blpapi.Event.RESPONSE):
                continue

            for msg in event:
                if msg.hasElement("responseError"):
                    raise RuntimeError(
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
