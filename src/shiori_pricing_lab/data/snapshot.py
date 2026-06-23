from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from shiori_pricing_lab.data.providers import validate_rates_points_frame


@dataclass(frozen=True)
class MarketDataSnapshot:
    """Frozen market state for a single, explicit valuation date.

    A snapshot wraps already-normalized market data so that pricing engines
    consume a stable, auditable view of the market rather than loose values.

    This is a pure data object. It deliberately knows nothing about curves,
    pricing, or valuation; constructing a :class:`RateCurve` lives in the pricing
    layer (``RateCurve.from_snapshot``), not here, so the data layer never
    depends on the pricing layer.

    Design notes:

    - ``valuation_date`` is a required, explicit string such as ``"2026-06-10"``.
      It is never defaulted to the system date, because historical valuation and
      backtesting must be able to value on arbitrary dates.
    - This minimal v0.1 snapshot only carries normalized rates points.
      Richer categories described in ``docs/02_data_and_market_snapshots.md``
      (curves, fx, vols, fixings, reference data) are intentionally deferred to
      later issues to keep the first skeleton small.
    - ``source`` records data origin, e.g. ``"synthetic"``, ``"csv"``, ``"manual"``.
    - ``metadata`` may later hold snapshot id, created_at, data version, quality
      flags, and notes (see docs/02 snapshot identity).

    Immutability:

    - ``@dataclass(frozen=True)`` only protects the field references, not the
      embedded :class:`pandas.DataFrame`. The snapshot therefore stores a deep
      copy of the rates points (in a private field) and exposes them through the
      ``rates_points`` property, which returns a fresh copy on every read.
      Mutating the original input frame or any returned frame never changes the
      stored snapshot.

    Construction normally goes through :meth:`from_rates_points`.
    """

    valuation_date: str
    source: str
    _rates_points: pd.DataFrame
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.valuation_date).strip():
            raise ValueError("valuation_date must be an explicit, non-empty value")

        # Defensive deep copy so later mutation of the caller's frame cannot leak
        # into the stored snapshot.
        object.__setattr__(self, "_rates_points", self._rates_points.copy(deep=True))

    @property
    def rates_points(self) -> pd.DataFrame:
        """Return a defensive copy of the stored rates points.

        A fresh copy is returned on every read so callers cannot mutate the
        snapshot's data in place.
        """

        return self._rates_points.copy(deep=True)

    @classmethod
    def from_rates_points(
        cls,
        frame: pd.DataFrame,
        valuation_date: str,
        source: str,
        metadata: dict | None = None,
    ) -> MarketDataSnapshot:
        """Build a snapshot for ``valuation_date`` from normalized rates points.

        Blank or empty ``valuation_date`` values are rejected before filtering,
        aligning with :class:`ValuationContext`.

        The input frame may contain multiple valuation dates (for example a
        historical dataset). It is filtered to ``valuation_date``; an error is
        raised only when no rows exist for the requested date.
        """

        if not str(valuation_date).strip():
            raise ValueError("valuation_date must be an explicit, non-empty value")

        validate_rates_points_frame(frame)

        selected = frame.loc[frame["date"].astype(str) == str(valuation_date)].copy()
        if selected.empty:
            raise ValueError(f"No market data rows found for valuation_date={valuation_date}")

        selected = selected.reset_index(drop=True)
        return cls(
            valuation_date=str(valuation_date),
            source=source,
            _rates_points=selected,
            metadata=dict(metadata) if metadata is not None else {},
        )
