from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shiori_pricing_lab.data.snapshot import MarketDataSnapshot

if TYPE_CHECKING:
    from shiori_pricing_lab.pricing.curve import RateCurve


@dataclass(frozen=True)
class ValuationContext:
    """The environment in which a product is valued.

    A product definition describes the trade. A valuation context describes how
    and when it is valued: the explicit valuation date, the market snapshot, and
    the model / convention / reporting settings.

    Design notes:

    - ``valuation_date`` is required and explicit. There is no default, so a
      context can never silently fall back to the system date. This is required
      for historical valuation and backtesting (see docs/03).
    - ``valuation_date`` must equal ``market_snapshot.valuation_date`` so a
      snapshot is never accidentally valued under a different date.
    - ``model_settings`` and ``metadata`` use ``default_factory=dict`` to avoid a
      shared mutable default.
    - ``scenario`` is reserved for a future scenario object (parallel shock,
      steepener, etc.) and stays ``None`` in this minimal skeleton.
    """

    valuation_date: str
    market_snapshot: MarketDataSnapshot
    reporting_currency: str = "USD"
    model_settings: dict = field(default_factory=dict)
    scenario: object | None = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.valuation_date:
            raise ValueError("valuation_date must be an explicit, non-empty value")

        if self.market_snapshot.valuation_date != self.valuation_date:
            raise ValueError(
                "valuation_date does not match market_snapshot.valuation_date: "
                f"{self.valuation_date} != {self.market_snapshot.valuation_date}"
            )

    def build_curve(self) -> RateCurve:
        """Build the rates curve for this context's snapshot and valuation date."""

        return self.market_snapshot.to_rate_curve()
