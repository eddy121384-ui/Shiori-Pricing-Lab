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
# ValuationContext forbids at construction). A real context carries the same
# snapshot object the engine is handed, so the fakes mirror that: ``_ctx``
# exposes ``market_snapshot`` and, by default, builds a consistent pair.


def _snap(valuation_date=_VALUATION_DATE):
    return SimpleNamespace(valuation_date=valuation_date)


def _ctx(snapshot=None, valuation_date=_VALUATION_DATE, reporting_currency="USD"):
    if snapshot is None:
        snapshot = _snap(valuation_date)
    return SimpleNamespace(
        valuation_date=valuation_date,
        reporting_currency=reporting_currency,
        market_snapshot=snapshot,
    )


def _consistent(valuation_date=_VALUATION_DATE, reporting_currency="USD"):
    """Return a (context, snapshot) pair sharing one snapshot object."""

    snap = _snap(valuation_date)
    ctx = _ctx(snap, valuation_date=valuation_date, reporting_currency=reporting_currency)
    return ctx, snap


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


# --- 1b. warnings / errors are normalized to tuples of PricingMessage --------


def test_errors_list_is_normalized_to_tuple():
    r = _result(
        status=PricingStatus.FAILED,
        errors=[PricingMessage(code=PricingErrorCode.ENGINE_ERROR, message="x")],
    )
    assert isinstance(r.errors, tuple)
    assert r.errors[0].code is PricingErrorCode.ENGINE_ERROR


def test_warnings_list_is_normalized_to_tuple():
    r = _result(
        status=PricingStatus.SUCCESS_WITH_WARNINGS,
        warnings=[PricingMessage(code=PricingWarningCode.DATA_QUALITY, message="x")],
    )
    assert isinstance(r.warnings, tuple)
    assert r.warnings[0].code is PricingWarningCode.DATA_QUALITY


def test_valid_tuple_of_messages_still_works():
    msgs = (
        PricingMessage(code=PricingWarningCode.TRADE_MATURED, message="a"),
        PricingMessage(code=PricingWarningCode.FORWARD_STARTING, message="b"),
    )
    r = _result(status=PricingStatus.SUCCESS_WITH_WARNINGS, warnings=msgs)
    assert r.warnings == msgs


def test_errors_with_bare_string_item_raises():
    with pytest.raises(TypeError, match="errors items must be PricingMessage"):
        _result(status=PricingStatus.FAILED, errors=("boom",))


def test_warnings_with_bare_string_item_raises():
    with pytest.raises(TypeError, match="warnings items must be PricingMessage"):
        _result(status=PricingStatus.SUCCESS_WITH_WARNINGS, warnings=["warn"])


def test_errors_as_bare_string_raises():
    # A bare string is iterable but must never be treated as a message collection.
    with pytest.raises(TypeError, match="errors must be a list or tuple"):
        _result(status=PricingStatus.FAILED, errors="boom")


def test_serialization_works_after_message_normalization():
    r = _result(
        status=PricingStatus.FAILED,
        errors=[
            PricingMessage(
                code=PricingErrorCode.MISSING_MARKET_DATA,
                message="no curve",
                detail={"currency": "EUR"},
            )
        ],
    )
    data = asdict(r)
    assert data["errors"][0]["code"] == "MISSING_MARKET_DATA"
    assert json.loads(json.dumps(data))["errors"][0]["detail"]["currency"] == "EUR"


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


# --- 2b. PricingMessage code is validated against the contract enums ---------


def test_message_accepts_error_code_member():
    msg = PricingMessage(code=PricingErrorCode.ENGINE_ERROR, message="x")
    assert msg.code is PricingErrorCode.ENGINE_ERROR


def test_message_accepts_warning_code_member():
    msg = PricingMessage(code=PricingWarningCode.DATA_QUALITY, message="x")
    assert msg.code is PricingWarningCode.DATA_QUALITY


def test_message_coerces_valid_raw_error_string():
    msg = PricingMessage(code="MISSING_MARKET_DATA", message="x")
    assert msg.code is PricingErrorCode.MISSING_MARKET_DATA


def test_message_coerces_valid_raw_warning_string():
    msg = PricingMessage(code="TRADE_MATURED", message="x")
    assert msg.code is PricingWarningCode.TRADE_MATURED


def test_message_rejects_unknown_code_string():
    with pytest.raises(ValueError, match="known error or warning code"):
        PricingMessage(code="NOT_A_REAL_CODE", message="x")


def test_message_code_serializes_to_plain_string():
    msg = PricingMessage(code=PricingErrorCode.UNSUPPORTED_PRODUCT, message="x")
    data = asdict(msg)
    assert data["code"] == "UNSUPPORTED_PRODUCT"
    assert json.loads(json.dumps(data))["code"] == "UNSUPPORTED_PRODUCT"


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
    ctx, snap = _consistent()
    result = price(product, ctx, snap)

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
    # Date check fires before the snapshot-identity check, so a differing pair
    # surfaces VALUATION_DATE_MISMATCH regardless of the snapshot objects.
    result = price(
        _irs(),
        _ctx(valuation_date="2026-06-29"),
        _snap(valuation_date="2026-06-30"),
    )

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.VALUATION_DATE_MISMATCH
    # Result surfaces both dates: the as-of valuation date and the snapshot date.
    assert result.valuation_date == "2026-06-29"
    assert result.market_data_as_of == "2026-06-30"


# --- 4b. Market snapshot identity mismatch -----------------------------------


def test_mixed_snapshots_same_date_returns_market_snapshot_mismatch():
    # Context built from snapshot A, but a different snapshot B is passed, with
    # the same valuation date. This must be rejected, not silently mixed.
    snap_a = _snap()
    snap_b = _snap()  # same valuation_date, different object
    ctx = _ctx(snap_a)

    result = price(_irs(), ctx, snap_b)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.MARKET_SNAPSHOT_MISMATCH
    assert result.errors[0].detail["context_valuation_date"] == _VALUATION_DATE
    assert result.errors[0].detail["passed_snapshot_valuation_date"] == _VALUATION_DATE


def test_snapshot_mismatch_does_not_call_any_engine():
    registry = PricingEngineRegistry()
    engine = _CountingEngine()
    registry.register("IRS", engine)

    snap_a = _snap()
    snap_b = _snap()
    ctx = _ctx(snap_a)

    result = price(_irs(), ctx, snap_b, registry=registry)

    assert result.errors[0].code is PricingErrorCode.MARKET_SNAPSHOT_MISMATCH
    assert engine.calls == 0  # routing never happened


def test_same_snapshot_object_reaches_routing():
    registry = PricingEngineRegistry()
    registry.register("IRS", _StubEngine())

    # The exact same snapshot object on the context and as the argument.
    ctx, snap = _consistent()
    result = price(_irs(), ctx, snap, registry=registry)

    assert result.status is PricingStatus.SUCCESS
    assert result.engine_name == "stub"


def test_context_without_market_snapshot_raises_contract_error():
    # A consistent valuation date but no market_snapshot attribute is a wrong
    # call shape, so it raises rather than returning a failed result.
    ctx = SimpleNamespace(valuation_date=_VALUATION_DATE, reporting_currency="USD")
    with pytest.raises(PricingContractError, match="missing required attribute"):
        price(_irs(), ctx, _snap())


def test_missing_market_snapshot_raises_even_when_dates_mismatch():
    # The contract guard for valuation_context.market_snapshot runs before any
    # return-path failure, so a missing snapshot raises rather than being masked
    # by a VALUATION_DATE_MISMATCH result.
    ctx = SimpleNamespace(valuation_date="2026-06-29", reporting_currency="USD")
    with pytest.raises(PricingContractError, match="missing required attribute"):
        price(_irs(), ctx, _snap(valuation_date="2026-06-30"))


# --- 5. Contract violations (raise-path) -------------------------------------


def test_none_product_raises_contract_error():
    ctx, snap = _consistent()
    with pytest.raises(PricingContractError, match="product must not be None"):
        price(None, ctx, snap)


def test_none_context_raises_contract_error():
    with pytest.raises(PricingContractError, match="valuation_context must not be None"):
        price(_irs(), None, _snap())


def test_none_snapshot_raises_contract_error():
    with pytest.raises(PricingContractError, match="market_snapshot must not be None"):
        price(_irs(), _ctx(), None)


def test_missing_required_attribute_raises_contract_error():
    # A product-shaped object missing product_type.
    broken = SimpleNamespace(product_id="X-1")
    ctx, snap = _consistent()
    with pytest.raises(PricingContractError, match="missing required attribute"):
        price(broken, ctx, snap)


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


class _CountingEngine:
    """Records how many times it is routed to, to prove non-routing paths."""

    def __init__(self) -> None:
        self.calls = 0

    def price(self, product, valuation_context, market_snapshot) -> PricingResult:
        self.calls += 1
        return _StubEngine().price(product, valuation_context, market_snapshot)


def test_registered_engine_routes_correctly():
    registry = PricingEngineRegistry()
    registry.register("IRS", _StubEngine())

    ctx, snap = _consistent()
    result = price(_irs(), ctx, snap, registry=registry)

    assert result.status is PricingStatus.SUCCESS
    assert result.engine_name == "stub"
    assert result.product_type == "IRS"


def test_unregistered_type_still_unsupported_with_partial_registry():
    registry = PricingEngineRegistry()
    registry.register("IRS", _StubEngine())

    # OIS is not registered -> still unsupported, even though IRS is.
    ctx, snap = _consistent()
    result = price(_ois(), ctx, snap, registry=registry)
    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.UNSUPPORTED_PRODUCT


def test_registered_engine_that_raises_becomes_engine_error():
    registry = PricingEngineRegistry()
    registry.register("IRS", _RaisingEngine())

    ctx, snap = _consistent()
    result = price(_irs(), ctx, snap, registry=registry)
    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.ENGINE_ERROR
    assert result.errors[0].detail.get("exception_type") == "RuntimeError"


def test_default_registry_is_empty_so_everything_is_unsupported():
    # No register_engine() calls happen in this slice, so the module-level front
    # door leaves all products unsupported.
    ctx, snap = _consistent()
    assert price(_irs(), ctx, snap).errors[0].code is (
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
