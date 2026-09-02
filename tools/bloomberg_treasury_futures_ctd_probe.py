"""Bloomberg CTD / conversion-factor field-discovery probe for Treasury futures (Issue #190).

Standalone diagnostic CLI -- **not** part of the production pricing or
workbench path, and never imported by either. It exists to answer one
question on a real Bloomberg workstation, empirically: which Bloomberg field
mnemonic actually returns each fact ``data/treasury_futures_ctd`` needs?

**Resolution history (Issue #190).** Eddy probed all four current active
contracts on his own Bloomberg Terminal, and every required field is now
resolved -- so the default candidate list below is empty, exactly as
``bloomberg_dapi_probe``'s own became once its candidates were resolved.

Confirmed and wired into ``data/treasury_futures_ctd.BLOOMBERG_CTD_FIELD_MAP``:

- ``PARSEKYABLE_DES`` -> ``contract_symbol``, resolving the desk-active
  contract alias to the actual delivery month (``TUA Comdty`` -> ``TUZ6``,
  ``FVA`` -> ``FVZ6``, ``TYA`` -> ``TYZ6``, ``USA`` -> ``USZ6``), carrying the
  yellow key the stage-two request already prices on.
- ``FUT_CTD_ISIN`` -> ``ctd_identifier`` (the canonical identifier).
- ``FUT_CTD_CPN`` -> ``ctd_coupon_percent``.
- ``FUT_CTD_MTY`` -> ``ctd_maturity_date``.
- ``FUT_CNVS_FACTOR`` -> ``conversion_factor``.
- ``FUT_DLV_DT_LAST`` -> ``last_delivery_date``.

Confirmed to return a value and wired into
``BLOOMBERG_CTD_DISPLAY_FIELD_MAP`` as display-only -- never the identifier a
calculation keys on: ``FUT_CTD_CUSIP``, ``FUT_CTD_TICKER``.

The live values behind each are recorded in ``data/treasury_futures_ctd``'s
own module docstring, as evidence of the mnemonic rather than as data.

**Superseded candidates.** The original list carried several candidates per
destination so one run could be conclusive: ``FUT_ACT_DEF_GEN_TICKER``,
``PARSEKYABLE_DES``, ``CTD_ISIN``, ``CTD_CUSIP``, ``CTD_CPN``, ``CTD_MTY``,
``FUT_CTD_MATURITY``, ``CTD_CONVERSION_FACTOR``, ``FUT_CTD_CNVS_FACTOR``,
``LAST_DELIVERY_DT``, ``FUT_LAST_DLV_DT``. A confirmed mnemonic was found for
every required field, so none is wired. They are recorded as **superseded,
not as confirmed rejections** -- no per-field ``BAD_FLD`` evidence was
reported for them individually, so nothing here claims any of them is
invalid. Re-adding one still needs its own confirmation.

**This script still confirms nothing by itself.** It reports what Bloomberg
returns; wiring a mapping is a separate, reviewed change. Pass ``--fields``
explicitly to probe a genuinely new candidate.

**Running it.** On a Bloomberg-networked workstation with ``blpapi``
installed and the Terminal logged in::

    python tools/bloomberg_treasury_futures_ctd_probe.py --contract ZN --fields FUT_CTD_ISIN
    python tools/bloomberg_treasury_futures_ctd_probe.py --security "TYZ6 Comdty" \
        --fields FUT_CNVS_FACTOR

It asks ``//blp/apiflds`` whether each mnemonic exists at all, then
``//blp/refdata`` whether it returns a value, and prints ``returned`` /
``absent`` / ``field_exception`` per field. It reuses
``bloomberg_dapi_probe``'s session plumbing and
``bloomberg_input_sourcing_probe.sanitize_external_text``; it adds no second
session implementation and writes nothing to disk.
"""

from __future__ import annotations

import argparse
import sys

from bloomberg_dapi_probe import describe_fields, probe_fields
from bloomberg_input_sourcing_probe import sanitize_external_text

from shiori_pricing_lab.data.treasury_futures_ctd import (
    BLOOMBERG_CTD_DISPLAY_FIELD_MAP,
    BLOOMBERG_CTD_FIELD_MAP,
    REQUIRED_BLOOMBERG_CTD_FIELDS,
    bloomberg_active_contract,
    unresolved_bloomberg_ctd_fields,
)
from shiori_pricing_lab.pricing.treasury_futures_contract import (
    SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES,
)

# Candidate Bloomberg field mnemonics still to be probed. Empty: every
# required CTD field is resolved (see the module docstring's resolution
# history). Add an entry here only for a genuinely new, not-yet-probed
# candidate -- the confirmed mnemonics live in
# `data/treasury_futures_ctd`, not here, and re-probing them proves nothing.
_CANDIDATE_CTD_FIELDS: dict[str, str] = {}


def default_security(contract_code: str) -> str:
    """Bloomberg desk-active contract alias for one supported contract code.

    Defers to `data/treasury_futures_ctd`'s own confirmed alias table rather
    than keeping a second copy: the aliases are production data now, and two
    copies could disagree about which contract this probe is reporting on.
    """

    return bloomberg_active_contract(contract_code)


def _print_field_dictionary(fields: list[str]) -> None:
    print("Bloomberg field dictionary (//blp/apiflds) -- does the mnemonic exist at all?")
    print(f"{'FIELD':<26}{'STATUS':<14}{'DATATYPE':<12}DESCRIPTION")
    print("-" * 100)
    try:
        descriptions = describe_fields(fields)
    except ImportError as exc:
        print(f"  skipped: blpapi is not installed ({exc})")
        return
    except RuntimeError as exc:
        print(f"  skipped: {sanitize_external_text(str(exc))}")
        return
    for description in descriptions:
        detail = description.description or description.detail or ""
        print(
            f"{description.field:<26}{description.status:<14}"
            f"{(description.datatype or ''):<12}{sanitize_external_text(detail) or ''}"
        )


def _print_probe(security: str, fields: list[str]) -> int:
    print()
    print(f"Reference data (//blp/refdata) for {security!r}")
    print(f"{'FIELD':<26}{'STATUS':<16}{'DESTINATION':<22}VALUE / DETAIL")
    print("-" * 100)
    try:
        results = probe_fields(security, fields)
    except ImportError as exc:
        print(
            "error: blpapi is not installed -- run this on a Bloomberg-networked "
            f"workstation with Bloomberg's official blpapi package installed ({exc})",
            file=sys.stderr,
        )
        return 2
    except RuntimeError as exc:
        print(f"error: {sanitize_external_text(str(exc))}", file=sys.stderr)
        return 1

    for result in results:
        detail = result.value if result.status == "returned" else (result.detail or "")
        destination = _CANDIDATE_CTD_FIELDS.get(result.field, "(caller-supplied)")
        print(
            f"{result.field:<26}{result.status:<16}{destination:<22}"
            f"{sanitize_external_text(detail) or ''}"
        )
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Probe candidate Bloomberg field mnemonics for Treasury futures CTD "
            "metadata (Issue #190). Confirms nothing on its own -- it reports what "
            "Bloomberg returns so a mnemonic can be confirmed or rejected."
        )
    )
    parser.add_argument(
        "--contract",
        default=",".join(SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES),
        help=(
            "Comma-separated Shiori contract codes to probe using their Bloomberg "
            "generic front-contract ticker (default: all supported)"
        ),
    )
    parser.add_argument(
        "--security",
        default=None,
        help=(
            "Probe this Bloomberg security string verbatim (e.g. \"TYZ6 Comdty\") "
            "instead of the generic front contract. Overrides --contract."
        ),
    )
    parser.add_argument(
        "--fields",
        default=None,
        help="Comma-separated Bloomberg field mnemonics (default: the candidate list)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    fields = (
        [field.strip() for field in args.fields.split(",") if field.strip()]
        if args.fields
        else list(_CANDIDATE_CTD_FIELDS)
    )
    if not fields:
        print("error: no fields to probe", file=sys.stderr)
        return 2

    if args.security:
        securities = [args.security]
    else:
        codes = [code.strip().upper() for code in args.contract.split(",") if code.strip()]
        try:
            securities = [default_security(code) for code in codes]
        except ValueError as exc:  # includes TreasuryFuturesCTDError
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if not securities:
            print("error: no contract selected", file=sys.stderr)
            return 2

    print("Treasury futures CTD field probe (Issue #190)")
    print(f"Required CTD fields:   {', '.join(REQUIRED_BLOOMBERG_CTD_FIELDS)}")
    for logical_field, mnemonic in sorted(BLOOMBERG_CTD_FIELD_MAP.items()):
        print(f"  confirmed: {logical_field:<22}{mnemonic}")
    for logical_field, mnemonic in sorted(BLOOMBERG_CTD_DISPLAY_FIELD_MAP.items()):
        print(f"  confirmed: {logical_field:<22}{mnemonic}  (display only)")
    print(f"Still unresolved:      {', '.join(unresolved_bloomberg_ctd_fields()) or 'none'}")
    print("Every mnemonic probed below is an UNCONFIRMED candidate.")
    print()

    _print_field_dictionary(fields)

    exit_code = 0
    for security in securities:
        result = _print_probe(security, fields)
        exit_code = exit_code or result
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
