"""Deterministic unit tests for ``scripts/launch_workbench.py`` (Issue #138).

The launcher is stdlib-only and every external effect (subprocess, network,
browser) is called through a plain module-level function, so each of these
is monkeypatched or passed in directly -- no real venv is created, no real
pip install runs, no real server starts, and no real browser opens anywhere
in this file.
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import launch_workbench as lw  # noqa: E402

# --- Project root resolution --------------------------------------------------


def test_resolve_project_root_is_relative_to_launcher_file_not_cwd(tmp_path, monkeypatch):
    weird_root = tmp_path / "Shiori Test (税) Folder"
    scripts_dir = weird_root / "scripts"
    scripts_dir.mkdir(parents=True)
    launcher_file = scripts_dir / "launch_workbench.py"
    launcher_file.write_text("# placeholder\n", encoding="utf-8")

    other_dir = tmp_path / "somewhere else entirely"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)

    assert lw.resolve_project_root(launcher_file) == weird_root.resolve()


def test_resolve_project_root_handles_spaces_unicode_and_parentheses(tmp_path):
    weird_root = tmp_path / "Répertoire (v2) — projet Shiori"
    scripts_dir = weird_root / "scripts"
    scripts_dir.mkdir(parents=True)
    launcher_file = scripts_dir / "launch_workbench.py"
    launcher_file.write_text("# placeholder\n", encoding="utf-8")

    assert lw.resolve_project_root(launcher_file) == weird_root.resolve()


# --- Python version detection --------------------------------------------------


def test_check_python_version_passes_for_3_11():
    lw.check_python_version(version_info=(3, 11, 0, "final", 0))


def test_check_python_version_passes_for_3_12():
    lw.check_python_version(version_info=(3, 12, 4, "final", 0))


def test_check_python_version_raises_below_3_11():
    with pytest.raises(lw.LauncherError, match="3.11"):
        lw.check_python_version(version_info=(3, 10, 9, "final", 0))


def test_check_python_version_raises_for_python_2():
    with pytest.raises(lw.LauncherError, match="3.11"):
        lw.check_python_version(version_info=(2, 7, 18, "final", 0))


# --- Interpreter selection (mirrors start_shiori.bat's own fallback order) ----


def test_select_interpreter_prefers_python_when_present():
    which = {"python": "/usr/bin/python", "py": None}.get
    assert lw.select_interpreter_command(which=which) == ["python"]


def test_select_interpreter_falls_back_to_py_when_python_absent():
    which = {"python": None, "py": "C:\\Windows\\py.exe"}.get
    assert lw.select_interpreter_command(which=which) == ["py", "-3"]


def test_select_interpreter_raises_when_both_absent():
    which = {"python": None, "py": None}.get
    with pytest.raises(lw.LauncherError, match="not found on PATH"):
        lw.select_interpreter_command(which=which)


# --- venv creation --------------------------------------------------------------


def test_build_venv_create_command(tmp_path):
    command = lw.build_venv_create_command("python", tmp_path)
    assert command == ["python", "-m", "venv", str(tmp_path / ".venv")]


def test_ensure_venv_reuses_existing_venv_without_running_anything(tmp_path):
    target = lw.venv_python(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("", encoding="utf-8")

    def _run(*args, **kwargs):
        raise AssertionError("must not run anything when the venv already exists")

    result = lw.ensure_venv(tmp_path, "python", run=_run)
    assert result == target


def test_ensure_venv_creates_when_missing(tmp_path):
    target = lw.venv_python(tmp_path)
    calls = []

    def _run(command, **kwargs):
        calls.append(command)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = lw.ensure_venv(tmp_path, "python", run=_run)
    assert result == target
    assert calls == [["python", "-m", "venv", str(tmp_path / ".venv")]]


def test_ensure_venv_failure_is_visible_and_actionable(tmp_path):
    def _run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="permission denied")

    with pytest.raises(lw.LauncherError, match="permission denied"):
        lw.ensure_venv(tmp_path, "python", run=_run)


# --- Dependency install ----------------------------------------------------------


def test_build_install_command_uses_literal_editable_spec_not_a_path(tmp_path):
    venv_py = tmp_path / ".venv" / "bin" / "python"
    command = lw.build_install_command(venv_py)
    assert command == [str(venv_py), "-m", "pip", "install", "-e", ".[quant]"]
    # The literal spec must never contain the project root's own path --
    # cwd carries the (possibly space/Unicode/parenthesis-laden) path
    # instead, so pip never has to parse those characters out of the spec.
    assert str(tmp_path) not in command[-1]


def test_install_dependencies_runs_from_the_actual_repository_root(tmp_path):
    calls = []

    def _run(command, cwd=None, **kwargs):
        calls.append((command, cwd))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    venv_py = tmp_path / ".venv" / "bin" / "python"
    lw.install_dependencies(tmp_path, venv_py, run=_run)

    assert len(calls) == 1
    command, cwd = calls[0]
    assert command == [str(venv_py), "-m", "pip", "install", "-e", ".[quant]"]
    assert cwd == str(tmp_path)


def test_install_dependencies_failure_is_visible_and_actionable(tmp_path):
    def _run(command, cwd=None, **kwargs):
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="Could not find a version of QuantLib"
        )

    venv_py = tmp_path / ".venv" / "bin" / "python"
    with pytest.raises(lw.LauncherError, match="Could not find a version of QuantLib"):
        lw.install_dependencies(tmp_path, venv_py, run=_run)


def test_dependencies_missing_true_on_import_failure(tmp_path):
    venv_py = tmp_path / ".venv" / "bin" / "python"

    def _run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="ModuleNotFoundError")

    assert lw.dependencies_missing(venv_py, run=_run) is True


def test_dependencies_missing_false_when_imports_succeed(tmp_path):
    venv_py = tmp_path / ".venv" / "bin" / "python"

    def _run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    assert lw.dependencies_missing(venv_py, run=_run) is False


# --- Port classification / readiness polling -------------------------------------


class _FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_classify_port_not_listening_when_connection_refused():
    def opener(url, timeout=None):
        raise OSError("connection refused")

    assert lw.classify_port("http://127.0.0.1:8765/", opener=opener) == "not_listening"


def test_classify_port_ours_when_marker_present():
    def opener(url, timeout=None):
        return _FakeResponse(200, "<title>Bond Option Pricer — Static Prototype</title>")

    assert lw.classify_port("http://127.0.0.1:8765/", opener=opener) == "ours"


def test_classify_port_occupied_by_other_when_marker_absent():
    def opener(url, timeout=None):
        return _FakeResponse(200, "<html>some other app</html>")

    assert lw.classify_port("http://127.0.0.1:8765/", opener=opener) == "occupied_by_other"


def test_wait_for_server_ready_polls_until_ready():
    calls = []
    results = iter(["not_listening", "not_listening", "ours"])

    def probe(url):
        calls.append(url)
        return next(results)

    sleeps = []
    clock_values = iter([0.0, 0.0, 0.1, 0.2])

    assert lw.wait_for_server_ready(
        "http://127.0.0.1:8765/",
        timeout_seconds=5.0,
        probe=probe,
        sleep=sleeps.append,
        clock=lambda: next(clock_values),
    )
    assert len(calls) == 3
    assert len(sleeps) == 2


def test_wait_for_server_ready_times_out():
    clock_values = iter([0.0, 1.0, 2.0, 3.0])
    assert not lw.wait_for_server_ready(
        "http://127.0.0.1:8765/",
        timeout_seconds=2.0,
        probe=lambda url: "not_listening",
        sleep=lambda _seconds: None,
        clock=lambda: next(clock_values),
    )


# --- Full orchestration: ordering, occupied-port safety, no-browser mode -------


def _patch_happy_path(monkeypatch, call_order, process=None):
    monkeypatch.setattr(lw, "check_python_version", lambda: None)
    monkeypatch.setattr(lw, "classify_port", lambda url: "not_listening")
    monkeypatch.setattr(lw, "select_interpreter_command", lambda: ["python"])
    monkeypatch.setattr(lw, "ensure_venv", lambda root, interpreter: Path("/fake/venv/python"))
    monkeypatch.setattr(lw, "dependencies_missing", lambda venv_py: False)

    def _start_server_process(venv_py, root):
        call_order.append("start_server_process")
        return process or types.SimpleNamespace(
            terminate=lambda: call_order.append("terminate"),
            wait=lambda timeout=None: call_order.append("wait"),
        )

    def _wait_for_server_ready(url):
        call_order.append("wait_for_server_ready")
        return True

    def _open_browser(url):
        call_order.append("open_browser")

    monkeypatch.setattr(lw, "start_server_process", _start_server_process)
    monkeypatch.setattr(lw, "wait_for_server_ready", _wait_for_server_ready)
    monkeypatch.setattr(lw, "open_browser", _open_browser)


def test_readiness_polling_happens_before_browser_open(monkeypatch):
    call_order = []
    _patch_happy_path(monkeypatch, call_order)

    exit_code = lw.run(["--no-browser"])

    assert exit_code == 0
    # Even in --no-browser mode, readiness must be checked before we would
    # ever have opened a browser -- assert readiness precedes the point at
    # which the server is torn down (the no-browser exit path), proving the
    # ordering invariant without actually opening anything.
    assert call_order.index("wait_for_server_ready") < call_order.index("terminate")


def test_readiness_polling_happens_strictly_before_browser_open_in_normal_mode(monkeypatch):
    call_order = []
    process = types.SimpleNamespace(
        terminate=lambda: call_order.append("terminate"),
        wait=lambda timeout=None: call_order.append("wait"),
    )
    _patch_happy_path(monkeypatch, call_order, process=process)

    exit_code = lw.run([])

    assert exit_code == 0
    assert "open_browser" in call_order
    assert call_order.index("wait_for_server_ready") < call_order.index("open_browser")


def test_no_browser_flag_never_opens_a_browser(monkeypatch):
    call_order = []
    _patch_happy_path(monkeypatch, call_order)

    lw.run(["--no-browser"])

    assert "open_browser" not in call_order


def test_no_browser_flag_terminates_the_server_it_started(monkeypatch):
    call_order = []
    _patch_happy_path(monkeypatch, call_order)

    lw.run(["--no-browser"])

    assert "terminate" in call_order


def test_occupied_port_does_not_install_start_or_terminate_anything(monkeypatch):
    monkeypatch.setattr(lw, "check_python_version", lambda: None)
    monkeypatch.setattr(lw, "classify_port", lambda url: "occupied_by_other")

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("must not be called when the port is occupied by another process")

    monkeypatch.setattr(lw, "ensure_venv", _must_not_be_called)
    monkeypatch.setattr(lw, "dependencies_missing", _must_not_be_called)
    monkeypatch.setattr(lw, "install_dependencies", _must_not_be_called)
    monkeypatch.setattr(lw, "start_server_process", _must_not_be_called)
    monkeypatch.setattr(lw, "open_browser", _must_not_be_called)

    exit_code = lw.run([])

    assert exit_code == 1


def test_occupied_port_reports_a_readable_error(monkeypatch, capsys):
    monkeypatch.setattr(lw, "check_python_version", lambda: None)
    monkeypatch.setattr(lw, "classify_port", lambda url: "occupied_by_other")

    lw.run([])

    captured = capsys.readouterr()
    assert "will not terminate unrelated processes" in captured.err


def test_already_running_own_server_skips_install_and_reuses_it(monkeypatch):
    call_order = []
    monkeypatch.setattr(lw, "check_python_version", lambda: None)
    monkeypatch.setattr(lw, "classify_port", lambda url: "ours")

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("must not re-install or restart an already-running valid server")

    monkeypatch.setattr(lw, "ensure_venv", _must_not_be_called)
    monkeypatch.setattr(lw, "install_dependencies", _must_not_be_called)
    monkeypatch.setattr(lw, "start_server_process", _must_not_be_called)
    monkeypatch.setattr(lw, "wait_for_server_ready", lambda url: call_order.append("wait") or True)
    monkeypatch.setattr(lw, "open_browser", lambda url: call_order.append("open_browser"))

    exit_code = lw.run([])

    assert exit_code == 0
    assert call_order == ["wait", "open_browser"]


def test_missing_python_version_below_3_11_stops_before_any_install(monkeypatch):
    def _raise_version_error():
        raise lw.LauncherError("Shiori Pricing Lab requires Python 3.11 or later.")

    monkeypatch.setattr(lw, "check_python_version", _raise_version_error)

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("must not proceed past a failed version check")

    monkeypatch.setattr(lw, "classify_port", _must_not_be_called)
    monkeypatch.setattr(lw, "ensure_venv", _must_not_be_called)

    exit_code = lw.run([])

    assert exit_code == 1


def test_readiness_timeout_terminates_the_started_process_and_reports_error(monkeypatch, capsys):
    call_order = []
    monkeypatch.setattr(lw, "check_python_version", lambda: None)
    monkeypatch.setattr(lw, "classify_port", lambda url: "not_listening")
    monkeypatch.setattr(lw, "select_interpreter_command", lambda: ["python"])
    monkeypatch.setattr(lw, "ensure_venv", lambda root, interpreter: Path("/fake/venv/python"))
    monkeypatch.setattr(lw, "dependencies_missing", lambda venv_py: False)
    monkeypatch.setattr(
        lw,
        "start_server_process",
        lambda venv_py, root: types.SimpleNamespace(
            terminate=lambda: call_order.append("terminate"),
            wait=lambda timeout=None: call_order.append("wait"),
        ),
    )
    monkeypatch.setattr(lw, "wait_for_server_ready", lambda url: False)

    exit_code = lw.run([])

    assert exit_code == 1
    assert "terminate" in call_order
    assert "did not become ready" in capsys.readouterr().err
