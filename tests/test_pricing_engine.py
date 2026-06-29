"""Tests for the deterministic pricing engine contract (Issue #10, first slice).

These exercise the *boundary* only: the ``PricingResult`` value type, structured
messages, and the routing front door ``price(...)``. No product is actually
priced — every product routes to ``UNSUPPORTED_PRODUCT`` until per-product
engines are registered in later PRs.
"""

import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from shiori_pricing_lab.pricing import (
    ENGINE_CONTRACT_VERSION,
    EngineRegistrationError,
    PricingContractError,
    PricingEngineRegistry,
    PricingErrorCode,
    PricingMessage,
    PricingResult,
    PricingStatus,
    PricingWarningCode,
    price,
)
from shiori_pricing_lab.products import (
    BusinessDayConvention,
    CompoundingMethod,
    CrossCurrencyLeg,
    CrossCurrencySwap,
    Currency,
    DayCount,
    FixedLeg,
    FloatingIndex,
    FloatingLeg,
    Frequency,
    FXSwap,
    InterestRateSwap,
    OvernightIndexedSwap,
    PayReceive,
)

_VALUATION_DATE = "2026-06-29"


# --- Lightweight fakes for context / snapshot --------------------------------
# The front door only duck-types a few attributes, so simple namespaces are
# enough and let us build an inconsistent valuation date (which a real
# ValuationContext forbids at construction).


def _ctx(valuation_date=_VALUATION_DATE, reporting_currency="USD"):
    return SimpleNamespace(
        valuation_date=valuation_date, reporting_currency=reporting_currency
    )


def _snap(valuation_date=_VALUATION_DATE):
    return SimpleNamespace(valuation_date=valuation_date)


# --- Real product builders (for routing fidelity) ----------------------------


def _irs():
    return InterestRateSwap(
        product_id="IRS-1",
        effective_date="2026-07-01",
        maturity_date="2031-07-01",
        currency=Currency.USD,
        notional=1_000_000.0,
        fixed_leg=FixedLeg(
            pay_receive=PayReceive.PAY,
            fixed_rate=0.03,
            payment_frequency=Frequency.SEMI_ANNUAL,
            day_count=DayCount.THIRTY_360,
        ),
        floating_leg=FloatingLeg(
            pay_receive=PayReceive.RECEIVE,
            index=FloatingIndex.USD_SOFR_TERM_3M,
            spread=0.0,
            payment_frequency=Frequency.QUARTERLY,
            day_count=DayCount.ACT_360,
            reset_frequency=Frequency.QUARTERLY,
        ),
        business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
    )


def _ois():
    return OvernightIndexedSwap(
        product_id="OIS-1",
        effective_date="2026-07-01",
        maturity_date="2027-07-01",
        currency=Currency.EUR,
        notional=1_000_000.0,
        fixed_leg=FixedLeg(
            pay_receive=PayReceive.RECEIVE,
            fixed_rate=0.025,
            payment_frequency=Frequency.ANNUAL,
            day_count=DayCount.ACT_360,
        ),
        floating_leg=FloatingLeg(
            pay_receive=PayReceive.PAY,
            index=FloatingIndex.EUR_ESTR,
            spread=0.0,
            payment_frequency=Frequency.ANNUAL,
            day_count=DayCount.ACT_360,
            compounding_method=CompoundingMethod.DAILY_COMPOUNDED,
        ),
        business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
    )


def _ccs():
    return CrossCurrencySwap(
        product_id="CCS-1",
        effective_date="2026-07-01",
        maturity_date="2031-07-01",
        leg_1=CrossCurrencyLeg(
            currency=Currency.USD,
            notional=1_000_000.0,
            leg=FixedLeg(
                pay_receive=PayReceive.PAY,
                fixed_rate=0.03,
                payment_frequency=Frequency.SEMI_ANNUAL,
                day_count=DayCount.THIRTY_360,
            ),
        ),
        leg_2=CrossCurrencyLeg(
            currency=Currency.EUR,
            notional=920_000.0,
            leg=FloatingLeg(
                pay_receive=PayReceive.RECEIVE,
                index=FloatingIndex.EUR_ESTR,
                spread=0.0,
                payment_frequency=Frequency.ANNUAL,
                day_count=DayCount.ACT_360,
                compounding_method=CompoundingMethod.DAILY_COMPOUNDED,
            ),
        ),
        initial_exchange=True,
        final_exchange=True,
        business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
    )


def _fxswap():
    return FXSwap(
        product_id="FX-1",
        base_currency=Currency.EUR,
        quote_currency=Currency.USD,
        near_date="2026-07-01",
        far_date="2026-10-01",
        base_notional=1_000_000.0,
        near_action="BUY",
        near_rate=1.08,
        far_rate=1.085,
        business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
    )


def _result(**overrides) -> PricingResult:
    params = dict(
        product_id="P-1",
        product_type="IRS",
        valuation_date=_VALUATION_DATE,
        result_currency="USD",
        status=PricingStatus.SUCCESS,
        engine_name="reference",
        engine_version=ENGINE_CONTRACT_VERSION,
        method="reference",
        market_data_as_of=_VALUATION_DATE,
    )
    params.update(overrides)
    return PricingResult(**params)


# --- 1. PricingResult construction and serialization -------------------------


def test_can_construct_success_warning_failed_results():
    success = _result()
    warned = _result(
        status=PricingStatus.SUCCESS_WITH_WARNINGS,
        warnings=(PricingMessage(code=PricingWarningCode.TRADE_MATURED, message="matured"),),
    )
    failed = _result(
        status=PricingStatus.FAILED,
        errors=(
            PricingMessage(code=PricingErrorCode.UNSUPPORTED_PRODUCT, message="nope"),
        ),
    )

    assert success.status is PricingStatus.SUCCESS
    assert warned.status is PricingStatus.SUCCESS_WITH_WARNINGS
    assert failed.status is PricingStatus.FAILED
    assert success.is_success is True
    assert failed.is_success is False


def test_pv_and_dv01_default_to_none():
    r = _result()
    assert r.pv is None
    assert r.dv01 is None
    assert r.cashflows is None
    assert r.scenario_results is None


def test_result_serializes_via_asdict_and_json():
    r = _result(
        warnings=(PricingMessage(code=PricingWarningCode.DATA_QUALITY, message="stale"),),
    )
    data = asdict(r)

    assert data["product_id"] == "P-1"
    assert data["status"] == "SUCCESS"  # StrEnum serializes to a plain string
    assert data["pv"] is None
    assert data["warnings"][0]["code"] == "DATA_QUALITY"

    # Round-trips through JSON without a custom encoder.
    reloaded = json.loads(json.dumps(data))
    assert reloaded["engine_version"] == ENGINE_CONTRACT_VERSION


def test_raw_status_string_is_coerced_to_enum():
    r = _result(status="FAILED")
    assert r.status is PricingStatus.FAILED


# --- 2. Message structure ----------------------------------------------------


def test_failed_result_carries_structured_error_messages():
    r = _result(
        status=PricingStatus.FAILED,
        errors=(
            PricingMessage(
                code=PricingErrorCode.MISSING_MARKET_DATA,
                message="no EUR curve",
                detail={"currency": "EUR"},
            ),
        ),
    )
    assert len(r.errors) == 1
    assert r.errors[0].code is PricingErrorCode.MISSING_MARKET_DATA
    assert r.errors[0].detail == {"currency": "EUR"}
    assert not isinstance(r.errors[0], str)


def test_success_result_has_no_errors():
    r = _result()
    assert r.errors == ()
    assert r.warnings == ()


def test_success_with_warnings_has_warnings_and_no_errors():
    r = _result(
        status=PricingStatus.SUCCESS_WITH_WARNINGS,
        warnings=(
            PricingMessage(code=PricingWarningCode.FORWARD_STARTING, message="fwd"),
        ),
    )
    assert len(r.warnings) == 1
    assert r.errors == ()


# --- 3. Unsupported product path (empty default registry) --------------------


@pytest.mark.parametrize(
    "product, expected_type",
    [
        (_irs(), "IRS"),
        (_ois(), "OIS"),
        (_ccs(), "CCS"),
        (_fxswap(), "FX_SWAP"),
    ],
)
def test_unsupported_product_for_all_current_schemas(product, expected_type):
    result = price(product, _ctx(), _snap())

    assert result.status is PricingStatus.FAILED
    assert result.product_type == expected_type
    assert result.product_id == product.product_id
    assert result.pv is None
    assert len(result.errors) == 1
    assert result.errors[0].code is PricingErrorCode.UNSUPPORTED_PRODUCT
    assert result.valuation_date == _VALUATION_DATE
    assert result.market_data_as_of == _VALUATION_DATE
    assert result.result_currency == "USD"


# --- 4. Valuation date mismatch ----------------------------------------------


def test_valuation_date_mismatch_returns_failed():
    result = price(_irs(), _ctx(valuation_date="2026-06-29"), _snap(valuation_date="2026-06-30"))

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.VALUATION_DATE_MISMATCH
    # Result surfaces both dates: the as-of valuation date and the snapshot date.
    assert result.valuation_date == "2026-06-29"
    assert result.market_data_as_of == "2026-06-30"


# --- 5. Contract violations (raise-path) -------------------------------------


def test_none_product_raises_contract_error():
    with pytest.raises(PricingContractError, match="product must not be None"):
        price(None, _ctx(), _snap())


def test_none_context_raises_contract_error():
    with pytest.raises(PricingContractError, match="valuation_context must not be None"):
        price(_irs(), None, _snap())


def test_none_snapshot_raises_contract_error():
    with pytest.raises(PricingContractError, match="market_snapshot must not be None"):
        price(_irs(), _ctx(), None)


def test_missing_required_attribute_raises_contract_error():
    # A product-shaped object missing product_type.
    broken = SimpleNamespace(product_id="X-1")
    with pytest.raises(PricingContractError, match="missing required attribute"):
        price(broken, _ctx(), _snap())


def test_invalid_registration_raises_contract_error():
    registry = PricingEngineRegistry()

    with pytest.raises(EngineRegistrationError, match="non-blank string"):
        registry.register("   ", _StubEngine())

    with pytest.raises(EngineRegistrationError, match="callable 'price'"):
        registry.register("IRS", object())  # no price method


# --- 6. Registry behavior ----------------------------------------------------


class _StubEngine:
    """A minimal engine that returns a SUCCESS result without real pricing."""

    def price(self, product, valuation_context, market_snapshot) -> PricingResult:
        return PricingResult(
            product_id=product.product_id,
            product_type=product.product_type,
            valuation_date=valuation_context.valuation_date,
            result_currency=valuation_context.reporting_currency,
            status=PricingStatus.SUCCESS,
            engine_name="stub",
            engine_version="test",
            method="stub",
            market_data_as_of=market_snapshot.valuation_date,
        )


class _RaisingEngine:
    def price(self, product, valuation_context, market_snapshot) -> PricingResult:
        raise RuntimeError("boom")


def test_registered_engine_routes_correctly():
    registry = PricingEngineRegistry()
    registry.register("IRS", _StubEngine())

    result = price(_irs(), _ctx(), _snap(), registry=registry)

    assert result.status is PricingStatus.SUCCESS
    assert result.engine_name == "stub"
    assert result.product_type == "IRS"


def test_unregistered_type_still_unsupported_with_partial_registry():
    registry = PricingEngineRegistry()
    registry.register("IRS", _StubEngine())

    # OIS is not registered -> still unsupported, even though IRS is.
    result = price(_ois(), _ctx(), _snap(), registry=registry)
    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.UNSUPPORTED_PRODUCT


def test_registered_engine_that_raises_becomes_engine_error():
    registry = PricingEngineRegistry()
    registry.register("IRS", _RaisingEngine())

    result = price(_irs(), _ctx(), _snap(), registry=registry)
    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.ENGINE_ERROR
    assert result.errors[0].detail.get("exception_type") == "RuntimeError"


def test_default_registry_is_empty_so_everything_is_unsupported():
    # No register_engine() calls happen in this slice, so the module-level front
    # door leaves all products unsupported.
    assert price(_irs(), _ctx(), _snap()).errors[0].code is (
        PricingErrorCode.UNSUPPORTED_PRODUCT
    )


# --- 7. Layering / boundary guards -------------------------------------------


_FORBIDDEN_PREFIXES = (
    "shiori_pricing_lab.data",
    "shiori_pricing_lab.valuation",
    "shiori_pricing_lab.products",
    "shiori_pricing_lab.app",
    "shiori_pricing_lab.ai",
)


def _import_fresh_and_check(module_name, forbidden):
    import importlib
    import sys

    for name in list(sys.modules):
        if name == module_name or name.startswith(forbidden):
            del sys.modules[name]

    importlib.import_module(module_name)

    return [name for name in sys.modules if name.startswith(forbidden)]


def test_result_module_imports_no_other_layers():
    leaked = _import_fresh_and_check("shiori_pricing_lab.pricing.result", _FORBIDDEN_PREFIXES)
    assert leaked == []


def test_errors_module_imports_no_other_layers():
    leaked = _import_fresh_and_check("shiori_pricing_lab.pricing.errors", _FORBIDDEN_PREFIXES)
    assert leaked == []


def test_engine_module_does_not_import_data_valuation_ui_or_ai():
    # The engine front door references those types only under TYPE_CHECKING, so
    # importing it must not pull in data providers, valuation, UI, or AI.
    leaked = _import_fresh_and_check("shiori_pricing_lab.pricing.engine", _FORBIDDEN_PREFIXES)
    assert leaked == []


def test_pricing_engine_modules_have_no_system_date():
    from pathlib import Path

    import shiori_pricing_lab.pricing as pricing_pkg

    pricing_dir = Path(pricing_pkg.__file__).parent
    for path in sorted(pricing_dir.glob("*.py")):
        text = path.read_text()
        assert "date.today(" not in text, f"{path.name} uses date.today()"
        assert "datetime.now(" not in text, f"{path.name} uses datetime.now()"
