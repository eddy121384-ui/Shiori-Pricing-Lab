"""S490 repo-carry funding resolver (Issue #173, prototype assumption).

Scope: one small, explicit, swappable resolver that turns the **existing
production Bloomberg Curve #490 / S490 data** into the single repo/carry
funding number ``pricing/bli_repo_carry_forward.py`` needs for one
``spot settlement -> forward settlement`` horizon. Nothing else: no forward
price, no option discounting, no Black-76, no volatility, no new Bloomberg
ticker mapping, and no second curve representation.

**Input is the production curve, not a new acquisition route.**
``curve_points`` are exactly the ``BLICurvePoint`` rows the existing
production Curve #490 loader in ``data/`` already returns (``S0490Z``
continuous zero rates: ``OPTION_DISCOUNT_CURVE`` /
``CONTINUOUS_ZERO_RATE``, each row carrying the vendor's own ``MATURITY``
verbatim in ``maturity_date``) -- the same rows Issue #171 already injects
into the Workbench case envelope. This module names no vendor ticker,
performs no acquisition of any kind, and never constructs a second mapping
-- Issue #173's "do not duplicate the vendor ticker mappings" requirement.
Like every other module in ``pricing/``, it takes already-acquired typed
curve rows and nothing else; the acquisition route lives entirely in
``data/`` and is exercised by
``tools/ust_s490_repo_carry_forward_parity.py``.

**Two labeled transformations, and why there are exactly two.** Issue #173
states plainly that OVME F's exact repo transformation for an arbitrary,
non-standard horizon (par interpolation / zero / DF-implied forward / an
internal SWDF transformation) is *not* proven by any evidence this
repository holds, and instructs implementing the uncertainty as a clearly
labeled prototype assumption and letting parity decide. Guessing once
would not let parity decide anything, so this resolver offers exactly the
two readings the available evidence actually supports, selected by
:class:`S490FundingMethod` and named verbatim on every result. Both end
with the same step 3, and both are prototype assumptions:

``TERM_RATE_FROM_CURVE_AS_OF`` (default)
    1. measure the repo term ``days(tS, tF)``;
    2. read the S490 curve at *that term length* from the curve's own
       as-of date -- coordinate ``days(tS, tF) / 365`` -- and take
       ``CarryFactor = 1 / DF(term)``.

    This is the spot-starting term-rate reading, and it is the one Issue
    #173's own GOVY observation supports: "GOVY 3M Repo is approximately
    Curve #490 3M SOFR OIS mid", i.e. a 3-month repo compared against the
    3-month point of the curve, not against a forward-forward rate
    starting at spot settlement. It is the default for that reason.

``FORWARD_FORWARD_DF_RATIO``
    1. resolve ``DF(tS)`` and ``DF(tF)``, each at its own ACT/365F
       coordinate ``(date - curve as-of).days / 365``;
    2. take ``CarryFactor = DF(tS) / DF(tF)``.

    The textbook forward-forward funding reading. Note it needs the curve
    to reach ``tS`` itself: for a T+1 spot settlement against a curve
    whose shortest node is 1W, the existing zero-curve chain refuses to
    extrapolate and this method fails closed rather than inventing a
    short-end rate. That refusal is a real, reportable outcome of the
    experiment -- not something this module works around.

Both methods then share step 3:

    3. express the carry factor as a simple ACT/360 repo rate over the
       same two settlement dates:
       ``rate = (CarryFactor - 1) / (days(tS, tF) / 360)``.

Step 3 is the exact inverse of ``bli_repo_carry_forward``'s own
``1 + r x t`` FPA factor, so feeding ``derived_repo_rate_decimal`` into
that primitive reproduces ``carry_factor`` -- the rate is a readable,
comparable restatement of the same funding, never a second, differently
derived number.

Every discount factor comes from the existing, already-reviewed
``pricing/bli_curve_discount_factor.discount_factor_from_continuous_zero_curve``
chain (linear interpolation on continuous zero rates, then
``exp(-z*t)``); no interpolation, bootstrap, or extrapolation is written
here. When a coordinate lands exactly on an S490 node the interpolation
step is an identity, so Issue #173's "simplest observation case" is this
same code evaluated at a node, not a separate path. **Nothing here is
calibrated, tuned, spread-adjusted, or fitted to any observed OVME
number, and no OVME repo value is hard-coded anywhere in this
repository.** If parity fails for both methods, the residuals are
reported as-is (see ``tools/ust_s490_repo_carry_forward_parity.py``) and
the transformation is replaced -- the whole point of keeping it in its own
module behind one function.

**Curve purpose is the production loader's own row tag, not a claim.**
Curve #490's production rows carry
``BLICurvePurpose.OPTION_DISCOUNT_CURVE``, so that is the purpose this
resolver selects on -- hardcoded, exactly like
``bli_forward_clean_price.py`` hardcodes ``BOND_REFERENCE_CURVE``, so a
caller cannot point this at some other curve by passing a parameter.
Selecting those rows here does **not** change, re-tag, or re-interpret the
option discounting path: ``bli_standalone_option_pricing_inputs.py`` and
every other consumer are untouched by Issue #173. Reading the same
published S490 curve as the repo/carry funding proxy is exactly what Issue
#173 instructs, and it is itself part of the prototype assumption under
test.

**Explicit dates, no calendar.** ``spot_settlement_date``,
``forward_settlement_date``, and ``curve_as_of_date`` are all explicit
caller inputs. Nothing here derives a settlement date from a lag, weekend,
holiday, or business-day convention -- this repository deliberately has no
such derivation -- and the system clock is never read.

**Fail closed.** A target before the curve as-of date, a horizon outside
the curve's node range, a missing/duplicate/wrong-basis curve row, a
non-positive discount factor, or a non-positive repo term all raise
(mostly from the already-reviewed helpers, unchanged). No fallback rate,
no flat extrapolation, no substituted default is ever produced.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from math import isfinite

from shiori_pricing_lab.data._validation import _parse_iso_date
from shiori_pricing_lab.data.bli_snapshot import BLICurvePoint, BLICurvePurpose
from shiori_pricing_lab.pricing.bli_curve_discount_factor import (
    discount_factor_from_continuous_zero_curve,
)
from shiori_pricing_lab.pricing.bli_curve_selector import select_curve_points_by_purpose
from shiori_pricing_lab.pricing.bli_repo_carry_forward import (
    REPO_DAY_COUNT_BASIS_DAYS,
    REPO_DAY_COUNT_CONVENTION,
    repo_term_days,
)
from shiori_pricing_lab.products.enums import Currency, coerce_enum

# The one curve purpose Curve #490's production loader tags its own rows
# with. Hardcoded, not a caller parameter -- see the module docstring.
_S490_CURVE_PURPOSE = BLICurvePurpose.OPTION_DISCOUNT_CURVE


class S490FundingMethod(StrEnum):
    """Which labeled S490 transformation produced a funding number.

    Both members are Issue #173 prototype assumptions -- see the module
    docstring for what each one reads off the curve and for the evidence
    behind the default. The member's own value is carried verbatim onto
    every :class:`S490RepoCarryFunding`, so a reported funding number can
    never be read without its method attached.
    """

    TERM_RATE_FROM_CURVE_AS_OF = "S490_TERM_RATE_FROM_CURVE_AS_OF__SIMPLE_ACT360__PROTOTYPE"
    FORWARD_FORWARD_DF_RATIO = "S490_FORWARD_FORWARD_DF_RATIO__SIMPLE_ACT360__PROTOTYPE"


DEFAULT_S490_FUNDING_METHOD = S490FundingMethod.TERM_RATE_FROM_CURVE_AS_OF

# What was actually read off the curve: the continuous zero-rate
# representation of Curve #490 the production loader publishes.
S490_SOURCE_REPRESENTATION = "BLOOMBERG_CURVE_490_CONTINUOUS_ZERO_RATE_NODES"

# The curve-internal coordinate convention the existing zero-curve chain
# already uses (ACT/365F days/365 from the curve's own as-of date).
# Deliberately distinct from the ACT/360 repo term above: Issue #173
# requires both to be separately visible on every result.
CURVE_COORDINATE_DAY_COUNT_CONVENTION = "ACT/365F"
_CURVE_COORDINATE_BASIS_DAYS = 365.0


@dataclass(frozen=True)
class S490CurveEvaluation:
    """One point actually read off the S490 curve, with its own coordinate.

    ``label`` names the role the evaluation played in the selected method
    (``REPO_TERM_FROM_CURVE_AS_OF``, ``SPOT_SETTLEMENT``,
    ``FORWARD_SETTLEMENT``). ``target_date`` is the calendar date that
    coordinate corresponds to on the curve -- for the term-rate method
    that is ``curve as-of + repo term days``, which is deliberately *not*
    the spot or forward settlement date and is shown here precisely so
    that distinction stays visible.
    """

    label: str
    target_date: str
    curve_year_fraction: float
    discount_factor: float


@dataclass(frozen=True)
class S490RepoCarryFunding:
    """One horizon's S490-derived repo/carry funding, with its whole derivation.

    Every field is either an explicit input echoed back or a value this
    module computed from the S490 curve -- nothing is a default, a
    fallback, or an observed OVME number.
    ``derived_repo_rate_decimal`` is a decimal fraction (``0.0377`` for
    3.77%), simple, on ``repo_day_count_convention``, and is the exact
    inverse of ``carry_factor`` under that convention.
    ``curve_evaluations`` carries every curve read the selected method
    performed, in the order it performed them.
    """

    method: str
    curve_as_of_date: str
    spot_settlement_date: str
    forward_settlement_date: str
    curve_coordinate_day_count_convention: str
    curve_evaluations: tuple[S490CurveEvaluation, ...]
    carry_factor: float
    repo_day_count_convention: str
    repo_term_days: int
    repo_term_year_fraction: float
    derived_repo_rate_decimal: float
    source_representation: str
    curve_ids: tuple[str, ...]
    source_systems: tuple[str, ...]


def _curve_coordinate(curve_as_of_date: str, target_date: str, field_name: str) -> float:
    """Return the ACT/365F curve coordinate from ``curve_as_of_date`` to ``target_date``.

    ``(target - as_of).days / 365`` -- the existing continuous-zero-curve
    coordinate convention every other consumer of this curve chain already
    uses. A target on the curve's own as-of date is ``0.0`` (its discount
    factor is exactly 1.0 and no zero-tenor interpolation is attempted,
    matching ``bli_standalone_option_pricing_inputs``' existing rule); a
    target before it raises.
    """

    as_of = _parse_iso_date(curve_as_of_date, "curve_as_of_date")
    target = _parse_iso_date(target_date, field_name)
    days = (target - as_of).days
    if days < 0:
        raise ValueError(
            f"{field_name} ({target_date!r}) must not be before curve_as_of_date "
            f"({curve_as_of_date!r})"
        )
    return days / _CURVE_COORDINATE_BASIS_DAYS


def _s490_curve_evaluation(
    curve_points: tuple[BLICurvePoint, ...],
    *,
    currency: Currency | str,
    curve_as_of_date: str,
    target_date: str,
    label: str,
    field_name: str,
) -> S490CurveEvaluation:
    """Read the S490 curve once, at ``target_date``, and record that read.

    Delegates to the existing
    ``discount_factor_from_continuous_zero_curve`` chain unchanged, passing
    ``as_of_date`` so Bloomberg's own ``maturity_date`` on each S490 node
    resolves that node's coordinate from a real date rather than a tenor
    label. Raises ``ValueError`` if the resolved discount factor is not
    finite and strictly positive -- a non-positive funding discount factor
    is never used to produce a carry factor.
    """

    coordinate = _curve_coordinate(curve_as_of_date, target_date, field_name)
    if coordinate == 0.0:
        return S490CurveEvaluation(
            label=label,
            target_date=target_date,
            curve_year_fraction=0.0,
            discount_factor=1.0,
        )

    discount_factor = discount_factor_from_continuous_zero_curve(
        curve_points,
        currency=currency,
        curve_purpose=_S490_CURVE_PURPOSE,
        target_year_fraction=coordinate,
        as_of_date=curve_as_of_date,
    )
    if (
        isinstance(discount_factor, bool)
        or not isinstance(discount_factor, (int, float))
        or not isfinite(discount_factor)
        or not discount_factor > 0
    ):
        raise ValueError(
            f"S490 discount factor to {field_name} ({target_date!r}) must be a finite "
            f"positive number, got {discount_factor!r}"
        )
    return S490CurveEvaluation(
        label=label,
        target_date=target_date,
        curve_year_fraction=coordinate,
        discount_factor=float(discount_factor),
    )


def resolve_s490_repo_carry_funding(
    curve_points: Iterable[BLICurvePoint],
    *,
    currency: Currency | str,
    curve_as_of_date: str,
    spot_settlement_date: str,
    forward_settlement_date: str,
    method: S490FundingMethod | str = DEFAULT_S490_FUNDING_METHOD,
) -> S490RepoCarryFunding:
    """Resolve the S490 repo/carry funding for one ``tS -> tF`` horizon.

    Applies exactly the labeled transformation named by ``method`` (see the
    module docstring for both), then the shared simple-ACT/360 step, to the
    production Curve #490 rows in ``curve_points``. Every error the
    existing curve chain raises -- no such curve/currency, a row whose
    ``rate_basis`` is not ``CONTINUOUS_ZERO_RATE``, a target outside the
    curve's own node range, a Bloomberg ``maturity_date`` row with no
    ``as_of_date`` -- propagates unchanged; this resolver never
    extrapolates past the curve or substitutes a fallback rate. Raises
    ``ValueError`` for an unknown ``method``.
    """

    method = coerce_enum(method, S490FundingMethod, "method")
    curve_points = tuple(curve_points)

    term_days = repo_term_days(spot_settlement_date, forward_settlement_date)
    term_year_fraction = term_days / REPO_DAY_COUNT_BASIS_DAYS

    if method is S490FundingMethod.TERM_RATE_FROM_CURVE_AS_OF:
        # The curve is read once, at the repo term's own length measured
        # from the curve's as-of date -- a spot-starting term rate, not a
        # forward-forward. The date shown is that coordinate's own calendar
        # date, which is deliberately neither settlement date.
        term_target_date = (
            _parse_iso_date(curve_as_of_date, "curve_as_of_date") + timedelta(days=term_days)
        ).isoformat()
        term_evaluation = _s490_curve_evaluation(
            curve_points,
            currency=currency,
            curve_as_of_date=curve_as_of_date,
            target_date=term_target_date,
            label="REPO_TERM_FROM_CURVE_AS_OF",
            field_name="repo term from curve_as_of_date",
        )
        evaluations = (term_evaluation,)
        carry_factor = 1.0 / term_evaluation.discount_factor
    else:
        spot_evaluation = _s490_curve_evaluation(
            curve_points,
            currency=currency,
            curve_as_of_date=curve_as_of_date,
            target_date=spot_settlement_date,
            label="SPOT_SETTLEMENT",
            field_name="spot_settlement_date",
        )
        forward_evaluation = _s490_curve_evaluation(
            curve_points,
            currency=currency,
            curve_as_of_date=curve_as_of_date,
            target_date=forward_settlement_date,
            label="FORWARD_SETTLEMENT",
            field_name="forward_settlement_date",
        )
        evaluations = (spot_evaluation, forward_evaluation)
        carry_factor = spot_evaluation.discount_factor / forward_evaluation.discount_factor

    derived_repo_rate = (carry_factor - 1.0) / term_year_fraction

    selected_points = select_curve_points_by_purpose(
        curve_points,
        currency=currency,
        curve_purpose=_S490_CURVE_PURPOSE,
    )
    curve_ids = tuple(sorted({point.curve_id for point in selected_points}))
    source_systems = tuple(sorted({point.source_system for point in selected_points}))

    return S490RepoCarryFunding(
        method=method.value,
        curve_as_of_date=curve_as_of_date,
        spot_settlement_date=spot_settlement_date,
        forward_settlement_date=forward_settlement_date,
        curve_coordinate_day_count_convention=CURVE_COORDINATE_DAY_COUNT_CONVENTION,
        curve_evaluations=evaluations,
        carry_factor=carry_factor,
        repo_day_count_convention=REPO_DAY_COUNT_CONVENTION,
        repo_term_days=term_days,
        repo_term_year_fraction=term_year_fraction,
        derived_repo_rate_decimal=derived_repo_rate,
        source_representation=S490_SOURCE_REPRESENTATION,
        curve_ids=curve_ids,
        source_systems=source_systems,
    )
