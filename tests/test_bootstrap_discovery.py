"""Focused regression coverage for bootstrap interpreter discovery.

Covers the required lookup order shared by ``start_shiori.bat`` (the real
entry point) and ``select_interpreter_command`` in
``scripts/launch_workbench.py`` (its unit-testable mirror):

1. ``%USERPROFILE%\\.venvs\\shiori-bloomberg\\Scripts\\python.exe``
   (exists AND probes >= 3.11)
2. ``python`` (probes >= 3.11)
3. ``py -3`` (probes >= 3.11)
4. Clean ``LauncherError`` otherwise.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import launch_workbench as lw  # noqa: E402


def _ok(command, **kwargs):
    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def _fail(command, **kwargs):
    return subprocess.CompletedProcess(command, 1, stdout="", stderr="")


def _make_venv_exe(user_root: Path) -> Path:
    exe = user_root / ".venvs" / "shiori-bloomberg" / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("dummy", encoding="utf-8")
    return exe


def test_bloomberg_venv_selected_when_valid(tmp_path, monkeypatch):
    user_root = tmp_path / "user"
    user_root.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user_root))
    exe = _make_venv_exe(user_root)

    def _run(command, **kwargs):
        if command[0] == str(exe):
            return _ok(command)
        return _fail(command)

    which = {"python": "/usr/bin/python", "py": "py.exe"}.get
    assert lw.select_interpreter_command(which=which, run=_run) == [str(exe)]


def test_falls_through_to_python_when_venv_missing(tmp_path, monkeypatch):
    user_root = tmp_path / "user"
    user_root.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user_root))
    # No venv file created -> must skip the Bloomberg step entirely.

    seen = []

    def _run(command, **kwargs):
        seen.append(command[0])
        return _ok(command)

    which = {"python": "/usr/bin/python", "py": None}.get
    assert lw.select_interpreter_command(which=which, run=_run) == ["python"]
    assert seen == ["python"]


def test_falls_through_when_venv_present_but_probe_fails(tmp_path, monkeypatch):
    user_root = tmp_path / "user"
    user_root.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user_root))
    _make_venv_exe(user_root)

    def _run(command, **kwargs):
        if "shiori-bloomberg" in str(command[0]):
            return _fail(command)
        return _ok(command)

    which = {"python": "/usr/bin/python", "py": None}.get
    assert lw.select_interpreter_command(which=which, run=_run) == ["python"]


def test_falls_through_when_venv_probe_raises_oserror(tmp_path, monkeypatch):
    user_root = tmp_path / "user"
    user_root.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user_root))
    _make_venv_exe(user_root)

    def _run(command, **kwargs):
        if "shiori-bloomberg" in str(command[0]):
            raise OSError("not a valid Win32 application")
        return _ok(command)

    which = {"python": "/usr/bin/python", "py": None}.get
    assert lw.select_interpreter_command(which=which, run=_run) == ["python"]


def test_falls_back_to_py_when_python_probe_fails(tmp_path, monkeypatch):
    user_root = tmp_path / "user"
    user_root.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user_root))

    def _run(command, **kwargs):
        if command[0] == "python":
            return _fail(command)
        if command[0] == "py":
            return _ok(command)
        return _fail(command)

    which = {"python": "/usr/bin/python", "py": "py.exe"}.get
    assert lw.select_interpreter_command(which=which, run=_run) == ["py", "-3"]


def test_fails_cleanly_when_all_unavailable(tmp_path, monkeypatch):
    user_root = tmp_path / "user"
    user_root.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user_root))

    def _missing(name):
        return None

    with pytest.raises(lw.LauncherError, match="not found"):
        lw.select_interpreter_command(which=_missing, run=_fail)


def _read_bat() -> str:
    return (_REPO_ROOT / "start_shiori.bat").read_text(encoding="utf-8")


def test_bat_probes_bloomberg_before_python_before_py():
    text = _read_bat()
    bloomberg_probe = text.find("BLOOMBERG_PY")
    python_probe = text.find('python -c "%VERSION_PROBE%"')
    py_probe = text.find('py -3 -c "%VERSION_PROBE%"')
    assert bloomberg_probe != -1, "bat must probe the Bloomberg venv"
    assert python_probe != -1, "bat must keep the python probe"
    assert py_probe != -1, "bat must keep the py -3 probe"
    assert bloomberg_probe < python_probe < py_probe


def test_bat_requires_existence_and_version_probe_for_bloomberg():
    text = _read_bat()
    assert 'if not exist "%BLOOMBERG_PY%" goto :try_python' in text
    assert '"%BLOOMBERG_PY%" -c "%VERSION_PROBE%"' in text
    assert 'set PYCMD="%BLOOMBERG_PY%"' in text


def test_bat_error_mentions_all_three_candidates():
    text = _read_bat()
    assert "shiori-bloomberg venv" in text
    assert '"python" and "py -3"' in text


def test_bat_never_touches_repo_local_venv():
    text = _read_bat()
    # The bat is a thin bootstrap shim: it must never create the repo-local
    # venv itself, install packages, or touch blpapi -- that all lives in
    # launch_workbench.py. (Comments may mention ".venv" to document the
    # boundary; what matters is no creation/install commands.)
    assert "-m venv" not in text
    assert "pyvenv.cfg" not in text
    assert "pip install" not in text
    assert "blpapi" not in text
