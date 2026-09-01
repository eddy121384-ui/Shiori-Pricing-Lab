"""Tests for ``tools/bloomberg_bond_yield_history_acceptance.py`` (Issue #196 §E).

The acceptance path must exercise the production loader -- not a parallel
request implementation -- and must record the workstation evidence Issue #196
asks for without ever writing a Bloomberg value to a file. Both are held down
here, along with the identifier reuse and the failure path.

Every value below is made up. No network access and no real ``blpapi``: the
production loader is replaced with a stand-in.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))

import bloomberg_bond_yield_history_acceptance as module  # noqa: E402
from bloomberg_bond_yield_history_acceptance import (  # noqa: E402
    build_report,
    console_sample,
    render_markdown,
    run_acceptance,
    write_report,
)

from shiori_pricing_lab.data.bloomberg_bond_quote import BLIBloombergDapiError  # noqa: E402
from shiori_pricing_lab.data.bloomberg_bond_yield_history import (  # noqa: E402
    BloombergBondYieldHistory,
    BondYieldObservation,
)

_ISIN = "US0000000000"
_FIELD = "SYNTHETIC_TEST_YIELD_FIELD"


def _observation(iso_date: str, raw: str | None) -> BondYieldObservation:
    return BondYieldObservation(
        observation_date=date.fromisoformat(iso_date),
        yield_value=None if raw is None else float(raw),
        raw_value=raw,
    )


def _history(observations=(), **overrides) -> BloombergBondYieldHistory:
    fields = {
        "requested_identifier": f"/isin/{_ISIN}",
        "security": "SYNTHETIC TEST Corp",
        "yield_field": _FIELD,
        "field_meaning": None,
        "field_unit": None,
        "requested_start_date": date(2026, 1, 1),
        "requested_end_date": date(2026, 1, 31),
        "observations": tuple(observations),
        "source_system": "BLOOMBERG_DAPI",
        "acquired_at": "2026-08-31T14:05:00+00:00",
    }
    fields.update(overrides)
    return BloombergBondYieldHistory(**fields)


def _stub_loader(monkeypatch, *, history=None, raises=None):
    calls: list[dict] = []

    def _fake(**kwargs):
        calls.append(kwargs)
        if raises is not None:
            raise raises
        return history if history is not None else _history()

    monkeypatch.setattr(module, "load_bloomberg_bond_yield_history", _fake)
    return calls


def _run(monkeypatch, **overrides):
    kwargs = {
        "identifier": _ISIN,
        "yield_field": _FIELD,
        "start": date(2026, 1, 1),
        "end": date(2026, 1, 31),
        "field_meaning": None,
        "field_unit": None,
    }
    kwargs.update(overrides)
    return run_acceptance(**kwargs)


# --- it runs the production route, not a parallel one -------------------------


def test_calls_the_production_loader_through_the_existing_identity_path(monkeypatch):
    calls = _stub_loader(monkeypatch)

    _run(monkeypatch)

    assert len(calls) == 1
    assert calls[0]["identifier"] == f"/isin/{_ISIN}"
    assert calls[0]["yield_field"] == _FIELD
    assert calls[0]["start_date"] == date(2026, 1, 1)
    assert calls[0]["end_date"] == date(2026, 1, 31)


def test_a_cusip_resolves_through_the_same_path(monkeypatch):
    calls = _stub_loader(monkeypatch)

    report = _run(monkeypatch, identifier="912828XX0")

    assert calls[0]["identifier"] == "/cusip/912828XX0"
    assert report.identifier_kind == "CUSIP"


def test_a_malformed_identifier_never_reaches_the_loader(monkeypatch):
    calls = _stub_loader(monkeypatch)

    with pytest.raises(ValueError, match="ISIN"):
        _run(monkeypatch, identifier="US912828 Govt")

    assert calls == []


def test_the_supplied_field_semantics_are_passed_through_verbatim(monkeypatch):
    calls = _stub_loader(monkeypatch)

    _run(monkeypatch, field_meaning="Synthetic test meaning", field_unit="percent")

    assert calls[0]["field_meaning"] == "Synthetic test meaning"
    assert calls[0]["field_unit"] == "percent"


# --- the acceptance record ----------------------------------------------------


def test_records_every_piece_of_evidence_the_issue_asks_for(monkeypatch):
    _stub_loader(
        monkeypatch,
        history=_history(
            [
                _observation("2026-01-06", "4.0"),
                _observation("2026-01-07", None),
                _observation("2026-01-09", "4.4"),
            ],
            field_unit="percent",
            field_meaning="Synthetic test meaning",
        ),
    )

    data = build_report(_run(monkeypatch))

    assert data["status"] == "loaded"
    assert data["requested_identifier"] == _ISIN
    assert data["bloomberg_identifier"] == f"/isin/{_ISIN}"
    assert data["resolved_security"] == "SYNTHETIC TEST Corp"
    assert data["yield_field"] == _FIELD
    assert data["field_unit"] == "percent"
    assert data["field_meaning"] == "Synthetic test meaning"
    assert data["requested_start_date"] == "2026-01-01"
    assert data["requested_end_date"] == "2026-01-31"
    assert data["observation_count"] == 3
    assert data["observations_with_a_value"] == 2
    assert data["rows_with_no_value"] == 1
    assert data["first_observation_date"] == "2026-01-06"
    assert data["last_observation_date"] == "2026-01-09"
    assert data["source_system"] == "BLOOMBERG_DAPI"
    assert data["acquired_at"] == "2026-08-31T14:05:00+00:00"


def test_an_empty_series_is_recorded_as_an_answer(monkeypatch):
    _stub_loader(monkeypatch, history=_history([]))

    data = build_report(_run(monkeypatch))

    assert data["status"] == "loaded"
    assert data["observation_count"] == 0
    assert data["first_observation_date"] is None
    assert data["last_observation_date"] is None


def test_a_bloomberg_failure_is_recorded_not_raised(monkeypatch):
    _stub_loader(monkeypatch, raises=BLIBloombergDapiError("field exception for BAD_FLD"))

    report = _run(monkeypatch)
    data = build_report(report)

    assert data["status"] == "error"
    assert "BAD_FLD" in data["error"]
    assert data["observation_count"] == 0


def test_no_bloomberg_value_is_ever_written_to_a_file(monkeypatch, tmp_path):
    _stub_loader(
        monkeypatch,
        history=_history(
            [_observation("2026-01-06", "4.1234567"), _observation("2026-01-09", "4.7654321")]
        ),
    )

    data = build_report(_run(monkeypatch))
    markdown_path, json_path = write_report(data, tmp_path)

    written = markdown_path.read_text(encoding="utf-8") + json_path.read_text(encoding="utf-8")
    assert "4.1234567" not in written
    assert "4.7654321" not in written
    assert "2026-01-06" in written
    assert "carries no Bloomberg value" in written


def test_the_console_sample_is_bounded_and_ends_on_the_last_observation(monkeypatch):
    _stub_loader(
        monkeypatch,
        history=_history(
            [_observation(f"2026-01-{day:02d}", f"4.{day}") for day in range(1, 21)]
        ),
    )

    sample = console_sample(_run(monkeypatch), 3)

    assert len(sample) == 4  # three from the head, plus the last observation
    assert [row[0] for row in sample[:3]] == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert sample[-1][0] == "2026-01-20"


def test_the_console_sample_marks_a_valueless_row_rather_than_dropping_it(monkeypatch):
    _stub_loader(
        monkeypatch,
        history=_history([_observation("2026-01-06", "4.0"), _observation("2026-01-07", None)]),
    )

    sample = console_sample(_run(monkeypatch), 5)

    assert sample == (("2026-01-06", "4.0"), ("2026-01-07", None))


def test_a_zero_row_sample_prints_nothing(monkeypatch):
    _stub_loader(monkeypatch, history=_history([_observation("2026-01-06", "4.0")]))

    assert console_sample(_run(monkeypatch), 0) == ()


def test_the_markdown_names_an_unconfirmed_unit_as_unconfirmed(monkeypatch):
    _stub_loader(monkeypatch, history=_history([_observation("2026-01-06", "4.0")]))

    rendered = render_markdown(build_report(_run(monkeypatch)))

    assert "Field unit: (not confirmed)" in rendered
    assert "Field meaning: (not confirmed)" in rendered
