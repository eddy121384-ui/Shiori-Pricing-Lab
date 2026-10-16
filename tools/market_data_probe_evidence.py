"""Shared report vocabulary for the Market Data Reconnection Phase 1 probes.

One small module, and deliberately the *only* thing the Bloomberg and LSEG
probes share. Both answer the same ten questions about the same target
coordinate, so both must label their answers with the same evidence
vocabulary -- otherwise a reader comparing the two reports is comparing two
different languages. Everything vendor-specific stays in each probe.

Nothing here talks to a vendor, imports a vendor library, or reads a clock
the caller cannot replace.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum

from bloomberg_input_sourcing_probe import AppliedOverride, sanitize_external_text

__all__ = [
    "AppliedOverride",
    "AuthorizationStatus",
    "EvidenceClass",
    "ReportItem",
    "items_to_dict",
    "sanitize_tree",
    "utc_now",
]


class EvidenceClass(StrEnum):
    """Which kind of thing actually establishes one reported answer.

    Kept as four distinct members rather than a boolean because "the
    operator said so" and "the vendor said so" are different facts, and a
    report that merges them is exactly how an unverified ticker becomes a
    production field mapping.
    """

    #: The vendor's own response establishes it (Bloomberg DAPI or LSEG
    #: Workspace alike -- the vocabulary is deliberately vendor-neutral so
    #: the two reports are directly comparable).
    VENDOR_RETURNED = "VENDOR_RETURNED"
    #: The operator asserted it on the command line. Recorded verbatim, and
    #: **not** vendor evidence.
    OPERATOR_DECLARED = "OPERATOR_DECLARED"
    #: This run observed it locally (the clock, an opened service/session).
    PROBE_OBSERVED = "PROBE_OBSERVED"
    #: Nothing established it. Never filled in.
    NOT_ESTABLISHED = "NOT_ESTABLISHED"


class AuthorizationStatus(StrEnum):
    """How far a run actually got in establishing entitlement.

    Neither DAPI nor the LSEG library exposes a blanket "is this account
    entitled" call in the surface these probes use, so entitlement is only
    ever *evidenced* -- by which session/service the vendor agreed to open,
    and by each field's own response. None of these members claims account
    entitlement.
    """

    #: No session/service opened -- nothing about entitlement is known.
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    #: A session opened; no field was probed, so only session-level
    #: authorisation is evidenced.
    SERVICE_LEVEL_ONLY = "SERVICE_LEVEL_ONLY"
    #: A session opened and at least one field probe produced the vendor's
    #: own per-field outcome (a value, an ``absent``, or a field exception).
    FIELD_LEVEL_OBSERVED = "FIELD_LEVEL_OBSERVED"


@dataclass(frozen=True)
class ReportItem:
    """One of the phase's report items, with the class of its evidence."""

    item: str
    evidence_class: EvidenceClass
    detail: str


def items_to_dict(items: tuple[ReportItem, ...]) -> list[dict]:
    return [
        {
            "item": item.item,
            "evidence_class": item.evidence_class.value,
            "detail": item.detail,
        }
        for item in items
    ]


def utc_now() -> str:
    """This run's own clock reading, as an explicit ISO-8601 UTC instant.

    Module-level and monkeypatchable, the same seam
    ``bloomberg_vcub_field_hunter._utc_now`` uses, so a test can pin the
    timestamp instead of reading the system clock. This is a *probe
    observation* -- when this run happened -- and is never presented as the
    market data's own date.
    """

    return datetime.now(UTC).isoformat(timespec="milliseconds")


def sanitize_tree(value, overrides: tuple[AppliedOverride, ...] = ()) -> object:
    """Recursively sanitize every string in a nested report structure.

    The no-leak guard is the one this repository already built and proved on
    a live workstation
    (:func:`bloomberg_input_sourcing_probe.sanitize_external_text`): it
    removes the override values a run actually sent, credential-shaped
    ``key=value`` pairs, filesystem paths and ``host:port`` pairs. It is
    reused here rather than reimplemented, and it is applied to *both*
    vendors' text -- an LSEG session error can quote the operator's own
    inputs just as a Bloomberg one can.
    """

    if isinstance(value, str):
        return sanitize_external_text(value, overrides)
    if isinstance(value, dict):
        return {key: sanitize_tree(item, overrides) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_tree(item, overrides) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return sanitize_tree(asdict(value), overrides)
    return value
