"""``BLIBenchmarkQuote``: one benchmark premium observation (Issue #98 PR A).

Scope, per the authorized decisions in Issue #94 comment 4964617762 and the
implementation slice authorized in Issue #98 comment 4964619458: a frozen,
explicit, single-quote-side benchmark source record for the standalone
bond-option pricing workflow. This module records **one already-observed**
external benchmark premium (Bloomberg today, per Phase 1 decision #3; a
``VENDOR`` source is a controlled-vocabulary placeholder only, not an
implemented adapter). It performs no comparison against a model fair
premium, no residual computation, no tolerance evaluation, and no
calibration -- those are explicitly deferred to a later, separately
authorized comparison slice ("PR B").

**One record, one side (decision #1).** A ``BLIBenchmarkQuote`` holds
exactly one explicit ``BLIBenchmarkQuoteSide``. Capturing ``BID``, ``MID``,
and ``OFFER`` for the same benchmark means constructing three separate
records -- there is no default side and no blending across sides.

**Local enums, not reused (decisions #2/#3).** ``BLIBenchmarkQuoteSide`` is
a new, benchmark-scoped enum -- it deliberately does not reuse
``products.enums.TreasuryFTPQuoteSide``, whose docstring and existing usage
are specific to the Treasury FTP deposit-rate matrix (docs/18 §2.4/§5), a
different market-data source with its own quote-side policy. Reusing it
here would silently assert an equivalence nobody has approved.
``BLIBenchmarkSourceType`` is likewise new and local to this module.

**Provenance is first-class (decision #4).** ``source_as_of`` (the
benchmark's own observation/business-date timestamp) and ``retrieved_at``
(the local refresh/retrieval time) are independent, both-mandatory fields.
Neither is defaulted from the other, from a system clock, or from any other
field on this dataclass. Each is validated with the same as-of/date-time
format parser already used by ``BLIMarketDataSnapshot``'s coherence checks
(``data._validation._parse_as_of_calendar_date``, see
``data/bli_standalone_option_request.py``) -- used here purely for format
validation of each field independently; this module draws no comparability,
staleness, or ordering conclusion between the two timestamps, or between
either timestamp and any other object's date.

**Both premium units are mandatory and independent (decision #5).**
``premium_per_100`` and ``total_premium`` are both required, both must be
finite and non-negative (zero is a valid premium), and both are stored
exactly as supplied. This module never derives one from the other and never
checks their relationship against a notional -- that reconciliation is
explicitly deferred to the later comparison slice.

**No quality/status vocabulary here (decision #6).** There is no
pass/warning/fail/non-comparable field on this record. Any known caveat
about the observation belongs in the free-text ``notes`` field; the
export/retrieval provenance belongs in ``source_reference``.

**No new persistent identity (decision #7).** This record links to the rest
of the workflow only via identifiers that already exist elsewhere:
``product_id`` (a ``PricingResult``/``BondOption`` identifier),
``snapshot_id`` (a ``BLIMarketDataSnapshot`` identifier), and
``underlying_id`` (an ISIN or other already-existing stable underlying
identifier). No quote/version/persistent request id is introduced.

**No tolerance logic here (decision #8).** The Annex A §A.13.4 tolerance
bands are pinned at the *methodology* level (``< 2%`` = pass, ``2%-5%`` =
warning inclusive of both boundaries, ``> 5%`` = fail) but this module
contains no comparison, threshold, or status computation of any kind --
that belongs entirely to the later comparison slice.

**Hard non-goals (Issue #98 PR A scope cap):** no residual math, no
near-zero denominator rule, no timestamp comparability policy, no
quote-side mismatch policy, no benchmark-vs-model comparison, no
calibration, no Bloomberg adapter/API call, no persistence/versioning, no
UI, no client-quote field, and no change to ``PricingResult``, the pricing
engine, or any request/builder/snapshot schema. This module imports from
``data._validation`` and ``products.enums`` only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from shiori_pricing_lab.data._validation import (
    _parse_as_of_calendar_date,
    _require_finite_number,
    _require_non_blank,
)
from shiori_pricing_lab.products.enums import Currency, coerce_enum


class BLIBenchmarkQuoteSide(StrEnum):
    """Quote side of one benchmark premium observation.

    Local to the benchmark-source contract (Issue #94 decision #2) --
    deliberately distinct from ``products.enums.TreasuryFTPQuoteSide``,
    which is scoped to the Treasury FTP deposit-rate matrix and carries its
    own, unrelated quote-side policy. There is no default member and no
    dataclass default: every ``BLIBenchmarkQuote`` must state its side
    explicitly.
    """

    BID = "BID"
    MID = "MID"
    OFFER = "OFFER"


class BLIBenchmarkSourceType(StrEnum):
    """Source type of a benchmark premium observation (Issue #94 decision #3).

    ``BLOOMBERG`` is the Phase 1 official benchmark source. ``VENDOR`` is
    reserved controlled vocabulary for a future non-Bloomberg vendor source;
    no vendor adapter exists yet. Neither member implies a specific
    Bloomberg function, field, screen, or export route -- none may be
    guessed (Issue #94 decision #1 note, Issue #98 non-goals).
    """

    BLOOMBERG = "BLOOMBERG"
    VENDOR = "VENDOR"


@dataclass(frozen=True)
class BLIBenchmarkQuote:
    """One explicit-side benchmark premium observation with its provenance.

    Every mandatory string field must be non-blank. ``source_as_of`` and
    ``retrieved_at`` are validated independently for as-of/date-time format
    only -- this dataclass draws no freshness, staleness, or ordering
    conclusion from either value or from comparing the two. Both premium
    fields are mandatory, finite, and non-negative, and are preserved
    verbatim with no derivation, conversion, or notional-consistency check
    between them. ``notes`` is optional; when supplied it must be
    non-blank.
    """

    benchmark_id: str
    source_type: BLIBenchmarkSourceType
    source_system: str
    source_as_of: str
    retrieved_at: str
    quote_side: BLIBenchmarkQuoteSide
    premium_per_100: float
    total_premium: float
    currency: Currency
    product_id: str
    snapshot_id: str
    underlying_id: str
    source_reference: str
    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_type",
            coerce_enum(self.source_type, BLIBenchmarkSourceType, "source_type"),
        )
        object.__setattr__(
            self, "quote_side", coerce_enum(self.quote_side, BLIBenchmarkQuoteSide, "quote_side")
        )
        object.__setattr__(self, "currency", coerce_enum(self.currency, Currency, "currency"))

        _require_non_blank(self.benchmark_id, "benchmark_id")
        _require_non_blank(self.source_system, "source_system")
        _require_non_blank(self.product_id, "product_id")
        _require_non_blank(self.snapshot_id, "snapshot_id")
        _require_non_blank(self.underlying_id, "underlying_id")
        _require_non_blank(self.source_reference, "source_reference")

        # source_as_of and retrieved_at are validated independently for
        # as-of/date-time format only (same parser as
        # BLIMarketDataSnapshot's coherence checks) -- neither is compared
        # against the other or against any other object's date here.
        _parse_as_of_calendar_date(self.source_as_of, "source_as_of")
        _parse_as_of_calendar_date(self.retrieved_at, "retrieved_at")

        _require_finite_number(self.premium_per_100, "premium_per_100")
        if self.premium_per_100 < 0:
            raise ValueError(
                f"premium_per_100 must be non-negative, got {self.premium_per_100}"
            )

        _require_finite_number(self.total_premium, "total_premium")
        if self.total_premium < 0:
            raise ValueError(
                f"total_premium must be non-negative, got {self.total_premium}"
            )

        if self.notes is not None:
            _require_non_blank(self.notes, "notes")
