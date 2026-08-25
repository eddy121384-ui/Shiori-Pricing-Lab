"""Bloomberg CTD / conversion-factor field-discovery probe for Treasury futures (Issue #190).

Standalone diagnostic CLI -- **not** part of the production pricing or
workbench path, and never imported by either. It exists to answer one
question on a real Bloomberg workstation, empirically:

    which Bloomberg field mnemonic actually returns each of the six facts
    ``data/treasury_futures_ctd`` needs for a Treasury futures contract?

Those six are ``contract_symbol``, ``ctd_identifier``, ``ctd_coupon_percent``,
``ctd_maturity_date``, ``conversion_factor`` and ``last_delivery_date``
(``REQUIRED_BLOOMBERG_CTD_FIELDS``). Today **none of them has a confirmed
mnemonic**, so ``BLOOMBERG_CTD_FIELD_MAP`` is empty and Shiori's automatic
CTD path fails closed. Issue #190 and AGENTS.md rule 7 both forbid closing
that gap by guessing, so this script proves or disproves a candidate instead.
It changes no production mapping by itself and writes nothing to disk.

**Every mnemonic below is an UNCONFIRMED candidate.** None has been seen to
return a value from a live DAPI response. The point of running this is to
find out which ones do; a candidate that comes back ``absent`` or
``field_exception`` is a *result*, not a failure of the run.

**What it does.** For each candidate it asks Bloomberg twice:

1. ``//blp/apiflds`` -- does Bloomberg's own field dictionary even define
   this mnemonic, and what does it say the field is? (``describe_fields``)
2. ``//blp/refdata`` -- does it return a value for this actual contract?
   (``probe_fields``)

Both come from ``tools/bloomberg_dapi_probe.py``; this script adds no second
session, request or event-loop implementation. It also reuses
``bloomberg_input_sourcing_probe.sanitize_external_text`` so Bloomberg-
authored text is scrubbed of workstation/session detail before it is printed.

**The security probed.** Bloomberg's generic front-contract ticker for each
root -- ``TU1 Comdty`` (ZT), ``FV1 Comdty`` (ZF), ``TY1 Comdty`` (ZN),
``US1 Comdty`` (ZB) -- is the default, and the exact string is printed before
the request so there is no doubt what was asked. That default is a
convenience, not an assertion: pass ``--security`` to probe an explicit
delivery-month ticker (e.g. ``"TYZ6 Comdty"``) verbatim instead, and the
script sends exactly what it was given without appending or rewriting a
yellow key.

**Running it.** On a Bloomberg-networked Windows workstation, with
Bloomberg's official ``blpapi`` package installed and the Terminal logged
in::

    python tools/bloomberg_treasury_futures_ctd_probe.py --contract ZN
    python tools/bloomberg_treasury_futures_ctd_probe.py --contract ZT,ZF,ZN,ZB
    python tools/bloomberg_treasury_futures_ctd_probe.py --security "TYZ6 Comdty"
    python tools/bloomberg_treasury_futures_ctd_probe.py --contract ZN --fields FUT_CTD_CPN

Paste the output into Issue #190. A candidate that returns a plausible value
for all four contracts is then wired into
``data/treasury_futures_ctd.BLOOMBERG_CTD_FIELD_MAP`` with its evidence
recorded there -- exactly the way ``data/bloomberg_bond_quote``'s own field
maps record theirs -- in a separate, reviewed change. Nothing here does that
wiring automatically.
"""

from __future__ import annotations

import argparse
import sys

from bloomberg_dapi_probe import describe_fields, probe_fields
from bloomberg_input_sourcing_probe import sanitize_external_text

from shiori_pricing_lab.data.treasury_futures_ctd import (
    BLOOMBERG_CTD_FIELD_MAP,
    REQUIRED_BLOOMBERG_CTD_FIELDS,
    unresolved_bloomberg_ctd_fields,
)
from shiori_pricing_lab.pricing.treasury_futures_contract import (
    SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES,
    get_contract,
)

# Bloomberg's generic front-contract suffix and the ticker root per Shiori
# contract code. Only used to build the default --security string, which is
# always printed before the request is sent, and deliberately kept here
# rather than in `pricing/treasury_futures_contract`: that package is guarded
# against vendor plumbing, and these roots are this probe's convenience, not
# a confirmed field mapping.
_GENERIC_FRONT_CONTRACT_SUFFIX = "1 Comdty"
_BLOOMBERG_TICKER_ROOTS = {"ZT": "TU", "ZF": "FV", "ZN": "TY", "ZB": "US"}

# Candidate Bloomberg field mnemonics -- UNCONFIRMED against any live DAPI
# response, every single one. Grouped by the `treasury_futures_ctd` field
# each would feed if it turns out to work. Several candidates per destination
# on purpose: the naming families Bloomberg uses for futures reference data
# are not consistent, and probing four cheap candidates in one request is
# what makes a single workstation run conclusive instead of the first of
# four round trips. Remove a candidate only once it is confirmed rejected,
# and record that rejection here when you do.
_CANDIDATE_CTD_FIELDS: dict[str, str] = {
    # contract_symbol -- which delivery month the generic ticker resolves to
    "FUT_CUR_GEN_TICKER": "contract_symbol",
    "FUT_ACT_DEF_GEN_TICKER": "contract_symbol",
    "PARSEKYABLE_DES": "contract_symbol",
    # ctd_identifier
    "FUT_CTD_ISIN": "ctd_identifier",
    "FUT_CTD_CUSIP": "ctd_identifier",
    "CTD_ISIN": "ctd_identifier",
    "CTD_CUSIP": "ctd_identifier",
    "FUT_CTD_TICKER": "ctd_identifier",
    # ctd_coupon_percent
    "FUT_CTD_CPN": "ctd_coupon_percent",
    "CTD_CPN": "ctd_coupon_percent",
    # ctd_maturity_date
    "FUT_CTD_MTY": "ctd_maturity_date",
    "CTD_MTY": "ctd_maturity_date",
    "FUT_CTD_MATURITY": "ctd_maturity_date",
    # conversion_factor
    "FUT_CNVS_FACTOR": "conversion_factor",
    "CTD_CONVERSION_FACTOR": "conversion_factor",
    "FUT_CTD_CNVS_FACTOR": "conversion_factor",
    # last_delivery_date
    "FUT_DLV_DT_LAST": "last_delivery_date",
    "LAST_DELIVERY_DT": "last_delivery_date",
    "FUT_LAST_DLV_DT": "last_delivery_date",
}


def default_security(contract_code: str) -> str:
    """Bloomberg generic front-contract ticker for one supported contract code."""

    contract = get_contract(contract_code)  # rejects an unsupported code
    root = _BLOOMBERG_TICKER_ROOTS.get(contract.code)
    if root is None:
        raise ValueError(
            f"no Bloomberg ticker root recorded for {contract.code} -- pass --security "
            "with the exact ticker to probe instead"
        )
    return f"{root}{_GENERIC_FRONT_CONTRACT_SUFFIX}"


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
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if not securities:
            print("error: no contract selected", file=sys.stderr)
            return 2

    print("Treasury futures CTD field probe (Issue #190)")
    print(f"Required CTD fields:   {', '.join(REQUIRED_BLOOMBERG_CTD_FIELDS)}")
    print(f"Confirmed today:       {sorted(BLOOMBERG_CTD_FIELD_MAP) or 'none'}")
    print(f"Still unresolved:      {', '.join(unresolved_bloomberg_ctd_fields()) or 'none'}")
    print("Every mnemonic below is an UNCONFIRMED candidate.")
    print()

    _print_field_dictionary(fields)

    exit_code = 0
    for security in securities:
        result = _print_probe(security, fields)
        exit_code = exit_code or result
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
