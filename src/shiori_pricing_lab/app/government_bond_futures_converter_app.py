"""Government Bond Futures Converter -- the desk app itself (Issue #206).

Double-click behaviour: this module *is* the application. It starts its own
private backend on a loopback ephemeral port inside this same process, opens
one chromeless browser window pointed at it, waits for the trader to close
that window, and exits. There is no console, no address bar, no localhost URL
to type, and no second process to leave behind.

**Why one process rather than a launcher plus a server.** The Workbench
launcher has to detect a stale server, classify an occupied port, and reuse or
refuse -- because its server is a separate long-lived process on a fixed port
8765. Two of those failure modes do not exist here, because this app does not
create them:

- *Stale prior process*: the backend is a daemon thread of this process, so it
  cannot outlive the window. Nothing can be left listening.
- *Port collision*: the backend binds port ``0``, so the OS hands out a free
  ephemeral port. There is no fixed port to collide on.

*Duplicate launch* is handled rather than designed away: one instance at a
time, enforced by :func:`acquire_single_instance`. A second double-click is
told the app is already running and exits without starting a backend or
opening a window.

What is left to handle is genuine: the browser being absent, the backend
failing to start, and Bloomberg being unavailable. The first two raise
:class:`ConverterAppError` and are shown to the trader in a dialog box with no
traceback in it; the third is not fatal at all -- the window opens, the page
reports what Bloomberg did not give, and offers Retry.

**Window shell.** Microsoft Edge in application mode
(``--app=<url>``), which is present on every supported workstation and shows a
window with no address bar, no tabs and no browser chrome. It is given a
throwaway ``--user-data-dir`` so it never touches, or is confused with, the
trader's own browser profile -- and so closing this window can never close
their browsing session, nor be kept alive by it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Sequence
from pathlib import Path

from shiori_pricing_lab.app.government_bond_futures_converter_server import create_server

#: What the trader sees. Never the internal package name.
APP_NAME = "Government Bond Futures Converter"

#: Where Edge lives on a standard Windows install, most specific first. The
#: 32-bit Program Files entry is first because that is where the per-machine
#: stable channel actually installs on x64 Windows.
EDGE_RELATIVE_PATH = Path("Microsoft") / "Edge" / "Application" / "msedge.exe"
EDGE_SEARCH_ENV_VARS = ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")


#: Session-scoped name of the mutex that makes this app single-instance. The
#: ``Local\`` prefix scopes it to the logged-on session, which is the right
#: boundary for a desktop app: two traders on the same terminal server each get
#: their own instance, while one trader cannot start two.
SINGLE_INSTANCE_MUTEX_NAME = "Local\\GovernmentBondFuturesConverter.SingleInstance"

#: Windows ``ERROR_ALREADY_EXISTS``. ``CreateMutexW`` still returns a valid
#: handle when the mutex exists, so the last-error code -- not a null handle --
#: is what says another instance owns it.
_ERROR_ALREADY_EXISTS = 183

#: Distinct from the generic failure code so a smoke test, or a shortcut that
#: checks it, can tell "already running" from "could not start".
EXIT_ALREADY_RUNNING = 3


class ConverterAppError(RuntimeError):
    """A startup failure with a message written for a trader, not a developer."""


class AlreadyRunningError(ConverterAppError):
    """Another instance of the app already owns the single-instance mutex."""


def _windows_mutex_api():
    """Return ``(CreateMutexW, GetLastError, CloseHandle)``, or ``None`` off Windows.

    Resolved lazily and behind a try/except so importing this module never
    depends on ``ctypes`` finding ``kernel32`` -- the tests import it on Linux.
    """

    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        return kernel32.CreateMutexW, (lambda: ctypes.get_last_error()), kernel32.CloseHandle
    except (ImportError, OSError, AttributeError):
        return None


def acquire_single_instance(
    create_mutex=None,
    last_error=None,
    close_handle=None,
    name: str = SINGLE_INSTANCE_MUTEX_NAME,
) -> object | None:
    """Claim the single-instance mutex, or refuse because one is already held.

    Returns the OS handle, which the caller keeps for the life of the process.
    **Nothing has to release it:** Windows drops the handle when the process
    ends for any reason, a crash or a kill included, so there is no stale state
    to detect or clean up -- which is exactly why this is a kernel object and
    not a lock file. A lock file would need a liveness check, a PID, and a
    recovery path for the case where the app was killed, and every one of those
    is a way to lock a trader out of their own tool.

    Off Windows there is no guard: this app ships only for Windows, and the
    kernel-object semantics above are the whole reason the design is safe. The
    two callables are injectable so the refusal path is testable anywhere, and
    ``name`` so a test can exercise the real syscall without colliding with a
    converter the trader actually has open.
    """

    if create_mutex is None or last_error is None:
        resolved = _windows_mutex_api()
        if resolved is None:
            return None
        create_mutex, last_error, resolved_close = resolved
        close_handle = resolved_close if close_handle is None else close_handle

    handle = create_mutex(None, True, name)
    if not handle:
        # The guard itself failed, which is not a reason to keep a trader out
        # of the converter. Start anyway: a duplicate window is a far smaller
        # problem than an app that will not open.
        return None
    if last_error() == _ERROR_ALREADY_EXISTS:
        # CreateMutexW hands back a *second* handle to the existing mutex, so
        # it is released here rather than left to process exit -- the refusing
        # process does exit immediately, but a caller that recovers instead
        # (the tests do) must not accumulate handles.
        if close_handle is not None:
            close_handle(handle)
        raise AlreadyRunningError(
            f"{APP_NAME} is already running.\n\n"
            "Switch to the window that is already open. If you cannot find it, "
            "close it from Task Manager and start the app again."
        )
    return handle


def find_browser(
    environ: dict[str, str] | None = None,
    exists=Path.is_file,
    which=shutil.which,
) -> Path:
    """Return the Edge executable to host the app window, or say it is missing.

    Checked in install-location order, then PATH. Never downloads, installs or
    falls back to a different browser: a silent fallback to whatever else is on
    the machine would open the app in a window with an address bar, which is
    the one thing the app shell exists to avoid.
    """

    env = os.environ if environ is None else environ
    for variable in EDGE_SEARCH_ENV_VARS:
        root = env.get(variable)
        if not root:
            continue
        candidate = Path(root) / EDGE_RELATIVE_PATH
        if exists(candidate):
            return candidate
    on_path = which("msedge")
    if on_path:
        return Path(on_path)
    raise ConverterAppError(
        "Microsoft Edge could not be found on this computer, and "
        f"{APP_NAME} needs it to display its window.\n\n"
        "Ask IT to install Microsoft Edge, then start this app again."
    )


def build_browser_command(browser: Path, url: str, profile_dir: Path) -> list[str]:
    """The exact command line that opens one chromeless app window at ``url``.

    ``--app`` is what removes the address bar, the tab strip and the rest of
    the browser chrome. ``--user-data-dir`` gives this window its own throwaway
    profile: without it Edge would hand the URL to the trader's already-running
    browser and exit immediately, and the app would have no window to wait on
    and would shut its own backend down instantly.
    """

    return [
        str(browser),
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1180,900",
    ]


def show_error_dialog(message: str, title: str = APP_NAME) -> None:
    """Put a startup failure in front of the trader, with no console to read.

    The packaged app is built windowed, so ``print`` goes nowhere at all. On
    Windows this is the OS message box; anywhere else (a developer running from
    a terminal, and the tests) it falls back to stderr. A failure to *display*
    the error must never replace it with a crash, so the fallback is also the
    handler for a message box that cannot be shown.
    """

    if sys.platform == "win32":
        try:
            import ctypes

            #: MB_OK | MB_ICONERROR | MB_SETFOREGROUND
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10 | 0x10000)
            return
        except Exception:  # noqa: BLE001 - the message matters more than how it arrives
            pass
    print(f"{title}: {message}", file=sys.stderr)


def start_backend() -> tuple[object, str]:
    """Start the private loopback backend and return it with its own URL.

    The serving thread is a daemon: if this process is killed outright, the
    backend dies with it rather than holding the process open.
    """

    try:
        server = create_server()
    except OSError as exc:
        raise ConverterAppError(
            f"{APP_NAME} could not start its internal service.\n\n"
            f"Windows reported: {exc}\n\n"
            "Try starting the app again. If it keeps failing, send this "
            "message to the desk's support contact."
        ) from exc
    host, port = server.server_address[0], server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True, name="converter-backend").start()
    return server, f"http://{host}:{port}/"


def run(argv: Sequence[str] | None = None) -> int:
    """Start the backend, open the window, wait for it, then shut everything down."""

    args = list(sys.argv[1:] if argv is None else argv)
    # Automated smoke testing only, never used by a trader: start the backend,
    # print the URL it is actually serving on, and keep serving until the
    # process is terminated -- so a packaged-build smoke test can drive the
    # real routes without a browser. The URL goes to stdout on its own line
    # and is flushed, because the caller reads it to know where to connect.
    no_window = "--no-window" in args

    server = None
    profile_dir = None
    try:
        # First, before anything is started: a refused second launch must not
        # bind a port, spawn a browser or create a profile directory. Held in a
        # local for the life of `run`, which is the life of the process.
        instance_guard = acquire_single_instance()  # noqa: F841 - held, not used
        browser = None if no_window else find_browser()
        server, url = start_backend()
        if no_window:
            print(url, flush=True)
            # Polled rather than a bare untimed wait: on Windows a blocking
            # wait with no timeout swallows Ctrl+C, and a smoke-test harness
            # that cannot interrupt the thing it started is a hung build.
            try:
                idle = threading.Event()
                while not idle.wait(0.5):
                    pass
            except KeyboardInterrupt:
                pass
            return 0

        profile_dir = Path(tempfile.mkdtemp(prefix="gbf-converter-"))
        process = subprocess.Popen(build_browser_command(browser, url, profile_dir))
        # Closing the window ends this wait, which shuts the backend down in
        # the `finally` below. That is the whole of "closing the app terminates
        # its private backend cleanly".
        process.wait()
        return 0
    except AlreadyRunningError as exc:
        # Not a failure, just a second double-click. Same short sentence either
        # way; only the channel differs, because a windowed build has no
        # console and a smoke test has no one to click a dialog.
        if no_window:
            print(str(exc), file=sys.stderr, flush=True)
        else:
            show_error_dialog(str(exc))
        return EXIT_ALREADY_RUNNING
    except ConverterAppError as exc:
        show_error_dialog(str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001
        # Anything unforeseen still reaches the trader as a sentence rather
        # than a traceback in a console that does not exist.
        show_error_dialog(
            f"{APP_NAME} stopped unexpectedly.\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "Please send this message to the desk's support contact."
        )
        return 1
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if profile_dir is not None:
            # The throwaway browser profile is ours alone, so removing it can
            # never touch the trader's own data. Best effort: a file still held
            # by an exiting Edge process must not turn a clean close into an
            # error dialog.
            shutil.rmtree(profile_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(run())
