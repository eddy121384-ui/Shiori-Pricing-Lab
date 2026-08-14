"""Effective Forward selection for the standalone Black-76 path (Issue #177).

Scope: one deterministic rule deciding **which** forward clean price
Black-76 actually prices from, and one canonical name for where that number
came from. Nothing else: no repo/carry derivation, no S490 transformation,
no coupon schedule, no curve, no Black-76, no discounting, no volatility.
Every number this module handles has already been produced by somebody
else.

**The rule, in full**::

    Trader Forward Override present  ->  effective F = the override
    otherwise                        ->  effective F = the Shiori Derived
                                         S490 repo-carry Forward
    otherwise                        ->  no F exists; pricing must not run

That third line is the whole point of this module existing. Before Issue
#177 the Forward was a required manual trader entry, so "no Forward" and
"the trader has not finished the ticket" were the same state and the
existing typed contract (``BLIForwardCleanPriceInput`` requires a finite,
strictly positive number) already refused it. Now the default Forward is
*derived*, and a derivation can fail for reasons that have nothing to do
with the ticket being incomplete -- a Bloomberg Curve #490 acquisition
failure, a same-as-of mismatch, a coupon window reaching the bond's
maturity, an interim coupon on a non-UST convention selection. In every one
of those cases there is no honest F, so :func:`select_effective_forward`
raises :class:`ForwardSourceUnavailableError` carrying the derivation's own
error text verbatim. It never substitutes the spot clean price, the last
Forward this ticket happened to hold, a zero-repo/flat-carry forward, or
any other default -- Issue #177's fail-closed requirement.

**One Forward model, not two (Issue #177 explicit constraint).** There is
no second override schema here. The already-reviewed
``data/bli_snapshot.BLIForwardCleanPriceInput`` -- the explicit
forward-with-provenance contract the OVME-aligned standalone path has
priced from since Issue #94 -- remains the *only* forward input the pricing
chain reads, and :func:`forward_clean_price_input_dict` produces exactly
that object's own field shape by constructing one. What Issue #177 adds is
solely a canonical vocabulary for its existing free-form ``source_system``
field, so a priced result can state which of the two sources produced its
F:

- ``SHIORI_DERIVED_S490`` -- the Issue #173/#175 S490 repo-carry Forward,
  derived from the live Bloomberg spot quote and a live Curve #490 / S490
  acquisition for this ticket's own Expiry;
- ``TRADER_FORWARD_OVERRIDE`` -- a Forward the trader typed, deliberately
  taking over from the derived default.

Any other ``source_system`` value (a bundled fixture's
``SANITIZED_SYNTHETIC_MARKET_SOURCE``, an uploaded Case JSON's own label,
the pre-#177 ``MANUAL_TRADER_ENTRY``) is **not** one of these two modes and
is deliberately left alone by every caller of this module -- see
``app/standalone_option_workbench_server.apply_effective_forward_to_case``.
This module classifies nothing on its own: the caller states which numbers
it has, and this module only chooses between them.

**No system clock, no network, no I/O.**
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite

from shiori_pricing_lab.data.bli_snapshot import BLIForwardCleanPriceInput

# The two canonical Forward sources. Written verbatim onto every
# BLIForwardCleanPriceInput this module produces, and therefore onto every
# priced result's own ``forward_clean_price_source_system`` assumption (see
# pricing/bli_pricing_engine.py, unchanged by Issue #177).
SHIORI_DERIVED_S490_FORWARD_SOURCE = "SHIORI_DERIVED_S490"
TRADER_FORWARD_OVERRIDE_FORWARD_SOURCE = "TRADER_FORWARD_OVERRIDE"

# Exactly the two modes this module arbitrates between. A case whose
# forward's ``source_system`` is outside this set is a legacy/explicit
# forward the Issue #177 wiring never touches.
EFFECTIVE_FORWARD_SOURCES: frozenset[str] = frozenset(
    {SHIORI_DERIVED_S490_FORWARD_SOURCE, TRADER_FORWARD_OVERRIDE_FORWARD_SOURCE}
)


class ForwardSourceUnavailableError(ValueError):
    """No effective Forward exists, so pricing must not proceed.

    Raised only in Shiori-derived mode (no Trader Forward Override active)
    when the derivation produced no usable forward clean price. Carries the
    derivation's own failure text verbatim when there is one -- Issue #177
    requires the real reason to survive, not be replaced by a generic
    "forward missing" message.
    """


def _is_usable_forward(value: object) -> bool:
    """True for a finite, strictly positive real number, False otherwise.

    Deliberately the same admissibility ``BLIForwardCleanPriceInput``
    itself enforces (finite and ``> 0``), applied *before* construction so
    an absent/blank/invalid candidate can be answered as "this source has
    no forward" rather than raising out of a constructor. ``bool`` is
    rejected explicitly -- ``True`` is a finite positive ``int`` in Python
    and is never a price.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return isfinite(value) and value > 0


@dataclass(frozen=True)
class EffectiveForward:
    """Which forward Black-76 prices from, and what the alternatives were.

    ``forward_clean_price_per_100`` is the effective F itself.
    ``forward_source`` is the canonical token naming where it came from.

    Both candidates are carried alongside it, always, so a result can
    answer Issue #177's four provenance questions without recomputing
    anything:

    - ``shiori_derived_forward_clean_price_per_100`` is the current Shiori
      Derived Forward, present even while an override is active (that is
      exactly the comparison value a trader with an override wants), and
      ``None`` when the derivation produced nothing;
    - ``trader_forward_override_per_100`` is the override, ``None`` when
      none is active;
    - ``shiori_derived_forward_error`` preserves the derivation's own
      failure text when it failed, ``None`` otherwise. It is populated on a
      *successful* selection too: an active override prices normally while
      the derived comparison value is unavailable, and the reason it is
      unavailable must still be visible rather than silently blank.
    """

    forward_source: str
    forward_clean_price_per_100: float
    shiori_derived_forward_clean_price_per_100: float | None
    trader_forward_override_per_100: float | None
    shiori_derived_forward_error: str | None


def select_effective_forward(
    *,
    shiori_derived_forward_clean_price_per_100: object = None,
    trader_forward_override_per_100: object = None,
    shiori_derived_forward_error: str | None = None,
) -> EffectiveForward:
    """Return the effective Forward for this run, or fail closed.

    An override that is a finite, strictly positive number wins outright:
    the derived Forward is still carried on the result for comparison, and
    a derivation that failed does **not** block pricing in that mode --
    Issue #177's "if the trader has explicitly enabled a valid Forward
    Override, price under the existing override contract".

    With no such override, the derived Forward is the default and must
    itself be a finite, strictly positive number. When it is not, this
    raises :class:`ForwardSourceUnavailableError` carrying
    ``shiori_derived_forward_error`` verbatim when one was supplied. No
    fallback of any kind is produced: not the spot clean price, not a
    previous Forward, not zero repo, not flat carry.

    Both candidates are plain ``object`` parameters so an absent value
    (``None``), a blank one, or a malformed one is answered as "this
    source has no forward" rather than raising a type error from inside the
    selection -- the caller's job is to hand over whatever it has, not to
    pre-validate it.
    """

    derived_usable = _is_usable_forward(shiori_derived_forward_clean_price_per_100)
    derived = float(shiori_derived_forward_clean_price_per_100) if derived_usable else None

    if _is_usable_forward(trader_forward_override_per_100):
        override = float(trader_forward_override_per_100)
        return EffectiveForward(
            forward_source=TRADER_FORWARD_OVERRIDE_FORWARD_SOURCE,
            forward_clean_price_per_100=override,
            shiori_derived_forward_clean_price_per_100=derived,
            trader_forward_override_per_100=override,
            shiori_derived_forward_error=shiori_derived_forward_error,
        )

    if derived is None:
        raise ForwardSourceUnavailableError(
            "no effective Forward is available: the Shiori Derived S490 repo-carry "
            "Forward is this run's Forward source and it produced no usable forward "
            "clean price"
            + (f" -- {shiori_derived_forward_error}" if shiori_derived_forward_error else "")
            + ". Pricing is blocked rather than falling back to the spot clean price, a "
            "previous Forward, zero repo, flat carry, or any other default; enter a "
            "Trader Forward Override to price from an explicit Forward instead"
        )

    return EffectiveForward(
        forward_source=SHIORI_DERIVED_S490_FORWARD_SOURCE,
        forward_clean_price_per_100=derived,
        shiori_derived_forward_clean_price_per_100=derived,
        trader_forward_override_per_100=None,
        shiori_derived_forward_error=shiori_derived_forward_error,
    )


def forward_clean_price_input_dict(effective: EffectiveForward, *, quote_side: object) -> dict:
    """Return ``effective`` as one case-envelope ``forward_clean_price_input``.

    Constructs the already-reviewed ``BLIForwardCleanPriceInput`` -- so its
    own finite/strictly-positive, quote-side-enum and ACTIVE-status rules
    are what validate this value, not a second copy of them here -- and
    returns its field shape via ``dataclasses.asdict``, exactly the shape
    ``app/standalone_option_workbench.build_request_from_standalone_option_case``
    already parses. ``quote_side`` is passed straight through (the request
    contract requires it to equal the spot ``bond_quote.quote_side``, which
    that contract itself still enforces); ``status`` is ``ACTIVE`` because
    an effective Forward that exists at all is an active observation.
    """

    return asdict(
        BLIForwardCleanPriceInput(
            forward_clean_price_per_100=effective.forward_clean_price_per_100,
            quote_side=quote_side,
            source_system=effective.forward_source,
            status="ACTIVE",
        )
    )
