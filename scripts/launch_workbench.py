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

**Bloomberg's blpapi package.** Installed as its own separate pip step (see
:func:`build_bloomberg_install_command`), using Bloomberg's own package
index (``blpapi`` is not published on PyPI) -- never folded into
``".[quant]"`` or any other requirement, so the project's other
dependencies are never resolved against Bloomberg's index. A failure here
is a visible, actionable :class:`LauncherError` like every other install
step, not a silent skip.

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
#
# Codex review (PR #139): this used to be the static page title, which an
# older revision's already-running server still serves unchanged even
# though its in-memory route table predates the Case JSON/export routes.
# The launcher must verify this exact, revision-specific API contract
# (served by GET /api/health) before reusing port 8765, not just the page
# title -- so a stale server from a parent revision is never mistaken for a
# current, reusable one. Bump this alongside the server module's own
# API_CONTRACT_ID whenever the API contract changes.
_EXPECTED_API_CONTRACT_ID = "shiori-standalone-workbench-api/case-json-export-bloomberg-v7"


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


def venv_is_valid(venv_python_exe: Path, run=subprocess.run) -> bool:
    """Return True only if ``venv_python_exe`` exists and can actually run pip.

    A venv left behind by an interrupted or partially-deleted creation can
    have a python executable on disk while everything else is missing or
    broken -- the existence check alone is not proof the venv is usable. A
    truncated or non-executable interpreter file makes ``run`` raise
    ``OSError`` (e.g. ``PermissionError``, "not a valid Win32 application")
    rather than returning a failed ``CompletedProcess``, so that must also
    count as invalid rather than escaping as an unhandled traceback.
    """

    if not venv_python_exe.exists():
        return False
    try:
        result = run(
            [str(venv_python_exe), "-m", "pip", "--version"],
            capture_output=True,
            text=True,
            env=subprocess_env(),
        )
    except OSError:
        return False
    return result.returncode == 0


def ensure_venv(
    project_root: Path, python_exe: str, run=subprocess.run, rmtree=shutil.rmtree
) -> Path:
    """Create or repair ``<project_root>/.venv`` and return its python executable.

    An existing venv is reused only if it passes :func:`venv_is_valid`. An
    incomplete or corrupted one (python present but pip broken, e.g. from an
    interrupted first launch) is removed and recreated automatically rather
    than being handed back as if it were fine -- Eddy's local UAT hit exactly
    this: a half-finished ``.venv`` that this function previously treated as
    already set up.
    """

    target = venv_python(project_root)
    if venv_is_valid(target, run=run):
        return target

    root = venv_dir(project_root)
    if root.exists():
        rmtree(root)

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


# Bloomberg quote refresh: blpapi is not on PyPI -- Bloomberg publishes it on
# its own package index. It is installed as a separate pip step, never
# folded into ".[quant]" or any other requirement, so the project's other
# dependencies are never resolved against Bloomberg's index.
_BLOOMBERG_PACKAGE_INDEX_URL = "https://blpapi.bloomberg.com/repository/releases/python/simple/"


def bloomberg_dependency_missing(venv_python_exe: Path, run=subprocess.run) -> bool:
    """Return True unless ``blpapi`` already imports cleanly in the venv."""

    result = run(
        [str(venv_python_exe), "-c", "import blpapi"],
        capture_output=True,
        text=True,
        env=subprocess_env(),
    )
    return result.returncode != 0


def build_bloomberg_install_command(venv_python_exe: Path) -> list[str]:
    """Return the exact ``blpapi`` install command, using Bloomberg's own package index."""

    return [
        str(venv_python_exe),
        "-m",
        "pip",
        "install",
        f"--index-url={_BLOOMBERG_PACKAGE_INDEX_URL}",
        "blpapi",
    ]


def install_bloomberg_dependency(venv_python_exe: Path, run=subprocess.run) -> None:
    result = run(
        build_bloomberg_install_command(venv_python_exe),
        capture_output=True,
        text=True,
        env=subprocess_env(),
    )
    if result.returncode != 0:
        raise LauncherError(
            "Failed to install Bloomberg's blpapi package with:\n"
            f"  {' '.join(build_bloomberg_install_command(venv_python_exe))}\n"
            "This requires network access to Bloomberg's package index (a "
            "Bloomberg-networked workstation). Details:\n"
            f"{result.stderr}"
        )


def classify_port(url: str, timeout: float = 1.0, opener=urllib.request.urlopen) -> str:
    """Return ``"not_listening"``, ``"ours"``, or ``"occupied_by_other"`` for ``url``.

    Probes ``<url>api/health`` rather than ``url`` itself: only a response
    that carries the expected, revision-specific API contract ID is treated
    as ``"ours"`` -- a bare "something answered", or even a page that looks
    like this workbench's own static HTML, is not enough, since an older
    revision's already-running server still serves that same unchanged page
    while lacking the Case JSON/export routes this revision requires (Codex
    review, PR #139). Blindly reusing or overwriting an unrelated service,
    or a stale server from a different revision, on the same port would be
    unsafe. Never terminates or otherwise touches whatever is listening;
    classification only.
    """

    health_url = url.rstrip("/") + "/api/health"
    try:
        with opener(health_url, timeout=timeout) as response:
            status = response.status
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError:
        # A real HTTP error response (401/404/500/...) is proof something is
        # listening and answering -- just not us -- so it must never be
        # treated the same as "nothing there" (HTTPError is itself a
        # URLError subclass, so it has to be caught ahead of the broader
        # except below or it would fall into "not_listening"). This is also
        # exactly what an older revision's server returns here, since
        # /api/health did not exist before this fix.
        return "occupied_by_other"
    except (urllib.error.URLError, OSError, TimeoutError):
        return "not_listening"
    if status == 200 and _EXPECTED_API_CONTRACT_ID in body:
        return "ours"
    return "occupied_by_other"


def wait_for_server_ready(
    url: str,
    process=None,
    timeout_seconds: float = 60.0,
    poll_interval: float = 0.25,
    probe=classify_port,
    sleep=time.sleep,
    clock=time.monotonic,
) -> str:
    """Poll ``url`` until it looks like our own server, ``process`` exits, or timeout.

    Returns ``"ready"``, ``"exited"``, or ``"timed_out"``. ``process`` is
    optional -- pass ``None`` when there is no child process to monitor
    (reusing an already-running server this launcher did not itself
    start). The process is checked on *every* poll, not only after the
    full timeout elapses, so a server that crashes early is reported
    immediately with its real exit code and output (see :func:`run`)
    instead of only after silently waiting out the whole timeout window
    -- this is what Eddy's first local UAT was missing: a 30s wait that
    reported only "did not become ready" whether the server was merely
    slow or had already died.
    """

    deadline = clock() + timeout_seconds
    while True:
        if probe(url) == "ours":
            return "ready"
        if process is not None and process.poll() is not None:
            return "exited"
        if clock() >= deadline:
            return "timed_out"
        sleep(poll_interval)


def server_log_path(project_root: Path) -> Path:
    """Return the fixed path the server's own stdout/stderr are captured to."""

    return venv_dir(project_root) / "server_startup.log"


def read_server_log(project_root: Path) -> str:
    """Return the server's captured output so far, or a short note if none exists."""

    try:
        return server_log_path(project_root).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(no server output was captured)"


def start_server_process(venv_python_exe: Path, project_root: Path, popen=subprocess.Popen):
    """Start the workbench server as a visible child process and return it (not waited on).

    stdout/stderr are redirected to a plain text file (see
    :func:`server_log_path`), truncated fresh on every start, rather than
    inherited -- the simplest way to make the server's real output
    available for diagnosis if it crashes early or is merely slow to
    become ready, without building any logging framework. The launcher's
    own status messages (printed via plain ``print()``) still appear in
    the console exactly as before; only the child's own output moves to
    this file.
    """

    log_file = open(server_log_path(project_root), "w", encoding="utf-8", buffering=1)
    try:
        return popen(
            [
                str(venv_python_exe),
                "-m",
                "shiori_pricing_lab.app.standalone_option_workbench_server",
            ],
            cwd=str(project_root),
            env=subprocess_env(),
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    finally:
        log_file.close()


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
            if bloomberg_dependency_missing(venv_py):
                print("Installing Bloomberg's blpapi package (first launch only)...")
                install_bloomberg_dependency(venv_py)
            process = start_server_process(venv_py, project_root)

        outcome = wait_for_server_ready(url, process=process)
        if outcome == "exited":
            exit_code = process.poll()
            raise LauncherError(
                f"The Shiori workbench server process exited early (exit code "
                f"{exit_code}) before it became ready at {url}. Its captured "
                f"output:\n{read_server_log(project_root)}"
            )
        if outcome == "timed_out":
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            raise LauncherError(
                f"The Shiori workbench server did not become ready at {url} in "
                "time. It was still running but never answered as expected. "
                f"Its captured output so far:\n{read_server_log(project_root)}\n"
                "To diagnose further, run it directly with:\n"
                f"  {venv_python(project_root)} -m "
                "shiori_pricing_lab.app.standalone_option_workbench_server"
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
