"""Tests for ``BLIBenchmarkQuote`` (Issue #98 PR A benchmark quote contract).

Covers construction, coercion, immutability, deterministic serialization,
mandatory-field enforcement, and the module boundary (no pricing,
comparison, provider, network, or clock dependency). No residual, tolerance,
or comparison behavior exists yet -- that is out of scope for this slice.
"""

from __future__ import annotations

import ast
import inspect
import math
from dataclasses import FrozenInstanceError, asdict

import pytest

from shiori_pricing_lab.data import bli_benchmark_quote as bli_benchmark_quote_module
from shiori_pricing_lab.data.bli_benchmark_quote import (
    BLIBenchmarkQuote,
    BLIBenchmarkQuoteSide,
    BLIBenchmarkSourceType,
)
from shiori_pricing_lab.products.enums import Currency


def _valid_kwargs(**overrides: object) -> dict:
    kwargs = dict(
        benchmark_id="BM-0001",
        source_type=BLIBenchmarkSourceType.BLOOMBERG,
        source_system="BLOOMBERG",
        source_as_of="2026-07-10T16:00:00Z",
        retrieved_at="2026-07-14T09:30:00Z",
        quote_side=BLIBenchmarkQuoteSide.MID,
        premium_per_100=1.2345,
        total_premium=123450.0,
        currency=Currency.USD,
        product_id="BONDOPT-0001",
        snapshot_id="SNAP-0001",
        underlying_id="ANON-BOND-001",
        source_reference="SANITIZED-BENCHMARK-REFERENCE-001",
    )
    kwargs.update(overrides)
    return kwargs


# --- 1. Valid construction ---------------------------------------------------


def test_valid_construction_with_enum_members():
    quote = BLIBenchmarkQuote(**_valid_kwargs())
    assert quote.source_type is BLIBenchmarkSourceType.BLOOMBERG
    assert quote.quote_side is BLIBenchmarkQuoteSide.MID
    assert quote.currency is Currency.USD
    assert quote.notes is None


def test_valid_construction_with_accepted_raw_strings():
    quote = BLIBenchmarkQuote(
        **_valid_kwargs(source_type="VENDOR", quote_side="OFFER", currency="EUR")
    )
    assert quote.source_type is BLIBenchmarkSourceType.VENDOR
    assert quote.quote_side is BLIBenchmarkQuoteSide.OFFER
    assert quote.currency is Currency.EUR


def test_valid_construction_with_notes():
    quote = BLIBenchmarkQuote(**_valid_kwargs(notes="dealer marked wide of screen"))
    assert quote.notes == "dealer marked wide of screen"


# --- 2. Immutability / determinism -------------------------------------------


def test_quote_is_frozen():
    quote = BLIBenchmarkQuote(**_valid_kwargs())
    with pytest.raises(FrozenInstanceError):
        quote.premium_per_100 = 9.99  # type: ignore[misc]


def test_asdict_is_deterministic_and_complete():
    quote = BLIBenchmarkQuote(**_valid_kwargs())
    first = asdict(quote)
    second = asdict(quote)
    assert first == second
    assert first == {
        "benchmark_id": "BM-0001",
        "source_type": BLIBenchmarkSourceType.BLOOMBERG,
        "source_system": "BLOOMBERG",
        "source_as_of": "2026-07-10T16:00:00Z",
        "retrieved_at": "2026-07-14T09:30:00Z",
        "quote_side": BLIBenchmarkQuoteSide.MID,
        "premium_per_100": 1.2345,
        "total_premium": 123450.0,
        "currency": Currency.USD,
        "product_id": "BONDOPT-0001",
        "snapshot_id": "SNAP-0001",
        "underlying_id": "ANON-BOND-001",
        "source_reference": "SANITIZED-BENCHMARK-REFERENCE-001",
        "notes": None,
    }


# --- 3. No default quote side / no defaults on mandatory fields -------------


def test_quote_side_has_no_default():
    kwargs = _valid_kwargs()
    del kwargs["quote_side"]
    with pytest.raises(TypeError):
        BLIBenchmarkQuote(**kwargs)


@pytest.mark.parametrize(
    "field_name",
    [
        "benchmark_id",
        "source_type",
        "source_system",
        "source_as_of",
        "retrieved_at",
        "quote_side",
        "premium_per_100",
        "total_premium",
        "currency",
        "product_id",
        "snapshot_id",
        "underlying_id",
        "source_reference",
    ],
)
def test_mandatory_field_has_no_default(field_name):
    kwargs = _valid_kwargs()
    del kwargs[field_name]
    with pytest.raises(TypeError):
        BLIBenchmarkQuote(**kwargs)


# --- 4. Enum / currency coercion failure -------------------------------------


def test_invalid_source_type_rejected():
    with pytest.raises(ValueError, match="source_type"):
        BLIBenchmarkQuote(**_valid_kwargs(source_type="REUTERS"))


def test_invalid_quote_side_rejected():
    with pytest.raises(ValueError, match="quote_side"):
        BLIBenchmarkQuote(**_valid_kwargs(quote_side="LAST"))


def test_invalid_currency_rejected():
    with pytest.raises(ValueError, match="currency"):
        BLIBenchmarkQuote(**_valid_kwargs(currency="XXX"))


# --- 5. Blank mandatory strings -----------------------------------------------


@pytest.mark.parametrize(
    "field_name",
    [
        "benchmark_id",
        "source_system",
        "product_id",
        "snapshot_id",
        "underlying_id",
        "source_reference",
    ],
)
def test_blank_mandatory_string_rejected(field_name):
    with pytest.raises(ValueError, match=field_name):
        BLIBenchmarkQuote(**_valid_kwargs(**{field_name: "   "}))


# --- 6. source_as_of / retrieved_at validated independently ------------------


def test_missing_source_as_of_format_rejected():
    with pytest.raises(ValueError, match="source_as_of"):
        BLIBenchmarkQuote(**_valid_kwargs(source_as_of="not-a-timestamp"))


def test_blank_source_as_of_rejected():
    with pytest.raises(ValueError, match="source_as_of"):
        BLIBenchmarkQuote(**_valid_kwargs(source_as_of=""))


def test_missing_retrieved_at_format_rejected():
    with pytest.raises(ValueError, match="retrieved_at"):
        BLIBenchmarkQuote(**_valid_kwargs(retrieved_at="not-a-timestamp"))


def test_blank_retrieved_at_rejected():
    with pytest.raises(ValueError, match="retrieved_at"):
        BLIBenchmarkQuote(**_valid_kwargs(retrieved_at=""))


def test_source_as_of_and_retrieved_at_fail_independently_of_each_other():
    # An invalid source_as_of with a valid retrieved_at still fails on
    # source_as_of, and vice versa -- neither field's validity depends on
    # or is inferred from the other.
    with pytest.raises(ValueError, match="source_as_of"):
        BLIBenchmarkQuote(**_valid_kwargs(source_as_of="garbage", retrieved_at="2026-07-14"))
    with pytest.raises(ValueError, match="retrieved_at"):
        BLIBenchmarkQuote(**_valid_kwargs(source_as_of="2026-07-10", retrieved_at="garbage"))


def test_source_as_of_and_retrieved_at_are_not_defaulted_from_each_other():
    quote = BLIBenchmarkQuote(
        **_valid_kwargs(source_as_of="2026-07-01", retrieved_at="2026-07-14")
    )
    assert quote.source_as_of == "2026-07-01"
    assert quote.retrieved_at == "2026-07-14"
    assert quote.source_as_of != quote.retrieved_at


def test_none_source_as_of_rejected():
    with pytest.raises(ValueError, match="source_as_of"):
        BLIBenchmarkQuote(**_valid_kwargs(source_as_of=None))


def test_none_retrieved_at_rejected():
    with pytest.raises(ValueError, match="retrieved_at"):
        BLIBenchmarkQuote(**_valid_kwargs(retrieved_at=None))


def test_none_source_as_of_and_none_retrieved_at_fail_independently():
    # A None source_as_of with a valid retrieved_at still fails on
    # source_as_of, and vice versa -- neither field's None-check depends on
    # or is inferred from the other.
    with pytest.raises(ValueError, match="source_as_of"):
        BLIBenchmarkQuote(**_valid_kwargs(source_as_of=None, retrieved_at="2026-07-14"))
    with pytest.raises(ValueError, match="retrieved_at"):
        BLIBenchmarkQuote(**_valid_kwargs(source_as_of="2026-07-10", retrieved_at=None))


# --- 7. Premium validation ----------------------------------------------------


def test_negative_premium_per_100_rejected():
    with pytest.raises(ValueError, match="premium_per_100"):
        BLIBenchmarkQuote(**_valid_kwargs(premium_per_100=-0.01))


def test_negative_total_premium_rejected():
    with pytest.raises(ValueError, match="total_premium"):
        BLIBenchmarkQuote(**_valid_kwargs(total_premium=-1.0))


def test_nan_premium_rejected():
    with pytest.raises(ValueError, match="premium_per_100"):
        BLIBenchmarkQuote(**_valid_kwargs(premium_per_100=math.nan))


def test_infinite_premium_rejected():
    with pytest.raises(ValueError, match="total_premium"):
        BLIBenchmarkQuote(**_valid_kwargs(total_premium=math.inf))


def test_negative_infinite_premium_rejected():
    with pytest.raises(ValueError, match="premium_per_100"):
        BLIBenchmarkQuote(**_valid_kwargs(premium_per_100=-math.inf))


def test_zero_premium_accepted():
    quote = BLIBenchmarkQuote(**_valid_kwargs(premium_per_100=0.0, total_premium=0.0))
    assert quote.premium_per_100 == 0.0
    assert quote.total_premium == 0.0


def test_premiums_preserved_verbatim_with_no_derived_relationship():
    # Deliberately inconsistent with any notional (e.g. total_premium is not
    # premium_per_100 scaled by any implied notional) -- this module performs
    # no such check or derivation; both values must simply survive untouched.
    quote = BLIBenchmarkQuote(**_valid_kwargs(premium_per_100=1.5, total_premium=999999.0))
    assert quote.premium_per_100 == 1.5
    assert quote.total_premium == 999999.0


# --- 8. Optional notes ---------------------------------------------------------


def test_notes_none_accepted():
    quote = BLIBenchmarkQuote(**_valid_kwargs(notes=None))
    assert quote.notes is None


def test_notes_blank_rejected():
    with pytest.raises(ValueError, match="notes"):
        BLIBenchmarkQuote(**_valid_kwargs(notes="   "))


# --- 9. Module boundary --------------------------------------------------------


def test_module_source_does_not_use_system_clock():
    source = inspect.getsource(bli_benchmark_quote_module)
    assert "date.today(" not in source
    assert "datetime.now(" not in source
    assert "import datetime" not in source
    assert "from datetime" not in source


def test_module_imports_no_provider_network_or_pricing_module():
    # AST-based: inspects actual import statements only, not the module
    # docstring's prose about what is deliberately *not* implemented here.
    tree = ast.parse(inspect.getsource(bli_benchmark_quote_module))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
    forbidden_modules = {
        "requests",
        "socket",
        "urllib",
        "urllib.request",
        "http.client",
        "blpapi",
        "shiori_pricing_lab.pricing.result",
        "shiori_pricing_lab.pricing.bli_pricing_engine",
        "shiori_pricing_lab.data.bli_snapshot",
        "shiori_pricing_lab.data.bli_standalone_option_request",
    }
    assert imported_names.isdisjoint(forbidden_modules)
    assert imported_names == {
        "__future__",
        "dataclasses",
        "enum",
        "shiori_pricing_lab.data._validation",
        "shiori_pricing_lab.products.enums",
    }


def test_module_defines_no_comparison_or_tolerance_names():
    # dir()-based: inspects the module's actual defined names, not prose in
    # the docstring explaining that comparison/tolerance are out of scope.
    module_names = set(dir(bli_benchmark_quote_module))
    forbidden_names = {
        "PASS",
        "WARNING",
        "FAIL",
        "NON_COMPARABLE",
        "BLIBenchmarkComparisonStatus",
        "compare_to_benchmark",
        "residual",
        "tolerance",
    }
    assert module_names.isdisjoint(forbidden_names)


def test_module_does_not_import_bli_request_or_snapshot_types():
    module_names = set(dir(bli_benchmark_quote_module))
    forbidden_names = {
        "BLIStandaloneBondOptionRequest",
        "BLIMarketDataSnapshot",
        "BLIMVPInputBundle",
        "PricingResult",
    }
    assert module_names.isdisjoint(forbidden_names)
