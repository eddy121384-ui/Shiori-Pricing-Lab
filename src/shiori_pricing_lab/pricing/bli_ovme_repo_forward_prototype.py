"""OVME flat-repo forward clean price prototype (Issue #94 diagnostic).

**RED PROTOTYPE, not production.** This module exists solely to test whether
an explicit flat repo rate reproduces the current Bloomberg OVME US Treasury
diagnostic case's forward clean price. It is executable diagnostic code,
isolated from every production pricing path, and does **not** authorize a
production methodology change, engine wiring, or a claim of Bloomberg
validation.

**Current approved prototype methodology (Issue #94 diagnostic slice):**

```text
Repo quote convention: ACT/365, semi-annual compounding.

t = actual_calendar_days(spot_settlement_date, forward_settlement_date) / 365
DF_repo(S, D) = (1 + repo_rate / 2) ** (-2 * t)

spot_dirty     = spot_clean_price + accrued_interest_at_spot_settlement
forward_dirty  = spot_dirty / DF_repo
forward_clean  = forward_dirty - accrued_interest_at_forward_settlement
```

**Current prototype scope cap:** no coupon payment may occur in
``(spot_settlement_date, forward_settlement_date]``. A bond/window pair with
an intervening coupon is refused outright (``ValueError``) rather than
approximated with a coupon-carry adjustment, which this prototype does not
implement.

**Reuse, not reimplementation:** the four-line forward/dirty/clean arithmetic
above is exactly Annex A §A.5.2's already-reviewed
``pricing.bli_forward_clean_price._forward_clean_price_from_components``,
called here with ``coupon_pv=0.0`` (enforcing the no-coupon scope cap) and
this module's own flat-repo ``DF_repo`` in place of a Bond Reference Curve
discount factor. That private helper's existing contract and behavior are
unchanged; this module only calls it, and does not modify
``bli_forward_clean_price.py``. Coupon detection and accrued interest reuse
the already-reviewed ``pricing.bli_quantlib_bond_adapter.coupon_flows_before``
/ ``accrued_interest_per_100`` exactly as-is.

**Zero composition beyond that reuse:** this module does not implement or
duplicate Black-76, does not import ``pricing.bli_pricing_engine`` or
``pricing.bli_black76_price_option``, does not import any request / builder /
workbench / UI module, and is not imported by, and does not change the
behavior of, any of them. Not wired into any production engine.

**No system clock:** every date this module touches is an explicit
caller-supplied ISO date string -- the current wall-clock date is never
read. Only stdlib ``datetime.date`` parsing and arithmetic is used; no new
dependency is added.

**No real Bloomberg data:** this module and its tests contain no committed
Bloomberg screenshot, export, or real market value -- only synthetic,
already-reviewed fixture-shaped inputs.
"""

from __future__ import annotations

import re
from datetime import date
from math import isfinite

from shiori_pricing_lab.pricing.bli_forward_clean_price import (
    _forward_clean_price_from_components,
)
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import (
    accrued_interest_per_100,
    coupon_flows_before,
)
from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData

_ISO_DATE_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _parse_iso_date(value: str, field_name: str) -> date:
    # Local, deliberately duplicated ISO-date parser (mirrors the same
    # small-helper-duplication convention already used across this
    # package's other modules) -- this prototype module has no import
    # relationship with any other module's private date parser.
    if not isinstance(value, str) or not _ISO_DATE_SHAPE.fullmatch(value):
        raise ValueError(f"{field_name} must be an ISO date string (YYYY-MM-DD), got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must be an ISO date string (YYYY-MM-DD), got {value!r}"
        ) from exc


def _require_finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    if not isfinite(value):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    return float(value)


def ovme_flat_repo_forward_clean_price_no_coupon_per_100(
    *,
    bond: BondReferenceData,
    spot_clean_price: float,
    spot_settlement_date: str,
    forward_settlement_date: str,
    repo_rate: float,
) -> float:
    """Return the flat-repo forward clean price per 100 for a no-coupon horizon.

    Diagnostic-only prototype: reproduces one observed Bloomberg OVME case's
    forward clean price using an explicit flat repo rate (ACT/365,
    semi-annual compounding) in place of the Bond Reference Curve. Raises
    ``TypeError`` for a non-``BondReferenceData`` ``bond``. Raises
    ``ValueError`` for: a non-finite or non-positive ``spot_clean_price``; a
    malformed or reversed settlement-date pair (``forward_settlement_date``
    must be strictly after ``spot_settlement_date``); a non-finite
    ``repo_rate`` whose implied semi-annual compounding base
    (``1 + repo_rate / 2``) is not strictly positive (a non-positive base
    raised to the non-integer power ``-2 * t`` silently produces a complex
    number in Python rather than failing, so this is checked explicitly); or
    any coupon payment falling in
    ``(spot_settlement_date, forward_settlement_date]`` -- this prototype's
    explicit no-coupon scope cap, refused outright rather than approximated
    with a coupon-carry adjustment.
    """

    if not isinstance(bond, BondReferenceData):
        raise TypeError(f"bond must be a BondReferenceData, got {type(bond).__name__}")

    spot_clean_price = _require_finite_number(spot_clean_price, "spot_clean_price")
    if not spot_clean_price > 0:
        raise ValueError(f"spot_clean_price must be positive, got {spot_clean_price!r}")

    spot_date = _parse_iso_date(spot_settlement_date, "spot_settlement_date")
    forward_date = _parse_iso_date(forward_settlement_date, "forward_settlement_date")
    if forward_date <= spot_date:
        raise ValueError(
            f"forward_settlement_date ({forward_settlement_date!r}) must be strictly after "
            f"spot_settlement_date ({spot_settlement_date!r})"
        )

    repo_rate = _require_finite_number(repo_rate, "repo_rate")
    compounding_base = 1.0 + repo_rate / 2.0
    if not compounding_base > 0:
        raise ValueError(
            "repo_rate implies a non-positive semi-annual compounding base "
            f"(1 + repo_rate / 2 = {compounding_base!r}) -- this prototype refuses to raise "
            "a non-positive base to a non-integer power rather than silently producing a "
            f"complex number, got repo_rate={repo_rate!r}"
        )

    intervening_coupons = coupon_flows_before(
        bond, after_date=spot_settlement_date, on_or_before_date=forward_settlement_date
    )
    if intervening_coupons:
        raise ValueError(
            "this prototype does not support intervening coupons: bond "
            f"{bond.isin!r} has {len(intervening_coupons)} coupon payment(s) in "
            f"(spot_settlement_date={spot_settlement_date!r}, "
            f"forward_settlement_date={forward_settlement_date!r}] "
            f"({[flow.payment_date for flow in intervening_coupons]!r}) -- a coupon-carry "
            "adjustment is out of scope for this diagnostic prototype"
        )

    accrued_interest_at_spot = accrued_interest_per_100(bond, as_of_date=spot_settlement_date)
    accrued_interest_at_forward = accrued_interest_per_100(
        bond, as_of_date=forward_settlement_date
    )

    actual_days = (forward_date - spot_date).days
    t = actual_days / 365.0
    discount_factor_repo = compounding_base ** (-2.0 * t)

    return _forward_clean_price_from_components(
        spot_clean_price=spot_clean_price,
        accrued_interest_at_valuation=accrued_interest_at_spot,
        coupon_pv=0.0,
        expiry_discount_factor=discount_factor_repo,
        accrued_interest_at_expiry=accrued_interest_at_forward,
    )
