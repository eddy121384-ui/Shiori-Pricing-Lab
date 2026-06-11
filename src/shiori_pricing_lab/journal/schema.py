from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradeJournalEntry:
    """Minimal trade journal record.

    This schema is intentionally small in v0.1. Storage and UI can be added later.
    """

    trade_id: str
    trade_date: str
    instrument: str
    direction: str
    entry_level: float
    size: float
    thesis: str
    stop_loss: float | None = None
    target: float | None = None
    regime: str | None = None
    review_note: str | None = None
