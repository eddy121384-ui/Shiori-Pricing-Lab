"""Low-level, product-agnostic validation helpers.

These are the small building blocks shared by the per-product validators
(IRS / OIS / CCS / FX Swap). They check individual fields only — a non-blank
string, a strict ``YYYY-MM-DD`` date — and contain no product-specific or
cross-field logic. The high-level "is this whole trade coherent?" checks stay
product-specific in each product module, deliberately not generalized.

Nothing here touches market data, a curve, or the system clock:
``date.fromisoformat`` is used purely to validate the format and calendar
validity of a deal-term date string, never to read "today".
"""

from __future__ import annotations

import re
from datetime import date

# A strict calendar date: four-digit year, two-digit month, two-digit day,
# dash-separated. This deliberately excludes the other forms
# ``date.fromisoformat`` accepts on Python 3.11+ (compact ``20260701`` and ISO
# week dates such as ``2026-W27-3``) so a product always stores an explicit
# ``YYYY-MM-DD`` string that round-trips unchanged.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _require_non_blank(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")


def _parse_iso_date(value: str, field_name: str) -> date:
    """Parse a strict ``YYYY-MM-DD`` calendar date string.

    Two checks are applied. First the string must match ``YYYY-MM-DD`` exactly:
    ``date.fromisoformat`` alone is too permissive on Python 3.11+ (it accepts
    compact ``20260701`` and ISO week dates like ``2026-W27-3``), but a product
    definition must store the explicit dash-separated form so it serializes back
    unchanged. Second, the value must be a real calendar date.

    ``date.fromisoformat`` is pure parsing, not the system clock, so this does
    not smuggle a valuation date or "today" into a product definition. It is used
    only to validate the format, calendar validity, and ordering of the schedule
    dates.
    """

    _require_non_blank(value, field_name)
    if not _ISO_DATE_RE.match(value):
        raise ValueError(f"{field_name} must be a YYYY-MM-DD date string: {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a YYYY-MM-DD date string: {value!r}") from exc
