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

**Bloomberg quote refresh.** One more stateless route:

- ``POST /api/case/bloomberg`` -- body is ``{"case": <full case>, "overlay":
  <6-field dict>, "bloomberg_security": <str>, "quote_side": "BID"|"MID"|
  "OFFER"}``. Applies the overlay to a fresh copy of ``case`` exactly like
  ``/api/case/price``, then calls the existing
  ``price_standalone_option_case_with_bloomberg_quote`` exactly once -- the
  expected ISIN always comes from the (overlaid) case's own
  ``bond_option.underlying_isin``, never from a separately supplied value.
  Returns the display dict verbatim, including its ``live_bloomberg_quote``
  section, on HTTP 200. Any validation, date, Bloomberg DAPI, or builder
  failure returns HTTP 400 with ``{"error": "..."}`` -- the case's own
  previous bond quote is never used as a fallback, and this route reprices
  fresh from Bloomberg every call (no cache, no polling).

**Trader-draft revision.** ``GET /api/base`` and ``POST /api/case`` are kept
unchanged for automated regression and developer use only -- the trader-facing
``index.html``/``script.js`` no longer calls either on load or expose a
"Load Case JSON" control. The normal trader workflow now starts with no
active case at all; a successful ``POST /api/bloomberg/bond`` lookup below
seeds an in-memory draft client-side (the bundled synthetic base case is
never copied into it), and completing that draft still goes through
``POST /api/case`` (reused, unmodified) once the trader has filled in every
required field, since that route already validates and prices a full case
dict exactly like this one.

**Instrument-first Bloomberg lookup.** One more stateless route:

- ``POST /api/bloomberg/bond`` -- body is ``{"bond_identifier": <str>,
  "quote_side": "BID"|"MID"|"OFFER"}``. ``bond_identifier`` is a plain
  12-character ISIN or 9-character CUSIP as the trader typed it (never a
  Bloomberg yellow-key ticker) -- parsed and symbology-qualified server-side
  via ``parse_bond_identifier``, then resolved via
  ``load_bloomberg_bond_identity_and_quote`` exactly once. Takes no expected
  ISIN: there is no active pricing case involved in this lookup at all, only
  one bond's own identity and one quote side's price/accrued interest.
  Returns that loader's dict verbatim, plus ``"acquired_at"`` (a Shiori
  acquisition timestamp) and ``"source_system"``, on HTTP 200 -- one
  complete result or a full HTTP 400 failure, never a partial one. This is
  a distinct concern from ``/api/case/bloomberg`` above, which still
  requires and reprices an active case; this route never touches or
  requires one.

No route mutates the on-disk base case file. No caching, session, or
persistence of any kind: every request re-reads the base case from disk and
reprices from it, so results are always reproducible from
``examples/standalone_option_case.json`` alone.
"""

from __future__ import annotations

import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from shiori_pricing_lab.app.standalone_option_run_export import (
    render_standalone_run_as_json,
    render_standalone_run_as_markdown,
)
from shiori_pricing_lab.app.standalone_option_workbench import (
    price_standalone_option_case,
    price_standalone_option_case_with_bloomberg_quote,
)
from shiori_pricing_lab.app.standalone_option_workbench_context import (
    extract_standalone_option_case_context,
)
from shiori_pricing_lab.app.standalone_option_workbench_overlay import (
    apply_standalone_option_case_overlay,
    extract_standalone_option_case_overlay,
)
from shiori_pricing_lab.data.bloomberg_bond_quote import (
    load_bloomberg_bond_identity_and_quote,
    parse_bond_identifier,
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
#
# Bumped to -v2 for the new POST /api/case/bloomberg route (Bloomberg quote
# refresh) -- an older server predating that route must not be reused
# either, for the same reason.
#
# Bumped to -v3 for the new POST /api/bloomberg/bond route (instrument-first
# Bloomberg lookup) -- same reasoning again.
#
# Bumped to -v4 for the trader-draft revision: no route signature changed,
# but the served index.html/script.js/styles.css changed substantially (no
# more synthetic-case bootstrap, no Load Case JSON control) -- a stale
# already-running process must not be reused and keep serving the old page.
API_CONTRACT_ID = "shiori-standalone-workbench-api/case-json-export-bloomberg-v4"


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


def price_case_with_bloomberg_quote(
    case: dict, overlay: dict, bloomberg_security: str, quote_side: str
) -> dict:
    """Apply ``overlay`` to a fresh copy of ``case``, then price with one live Bloomberg quote.

    Reuses :func:`apply_standalone_option_case_overlay` exactly like
    :func:`price_explicit_case_with_overlay`, then calls the existing
    ``price_standalone_option_case_with_bloomberg_quote`` exactly once --
    this function duplicates no Bloomberg field mapping, ISIN verification,
    pricing, discounting, Greek, timestamp, or provenance logic of its own.
    ``quote_side`` is passed through as given (a raw string is fine; the
    existing loader coerces and validates it) -- required, no default is
    ever substituted here. The expected ISIN the loader verifies against
    always comes from the (overlaid) case's own
    ``bond_option.underlying_isin``; this function accepts no separate
    expected-ISIN input. Returns the display dict verbatim, including its
    ``live_bloomberg_quote`` section. Raises whatever
    ``price_standalone_option_case_with_bloomberg_quote`` itself raises for
    a blank security, invalid quote side, envelope/date problem, or
    Bloomberg DAPI failure -- never caught or remapped here, and the case's
    original bond quote is never used as a fallback.
    """

    overlaid_case = apply_standalone_option_case_overlay(case, overlay)
    _, _, _, display = price_standalone_option_case_with_bloomberg_quote(
        overlaid_case, bloomberg_security=bloomberg_security, quote_side=quote_side
    )
    return display


def _shiori_acquisition_now() -> datetime:
    """Return one offset-aware Shiori acquisition timestamp via the platform clock.

    Mirrors ``standalone_option_workbench._shiori_acquisition_now`` exactly
    (not imported -- that one is module-private and scoped to the case-based
    Bloomberg pricing workflow's own ``pricing_timestamp``/``valuation_date``
    invariant, which does not apply here). The instrument-first bond lookup
    has no case or pricing timestamp to satisfy; this is the same kind of
    clock read, just for display alongside the resolved bond identity.
    Tests monkeypatch this exact function so no real clock is read in CI.
    """

    return datetime.now().astimezone()


_BLOOMBERG_SOURCE_SYSTEM = "BLOOMBERG_DAPI"


def lookup_bloomberg_bond(bond_identifier: str, quote_side: str) -> dict:
    """Parse ``bond_identifier`` (ISIN/CUSIP) and resolve one Bloomberg identity + quote.

    Instrument-first Bloomberg lookup: reuses :func:`parse_bond_identifier`
    to reject anything that is not a bounded 12-character ISIN or
    9-character CUSIP and to build the symbology-qualified request string
    (never a guessed yellow-key ticker), then calls
    :func:`load_bloomberg_bond_identity_and_quote` exactly once. Takes no
    expected ISIN and involves no active case at all -- this is a pure
    bond-identity/quote lookup, distinct from
    :func:`price_case_with_bloomberg_quote` above. Returns that loader's
    dict verbatim, plus ``"acquired_at"`` (one Shiori acquisition timestamp,
    captured only after a successful loader return) and
    ``"source_system"`` for display. Raises ``ValueError`` for an invalid
    identifier/quote_side, and ``BLIBloombergDapiError`` for any Bloomberg-side
    failure -- never caught or remapped here, and the clock is never read
    before the loader has actually succeeded.
    """

    _, bloomberg_identifier = parse_bond_identifier(bond_identifier)
    result = load_bloomberg_bond_identity_and_quote(
        identifier=bloomberg_identifier, quote_side=quote_side
    )
    acquired_at = _shiori_acquisition_now().isoformat(timespec="seconds")
    return {**result, "acquired_at": acquired_at, "source_system": _BLOOMBERG_SOURCE_SYSTEM}


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

    def _handle_api_case_bloomberg(self, raw_body: bytes) -> None:
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            self._write_json(400, {"error": f"invalid JSON body: {exc}"})
            return
        required_keys = {"case", "overlay", "bloomberg_security", "quote_side"}
        if not isinstance(body, dict) or not required_keys.issubset(body):
            self._write_json(
                400,
                {
                    "error": (
                        "request body must be a JSON object with 'case', 'overlay', "
                        "'bloomberg_security', and 'quote_side'"
                    )
                },
            )
            return
        try:
            display = price_case_with_bloomberg_quote(
                body["case"], body["overlay"], body["bloomberg_security"], body["quote_side"]
            )
        except Exception as exc:  # noqa: BLE001
            self._write_json(400, {"error": str(exc)})
            return
        self._write_json(200, display)

    def _handle_api_bloomberg_bond(self, raw_body: bytes) -> None:
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            self._write_json(400, {"error": f"invalid JSON body: {exc}"})
            return
        required_keys = {"bond_identifier", "quote_side"}
        if not isinstance(body, dict) or not required_keys.issubset(body):
            self._write_json(
                400,
                {
                    "error": (
                        "request body must be a JSON object with 'bond_identifier' "
                        "and 'quote_side'"
                    )
                },
            )
            return
        try:
            result = lookup_bloomberg_bond(body["bond_identifier"], body["quote_side"])
        except Exception as exc:  # noqa: BLE001
            self._write_json(400, {"error": str(exc)})
            return
        self._write_json(200, result)

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
        "/api/case/bloomberg": _handle_api_case_bloomberg,
        "/api/bloomberg/bond": _handle_api_bloomberg_bond,
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
