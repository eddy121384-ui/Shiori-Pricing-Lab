@echo off
rem Thin shim only: locate a Python 3.11+ interpreter, then hand off
rem everything else (repo-root resolution, venv, dependency install,
rem server start, readiness wait, browser open) to the testable Python
rem launcher at scripts\launch_workbench.py. No PowerShell, no
rem execution-policy change, no administrator rights, no recursive
rem repository search.
setlocal

set "LAUNCHER=%~dp0scripts\launch_workbench.py"

rem Set from the root of the whole process tree so every descendant process
rem (this shell, the launcher, the venv's own pip, and any subprocess pip
rem itself spawns for an isolated build) inherits it via normal OS-level
rem environment inheritance -- see scripts\launch_workbench.py:subprocess_env()
rem for why this matters (a repository path containing certain Unicode
rem characters otherwise breaks the editable install on Windows).
set "PYTHONUTF8=1"

rem Codex review (PR #139): "python" existing on PATH is not enough -- it
rem may be a Python 2, an unsupported Python 3.x, or the Microsoft Store
rem execution alias stub (present on PATH by default on many Windows
rem installs even with no real Python). A quick, non-interactive version
rem probe (redirected from/to nul so a misbehaving stub cannot block on
rem input) decides whether each candidate is actually usable, falling
rem through to "py -3" exactly like a missing "python" would. The 3.11
rem threshold here intentionally mirrors MIN_PYTHON in
rem scripts\launch_workbench.py -- batch cannot import that constant.
set "VERSION_PROBE=import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"

python -c "%VERSION_PROBE%" <nul >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    set "PYCMD=python"
    goto :found
)

py -3 -c "%VERSION_PROBE%" <nul >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    set "PYCMD=py -3"
    goto :found
)

echo ERROR: Python 3.11 or later was not found (checked "python" and "py -3").
echo Install it from https://www.python.org/downloads/ and try again.
rem CI (GitHub Actions sets CI=true) runs non-interactively -- never block
rem an automated run on a keypress that will never come.
if not "%CI%"=="true" pause
exit /b 1

:found
%PYCMD% "%LAUNCHER%" %*
set "LAUNCH_RESULT=%ERRORLEVEL%"
if not "%LAUNCH_RESULT%"=="0" (
    echo.
    echo Shiori workbench did not start successfully. See the message above.
    if not "%CI%"=="true" pause
)
exit /b %LAUNCH_RESULT%
