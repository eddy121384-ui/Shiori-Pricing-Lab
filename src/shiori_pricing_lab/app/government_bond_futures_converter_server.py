"""Dedicated loopback backend for the Government Bond Futures Converter desk app
(Issue #206).

Scope: serve one page and the four routes that page needs. **This module adds
no pricing, quote-parsing, schedule, solver, CTD-sourcing or registry logic of
its own.** The three request handlers below are the *same function objects*
the Workbench already serves -- imported from
``standalone_option_workbench_server``, never copied:

    treasury_futures_contract_catalogue
    load_treasury_futures_ctd
    convert_treasury_futures

That import is deliberately the whole point of the module. Issue #206 requires
that the standalone app and the source Workbench give identical answers for
identical inputs, and the only way to guarantee that without a parity test
that can rot is for both servers to call literally the same code object. A
test asserts that identity with ``is``, so a future copy-paste breaks the
build rather than the desk.

The registry is likewise not restated here: the catalogue route returns
whatever ``pricing/treasury_futures_contract.TREASURY_FUTURES_CONTRACTS``
holds, so the app's contract list follows the production registry (ZT, ZF,
ZN, ZB, UXY, WN, FGBS, FGBM, FGBL, FGBX) automatically.

**Routes.**

- ``GET /`` / ``/index.html`` / ``/app.css`` / ``/app.js`` -- the static app.
- ``GET /api/health`` -- ``{"api_contract": API_CONTRACT_ID}``. The launcher
  probes this to know its own backend is up, exactly as the Workbench
  launcher does; the ID is specific to this app's route set so an unrelated
  service answering on the same port is never mistaken for it.
- ``GET /api/treasury-futures/contracts``
- ``POST /api/treasury-futures/ctd``
- ``POST /api/treasury-futures/convert``

**Binding.** :func:`create_server` binds ``127.0.0.1`` only -- never
``0.0.0.0`` -- so the backend is unreachable from the LAN. Port ``0`` is the
default: the OS hands out a free ephemeral port, which is what makes port
collision and stale-process reuse structurally impossible for this app rather
than something to detect and recover from. Read the real port back from
``server.server_address[1]`` after construction.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from shiori_pricing_lab.app.standalone_option_workbench_server import (
    convert_treasury_futures,
    load_treasury_futures_ctd,
    treasury_futures_contract_catalogue,
)

#: Loopback only. Not configurable: Issue #206 forbids a LAN listener, and an
#: overridable host is exactly how one gets introduced by accident later.
HOST = "127.0.0.1"

#: ``0`` means "let the OS pick a free port". See the module docstring.
EPHEMERAL_PORT = 0

#: Identifies this app's route set to its own launcher. Bump it whenever a
#: route below changes, for the same reason the Workbench bumps its own.
API_CONTRACT_ID = "government-bond-futures-converter-api/v1"

_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}


def asset_dir() -> Path:
    """Where the three static files live, in a source tree or a frozen build.

    PyInstaller unpacks bundled data under ``sys._MEIPASS`` (the ``_internal``
    directory of a onedir build), so ``__file__``-relative resolution -- which
    is correct in the source tree -- points into the frozen archive and finds
    nothing. Both cases are answered here rather than at each call site.
    """

    bundled = getattr(sys, "_MEIPASS", None)
    if bundled is not None:
        return Path(bundled) / "government-bond-futures-converter"
    return Path(__file__).resolve().parents[3] / "prototype" / "government-bond-futures-converter"


class _ConverterRequestHandler(BaseHTTPRequestHandler):
    server_version = "GovernmentBondFuturesConverter/1.0"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # The packaged app has no console to log to, and a desk tool that
        # prints a line per request to nowhere is only overhead.
        pass

    def _write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_static_file(self, file_name: str, content_type: str) -> None:
        body = (asset_dir() / file_name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _decoded_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length else b""
        body = json.loads(raw_body.decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("request body must be a JSON object")
        return body

    def do_GET(self) -> None:  # noqa: N802
        if self.path in _STATIC_FILES:
            file_name, content_type = _STATIC_FILES[self.path]
            self._write_static_file(file_name, content_type)
            return
        if self.path == "/api/health":
            self._write_json(200, {"api_contract": API_CONTRACT_ID})
            return
        if self.path == "/api/treasury-futures/contracts":
            self._write_json(200, treasury_futures_contract_catalogue())
            return
        self._write_json(404, {"error": f"no such route: {self.path}"})

    def do_POST(self) -> None:  # noqa: N802
        handler = {
            "/api/treasury-futures/ctd": load_treasury_futures_ctd,
            "/api/treasury-futures/convert": convert_treasury_futures,
        }.get(self.path)
        if handler is None:
            self._write_json(404, {"error": f"no such route: {self.path}"})
            return
        try:
            body = self._decoded_body()
        except (UnicodeDecodeError, ValueError) as exc:
            self._write_json(400, {"error": f"malformed request body: {exc}"})
            return
        try:
            self._write_json(200, handler(body))
        except Exception as exc:  # noqa: BLE001
            # Every failure the trader can act on -- an unsupported contract,
            # an incomplete manual CTD, Bloomberg unreachable, a missing
            # Bloomberg field -- arrives here as a domain exception whose
            # message already names what is wrong. It is forwarded verbatim
            # and the page renders it; a raw traceback never reaches the UI.
            self._write_json(400, {"error": str(exc)})


def create_server(host: str = HOST, port: int = EPHEMERAL_PORT) -> ThreadingHTTPServer:
    """Return an unstarted loopback-bound server. See the module docstring."""

    return ThreadingHTTPServer((host, port), _ConverterRequestHandler)
