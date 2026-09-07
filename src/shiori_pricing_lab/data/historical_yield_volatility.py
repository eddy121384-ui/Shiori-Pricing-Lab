"""``calculate_historical_yield_volatility``: Middle Office-style Historical
Yield Volatility from one bond's own Bloomberg Yield history (Issue #197).

**What this module is.** One canonical, deterministic calculator that reads a
:class:`~shiori_pricing_lab.data.bloomberg_bond_yield_history.BloombergBondYieldHistory`
-- the merged Issue #196 contract, the only historical bond-Yield path in this
repository -- and turns it into an auditable Historical Yield Vol:

``180 Yield observations -> 179 daily Yield Changes -> STDEV.S (ddof=1)
-> x sqrt(252)``

Audit and request provenance travels with every result -- everything Issue
#197 §3 asks for: which security Bloomberg resolved, which Yield mnemonic was
asked for, the requested date range, the requested and the *actual*
observation counts, the exact dates used, the number of changes, the
standard-deviation convention, the annualization factor, the daily and the
annualized figure, the unit, both timestamps, and any warning or blocker.

That is enough to *re-issue* the identical request and *re-run* the identical
convention over its answer. It is deliberately **not** enough to replay the
arithmetic: the Yield values and the Yield Changes themselves are not carried
(Codex review, PR #200), and once Bloomberg's answer for that range changes,
re-issuing does not reproduce these figures either. A consumer needing the
values reads them from the #196 loader for the same query.

``acquired_at`` is temporal provenance, not an acquisition identifier: the
#196 loader stamps it to whole seconds, so two requests answered inside the
same second carry the same string. Two *different* timestamps prove two
acquisitions; two equal ones prove nothing (Codex review, PR #200).

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
  resampling, or synthetic observation exists here. Consequently a Yield
  Change is the change between two *consecutive returned observations*, and
  the window is *observation*-based throughout -- never day-based.

  That distinction matters at the parity gate (Codex review, PR #200).
  ``ACTIVE_DAYS_ONLY`` stops Bloomberg filling a non-trading day in, but it
  does not promise that every active day came back, and this module performs
  no completeness check and has no trading calendar to perform one with. So
  a change spanning an omitted active day is a multi-day move counted once,
  and 180 returned observations can span more than 180 trading days. Where
  Bloomberg returned every active day the two readings coincide, which is the
  ordinary case -- but Middle Office's "180 trading days" and this module's
  "180 returned observations" are not the same statement, and a parity
  mismatch should check the returned dates before it blames the convention.
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


def _require_finite_step(
    values: list[float], history: BloombergBondYieldHistory, *, what: str = "Yield Change"
) -> None:
    """Refuse an intermediate the arithmetic could not represent.

    Separate from the input check on purpose: the observations can each be
    perfectly finite and their difference, or the annualization of their
    standard deviation, still overflow. A non-finite intermediate is not a
    small numerical wobble -- it means the magnitudes involved are outside
    what this statistic can express, so it fails closed rather than reporting
    ``inf`` as a volatility.
    """

    for value in values:
        if not math.isfinite(value):
            raise HistoricalYieldVolInputError(
                f"the {what} for {history.security!r} on {history.yield_field} is "
                f"{value!r}, which is not a finite number -- the Yield magnitudes in this "
                "window overflow the arithmetic, so no Historical Yield Vol is reported "
                "for it"
            )


def _require_consistent_observation_dates(history: BloombergBondYieldHistory) -> None:
    """Every observation is a calendar date, ascending, and inside the range.

    The range half matters because the result copies
    ``requested_start_date``/``requested_end_date`` verbatim and the route
    serializes them as this calculation's provenance. A window built from
    observations outside the range it reports is published as an ``ACTIVE``
    risk source carrying false request provenance (Codex review, PR #200).
    The #196 loader already refuses an out-of-range observation for the same
    reason; this repeats the rule for producers that are not it.
    """

    for name, value in (
        ("requested_start_date", history.requested_start_date),
        ("requested_end_date", history.requested_end_date),
    ):
        if not isinstance(value, date) or isinstance(value, datetime):
            raise HistoricalYieldVolInputError(
                f"{name} must be a calendar date for {history.security!r}, got {value!r} "
                f"({type(value).__name__})"
            )
    if history.requested_start_date > history.requested_end_date:
        raise HistoricalYieldVolInputError(
            f"the declared range for {history.security!r} starts "
            f"{history.requested_start_date.isoformat()}, after it ends "
            f"{history.requested_end_date.isoformat()}"
        )

    previous: date | None = None
    for observation in history.observations:
        # Typed before compared (Codex review, PR #200). `<=` on a mixed
        # date/None or date/str sequence raises TypeError -- not this module's
        # error type -- and an all-string sequence orders lexicographically,
        # passes, and produces a result whose `.isoformat()` blows up in the
        # route later. The #196 loader only ever produces `date`, but this
        # guard exists precisely for the producers that are not it.
        if not isinstance(observation.observation_date, date) or isinstance(
            observation.observation_date, datetime
        ):
            raise HistoricalYieldVolInputError(
                f"every historical Yield observation must carry a calendar date for "
                f"{history.security!r}, got {observation.observation_date!r} "
                f"({type(observation.observation_date).__name__})"
            )
        if previous is not None and observation.observation_date <= previous:
            raise HistoricalYieldVolInputError(
                "historical Yield observations must be strictly ascending by date before "
                f"a Yield Change is taken -- {observation.observation_date.isoformat()} "
                f"follows {previous.isoformat()} for {history.security!r}"
            )
        if (
            observation.observation_date < history.requested_start_date
            or observation.observation_date > history.requested_end_date
        ):
            raise HistoricalYieldVolInputError(
                f"observation {observation.observation_date.isoformat()} for "
                f"{history.security!r} falls outside the declared range "
                f"{history.requested_start_date.isoformat()}.."
                f"{history.requested_end_date.isoformat()} -- a window cannot report a "
                "request range its own observations do not sit in"
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
    _require_consistent_observation_dates(history)

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
            values.append(float(observation.yield_value))  # type: ignore[arg-type]
        except (ValueError, OverflowError) as exc:
            # Converted, not propagated: this module promises one error type
            # for every fail-closed condition, and the workbench route maps
            # that type to an HTTP 400 carrying the refusal verbatim.
            #
            # OverflowError belongs here as much as ValueError (Codex review,
            # PR #200). An int observation beyond the float range -- 10**400
            # from a hand-built or future producer -- overflows inside
            # math.isfinite, and float() overflows on the same value. Neither
            # is a ValueError, so both used to escape this module as an
            # HTTP 500 while its docstring promised one error type.
            raise HistoricalYieldVolInputError(
                f"{history.yield_field} on {observation.observation_date} is not a usable "
                f"Yield value for {history.security!r}: {exc}"
            ) from exc

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
        # A warning, not a blocker: being short does not by itself stop this
        # window's number reaching the publication helper's remaining checks
        # (a declared, supported unit; a positive sigma), and what must never
        # happen is it being read as a full-window result.
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

    # Finite observations are not enough to make the arithmetic finite (Codex
    # review, PR #200). Subtracting two representable Yields can overflow --
    # 1e308 - -1e308 is inf -- and so can annualizing a large daily sigma, and
    # `statistics.stdev` raises AttributeError rather than a ValueError when
    # handed an inf. Left unguarded, this module returned inf as a usable
    # volatility with no blocker at all, and the route turned the other case
    # into an HTTP 500. Every intermediate is checked instead, so an
    # unrepresentable magnitude fails closed on this module's own error type.
    _require_finite_step(changes, history)

    if len(changes) >= _MINIMUM_CHANGES_FOR_STDEV:
        # Sample standard deviation, ddof=1 -- Excel's STDEV.S. The stdlib
        # accumulates the sum of squares in exact rational arithmetic, so the
        # answer is the correctly-rounded value of the ddof=1 formula over
        # these changes rather than an accumulation-order artifact.
        #
        # That exactness is why the call is wrapped (Codex review, PR #200).
        # I had reasoned the Fraction accumulation made an internal overflow
        # impossible and said so; it is wrong. The accumulation is exact, but
        # converting the exact result *back* to a float can still overflow --
        # stdev([float_info.max, -float_info.max]) raises OverflowError, from
        # changes this module has already checked finite. The guard below
        # never ran, and the route answered HTTP 500 under a docstring
        # promising one error type.
        try:
            daily: float | None = statistics.stdev(changes)
        except (OverflowError, ArithmeticError) as exc:
            raise HistoricalYieldVolInputError(
                f"the {STANDARD_DEVIATION_CONVENTION} standard deviation of the "
                f"{len(changes)} Yield Changes for {history.security!r} cannot be "
                f"represented as a finite number: {type(exc).__name__}: {exc}"
            ) from exc
        _require_finite_step([daily], history, what="daily standard deviation")

        # A zero sigma is only honest when the changes really were all equal.
        # Subnormal changes -- [5e-324, 0.0, 0.0, 0.0] -- have a strictly
        # positive exact standard deviation that rounds to 0.0 on the way back
        # to a float, and reporting that as a usable zero volatility with no
        # blocker is a false risk figure, not a flat window (Codex review,
        # PR #200).
        if daily == 0.0 and len(set(changes)) > 1:
            raise HistoricalYieldVolInputError(
                f"the {STANDARD_DEVIATION_CONVENTION} standard deviation of the "
                f"{len(changes)} Yield Changes for {history.security!r} is strictly "
                "positive but underflows to zero as a float -- the changes are too small "
                "to express a volatility, and a zero is not reported in their place"
            )

        annualized: float | None = daily * ANNUALIZATION_FACTOR
        _require_finite_step([annualized], history, what="annualized Historical Yield Vol")
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


def _require_publishable_shape(result: HistoricalYieldVolResult) -> None:
    """Refuse a result whose own fields contradict each other or its contract.

    A ``HistoricalYieldVolResult`` reaching publication is normally one this
    module just built, and it was published on that basis. It need not be:
    the dataclass is public, ``dataclasses.replace`` is one call, and a future
    producer is a different author. Every check here is something this
    module's own calculator guarantees and a hand-built result does not
    (Codex review, PR #200).

    - **Any blocker is fatal.** The dataclass says so, and until this check
      existed a result carrying "must not be used" alongside a finite figure
      published as an ``ACTIVE`` risk source anyway.
    - **The status is the enum**, not a string that looks like one -- reading
      ``.value`` off a bare ``str`` raised ``AttributeError`` past this
      helper's documented error type.
    - **The counts are non-negative integers that agree with each other and
      with the status.** They are copied verbatim into
      ``override_or_fallback_audit``, so an unchecked count is fabricated
      calculation provenance travelling under this module's name --
      ``observation_count=-1`` with ``yield_change_count=999`` published an
      audit claiming exactly that.
    """

    # The status is typed first so every message below can name it safely --
    # reading `.value` off a bare string is one of the escapes this guard
    # exists to close.
    if not isinstance(result.window_status, HistoricalYieldVolStatus):
        raise HistoricalYieldVolUnavailableError(
            f"window_status must be a HistoricalYieldVolStatus for {result.security!r}, got "
            f"{result.window_status!r} ({type(result.window_status).__name__})"
        )
    if result.blockers:
        raise HistoricalYieldVolUnavailableError(
            f"no Historical Yield Vol is available for {result.security!r} "
            f"({result.window_status.value}): "
            f"{'; '.join(str(blocker) for blocker in result.blockers)}"
        )
    if result.annualized_yield_vol is None:
        raise HistoricalYieldVolUnavailableError(
            f"this result for {result.security!r} ({result.window_status.value}) carries no "
            "Historical Yield Vol and no blocker explaining why, so there is nothing to "
            "publish and no reason to give"
        )

    counts = {
        "series_observation_count": result.series_observation_count,
        "requested_observation_count": result.requested_observation_count,
        "observation_count": result.observation_count,
        "yield_change_count": result.yield_change_count,
    }
    for name, value in counts.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise HistoricalYieldVolUnavailableError(
                f"{name} must be a non-negative int for {result.security!r}, got {value!r}"
            )

    expected_changes = max(result.observation_count - 1, 0)
    if result.yield_change_count != expected_changes:
        raise HistoricalYieldVolUnavailableError(
            f"{result.observation_count} Yield observations make {expected_changes} Yield "
            f"Changes, but this result for {result.security!r} claims "
            f"{result.yield_change_count} -- its counts do not describe one calculation"
        )
    if result.observation_count > result.requested_observation_count:
        raise HistoricalYieldVolUnavailableError(
            f"this result for {result.security!r} used {result.observation_count} of a "
            f"requested {result.requested_observation_count} Yield observations, which is "
            "more than were asked for"
        )
    if result.observation_count > result.series_observation_count:
        raise HistoricalYieldVolUnavailableError(
            f"this result for {result.security!r} used {result.observation_count} Yield "
            f"observations from a series of {result.series_observation_count}"
        )

    # The canonical source label names one methodology. A result claiming a
    # different convention or annualization publishes an audit asserting an
    # unapproved method under this module's name, whatever its number was
    # actually computed with (Codex review, PR #200).
    if result.standard_deviation_convention != STANDARD_DEVIATION_CONVENTION:
        raise HistoricalYieldVolUnavailableError(
            f"{HISTORICAL_YIELD_VOL_MO_SOURCE} is the "
            f"{STANDARD_DEVIATION_CONVENTION} convention; this result for "
            f"{result.security!r} claims {result.standard_deviation_convention!r} and "
            "cannot be published under that label"
        )
    if (
        result.annualization_trading_days != ANNUALIZATION_TRADING_DAYS
        or result.annualization_factor != ANNUALIZATION_FACTOR
    ):
        raise HistoricalYieldVolUnavailableError(
            f"{HISTORICAL_YIELD_VOL_MO_SOURCE} annualizes by sqrt("
            f"{ANNUALIZATION_TRADING_DAYS}); this result for {result.security!r} claims "
            f"sqrt({result.annualization_trading_days!r}) = "
            f"{result.annualization_factor!r}"
        )

    # The figure is validated before anything computes with it or compares
    # against it (Codex review, PR #200). A string or complex raised TypeError
    # out of the normalization, inf reached BLIVolatilityInput and raised its
    # raw ValueError, and NaN was reported as a flat window it never was --
    # and checking it here rather than after the daily comparison keeps the
    # message about the figure that is actually malformed.
    try:
        _require_finite_number(result.annualized_yield_vol, "annualized_yield_vol")
    except (ValueError, OverflowError, TypeError) as exc:
        raise HistoricalYieldVolUnavailableError(
            f"the Historical Yield Vol carried by this result for {result.security!r} is "
            f"not a finite number ({result.annualized_yield_vol!r}), so it cannot be "
            f"published as {HISTORICAL_YIELD_VOL_MO_SOURCE}: {exc}"
        ) from exc

    # Both figures exist together and follow the stated annualization. The
    # gate used to be one-sided, so a result with a missing, non-finite or
    # simply wrong daily sigma published beside a valid annualized one, and
    # the card drew a dash or a bogus daily headline next to a live source.
    try:
        _require_finite_number(result.daily_yield_vol, "daily_yield_vol")
    except (ValueError, OverflowError, TypeError) as exc:
        raise HistoricalYieldVolUnavailableError(
            f"the daily standard deviation carried by this result for {result.security!r} "
            f"is not a finite number ({result.daily_yield_vol!r}): {exc}"
        ) from exc
    if result.daily_yield_vol < 0:
        raise HistoricalYieldVolUnavailableError(
            f"the daily standard deviation for {result.security!r} is "
            f"{result.daily_yield_vol!r}, and a standard deviation is never negative"
        )
    # isclose rather than equality: the calculator's own results match exactly,
    # but a legitimate result rebuilt elsewhere may differ in the last ulp, and
    # this guard must not refuse an honest number for that (see the note in
    # this module's review history about rules stricter than the calculator).
    if not math.isclose(
        result.annualized_yield_vol,
        result.daily_yield_vol * ANNUALIZATION_FACTOR,
        rel_tol=1e-12,
        abs_tol=0.0,
    ):
        raise HistoricalYieldVolUnavailableError(
            f"this result for {result.security!r} reports a daily standard deviation of "
            f"{result.daily_yield_vol!r} and an annualized Historical Yield Vol of "
            f"{result.annualized_yield_vol!r}, which is not that daily figure times "
            f"sqrt({ANNUALIZATION_TRADING_DAYS})"
        )

    expected_status = (
        HistoricalYieldVolStatus.NO_HISTORY
        if result.observation_count == 0
        else HistoricalYieldVolStatus.FULL_WINDOW
        if result.observation_count == result.requested_observation_count
        else HistoricalYieldVolStatus.INSUFFICIENT_HISTORY
    )
    if result.window_status is not expected_status:
        raise HistoricalYieldVolUnavailableError(
            f"this result for {result.security!r} reports {result.window_status.value} for "
            f"{result.observation_count} of {result.requested_observation_count} Yield "
            f"observations, which is {expected_status.value}"
        )


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
    _require_publishable_shape(result)
    # An INSUFFICIENT_HISTORY result reaching here is publishable by design,
    # and its warning is carried into the audit string below rather than
    # being dropped at the boundary.

    factor = decimal_annual_normalization_factor(result.field_unit)
    normalized = result.annualized_yield_vol * factor

    if not normalized > 0:
        # Two different causes end up here and they must not be confused: the
        # window really was flat, or a strictly positive vol underflowed to
        # zero on the way into the published unit (0.0 / 5e-324 / 0.0 in
        # PERCENT does exactly that). Blaming identical changes for the second
        # would be a refusal message asserting something untrue, which is the
        # same defect as any other overclaim in this module.
        if not result.annualized_yield_vol > 0:
            cause = (
                "every Yield Change in the window was identical, so this degenerate window "
                "is reported as what it is rather than published as a volatility"
            )
        else:
            cause = (
                f"its Historical Yield Vol of {result.annualized_yield_vol!r} "
                f"{result.field_unit} is too small to represent in "
                f"{PUBLISHED_VOLATILITY_UNIT} and underflowed to zero under the "
                f"{factor!r} normalization -- a volatility that cannot survive its own "
                "unit conversion is refused rather than published as zero"
            )
        raise HistoricalYieldVolUnavailableError(
            f"the Historical Yield Vol of the selected {result.observation_count}-observation "
            f"window for {result.security!r} is {normalized!r} {PUBLISHED_VOLATILITY_UNIT}, "
            f"which is not positive -- {cause}"
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
