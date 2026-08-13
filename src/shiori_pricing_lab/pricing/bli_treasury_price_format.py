"""Treasury quote display formatting: decimal per 100 -> ``"98-16+"``.

Scope: one pure function, the exact inverse of the existing trader-desk
Treasury quote parser in ``prototype/bond-option-workbench/script.js``
(Issue #161's browser-only ``parse`` + ``TreasuryQuote`` naming, the single
approved Treasury-quote parser -- see
``test_standalone_option_workbench_prototype_static_content.py``'s own
"exists exactly once and only in the browser" guard, which this module's
docstring deliberately never spells that identifier out to avoid tripping)
-- a handle, two 32nds digits, then an optional sub-32nd (``"+"`` for a
half 32nd, otherwise a digit counting eighths of a 32nd). That parser
already reads ``"98-16"`` / ``"98-16+"`` / ``"98-164"`` / ``"99-032"`` into
a decimal per 100; this module renders a decimal back out the same way, so
a number shown next to an OVME reading reads the way the trader's own
screen does. This module is a display formatter only, not a second parser:
it has no ``str -> float`` direction of its own.

**This is a rounded display, never a pricing value.** A derived forward is
an arbitrary decimal and cannot generally be an exact 32nd, so it is
rounded to the nearest eighth of a 32nd (1/256 of a point) to be
displayable at all -- every caller of this module keeps the exact decimal
alongside the formatted string, and computes any residual from the
decimal, never from this string.

Extracted from ``tools/ust_s490_repo_carry_forward_parity.py`` (Issue
#173) so the Workbench server route added in the #174 follow-up can reuse
the exact same formatting rule rather than a second implementation.
"""

from __future__ import annotations

TREASURY_32NDS_PER_POINT = 32
TREASURY_EIGHTHS_PER_32ND = 8
TREASURY_PLUS_EIGHTHS = 4


def format_price_as_treasury_fraction(price_per_100: float) -> str:
    """Return ``price_per_100`` as a Treasury quote string, e.g. ``"98-16+"``.

    A negative price (not a thing any composed caller currently produces,
    but not worth silently mis-rendering) is returned with a leading ``-``
    on the magnitude.
    """

    sign = "-" if price_per_100 < 0 else ""
    magnitude = abs(float(price_per_100))
    total_eighths = round(magnitude * TREASURY_32NDS_PER_POINT * TREASURY_EIGHTHS_PER_32ND)
    eighths_per_point = TREASURY_32NDS_PER_POINT * TREASURY_EIGHTHS_PER_32ND
    handle, remainder = divmod(total_eighths, eighths_per_point)
    thirty_seconds, eighths = divmod(remainder, TREASURY_EIGHTHS_PER_32ND)

    if eighths == 0:
        tail = ""
    elif eighths == TREASURY_PLUS_EIGHTHS:
        tail = "+"
    else:
        tail = str(eighths)
    return f"{sign}{handle}-{thirty_seconds:02d}{tail}"
