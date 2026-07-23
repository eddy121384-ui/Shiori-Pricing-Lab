"""Repository-owned local launcher for the standalone bond-option workbench
(Issue #138). Invoked by ``start_shiori.bat`` at the repository root, or
directly with any Python 3.11+ interpreter.

Stdlib only -- this launcher installs the workbench's own runtime
dependencies (including QuantLib) into a repo-local ``.venv``, but never
depends on any of them being importable in the interpreter that runs *this*
file. It performs no pricing, no HTTP handling, and no page rendering of its
own -- it only prepares the environment, starts
``shiori_pricing_lab.app.standalone_option_workbench_server`` as a
subprocess, waits for it to answer, and opens a browser tab.

**Repository location.** :func:`resolve_project_root` locates the repo
relative to this file's own path (``<repo>/scripts/launch_workbench.py`` ->
``<repo>``) -- never by walking up looking for a marker file, never via the
current working directory. This is what makes double-clicking
``start_shiori.bat`` from an extracted ZIP whose path contains spaces,
Unicode, or parentheses work: every path this module builds is derived from
``Path(__file__).resolve()``, which Python resolves exactly regardless of
those characters.

**Editable install form.** ``pip install -e ".[quant]"`` must be run with
the repository root as the *working directory* and the literal string
``".[quant]"`` as the argument -- never the absolute project path baked into
the argument. Passing an absolute path containing spaces/parentheses as the
editable-install target is exactly the kind of thing that breaks on some
pip/setuptools versions; using ``cwd`` instead sidesteps the whole class of
problem. See :func:`build_install_command`.

**No PowerShell, no admin, no unrelated-process termination.** Every
external command this module runs is a plain, argv-list ``subprocess`` call
(no shell string, no PowerShell). Nothing here ever calls ``taskkill``,
``psutil``, or any other process-termination API against a process this
launcher did not itself start in the current run (see
:func:`classify_port` / the ``occupied_by_other`` branch in :func:`run`).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MIN_PYTHON = (3, 11)
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
# Duplicated (not imported) from standalone_option_workbench_server.py on
# purpose: this launcher must run even before the workbench's own package
# and its dependencies are installed, so it cannot import that module.
_EXPECTED_PAGE_MARKER = "Bond Option Pricer"


class LauncherError(Exception):
    """A launcher precondition failed. The message is meant to be shown to the user verbatim."""


def resolve_project_root(launcher_file: Path) -> Path:
    """Return the repository root, located relative to ``launcher_file`` only.

    ``launcher_file`` is ``<repo>/scripts/launch_workbench.py``, so the repo
    root is simply its resolved grandparent directory. Never searches the
    filesystem, never walks parent directories looking for a marker file,
    and never reads the current working directory -- this must give the
    same answer whether the user double-clicks the launcher from an
    extracted ZIP, a git checkout, or any other directory, including paths
    containing spaces, Unicode, or parentheses.
    """

    return launcher_file.resolve().parent.parent


def check_python_version(version_info=None) -> None:
    """Raise :class:`LauncherError` if the running interpreter is older than 3.11."""

    version_info = sys.version_info if version_info is None else version_info
    major, minor = version_info[0], version_info[1]
    if (major, minor) < MIN_PYTHON:
        raise LauncherError(
            "Shiori Pricing Lab requires Python 3.11 or later. This "
            f"interpreter is Python {major}.{minor}. "
            "Install Python 3.11+ from https://www.python.org/downloads/ "
            "and try again."
        )


def select_interpreter_command(which=shutil.which) -> list[str]:
    """Return the argv prefix for a Python 3 interpreter: ``python`` or ``py -3``.

    Mirrors, as a unit-testable function, the exact two-step fallback
    ``start_shiori.bat`` performs before any Python code can run at all --
    batch is the only thing that can execute before an interpreter is known
    to exist, so the ``.bat`` file encodes this same order directly. This
    function exists purely so that order, and the "neither is present"
    failure, has deterministic test coverage even though the literal
    batch-file bytes cannot be executed by pytest -- :func:`run` below does
    **not** call this (it uses ``sys.executable``, the interpreter already
    proven to satisfy :func:`check_python_version`, for every subprocess it
    starts, rather than re-deriving a selection that could disagree with
    it). The real entry point is proven separately by the Windows CI smoke
    job (see ``.github/workflows``).
    """

    if which("python") is not None:
        return ["python"]
    if which("py") is not None:
        return ["py", "-3"]
    raise LauncherError(
        "Python 3.11+ was not found on PATH (checked for both 'python' and "
        "'py'). Install it from https://www.python.org/downloads/ and try "
        "again."
    )


def venv_dir(project_root: Path) -> Path:
    return project_root / ".venv"


def venv_python(project_root: Path) -> Path:
    """Return the venv's own python executable path (platform-aware)."""

    root = venv_dir(project_root)
    if sys.platform == "win32":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def build_venv_create_command(python_exe: str, project_root: Path) -> list[str]:
    return [python_exe, "-m", "venv", str(venv_dir(project_root))]


def subprocess_env() -> dict[str, str]:
    """Return the environment for every child process this launcher starts.

    Sets ``PYTHONUTF8=1`` (PEP 540) on top of the inherited environment.
    Without it, a Python interpreter on Windows defaults text-file I/O with
    no explicit ``encoding=`` to the system ANSI codepage rather than UTF-8;
    setuptools' editable-install machinery writes its ``.pth`` file exactly
    that way, so a repository path containing a non-Latin-1 Unicode
    character (fully valid on an NTFS filesystem) made the whole
    ``pip install -e`` step fail with ``UnicodeEncodeError`` -- precisely
    the "path contains Unicode" case this launcher is required to support.
    UTF-8 mode fixes the encoding used for that file (and any other
    default-encoded I/O these child processes perform) regardless of the
    host's configured codepage/locale.
    """

    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    return env


def ensure_venv(project_root: Path, python_exe: str, run=subprocess.run) -> Path:
    """Create ``<project_root>/.venv`` if it does not already have a python, and return it."""

    target = venv_python(project_root)
    if target.exists():
        return target

    result = run(
        build_venv_create_command(python_exe, project_root),
        capture_output=True,
        text=True,
        env=subprocess_env(),
    )
    if result.returncode != 0 or not target.exists():
        raise LauncherError(
            f"Failed to create the virtual environment at {venv_dir(project_root)}.\n"
            f"{result.stderr}"
        )
    return target


def dependencies_missing(venv_python_exe: Path, run=subprocess.run) -> bool:
    """Return True unless the workbench's own package and QuantLib both import cleanly."""

    probe = (
        "import importlib\n"
        "for module_name in ("
        "'shiori_pricing_lab.app.standalone_option_workbench_server', 'QuantLib'"
        "):\n"
        "    importlib.import_module(module_name)\n"
    )
    result = run(
        [str(venv_python_exe), "-c", probe], capture_output=True, text=True, env=subprocess_env()
    )
    return result.returncode != 0


def build_install_command(venv_python_exe: Path) -> list[str]:
    """Return the exact editable-install command, meant to run with ``cwd=project_root``.

    Uses the literal ``".[quant]"`` spec, never a full path baked into the
    argument -- the working directory carries the (possibly
    space/Unicode/parenthesis-containing) path, not this argument, so pip
    never has to parse those characters out of an editable-install spec.
    """

    return [str(venv_python_exe), "-m", "pip", "install", "-e", ".[quant]"]


def install_dependencies(project_root: Path, venv_python_exe: Path, run=subprocess.run) -> None:
    result = run(
        build_install_command(venv_python_exe),
        cwd=str(project_root),
        capture_output=True,
        text=True,
        env=subprocess_env(),
    )
    if result.returncode != 0:
        raise LauncherError(
            "Failed to install dependencies with:\n"
            f"  cd {project_root}\n"
            f"  {' '.join(build_install_command(venv_python_exe))}\n"
            "This requires an internet connection on first launch. Details:\n"
            f"{result.stderr}"
        )


def classify_port(url: str, timeout: float = 1.0, opener=urllib.request.urlopen) -> str:
    """Return ``"not_listening"``, ``"ours"``, or ``"occupied_by_other"`` for ``url``.

    Only a response that actually looks like this workbench (HTTP 200 with
    the expected page marker) is treated as ``"ours"`` -- a bare "something
    answered" is not enough, since blindly reusing or overwriting an
    unrelated service on the same port would be unsafe. Never terminates or
    otherwise touches whatever is listening; classification only.
    """

    try:
        with opener(url, timeout=timeout) as response:
            status = response.status
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError):
        return "not_listening"
    if status == 200 and _EXPECTED_PAGE_MARKER in body:
        return "ours"
    return "occupied_by_other"


def wait_for_server_ready(
    url: str,
    timeout_seconds: float = 30.0,
    poll_interval: float = 0.25,
    probe=classify_port,
    sleep=time.sleep,
    clock=time.monotonic,
) -> bool:
    """Poll ``url`` until it looks like our own server, or ``timeout_seconds`` elapses."""

    deadline = clock() + timeout_seconds
    while True:
        if probe(url) == "ours":
            return True
        if clock() >= deadline:
            return False
        sleep(poll_interval)


def start_server_process(venv_python_exe: Path, project_root: Path, popen=subprocess.Popen):
    """Start the workbench server as a visible child process and return it (not waited on)."""

    return popen(
        [str(venv_python_exe), "-m", "shiori_pricing_lab.app.standalone_option_workbench_server"],
        cwd=str(project_root),
        env=subprocess_env(),
    )


def open_browser(url: str, opener=None) -> None:
    import webbrowser

    (opener or webbrowser.open)(url)


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="Launch the local Shiori standalone workbench.")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help=(
            "Start the server, wait for it to become ready, then exit "
            "without opening a browser. For automated smoke testing only."
        ),
    )
    return parser.parse_args(argv)


def run(argv=None) -> int:
    args = _parse_args(argv)
    project_root = resolve_project_root(Path(__file__))
    url = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/"

    try:
        check_python_version()

        status = classify_port(url)
        if status == "occupied_by_other":
            raise LauncherError(
                f"Port {DEFAULT_PORT} on {DEFAULT_HOST} is already in use by a "
                "process that does not look like the Shiori workbench. Stop "
                "that process, then try again. Shiori will not terminate "
                "unrelated processes."
            )

        process = None
        if status == "not_listening":
            # Codex review (PR #139): re-deriving the interpreter here via
            # select_interpreter_command()[0] silently drops the "-3" flag
            # from a ["py", "-3"] fallback result, so the venv could be
            # created with the `py` launcher's *default* Python (which may
            # not be 3.11+, or may be Python 2) instead of the interpreter
            # this launcher is actually running under -- the one
            # check_python_version() just verified above. sys.executable is
            # the absolute path to that exact, already-verified interpreter,
            # so there is no re-selection ambiguity at all.
            venv_py = ensure_venv(project_root, sys.executable)
            if dependencies_missing(venv_py):
                print("Installing workbench dependencies (first launch only)...")
                install_dependencies(project_root, venv_py)
            process = start_server_process(venv_py, project_root)

        if not wait_for_server_ready(url):
            if process is not None:
                process.terminate()
            raise LauncherError(
                f"The Shiori workbench server did not become ready at {url} in time."
            )

        print(f"Shiori workbench is ready at {url}")

        if args.no_browser:
            if process is not None:
                process.terminate()
                process.wait(timeout=10)
            return 0

        open_browser(url)
        print("Leave this window open while you use the workbench.")
        print("Close this window, or press Ctrl+C, to stop the server.")
        if process is not None:
            try:
                process.wait()
            except KeyboardInterrupt:
                process.terminate()
        return 0
    except LauncherError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(run())
