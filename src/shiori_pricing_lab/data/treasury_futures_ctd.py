"""Current cheapest-to-deliver metadata for a Treasury futures contract (Issue #190).

Scope: the *sourcing* half of the desk's futures <-> CTD implied-yield
converter -- the validated record of which cash Treasury is currently
cheapest to deliver into one futures contract, and where that record came
from. No pricing, no yield, no schedule, no quote parsing lives here.

**Automatic sourcing is deliberately not wired yet, and fails closed.**
Issue #190's RED data contract is that current CTD metadata must come from a
verifiable path, not from a synthetic cache like PR #9's embedded
``ZNM6``/``ZBM6`` records. Shiori already has the Bloomberg Desktop API
infrastructure for that (``data/bloomberg_bond_quote.py``,
``tools/bloomberg_dapi_probe.py``), but it has **no confirmed field mnemonic
for any of the five CTD fields below** -- and AGENTS.md rule 7 plus Issue
#190 both forbid guessing one. So :data:`BLOOMBERG_CTD_FIELD_MAP` is empty,
:func:`load_bloomberg_ctd_metadata` raises rather than returning anything,
and the error names the exact unresolved fields and the probe that resolves
them. It is never a silent fallback to manual data, and there is no
synthetic contract cache in this repository.

``tools/bloomberg_treasury_futures_ctd_probe.py`` is the probe. Once Eddy
runs it on a Bloomberg-networked workstation and confirms which candidate
mnemonic actually returns each value, the confirmed mnemonics get wired into
:data:`BLOOMBERG_CTD_FIELD_MAP` (with the returned evidence recorded here,
exactly as ``bloomberg_bond_quote``'s own field maps record theirs) and this
module's automatic path opens. Not before.

**Manual entry is a first-class debug/fallback path, and is always visibly
unconfirmed.** A trader who has the CTD, coupon, maturity, conversion factor
and last delivery date in front of them can still use the converter today --
but every record built that way carries
``TreasuryFuturesCTDSource.MANUAL_UNCONFIRMED`` and its own operator-supplied
``as_of``, and every consumer (the API response and the desk panel) renders
that status next to the answer. Manual entry does not satisfy Issue #190's
automatic-data acceptance criterion and never claims to.

**No clock is read here.** ``as_of`` is always explicit and caller-supplied;
this module never substitutes "now" for a missing timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from shiori_pricing_lab.data._validation import (
    _parse_iso_date,
    _require_finite_number,
    _require_non_blank,
)


class TreasuryFuturesCTDSource(StrEnum):
    """Where one CTD record came from, and whether it is confirmed.

    ``BLOOMBERG_DAPI`` is reserved for a record built from confirmed
    Bloomberg field mnemonics. Nothing produces it yet -- see the module
    docstring.
    """

    BLOOMBERG_DAPI = "BLOOMBERG_DAPI"
    MANUAL_UNCONFIRMED = "MANUAL_UNCONFIRMED"


#: Sources whose data is confirmed to be current automatic market data. A
#: record whose source is not in here must be shown as unconfirmed wherever
#: its numbers are shown (Issue #190: "stale/unconfirmed source is visibly
#: flagged rather than silently treated as current").
CONFIRMED_TREASURY_FUTURES_CTD_SOURCES = frozenset({TreasuryFuturesCTDSource.BLOOMBERG_DAPI})

#: The five CTD facts the converter cannot compute without, plus the contract
#: symbol that identifies which delivery month they belong to. Each needs a
#: confirmed Bloomberg field mnemonic before the automatic path can open.
REQUIRED_BLOOMBERG_CTD_FIELDS = (
    "contract_symbol",
    "ctd_identifier",
    "ctd_coupon_percent",
    "ctd_maturity_date",
    "conversion_factor",
    "last_delivery_date",
)

#: Confirmed logical field -> Bloomberg mnemonic. Empty on purpose: no
#: candidate has been confirmed against a live Bloomberg workstation yet, and
#: an unconfirmed mnemonic is never wired in here. See the module docstring
#: and ``tools/bloomberg_treasury_futures_ctd_probe.py``.
BLOOMBERG_CTD_FIELD_MAP: dict[str, str] = {}


class TreasuryFuturesCTDError(ValueError):
    """A CTD metadata record is missing a required field or is internally invalid."""


class TreasuryFuturesCTDFieldsUnconfirmedError(RuntimeError):
    """Automatic Bloomberg CTD sourcing was asked for before its fields were confirmed."""


@dataclass(frozen=True)
class TreasuryFuturesCTD:
    """The current CTD for one futures contract, as the converter needs it.

    ``ctd_coupon_percent`` is a percent per annum (``4.25`` means 4.25%),
    matching how a coupon is quoted on a Bloomberg screen and in this
    repository's existing bond reference data. ``conversion_factor`` is the
    exchange's published factor for this CTD into this contract.
    ``last_delivery_date`` is the futures contract's last delivery day, which
    Issue #190 fixes as the settlement date of the implied-yield calculation.
    """

    contract_code: str
    contract_symbol: str
    ctd_identifier: str
    ctd_coupon_percent: float
    ctd_maturity_date: date
    conversion_factor: float
    last_delivery_date: date
    source: TreasuryFuturesCTDSource
    as_of: str

    @property
    def is_confirmed_source(self) -> bool:
        """Whether these numbers came from a confirmed automatic market-data path."""

        return self.source in CONFIRMED_TREASURY_FUTURES_CTD_SOURCES

    def as_display_payload(self) -> dict[str, object]:
        """The small print the desk panel shows under every answer."""

        return {
            "contract_code": self.contract_code,
            "contract_symbol": self.contract_symbol,
            "ctd_identifier": self.ctd_identifier,
            "ctd_coupon_percent": self.ctd_coupon_percent,
            "ctd_maturity_date": self.ctd_maturity_date.isoformat(),
            "conversion_factor": self.conversion_factor,
            "last_delivery_date": self.last_delivery_date.isoformat(),
            "source": str(self.source),
            "as_of": self.as_of,
            "is_confirmed_source": self.is_confirmed_source,
        }


def unresolved_bloomberg_ctd_fields() -> tuple[str, ...]:
    """Required CTD fields that still have no confirmed Bloomberg mnemonic."""

    return tuple(
        field for field in REQUIRED_BLOOMBERG_CTD_FIELDS if field not in BLOOMBERG_CTD_FIELD_MAP
    )


def load_bloomberg_ctd_metadata(contract_code: str) -> TreasuryFuturesCTD:
    """Load current CTD metadata for ``contract_code`` from Bloomberg DAPI.

    Raises :class:`TreasuryFuturesCTDFieldsUnconfirmedError` while any
    required field has no confirmed mnemonic -- which is every field today.
    It never falls back to manual, cached, or synthetic data.
    """

    unresolved = unresolved_bloomberg_ctd_fields()
    if unresolved:
        raise TreasuryFuturesCTDFieldsUnconfirmedError(
            "automatic Bloomberg CTD sourcing is not available: no confirmed Bloomberg "
            f"field mnemonic for {', '.join(unresolved)}. Run "
            "tools/bloomberg_treasury_futures_ctd_probe.py on a Bloomberg-networked "
            "workstation to confirm the candidate mnemonics, then wire the confirmed "
            "ones into data/treasury_futures_ctd.BLOOMBERG_CTD_FIELD_MAP. Until then use "
            "the manual CTD entry path, which is reported as MANUAL_UNCONFIRMED."
        )
    raise TreasuryFuturesCTDFieldsUnconfirmedError(  # pragma: no cover - unreachable today
        f"Bloomberg CTD field mnemonics are confirmed for {contract_code} but the DAPI "
        "request path has not been implemented yet"
    )


def treasury_futures_ctd_from_manual_entry(payload: dict[str, object]) -> TreasuryFuturesCTD:
    """Build a validated, explicitly-unconfirmed CTD record from operator input.

    Every field is required: a converter answer built on a missing conversion
    factor, maturity or delivery date would be wrong rather than approximate,
    so this fails closed instead of defaulting anything.
    """

    if not isinstance(payload, dict):
        raise TreasuryFuturesCTDError("CTD metadata must be a JSON object")

    missing = [
        field
        for field in (*REQUIRED_BLOOMBERG_CTD_FIELDS, "contract_code", "as_of")
        if payload.get(field) is None
    ]
    if missing:
        raise TreasuryFuturesCTDError(f"CTD metadata is missing required field(s): {missing}")

    try:
        for field in ("contract_code", "contract_symbol", "ctd_identifier", "as_of"):
            _require_non_blank(payload[field], field)
        _require_finite_number(payload["ctd_coupon_percent"], "ctd_coupon_percent")
        _require_finite_number(payload["conversion_factor"], "conversion_factor")
        ctd_maturity_date = _parse_iso_date(payload["ctd_maturity_date"], "ctd_maturity_date")
        last_delivery_date = _parse_iso_date(payload["last_delivery_date"], "last_delivery_date")
    except ValueError as exc:
        raise TreasuryFuturesCTDError(str(exc)) from exc

    coupon_percent = float(payload["ctd_coupon_percent"])  # type: ignore[arg-type]
    conversion_factor = float(payload["conversion_factor"])  # type: ignore[arg-type]
    if coupon_percent < 0:
        raise TreasuryFuturesCTDError(
            f"ctd_coupon_percent must not be negative, got {coupon_percent}"
        )
    if conversion_factor <= 0:
        raise TreasuryFuturesCTDError(
            f"conversion_factor must be positive, got {conversion_factor}"
        )
    if last_delivery_date >= ctd_maturity_date:
        raise TreasuryFuturesCTDError(
            f"last_delivery_date {last_delivery_date.isoformat()} must be before the CTD's "
            f"maturity {ctd_maturity_date.isoformat()}"
        )

    return TreasuryFuturesCTD(
        contract_code=str(payload["contract_code"]).strip().upper(),
        contract_symbol=str(payload["contract_symbol"]).strip(),
        ctd_identifier=str(payload["ctd_identifier"]).strip().upper(),
        ctd_coupon_percent=coupon_percent,
        ctd_maturity_date=ctd_maturity_date,
        conversion_factor=conversion_factor,
        last_delivery_date=last_delivery_date,
        source=TreasuryFuturesCTDSource.MANUAL_UNCONFIRMED,
        as_of=str(payload["as_of"]).strip(),
    )
