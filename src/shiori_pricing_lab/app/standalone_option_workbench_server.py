"""Minimal local HTTP bridge for the standalone HTML workbench prototype
(``prototype/bond-option-workbench``, PR #136).

Scope: let the static HTML/CSS/JS page in ``prototype/bond-option-workbench``
drive one real manual pricing run through the existing, unmodified
``standalone_option_workbench.price_standalone_option_case`` -- nothing else.
Python standard library only (``http.server``, ``json``, ``pathlib``): no web
framework, no new dependency. This module adds no pricing, discounting,
accrual, scaling, or Greek logic of its own; it only (a) serves the three
static prototype files, (b) loads the bundled sanitized-synthetic base case
once per request, (c) applies the bounded six-field overlay from
``standalone_option_workbench_overlay``, and (d) calls
``price_standalone_option_case`` and returns its display dict verbatim as
JSON.

**Routes.**

- ``GET /`` -- serves ``prototype/bond-option-workbench/index.html``.
- ``GET /styles.css`` / ``GET /script.js`` -- serve the matching static file.
- ``GET /api/base`` -- prices the unmodified base case and returns
  ``{"overlay": <6-field dict>, "context": <read-only identity/date/quote
  dict>, "display": <display dict>}``. The page loads this once on startup
  so its initial values, its instrument identity, and its "Clear" action all
  come from the exact same base case / pricing response.
- ``POST /api/price`` -- body is a JSON object with exactly the six overlay
  keys (see ``standalone_option_workbench_overlay.OVERLAY_FIELDS``). Applies
  the overlay to a fresh copy of the base case, prices it, and returns the
  display dict verbatim as JSON. A malformed body or invalid overlay key set
  returns HTTP 400 with ``{"error": "..."}``; a well-formed request that
  prices to a ``FAILED`` ``PricingResult`` still returns HTTP 200 with that
  result's own ``status``/``errors`` in the display dict -- a domain pricing
  failure is not a bridge error.

No route mutates the on-disk base case file. No caching, session, or
persistence of any kind: every request re-reads the base case from disk and
reprices from it, so results are always reproducible from
``examples/standalone_option_case.json`` alone.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from shiori_pricing_lab.app.standalone_option_workbench import price_standalone_option_case
from shiori_pricing_lab.app.standalone_option_workbench_context import (
    extract_standalone_option_case_context,
)
from shiori_pricing_lab.app.standalone_option_workbench_overlay import (
    apply_standalone_option_case_overlay,
    extract_standalone_option_case_overlay,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BASE_CASE_PATH = PROJECT_ROOT / "examples" / "standalone_option_case.json"
PROTOTYPE_DIR = PROJECT_ROOT / "prototype" / "bond-option-workbench"

_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/script.js": ("script.js", "application/javascript; charset=utf-8"),
}

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def load_base_case() -> dict:
    """Return the bundled sanitized-synthetic base case, parsed fresh from disk."""

    return json.loads(BASE_CASE_PATH.read_text(encoding="utf-8"))


def price_base_case() -> dict:
    """Price the unmodified base case and return ``{"overlay", "context", "display"}``.

    Both this function and :func:`price_overlay_case` are the only two ways
    this module ever calls the pricing entry point -- always through the
    unmodified ``price_standalone_option_case``. ``context`` is the bounded,
    read-only identity/date/quote dict from
    ``standalone_option_workbench_context`` -- it never changes across a
    ``/api/price`` call in this round, since none of the six overlay fields
    touch the underlying instrument's identity, so it is fetched here once,
    on ``/api/base``, and never resent by ``/api/price``.
    """

    base_case = load_base_case()
    _, _, display = price_standalone_option_case(base_case)
    return {
        "overlay": extract_standalone_option_case_overlay(base_case),
        "context": extract_standalone_option_case_context(base_case),
        "display": display,
    }


def price_overlay_case(overlay: dict) -> dict:
    """Apply ``overlay`` to a fresh copy of the base case and price it.

    Returns the display dict verbatim. Raises ``ValueError`` (bad overlay
    key set) or any exception the existing typed constructors raise for an
    invalid field value (non-finite/non-positive number, unknown enum
    string, ...) -- never caught or remapped here.
    """

    base_case = load_base_case()
    overlaid_case = apply_standalone_option_case_overlay(base_case, overlay)
    _, _, display = price_standalone_option_case(overlaid_case)
    return display


class _WorkbenchRequestHandler(BaseHTTPRequestHandler):
    server_version = "ShioriStandaloneWorkbenchBridge/0.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def _write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_static_file(self, file_name: str, content_type: str) -> None:
        path = PROTOTYPE_DIR / file_name
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in _STATIC_FILES:
            file_name, content_type = _STATIC_FILES[self.path]
            self._write_static_file(file_name, content_type)
            return
        if self.path == "/api/base":
            try:
                self._write_json(200, price_base_case())
            except Exception as exc:  # noqa: BLE001
                self._write_json(500, {"error": str(exc)})
            return
        self._write_json(404, {"error": f"no such route: {self.path}"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/price":
            self._write_json(404, {"error": f"no such route: {self.path}"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length else b""
        try:
            overlay = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            self._write_json(400, {"error": f"invalid JSON body: {exc}"})
            return

        try:
            display = price_overlay_case(overlay)
        except Exception as exc:  # noqa: BLE001
            self._write_json(400, {"error": str(exc)})
            return

        self._write_json(200, display)


def create_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Return an unstarted server bound to ``host``/``port``."""

    return ThreadingHTTPServer((host, port), _WorkbenchRequestHandler)


def main() -> None:
    server = create_server()
    host, port = server.server_address[0], server.server_address[1]
    print(f"Shiori standalone workbench bridge serving on http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
