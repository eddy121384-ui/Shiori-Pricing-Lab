"""Tests for `tools/lseg_swaption_vol_probe.py` (Market Data Reconnection
Phase 1).

This is a standalone diagnostic CLI, not part of the production pricing,
vol, ingestion or workbench path. These tests prove four things and nothing
else:

1. every fact about the client is *discovered* -- a missing client, a missing
   surface module, a session that refuses, or a missing `open_session` is
   reported as-is instead of being worked around with an assumed API shape;
2. **no RIC, field mnemonic or surface parameter is ever defaulted** -- a run
   with no operator-supplied candidate requests no data and says so;
3. the report items keep the same evidence classes apart as the Bloomberg
   probe's, so the two reports are directly comparable, and a returned number
   never establishes its own vol type or unit;
4. the exact-label lookup performs an exact match or reports NOT_FOUND with
   the labels the table really carries -- never the closest-looking column.

No network access and no real `lseg-data`. The client is faked with module
objects injected into `sys.modules` that carry a real `ModuleSpec` (so
`importlib.util.find_spec`, which the probe uses, behaves as it does against
an installed package). Real `pandas` is used for returned tables, because the
probe's bounded-preview and exact-label paths are genuinely pandas-shaped.

The client's own error contract is faked only as far as the probe reads it --
an exception carrying `.code` -- which is the one thing the probe's
diagnosis table depends on.
"""

from __future__ import annotations

import enum
import importlib.machinery
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))
import lseg_swaption_vol_probe as module  # noqa: E402
from lseg_swaption_vol_probe import (  # noqa: E402
    CANDIDATE_CLIENT_MODULES,
    ClientModule,
    EvidenceClass,
    LabelLookup,
    SessionOutcome,
    SurfaceCapability,
    TableSnapshot,
    TargetCoordinate,
    build_report,
    build_report_items,
    discover_client_modules,
    discover_surface_capability,
    main,
    open_desktop_session,
    run_surface_probe,
)

_CLIENT = "lseg.data"
_SURFACE_MODULE = "lseg.data.content.ipa.surfaces.swaption"
_ENUM_MODULE = "lseg.data.content.ipa.surfaces._enums"


# --------------------------------------------------------------------------
# Fake client packages, injected with real ModuleSpecs
# --------------------------------------------------------------------------


def _install_module(monkeypatch, name: str, **attributes) -> types.ModuleType:
    """Inject a fake module that `importlib.util.find_spec` can see."""

    fake = types.ModuleType(name)
    fake.__spec__ = importlib.machinery.ModuleSpec(name, loader=None)
    for attribute, value in attributes.items():
        setattr(fake, attribute, value)
    monkeypatch.setitem(sys.modules, name, fake)
    return fake


def _install_fake_client(monkeypatch, **attributes) -> types.ModuleType:
    _install_module(monkeypatch, "lseg", __path__=[])
    return _install_module(monkeypatch, _CLIENT, __version__="2.1.1", **attributes)


def test_no_client_module_is_reported_as_absent(monkeypatch):
    for name in CANDIDATE_CLIENT_MODULES:
        monkeypatch.setitem(sys.modules, name, None)

    outcomes = discover_client_modules(CANDIDATE_CLIENT_MODULES)

    assert [outcome.module_name for outcome in outcomes] == list(CANDIDATE_CLIENT_MODULES)
    assert all(outcome.imported is False for outcome in outcomes)


def test_an_installed_client_is_reported_with_its_version(monkeypatch):
    _install_fake_client(monkeypatch)

    outcomes = discover_client_modules(CANDIDATE_CLIENT_MODULES)

    by_name = {outcome.module_name: outcome for outcome in outcomes}
    assert by_name[_CLIENT].imported is True
    assert by_name[_CLIENT].version == "2.1.1"
    assert by_name["refinitiv.data"].imported is False
    assert by_name["eikon"].imported is False


# --------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------


def test_open_session_uses_the_clients_own_entry_point(monkeypatch):
    calls: list[str] = []
    state = types.SimpleNamespace(open_state="OpenState.Open")
    session_namespace = types.SimpleNamespace(get_default=lambda: state)
    _install_fake_client(
        monkeypatch,
        open_session=lambda: calls.append("opened"),
        session=session_namespace,
    )

    outcome = open_desktop_session(_CLIENT)

    assert calls == ["opened"]
    assert outcome.attempted is True
    assert outcome.entry_point == "open_session"
    assert "OpenState.Open" in (outcome.open_state or "")


def test_a_refused_session_is_reported_with_the_clients_own_code(monkeypatch):
    class _FakeClientError(Exception):
        code = 403

    def _refuse():
        raise _FakeClientError("Your Workspace account is not allowed to run API Proxy.")

    _install_fake_client(monkeypatch, open_session=_refuse)

    outcome = open_desktop_session(_CLIENT)

    assert outcome.attempted is True
    assert outcome.error_code == "403"
    assert "not allowed" in (outcome.error_message or "")


def test_a_client_without_an_open_session_shape_is_not_guessed_at(monkeypatch):
    _install_fake_client(monkeypatch)

    outcome = open_desktop_session(_CLIENT)

    assert outcome.attempted is False
    assert outcome.entry_point is None
    assert "no open_session-shaped entry point" in (outcome.error_message or "")


def test_no_client_means_no_session_attempt():
    outcome = open_desktop_session(None)

    assert outcome.attempted is False
    assert "no LSEG client module could be imported" in (outcome.error_message or "")


# --------------------------------------------------------------------------
# Surface capability discovery
# --------------------------------------------------------------------------


def _install_fake_surface(monkeypatch, *, with_definition=True):
    def Definition(**kwargs):  # noqa: N802 - mirrors the vendor's own spelling
        return kwargs

    attributes = {"Definition": Definition} if with_definition else {}
    _install_module(monkeypatch, _SURFACE_MODULE, **attributes)


def _install_fake_enums(monkeypatch):
    class VolatilityType(enum.Enum):  # noqa: N801 - mirrors the vendor's own spelling
        NORMAL_VOLATILITY = "NormalVolatility"
        LOG_NORMAL_VOLATILITY = "LogNormalVolatility"

    class PriceSide(enum.Enum):  # noqa: N801
        BID = "Bid"
        MID = "Mid"

    _install_module(monkeypatch, _ENUM_MODULE, VolatilityType=VolatilityType, PriceSide=PriceSide)


def test_surface_capability_reports_the_real_module_and_signature(monkeypatch):
    _install_fake_client(monkeypatch)
    _install_fake_surface(monkeypatch)

    capability = discover_surface_capability(_CLIENT)

    assert capability.available_module == _SURFACE_MODULE
    assert capability.definition_callable == f"{_SURFACE_MODULE}.Definition"
    assert "kwargs" in (capability.definition_signature or "")


def test_surface_capability_reports_the_clients_own_volatility_enum_members(monkeypatch):
    _install_fake_client(monkeypatch)
    _install_fake_surface(monkeypatch)
    _install_fake_enums(monkeypatch)

    capability = discover_surface_capability(_CLIENT)

    assert _ENUM_MODULE in capability.enum_modules_found
    joined = " ".join(capability.volatility_type_members)
    assert "VolatilityType.NORMAL_VOLATILITY -> 'NormalVolatility'" in joined
    assert "VolatilityType.LOG_NORMAL_VOLATILITY -> 'LogNormalVolatility'" in joined
    # A non-volatility enum is not dragged in as if it were one.
    assert "PriceSide.BID" not in joined


def test_a_missing_surface_module_is_reported_absent_not_worked_around(monkeypatch):
    _install_fake_client(monkeypatch)

    capability = discover_surface_capability(_CLIENT)

    assert capability.available_module is None
    assert capability.definition_callable is None
    assert any("module not found" in note for note in capability.notes)


def test_a_surface_module_without_a_definition_is_reported_not_called(monkeypatch):
    _install_fake_client(monkeypatch)
    _install_fake_surface(monkeypatch, with_definition=False)

    capability = discover_surface_capability(_CLIENT)

    assert capability.available_module == _SURFACE_MODULE
    assert capability.definition_callable is None
    assert any("exposes no callable Definition" in note for note in capability.notes)


def test_capability_without_a_client_is_reported_as_such():
    capability = discover_surface_capability(None)

    assert capability.available_module is None
    assert capability.notes == ("no client module imported, so no capability was introspected",)


# --------------------------------------------------------------------------
# Surface invocation
# --------------------------------------------------------------------------


def test_surface_probe_passes_only_operator_supplied_parameters(monkeypatch):
    seen: list[dict] = []

    def Definition(**kwargs):  # noqa: N802
        seen.append(kwargs)
        return pd.DataFrame({"1Y": [89.15]}, index=["5Y"])

    _install_module(monkeypatch, _SURFACE_MODULE, Definition=Definition)
    capability = SurfaceCapability(
        client_module=_CLIENT,
        candidate_module_paths=(_SURFACE_MODULE,),
        available_module=_SURFACE_MODULE,
        definition_callable=f"{_SURFACE_MODULE}.Definition",
        definition_signature="(**kwargs)",
        enum_modules_found=(),
        volatility_type_members=(),
        discovery_callables=(),
        notes=(),
    )

    looked_up = run_surface_probe(
        capability,
        (("OPERATOR_SUPPLIED_PARAMETER", "OPERATOR-SUPPLIED-VALUE"),),
        expiry_label="1Y",
        tenor_label="5Y",
    )

    assert seen == [{"OPERATOR_SUPPLIED_PARAMETER": "OPERATOR-SUPPLIED-VALUE"}]
    assert looked_up is not None
    snapshot, lookup = looked_up
    assert snapshot.shape == (1, 1)
    assert snapshot.columns == ("1Y",)
    assert snapshot.preview[0]["1Y"] == "89.15"
    assert lookup is not None and lookup.matched is True
    assert lookup.value == "89.15"
    assert lookup.source.startswith(_SURFACE_MODULE)


def test_surface_probe_is_not_attempted_without_a_definition_or_parameters(monkeypatch):
    capability = SurfaceCapability(
        client_module=_CLIENT,
        candidate_module_paths=(),
        available_module=None,
        definition_callable=None,
        definition_signature=None,
        enum_modules_found=(),
        volatility_type_members=(),
        discovery_callables=(),
        notes=(),
    )

    assert run_surface_probe(capability, (("A", "B"),), expiry_label=None, tenor_label=None) is None

def test_a_failing_surface_call_reports_the_clients_own_error(monkeypatch):
    class _FakeClientError(Exception):
        code = 400

    def Definition(**kwargs):  # noqa: N802
        raise _FakeClientError("unexpected parameter shape")

    _install_module(monkeypatch, _SURFACE_MODULE, Definition=Definition)
    capability = SurfaceCapability(
        client_module=_CLIENT,
        candidate_module_paths=(_SURFACE_MODULE,),
        available_module=_SURFACE_MODULE,
        definition_callable=f"{_SURFACE_MODULE}.Definition",
        definition_signature="(**kwargs)",
        enum_modules_found=(),
        volatility_type_members=(),
        discovery_callables=(),
        notes=(),
    )

    failed = run_surface_probe(
        capability, (("A", "B"),), expiry_label=None, tenor_label=None
    )

    assert failed is not None
    snapshot, lookup = failed
    assert snapshot.error_code == "400"
    assert "unexpected parameter shape" in (snapshot.error_message or "")
    assert lookup is None


# --------------------------------------------------------------------------
# get_data snapshots and exact-label lookup
# --------------------------------------------------------------------------


def _frame() -> pd.DataFrame:
    return pd.DataFrame({"1Y": [89.15], "2Y": [91.0]}, index=["5Y", "10Y"])


def test_get_data_snapshot_is_bounded_and_carries_its_labels(monkeypatch):
    client = _install_fake_client(
        monkeypatch, get_data=lambda universe, fields: _frame()
    )

    snapshot, lookup = module._read_table(  # noqa: SLF001 - the probe's own read path
        client, universe=["OPERATOR-SUPPLIED-RIC"], fields=["OPERATOR_SUPPLIED_FIELD"],
        expiry_label="1Y", tenor_label="5Y",
    )

    assert snapshot.shape == (2, 2)
    assert snapshot.columns == ("1Y", "2Y")
    assert snapshot.row_labels == ("5Y", "10Y")
    assert snapshot.fetched_at
    assert lookup is not None and lookup.matched is True
    assert lookup.value == "89.15"


def test_exact_label_lookup_reports_not_found_with_the_labels_it_has(monkeypatch):
    client = _install_fake_client(
        monkeypatch, get_data=lambda universe, fields: _frame()
    )

    _snapshot, lookup = module._read_table(  # noqa: SLF001
        client, universe=["R"], fields=["F"], expiry_label="1x1", tenor_label="5Y",
    )

    assert lookup is not None
    assert lookup.matched is False
    assert lookup.value is None
    assert "1Y" in lookup.available_labels


def test_ambiguous_label_orientation_is_refused_rather_than_picked(monkeypatch):
    """Both orientations resolve and disagree -- so neither is presented as the answer."""

    ambiguous = pd.DataFrame(
        {"1Y": [10.0, 20.0], "5Y": [30.0, 40.0]}, index=["1Y", "5Y"]
    )
    client = _install_fake_client(
        monkeypatch, get_data=lambda universe, fields: ambiguous
    )

    _snapshot, lookup = module._read_table(  # noqa: SLF001
        client, universe=["R"], fields=["F"], expiry_label="1Y", tenor_label="5Y",
    )

    assert lookup is not None
    assert lookup.matched is False
    assert lookup.value is None
    assert any("AMBIGUOUS" in label for label in lookup.available_labels)


def test_a_client_error_on_get_data_is_reported_not_raised(monkeypatch):
    class _FakeClientError(Exception):
        code = 403

    def _refuse(universe, fields):
        raise _FakeClientError("not entitled to this field")

    client = _install_fake_client(monkeypatch, get_data=_refuse)

    snapshot, lookup = module._read_table(  # noqa: SLF001
        client, universe=["R"], fields=["F"],
    )

    assert snapshot.error_code == "403"
    assert "not entitled" in (snapshot.error_message or "")
    assert lookup is None


def test_a_client_without_get_data_is_reported_not_substituted(monkeypatch):
    client = _install_fake_client(monkeypatch)

    snapshot, _lookup = module._read_table(client, universe=["R"], fields=["F"])  # noqa: SLF001

    assert "no callable get_data" in (snapshot.error_message or "")


# --------------------------------------------------------------------------
# Report contract -- the same evidence discipline as the Bloomberg probe
# --------------------------------------------------------------------------


def _capability() -> SurfaceCapability:
    return SurfaceCapability(
        client_module=_CLIENT,
        candidate_module_paths=(_SURFACE_MODULE,),
        available_module=_SURFACE_MODULE,
        definition_callable=f"{_SURFACE_MODULE}.Definition",
        definition_signature="(**kwargs)",
        enum_modules_found=(_ENUM_MODULE,),
        volatility_type_members=("VolatilityType.NORMAL_VOLATILITY -> 'NormalVolatility'",),
        discovery_callables=("search",),
        notes=(),
    )


def _session_outcome(*, ok=True) -> SessionOutcome:
    if ok:
        return SessionOutcome(
            attempted=True,
            entry_point="open_session",
            open_state="OpenState.Open",
            error_code=None,
            error_message=None,
            session_object="lseg.data (imported)",
        )
    return SessionOutcome(
        attempted=True,
        entry_point="open_session",
        open_state=None,
        error_code="403",
        error_message="not allowed to run API Proxy",
        session_object=None,
    )


def _snapshot(*, error=False) -> TableSnapshot:
    if error:
        return TableSnapshot(
            source="get_data",
            shape=None,
            columns=(),
            row_labels=(),
            preview=(),
            error_code="403",
            error_message="not entitled",
            fetched_at="2026-01-01T00:00:00Z",
        )
    return TableSnapshot(
        source="get_data",
        shape=(1, 1),
        columns=("1Y",),
        row_labels=("5Y",),
        preview=({"__index__": "5Y", "1Y": "89.15"},),
        fetched_at="2026-01-01T00:00:00Z",
    )


def _items_by_name(items):
    return {item.item: item for item in items}


def test_report_items_cover_every_question_this_phase_asks():
    items = _items_by_name(
        build_report_items(
            _session_outcome(),
            _capability(),
            TargetCoordinate(),
            (),
            attempted_value_probe=False,
            fetched_at="2026-01-01T00:00:00Z",
        )
    )

    assert set(items) >= {
        "connection",
        "authorization",
        "value",
        "vol_type",
        "vol_unit",
        "expiry",
        "tenor",
        "quote_side",
        "data_date",
        "fetched_at",
        "client_published_volatility_types",
    }


def test_a_returned_table_never_establishes_its_own_vol_type_or_unit():
    items = _items_by_name(
        build_report_items(
            _session_outcome(),
            _capability(),
            TargetCoordinate(),
            (_snapshot(),),
            attempted_value_probe=True,
            fetched_at="2026-01-01T00:00:00Z",
        )
    )

    assert items["value"].evidence_class is EvidenceClass.VENDOR_RETURNED
    for item_name in ("vol_type", "vol_unit", "expiry", "tenor", "quote_side", "data_date"):
        assert items[item_name].evidence_class is EvidenceClass.NOT_ESTABLISHED


def test_a_refused_session_leaves_connection_and_authorization_unestablished():
    items = _items_by_name(
        build_report_items(
            _session_outcome(ok=False),
            _capability(),
            TargetCoordinate(),
            (_snapshot(error=True),),
            attempted_value_probe=True,
            fetched_at="2026-01-01T00:00:00Z",
        )
    )

    assert items["connection"].evidence_class is EvidenceClass.NOT_ESTABLISHED
    assert items["authorization"].evidence_class is EvidenceClass.NOT_ESTABLISHED
    assert "403" in items["connection"].detail


def test_operator_declarations_are_labelled_as_his():
    items = _items_by_name(
        build_report_items(
            _session_outcome(),
            _capability(),
            TargetCoordinate(
                expiry="1Y",
                tenor="5Y",
                strike="ATM",
                quote_side="MID",
                vol_type="Normal",
                vol_unit="bp",
                data_as_of="2026-01-01",
            ),
            (),
            attempted_value_probe=False,
            fetched_at="2026-01-01T00:00:00Z",
        )
    )

    for item_name in ("vol_type", "vol_unit", "expiry", "tenor", "quote_side", "data_date"):
        assert items[item_name].evidence_class is EvidenceClass.OPERATOR_DECLARED
        assert "his assertion" in items[item_name].detail


def test_client_published_volatility_types_are_vendor_evidence_only():
    items = _items_by_name(
        build_report_items(
            _session_outcome(),
            _capability(),
            TargetCoordinate(),
            (),
            attempted_value_probe=False,
            fetched_at="2026-01-01T00:00:00Z",
        )
    )

    assert items["client_published_volatility_types"].evidence_class is (
        EvidenceClass.VENDOR_RETURNED
    )
    assert "not an assertion" in items["client_published_volatility_types"].detail


def test_report_declares_itself_read_only_and_assigns_no_verdict():
    report = build_report(
        (ClientModule(module_name=_CLIENT, imported=True, version="2.1.1", error=None),),
        _session_outcome(),
        _capability(),
        TargetCoordinate(),
        (),
        (),
        attempted_value_probe=False,
        generated_at="2026-01-01T00:00:00Z",
    )

    assert report["read_only"] is True
    assert report["production_paths_touched"] == []
    assert report["verdict"] == "UNRESOLVED"
    assert report["phase"] == "MARKET_DATA_RECONNECTION_PHASE_1"
    json.dumps(report)


# --------------------------------------------------------------------------
# CLI behaviour
# --------------------------------------------------------------------------


def test_the_cli_starts_without_an_editable_install():
    """Guards the bootstrap at the top of the probe.

    The probe reaches `bloomberg_dapi_probe` through the shared evidence
    module, and that imports `shiori_pricing_lab` at module level. Without the
    probe's own `src/` bootstrap, a checkout that was never
    `pip install -e`'d dies on an ImportError traceback instead of reporting
    the absent LSEG client. `PYTHONPATH` is scrubbed so a developer's own
    environment cannot make this pass spuriously.
    """

    environment = {k: v for k, v in os.environ.items() if k.upper() != "PYTHONPATH"}
    completed = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "tools" / "lseg_swaption_vol_probe.py"), "--help"],
        cwd=str(_REPO_ROOT),
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

    assert completed.returncode == 0


def test_main_reports_a_missing_client_as_an_install_error(monkeypatch, capsys):
    for name in CANDIDATE_CLIENT_MODULES:
        monkeypatch.setitem(sys.modules, name, None)

    assert main(["--session-only"]) == 2
    assert "no LSEG client module is installed" in capsys.readouterr().err


def test_session_only_writes_a_report_and_requests_no_data(monkeypatch, tmp_path, capsys):
    _install_fake_client(monkeypatch, open_session=lambda: None)

    exit_code = main(["--session-only", "--output-dir", str(tmp_path)])

    assert exit_code == 0
    assert "requesting no data" in capsys.readouterr().out
    report = json.loads((tmp_path / "lseg_swaption_vol_probe.json").read_text("utf-8"))
    assert report["table_snapshots"] == []


def test_main_with_no_candidate_requests_no_data_and_says_so(monkeypatch, tmp_path, capsys):
    _install_fake_client(monkeypatch, open_session=lambda: None)

    exit_code = main(["--output-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "never invents a RIC or a field" in captured.out
    assert "was not called" in captured.out
    report = json.loads((tmp_path / "lseg_swaption_vol_probe.json").read_text("utf-8"))
    assert report["table_snapshots"] == []


def test_main_probes_only_operator_supplied_ric_and_field(monkeypatch, tmp_path, capsys):
    seen: list[tuple] = []

    def _get_data(universe, fields):
        seen.append((tuple(universe), tuple(fields)))
        return _frame()

    _install_fake_client(monkeypatch, open_session=lambda: None, get_data=_get_data)

    exit_code = main(
        [
            "--ric",
            "OPERATOR-SUPPLIED-RIC",
            "--field",
            "OPERATOR_SUPPLIED_FIELD",
            "--expiry-label",
            "1Y",
            "--tenor-label",
            "5Y",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert seen == [(("OPERATOR-SUPPLIED-RIC",), ("OPERATOR_SUPPLIED_FIELD",))]
    report = json.loads((tmp_path / "lseg_swaption_vol_probe.json").read_text("utf-8"))
    assert len(report["table_snapshots"]) == 1
    assert report["label_lookups"][0]["matched"] is True
    assert report["label_lookups"][0]["value"] == "89.15"


def test_main_does_not_call_an_undiscovered_surface_definition(monkeypatch, tmp_path, capsys):
    _install_fake_client(monkeypatch, open_session=lambda: None)

    exit_code = main(
        [
            "--ipa-param",
            "OPERATOR_SUPPLIED_PARAMETER=OPERATOR-SUPPLIED-VALUE",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert "not attempted: no discovered definition" in capsys.readouterr().err


def test_label_lookup_is_not_requested_twice(monkeypatch, tmp_path):
    """One read, one `fetched_at` -- a second vendor call could disagree silently."""

    calls: list[int] = []

    def _get_data(universe, fields):
        calls.append(1)
        return _frame()

    _install_fake_client(monkeypatch, open_session=lambda: None, get_data=_get_data)

    assert main(
        [
            "--ric",
            "OPERATOR-SUPPLIED-RIC",
            "--field",
            "OPERATOR_SUPPLIED_FIELD",
            "--expiry-label",
            "1Y",
            "--tenor-label",
            "5Y",
            "--output-dir",
            str(tmp_path),
        ]
    ) == 0
    assert len(calls) == 1


def test_label_lookup_dataclass_is_constructible_empty():
    lookup = LabelLookup(
        source="get_data", expiry_label=None, tenor_label=None, matched=False, value=None
    )

    assert lookup.available_labels == ()


@pytest.mark.parametrize("raw", ["NOVALUE", "=VALUE"])
def test_ipa_parameter_requires_a_name_and_a_value(raw):
    import argparse

    from lseg_swaption_vol_probe import _parse_parameter

    with pytest.raises(argparse.ArgumentTypeError):
        _parse_parameter(raw)
