"""Issue #217: the first plain U.S. corporate bond option priced end to end.

One narrow slice, and the tests here exist to pin its two halves:

- what the corporate path *may* do -- price through the same reviewed
  Black-76 engine, discounting curve, basis contract, Greeks, benchmark and
  export that UST already uses, from an explicit trader Forward Clean Price
  and a direct PRICE_VOL;
- what it may *not* do -- reach the S490 repo-carry Forward (a U.S. Treasury
  model) by any route, including a direct API payload that never went near
  the browser. (The Historical-Yield-derived volatility path this slice kept
  closed was opened by Issue #218 on the reconciled 30/360 BondBasis
  convention; see the one test below that records the handover.)

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

from shiori_pricing_lab.app import standalone_option_historical_vol_source as source_module
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
from shiori_pricing_lab.pricing.bli_bond_convention_profile import (
    US_CORPORATE_CONVENTION_PROFILE,
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


def test_readiness_refuses_the_same_case_pricing_would(monkeypatch):
    """Readiness and pricing must not disagree about the same case.

    The eligibility rule is local, deterministic and free, while the routes
    that would otherwise reach it first fetch a live Option Discount Curve
    and, on a refresh, a fresh quote. Checking it only inside the derivation
    meant readiness answered "ready" for a case pricing was always going to
    refuse, and meant a Bloomberg outage got reported in place of the real
    reason. Same rule, same answer, before either."""

    case = _corporate_case(
        forward_clean_price_input={
            "forward_clean_price_per_100": None,
            "quote_side": "MID",
            "source_system": _DERIVED_FORWARD_SOURCE,
            "status": "ACTIVE",
        },
        spot_settlement_date="2026-09-22",
    )

    def _never(*args, **kwargs):
        raise AssertionError("no curve may be acquired before this refusal")

    monkeypatch.setattr(
        server_module, "load_bloomberg_usd_sofr_option_discount_curve", _never
    )

    with pytest.raises(S490ForwardConventionProfileError):
        server_module.validate_deterministic_forward_inputs(case)


def test_readiness_leaves_an_approved_market_and_an_explicit_corporate_alone():
    """The same pre-flight is a no-op for every case it does not govern."""

    # An explicit corporate Forward: nothing about the S490 model applies.
    server_module.validate_deterministic_forward_inputs(_corporate_case())
    # A UST case in derived mode reaches its own model's other checks, not
    # this one -- proven by it raising something else, or nothing at all.
    ust = _corporate_case(
        convention_profile="UST",
        forward_clean_price_input={
            "forward_clean_price_per_100": None,
            "quote_side": "MID",
            "source_system": _DERIVED_FORWARD_SOURCE,
            "status": "ACTIVE",
        },
        spot_settlement_date="2026-09-22",
    )
    try:
        server_module.validate_deterministic_forward_inputs(ust)
    except S490ForwardConventionProfileError:  # pragma: no cover - the regression
        pytest.fail("UST must not be refused by the S490 eligibility rule")
    except ValueError:
        pass  # any other pre-flight refusal is this test's business to ignore


def test_the_eligibility_rule_is_published_for_clients_rather_than_copied(monkeypatch):
    """The browser reads this rule; it must not keep its own copy of it.

    The same reasoning as ``supported_convention_profiles``: a second copy of
    a rule goes stale exactly the way a second copy of the registry does, and
    the repository already forbids the latter in ``script.js``."""

    payload = server_module.resolve_bond_convention_profile_candidates(
        {"currency": "USD", "bond_master": {"coupon_frequency": "SEMI_ANNUAL"}}
    )

    assert payload["s490_derived_forward_convention_profiles"] == list(
        S490_DERIVED_FORWARD_CONVENTION_PROFILES
    )
    # Published, never a claim that every candidate may use it.
    assert "US_CORPORATE" in payload["candidates"]
    assert "US_CORPORATE" not in payload["s490_derived_forward_convention_profiles"]


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


def test_the_historical_yield_vol_source_admits_a_corporate_before_acquisition(monkeypatch):
    """Issue #218 opened what Issue #217 kept closed, and nothing else.

    Issue #217 refused ``HISTORICAL_YIELD_VOL_MO`` for this market at the
    duration producer's profile allowlist, before any Yield series was
    acquired. Issue #218 approved ``US_CORPORATE`` on its reconciled 30/360
    BondBasis convention, so every date-only gate now admits the case and the
    first thing it meets is the #196 loader -- stubbed here to stop there,
    so no Bloomberg session is opened and the call itself is the evidence.
    The full corporate chain is priced end to end in the historical-source
    server tests."""

    class _ReachedTheLoader(Exception):
        pass

    calls: list = []

    def _stop_at_the_loader(**kwargs):
        calls.append(kwargs)
        raise _ReachedTheLoader

    monkeypatch.setattr(source_module, "load_bloomberg_bond_yield_history", _stop_at_the_loader)

    case = _corporate_case()
    case["volatility_input"] = {
        **case["volatility_input"],
        "volatility": None,
        "volatility_basis": "EQUIVALENT_PRICE_VOL",
        "source_system": "HISTORICAL_YIELD_VOL_MO",
    }
    case["historical_yield_vol_request"] = {
        "bond_identifier": _CORPORATE_ISIN,
        "yield_field": "YLD_YTM_MID",
        "start_date": "2025-07-01",
        "end_date": "2026-06-30",
        "field_unit": "PERCENT",
    }

    with pytest.raises(_ReachedTheLoader):
        historical_equivalent_price_vol_preview(case)

    assert len(calls) == 1
    assert calls[0]["identifier"] == f"/isin/{_CORPORATE_ISIN}"


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


# --- Issue #217 follow-up: the option's timing is not the cash bond's --------


def _priced(case: dict) -> dict:
    _, _, display, _priced_case = price_standalone_option_case(case)
    return display


@_QUANTLIB_SKIP
def test_the_corporate_cash_bond_t_plus_2_never_reaches_the_option_dates():
    """The workstation finding, on the pricing path.

    `US_CORPORATE.settlement_business_days` is the cash bond's T+2 spot
    settlement convention. The option's own settlement dates are explicit
    trade inputs, and the run prices from exactly what the ticket states --
    no lag is applied to them on the way in, so a date two business days
    after expiry appears only if the trader put it there."""

    assert US_CORPORATE_CONVENTION_PROFILE.settlement_business_days == 2

    case = _corporate_case(
        forward_settlement_date="2026-10-01",
        option_settlement_date="2026-10-01",
    )
    display = _priced(case)

    assert display["status"] == "SUCCESS"
    assumptions = display["assumptions"]
    assert assumptions["forward_settlement_date"] == "2026-10-01"
    assert assumptions["option_settlement_date"] == "2026-10-01"


@_QUANTLIB_SKIP
@pytest.mark.parametrize("delivery_delay", [None, 0, 1, 2])
def test_a_recorded_delivery_delay_derives_neither_date_and_blocks_nothing(delivery_delay):
    """Issue #217 follow-up: Delivery Delay is recorded, and inert.

    The same ticket prices identically with no Delivery Delay, with the 1
    OVME showed for this bond, and with any other value: it derives neither
    settlement date, reaches no pricing arithmetic, and its absence blocks
    nothing. The calendar semantics that would be needed to turn it into
    either date -- what the integer counts, on which calendar, from which
    date -- are established nowhere in this repository, which is exactly why
    it counts nothing here."""

    case = _corporate_case(
        forward_settlement_date="2026-10-01",
        option_settlement_date="2026-10-01",
    )
    case["bond_option"] = {**case["bond_option"], "settlement_lag_days": delivery_delay}

    display = _priced(case)

    assert display["status"] == "SUCCESS"
    assert display["option_delivery_delay_days_recorded"] == delivery_delay
    # Neither date moved with it, and the premium did not either.
    assert display["assumptions"]["forward_settlement_date"] == "2026-10-01"
    assert display["assumptions"]["option_settlement_date"] == "2026-10-01"
    assert display["model_fair_premium_per_100"] == pytest.approx(
        _priced(
            _corporate_case(
                forward_settlement_date="2026-10-01",
                option_settlement_date="2026-10-01",
            )
        )["model_fair_premium_per_100"]
    )


@_QUANTLIB_SKIP
def test_the_two_explicit_settlement_dates_may_differ():
    """They are separate authoritative inputs, and the contract says so:
    "deliberately kept distinct ... never requires them to be equal".

    A run with a bond delivery on one date and the option's own cash
    settlement on another prices, and each date reaches the leg that uses
    it -- the accrued interest sits on the forward settlement date, the
    discount factor on the option settlement date."""

    same = _priced(
        _corporate_case(
            forward_settlement_date="2026-10-01",
            option_settlement_date="2026-10-01",
        )
    )
    differing = _priced(
        _corporate_case(
            forward_settlement_date="2026-10-01",
            option_settlement_date="2026-10-05",
        )
    )

    assert differing["status"] == "SUCCESS"
    assert differing["assumptions"]["forward_settlement_date"] == "2026-10-01"
    assert differing["assumptions"]["option_settlement_date"] == "2026-10-05"
    # The forward leg is untouched by the option's own settlement date...
    assert (
        differing["assumptions"]["accrued_interest_at_forward_settlement_per_100"]
        == same["assumptions"]["accrued_interest_at_forward_settlement_per_100"]
    )
    # ...and the discounting leg genuinely moved with it.
    assert (
        differing["assumptions"]["pricing_to_option_settlement_discount_factor"]
        != same["assumptions"]["pricing_to_option_settlement_discount_factor"]
    )


@_QUANTLIB_SKIP
def test_the_recorded_delivery_delay_is_exported_as_a_recorded_term():
    """It reaches the audit trail, named for what it is and where it stops."""

    case = _corporate_case(
        forward_settlement_date="2026-10-01",
        option_settlement_date="2026-10-01",
    )
    case["bond_option"] = {**case["bond_option"], "settlement_lag_days": 1}

    display = _priced(case)
    markdown = render_standalone_run_as_markdown(display)
    exported = json.loads(render_standalone_run_as_json(display))

    assert "OVME Delivery Delay (recorded, derives no date)" in markdown
    assert exported["option_delivery_delay_days_recorded"] == 1
    # The dates it does not derive are exported too, from the run's own
    # assumptions, as they always have been.
    assert exported["assumptions"]["forward_settlement_date"] == "2026-10-01"
    assert exported["assumptions"]["option_settlement_date"] == "2026-10-01"


@_QUANTLIB_SKIP
@pytest.mark.parametrize("bad", [-1, 1.5, "1", True])
def test_a_malformed_delivery_delay_is_refused_on_shape_alone(bad):
    """Validated as the contract shape it claims to be, and nothing more."""

    case = _corporate_case(
        forward_settlement_date="2026-10-01",
        option_settlement_date="2026-10-01",
    )
    case["bond_option"] = {**case["bond_option"], "settlement_lag_days": bad}

    with pytest.raises(ValueError) as excinfo:
        price_standalone_option_case(case)

    assert "settlement_lag_days" in str(excinfo.value)
