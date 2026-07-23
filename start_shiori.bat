@echo off
rem Thin shim only: locate a Python 3 interpreter, then hand off everything
rem else (repo-root resolution, venv, dependency install, server start,
rem readiness wait, browser open) to the testable Python launcher at
rem scripts\launch_workbench.py. No PowerShell, no execution-policy change,
rem no administrator rights, no recursive repository search.
setlocal

set "LAUNCHER=%~dp0scripts\launch_workbench.py"

where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    python "%LAUNCHER%" %*
    set "LAUNCH_RESULT=%ERRORLEVEL%"
    goto :done
)

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    py -3 "%LAUNCHER%" %*
    set "LAUNCH_RESULT=%ERRORLEVEL%"
    goto :done
)

echo ERROR: Python 3.11 or later was not found on PATH (checked "python" and "py").
echo Install it from https://www.python.org/downloads/ and try again.
rem CI (GitHub Actions sets CI=true) runs non-interactively -- never block
rem an automated run on a keypress that will never come.
if not "%CI%"=="true" pause
exit /b 1

:done
if not "%LAUNCH_RESULT%"=="0" (
    echo.
    echo Shiori workbench did not start successfully. See the message above.
    if not "%CI%"=="true" pause
)
exit /b %LAUNCH_RESULT%
