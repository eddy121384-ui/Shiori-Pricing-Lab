"""``BLIMarketDataSnapshot``: BLI-scoped market data (docs/23, MarketDataSnapshot
schema preflight -- implementation slice).

Scope, per ``docs/23_bli_market_data_snapshot_schema_preflight.md``: a
frozen, explicit, point-in-time container of market observations and curve
inputs for one BLI valuation context. It answers "what market data did we
use for this valuation context?" only -- it does not answer "what product
did we trade" (``BondOption`` / ``DepositLeg`` / ``BondLinkedStructuredProduct``),
"what did the issuer promise" (``BondReferenceData``), "is the bond
eligible" (``resolve_bond_reference_data`` / ``is_mvp_pricing_eligible``),
or "what is the PV" (a future ``PricingResult``).

**Naming and location (docs/23 §3):** this module lives inside the existing
``data/`` package (``AGENTS.md`` rule 2 already designates it as the one
place market-data code may live), and the class is named
``BLIMarketDataSnapshot`` -- deliberately distinct from the existing
vanilla-rates-core ``MarketDataSnapshot`` (``data/snapshot.py``), which is a
structurally unrelated DataFrame-of-rates-points object. Neither class is
modified by the other.

**What this module does not do (docs/23 §17):** no MVP input bundle,
bundle builder, pricing engine, payoff skeleton, cash-flow generation,
schedule engine, yield-to-price calculation, curve interpolation,
volatility surface, credit spread model, Treasury FTP parser, ingestion,
Bloomberg/API connector, QuantLib adapter, or UI is implemented here.
``BondOption``, ``DepositLeg``, ``BondLinkedStructuredProduct``,
``BondReferenceData``, and ``resolve_bond_reference_data`` are all
unmodified. Issue #38 remains open.

Curve data is a **collection of tenor/rate rows**, not one row per curve
(docs/23 §12, Codex P2 review of PR #62): a normal curve has several rows
sharing the same ``currency`` + ``curve_purpose`` across different tenors,
which is expected and valid. Duplicate/conflicting detection is keyed at
the curve-node level (``curve_id`` + ``tenor``), and an ambiguous set of
different ``curve_id``s claiming the same ``currency`` + ``curve_purpose``
without an explicit mapping rule is rejected (see ``_validate_curve_points``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from shiori_pricing_lab.data._validation import (
    _parse_iso_date,
    _require_finite_number,
    _require_non_blank,
)
from shiori_pricing_lab.products.enums import (
    Currency,
    TreasuryFTPQuoteSide,
    TreasuryFTPTenor,
    coerce_enum,
)

# FTP percent<->decimal consistency tolerance (docs/23 §5/§12): 3.5500 means
# 3.5500%, decimal 0.0355. This only guards against genuinely inconsistent
# pairs (a transcription error), not floating-point noise from the /100
# division itself.
_FTP_PERCENT_DECIMAL_TOLERANCE = 1e-9


class BLIMarketDataStatus(StrEnum):
    """Per-sub-observation data-quality status (docs/23 §10).

    A minimal, explicitly-not-finalized starting vocabulary, modeled on the
    existing ``BondStatus`` (``docs/20``) precedent. **Construction-time
    gating (Codex P2 review of PR #63, narrowing docs/23 §12's "not
    accepted at construction absent an explicit policy" rule):** only
    ``ACTIVE`` is accepted when building the snapshot or any nested
    observation. ``STALE``/``INVALID``/``MISSING`` are rejected outright.
    ``MANUAL_VERIFIED`` is also rejected for now -- accepting it requires a
    future, explicit manual-verification audit policy (audit metadata,
    who/when/why) that does not exist yet; it is not accepted merely by
    naming the status. The enum keeps all five members because a future
    bundle-layer policy may still need to represent/report a non-active
    status it observed elsewhere -- only *this module's* construction path
    is restricted to ``ACTIVE``.
    """

    ACTIVE = "ACTIVE"
    STALE = "STALE"
    INVALID = "INVALID"
    MISSING = "MISSING"
    MANUAL_VERIFIED = "MANUAL_VERIFIED"


def _require_active_status(status: BLIMarketDataStatus, context: str) -> None:
    """Reject any status other than ``ACTIVE`` at construction (docs/23 §12).

    ``MANUAL_VERIFIED`` gets its own message: it is not rejected because it
    is treated the same as stale/invalid/missing data, but because this
    slice does not yet implement the audit policy (docs/23 §10) that would
    make it acceptable.
    """

    if status is BLIMarketDataStatus.ACTIVE:
        return
    if status is BLIMarketDataStatus.MANUAL_VERIFIED:
        raise ValueError(
            f"{context} status MANUAL_VERIFIED is not accepted yet -- manual verified "
            "status requires a future explicit audit policy (docs/23 §10), not yet "
            "implemented"
        )
    raise ValueError(
        f"{context} status must be ACTIVE, got {status.value} -- STALE/INVALID/MISSING "
        "data is not accepted at construction (docs/23 §12)"
    )


class BLICurvePurpose(StrEnum):
    """Curve purpose vocabulary (docs/23 §7, SPEC §3.5/§7.3).

    Carried explicitly on every curve record -- never inferred from
    currency alone. Option Discount Curve and Bond Reference Curve must
    never be mixed, and the deposit leg must not silently reuse the Option
    Discount Curve without an explicit mapping rule.
    """

    BOND_REFERENCE_CURVE = "BOND_REFERENCE_CURVE"
    OPTION_DISCOUNT_CURVE = "OPTION_DISCOUNT_CURVE"
    DEPOSIT_CURVE = "DEPOSIT_CURVE"
    FUNDING_CURVE = "FUNDING_CURVE"


class BLICurveRateBasis(StrEnum):
    """Economic/quoting basis of a `BLICurvePoint.rate` value (docs/28 §5/§6).

    Deliberately separate from ``BLICurvePurpose``: ``curve_purpose``
    states what a curve is *used for* (discounting the option leg,
    discounting bond coupons, ...); ``rate_basis`` states what a given
    row's ``rate`` value *means* (a continuously-compounded zero rate,
    a par rate, a swap rate, ...). The two questions are independent --
    a ``BOND_REFERENCE_CURVE`` row could in principle carry a par rate
    just as easily as a zero rate, and nothing about ``curve_purpose``
    (or ``curve_name``/``curve_id``/``source_system``/``tenor``) answers
    that (docs/28 §2.1). Annex A §A.10.2 requires interpolation on
    continuously-compounded zero rates; a future interpolation helper
    must reject any row whose ``rate_basis`` is not
    ``CONTINUOUS_ZERO_RATE`` (or route it through a separately reviewed
    conversion slice, docs/28 §5 Path C) rather than silently treating
    it as one.
    """

    CONTINUOUS_ZERO_RATE = "CONTINUOUS_ZERO_RATE"
    SIMPLE_ZERO_RATE = "SIMPLE_ZERO_RATE"
    PAR_RATE = "PAR_RATE"
    SWAP_RATE = "SWAP_RATE"
    BOND_YIELD = "BOND_YIELD"
    FUNDING_RATE = "FUNDING_RATE"
    OTHER = "OTHER"


class BLIVolatilityBasis(StrEnum):
    """Volatility basis vocabulary (docs/23 §8, SPEC §7.4)."""

    YIELD_VOL = "YIELD_VOL"
    PRICE_VOL = "PRICE_VOL"
    EQUIVALENT_PRICE_VOL = "EQUIVALENT_PRICE_VOL"


class BLICreditSpreadTreatment(StrEnum):
    """How a credit spread input was decided (docs/23 §9, SPEC §7.5).

    ``OBSERVED`` is a directly observed spread value -- no override
    happened, so no audit explanation is required. ``OVERRIDE``/``FALLBACK``
    (down the bond-specific -> issuer -> rating/sector proxy -> manual
    override priority chain) and ``EMBEDDED``/``NOT_REQUIRED`` (spread is
    already embedded in a quote/curve, or genuinely not applicable) must
    each carry a non-blank audit explanation -- "not required" is an
    audited decision, never a silent assumption.
    """

    OBSERVED = "OBSERVED"
    OVERRIDE = "OVERRIDE"
    FALLBACK = "FALLBACK"
    EMBEDDED = "EMBEDDED"
    NOT_REQUIRED = "NOT_REQUIRED"


class BLIQuoteBasis(StrEnum):
    """Bond quote basis vocabulary: is the quote expressed as price or yield?

    Deliberately **not** ``products.enums.PayoffBasis`` (Codex P3 review of
    PR #63): ``PayoffBasis`` documents a bond *option's payoff* basis, a
    product/methodology concept, not a market-data quote's basis. Reusing
    it here would couple this market-data schema to a product enum whose
    meaning is unrelated, so a future ``PayoffBasis`` change made for
    option-payoff reasons could silently change what quote basis this
    snapshot accepts. This enum is local to the ``data`` package and used
    only to label which basis a bond quote was primarily observed in --
    it no longer gates which of ``clean_price_per_100`` / ``yield_value``
    may be populated (see ``BLIBondQuote``).
    """

    PRICE = "PRICE"
    YIELD = "YIELD"


@dataclass(frozen=True)
class BLIBondQuote:
    """One bond price/yield observation (docs/23 §4.2, Annex B §B.1).

    At least one of ``clean_price_per_100`` / ``yield_value`` is required;
    **both may be present** (Codex P2 review of PR #63): docs/23 §4.2
    describes the field as "clean_price_per_100 and/or yield", and a bond
    price/yield feed may validly report both for the same observation.
    This schema preserves whatever was observed -- it never performs a
    yield-to-price or price-to-yield conversion, and never infers or
    discards one value in favor of the other. ``price_type`` records which
    basis the quote was primarily reported in; it no longer requires the
    other field to be absent. ``quote_side`` is always explicit; there is
    no default.
    """

    isin: str
    currency: Currency
    price_type: BLIQuoteBasis
    quote_side: TreasuryFTPQuoteSide
    source_system: str
    status: BLIMarketDataStatus
    clean_price_per_100: float | None = None
    yield_value: float | None = None
    accrued_interest_per_100: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", coerce_enum(self.currency, Currency, "currency"))
        object.__setattr__(
            self, "price_type", coerce_enum(self.price_type, BLIQuoteBasis, "price_type")
        )
        object.__setattr__(
            self, "quote_side", coerce_enum(self.quote_side, TreasuryFTPQuoteSide, "quote_side")
        )
        object.__setattr__(
            self, "status", coerce_enum(self.status, BLIMarketDataStatus, "status")
        )
        _require_active_status(self.status, "bond_quote")

        _require_non_blank(self.isin, "isin")
        _require_non_blank(self.source_system, "source_system")

        if self.clean_price_per_100 is None and self.yield_value is None:
            raise ValueError(
                "at least one of clean_price_per_100 / yield_value is required"
            )

        if self.clean_price_per_100 is not None:
            _require_finite_number(self.clean_price_per_100, "clean_price_per_100")
            if not self.clean_price_per_100 > 0:
                raise ValueError(
                    f"clean_price_per_100 must be positive, got {self.clean_price_per_100}"
                )

        if self.yield_value is not None:
            _require_finite_number(self.yield_value, "yield_value")

        if self.accrued_interest_per_100 is not None:
            _require_finite_number(self.accrued_interest_per_100, "accrued_interest_per_100")
            if self.accrued_interest_per_100 < 0:
                raise ValueError(
                    "accrued_interest_per_100 must be non-negative, got "
                    f"{self.accrued_interest_per_100}"
                )


@dataclass(frozen=True)
class BLICurvePoint:
    """One tenor/rate row of a named curve (docs/23 §4.3, Annex B §B.2).

    A curve is a collection of these rows sharing ``curve_id`` -- repeated
    ``currency`` + ``curve_purpose`` values across rows with different
    ``tenor`` are expected and valid, not duplicates (docs/23 §12).

    ``rate_basis`` is required, with no default (docs/28 §6): a
    ``BLICurvePoint`` cannot be constructed without explicitly stating
    what its ``rate`` value means. There is no "if missing, assume
    zero rate" fallback anywhere in this class -- a caller must always
    supply ``rate_basis`` explicitly, and an unrecognized value raises
    rather than being silently coerced to a default.
    """

    curve_id: str
    curve_name: str
    currency: Currency
    curve_purpose: BLICurvePurpose
    tenor: str
    rate: float
    rate_basis: BLICurveRateBasis
    source_system: str
    status: BLIMarketDataStatus

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", coerce_enum(self.currency, Currency, "currency"))
        object.__setattr__(
            self,
            "curve_purpose",
            coerce_enum(self.curve_purpose, BLICurvePurpose, "curve_purpose"),
        )
        object.__setattr__(
            self,
            "rate_basis",
            coerce_enum(self.rate_basis, BLICurveRateBasis, "rate_basis"),
        )
        object.__setattr__(
            self, "status", coerce_enum(self.status, BLIMarketDataStatus, "status")
        )
        _require_active_status(self.status, "curve_point")

        _require_non_blank(self.curve_id, "curve_id")
        _require_non_blank(self.curve_name, "curve_name")
        _require_non_blank(self.tenor, "tenor")
        _require_non_blank(self.source_system, "source_system")

        # Yields/rates may legitimately be signed, same reasoning already
        # applied to BondOption.strike_yield -- no blanket sign constraint.
        _require_finite_number(self.rate, "rate")


@dataclass(frozen=True)
class BLIDepositRateObservation:
    """One Treasury FTP deposit-rate observation (docs/23 §4.4, §5).

    Both the raw percent value and the normalized decimal value are stored
    explicitly (``3.5500`` means ``3.5500%``, decimal ``0.0355``) so the
    conversion is visible in the schema itself; the two must agree within a
    small tolerance, never silently trusting one over the other.
    """

    currency: Currency
    tenor: TreasuryFTPTenor
    quote_side: TreasuryFTPQuoteSide
    ftp_rate_percent_value: float
    ftp_rate_decimal_value: float
    source_system: str
    status: BLIMarketDataStatus

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", coerce_enum(self.currency, Currency, "currency"))
        object.__setattr__(self, "tenor", coerce_enum(self.tenor, TreasuryFTPTenor, "tenor"))
        object.__setattr__(
            self, "quote_side", coerce_enum(self.quote_side, TreasuryFTPQuoteSide, "quote_side")
        )
        object.__setattr__(
            self, "status", coerce_enum(self.status, BLIMarketDataStatus, "status")
        )
        _require_active_status(self.status, "deposit_rate_observation")

        _require_non_blank(self.source_system, "source_system")
        _require_finite_number(self.ftp_rate_percent_value, "ftp_rate_percent_value")
        _require_finite_number(self.ftp_rate_decimal_value, "ftp_rate_decimal_value")

        implied_decimal = self.ftp_rate_percent_value / 100
        if not math.isclose(
            implied_decimal,
            self.ftp_rate_decimal_value,
            abs_tol=_FTP_PERCENT_DECIMAL_TOLERANCE,
        ):
            raise ValueError(
                "ftp_rate_percent_value "
                f"({self.ftp_rate_percent_value}) / 100 = {implied_decimal} does not match "
                f"ftp_rate_decimal_value ({self.ftp_rate_decimal_value}) within tolerance "
                f"{_FTP_PERCENT_DECIMAL_TOLERANCE} -- inconsistent percent/decimal pair"
            )


@dataclass(frozen=True)
class BLIVolatilityInput:
    """One explicit volatility observation (docs/23 §4.5, §8).

    ``override_or_fallback_audit`` must be non-blank whenever it is set --
    a blank/whitespace audit paired with an override/fallback value is
    rejected, never silently accepted. Leaving it ``None`` means the
    volatility is a directly observed value, not an override or fallback.
    """

    volatility: float
    volatility_basis: BLIVolatilityBasis
    source_system: str
    status: BLIMarketDataStatus
    override_or_fallback_audit: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "volatility_basis",
            coerce_enum(self.volatility_basis, BLIVolatilityBasis, "volatility_basis"),
        )
        object.__setattr__(
            self, "status", coerce_enum(self.status, BLIMarketDataStatus, "status")
        )
        _require_active_status(self.status, "volatility_input")

        _require_non_blank(self.source_system, "source_system")

        _require_finite_number(self.volatility, "volatility")
        if not self.volatility > 0:
            raise ValueError(f"volatility must be positive, got {self.volatility}")

        if self.override_or_fallback_audit is not None:
            _require_non_blank(self.override_or_fallback_audit, "override_or_fallback_audit")


@dataclass(frozen=True)
class BLICreditSpreadInput:
    """One explicit credit-spread treatment (docs/23 §4.6, §9).

    ``spread_treatment`` fixes whether ``credit_spread`` /
    ``credit_spread_basis`` are populated:

    - ``OBSERVED``: both required; no audit required (no override happened).
    - ``OVERRIDE`` / ``FALLBACK``: both required; ``override_or_fallback_audit``
      required, non-blank.
    - ``EMBEDDED`` / ``NOT_REQUIRED``: both must be ``None``;
      ``override_or_fallback_audit`` required, non-blank, explaining the
      decision -- credit spread must never silently default to zero, and
      "not required"/"embedded" must be an audited decision, never a
      silent assumption.
    """

    spread_treatment: BLICreditSpreadTreatment
    source_system: str
    status: BLIMarketDataStatus
    credit_spread: float | None = None
    credit_spread_basis: str | None = None
    override_or_fallback_audit: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "spread_treatment",
            coerce_enum(self.spread_treatment, BLICreditSpreadTreatment, "spread_treatment"),
        )
        object.__setattr__(
            self, "status", coerce_enum(self.status, BLIMarketDataStatus, "status")
        )
        _require_active_status(self.status, "credit_spread_input")

        _require_non_blank(self.source_system, "source_system")

        if self.spread_treatment in (
            BLICreditSpreadTreatment.EMBEDDED,
            BLICreditSpreadTreatment.NOT_REQUIRED,
        ):
            if self.credit_spread is not None:
                raise ValueError(
                    "credit_spread must be None when spread_treatment is "
                    f"{self.spread_treatment.value}"
                )
            if self.credit_spread_basis is not None:
                raise ValueError(
                    "credit_spread_basis must be None when spread_treatment is "
                    f"{self.spread_treatment.value}"
                )
            _require_non_blank(
                self.override_or_fallback_audit, "override_or_fallback_audit"
            )
            return

        # OBSERVED / OVERRIDE / FALLBACK all require an actual spread value.
        if self.credit_spread is None:
            raise ValueError(
                f"credit_spread is required when spread_treatment is {self.spread_treatment.value}"
            )
        # Spreads may legitimately be signed (e.g. sub-benchmark), no sign check.
        _require_finite_number(self.credit_spread, "credit_spread")
        _require_non_blank(self.credit_spread_basis, "credit_spread_basis")

        if self.spread_treatment is BLICreditSpreadTreatment.OBSERVED:
            if self.override_or_fallback_audit is not None:
                raise ValueError(
                    "override_or_fallback_audit must be None when spread_treatment is OBSERVED"
                )
        else:  # OVERRIDE / FALLBACK
            _require_non_blank(
                self.override_or_fallback_audit, "override_or_fallback_audit"
            )


def _validate_curve_points(curve_points: tuple[BLICurvePoint, ...]) -> None:
    """Reject duplicate/conflicting curve nodes and ambiguous curve identity.

    Keyed at the curve-node level per docs/23 §12: ``curve_id`` + ``tenor``
    identifies one node (repeated ``currency`` + ``curve_purpose`` across
    different tenors of the same curve is expected). Two different
    ``curve_id``s claiming the same ``currency`` + ``curve_purpose`` without
    an explicit mapping rule is rejected as ambiguous.
    """

    if not curve_points:
        raise ValueError("curve_points must not be empty")

    for point in curve_points:
        if not isinstance(point, BLICurvePoint):
            raise TypeError("curve_points must contain BLICurvePoint instances")

    seen_nodes: dict[tuple[str, str], float] = {}
    for point in curve_points:
        node_key = (point.curve_id, point.tenor)
        if node_key in seen_nodes:
            if seen_nodes[node_key] == point.rate:
                raise ValueError(
                    f"duplicate curve node for curve_id={point.curve_id!r} "
                    f"tenor={point.tenor!r}"
                )
            raise ValueError(
                f"conflicting rate for curve_id={point.curve_id!r} tenor={point.tenor!r}: "
                f"{seen_nodes[node_key]!r} vs {point.rate!r}"
            )
        seen_nodes[node_key] = point.rate

    purpose_curve_ids: dict[tuple[Currency, BLICurvePurpose], set[str]] = {}
    for point in curve_points:
        purpose_key = (point.currency, point.curve_purpose)
        purpose_curve_ids.setdefault(purpose_key, set()).add(point.curve_id)
    for (currency, purpose), curve_ids in purpose_curve_ids.items():
        if len(curve_ids) > 1:
            raise ValueError(
                f"ambiguous curve_id set {sorted(curve_ids)!r} for currency={currency.value!r} "
                f"curve_purpose={purpose.value!r} -- no explicit mapping rule to choose "
                "between them"
            )


@dataclass(frozen=True)
class BLIMarketDataSnapshot:
    """Frozen, explicit, point-in-time BLI market data (docs/23 §2/§4).

    This is layer 3 of the four-layer boundary only: it holds market
    observations for one valuation context, not product terms, bond
    reference data, eligibility, or a pricing result. ``deposit_rate_observation``
    may be ``None`` -- e.g. a ``FIXED_RATE`` ``DepositLeg`` whose rate is
    already a deal term needs no separate market-data rate input, though
    the Deposit Curve in ``curve_points`` may still be required for
    discounting (docs/23 §11.1).
    """

    valuation_date: str
    as_of_timestamp: str
    source_system: str
    snapshot_id: str
    status: BLIMarketDataStatus
    bond_quote: BLIBondQuote
    curve_points: tuple[BLICurvePoint, ...]
    volatility_input: BLIVolatilityInput
    credit_spread_input: BLICreditSpreadInput
    deposit_rate_observation: BLIDepositRateObservation | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "status", coerce_enum(self.status, BLIMarketDataStatus, "status")
        )
        _require_active_status(self.status, "snapshot")

        # valuation_date is explicit and parsed only for format validation --
        # never date.today()/datetime.now() anywhere in this module.
        _parse_iso_date(self.valuation_date, "valuation_date")
        _require_non_blank(self.as_of_timestamp, "as_of_timestamp")
        _require_non_blank(self.source_system, "source_system")
        _require_non_blank(self.snapshot_id, "snapshot_id")

        if not isinstance(self.bond_quote, BLIBondQuote):
            raise TypeError("bond_quote must be a BLIBondQuote")
        if not isinstance(self.volatility_input, BLIVolatilityInput):
            raise TypeError("volatility_input must be a BLIVolatilityInput")
        if not isinstance(self.credit_spread_input, BLICreditSpreadInput):
            raise TypeError("credit_spread_input must be a BLICreditSpreadInput")
        if self.deposit_rate_observation is not None and not isinstance(
            self.deposit_rate_observation, BLIDepositRateObservation
        ):
            raise TypeError(
                "deposit_rate_observation must be None or a BLIDepositRateObservation"
            )

        curve_points = tuple(self.curve_points)
        object.__setattr__(self, "curve_points", curve_points)
        _validate_curve_points(curve_points)


def require_exact_isin_match(snapshot: BLIMarketDataSnapshot, expected_isin: str) -> None:
    """Raise ``ValueError`` unless ``snapshot``'s bond quote ISIN exactly matches.

    Exact string match only (docs/23 §12, mirroring the docs/21 exact-ISIN
    resolver rule) -- no fuzzy, prefix, or case-insensitive matching. Callers
    use this to confirm a snapshot's bond quote lines up with the ISIN a
    ``resolve_bond_reference_data`` call resolved, before combining the two
    in a future input bundle.
    """

    _require_non_blank(expected_isin, "expected_isin")
    if snapshot.bond_quote.isin != expected_isin:
        raise ValueError(
            f"snapshot bond_quote.isin ({snapshot.bond_quote.isin!r}) does not exactly "
            f"match expected_isin ({expected_isin!r})"
        )
