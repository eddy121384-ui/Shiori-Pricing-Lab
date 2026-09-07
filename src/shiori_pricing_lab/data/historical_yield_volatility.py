"""``calculate_historical_yield_volatility``: Middle Office-style Historical
Yield Volatility from one bond's own Bloomberg Yield history (Issue #197).

**What this module is.** One canonical, deterministic calculator that reads a
:class:`~shiori_pricing_lab.data.bloomberg_bond_yield_history.BloombergBondYieldHistory`
-- the merged Issue #196 contract, the only historical bond-Yield path in this
repository -- and turns it into an auditable Historical Yield Vol:

``180 Yield observations -> 179 daily Yield Changes -> STDEV.S (ddof=1)
-> x sqrt(252)``

Every number needed to reproduce that result travels with it: which security
Bloomberg resolved, which Yield mnemonic was asked for, the requested date
range, the requested and the *actual* observation counts, the exact dates
used, the number of changes, the standard-deviation convention, the
annualization factor, the daily and the annualized figure, the unit, both
timestamps, and any blocker.

**The convention is PROVISIONAL, not methodology-final (Issue #197).** Middle
Office has confirmed the underlying's own Yield, daily Yield *Change*, a
180-observation 6M-style window, sqrt(252) annualization, and that an
instrument with no history has no approved proxy. It has *not* yet confirmed
179-vs-180 changes or STDEV.S-vs-STDEV.P. This module implements the issue's
explicitly provisional choice -- 179 changes, ddof=1 -- and names it in the
result (``standard_deviation_convention``) precisely so a parity run against
one Middle Office reference case can disprove it cheaply. Nothing here should
be read as evidence that the provisional half is settled.

**What this module deliberately is not.**

- It acquires nothing. It never opens a Bloomberg session, never widens a
  date range, and never re-requests a field. It is handed a series that the
  #196 loader already validated, and it is the only consumer-side statistic
  in this slice.
- It fills nothing. A date Bloomberg did not answer for is simply not in the
  series, and this module neither notices its absence nor manufactures it: no
  interpolation, forward-fill, back-fill, smoothing, winsorization,
  resampling, or synthetic observation exists here. Consequently a "daily"
  Yield Change is the change between two *consecutive returned observations*,
  which under Bloomberg's ``ACTIVE_DAYS_ONLY`` is the natural trading-day
  step and is exactly what a 180-trading-day Middle Office window means.
- **The calculated result** converts no units. The standard deviation of a
  difference carries the unit of the values differenced, so the vol this
  module reports is in the Yield field's own unit -- ``field_unit``, carried
  verbatim from #196 and ``None`` when the request did not establish it.
  That is the number a Middle Office parity run compares against, so it is
  never rescaled behind the reader's back.

  **Publication to the normalized volatility layer** is where a unit is
  normalized, and only there, because that layer's contract states a unit:
  ``BLIVolatilityInput`` carries a *decimal annual* volatility
  (docs/30 §1). Annex A §A.8.1 fixes the discipline this module follows --
  read only an explicitly declared unit, normalize it by an explicit factor
  (``1 bp = 1e-4``), never infer a unit from a value's magnitude, and fail
  closed on a unit that is undeclared or cannot be pinned. So
  :func:`decimal_annual_normalization_factor` accepts exactly
  ``DECIMAL``/``PERCENT``/``BASIS_POINTS`` and refuses everything else,
  including ``None``, and every published input records the source unit and
  the factor applied.
- It prices nothing. The output is a **Yield Vol**. This repository has no
  approved Yield-Vol -> Price-Vol conversion -- ``pricing/
  bli_mvp_required_input_guard.py`` refuses ``YIELD_VOL`` outright, in both
  the bundle and the standalone path, saying so in as many words -- so this
  result stops at the normalized volatility layer and enters no pricing
  chain. Inventing that conversion is a separate, separately approved issue.
- It chooses no proxy. Zero observations is an answer (``NO_HISTORY``), not a
  cue to reach for a benchmark, an index, VCUB, ``VOLATILITY_90D``, or a flat
  synthetic number.

**Window selection.** The window is the *tail* of the returned series: the
most recent ``requested_observation_count`` observations. A trader who asks
Bloomberg for a wider date range than the window still gets exactly the most
recent N observations, and the first/last dates actually used are reported so
the window is never taken on trust.

**Fail-closed conditions**, every one raising
:class:`HistoricalYieldVolInputError` before any statistic exists:

- a ``requested_observation_count`` that is not an ``int`` >= 3 (two
  observations make one change, and one change has no ddof=1 standard
  deviation -- this is arithmetic, not a methodology choice);
- a series whose observation dates are not strictly ascending (the #196
  loader guarantees this; the guard exists so a hand-built or future series
  cannot quietly reorder the changes);
- a non-finite Yield value anywhere in the selected window;
- a row inside the selected window that Bloomberg returned with **no value**.
  This one is a refusal on purpose. Dropping the row would compute a change
  across the hole -- a two-day move recorded as a one-day move -- and keeping
  it would require a number nobody observed. Both are inventions, and which
  one Middle Office would sanction is not established, so the calculation
  stops and names the dates instead.

**Statuses.** ``FULL_WINDOW`` (actual == requested), ``INSUFFICIENT_HISTORY``
(some history, but fewer observations than requested), ``NO_HISTORY`` (none at
all, blocking). An ``INSUFFICIENT_HISTORY`` result still carries whatever the
available observations support, but it can never be mistaken for a full
window: the status, both counts, and a warning line travel with it. Fatal
``blockers`` and non-fatal ``warnings`` are separate fields precisely so a
consumer cannot read "this window is short" as "there is no number here". The "use
the longest available vol flat for the missing horizon" behaviour Middle
Office once mentioned is **not** implemented -- doing so needs an expiry ->
lookback mapping this repository does not have and Issue #197 forbids
inventing.

**No clock inside the statistic.** ``calculated_at`` is provenance, read once
after the arithmetic is complete; no window boundary, count, or value depends
on today's date.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from shiori_pricing_lab.data._validation import _require_finite_number
from shiori_pricing_lab.data.bli_snapshot import (
    BLIMarketDataStatus,
    BLIVolatilityBasis,
    BLIVolatilityInput,
)
from shiori_pricing_lab.data.bloomberg_bond_yield_history import BloombergBondYieldHistory

# The canonical normalized volatility-source label for this calculation.
# Deliberately distinct from Bloomberg VCUB / market-implied vol, from a
# manual override, and from PRICE_VOL / EQUIVALENT_PRICE_VOL: this is a
# Middle Office-style historical statistic on the bond's own Yield, and a
# consumer must be able to tell it apart from all of them at a glance.
HISTORICAL_YIELD_VOL_MO_SOURCE = "HISTORICAL_YIELD_VOL_MO"

# Middle Office's confirmed 6M-style window and annualization.
MIDDLE_OFFICE_6M_OBSERVATION_COUNT = 180
ANNUALIZATION_TRADING_DAYS = 252
ANNUALIZATION_FACTOR = math.sqrt(ANNUALIZATION_TRADING_DAYS)

# PROVISIONAL (Issue #197): sample standard deviation, Excel's STDEV.S.
# Named in every result so the Middle Office parity gate can disprove it.
STANDARD_DEVIATION_CONVENTION = "SAMPLE_STDEV_S_DDOF_1"

# The unit BLIVolatilityInput's own contract states (docs/30 §1), and
# therefore the unit anything published into it must already be in.
PUBLISHED_VOLATILITY_UNIT = "DECIMAL_ANNUAL"

# The only Yield-field units this module will normalize, and the exact factor
# each takes. Annex A §A.8.1: read a *declared* unit, normalize by an explicit
# factor (1 bp = 1e-4), never infer one from a value's magnitude, fail closed
# on anything undeclared. There is no fuzzy matching and no alias list here --
# a unit outside this vocabulary is refused with the vocabulary named, which
# is what tells a trader what to confirm on the workstation.
_DECIMAL_ANNUAL_NORMALIZATION_FACTORS: dict[str, float] = {
    "DECIMAL": 1.0,
    "PERCENT": 1e-2,
    "BASIS_POINTS": 1e-4,
}

SUPPORTED_PUBLICATION_YIELD_UNITS: tuple[str, ...] = tuple(
    sorted(_DECIMAL_ANNUAL_NORMALIZATION_FACTORS)
)

# ddof=1 needs two changes, and two changes need three observations.
_MINIMUM_OBSERVATIONS_FOR_STDEV = 3
_MINIMUM_CHANGES_FOR_STDEV = 2


class HistoricalYieldVolInputError(ValueError):
    """The supplied series or contract cannot produce an honest statistic."""


class HistoricalYieldVolUnavailableError(ValueError):
    """This result cannot be published as a normalized volatility source."""


class HistoricalYieldVolStatus(StrEnum):
    """How much of the requested observation window actually existed."""

    FULL_WINDOW = "FULL_WINDOW"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    NO_HISTORY = "NO_HISTORY"


@dataclass(frozen=True)
class HistoricalYieldVolResult:
    """One Historical Yield Vol calculation, with everything needed to redo it.

    ``field_unit`` is the unit of ``daily_yield_vol`` and
    ``annualized_yield_vol`` as well as of the Yield observations: a standard
    deviation of differences carries the unit of what was differenced, and
    nothing here rescales. ``None`` means the request did not establish a
    unit (#196 never infers one), which a consumer must display as unknown
    rather than assume.

    ``blockers`` and ``warnings`` are deliberately two fields, because the
    two have opposite consequences and conflating them is how a consumer
    ends up discarding a result it was meant to use (Codex review, PR #200):

    - ``blockers`` is **fatal**. ``daily_yield_vol``/``annualized_yield_vol``
      are ``None`` exactly when ``blockers`` is non-empty -- there is no
      number, and every entry says why. :attr:`is_usable` is the same test.
    - ``warnings`` is **not fatal**. An ``INSUFFICIENT_HISTORY`` window that
      still supports the standard-deviation convention carries a number
      *and* a warning: the window being short is not itself a bar to
      publication, but the number is not a full-window result and must never
      be presented as one. A consumer that drops it has lost the only honest
      answer available for that bond.

      "Not a bar to publication" is not a promise that it *will* publish
      (Codex review, PR #200): :func:`historical_yield_vol_volatility_input`
      applies its own conditions on top -- a declared and supported unit, and
      a positive standard deviation -- and a short window failing either of
      those is refused exactly as a full window would be.

    A short window with too few Yield Changes carries both: the warning that
    says the window is short, and the blocker that says it is too short to
    produce a standard deviation at all.
    """

    # -- Provenance carried verbatim from the #196 acquisition --------------
    requested_identifier: str
    security: str
    yield_field: str
    field_meaning: str | None
    field_unit: str | None
    source_system: str
    acquired_at: str
    requested_start_date: date
    requested_end_date: date

    # -- The window this calculation actually used -------------------------
    series_observation_count: int
    requested_observation_count: int
    observation_count: int
    observation_dates: tuple[date, ...]
    first_observation_date: date | None
    last_observation_date: date | None
    yield_change_count: int

    # -- The convention, stated rather than assumed ------------------------
    standard_deviation_convention: str
    annualization_trading_days: int
    annualization_factor: float

    # -- The result --------------------------------------------------------
    daily_yield_vol: float | None
    annualized_yield_vol: float | None
    window_status: HistoricalYieldVolStatus
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    calculated_at: str

    @property
    def is_usable(self) -> bool:
        """Whether this result carries a Historical Yield Vol at all.

        Equivalent to ``not self.blockers``. A ``True`` here says only that a
        number exists -- ``window_status`` and ``warnings`` still decide how
        it may be presented.
        """

        return self.annualized_yield_vol is not None


def _calculation_now() -> datetime:
    """One offset-aware calculation timestamp, read from the platform clock.

    Called only once the arithmetic is finished, so nothing in the statistic
    can depend on it. Module-level for the same reason ``bloomberg_bond_
    yield_history._acquisition_now`` is: tests monkeypatch it and no real
    clock is read in CI.
    """

    return datetime.now().astimezone()


def validate_requested_observation_count(value: object) -> int:
    """Return ``value`` as a usable observation contract, or refuse it.

    Public so a caller can refuse a bad contract *before* spending a
    Bloomberg round trip on a request whose result it would then throw away
    -- the same "caller-input problems raise before anything is sent"
    convention the #196 loader already follows. The calculator calls it too,
    so the check cannot drift between the two.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        raise HistoricalYieldVolInputError(
            f"requested_observation_count must be an int, got {value!r}"
        )
    if value < _MINIMUM_OBSERVATIONS_FOR_STDEV:
        raise HistoricalYieldVolInputError(
            "requested_observation_count must be at least "
            f"{_MINIMUM_OBSERVATIONS_FOR_STDEV} -- {value} observations yield "
            f"{max(value - 1, 0)} Yield Change(s), and the {STANDARD_DEVIATION_CONVENTION} "
            f"convention needs at least {_MINIMUM_CHANGES_FOR_STDEV}"
        )
    return value


def _require_strictly_ascending(history: BloombergBondYieldHistory) -> None:
    previous: date | None = None
    for observation in history.observations:
        if previous is not None and observation.observation_date <= previous:
            raise HistoricalYieldVolInputError(
                "historical Yield observations must be strictly ascending by date before "
                f"a Yield Change is taken -- {observation.observation_date.isoformat()} "
                f"follows {previous.isoformat()} for {history.security!r}"
            )
        previous = observation.observation_date


def calculate_historical_yield_volatility(
    history: BloombergBondYieldHistory,
    *,
    requested_observation_count: int = MIDDLE_OFFICE_6M_OBSERVATION_COUNT,
) -> HistoricalYieldVolResult:
    """Return the Historical Yield Vol of ``history``'s most recent window.

    ``history`` is the underlying bond's **own** Yield series, exactly as the
    Issue #196 loader returned it. ``requested_observation_count`` is the
    explicit observation contract; it defaults to Middle Office's confirmed
    180-observation 6M-style window and is never derived from an expiry, a
    tenor, or a date range.

    Raises :class:`HistoricalYieldVolInputError` for every fail-closed
    condition in the module docstring. Returns a result with
    ``annualized_yield_vol=None`` and a non-empty ``blockers`` for the two
    honest "no number" cases -- no history at all, and history too short to
    support the standard-deviation convention.
    """

    if not isinstance(history, BloombergBondYieldHistory):
        raise HistoricalYieldVolInputError(
            "history must be a BloombergBondYieldHistory produced by the Issue #196 "
            f"loader, got {type(history).__name__}"
        )
    requested = validate_requested_observation_count(requested_observation_count)
    _require_strictly_ascending(history)

    window = history.observations[-requested:]
    dates = tuple(observation.observation_date for observation in window)

    unvalued = [
        observation.observation_date.isoformat()
        for observation in window
        if observation.yield_value is None
    ]
    if unvalued:
        raise HistoricalYieldVolInputError(
            f"Bloomberg returned {len(unvalued)} row(s) with no Yield value inside the "
            f"selected {len(window)}-observation window for {history.security!r} "
            f"({', '.join(unvalued)}) -- a Yield Change is neither taken across such a "
            "row nor invented for it, so this window produces no Historical Yield Vol"
        )

    values: list[float] = []
    for observation in window:
        try:
            _require_finite_number(
                observation.yield_value, f"{history.yield_field} on {observation.observation_date}"
            )
        except ValueError as exc:
            # Converted, not propagated: this module promises one error type
            # for every fail-closed condition, and the workbench route maps
            # that type to an HTTP 400 carrying the refusal verbatim.
            raise HistoricalYieldVolInputError(str(exc)) from exc
        values.append(float(observation.yield_value))  # type: ignore[arg-type]

    # Y_t - Y_{t-1} between consecutive returned observations, in the field's
    # own unit. Ordinary float subtraction on purpose: it is the same
    # arithmetic the Middle Office spreadsheet performs on the same values.
    # `strict=False` is deliberate, not an oversight: pairing a series with
    # its own tail is the one place unequal lengths are the point -- N values
    # make exactly N-1 changes, which is the 180 -> 179 contract itself.
    changes = [
        current - previous for previous, current in zip(values, values[1:], strict=False)
    ]

    # Fatal (no number) and non-fatal (a number, but a qualified one) are
    # kept apart on purpose -- see HistoricalYieldVolResult's docstring.
    blockers: list[str] = []
    warnings: list[str] = []
    if not window:
        status = HistoricalYieldVolStatus.NO_HISTORY
        blockers.append(
            f"Bloomberg returned no Yield observations for {history.security!r} over "
            f"{history.requested_start_date.isoformat()}.."
            f"{history.requested_end_date.isoformat()} -- there is no approved proxy for an "
            "instrument with no history, so no Historical Yield Vol is available"
        )
    elif len(window) < requested:
        status = HistoricalYieldVolStatus.INSUFFICIENT_HISTORY
        # A warning, not a blocker: this window's number is publishable, and
        # what must never happen is it being read as a full-window result.
        warnings.append(
            f"INSUFFICIENT_HISTORY: {len(window)} of the requested {requested} Yield "
            "observations exist. This is not a full-window Historical Yield Vol, and no "
            "flat extension, benchmark, index or VCUB substitute has been applied"
        )
    else:
        status = HistoricalYieldVolStatus.FULL_WINDOW

    if window and len(changes) < _MINIMUM_CHANGES_FOR_STDEV:
        blockers.append(
            f"{len(changes)} Yield Change(s) is below the {_MINIMUM_CHANGES_FOR_STDEV} the "
            f"{STANDARD_DEVIATION_CONVENTION} convention needs -- no standard deviation is "
            "reported for this window"
        )

    if len(changes) >= _MINIMUM_CHANGES_FOR_STDEV:
        # Sample standard deviation, ddof=1 -- Excel's STDEV.S. The stdlib
        # accumulates the sum of squares in exact rational arithmetic, so the
        # answer is the correctly-rounded value of the ddof=1 formula over
        # these changes rather than an accumulation-order artifact.
        daily: float | None = statistics.stdev(changes)
        annualized: float | None = daily * ANNUALIZATION_FACTOR
    else:
        daily = None
        annualized = None

    return HistoricalYieldVolResult(
        requested_identifier=history.requested_identifier,
        security=history.security,
        yield_field=history.yield_field,
        field_meaning=history.field_meaning,
        field_unit=history.field_unit,
        source_system=history.source_system,
        acquired_at=history.acquired_at,
        requested_start_date=history.requested_start_date,
        requested_end_date=history.requested_end_date,
        series_observation_count=len(history.observations),
        requested_observation_count=requested,
        observation_count=len(window),
        observation_dates=dates,
        first_observation_date=dates[0] if dates else None,
        last_observation_date=dates[-1] if dates else None,
        yield_change_count=len(changes),
        standard_deviation_convention=STANDARD_DEVIATION_CONVENTION,
        annualization_trading_days=ANNUALIZATION_TRADING_DAYS,
        annualization_factor=ANNUALIZATION_FACTOR,
        daily_yield_vol=daily,
        annualized_yield_vol=annualized,
        window_status=status,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        calculated_at=_calculation_now().isoformat(timespec="seconds"),
    )


def decimal_annual_normalization_factor(field_unit: object) -> float:
    """Return the factor that takes ``field_unit`` to ``DECIMAL_ANNUAL``.

    Annex A §A.8.1's rule, applied to this module's own output: the unit must
    have been *declared*, it is normalized by an explicit factor, and a unit
    that is absent or outside the supported vocabulary fails closed rather
    than being guessed from how large the number looks.

    Raises :class:`HistoricalYieldVolUnavailableError` for ``None``, for a
    non-string, and for any string outside
    :data:`SUPPORTED_PUBLICATION_YIELD_UNITS`. Surrounding whitespace and
    case are normalized before matching -- that is lexical tidying of the
    trader's own typing, never an interpretation of what a unit means.
    """

    if not isinstance(field_unit, str) or not field_unit.strip():
        raise HistoricalYieldVolUnavailableError(
            "the Yield field's unit was not established by this request, so its Historical "
            f"Yield Vol cannot be normalized to {PUBLISHED_VOLATILITY_UNIT} -- confirm the "
            "unit on the workstation and supply one of "
            f"{', '.join(SUPPORTED_PUBLICATION_YIELD_UNITS)}"
        )
    candidate = field_unit.strip().upper()
    if candidate not in _DECIMAL_ANNUAL_NORMALIZATION_FACTORS:
        raise HistoricalYieldVolUnavailableError(
            f"Yield field unit {field_unit!r} has no approved normalization to "
            f"{PUBLISHED_VOLATILITY_UNIT} -- this module normalizes only "
            f"{', '.join(SUPPORTED_PUBLICATION_YIELD_UNITS)} and refuses to infer a unit "
            "from a value's magnitude (Annex A §A.8.1)"
        )
    return _DECIMAL_ANNUAL_NORMALIZATION_FACTORS[candidate]


def historical_yield_vol_volatility_input(
    result: HistoricalYieldVolResult,
) -> BLIVolatilityInput:
    """Publish ``result`` as Shiori's normalized ``HISTORICAL_YIELD_VOL_MO`` source.

    The already-reviewed ``BLIVolatilityInput`` is the one normalized
    volatility contract in this repository, so this function constructs one
    rather than adding a second schema beside it -- the same
    "one model, a canonical source label for its existing ``source_system``"
    shape ``pricing/bli_effective_forward.py`` established for the Forward.
    The basis is ``YIELD_VOL``, which the MVP/standalone required-input guard
    refuses outright: this source is therefore visible and auditable without
    being able to reach Black-76 through any existing path.

    **The value is normalized to ``DECIMAL_ANNUAL`` on the way in**, because
    that is the unit ``BLIVolatilityInput`` states (docs/30 §1) and the
    contract carries no unit field of its own to say otherwise. A ``PERCENT``
    Yield field whose Historical Vol is ``6.35`` publishes as ``0.0635``; a
    ``BASIS_POINTS`` field normalizes by ``1e-4``. The factor comes from
    :func:`decimal_annual_normalization_factor`, so an undeclared or
    unsupported unit refuses publication instead of putting a
    hundred-times-too-large number into a risk contract. The calculated
    result itself is left in the field's own unit -- that is the number the
    Middle Office parity run compares against.

    Nothing is overwritten or replaced here. A VCUB capture, a manual
    override and this historical statistic are three separately labelled
    inputs; producing one never consumes another.

    ``override_or_fallback_audit`` is always populated, never ``None``. A
    Historical Yield Vol is a computed statistic, not the "directly observed
    value" a blank audit would claim, and Annex A §A.8.1 requires the source
    unit and the normalization factor to travel with every resolved result --
    so a consumer holding only this input can still see the window status,
    both observation counts, the convention, and exactly how the number was
    rescaled.

    Raises :class:`HistoricalYieldVolUnavailableError` when the result cannot
    honestly enter that layer: it carries no volatility (``NO_HISTORY``, or
    too few changes); its unit is undeclared or outside the supported
    vocabulary; or the window's standard deviation is not positive, which
    ``BLIVolatilityInput`` refuses and which this module reports as the
    degenerate window it is rather than publishing.
    """

    if not isinstance(result, HistoricalYieldVolResult):
        raise HistoricalYieldVolUnavailableError(
            f"result must be a HistoricalYieldVolResult, got {type(result).__name__}"
        )
    if result.annualized_yield_vol is None:
        raise HistoricalYieldVolUnavailableError(
            f"no Historical Yield Vol is available for {result.security!r} "
            f"({result.window_status.value}): {'; '.join(result.blockers)}"
        )
    # An INSUFFICIENT_HISTORY result reaching here is publishable by design,
    # and its warning is carried into the audit string below rather than
    # being dropped at the boundary.

    factor = decimal_annual_normalization_factor(result.field_unit)
    normalized = result.annualized_yield_vol * factor

    if not normalized > 0:
        raise HistoricalYieldVolUnavailableError(
            f"the Historical Yield Vol of the selected {result.observation_count}-observation "
            f"window for {result.security!r} is {normalized!r}, which is not positive -- "
            "every Yield Change in the window was identical, so this degenerate window is "
            "reported as what it is rather than published as a volatility"
        )

    audit = (
        f"{HISTORICAL_YIELD_VOL_MO_SOURCE} {result.window_status.value}: calculated from "
        f"{result.observation_count} of the requested {result.requested_observation_count} "
        f"Yield observations ({result.yield_change_count} Yield Changes), "
        f"{result.standard_deviation_convention} x sqrt({result.annualization_trading_days}); "
        f"source unit {result.field_unit} normalized to {PUBLISHED_VOLATILITY_UNIT} by factor "
        f"{factor!r}. No flat extension, benchmark, index or VCUB substitute applied."
    )

    return BLIVolatilityInput(
        volatility=normalized,
        volatility_basis=BLIVolatilityBasis.YIELD_VOL,
        source_system=HISTORICAL_YIELD_VOL_MO_SOURCE,
        status=BLIMarketDataStatus.ACTIVE,
        override_or_fallback_audit=audit,
    )
