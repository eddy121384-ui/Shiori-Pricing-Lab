import pytest
from pathlib import Path
import subprocess
import os
import shutil
import sys

# Add scripts directory to sys.path manually since we are in a non-standard test environment
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import launch_workbench as lw

def test_select_interpreter_prefers_shiori_bloomberg_venv(tmp_path, monkeypatch):
    # Setup environment
    user_profile = tmp_path / "user"
    user_profile.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user_profile))
    
    # Create the shiori-bloomberg venv executable
    venv_py = user_profile / ".venvs" / "shiori-bloomberg" / "Scripts" / "python.exe"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("dummy", encoding="utf-8")
    
    def mock_run(command, **kwargs):
        # Success for the special venv
        if str(venv_py) in command:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(shutil, "which", lambda x: "/bin/" + x if x == "python" else None)

    result = lw.select_interpreter_command(run=mock_run)
    assert result == [str(venv_py)]

def test_select_interpreter_falls_back_to_python_when_venv_missing(tmp_path, monkeypatch):
    user_profile = tmp_path / "user"
    user_profile.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user_profile))
    
    def mock_run(command, **kwargs):
        if "python" in command and "shiori-bloomberg" not in str(command):
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr(shutil, "which", lambda x: "/bin/python" if x == "python" else None)
    
    result = lw.select_interpreter_command(which=lambda x: "/bin/python" if x == "python" else None, run=mock_run)
    assert result == ["python"]

def test_select_interpreter_falls_back_to_py_when_python_absent(tmp_path, monkeypatch):
    user_profile = tmp_path / "user"
    user_profile.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user_profile))
    
    def mock_run(command, **kwargs):
        if "py" in command:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr(shutil, "which", lambda x: "/bin/py" if x == "py" else None)
    
    result = lw.select_interpreter_command(which=lambda x: "/bin/py" if x == "py" else None, run=mock_run)
    assert result == ["py", "-3"]

def test_select_interpreter_fails_when_all_absent(tmp_path, monkeypatch):
    user_profile = tmp_path / "user"
    user_profile.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user_profile))
    
    def mock_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    result_func = lambda x: None
    
    with pytest.raises(lw.LauncherError, match="Python 3.11\+ was not found"):
        lw.select_interpreter_command(which=result_func, run=mock_run)
