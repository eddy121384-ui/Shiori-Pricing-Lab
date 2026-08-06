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
applied because it is *selected* by the trader -- not because Shiori believes
it has identified the bond's issuer.

**Shiori does not auto-select a profile (Issue #161 follow-up correction).**
An earlier revision of this module suggested a profile whenever the bond's
confirmed currency and coupon frequency narrowed the registry to exactly one
entry. Eddy withdrew that: currency and coupon frequency are evidence about a
bond's *cash flows*, never about its issuer class, and the narrowing only
looked safe because ``UST`` happened to be the sole registered USD profile.
With ``US_CORPORATE`` registered alongside it the same USD semi-annual bond
fits both, and no confirmed Bloomberg classification field exists to tell
them apart -- ``SECURITY_TYP`` is still an unprobed candidate in
Shiori's Bloomberg DAPI probe tool (under ``tools/``).

So :func:`convention_profile_candidates` narrows and never chooses. Narrowing
is safe because it only ever *removes* profiles whose stated conventions do
not cover this bond's own confirmed terms; choosing among what remains needs
classification evidence Shiori does not have. Until it does, the trader
selects the profile -- which is exactly what Issue #161 requires as the
honest fallback, and is emphatically not a fallback to hand-typing the eight
Advanced technical fields.

**Every value here needs an approved source.** A profile's constants are
market conventions -- day count, coupon frequency compatibility,
ex-dividend, settlement lag, settlement calendar, bond-type default. Per
``AGENTS.md`` rule 7 and Issue #161's own boundary, each one must come from
Bloomberg evidence, an existing reviewed document, or Eddy's explicit
confirmation. **Model recall is not a source.** The three registered profiles
below carry, per field, the source each value came from; anything without one
is ``None`` and blocks its own field rather than being guessed.
"""

from __future__ import annotations

from collections.abc import Callable
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

# The semantic name stays ``US_SIFMA`` -- what Eddy actually confirmed for
# ``US_CORPORATE`` -- even though the QuantLib object behind it is
# ``UnitedStates(GovernmentBond)``. QuantLib 1.43 exposes no separate "SIFMA"
# market (its ``UnitedStates::Market`` members are Settlement, NYSE,
# GovernmentBond, NERC, LiborImpact, FederalReserve and SOFR); accepted as the
# implementation, per Eddy, but the name in this code must not call a
# corporate-bond convention a "UST calendar" -- the two profiles share a
# QuantLib object, not an identity.
CALENDAR_US_SIFMA = "US_SIFMA"
CALENDAR_TARGET = "TARGET"

_CALENDAR_FACTORIES = {
    CALENDAR_US_SIFMA: lambda: ql.UnitedStates(ql.UnitedStates.GovernmentBond),
    CALENDAR_TARGET: lambda: ql.TARGET(),
}


# --- Plain fixed-coupon structural evidence (Issue #161 follow-up) ------------
#
# The Bond Master facts that would let Shiori positively establish a bond is
# an ordinary fixed-coupon bullet rather than a floater, a linker, a
# convertible or an amortizer.
#
# The already-confirmed ``CALLABLE`` and ``SINKABLE`` flags are deliberately
# not in this tuple: they are checked separately by the resolver's own
# product-shape gate, and neither of them says anything about whether the
# coupon is fixed. A USD floater returns a current coupon in ``CPN`` and a
# reset frequency in ``CPN_FREQ`` and is neither callable nor sinkable, so it
# passes every check that exists today -- which is precisely the exposure
# this gate closes for the two new profiles.
PLAIN_FIXED_COUPON_EVIDENCE_FIELDS = (
    "coupon_type",
    "security_type",
    "inflation_linked_flag",
    "convertible_flag",
    "amortizing_flag",
)

# --- Bloomberg workstation evidence log (Issue #161 follow-up) ---------------
#
# Real evidence Eddy captured on his own Bloomberg Terminal, against
# ``US91282CMC28`` (UST, already admitted), ``US023135EC69`` (a USD
# fixed-rate corporate bond candidate) and ``DE000BU2Z072`` (a EUR fixed-rate
# German government bond candidate). See Shiori's Bloomberg DAPI probe tool's
# own module docstring (under ``tools/``) for the raw per-field probe output
# this summarizes. Recorded here, not just in a chat log, because AGENTS.md
# rule 6 requires deterministic code to trace to real evidence, not to
# something the model recalls.
#
# **Confirmed and wired into structural evidence** (all three securities
# returned a value; see :func:`confirms_plain_fixed_coupon_evidence` below
# for what value each one must carry to count as positive confirmation):
#
# - ``CPN_TYP`` -> ``coupon_type``: all three returned ``"FIXED"``.
# - ``INFLATION_LINKED_INDICATOR`` -> ``inflation_linked_flag``: all three
#   returned ``"N"``.
# - ``CONVERTIBLE`` -> ``convertible_flag``: all three returned ``"N"``.
#
# **Confirmed to return a value, but explicitly NOT approved as structural
# evidence** -- no criterion exists for what value would confirm a plain
# bullet, so it stays unwired and the field permanently blocks the gate
# until one is:
#
# - ``SECURITY_TYP`` -> ``security_type``: returned ``"US GOVERNMENT"``
#   (UST), ``"GLOBAL"`` (corporate candidate), ``"EURO-ZONE"`` (German govt
#   candidate). Three different strings on three different markets is
#   evidence the field carries *some* classification, not evidence of what
#   values would mean "plain fixed-coupon bullet" -- that mapping is not
#   guessed here.
#
# **Confirmed rejected** -- probed as amortizing-evidence candidates,
# disproven, and must not be re-added without a fresh, separately-approved
# confirmation (same standing as ``PENULTIMATE_COUPON_DATE``/
# ``REDEMPTION_VALUE`` in Shiori's Bloomberg DAPI probe tool (under ``tools/``)):
#
# - ``IS_AMORTIZING``, ``AMORT_TYP``, ``REDEMP_TYP``, ``SCHED_TYP``:
#   confirmed ``BAD_FLD`` against the corporate and German government
#   candidates.
# - ``MTG_TYP``: confirmed not applicable to either candidate.
# - ``PRINCIPAL_FACTOR``: confirmed **returned** ``1.000000`` on both
#   candidates, but explicitly **not approved** as amortizing evidence --
#   Eddy's own reasoning: a factor of 1.0 only proves the principal has not
#   yet paid down, never that no future amortization schedule exists. There
#   is still no approved criterion for ``amortizing_flag``.
#
# No further Bloomberg mnemonic guessing follows any of this. The next step
# is Eddy searching Bloomberg's own field directory on his workstation
# (``FLDS<GO>``) or asking Bloomberg support (``HELP HELP``) for the field
# that identifies bullet vs. amortizing redemption or a principal repayment
# schedule -- see Shiori's Bloomberg DAPI probe tool (under ``tools/``)'s own "still needed"
# section for the exact keywords and what to copy back.


# Field -> the predicate its Bloomberg-sourced value must satisfy to count as
# positive confirmation that this bond is a plain fixed-coupon bullet, per
# the evidence log above. A field with no entry here has no approved
# criterion at all -- not "no value yet", but no confirmed rule for what any
# value would mean -- so it can never confirm anything regardless of what
# ``bond_master`` holds for it (`security_type`, `amortizing_flag`).
_PLAIN_FIXED_COUPON_EVIDENCE_CHECKS: dict[str, Callable[[object], bool]] = {
    "coupon_type": lambda value: value == "FIXED",
    "inflation_linked_flag": lambda value: value is False,
    "convertible_flag": lambda value: value is False,
}


def confirms_plain_fixed_coupon_evidence(field: str, value: object) -> bool:
    """Whether ``value`` (from ``bond_master[field]``) positively confirms
    this one structural-evidence field for a plain fixed-coupon bullet.

    This is a *value* check, not a presence check: ``bond_master`` can carry
    a non-``None`` value for ``coupon_type`` (e.g. a real floater's
    ``"FLOATING"``) that must still fail to confirm anything, which is why
    the resolver never treats "the key exists" as sufficient evidence on its
    own. A field with no entry in :data:`_PLAIN_FIXED_COUPON_EVIDENCE_CHECKS`
    (``security_type``, ``amortizing_flag``) has no approved criterion at
    all yet, so this always returns ``False`` for it regardless of the
    value -- see the evidence log above for why.
    """

    check = _PLAIN_FIXED_COUPON_EVIDENCE_CHECKS.get(field)
    if check is None:
        return False
    return bool(check(value))


class BLIConventionProfileCalendarError(RuntimeError):
    """Raised when a profile's settlement calendar cannot be constructed."""


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
    response. ``default_provenance`` is derived from it, so a profile can
    never be registered whose provenance tier disagrees with its own name.

    ``source_system`` is **not** derived from ``name``, and this is a
    deliberate compatibility correction: an earlier revision derived it as
    ``f"SHIORI_{name}_CONVENTION_PROFILE"``, which would have silently
    changed UST's already-shipped run-export value from
    ``SHIORI_UST_FIXED_COUPON_PROFILE`` (PR #162, real Bloomberg workstation
    UAT) to ``SHIORI_UST_CONVENTION_PROFILE``. Eddy's instruction is that the
    existing UST export contract must not move for the sake of a uniform
    naming scheme, so every profile states its own ``source_system``
    explicitly instead -- see :data:`UST_CONVENTION_PROFILE` for the
    preserved value, and :data:`US_CORPORATE_CONVENTION_PROFILE` /
    :data:`GERMAN_GOVT_CONVENTION_PROFILE` for the new profiles' own.

    Two fields are deliberately **optional**, because "this market's value is
    not confirmed" is a real state that must not be papered over with a
    guess:

    - ``ex_dividend_days`` -- ``None`` means no approved default for this
      market. The resolver then uses a typed Bloomberg value if one is ever
      returned, and otherwise blocks that one field for the trader to set,
      leaving every other Advanced field resolved as normal.
    - ``day_count_evidence`` -- ``None`` means Bloomberg's own
      ``DAY_CNT_DES`` description string for this market has not been
      confirmed, so it is not read at all for this profile. It is only ever
      used to *withhold* ``day_count`` when it contradicts, never to produce
      a typed value from a string (Issue #145's prohibition, intact), and
      comparing against an unconfirmed expected string would block every
      ordinary bond in the market instead.
    """

    name: str
    currency: Currency
    # Every coupon frequency this profile's conventions are stated for. A
    # bond whose Bloomberg-confirmed frequency is not in here fails closed
    # rather than borrowing another frequency's conventions: the resolver
    # would otherwise silently accrue a Bund on a semi-annual grid.
    coupon_frequencies: tuple[Frequency, ...]
    day_count: DayCount
    bond_type: BondType
    status: BondStatus
    settlement_business_days: int
    settlement_calendar: str
    # This profile's own explicit ``source_system`` label for the run
    # export. Explicit, not derived from ``name`` -- see this dataclass's own
    # docstring for why UST's value must not move.
    source_system: str
    ex_dividend_days: int | None = None
    day_count_evidence: str | None = None
    # Whether this profile may only be applied to a bond Shiori can
    # positively establish is a plain fixed-coupon bullet, using the
    # confirmed Bond Master facts in
    # :data:`PLAIN_FIXED_COUPON_EVIDENCE_FIELDS`. ``UST`` is exempt by
    # Eddy's explicit instruction (its behaviour passed real Bloomberg
    # workstation UAT and must not regress); every other market requires the
    # evidence, so selecting a profile by hand can never push a floater, a
    # linker, a convertible or an amortizer through.
    plain_fixed_coupon_evidence_required: bool = True

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("convention profile name must be a non-blank string")
        if not self.coupon_frequencies:
            raise ValueError(
                f"convention profile {self.name!r} must state at least one coupon "
                "frequency its conventions cover"
            )
        if not self.source_system or not self.source_system.strip():
            raise ValueError(
                f"convention profile {self.name!r} must state an explicit, non-blank "
                "source_system for the run export"
            )
        if self.ex_dividend_days is not None:
            if isinstance(self.ex_dividend_days, bool) or not isinstance(
                self.ex_dividend_days, int
            ):
                raise ValueError(
                    f"convention profile {self.name!r} ex_dividend_days must be an int or "
                    f"None (no approved default), got {self.ex_dividend_days!r}"
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


# --- The registered profiles --------------------------------------------------

# Reproduces PR #162's already-UAT-passed constants exactly, value for value.
# Its day count is ACT_ACT_BOND (QuantLib's ActualActual::Bond, the ISMA /
# bond-basis convention) rather than ACT_ACT_ISDA -- see that PR's correction
# and `products/enums.py`: the two are genuinely different accrual rules, and
# ISDA here would misprice accrued interest on every UST.
#
# It is the one profile exempt from the plain-fixed-coupon evidence gate, by
# Eddy's explicit instruction: this exact behaviour passed real Bloomberg
# workstation UAT on US91282CMC28 and Issue #161 requires it not regress.
#
# `source_system` is PR #162's already-shipped, UAT-passed export label,
# verbatim. It is explicit here rather than derived from `name` specifically
# so registering US_CORPORATE and GERMAN_GOVT beside it cannot move it --
# see BLIConventionProfile's own docstring.
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
    settlement_calendar=CALENDAR_US_SIFMA,
    source_system="SHIORI_UST_FIXED_COUPON_PROFILE",
    plain_fixed_coupon_evidence_required=False,
)

# Annex A, confirmed by Eddy: USD, semi-annual, 30/360 (bond basis), T+2 on
# the SIFMA-recommended U.S. bond-market calendar.
#
# `ex_dividend_days` is deliberately absent: Eddy confirmed the four Annex A
# values above and explicitly did *not* confirm an ex-dividend default for
# this market, so the resolver blocks that one field rather than assuming
# zero. `day_count_evidence="30/360"` is the Bloomberg workstation evidence
# log's own DAY_CNT_DES observation for US023135EC69 (a real USD fixed-rate
# corporate bond candidate) -- it agrees with the confirmed Annex A day
# count, so it is wired the same way UST's is: DAY_CNT_DES reading anything
# else contradicts this profile and withholds day_count alone for the trader
# to set.
US_CORPORATE_CONVENTION_PROFILE = BLIConventionProfile(
    name="US_CORPORATE",
    currency=Currency.USD,
    coupon_frequencies=(Frequency.SEMI_ANNUAL,),
    day_count=DayCount.THIRTY_360,
    day_count_evidence="30/360",
    bond_type=BondType.FIXED_COUPON_BULLET,
    status=BondStatus.ACTIVE,
    settlement_business_days=2,
    settlement_calendar=CALENDAR_US_SIFMA,
    source_system="SHIORI_US_CORPORATE_CONVENTION_PROFILE",
)

# Annex A, confirmed by Eddy: EUR, annual, ACT/ACT (bond basis -- QuantLib's
# ActualActual::Bond, the ISMA convention), T+2 on the TARGET calendar. Named
# GERMAN_GOVT rather than EUR_GOVT at Eddy's direction, so a French or
# Italian government bond gets its own profile rather than quietly borrowing
# this one's settlement calendar and day count.
#
# `ex_dividend_days` is absent for the same reason as US_CORPORATE above.
# `day_count_evidence="ACT/ACT"` is the Bloomberg workstation evidence log's
# own DAY_CNT_DES observation for DE000BU2Z072 (a real EUR fixed-rate German
# government bond candidate) -- it agrees with the confirmed Annex A day
# count, so it is wired the same way.
GERMAN_GOVT_CONVENTION_PROFILE = BLIConventionProfile(
    name="GERMAN_GOVT",
    currency=Currency.EUR,
    coupon_frequencies=(Frequency.ANNUAL,),
    day_count=DayCount.ACT_ACT_BOND,
    day_count_evidence="ACT/ACT",
    bond_type=BondType.FIXED_COUPON_BULLET,
    status=BondStatus.ACTIVE,
    settlement_business_days=2,
    settlement_calendar=CALENDAR_TARGET,
    source_system="SHIORI_GERMAN_GOVT_CONVENTION_PROFILE",
)

CONVENTION_PROFILES: dict[str, BLIConventionProfile] = {
    profile.name: profile
    for profile in (
        UST_CONVENTION_PROFILE,
        US_CORPORATE_CONVENTION_PROFILE,
        GERMAN_GOVT_CONVENTION_PROFILE,
    )
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
class BLIConventionProfileCandidates:
    """Which profiles this bond's own confirmed terms have not ruled out.

    ``candidates`` holds every registered profile whose stated currency and
    coupon frequency cover this bond. It is a *narrowing*, never a choice:

    - **empty** -- no registered profile states conventions for this bond at
      all. There is nothing for the trader to select, so this is a refusal,
      and the browser disables the Advanced controls exactly as it does for
      any other unsupported product.
    - **one or more** -- the trader selects which one applies. Shiori does
      not pick, even when only one name is left, because narrowing by
      currency and coupon frequency is not issuer classification and the
      Bloomberg field that would be (``SECURITY_TYP``) is still unconfirmed.

    There is deliberately no ``suggested`` field. See the module docstring.
    """

    candidates: tuple[str, ...]
    reasons: tuple[str, ...]


def convention_profile_candidates(
    *,
    currency: object,
    bond_master: dict | None,
) -> BLIConventionProfileCandidates:
    """Narrow the registry by the bond's confirmed terms; never choose for the trader.

    This reads exactly two Bloomberg-confirmed facts -- the currency the
    quote came back in, and ``CPN_FREQ``'s typed coupon frequency -- and asks
    which registered profiles state conventions for that combination. It
    reads no ISIN, no CUSIP, no security name, and no description string, and
    it makes no claim about who issued the bond.
    """

    bond_master = dict(bond_master or {})
    coupon_frequency = bond_master.get("coupon_frequency")

    candidates = tuple(
        profile.name
        for profile in CONVENTION_PROFILES.values()
        if profile.currency.value == currency
        and any(frequency.value == coupon_frequency for frequency in profile.coupon_frequencies)
    )

    if not candidates:
        return BLIConventionProfileCandidates(
            candidates=(),
            reasons=(
                f"no convention profile Shiori has states conventions for a {currency!r} "
                f"bond paying a {coupon_frequency!r} coupon, so there is no profile to "
                "select and nothing here can be pre-filled",
            ),
        )
    return BLIConventionProfileCandidates(
        candidates=candidates,
        reasons=(
            f"a {currency!r} bond paying a {coupon_frequency!r} coupon fits "
            f"{', '.join(candidates)}. Shiori will not choose between profiles: currency "
            "and coupon frequency describe this bond's cash flows, not its issuer class, "
            "and it will never guess one from an ISIN, a CUSIP or a security name. Select "
            "the profile that applies to this bond",
        ),
    )
