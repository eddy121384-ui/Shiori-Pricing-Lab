"""Minimum Python -> C++ Rates Engine invocation surface (Issue #227).

This module is deliberately thin: it is the **smallest** seam between the Python
application and the C++ Rates Engine CLI (``cpp/rates_engine/``). Python remains
responsible for the user interface, market-data acquisition and persistence, research
workflows, and validation; the C++ engine owns deterministic computation and performs
**no live Bloomberg access**. This module performs no network access either -- it has
no HTTP, socket, or Bloomberg import anywhere -- and it never invents market data,
pricing results, or a path to an executable that is not on disk.

A call has exactly three outcomes:

* **exit 0** -- stdout is the result JSON object, returned as a ``dict``;
* **exit 2** -- the engine's *fail-closed domain refusal*: stdout carries
  ``errors[0].{code,message,detail}`` and that refusal is raised as
  :class:`RatesEngineError` **with the engine's own code** (it is a result, not a
  transport failure);
* **anything else** -- a transport failure
  (``ENGINE_TRANSPORT_FAILURE``), including timeouts, a missing executable, an
  unusable JSON response, and non-zero exits the engine does not define.

The request is serialized with
:func:`shiori_pricing_lab.rates_bridge.canonical.dumps_bytes`, so the engine receives
the same canonical UTF-8 JSON bytes Python would fingerprint. The request object is
never mutated.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from shiori_pricing_lab.rates_bridge.canonical import dumps_bytes

__all__ = [
    "ENGINE_CLI_NOT_FOUND",
    "ENGINE_TRANSPORT_FAILURE",
    "RatesEngineError",
    "engine_identity",
    "find_engine_cli",
    "invoke",
]

#: Bridge-side error token: no engine executable could be resolved.
ENGINE_CLI_NOT_FOUND = "ENGINE_CLI_NOT_FOUND"

#: Bridge-side error token: the engine process did not deliver a usable result.
ENGINE_TRANSPORT_FAILURE = "ENGINE_TRANSPORT_FAILURE"

#: Default operation -- the skeleton's input-shape validation call.
DEFAULT_OPERATION = "validate-kernel-input"

#: The engine's own identity echo (build, compiler, QuantLib, version metadata).
IDENTITY_OPERATION = "engine-identity"

#: Default wall-clock budget for one engine call, in seconds.
DEFAULT_TIMEOUT_S = 60.0

#: The repository root is found by walking up to the directory holding this file.
_REPOSITORY_MARKER = "AGENTS.md"

#: Engine CLI executable name (built artifacts live under the git-ignored build tree).
_CLI_BASENAME = "shiori_rates_cli"
_CLI_FILENAMES = (_CLI_BASENAME, _CLI_BASENAME + ".exe")

#: Explicit override, e.g. a CI job that knows exactly which artifact to use.
_CLI_ENV_VAR = "SHIORI_RATES_ENGINE_CLI"

#: Out-of-source build root locked by issue #226 section 2.2.
_BUILD_RELATIVE_PATH = ("cpp", "rates_engine", "build")

#: CMake preset directory names defined for the C++ engine (issue #227). The list is a
#: search order, not a contract: the glob fallback below accepts any other layout.
_PRESET_DIRECTORIES = (
    "dev-gcc",
    "dev-msvc",
    "ci-release-gcc",
    "ci-release-msvc",
    "release-gcc",
    "release-msvc",
)


class RatesEngineError(Exception):
    """An engine invocation that did not return a usable result.

    ``code`` is a stable machine-readable token:

    * the engine's own ``errors[0].code`` for an exit-2 fail-closed refusal;
    * ``ENGINE_CLI_NOT_FOUND`` when no executable is available;
    * ``ENGINE_TRANSPORT_FAILURE`` for every other unusable outcome.

    ``message`` is human-readable, ``detail`` carries the engine's structured detail
    (or bridge-side context: exit code, stderr, timeout). No component of the error is
    synthesized when the engine did not supply it -- an unparseable refusal envelope
    becomes a transport failure, never an invented error code.
    """

    def __init__(self, code: str, message: str = "", detail: Any = None) -> None:
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(f"{code}: {message}" if message else code)

    def to_dict(self) -> dict[str, Any]:
        """Return the ``{code, message, detail}`` record (engine error shape)."""
        return {"code": self.code, "message": self.message, "detail": self.detail}


def find_engine_cli() -> Path | None:
    """Return the built engine CLI executable, or ``None`` when it is not present.

    Resolution order (first hit wins):

    1. ``SHIORI_RATES_ENGINE_CLI`` -- honored only when it names an existing *file*;
       a stale or wrong value falls through instead of failing the whole call;
    2. ``cpp/rates_engine/build/<preset>/shiori_rates_cli[.exe]`` for the known #227
       preset names, in the order listed here;
    3. any other ``shiori_rates_cli*`` file found under ``cpp/rates_engine/build/``,
       exact names preferred and the rest visited in a stable path order.

    The repository root is derived from this file's location by walking upward to the
    directory containing ``AGENTS.md``. A path is **never** fabricated: an unbuilt or
    absent engine yields ``None`` so the caller can report it plainly.
    """
    override = os.environ.get(_CLI_ENV_VAR, "").strip()
    if override:
        candidate = Path(override)
        if candidate.is_file():
            return candidate

    root = _repository_root()
    if root is None:
        return None
    build_root = root.joinpath(*_BUILD_RELATIVE_PATH)

    for preset in _PRESET_DIRECTORIES:
        for filename in _CLI_FILENAMES:
            candidate = build_root / preset / filename
            if candidate.is_file():
                return candidate

    if build_root.is_dir():
        matches = sorted(
            (path for path in build_root.rglob(_CLI_BASENAME + "*") if path.is_file()),
            key=lambda path: (path.name not in _CLI_FILENAMES, str(path).lower()),
        )
        if matches:
            return matches[0]
    return None


def invoke(
    request: Any,
    *,
    operation: str = DEFAULT_OPERATION,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    cli_path: Path | str | None = None,
) -> dict[str, Any]:
    """Run one engine *operation* on *request* and return its parsed JSON result.

    *request* may be:

    * a JSON value (``dict``, ``list``, ...) -- canonicalized here with
      :func:`~shiori_pricing_lab.rates_bridge.canonical.dumps_bytes`;
    * ``str`` -- already-canonical JSON text, sent unchanged;
    * ``bytes`` / ``bytearray`` -- already-canonical JSON bytes, sent unchanged.

    The request is only read, never mutated. ``cli_path`` overrides executable
    discovery (useful for tests and for a pinned CI artifact).

    Raises :class:`RatesEngineError` as described in the module docstring. The engine
    is always driven through ``[cli, "--operation", operation]``; nothing else on the
    command line is inferred.
    """
    cli = Path(cli_path) if cli_path is not None else find_engine_cli()
    if cli is None or not cli.is_file():
        raise RatesEngineError(
            code=ENGINE_CLI_NOT_FOUND,
            message="the Shiori Rates Engine CLI is not available",
            detail={
                "operation": operation,
                "cli_path": str(cli) if cli is not None else None,
            },
        )

    payload = _request_bytes(request)
    argv = [str(cli), "--operation", operation]

    # Binary pipes are used on purpose: they keep the child's stdin byte-exact (Python's
    # subprocess refuses bytes input once text mode is on), and stdout/stderr are then
    # decoded as UTF-8 explicitly below. Text mode would also translate newlines on the
    # way in, which is unacceptable for a byte-canonical bridge.
    try:
        completed = subprocess.run(
            argv,
            input=payload,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RatesEngineError(
            code=ENGINE_TRANSPORT_FAILURE,
            message=f"the Shiori Rates Engine did not answer within {timeout_s} seconds",
            detail={"operation": operation, "cli_path": str(cli), "timeout_s": timeout_s},
        ) from exc
    except OSError as exc:
        raise RatesEngineError(
            code=ENGINE_TRANSPORT_FAILURE,
            message=f"the Shiori Rates Engine could not be executed: {exc}",
            detail={"operation": operation, "cli_path": str(cli)},
        ) from exc

    stdout = _decode_utf8(completed.stdout, cli, operation, "stdout")
    stderr = _decode_utf8(completed.stderr, cli, operation, "stderr")

    if completed.returncode == 0:
        return _success_result(stdout, cli, operation)
    if completed.returncode == 2:
        raise _refusal(stdout, stderr, cli, operation)
    raise RatesEngineError(
        code=ENGINE_TRANSPORT_FAILURE,
        message=(
            f"the Shiori Rates Engine exited with code {completed.returncode} "
            "instead of 0 (result) or 2 (structured refusal)"
        ),
        detail={
            "operation": operation,
            "cli_path": str(cli),
            "exit_code": completed.returncode,
            "stderr": stderr,
        },
    )


def engine_identity(cli_path: Path | str | None = None) -> dict[str, Any]:
    """Return the engine's identity record (``--operation engine-identity``).

    The request body is the empty canonical object ``{}``: identity is a property of
    the executable, not of any market or model input.
    """
    return invoke({}, operation=IDENTITY_OPERATION, cli_path=cli_path)


def _repository_root() -> Path | None:
    """Return the repository root (the directory holding ``AGENTS.md``), or ``None``."""
    for candidate in Path(__file__).resolve().parents:
        if (candidate / _REPOSITORY_MARKER).is_file():
            return candidate
    return None


def _request_bytes(request: Any) -> bytes:
    """Return the exact bytes to write to the engine's stdin."""
    if isinstance(request, (bytes, bytearray)):
        return bytes(request)
    if isinstance(request, str):
        # Already-serialized canonical text: forwarded unchanged, never re-serialized.
        return request.encode("utf-8")
    return dumps_bytes(request)


def _decode_utf8(data: bytes, cli: Path, operation: str, stream: str) -> str:
    """Decode one engine stream as UTF-8, failing closed on invalid bytes."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RatesEngineError(
            code=ENGINE_TRANSPORT_FAILURE,
            message=f"the Shiori Rates Engine wrote non-UTF-8 bytes on {stream}",
            detail={"operation": operation, "cli_path": str(cli), "stream": stream},
        ) from exc


def _success_result(stdout: str, cli: Path, operation: str) -> dict[str, Any]:
    """Parse the exit-0 result, refusing anything that is not a JSON object."""
    try:
        response = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RatesEngineError(
            code=ENGINE_TRANSPORT_FAILURE,
            message="the Shiori Rates Engine exited 0 without a JSON response",
            detail={"operation": operation, "cli_path": str(cli), "stdout": stdout},
        ) from exc
    if not isinstance(response, dict):
        raise RatesEngineError(
            code=ENGINE_TRANSPORT_FAILURE,
            message="the Shiori Rates Engine response is not a JSON object",
            detail={"operation": operation, "cli_path": str(cli), "stdout": stdout},
        )
    return response


def _refusal(stdout: str, stderr: str, cli: Path, operation: str) -> RatesEngineError:
    """Build the error for an exit-2 refusal, or a transport failure if malformed."""
    response: Any = None
    try:
        response = json.loads(stdout)
    except json.JSONDecodeError:
        response = None

    errors = response.get("errors") if isinstance(response, dict) else None
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        first = errors[0]
        code = first.get("code")
        if isinstance(code, str) and code:
            message = first.get("message")
            return RatesEngineError(
                code=code,
                message=message if isinstance(message, str) else "",
                detail=first.get("detail"),
            )

    # The engine claimed a refusal but did not deliver the structured envelope: that is
    # a broken transport, and inventing an error code here would hide it.
    return RatesEngineError(
        code=ENGINE_TRANSPORT_FAILURE,
        message=(
            "the Shiori Rates Engine exited 2 without a structured errors[0] "
            "{code,message,detail} refusal"
        ),
        detail={
            "operation": operation,
            "cli_path": str(cli),
            "exit_code": 2,
            "stdout": stdout,
            "stderr": stderr,
        },
    )
