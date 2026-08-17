"""Tests for `tools/bloomberg_vcub_field_hunter.py` (Issue #179).

Standalone diagnostic CLI, not part of the production pricing, vol or
ingestion path. Every test here is deterministic and offline -- no live
Bloomberg value is pinned as regression truth anywhere. They prove:

1. the §2.5 proxy coordinate is derived from the case's own dates/prices,
   with the reused ACT/365F helper and the raw day counts side by side,
   and is refused rather than guessed when the case cannot support it;
2. the `//blp/apiflds` search only fires once this run's own
   `discover_service` confirms the proven `FieldSearchRequest.searchSpec`
   pair, marks records by literal Bloomberg-text marker only, survives an
   unparseable response without losing the raw dump, and expands only
   marker-relevant mnemonics;
3. `FY`/`KY` come from Bloomberg with overrides taken from the candidate
   field's *own documented* override list -- and a field documenting no
   price override is skipped, never probed with a guessed override name;
4. `KY - FY` is only formed within one field, and `Kproxy` refuses to
   exist without a supplied, unit-confirmed, unambiguous `KATM`;
5. stage 4 probes only operator-supplied identifiers and refuses a
   cross product above the no-brute-force cap;
6. the report/rendering and CLI contracts.

No `blpapi` faking is needed: every Bloomberg seam
(`send_request`/`discover_service_fn`/`describe`/`probe`) is injectable,
mirroring the seams the Issue #165 probes already established.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))

import bloomberg_vcub_field_hunter as module  # noqa: E402
from bloomberg_curve_discovery_probe import (  # noqa: E402
    OperationEvidence,
    ServiceEvidence,
)
from bloomberg_dapi_probe import FieldDescription, ProbeFieldResult  # noqa: E402
from bloomberg_vcub_field_hunter import (  # noqa: E402
    DEFAULT_VCUB_SEARCH_TERMS,
    MAX_CANDIDATE_PROBES,
    ProxyCoordinate,
    build_report,
    derive_proxy_coordinate,
    hunt_vol_fields,
    main,
    probe_candidate_values,
    probe_yield_conversion,
    render_json,
    render_markdown,
    resolve_kproxy,
    write_report,
    yield_moneyness_by_field,
)

from shiori_pricing_lab.app.standalone_option_workbench import (  # noqa: E402
    build_request_from_standalone_option_case,
)

_APIFLDS = "//blp/apiflds"

_CASE = json.loads((_REPO_ROOT / "examples" / "standalone_option_case.json").read_text("utf-8"))


def _case_request(**overrides):
    case = json.loads(json.dumps(_CASE))
    for path, value in overrides.items():
        keys = path.split(".")
        target = case
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
    return build_request_from_standalone_option_case(case)


def _coordinate() -> ProxyCoordinate:
    return derive_proxy_coordinate(_case_request())


# --- stage 1: proxy coordinate ------------------------------------------------------


def test_proxy_coordinate_is_derived_from_the_case_dates_and_prices():
    coordinate = _coordinate()

    # TF = forward settlement date, TB = bond maturity -- read, never inferred.
    assert coordinate.forward_settlement_date == "2026-10-01"
    assert coordinate.bond_maturity_date == "2030-06-15"
    assert coordinate.valuation_date == "2026-07-01"
    assert coordinate.currency == "USD"
    assert coordinate.option_type == "CALL"
    assert coordinate.forward_clean_price_per_100 == 101.3
    assert coordinate.strike_price_per_100 == 99.5

    # Texp/Ttenor: the reused ACT/365F helper, with raw day counts beside them.
    assert coordinate.texp_days == 92
    assert coordinate.ttenor_days == 1353
    assert coordinate.texp_years == pytest.approx(92 / 365.0)
    assert coordinate.ttenor_years == pytest.approx(1353 / 365.0)
    assert "ACT/365F" in coordinate.year_fraction_convention


def test_proxy_coordinate_refuses_a_case_without_an_explicit_forward():
    request = _case_request()
    object.__setattr__(request.market_data_snapshot, "forward_clean_price_input", None)

    with pytest.raises(ValueError, match="forward_clean_price_input"):
        derive_proxy_coordinate(request)


def test_proxy_coordinate_refuses_dates_that_are_not_a_bond_option_coordinate():
    request = _case_request()
    # A forward settlement after the bond matures is not a Ttenor at all.
    object.__setattr__(request, "forward_settlement_date", "2031-01-01")

    with pytest.raises(ValueError, match="proxy coordinate"):
        derive_proxy_coordinate(request)


# --- stage 2: field metadata discovery ----------------------------------------------


def _proven_apiflds(*, opened: bool = True) -> ServiceEvidence:
    return ServiceEvidence(
        service_uri=_APIFLDS,
        opened=opened,
        open_error=None if opened else "could not open",
        full_schema_dump="schema",
        operations=(
            OperationEvidence(
                name="FieldSearchRequest",
                description=None,
                request_schema=None,
                response_schemas=(),
                candidate_string_elements=("searchSpec",),
            ),
        ),
        operation_list_error=None,
    )


class _FakeElement:
    """Minimal `blpapi`-shaped element: only `hasElement`/`getElement*` are used."""

    def __init__(self, values: dict):
        self._values = values

    def hasElement(self, name: str) -> bool:  # noqa: N802 -- blpapi's own spelling
        return name in self._values

    def getElement(self, name: str):  # noqa: N802
        return self._values[name]

    def getElementAsString(self, name: str) -> str:  # noqa: N802
        return str(self._values[name])

    def __str__(self) -> str:
        return f"element({sorted(self._values)})"


class _FakeArray:
    def __init__(self, elements: list[_FakeElement]):
        self._elements = elements

    def numValues(self) -> int:  # noqa: N802
        return len(self._elements)

    def getValueAsElement(self, index: int) -> _FakeElement:  # noqa: N802
        return self._elements[index]


def _search_message(records: list[dict]) -> _FakeElement:
    return _FakeElement(
        {
            "fieldData": _FakeArray(
                [
                    _FakeElement({"id": record["id"], "fieldInfo": _FakeElement(record["info"])})
                    for record in records
                ]
            )
        }
    )


def _record(field_id: str, mnemonic: str, description: str) -> dict:
    return {"id": field_id, "info": {"mnemonic": mnemonic, "description": description}}


def test_field_search_does_not_fire_until_this_run_confirms_the_proven_pair():
    sent: list[str] = []

    def _send_request(**kwargs):
        sent.append(kwargs["request_name"])

    report = hunt_vol_fields(
        ("swaption",),
        send_request=_send_request,
        discover_service_fn=lambda uri: _proven_apiflds(opened=False),
    )

    assert sent == []
    assert report.search_capability_confirmed is False
    assert "was not confirmed" in report.blocker_note
    assert report.marker_relevant_mnemonics == ()


def test_field_search_marks_records_only_by_literal_bloomberg_text():
    described: list[list[str]] = []

    def _send_request(*, service_uri, request_name, configure, collect, context):
        assert service_uri == _APIFLDS
        assert request_name == "FieldSearchRequest"
        collect(
            _search_message(
                [
                    _record("VC001", "USD_SWAPTION_NORMAL_VOL", "Swaption normal volatility"),
                    _record("XX002", "SOME_OTHER_FIELD", "Bond duration"),
                ]
            )
        )

    def _describe(fields):
        described.append(list(fields))
        return [
            FieldDescription(
                field=field,
                status="described",
                mnemonic=field,
                description="d",
                datatype="Double",
                overrides=(),
            )
            for field in fields
        ]

    report = hunt_vol_fields(
        ("swaption",),
        send_request=_send_request,
        discover_service_fn=lambda uri: _proven_apiflds(),
        describe=_describe,
    )

    outcome = report.term_outcomes[0]
    assert outcome.status == "sent"
    assert [(r.mnemonic, r.marker_relevant) for r in outcome.records] == [
        ("USD_SWAPTION_NORMAL_VOL", True),
        ("SOME_OTHER_FIELD", False),
    ]
    assert report.marker_relevant_mnemonics == ("USD_SWAPTION_NORMAL_VOL",)
    # Only the marker-relevant mnemonic is expanded -- never the whole result set.
    assert described[0] == ["USD_SWAPTION_NORMAL_VOL"]


def test_field_search_keeps_the_raw_dump_when_the_response_does_not_parse():
    def _send_request(*, service_uri, request_name, configure, collect, context):
        collect(_FakeElement({"somethingElse": "unexpected shape"}))

    report = hunt_vol_fields(
        ("VCUB",),
        send_request=_send_request,
        discover_service_fn=lambda uri: _proven_apiflds(),
    )

    outcome = report.term_outcomes[0]
    assert outcome.structured_parse_ok is False
    assert outcome.records == ()
    assert "somethingElse" in outcome.raw_response_dump
    assert report.documentation_expansion is None
    assert "raw response dump" in report.blocker_note


def test_one_search_term_failure_never_blocks_the_others():
    def _send_request(*, service_uri, request_name, configure, collect, context):
        if "VCUB" in context:
            raise RuntimeError("Bloomberg DAPI request timed out for VCUB")
        collect(_search_message([_record("VC001", "SWAPTION_VOL", "Swaption normal vol")]))

    report = hunt_vol_fields(
        ("VCUB", "swaption"),
        send_request=_send_request,
        discover_service_fn=lambda uri: _proven_apiflds(),
        describe=lambda fields: [],
    )

    assert [(o.term, o.status) for o in report.term_outcomes] == [
        ("VCUB", "error"),
        ("swaption", "sent"),
    ]
    assert "timed out" in report.term_outcomes[0].error
    assert report.marker_relevant_mnemonics == ("SWAPTION_VOL",)


def test_issue_179_search_vocabulary_is_used_verbatim():
    assert DEFAULT_VCUB_SEARCH_TERMS == (
        "VCUB",
        "swaption",
        "normal vol",
        "normal volatility",
        "volatility cube",
        "ATM swap rate",
        "swaption expiry",
        "swap tenor",
        "strike spread",
        "moneyness",
    )


# --- stage 3: FY / KY ---------------------------------------------------------------


def _describe_with_overrides(fields):
    """Pass 1 documents override ids; pass 2 names them -- exactly the two-pass shape."""

    if list(fields) == ["YAS_BOND_YLD"]:
        return [
            FieldDescription(
                field="YAS_BOND_YLD",
                status="described",
                mnemonic="YAS_BOND_YLD",
                description="Yield from price",
                datatype="Double",
                overrides=("PY001", "PY002"),
            )
        ]
    return [
        FieldDescription(
            field="PY001",
            status="described",
            mnemonic="YAS_BOND_PX",
            description="price override",
            datatype="Double",
        ),
        FieldDescription(
            field="PY002",
            status="described",
            mnemonic="SETTLE_DT",
            description="settlement override",
            datatype="Date",
        ),
    ]


def test_fy_and_ky_use_the_fields_own_documented_overrides():
    sent: list[tuple[str, list[str], list[tuple[str, str]]]] = []

    def _probe(identifier, fields, overrides=None):
        sent.append((identifier, list(fields), list(overrides or [])))
        value = "4.100000" if overrides[0][1] == "101.3" else "4.550000"
        return [ProbeFieldResult(field=fields[0], status="returned", value=value)]

    report = probe_yield_conversion(
        _coordinate(),
        ("YAS_BOND_YLD",),
        describe=_describe_with_overrides,
        probe=_probe,
    )

    assert report.identifier == "/isin/XS0000000001"
    assert [(identifier, fields) for identifier, fields, _ in sent] == [
        ("/isin/XS0000000001", ["YAS_BOND_YLD"]),
        ("/isin/XS0000000001", ["YAS_BOND_YLD"]),
    ]
    # Override names come from Bloomberg's own documentation, and the
    # settlement override carries TF in Bloomberg's YYYYMMDD form.
    assert sent[0][2] == [("YAS_BOND_PX", "101.3"), ("SETTLE_DT", "20261001")]
    assert sent[1][2] == [("YAS_BOND_PX", "99.5"), ("SETTLE_DT", "20261001")]
    assert [(p.role, p.status, p.value) for p in report.probes] == [
        ("FY", "returned", "4.100000"),
        ("KY", "returned", "4.550000"),
    ]
    # KY - FY only within one field, in that field's own unit.
    assert yield_moneyness_by_field(report) == {"YAS_BOND_YLD": pytest.approx(0.45)}


def test_a_field_documenting_no_price_override_is_skipped_not_guessed():
    def _describe(fields):
        if list(fields) == ["MYSTERY_YLD"]:
            return [
                FieldDescription(
                    field="MYSTERY_YLD",
                    status="described",
                    mnemonic="MYSTERY_YLD",
                    description="yield",
                    datatype="Double",
                    overrides=(),
                )
            ]
        return []

    def _probe(identifier, fields, overrides=None):  # pragma: no cover -- must not run
        raise AssertionError("no probe may be sent without a documented price override")

    report = probe_yield_conversion(
        _coordinate(), ("MYSTERY_YLD",), describe=_describe, probe=_probe
    )

    assert [(p.role, p.status) for p in report.probes] == [("FY", "skipped"), ("KY", "skipped")]
    assert all(p.price_override_field is None for p in report.probes)
    assert "no price-like override" in report.probes[0].detail
    assert yield_moneyness_by_field(report) == {}
    assert "no reviewed price->yield helper" in report.blocker_note


def test_a_field_exception_is_recorded_verbatim_and_never_becomes_a_number():
    def _probe(identifier, fields, overrides=None):
        return [
            ProbeFieldResult(
                field=fields[0],
                status="field_exception",
                detail="errorInfo: Not entitled to this field",
            )
        ]

    report = probe_yield_conversion(
        _coordinate(), ("YAS_BOND_YLD",), describe=_describe_with_overrides, probe=_probe
    )

    assert {p.status for p in report.probes} == {"field_exception"}
    assert "Not entitled" in report.probes[0].detail
    assert yield_moneyness_by_field(report) == {}


def test_a_request_error_is_recorded_per_probe_and_does_not_abort_the_run():
    calls: list[int] = []

    def _probe(identifier, fields, overrides=None):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Bloomberg DAPI securityError for '/isin/XS0000000001'")
        return [ProbeFieldResult(field=fields[0], status="returned", value="4.5")]

    report = probe_yield_conversion(
        _coordinate(), ("YAS_BOND_YLD",), describe=_describe_with_overrides, probe=_probe
    )

    assert [(p.role, p.status) for p in report.probes] == [
        ("FY", "request_error"),
        ("KY", "returned"),
    ]
    assert "securityError" in report.probes[0].detail
    # One leg alone is not a moneyness.
    assert yield_moneyness_by_field(report) == {}


# --- Kproxy -------------------------------------------------------------------------


def test_kproxy_resolves_only_with_a_supplied_unit_confirmed_katm():
    resolution = resolve_kproxy({"YAS_BOND_YLD": 0.45}, 3.85, True, "SWPM ATM par rate")

    assert resolution.status == "resolved"
    assert resolution.yield_field == "YAS_BOND_YLD"
    assert resolution.kproxy == pytest.approx(4.30)
    assert resolution.katm_provenance == "SWPM ATM par rate"
    assert resolution.reason is None


@pytest.mark.parametrize(
    ("moneyness", "katm", "unit_confirmed", "expected_reason_fragment"),
    [
        ({}, 3.85, True, "no candidate yield field returned both"),
        ({"A": 0.4, "B": 0.5}, 3.85, True, "more than one candidate yield field"),
        ({"A": 0.4}, None, True, "no KATM was supplied"),
        ({"A": 0.4}, 3.85, False, "not confirmed to be quoted in the same unit"),
    ],
)
def test_kproxy_refuses_rather_than_guessing(
    moneyness, katm, unit_confirmed, expected_reason_fragment
):
    resolution = resolve_kproxy(moneyness, katm, unit_confirmed, None)

    assert resolution.status == "unresolved"
    assert resolution.kproxy is None
    assert expected_reason_fragment in resolution.reason


# --- stage 4: candidate value probes ------------------------------------------------


def test_candidate_probes_only_use_supplied_identifiers_and_record_every_outcome():
    def _probe(security, fields, overrides=None):
        assert overrides == [("SETTLE_DT", "20261001")]
        return [
            ProbeFieldResult(field="PX_LAST", status="returned", value="72.5"),
            ProbeFieldResult(field="MADE_UP_FLD", status="absent"),
        ]

    probes = probe_candidate_values(
        ("USSN0110 Curncy",),
        ("PX_LAST", "MADE_UP_FLD"),
        (("SETTLE_DT", "20261001"),),
        probe=_probe,
    )

    assert [(p.security, p.field, p.status, p.value) for p in probes] == [
        ("USSN0110 Curncy", "PX_LAST", "returned", "72.5"),
        ("USSN0110 Curncy", "MADE_UP_FLD", "absent", None),
    ]


def test_candidate_probe_request_failure_is_recorded_for_every_field():
    def _probe(security, fields, overrides=None):
        raise RuntimeError("Bloomberg DAPI securityError: Unknown security")

    probes = probe_candidate_values(("BAD TICKER",), ("PX_LAST", "VOL"), probe=_probe)

    assert [(p.field, p.status) for p in probes] == [
        ("PX_LAST", "request_error"),
        ("VOL", "request_error"),
    ]
    assert "Unknown security" in probes[0].detail


def test_candidate_probes_refuse_a_brute_force_cross_product():
    securities = tuple(f"S{i}" for i in range(MAX_CANDIDATE_PROBES))
    with pytest.raises(ValueError, match="forbids broad brute force"):
        probe_candidate_values(securities, ("A", "B"), probe=lambda *a, **k: [])


def test_no_candidate_identifier_means_no_probe_at_all():
    assert probe_candidate_values((), ("PX_LAST",), probe=lambda *a, **k: []) == ()
    assert probe_candidate_values(("USSN0110 Curncy",), (), probe=lambda *a, **k: []) == ()


# --- report -------------------------------------------------------------------------


def _report_data() -> dict:
    return build_report(
        generated_at="2026-08-17T00:00:00.000+00:00",
        coordinate=_coordinate(),
        field_search=None,
        yield_conversion=None,
        candidate_probes=(),
        candidate_probe_error=None,
        kproxy=resolve_kproxy({}, None, False, None),
    )


def test_report_is_json_serializable_and_assigns_no_verdict():
    data = _report_data()

    assert json.loads(render_json(data))["verdict"] is None
    assert "does not assign" in data["verdict_note"]
    assert data["proxy_coordinate"]["currency"] == "USD"


def test_markdown_renders_the_coordinate_and_the_unresolved_kproxy():
    markdown = render_markdown(_report_data())

    assert "# Bloomberg VCUB field hunter (Issue #179)" in markdown
    assert "forward_settlement_date | 2026-10-01" in markdown
    assert "Status: unresolved" in markdown


def test_write_report_writes_both_files(tmp_path):
    markdown_path, json_path = write_report(_report_data(), tmp_path / "out")

    assert markdown_path.read_text("utf-8").startswith("# Bloomberg VCUB field hunter")
    assert json.loads(json_path.read_text("utf-8"))["issue"].startswith("#179")


# --- CLI ----------------------------------------------------------------------------


def test_cli_runs_the_offline_coordinate_and_writes_a_report(tmp_path, monkeypatch, capsys):
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(_CASE), encoding="utf-8")

    monkeypatch.setattr(module, "hunt_vol_fields", lambda *a, **k: None)

    exit_code = main(
        [
            "--case",
            str(case_path),
            "--skip-field-search",
            "--skip-yield-conversion",
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert exit_code == 0
    written = json.loads((tmp_path / "out" / "bloomberg_vcub_field_hunter.json").read_text("utf-8"))
    assert written["proxy_coordinate"]["texp_days"] == 92
    assert written["field_search"] is None
    assert written["yield_conversion"] is None
    assert written["kproxy"]["status"] == "unresolved"
    assert "Ttenor" in capsys.readouterr().out


def test_cli_rejects_a_malformed_override(tmp_path, capsys):
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(_CASE), encoding="utf-8")

    exit_code = main(["--case", str(case_path), "--candidate-override", "NOT_A_PAIR"])

    assert exit_code == 2
    assert "FIELD=VALUE" in capsys.readouterr().err


def test_cli_refuses_half_a_stage_four_request(tmp_path, monkeypatch):
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(_CASE), encoding="utf-8")
    monkeypatch.setattr(module, "probe_candidate_values", lambda *a, **k: ())

    exit_code = main(
        [
            "--case",
            str(case_path),
            "--skip-field-search",
            "--skip-yield-conversion",
            "--candidate-security",
            "USSN0110 Curncy",
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert exit_code == 0
    written = json.loads((tmp_path / "out" / "bloomberg_vcub_field_hunter.json").read_text("utf-8"))
    assert "needs both" in written["candidate_value_probe_error"]
    assert written["candidate_value_probes"] == []
