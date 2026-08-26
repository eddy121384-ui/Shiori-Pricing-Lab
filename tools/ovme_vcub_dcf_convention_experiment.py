"""OVME `DCF_VCUB` / `DCF_BondVol` candidate-convention experiment (Issue #192).

Bounded, **offline**, read-only methodology diagnostic -- not part of the
production pricing path, never imported by it, and it pins nothing. It
contains no Bloomberg number, no Bloomberg connection, and no default
convention: every value it reports is either supplied by the operator or
computed from supplied dates.

Annex A §A.8.5 states Bloomberg's OVME total-variance bridge

```text
(σ_Y^N)^2 × DCF_BondVol(t0, TE) = (λ_vcub × σ_vcub)^2 × DCF_VCUB(t0, TE)
```

and keeps both day-count conventions RED: Shiori may not assume they are
equal, may not default the ratio to 1, and may not guess. Issue #192 asks
for the *controlled experiment* that would discriminate the candidates
quantitatively. This module is that experiment's deterministic half:

1. it builds every **candidate year fraction** as an explicit
   (date role × day-count convention) pair -- the day count alone is not a
   candidate, because a one-day difference in the end date (`TE` vs the
   forward settlement date `TF`) moves the volatility multiplier by as much
   as the whole ACT/ACT-vs-ACT/365F effect at the same horizon;
2. it crosses the two legs into candidate pairs and reports each pair's
   `DCF_VCUB`, `DCF_BondVol`, ratio and volatility multiplier
   `sqrt(DCF_VCUB / DCF_BondVol)`;
3. given one live OVME observation (`σ_vcub`, `σ_Y^N`, `λ_vcub`, and the
   **display quantum** of each volatility), it reports the implied ratio as
   an *interval* built from the display rounding, marks which candidate
   pairs survive it, and reports, pair by pair, whether the display
   precision could have separated them at all.

Point 3 is the part the issue insists on: a candidate is never chosen for
"looking closest". A candidate pair is reported as surviving only if its
predicted `σ_Y^N` interval intersects the displayed `σ_Y^N` interval, and
two candidates are reported as distinguishable only if their predicted
intervals are further apart than the display quantum.

**Units.** Every volatility here is in the operator's own display unit
(Bloomberg quotes VCUB normal vol in basis points, per Annex A §A.8.1) and
is never converted: the ratio is scale-invariant, so the arithmetic is
valid in any single consistent unit, and refusing to convert keeps this
diagnostic out of the `bp` / decimal normalization contract that belongs to
the canonical store.

**Day counts.** The ACT/ACT (ISDA) and ACT/365F year fractions reuse the
existing `pricing/bli_valuation_time.py` helpers rather than re-deriving
leap-year arithmetic. ACT/360 is computed here as `days / 360`; the
existing `pricing/bli_repo_carry_forward.py` ACT/360 helper is deliberately
**not** reused, because that one is the repo *accrual* term -- a different
contract that this issue explicitly forbids conflating with a volatility
annualization convention.

**One command, run wherever the OVME numbers were written down**::

    python tools/ovme_vcub_dcf_convention_experiment.py \\
        --pricing-date 2026-08-26 \\
        --expiry-date 2028-12-31 \\
        --forward-settlement-date 2029-01-02 \\
        --sigma-vcub 89.15 --sigma-vcub-quantum 0.01 \\
        --sigma-yield 89.20 --sigma-yield-quantum 0.01 \\
        --lambda-vcub 1.0

Omit `--sigma-yield` to get the design half alone: the candidate ratios,
and -- if `--sigma-yield-quantum` says what precision the screen will
have -- what that precision could ever separate, before spending a live
capture.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from math import isfinite, sqrt
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from shiori_pricing_lab.pricing.bli_valuation_time import (  # noqa: E402
    actual_actual_isda_year_fraction_between_datetimes,
    year_fraction_to_expiry,
)

#: ACT/360's fixed denominator, named here so the number never appears bare.
#: Deliberately *not* imported from the repo-carry module: that constant is
#: the repo accrual basis, and Issue #192 forbids inferring a volatility
#: annualization convention from an accrual convention.
ACT_360_BASIS_DAYS = 360.0


def _act_act_isda_year_fraction(start: date, end: date) -> float:
    """Return the date-based ACT/ACT (ISDA) year fraction."""

    return actual_actual_isda_year_fraction_between_datetimes(
        datetime(start.year, start.month, start.day, tzinfo=UTC),
        datetime(end.year, end.month, end.day, tzinfo=UTC),
    )


def _act_365f_year_fraction(start: date, end: date) -> float:
    """Return the ACT/365F year fraction."""

    return year_fraction_to_expiry(start.isoformat(), end.isoformat())


def _act_360_year_fraction(start: date, end: date) -> float:
    """Return the ACT/360 year fraction (`days / 360`)."""

    if end <= start:
        raise ValueError(f"end ({end.isoformat()!r}) must be strictly after {start.isoformat()!r}")
    return (end - start).days / ACT_360_BASIS_DAYS


#: The candidate day counts Issue #192 names. No entry is a default and no
#: entry is evidence: this is the set the experiment discriminates between.
CANDIDATE_DAY_COUNTS: dict[str, Callable[[date, date], float]] = {
    "ACT/ACT ISDA": _act_act_isda_year_fraction,
    "ACT/365F": _act_365f_year_fraction,
    "ACT/360": _act_360_year_fraction,
}


@dataclass(frozen=True)
class DateRole:
    """One start/end date-role candidate, e.g. `t0 -> TE`.

    The role is part of the candidate, not a fixed input: Issue #192 asks
    for the start/end date semantics to be pinned alongside the day count,
    and at every horizon reachable from a present-day pricing date the
    `TE` / `TF` choice is worth at least as much volatility as the choice
    between ACT/ACT ISDA and ACT/365F.
    """

    name: str
    start: date
    end: date

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("DateRole.name must not be empty")
        if self.end <= self.start:
            raise ValueError(
                f"DateRole {self.name!r}: end ({self.end.isoformat()}) must be strictly "
                f"after start ({self.start.isoformat()})"
            )

    @property
    def calendar_days(self) -> int:
        return (self.end - self.start).days


def candidate_year_fractions(
    roles: Sequence[DateRole],
    day_counts: Mapping[str, Callable[[date, date], float]] = CANDIDATE_DAY_COUNTS,
) -> dict[str, float]:
    """Return `{"<role> <day count>": year fraction}` for every combination.

    Raises `ValueError` on a duplicate role name, so two different date
    pairs can never hide behind one label in a report.
    """

    seen: set[str] = set()
    candidates: dict[str, float] = {}
    for role in roles:
        if role.name in seen:
            raise ValueError(f"duplicate date-role name: {role.name!r}")
        seen.add(role.name)
        for convention, year_fraction in day_counts.items():
            candidates[f"{role.name} {convention}"] = year_fraction(role.start, role.end)
    if not candidates:
        raise ValueError("at least one date role and one day count are required")
    return candidates


@dataclass(frozen=True)
class CandidatePair:
    """One `(DCF_VCUB, DCF_BondVol)` convention hypothesis."""

    vcub_label: str
    bondvol_label: str
    dcf_vcub: float
    dcf_bondvol: float

    @property
    def ratio(self) -> float:
        """`DCF_VCUB / DCF_BondVol`."""

        return self.dcf_vcub / self.dcf_bondvol

    @property
    def vol_multiplier(self) -> float:
        """`sqrt(DCF_VCUB / DCF_BondVol)` -- the factor applied to `λ σ_vcub`."""

        return sqrt(self.ratio)


def candidate_pairs(
    vcub_candidates: Mapping[str, float],
    bondvol_candidates: Mapping[str, float],
) -> tuple[CandidatePair, ...]:
    """Return every `(VCUB leg, BondVol leg)` combination of the two candidate sets."""

    if not vcub_candidates or not bondvol_candidates:
        raise ValueError("both legs need at least one candidate year fraction")
    pairs: list[CandidatePair] = []
    for vcub_label, dcf_vcub in vcub_candidates.items():
        for bondvol_label, dcf_bondvol in bondvol_candidates.items():
            if not (dcf_vcub > 0 and dcf_bondvol > 0):
                raise ValueError(
                    "candidate year fractions must be strictly positive "
                    f"({vcub_label!r}={dcf_vcub!r}, {bondvol_label!r}={dcf_bondvol!r})"
                )
            pairs.append(CandidatePair(vcub_label, bondvol_label, dcf_vcub, dcf_bondvol))
    return tuple(pairs)


@dataclass(frozen=True)
class DisplayedVol:
    """A volatility as Bloomberg displayed it, with its display quantum.

    `quantum` is the smallest increment the screen can show (`0.01` for a
    value displayed as `89.15` in basis points). The displayed value is
    treated as the centre of a half-quantum rounding interval; nothing here
    assumes the screen carries more precision than it shows.
    """

    value: float
    quantum: float

    def __post_init__(self) -> None:
        if not (isfinite(self.value) and self.value > 0):
            raise ValueError(
                f"displayed volatility must be finite and positive, got {self.value!r}"
            )
        if not (isfinite(self.quantum) and self.quantum > 0):
            raise ValueError(f"display quantum must be finite and positive, got {self.quantum!r}")
        if self.quantum >= 2 * self.value:
            raise ValueError(
                f"display quantum ({self.quantum!r}) is too coarse for the displayed "
                f"value ({self.value!r}): the rounding interval would reach zero"
            )

    @property
    def interval(self) -> tuple[float, float]:
        half = self.quantum / 2.0
        return (self.value - half, self.value + half)


def _require_positive_lambda(lambda_vcub: float) -> float:
    if not (isfinite(lambda_vcub) and lambda_vcub > 0):
        raise ValueError(f"lambda_vcub must be finite and positive, got {lambda_vcub!r}")
    return lambda_vcub


def implied_ratio_interval(
    *,
    sigma_vcub: DisplayedVol,
    sigma_yield: DisplayedVol,
    lambda_vcub: float,
) -> tuple[float, float]:
    """Return the `DCF_VCUB / DCF_BondVol` interval implied by one observation.

    From Annex A §A.8.5, `ratio = (σ_Y^N / (λ_vcub × σ_vcub))^2`. Both
    volatilities are displayed, so the implied ratio is an interval, not a
    number: it is widest when a high `σ_Y^N` meets a low `σ_vcub`.
    """

    scale = _require_positive_lambda(lambda_vcub)
    vcub_low, vcub_high = sigma_vcub.interval
    yield_low, yield_high = sigma_yield.interval
    return (
        (yield_low / (scale * vcub_high)) ** 2,
        (yield_high / (scale * vcub_low)) ** 2,
    )


def predicted_yield_vol_interval(
    pair: CandidatePair, *, sigma_vcub: DisplayedVol, lambda_vcub: float
) -> tuple[float, float]:
    """Return the `σ_Y^N` interval this candidate pair predicts.

    The width comes from `σ_vcub`'s own display rounding: a candidate
    predicts an interval, so a candidate can only ever be excluded beyond
    that width.
    """

    scale = _require_positive_lambda(lambda_vcub)
    low, high = sigma_vcub.interval
    multiplier = pair.vol_multiplier
    return (scale * multiplier * low, scale * multiplier * high)


def is_consistent(
    pair: CandidatePair,
    *,
    sigma_vcub: DisplayedVol,
    sigma_yield: DisplayedVol,
    lambda_vcub: float,
) -> bool:
    """Return whether the candidate's predicted interval meets the displayed one."""

    predicted_low, predicted_high = predicted_yield_vol_interval(
        pair, sigma_vcub=sigma_vcub, lambda_vcub=lambda_vcub
    )
    observed_low, observed_high = sigma_yield.interval
    return predicted_low <= observed_high and observed_low <= predicted_high


def surviving_candidates(
    pairs: Sequence[CandidatePair],
    *,
    sigma_vcub: DisplayedVol,
    sigma_yield: DisplayedVol,
    lambda_vcub: float,
) -> tuple[CandidatePair, ...]:
    """Return the candidate pairs one observation cannot exclude."""

    return tuple(
        pair
        for pair in pairs
        if is_consistent(
            pair, sigma_vcub=sigma_vcub, sigma_yield=sigma_yield, lambda_vcub=lambda_vcub
        )
    )


@dataclass(frozen=True)
class Separation:
    """How far apart two candidate pairs' predicted `σ_Y^N` are."""

    left: CandidatePair
    right: CandidatePair
    centre_gap: float
    clear_gap: float
    yield_quantum: float

    @property
    def distinguishable(self) -> bool:
        """Whether *any* displayed `σ_Y^N` could exclude one of the two.

        True only when the two predicted intervals are further apart than
        the `σ_Y^N` display quantum: otherwise some displayed value is
        consistent with both, and this observation can never separate them.
        """

        return self.clear_gap > self.yield_quantum


def separation(
    left: CandidatePair,
    right: CandidatePair,
    *,
    sigma_vcub: DisplayedVol,
    sigma_yield_quantum: float,
    lambda_vcub: float,
) -> Separation:
    """Return the separation between two candidate pairs' predicted `σ_Y^N`."""

    if not (isfinite(sigma_yield_quantum) and sigma_yield_quantum > 0):
        raise ValueError(
            f"sigma_yield_quantum must be finite and positive, got {sigma_yield_quantum!r}"
        )
    left_low, left_high = predicted_yield_vol_interval(
        left, sigma_vcub=sigma_vcub, lambda_vcub=lambda_vcub
    )
    right_low, right_high = predicted_yield_vol_interval(
        right, sigma_vcub=sigma_vcub, lambda_vcub=lambda_vcub
    )
    left_centre = (left_low + left_high) / 2.0
    right_centre = (right_low + right_high) / 2.0
    centre_gap = abs(left_centre - right_centre)
    half_widths = (left_high - left_low) / 2.0 + (right_high - right_low) / 2.0
    return Separation(
        left=left,
        right=right,
        centre_gap=centre_gap,
        clear_gap=centre_gap - half_widths,
        yield_quantum=sigma_yield_quantum,
    )


def indistinguishable_pairs(
    pairs: Sequence[CandidatePair],
    *,
    sigma_vcub: DisplayedVol,
    sigma_yield_quantum: float,
    lambda_vcub: float,
) -> tuple[Separation, ...]:
    """Return every pair of candidates this observation could never separate.

    Candidates that share a volatility multiplier exactly (the same ratio
    reached by a different label) are excluded: they are not two hypotheses
    the experiment could distinguish even with an exact screen, and
    reporting them as an ambiguity would misstate the evidence.
    """

    unseparated: list[Separation] = []
    for index, left in enumerate(pairs):
        for right in pairs[index + 1 :]:
            if left.vol_multiplier == right.vol_multiplier:
                continue
            gap = separation(
                left,
                right,
                sigma_vcub=sigma_vcub,
                sigma_yield_quantum=sigma_yield_quantum,
                lambda_vcub=lambda_vcub,
            )
            if not gap.distinguishable:
                unseparated.append(gap)
    return tuple(unseparated)


def build_date_roles(
    *,
    pricing_date: date,
    expiry_date: date,
    forward_settlement_date: date | None = None,
    spot_settlement_date: date | None = None,
) -> tuple[DateRole, ...]:
    """Return the date-role candidates implied by the supplied dates.

    `t0 -> TE` always exists. Each optional date adds the roles it makes
    possible; nothing is invented, and no settlement lag is derived from a
    calendar this diagnostic does not have.
    """

    roles = [DateRole("t0->TE", pricing_date, expiry_date)]
    if forward_settlement_date is not None:
        roles.append(DateRole("t0->TF", pricing_date, forward_settlement_date))
    if spot_settlement_date is not None:
        roles.append(DateRole("spot->TE", spot_settlement_date, expiry_date))
        if forward_settlement_date is not None:
            roles.append(DateRole("spot->TF", spot_settlement_date, forward_settlement_date))
    return tuple(roles)


def render_report(
    *,
    roles: Sequence[DateRole],
    pairs: Sequence[CandidatePair],
    sigma_vcub: DisplayedVol,
    lambda_vcub: float,
    sigma_yield: DisplayedVol | None,
    sigma_yield_quantum: float | None = None,
) -> str:
    """Return the human-readable experiment report.

    `sigma_yield_quantum` supplies the `σ_Y^N` display precision for a
    design run that has no observation yet. It is never guessed from
    `σ_vcub`'s own quantum: without it a design run reports the candidate
    ratios and says the separability question needs that precision.
    """

    if sigma_yield is not None and sigma_yield_quantum is not None:
        raise ValueError(
            "sigma_yield_quantum is for a design run only; an observed sigma_yield "
            "already carries its own display quantum"
        )

    lines: list[str] = []
    lines.append("OVME DCF_VCUB / DCF_BondVol candidate-convention experiment (Issue #192)")
    lines.append("")
    lines.append("This report pins nothing. It states what the supplied dates imply and,")
    lines.append("if an observation was supplied, which candidates it cannot exclude.")
    lines.append("")

    lines.append("Date roles")
    for role in roles:
        lines.append(
            f"  {role.name:<10} {role.start.isoformat()} -> {role.end.isoformat()}  "
            f"calendar days = {role.calendar_days}"
        )
    lines.append("")

    lines.append(f"lambda_vcub = {lambda_vcub!r}")
    lines.append(
        f"sigma_vcub  = {sigma_vcub.value!r} (display quantum {sigma_vcub.quantum!r}, "
        f"rounding interval [{sigma_vcub.interval[0]!r}, {sigma_vcub.interval[1]!r}])"
    )
    if sigma_yield is None:
        lines.append("sigma_Y^N   = not supplied (design run: candidate ratios only)")
        implied: tuple[float, float] | None = None
    else:
        lines.append(
            f"sigma_Y^N   = {sigma_yield.value!r} (display quantum {sigma_yield.quantum!r}, "
            f"rounding interval [{sigma_yield.interval[0]!r}, {sigma_yield.interval[1]!r}])"
        )
        implied = implied_ratio_interval(
            sigma_vcub=sigma_vcub, sigma_yield=sigma_yield, lambda_vcub=lambda_vcub
        )
        lines.append(f"implied ratio interval = [{implied[0]!r}, {implied[1]!r}]")
    lines.append("")

    header = (
        f"{'candidate DCF_VCUB':<26} {'candidate DCF_BondVol':<26} "
        f"{'DCF_VCUB':>12} {'DCF_BondVol':>12} {'ratio':>12} {'multiplier':>12} "
        f"{'predicted sigma_Y^N':>22}"
    )
    if sigma_yield is not None:
        header += f" {'abs err':>12} {'rel err':>12} {'survives':>9}"
    lines.append("Candidate table")
    lines.append("  " + header)
    ordered = sorted(pairs, key=lambda pair: (pair.ratio, pair.vcub_label, pair.bondvol_label))
    for pair in ordered:
        low, high = predicted_yield_vol_interval(
            pair, sigma_vcub=sigma_vcub, lambda_vcub=lambda_vcub
        )
        row = (
            f"{pair.vcub_label:<26} {pair.bondvol_label:<26} "
            f"{pair.dcf_vcub:>12.8f} {pair.dcf_bondvol:>12.8f} {pair.ratio:>12.8f} "
            f"{pair.vol_multiplier:>12.8f} {f'[{low:.4f}, {high:.4f}]':>22}"
        )
        if sigma_yield is not None and implied is not None:
            implied_centre = (implied[0] + implied[1]) / 2.0
            absolute_error = abs(pair.ratio - implied_centre)
            survives = is_consistent(
                pair,
                sigma_vcub=sigma_vcub,
                sigma_yield=sigma_yield,
                lambda_vcub=lambda_vcub,
            )
            row += (
                f" {absolute_error:>12.8f} {absolute_error / implied_centre:>12.8f} "
                f"{('yes' if survives else 'no'):>9}"
            )
        lines.append("  " + row)
    lines.append("")

    if sigma_yield is not None:
        survivors = surviving_candidates(
            ordered, sigma_vcub=sigma_vcub, sigma_yield=sigma_yield, lambda_vcub=lambda_vcub
        )
        lines.append(f"Surviving candidates: {len(survivors)} of {len(ordered)}")
        for pair in survivors:
            lines.append(f"  DCF_VCUB = {pair.vcub_label}   DCF_BondVol = {pair.bondvol_label}")
        if len(survivors) != 1:
            lines.append(
                "  This observation does not identify a single convention pair. "
                "Do not pick one from this list."
            )
        lines.append("")

    quantum = sigma_yield.quantum if sigma_yield is not None else sigma_yield_quantum
    if quantum is None:
        lines.append(
            "Separability not reported: it needs the sigma_Y^N display quantum, and "
            "this run supplied neither an observed sigma_Y^N nor --sigma-yield-quantum."
        )
        return "\n".join(lines) + "\n"
    unseparated = indistinguishable_pairs(
        ordered,
        sigma_vcub=sigma_vcub,
        sigma_yield_quantum=quantum,
        lambda_vcub=lambda_vcub,
    )
    lines.append(
        f"Candidate pairs this display precision (sigma_Y^N quantum {quantum!r}) "
        f"can never separate: {len(unseparated)}"
    )
    for gap in unseparated[:20]:
        lines.append(
            f"  gap {gap.clear_gap:+.6f} <= quantum {gap.yield_quantum!r}: "
            f"({gap.left.vcub_label} / {gap.left.bondvol_label}) vs "
            f"({gap.right.vcub_label} / {gap.right.bondvol_label})"
        )
    if len(unseparated) > 20:
        lines.append(f"  ... and {len(unseparated) - 20} more")
    return "\n".join(lines) + "\n"


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Deterministic OVME DCF_VCUB / DCF_BondVol candidate-convention experiment "
            "(Issue #192). Pins nothing; reports what the supplied dates and one "
            "supplied observation can and cannot establish."
        )
    )
    parser.add_argument("--pricing-date", required=True, type=_parse_date, help="t0 (YYYY-MM-DD)")
    parser.add_argument("--expiry-date", required=True, type=_parse_date, help="TE (YYYY-MM-DD)")
    parser.add_argument(
        "--forward-settlement-date",
        type=_parse_date,
        default=None,
        help="TF, the bond forward settlement date, if it is known for this case",
    )
    parser.add_argument(
        "--spot-settlement-date",
        type=_parse_date,
        default=None,
        help="the spot settlement date, if the start-date role is also in question",
    )
    parser.add_argument(
        "--sigma-vcub",
        required=True,
        type=float,
        help="the resolved VCUB normal swaption vol, in its own display unit",
    )
    parser.add_argument(
        "--sigma-vcub-quantum",
        required=True,
        type=float,
        help="the smallest increment that vol's display can show (e.g. 0.01)",
    )
    parser.add_argument(
        "--sigma-yield",
        type=float,
        default=None,
        help="OVME's displayed normal bond yield vol, in the same unit as --sigma-vcub",
    )
    parser.add_argument(
        "--sigma-yield-quantum",
        type=float,
        default=None,
        help=(
            "the smallest increment OVME's normal yield vol display can show; required "
            "with --sigma-yield, and usable alone to ask a design run what that "
            "precision could ever separate"
        ),
    )
    parser.add_argument(
        "--lambda-vcub",
        required=True,
        type=float,
        help="the bond-specific scaling factor actually in force for the observed case",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.sigma_yield is not None and args.sigma_yield_quantum is None:
        parser.error("--sigma-yield needs --sigma-yield-quantum: its display precision")

    try:
        roles = build_date_roles(
            pricing_date=args.pricing_date,
            expiry_date=args.expiry_date,
            forward_settlement_date=args.forward_settlement_date,
            spot_settlement_date=args.spot_settlement_date,
        )
        candidates = candidate_year_fractions(roles)
        pairs = candidate_pairs(candidates, candidates)
        sigma_vcub = DisplayedVol(args.sigma_vcub, args.sigma_vcub_quantum)
        sigma_yield = (
            None
            if args.sigma_yield is None
            else DisplayedVol(args.sigma_yield, args.sigma_yield_quantum)
        )
        report = render_report(
            roles=roles,
            pairs=pairs,
            sigma_vcub=sigma_vcub,
            lambda_vcub=args.lambda_vcub,
            sigma_yield=sigma_yield,
            sigma_yield_quantum=None if sigma_yield is not None else args.sigma_yield_quantum,
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(report, end="")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
