"""Low-level, reference-data-agnostic validation helpers.

Deliberately duplicated from ``shiori_pricing_lab.products._validation``
rather than imported: ``reference_data`` is a sibling package to
``products``, not a consumer of it (docs/20 §7 -- Bond Reference Data is
not a product schema), so it does not take a dependency on the products
package for three small field-level checks. This mirrors the existing
repo convention of duplicating small, module-local validation helpers
(e.g. ``_require_finite_number`` is already duplicated between
``products/bond_option.py`` and ``products/deposit_leg.py``).

Nothing here touches market data, a curve, or the system clock:
``date.fromisoformat`` is used purely to validate the format and calendar
validity of a reference-data date string, never to read "today".
"""

from __future__ import annotations

import math
import re
from datetime import date

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _require_non_blank(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")


def _parse_iso_date(value: object, field_name: str) -> date:
    """Parse a strict ``YYYY-MM-DD`` calendar date string.

    Rejects anything that is not a non-blank string matching ``YYYY-MM-DD``
    exactly (so compact ``20260701`` and ISO week dates like ``2026-W27-3``
    are rejected), then requires it to be a real calendar date.
    """

    _require_non_blank(value, field_name)
    assert isinstance(value, str)
    if not _ISO_DATE_RE.match(value):
        raise ValueError(f"{field_name} must be a YYYY-MM-DD date string: {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a YYYY-MM-DD date string: {value!r}") from exc


def _require_finite_number(value: object, field_name: str) -> None:
    """Reject anything that is not a real, finite ``int``/``float``.

    ``bool`` is an ``int`` subclass in Python, so it is excluded explicitly.
    This only checks "is this a usable real number" -- it does not impose a
    sign; callers apply their own sign rules on top where needed.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
