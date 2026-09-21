"""Issue #217: the first plain U.S. corporate bond option priced end to end.

One narrow slice, and the tests here exist to pin its two halves:

- what the corporate path *may* do -- price through the same reviewed
  Black-76 engine, discounting curve, basis contract, Greeks, benchmark and
  export that UST already uses, from an explicit trader Forward Clean Price
  and a direct PRICE_VOL;
- what it may *not* do -- reach the S490 repo-carry Forward (a U.S. Treasury
  model), or the Historical-Yield-derived volatility path, by any route
  including a direct API payload that never went near the browser.

The bond is `US61760QRP18`, the plain USD corporate bullet Issue #216
admitted on real Bloomberg workstation evidence: fixed 5.15 semi-annual,
30/360, issued 2025-02-10, first coupon 2025-08-10, maturing 2040-02-10,
non-callable / non-sinkable / non-convertible / non-inflation-linked, with
`MTY_TYP` "AT MATURITY". Its structural admission is tested in
`test_bli_bond_advanced_field_resolver.py`; what is tested here is what
happens *after* it is admitted.

Every market number below -- the Forward, the strike, the volatility, the
curve -- is a synthetic test value carried by
`examples/standalone_option_case.json`, not a Bloomberg observation. Only
the bond's own confirmed terms are real, and no Bloomberg price, yield or
daily series is committed anywhere in this repository.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shiori_pricing_lab.app import standalone_option_workbench_server as server_module
from shiori_pricing_lab.app.standalone_option_run_export import (
    render_standalone_run_as_json,
    render_standalone_run_as_markdown,
)
from shiori_pricing_lab.app.standalone_option_workbench import price_standalone_option_case
from shiori_pricing_lab.app.standalone_option_workbench_server import (
    apply_effective_forward_to_case,
    historical_equivalent_price_vol_preview,
)
from shiori_pricing_lab.pricing.bli_effective_forward import (
    S490_DERIVED_FORWARD_CONVENTION_PROFILES,
    S490ForwardConventionProfileError,
    supports_s490_derived_forward,
)

try:  # QuantLib is the optional 'quant' extra; the whole slice needs it.
    import QuantLib  # noqa: F401

    _QUANTLIB_SKIP = pytest.mark.skipif(False, reason="")
except ImportError:  # pragma: no cover
    _QUANTLIB_SKIP = pytest.mark.skip(reason="QuantLib is not installed")

_EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "standalone_option_case.json"

_CORPORATE_ISIN = "US61760QRP18"
_DERIVED_FORWARD_SOURCE = "SHIORI_DERIVED_S490"
_TRADER_FORWARD_OVERRIDE_SOURCE = "TRADER_FORWARD_OVERRIDE"

# Issue #216's confirmed Bloomberg terms, as the typed reference record the
# Advanced-field resolver produces from them. `day_count` and
# `ex_dividend_days` are the `US_CORPORATE` profile's own constants, which is
# what the workbench fills without the trader hand-entering anything.
_CORPORATE_BOND = {
    "isin": _CORPORATE_ISIN,
    "issuer": "Issue #217 UAT security (issuer not asserted)",
    "currency": "USD",
    "coupon": 0.0515,
    "coupon_frequency": "SEMI_ANNUAL",
    "maturity_date": "2040-02-10",
    "issue_date": "2025-02-10",
    "day_count": "THIRTY_360",
    "callable_flag": False,
    "sinkable_flag": False,
    "bond_type": "FIXED_COUPON_BULLET",
    "ex_dividend_days": 0,
    "first_coupon_date": "2025-08-10",
    "last_coupon_date": "2039-08-10",
    "status": "ACTIVE",
}

_EXPLICIT_FORWARD_PER_100 = 98.75


def _corporate_case(**overrides) -> dict:
    """The example case, re-pointed at the confirmed corporate bullet.

    Everything the corporate slice needs and nothing it does not: the same
    synthetic curve, quote, valuation date and option terms the UST example
    carries, with the bond swapped, `US_CORPORATE` selected, and the Forward
    supplied explicitly -- which is the only Forward source this issue opens
    for this market.
    """

    case = json.loads(_EXAMPLE_PATH.read_text())
    case["bond_reference_data_universe"] = [dict(_CORPORATE_BOND)]
    case["bond_option"] = {**case["bond_option"], "underlying_isin": _CORPORATE_ISIN}
    case["bond_quote"] = {**case["bond_quote"], "isin": _CORPORATE_ISIN}
    case["convention_profile"] = "US_CORPORATE"
    case["forward_clean_price_input"] = {
        "forward_clean_price_per_100": _EXPLICIT_FORWARD_PER_100,
        "quote_side": "MID",
        "source_system": _TRADER_FORWARD_OVERRIDE_SOURCE,
        "status": "ACTIVE",
    }
    case["bond_option_price_basis"] = "DIRTY"
    case.update(overrides)
    return case


# --- The Forward this market may and may not use ------------------------------


def test_only_explicitly_approved_profiles_reach_the_s490_forward():
    """Fail-closed, and by exact name.

    A missing, blank, unknown, differently-cased or whitespace-padded
    selection must not inherit UST's behaviour -- which is exactly what a
    truthiness test or a normalizing comparison would let it do."""

    assert S490_DERIVED_FORWARD_CONVENTION_PROFILES == ("UST",)
    assert supports_s490_derived_forward("UST") is True
    for rejected in (None, "", "   ", "ust", " UST ", "US_CORPORATE", "GERMAN_GOVT", 1, True):
        assert supports_s490_derived_forward(rejected) is False, rejected


def test_a_corporate_declaring_the_derived_forward_is_refused_before_any_derivation():
    """Issue #217's server-side rule, on the function the pricing path calls.

    The refusal happens on the declared source alone -- no curve, no funding
    resolution, no repo-carry primitive -- so a direct API payload that never
    went near the browser cannot reach the Treasury model either."""

    case = _corporate_case(
        forward_clean_price_input={
            "forward_clean_price_per_100": None,
            "quote_side": "MID",
            "source_system": _DERIVED_FORWARD_SOURCE,
            "status": "ACTIVE",
        },
        spot_settlement_date="2026-09-22",
    )

    with pytest.raises(S490ForwardConventionProfileError) as excinfo:
        apply_effective_forward_to_case(case)

    message = str(excinfo.value)
    assert "US_CORPORATE" in message
    assert _DERIVED_FORWARD_SOURCE in message
    assert _TRADER_FORWARD_OVERRIDE_SOURCE in message


@pytest.mark.parametrize(
    ("spot_settlement_date", "forward_settlement_date", "horizon"),
    [
        # Case A: no coupon scheduled in (tS, tF]. Before Issue #217 this
        # resolved silently and produced a UST repo-carry Forward for a
        # corporate -- the quiet half of the defect, and the half a trader
        # could not have seen.
        ("2026-09-22", "2026-12-22", "no interim coupon"),
        # Case B: 2027-02-10 falls in the window. Before Issue #217 this
        # refused, but only by reaching the Treasury coupon-payment
        # convention deep inside the primitive -- an accident of where the
        # coupon fell, not a rule about which market may use the model.
        ("2027-01-22", "2027-03-22", "interim coupon in the window"),
    ],
)
def test_both_audited_s490_horizons_are_refused_by_the_same_rule(
    spot_settlement_date, forward_settlement_date, horizon
):
    """The two horizons Issue #217's audit found, refused identically.

    Same rule, same place, same reason, whether or not a coupon happens to
    fall in the window: the S490 model does not apply to this market."""

    case = _corporate_case(
        forward_clean_price_input={
            "forward_clean_price_per_100": None,
            "quote_side": "MID",
            "source_system": _DERIVED_FORWARD_SOURCE,
            "status": "ACTIVE",
        },
        spot_settlement_date=spot_settlement_date,
        forward_settlement_date=forward_settlement_date,
    )

    with pytest.raises(S490ForwardConventionProfileError) as excinfo:
        apply_effective_forward_to_case(case)

    # Not the coupon-convention refusal, whichever horizon this is.
    assert "RepoCarryInterimCouponConventionError" not in str(excinfo.value)


def test_a_corporate_override_run_attaches_no_s490_derivation_trace():
    """An override on this market prices, and carries no S490 anything.

    Not merely "the derivation failed": it is never attempted, so no live
    Curve #490 is acquired for it and the provenance carries no repo-carry
    trace to read as evidence that Shiori has a corporate Forward model. It
    does not."""

    case = _corporate_case(spot_settlement_date="2026-09-22")

    effective_case, provenance = apply_effective_forward_to_case(case)

    assert provenance["forward_source"] == _TRADER_FORWARD_OVERRIDE_SOURCE
    assert provenance["effective_forward_clean_price_per_100"] == _EXPLICIT_FORWARD_PER_100
    assert provenance["shiori_derived_forward"] is None
    assert provenance["shiori_derived_forward_clean_price_per_100"] is None
    assert provenance["shiori_derived_forward_curve_acquired"] is False
    # The reason there is no comparison value is stated rather than left blank.
    assert "no automatic Forward model" in provenance["shiori_derived_forward_error"]
    # An override's own input is carried through untouched.
    assert effective_case["forward_clean_price_input"] == case["forward_clean_price_input"]


def test_the_ust_derived_forward_default_is_untouched():
    """The other half of the rule: UST still reaches its own model.

    Asserted on the eligibility decision rather than by pricing a UST case,
    which the existing Issue #177/#178 suites already cover end to end."""

    assert supports_s490_derived_forward("UST") is True


# --- Pricing the supported corporate case -------------------------------------


@_QUANTLIB_SKIP
def test_a_supported_corporate_prices_through_the_existing_engine():
    """Issue #217's headline: explicit Forward + direct PRICE_VOL prices.

    Through the same engine UST uses -- asserted by name, because "reuse the
    existing engine" is the requirement, not an implementation detail."""

    _, _, display, _priced = price_standalone_option_case(_corporate_case())

    assert display["status"] == "SUCCESS"
    assert display["errors"] == []
    assert display["engine_name"] == "bli_standalone_bond_option_ovme_black76_engine"
    assert display["method"] == "black76_forward_dirty_price_ovme_v1"
    assert display["forward_source"] == _TRADER_FORWARD_OVERRIDE_SOURCE
    assert display["forward_clean_price_per_100"] == _EXPLICIT_FORWARD_PER_100
    assert display["result_currency"] == "USD"


@_QUANTLIB_SKIP
def test_the_corporate_run_produces_a_premium_and_every_greek():
    """Premium, Delta, Gamma, Vega and Theta, per 100 and position-total.

    Values are asserted as finite and correctly signed rather than pinned to
    literals: the numbers belong to the engine, which this issue does not
    change, and pinning them here would create a second place to update when
    an unrelated engine test legitimately moves one."""

    _, _, display, _priced = price_standalone_option_case(_corporate_case())

    assert display["model_fair_premium_per_100"] > 0
    assert display["total_notional_model_fair_premium"] > 0
    # A bought call: positive delta and gamma, positive vega, negative theta.
    assert display["forward_price_delta_per_100"] > 0
    assert display["forward_price_gamma_per_100"] > 0
    assert display["vega_per_vol_point_per_100"] > 0
    assert display["theta_per_calendar_day_per_100"] < 0
    assert display["position"] == "BUY"
    assert display["position_forward_price_delta_total"] > 0
    assert display["position_vega_per_vol_point_total"] > 0
    assert display["position_theta_per_calendar_day_total"] < 0
    assert display["greeks_units"]


@_QUANTLIB_SKIP
@pytest.mark.parametrize("basis", ["DIRTY", "CLEAN"])
def test_the_forward_and_strike_are_on_one_basis_end_to_end(basis):
    """CLEAN/DIRTY consistency, checked as the invariant rather than by eye.

    Accrued interest is yield-independent, so the two legs share it exactly:
    whichever basis is declared, `model_forward - model_strike` must equal
    `forward_clean - strike_clean`. A mixed-basis composition -- a dirty
    forward against a clean strike -- breaks that identity by one accrual,
    which is precisely the reconciliation break Issue #211 made the basis
    explicit to prevent."""

    case = _corporate_case(bond_option_price_basis=basis)
    _, _, display, _priced = price_standalone_option_case(case)

    assert display["bond_option_price_basis"] == basis
    assert display["priced_bond_option_price_basis"] == basis

    strike_clean = case["bond_option"]["strike_price"]
    model_forward = display["model_forward_price_per_100"]
    model_strike = display["model_strike_price_per_100"]
    accrued = model_forward - _EXPLICIT_FORWARD_PER_100

    assert model_strike - strike_clean == pytest.approx(accrued)
    assert model_forward - model_strike == pytest.approx(
        _EXPLICIT_FORWARD_PER_100 - strike_clean
    )
    if basis == "CLEAN":
        assert accrued == pytest.approx(0.0)
    else:
        assert accrued > 0


@_QUANTLIB_SKIP
def test_the_corporate_run_exports_through_the_existing_contract():
    """Export/benchmark contract, unchanged by this issue.

    The run export is built from the same display payload every other run
    produces, so the check that matters is that a corporate run fills the
    same named fields rather than a parallel corporate shape."""

    _, _, display, _priced = price_standalone_option_case(_corporate_case())
    markdown = render_standalone_run_as_markdown(display)
    exported = json.loads(render_standalone_run_as_json(display))

    # The same named sections every other run exports, filled by a corporate
    # run -- not a parallel corporate shape.
    assert "Forward source" in markdown
    assert _TRADER_FORWARD_OVERRIDE_SOURCE in markdown
    assert _DERIVED_FORWARD_SOURCE not in markdown
    assert exported["forward_source"] == _TRADER_FORWARD_OVERRIDE_SOURCE
    assert exported["model_fair_premium_per_100"] > 0
    assert exported["bond_option_price_basis"] == "DIRTY"


def test_a_corporate_without_an_explicit_forward_is_blocked_and_nothing_is_substituted():
    """The corollary of requiring an explicit Forward: a missing one blocks.

    This market has no automatic Forward to fall back to, and Shiori does not
    invent one -- not the spot clean price, not the last value this ticket
    held, not a zero-carry forward. The declared override contract refuses
    the run on its own terms."""

    case = _corporate_case(
        forward_clean_price_input={
            "forward_clean_price_per_100": None,
            "quote_side": "MID",
            "source_system": _TRADER_FORWARD_OVERRIDE_SOURCE,
            "status": "ACTIVE",
        }
    )

    with pytest.raises(ValueError) as excinfo:
        apply_effective_forward_to_case(case)

    assert "forward_clean_price_per_100" in str(excinfo.value)


def test_the_historical_yield_vol_source_stays_closed_for_a_corporate(monkeypatch):
    """Issue #217 volatility scope, enforced where a direct payload meets it.

    The accepted source for this slice is the direct PRICE_VOL the tests
    above price from. `HISTORICAL_YIELD_VOL_MO` is Issue #218's, and a case
    asking for it here is refused at the workbench composition boundary --
    by the duration producer's own profile allowlist, which this issue does
    not touch and the generic Black-76 engine knows nothing about.

    Proven to happen *before* acquisition, not after: the #196 Yield loader
    is never called. Hiding the option in the browser would not have shown
    that, which is the point."""

    calls: list = []

    def _never_loaded(**kwargs):
        calls.append(kwargs)
        raise AssertionError("the Yield series must not be acquired for this case")

    monkeypatch.setattr(server_module, "load_bloomberg_bond_yield_history", _never_loaded)

    case = _corporate_case()
    case["historical_yield_vol_request"] = {
        "bond_identifier": _CORPORATE_ISIN,
        "yield_field": "YLD_YTM_MID",
        "start_date": "2025-07-01",
        "end_date": "2026-06-30",
        "field_unit": "PERCENT",
    }

    with pytest.raises(Exception) as excinfo:
        historical_equivalent_price_vol_preview(case)

    message = str(excinfo.value)
    assert "US_CORPORATE" in message
    assert "HISTORICAL_YIELD_VOL_MO" in message
    assert calls == []


# --- Everything outside the slice stays closed --------------------------------


@_QUANTLIB_SKIP
def test_a_callable_corporate_is_still_refused_by_the_pricing_eligibility_gate():
    """Unsupported structures remain fail-closed on the pricing path too.

    The admission gate refuses a callable bond before a case exists at all
    (Issue #216); this is the second, independent refusal that does not
    depend on it."""

    case = _corporate_case()
    case["bond_reference_data_universe"] = [{**_CORPORATE_BOND, "callable_flag": True}]

    with pytest.raises(Exception) as excinfo:
        price_standalone_option_case(case)

    assert "callable" in str(excinfo.value).lower()
