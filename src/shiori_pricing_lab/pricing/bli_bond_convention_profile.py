"""Convention profiles: the market-specific half of Advanced field resolution
(Issue #161, parent milestone #143, following PR #162's UST vertical slice).

**What lives here, and what deliberately does not.** Issue #161's requirement
A splits Advanced field resolution in two, so that a second or third bond
market can be supported without a second or third copy of the resolver:

- **Common bond resolver** (``bli_bond_advanced_field_resolver.py``) -- every
  derivation that does *not* vary by market: the reporting date carried from
  the run's own valuation date, the ACTIVE/matured comparison, the coupon
  grid and its ``last_coupon_date``, the precedence of a confirmed typed
  Bloomberg value over anything else, and the whole field-level
  BLOCKED/unresolved contract. None of that is behind a UST gate any more.
- **This module** -- only the values and rules that genuinely differ between
  markets, expressed once per profile as data, never as a branch in the
  resolver.

A profile is therefore a small frozen record, not a class hierarchy and not a
plugin system: adding a market means adding one
:class:`BLIConventionProfile` instance to :data:`CONVENTION_PROFILES`, and
nothing in the resolver changes.

**A profile is a selection, never an identity claim.** This is carried over
unchanged from PR #162 and is load-bearing for the whole design. Shiori does
not, anywhere, claim to have established who issued a bond. There is no
issuer classification here, and (per Eddy's explicit product-direction
correction on Issue #157 P1-1) no ISIN country prefix, no CUSIP issuer block
and no security-name matching is used to pick a profile. The profile is
applied because it is *selected* -- by the trader, or by a suggestion the
trader can see and override -- not because Shiori believes it has identified
the bond's issuer.

**Suggestion is registry narrowing, not classification** (see
:func:`suggest_convention_profile`). Shiori suggests a profile only when the
bond's *own confirmed terms* fit exactly one registered profile. Two
candidates means no suggestion and an explicit trader choice; zero candidates
means no suggestion and honest reasons. That rule is what keeps requirement
D's promise -- "when Shiori cannot safely tell, ask the trader for a profile,
never for eight hand-typed technical fields" -- true by construction rather
than by good intentions, and it is the reason the suggestion stops being
useful for USD the moment a second USD profile is registered. That is
correct, not a regression: a confirmed Bloomberg currency of ``USD`` is
evidence about the bond's currency and nothing else.

**Every value here needs an approved source.** A profile's constants are
market conventions -- day count, coupon frequency compatibility,
ex-dividend, settlement lag, settlement calendar, bond-type default. Per
``AGENTS.md`` rule 7 and Issue #161's own boundary, each one must come from
Bloomberg evidence, an existing reviewed document, or Eddy's explicit
confirmation. **Model recall is not a source.** :data:`CONVENTION_PROFILES`
therefore contains exactly the profiles whose every constant has such a
source today, which is why ``UST`` is currently alone in it: see this
module's ``docs``-facing note in Issue #161 for the ``US_CORPORATE`` and
``GERMAN_GOVT`` constants still awaiting confirmation. Registering a profile
whose day count or settlement calendar was guessed would produce a wrong
accrued interest, and therefore a wrong dirty price, silently.
"""

from __future__ import annotations

from dataclasses import dataclass

from shiori_pricing_lab.products.enums import Currency, DayCount, Frequency
from shiori_pricing_lab.reference_data.enums import BondStatus, BondType

try:
    import QuantLib as ql
except ImportError:  # QuantLib is optional -- pyproject.toml [project.optional-dependencies].quant
    ql = None


# --- Settlement calendars -----------------------------------------------------
#
# A profile names a calendar; it never carries a holiday table of its own.
# Every entry is a QuantLib calendar reused verbatim, exactly as PR #162 did
# for the U.S. government-bond market -- this repo writes no holiday rules,
# partial or otherwise.

CALENDAR_US_GOVERNMENT_BOND = "US_GOVERNMENT_BOND"

_CALENDAR_FACTORIES = {
    CALENDAR_US_GOVERNMENT_BOND: lambda: ql.UnitedStates(ql.UnitedStates.GovernmentBond),
}


@dataclass(frozen=True)
class BLIConventionProfile:
    """One market's conventions, as data the common resolver reads.

    Only fields that genuinely differ between markets belong here. Anything
    the same for every bond -- the reporting date, the maturity comparison,
    the coupon-grid derivation, the Bloomberg-value precedence -- is the
    common resolver's, and putting a copy of it here would be exactly the
    per-market drift Issue #161 requirement A forbids.

    ``name`` is the selection token that crosses the browser/server boundary
    (``convention_profile`` in the request body) and is echoed back in the
    response. ``default_provenance`` is derived from it so a profile can
    never be registered whose provenance label disagrees with its own name.
    """

    name: str
    currency: Currency
    # Every coupon frequency this profile's conventions are stated for. A
    # bond whose Bloomberg-confirmed frequency is not in here fails closed
    # rather than borrowing another frequency's conventions: the resolver
    # would otherwise silently accrue a Bund on a semi-annual grid.
    coupon_frequencies: tuple[Frequency, ...]
    day_count: DayCount
    # The Bloomberg ``DAY_CNT_DES`` description string that agrees with
    # ``day_count`` for this market. Used only to *withhold* ``day_count``
    # when Bloomberg's own description contradicts it -- never to produce a
    # typed value from a string (Issue #145's prohibition, kept intact).
    day_count_evidence: str
    bond_type: BondType
    ex_dividend_days: int
    status: BondStatus
    settlement_business_days: int
    settlement_calendar: str

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("convention profile name must be a non-blank string")
        if not self.coupon_frequencies:
            raise ValueError(
                f"convention profile {self.name!r} must state at least one coupon "
                "frequency its conventions cover"
            )
        if isinstance(self.ex_dividend_days, bool) or not isinstance(self.ex_dividend_days, int):
            raise ValueError(
                f"convention profile {self.name!r} ex_dividend_days must be an int, got "
                f"{self.ex_dividend_days!r}"
            )
        if self.ex_dividend_days < 0:
            raise ValueError(
                f"convention profile {self.name!r} ex_dividend_days must be non-negative"
            )
        if self.settlement_business_days <= 0:
            raise ValueError(
                f"convention profile {self.name!r} settlement_business_days must be positive"
            )
        if self.settlement_calendar not in _CALENDAR_FACTORIES:
            raise ValueError(
                f"convention profile {self.name!r} names settlement calendar "
                f"{self.settlement_calendar!r}, which is not one of the reviewed "
                f"calendars ({tuple(_CALENDAR_FACTORIES)!r})"
            )

    @property
    def default_provenance(self) -> str:
        """This profile's own provenance tier label, e.g. ``UST_PROFILE_DEFAULT``.

        Derived rather than stored so a profile's label and its selection
        token can never drift apart.
        """

        return f"{self.name}_PROFILE_DEFAULT"

    def calendar(self) -> ql.Calendar:
        """Return this profile's reviewed QuantLib settlement calendar."""

        if ql is None:
            raise BLIConventionProfileCalendarError(
                "QuantLib is not installed -- install the optional 'quant' dependency "
                'group (pip install "shiori-pricing-lab[quant]") to use this function'
            )
        return _CALENDAR_FACTORIES[self.settlement_calendar]()


class BLIConventionProfileCalendarError(RuntimeError):
    """Raised when a profile's settlement calendar cannot be constructed."""


# --- The registered profiles --------------------------------------------------
#
# UST reproduces PR #162's already-UAT-passed constants exactly, value for
# value. Its day count is ACT_ACT_BOND (QuantLib's ActualActual::Bond, the
# ISMA/bond-basis convention) rather than ACT_ACT_ISDA -- see that PR's
# correction and `products/enums.py`: the two are genuinely different accrual
# rules, and ISDA here would misprice accrued interest on every UST.

UST_CONVENTION_PROFILE = BLIConventionProfile(
    name="UST",
    currency=Currency.USD,
    coupon_frequencies=(Frequency.SEMI_ANNUAL,),
    day_count=DayCount.ACT_ACT_BOND,
    day_count_evidence="ACT/ACT",
    bond_type=BondType.FIXED_COUPON_BULLET,
    ex_dividend_days=0,
    status=BondStatus.ACTIVE,
    settlement_business_days=1,
    settlement_calendar=CALENDAR_US_GOVERNMENT_BOND,
)

# Issue #161 asks for `US_CORPORATE` and `GERMAN_GOVT`/`EUR_GOVT` alongside
# `UST`. They are deliberately **not** registered here yet, and this is the
# blocking gap reported on that issue rather than an oversight: each one needs
# a day-count fallback, an ex-dividend rule, a settlement lag, a settlement
# calendar and a coupon-frequency set, and none of those five has a Bloomberg
# evidence record, a reviewed document in `docs/`, or Eddy's confirmation
# today. `docs/30_ust_forward_vol_discounting_reconciliation.md` is explicit
# that settlement dates are "days only if display defines them ... no assumed
# calendar", so inventing one here would contradict an already-reviewed
# decision. The framework above is what makes registering them, once
# confirmed, a data change of a few lines rather than a second resolver.
CONVENTION_PROFILES: dict[str, BLIConventionProfile] = {
    UST_CONVENTION_PROFILE.name: UST_CONVENTION_PROFILE,
}

SUPPORTED_CONVENTION_PROFILE_NAMES = tuple(CONVENTION_PROFILES)


def get_convention_profile(convention_profile: object) -> BLIConventionProfile:
    """Return the registered profile named by caller-supplied browser state.

    ``convention_profile`` is required input naming *which profile is
    selected* -- never a value this module defaults or infers. A missing,
    blank, or unregistered selection raises ``ValueError`` immediately;
    Shiori never silently falls back to a default profile, because a bond
    resolved against the wrong market's conventions prices wrongly without
    saying so.
    """

    if not isinstance(convention_profile, str) or not convention_profile.strip():
        raise ValueError(
            "convention_profile is required and must name the selected convention "
            f"profile (one of {SUPPORTED_CONVENTION_PROFILE_NAMES!r}); got "
            f"{convention_profile!r} -- Shiori never silently falls back to a default "
            "profile for a missing or blank selection"
        )
    profile = CONVENTION_PROFILES.get(convention_profile)
    if profile is None:
        raise ValueError(
            f"convention_profile {convention_profile!r} is not one of the profiles this "
            f"route supports ({SUPPORTED_CONVENTION_PROFILE_NAMES!r}); Shiori never "
            "silently falls back to a default profile for an unrecognized selection"
        )
    return profile


@dataclass(frozen=True)
class BLIConventionProfileSuggestion:
    """Which profile (if any) the bond's own confirmed terms narrow down to.

    ``suggested`` is a profile name only when *exactly one* registered
    profile's stated currency and coupon frequency match what Bloomberg
    confirmed for this bond. ``candidates`` lists every profile that fits, so
    the browser can show the trader a real choice instead of a blank; when
    it holds two or more names, the honest answer is a profile selection --
    never a request to hand-type the Advanced technical fields.

    ``reasons`` explains an absent suggestion in the trader's own terms.
    """

    suggested: str | None
    candidates: tuple[str, ...]
    reasons: tuple[str, ...]


def suggest_convention_profile(
    *,
    currency: object,
    bond_master: dict | None,
) -> BLIConventionProfileSuggestion:
    """Narrow the registry by the bond's confirmed terms; never classify it.

    This reads exactly two Bloomberg-confirmed facts -- the currency the
    quote came back in, and ``CPN_FREQ``'s typed coupon frequency -- and asks
    which registered profiles state conventions for that combination. It
    reads no ISIN, no CUSIP, no security name, and no description string, and
    it makes no claim about who issued the bond.

    Narrowing to one profile is not proof that the profile is right: a USD
    semi-annual corporate bond narrows to ``UST`` today purely because no
    other USD profile is registered yet. That is why the result is a
    *suggestion* the trader sees and can override, and why the profile the
    browser sends is what the resolver actually applies.
    """

    bond_master = dict(bond_master or {})
    coupon_frequency = bond_master.get("coupon_frequency")

    candidates = tuple(
        profile.name
        for profile in CONVENTION_PROFILES.values()
        if profile.currency.value == currency
        and any(frequency.value == coupon_frequency for frequency in profile.coupon_frequencies)
    )

    if len(candidates) == 1:
        return BLIConventionProfileSuggestion(
            suggested=candidates[0], candidates=candidates, reasons=()
        )
    if not candidates:
        return BLIConventionProfileSuggestion(
            suggested=None,
            candidates=(),
            reasons=(
                f"no convention profile Shiori has states conventions for a {currency!r} "
                f"bond paying a {coupon_frequency!r} coupon, so there is nothing to "
                "suggest; select a profile only if one of them genuinely applies",
            ),
        )
    return BLIConventionProfileSuggestion(
        suggested=None,
        candidates=candidates,
        reasons=(
            f"a {currency!r} bond paying a {coupon_frequency!r} coupon fits more than one "
            f"convention profile ({', '.join(candidates)}), and Shiori will not guess an "
            "issuer from an ISIN, a CUSIP or a security name; choose the profile that "
            "applies to this bond",
        ),
    )
