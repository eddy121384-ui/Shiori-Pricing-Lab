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
- ``GET /api/health`` -- returns ``{"api_contract": API_CONTRACT_ID}``, a
  revision-specific marker (Codex review, PR #139) the launcher probes
  before reusing an already-running process on this port, so a stale server
  from an older revision (lacking the Case JSON/export routes below) is
  never mistaken for a current, reusable one.
- ``GET /api/base`` -- prices the unmodified base case and returns
  ``{"case": <the bundled base case dict>, "overlay": <6-field dict>,
  "context": <read-only identity/date/quote dict>, "display": <display
  dict>}``. The page loads this once on startup so its initial values, its
  instrument identity, and its "Clear" action all come from the exact same
  base case / pricing response. ``case`` (Issue #138) lets the page hold
  one uniform "active case" concept from the very first load, whether that
  case is later replaced by an uploaded one or not.
- ``POST /api/price`` -- body is a JSON object with exactly the six overlay
  keys (see ``standalone_option_workbench_overlay.OVERLAY_FIELDS``). Applies
  the overlay to a fresh copy of the base case, prices it, and returns the
  display dict verbatim as JSON. A malformed body or invalid overlay key set
  returns HTTP 400 with ``{"error": "..."}``; a well-formed request that
  prices to a ``FAILED`` ``PricingResult`` still returns HTTP 200 with that
  result's own ``status``/``errors`` in the display dict -- a domain pricing
  failure is not a bridge error.

**Issue #138: local Case JSON load and current-run export.** Three more
routes, all stateless (nothing here is stored server-side across requests;
the browser holds whichever case is currently active and resends it):

- ``POST /api/case`` -- body is the raw bytes of one uploaded Case JSON
  file. Decodes strict UTF-8 (a malformed byte sequence raises
  ``UnicodeDecodeError``, never silently replaced), parses JSON, then prices
  the case through the same unmodified ``price_standalone_option_case`` and
  extracts overlay/context the same way ``/api/base`` does. Returns
  ``{"case": <parsed case>, "overlay": ..., "context": ..., "display": ...}``
  on HTTP 200 -- including a domain ``FAILED`` display, exactly like
  ``/api/base``/``/api/price``. Any decode, parse, or schema/builder failure
  returns HTTP 400 with ``{"error": "..."}``. The uploaded bytes are never
  written to disk and no client-supplied file path is ever read or echoed
  back -- only the file's content matters.
- ``POST /api/case/price`` -- body is ``{"case": <full case>, "overlay":
  <6-field dict>}``. Applies the overlay to a fresh copy of the given
  ``case`` (never the bundled one) and prices it, returning the display
  dict verbatim -- the explicit-case counterpart of ``/api/price`` for a
  page that has loaded a case other than the bundled default.
- ``POST /api/export/json`` / ``POST /api/export/markdown`` -- body is
  ``{"display": <the already-computed display dict>}``. Returns
  ``{"content": <text>, "filename": ..., "mime": ...}`` where ``content`` is
  exactly ``render_standalone_run_as_json``/``render_standalone_run_as_markdown``'s
  output on that dict, verbatim. These two routes call no pricing function
  of any kind -- they format an already-computed display dict the browser
  sends them, nothing else -- and write nothing to disk.

No route mutates the on-disk base case file. No caching, session, or
persistence of any kind: every request re-reads the base case from disk and
reprices from it, so results are always reproducible from
``examples/standalone_option_case.json`` alone.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from shiori_pricing_lab.app.standalone_option_run_export import (
    render_standalone_run_as_json,
    render_standalone_run_as_markdown,
)
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

# Codex review (PR #139): the launcher previously identified an already-
# running server as "ours" only by the static page title, which an older
# revision (started from a parent commit, before the Case JSON/export routes
# existed) still carries unchanged -- the launcher would then reuse that
# stale process, and the browser would load this commit's UI against a
# process whose in-memory route table 404s on /api/case, /api/case/price,
# and the export routes. GET /api/health exposes this exact API contract
# instead, so a server lacking it (any older revision, or an unrelated
# process on this port) is never mistaken for a current, reusable one. Bump
# this literal string whenever a route in this module's contract changes.
API_CONTRACT_ID = "shiori-standalone-workbench-api/case-json-export-v1"


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
        "case": base_case,
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


# --- Issue #138: Case JSON load and explicit-case pricing -----------------------


def decode_uploaded_case_bytes(raw_body: bytes) -> dict:
    """Strictly decode ``raw_body`` as UTF-8 and parse it as one JSON case object.

    ``bytes.decode("utf-8")`` is strict by default -- an invalid byte
    sequence raises ``UnicodeDecodeError`` rather than silently replacing or
    dropping bytes. Malformed (but validly-encoded) JSON raises the standard
    ``json.JSONDecodeError``. Neither exception is caught here; the HTTP
    handler maps both to HTTP 400 with an explicit message.
    """

    text = raw_body.decode("utf-8")
    return json.loads(text)


def price_uploaded_case(case: dict) -> dict:
    """Validate and price an uploaded case exactly like :func:`price_base_case`.

    Returns ``{"case": case, "overlay": ..., "context": ..., "display": ...}``.
    ``case`` is echoed back verbatim -- never re-read from disk, never
    written to disk -- so the browser can hold and resend the exact same
    object this call just validated, keeping this module fully stateless
    across requests. Raises whatever the existing typed constructors /
    context extraction raise for a schema problem (missing/unknown
    top-level key, invalid nested field, no matching reference-data
    record, ...); never caught or remapped here.
    """

    _, _, display = price_standalone_option_case(case)
    return {
        "case": case,
        "overlay": extract_standalone_option_case_overlay(case),
        "context": extract_standalone_option_case_context(case),
        "display": display,
    }


def price_explicit_case_with_overlay(case: dict, overlay: dict) -> dict:
    """Apply ``overlay`` to a fresh copy of ``case`` (not the bundled one) and price it.

    The explicit-case counterpart of :func:`price_overlay_case`, used once
    the page has an active case other than the bundled default. Returns the
    display dict verbatim; raises for a bad overlay key set or invalid
    field value exactly like :func:`price_overlay_case`.
    """

    overlaid_case = apply_standalone_option_case_overlay(case, overlay)
    _, _, display = price_standalone_option_case(overlaid_case)
    return display


_EXPORT_JSON_FILENAME = "shiori_standalone_option_run.json"
_EXPORT_MARKDOWN_FILENAME = "shiori_standalone_option_run.md"


def export_current_run_as_json(display: dict) -> dict:
    """Return ``{"content", "filename", "mime"}`` for ``display``'s JSON export.

    Calls only :func:`render_standalone_run_as_json` -- no pricing,
    comparison, calibration, or Bloomberg call of any kind, and writes
    nothing to disk. ``display`` is used exactly as the browser sends it;
    this function computes nothing about its contents beyond formatting.
    """

    return {
        "content": render_standalone_run_as_json(display),
        "filename": _EXPORT_JSON_FILENAME,
        "mime": "application/json",
    }


def export_current_run_as_markdown(display: dict) -> dict:
    """Return ``{"content", "filename", "mime"}`` for ``display``'s Markdown export.

    See :func:`export_current_run_as_json` -- same contract, Markdown text.
    """

    return {
        "content": render_standalone_run_as_markdown(display),
        "filename": _EXPORT_MARKDOWN_FILENAME,
        "mime": "text/markdown",
    }


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
        if self.path == "/api/health":
            self._write_json(200, {"api_contract": API_CONTRACT_ID})
            return
        if self.path == "/api/base":
            try:
                self._write_json(200, price_base_case())
            except Exception as exc:  # noqa: BLE001
                self._write_json(500, {"error": str(exc)})
            return
        self._write_json(404, {"error": f"no such route: {self.path}"})

    def _handle_api_price(self, raw_body: bytes) -> None:
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

    def _handle_api_case(self, raw_body: bytes) -> None:
        try:
            case = decode_uploaded_case_bytes(raw_body)
        except UnicodeDecodeError as exc:
            self._write_json(400, {"error": f"uploaded file is not valid UTF-8: {exc}"})
            return
        except json.JSONDecodeError as exc:
            self._write_json(400, {"error": f"invalid JSON body: {exc}"})
            return
        try:
            payload = price_uploaded_case(case)
        except Exception as exc:  # noqa: BLE001
            self._write_json(400, {"error": str(exc)})
            return
        self._write_json(200, payload)

    def _handle_api_case_price(self, raw_body: bytes) -> None:
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            self._write_json(400, {"error": f"invalid JSON body: {exc}"})
            return
        if not isinstance(body, dict) or "case" not in body or "overlay" not in body:
            self._write_json(
                400, {"error": "request body must be a JSON object with 'case' and 'overlay'"}
            )
            return
        try:
            display = price_explicit_case_with_overlay(body["case"], body["overlay"])
        except Exception as exc:  # noqa: BLE001
            self._write_json(400, {"error": str(exc)})
            return
        self._write_json(200, display)

    def _handle_export(self, raw_body: bytes, export_fn) -> None:
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            self._write_json(400, {"error": f"invalid JSON body: {exc}"})
            return
        if not isinstance(body, dict) or "display" not in body:
            self._write_json(400, {"error": "request body must be a JSON object with 'display'"})
            return
        try:
            payload = export_fn(body["display"])
        except Exception as exc:  # noqa: BLE001
            self._write_json(400, {"error": str(exc)})
            return
        self._write_json(200, payload)

    def _handle_api_export_json(self, raw_body: bytes) -> None:
        self._handle_export(raw_body, export_current_run_as_json)

    def _handle_api_export_markdown(self, raw_body: bytes) -> None:
        self._handle_export(raw_body, export_current_run_as_markdown)

    _POST_ROUTES = {
        "/api/price": _handle_api_price,
        "/api/case": _handle_api_case,
        "/api/case/price": _handle_api_case_price,
        "/api/export/json": _handle_api_export_json,
        "/api/export/markdown": _handle_api_export_markdown,
    }

    def do_POST(self) -> None:  # noqa: N802
        handler = self._POST_ROUTES.get(self.path)
        if handler is None:
            self._write_json(404, {"error": f"no such route: {self.path}"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length else b""
        handler(self, raw_body)


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
