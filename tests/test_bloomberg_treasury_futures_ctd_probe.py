"""Tests for `tools/bloomberg_treasury_futures_ctd_probe.py` (Issue #190).

A standalone diagnostic CLI, not part of the pricing or workbench path.
These tests prove only what matters about a field-discovery probe:

- it reuses the existing DAPI probe plumbing rather than opening its own
  session;
- its default candidate list is empty now that every required CTD field is
  confirmed, so a run cannot silently re-probe a resolved field;
- it prints exactly which security it asked about, so nobody has to guess
  what produced the evidence;
- it reports a bad/absent field instead of aborting the run -- that outcome
  is the result, not a failure;
- and it confirms nothing on its own: running it wires nothing into
  `BLOOMBERG_CTD_FIELD_MAP`.

No network access and no real `blpapi`: the two DAPI entry points the script
uses are replaced with stand-ins.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))
import bloomberg_treasury_futures_ctd_probe as module  # noqa: E402
from bloomberg_dapi_probe import FieldDescription, ProbeFieldResult  # noqa: E402

from shiori_pricing_lab.data.treasury_futures_ctd import (  # noqa: E402
    BLOOMBERG_CTD_FIELD_MAP,
    REQUIRED_BLOOMBERG_CTD_FIELDS,
)


@pytest.fixture()
def fake_dapi(monkeypatch):
    """Record what the script asked for, and answer with a fixed mixed result."""

    calls = {"describe": [], "probe": []}

    def _describe(fields):
        calls["describe"].append(list(fields))
        return [
            FieldDescription(
                field=field,
                status="described",
                mnemonic=field,
                description=f"description of {field}",
                datatype="String",
            )
            for field in fields
        ]

    def _probe(security, fields, overrides=None):
        calls["probe"].append((security, list(fields)))
        results = []
        for index, field in enumerate(fields):
            if index % 3 == 0:
                results.append(ProbeFieldResult(field=field, status="returned", value="4.25"))
            elif index % 3 == 1:
                results.append(ProbeFieldResult(field=field, status="absent"))
            else:
                results.append(
                    ProbeFieldResult(
                        field=field, status="field_exception", detail="BAD_FLD"
                    )
                )
        return results

    monkeypatch.setattr(module, "describe_fields", _describe)
    monkeypatch.setattr(module, "probe_fields", _probe)
    return calls


def test_the_candidate_list_is_empty_now_that_every_field_is_confirmed() -> None:
    # Same shape `bloomberg_dapi_probe`'s own list took once its candidates
    # were resolved: the confirmed mnemonics live in the production module,
    # and re-probing them proves nothing.
    assert module._CANDIDATE_CTD_FIELDS == {}
    assert set(REQUIRED_BLOOMBERG_CTD_FIELDS).issubset(set(BLOOMBERG_CTD_FIELD_MAP))


def test_every_candidate_destination_is_a_real_required_field() -> None:
    # Vacuous while the list is empty, and the guard that matters the moment a
    # genuinely new candidate is added back.
    for field, destination in module._CANDIDATE_CTD_FIELDS.items():
        assert destination in REQUIRED_BLOOMBERG_CTD_FIELDS, field


def test_a_run_with_no_explicit_fields_probes_nothing(fake_dapi) -> None:
    # With every field resolved there is nothing to probe by default, so the
    # run refuses rather than re-asking Bloomberg about confirmed mnemonics.
    assert module.main(["--contract", "ZN"]) == 2
    assert fake_dapi["probe"] == []


def test_running_the_probe_confirms_nothing_on_its_own(fake_dapi) -> None:
    # The discipline that outlives the RED gate: a probe run reports evidence,
    # it never wires a mapping.
    before = dict(BLOOMBERG_CTD_FIELD_MAP)
    assert module.main(["--contract", "ZN", "--fields", "SOME_NEW_CANDIDATE"]) == 0
    assert BLOOMBERG_CTD_FIELD_MAP == before


def test_the_default_security_is_the_active_alias_per_code() -> None:
    assert module.default_security("ZT") == "TUA Comdty"
    assert module.default_security("ZF") == "FVA Comdty"
    assert module.default_security("ZN") == "TYA Comdty"
    assert module.default_security("ZB") == "USA Comdty"


def test_an_unsupported_contract_code_is_refused_not_guessed() -> None:
    with pytest.raises(ValueError):
        module.default_security("ZQ")


def test_all_four_mvp_contracts_are_probed_when_fields_are_given(fake_dapi) -> None:
    assert module.main(["--fields", "SOME_NEW_CANDIDATE"]) == 0
    probed = [security for security, _ in fake_dapi["probe"]]
    assert probed == ["TUA Comdty", "FVA Comdty", "TYA Comdty", "USA Comdty"]


def test_an_explicit_security_is_sent_verbatim(fake_dapi) -> None:
    assert module.main(["--security", "TYZ6 Comdty", "--fields", "SOME_NEW_CANDIDATE"]) == 0
    assert [security for security, _ in fake_dapi["probe"]] == ["TYZ6 Comdty"]


def test_explicit_fields_override_the_candidate_list(fake_dapi) -> None:
    assert module.main(["--contract", "ZN", "--fields", "FUT_CNVS_FACTOR, FUT_CTD_CPN"]) == 0
    assert fake_dapi["probe"][0][1] == ["FUT_CNVS_FACTOR", "FUT_CTD_CPN"]


def test_the_probed_security_and_every_field_outcome_are_printed(fake_dapi, capsys) -> None:
    module.main(["--contract", "ZN", "--fields", "CAND_A,CAND_B,CAND_C"])
    output = capsys.readouterr().out
    assert "TYA Comdty" in output
    # A returned value, an absent field and a field exception all appear --
    # one bad field never aborts the run.
    assert "returned" in output
    assert "absent" in output
    assert "field_exception" in output
    for field in ("CAND_A", "CAND_B", "CAND_C"):
        assert field in output


def test_the_run_prints_the_confirmed_mapping_and_says_what_is_still_unresolved(
    fake_dapi, capsys
) -> None:
    module.main(["--contract", "ZN", "--fields", "SOME_NEW_CANDIDATE"])
    output = capsys.readouterr().out
    # Every confirmed destination and its mnemonic, so a run always states
    # what is already wired before reporting anything new.
    for logical_field, mnemonic in BLOOMBERG_CTD_FIELD_MAP.items():
        assert logical_field in output
        assert mnemonic in output
    assert "Still unresolved:      none" in output
    # Anything actually probed is still labelled unconfirmed.
    assert "UNCONFIRMED candidate" in output


def test_a_dictionary_lookup_failure_does_not_stop_the_reference_probe(
    monkeypatch, fake_dapi, capsys
) -> None:
    def _raise(fields):
        raise RuntimeError("apiflds unavailable")

    monkeypatch.setattr(module, "describe_fields", _raise)
    assert module.main(["--contract", "ZN", "--fields", "SOME_NEW_CANDIDATE"]) == 0
    assert "apiflds unavailable" in capsys.readouterr().out


def test_a_missing_blpapi_is_reported_as_a_workstation_prerequisite(
    monkeypatch, capsys
) -> None:
    def _raise_import(*args, **kwargs):
        raise ImportError("No module named 'blpapi'")

    monkeypatch.setattr(module, "describe_fields", _raise_import)
    monkeypatch.setattr(module, "probe_fields", _raise_import)
    assert module.main(["--contract", "ZN", "--fields", "SOME_NEW_CANDIDATE"]) == 2
    captured = capsys.readouterr()
    assert "Bloomberg-networked" in captured.err
