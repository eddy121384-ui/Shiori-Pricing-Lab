from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pandas as pd

from shiori_pricing_lab.data.providers import validate_rates_points_frame

if TYPE_CHECKING:
    from shiori_pricing_lab.pricing.curve import RateCurve


@dataclass(frozen=True)
class MarketDataSnapshot:
    """Frozen market state for a single, explicit valuation date.

    A snapshot wraps already-normalized market data so that pricing engines
    consume a stable, auditable view of the market rather than loose values.

    Design notes:

    - ``valuation_date`` is a required, explicit string such as ``"2026-06-10"``.
      It is never defaulted to the system date, because historical valuation and
      backtesting must be able to value on arbitrary dates.
    - This minimal v0.1 snapshot only carries normalized ``rates_points``.
      Richer categories described in ``docs/02_data_and_market_snapshots.md``
      (curves, fx, vols, fixings, reference data) are intentionally deferred to
      later issues to keep the first skeleton small.
    - ``source`` records data origin, e.g. ``"synthetic"``, ``"csv"``, ``"manual"``.
    - ``metadata`` may later hold snapshot id, created_at, data version, quality
      flags, and notes (see docs/02 snapshot identity).
    """

    valuation_date: str
    source: str
    rates_points: pd.DataFrame
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_rates_points(
        cls,
        frame: pd.DataFrame,
        valuation_date: str,
        source: str,
        metadata: dict | None = None,
    ) -> MarketDataSnapshot:
        """Build a snapshot for ``valuation_date`` from normalized rates points.

        The input frame may contain multiple valuation dates (for example a
        historical dataset). It is filtered to ``valuation_date``; an error is
        raised only when no rows exist for the requested date.
        """

        validate_rates_points_frame(frame)

        selected = frame.loc[frame["date"].astype(str) == str(valuation_date)].copy()
        if selected.empty:
            raise ValueError(f"No market data rows found for valuation_date={valuation_date}")

        selected = selected.reset_index(drop=True)
        return cls(
            valuation_date=str(valuation_date),
            source=source,
            rates_points=selected,
            metadata=dict(metadata) if metadata is not None else {},
        )

    def to_rate_curve(self) -> RateCurve:
        """Build a :class:`RateCurve` for this snapshot's valuation date."""

        # Imported lazily to keep the data layer free of a hard pricing dependency.
        from shiori_pricing_lab.pricing.curve import RateCurve

        return RateCurve.from_snapshot(self)
