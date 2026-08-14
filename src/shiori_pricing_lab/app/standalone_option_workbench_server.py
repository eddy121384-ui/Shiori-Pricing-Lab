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
  back -- only the file's content matters. **Issue #171:** a case with no
  manually-entered ``curve_points`` gets the live Bloomberg Curve #490
  Option Discount Curve injected before pricing (see
  ``inject_live_option_discount_curve_if_absent``); a case that already
  carries explicit curve nodes is priced exactly as before, and a Bloomberg
  or same-as-of failure returns HTTP 400 with no manual/sample/stale
  fallback.
- ``POST /api/case/price`` -- body is ``{"case": <full case>, "overlay":
  <6-field dict>}``. Applies the overlay to a fresh copy of the given
  ``case`` (never the bundled one) and prices it, returning the display
  dict verbatim -- the explicit-case counterpart of ``/api/price`` for a
  page that has loaded a case other than the bundled default.
- ``POST /api/case/validate`` (Issue #143) -- body is one full case dict.
  Runs ``build_request_from_standalone_option_case`` and nothing else, then
  discards the request: no pricing, no engine, no clock, no Bloomberg call.
  Returns ``{"ready": true, "error": null}`` on HTTP 200 when the reviewed
  typed builder accepts the draft, and ``{"ready": false, "error": "..."}``
  -- also HTTP 200 -- when it does not, carrying that exception's own
  message verbatim. This is what decides whether the browser's Price button
  is enabled, so a merely non-blank form can never enable it. **Issue #171:**
  an empty ``curve_points`` (the live-Workbench-flow shape) is validated
  against a wide, non-Bloomberg placeholder curve instead of the real
  production one (see ``_validation_only_placeholder_curve_points``) --
  still no Bloomberg call and no clock read here; the real curve is only
  fetched, and only actually enforced, when the trader clicks Price.
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
  Returns ``{"case": <the envelope that was actually priced>, "display":
  <the display dict, including its ``live_bloomberg_quote`` section>}`` on
  HTTP 200 -- mirroring ``POST /api/case``, so a client adopts a priced case
  instead of assembling one. Any validation, date, Bloomberg DAPI, or builder
  failure returns HTTP 400 with ``{"error": "..."}`` -- the case's own
  previous bond quote is never used as a fallback, and this route reprices
  fresh from Bloomberg every call (no cache, no polling). **Issue #171:**
  the overlaid case gets the same live Curve #490 injection as ``POST
  /api/case`` above, applied before the bond-quote refresh.

**S490 repo-carry Forward parity (Issues #173/#174/#175).** One more
stateless, read-only route -- see :func:`resolve_s490_repo_carry_parity` for
the full contract:

- ``POST /api/case/s490-repo-carry`` -- body is ``{"case": <full case>,
  "spot_settlement_date": "YYYY-MM-DD", "convention_profile": <str|null>}``.
  ``convention_profile`` is the trader's own selection, passed as browser
  state exactly like ``POST /api/bond/advanced-profile`` treats it, and is
  never defaulted or inferred here. It is read for one purpose: only the
  ``UST`` selection asserts the Federal Reserve coupon-payment convention
  Issue #175 approved for Treasuries, so an interim-coupon expiry on any
  other selection fails closed in the primitive rather than having a UST
  convention applied to it. Case A is unaffected either way. **Always acquires a fresh live
  Curve #490** (same-as-of gate included) and discards whatever
  ``curve_points`` the case carried, even a genuine manual trader override
  -- unlike ``POST /api/case``, which leaves manual curve nodes untouched
  for pricing (see :func:`acquire_production_curve_490_for_s490_parity`
  for why this route cannot share that leniency). Resolves the S490
  funding and repo-carry Forward for the case's own
  ``forward_settlement_date`` -- the date the bond forward settles, and
  therefore the date the reviewed pricing contract defines the forward
  clean price at (Codex P1 review of PR #178; Issue #174 derived at Expiry
  instead, on a premise Issue #177 removed -- see
  :func:`resolve_s490_repo_carry_parity`). Changing Expiry still recomputes
  it, through the convention profile that fills forward settlement.
  ``forward_clean_price_input`` is read by nothing here.
  Returns ``{"case": <the case actually priced, freshly acquired curve
  included>, "s490_repo_carry": {"funding": {...}, "forward": {...},
  "forward_clean_price_per_100": <float>,
  "forward_clean_price_treasury_fraction": <str>, "curve_acquisition":
  <str>, "case_curve_points_discarded": <int>}}`` on HTTP 200. Since Issue
  #175 ``forward`` also carries the primitive's interim-coupon fields
  (``interim_coupons``, ``interim_coupon_forward_value_per_100``,
  ``interim_coupon_treatment``, ``carried_spot_dirty_price_per_100``), so a
  coupon scheduled in ``(spot settlement, Expiry]`` is carried to Expiry
  from its own Federal Reserve payment date rather than refused, and each
  coupon's scheduled date, payment date, roll and reinvestment leg reach the
  response. Any validation, date, Bloomberg DAPI, or curve-range failure
  returns HTTP 400 with ``{"error": "..."}``. This is a parity/testing
  display only: it prices nothing through Black-76.

**Effective Forward for Black-76 (Issue #177).** No new route: the same two
pricing routes above (``POST /api/case``, ``POST /api/case/bloomberg``) now
resolve *which* Forward Black-76 prices from, through
:func:`apply_effective_forward_to_case` -- the Shiori Derived S490
repo-carry Forward by default, an explicitly typed Trader Forward Override
when one is active. The mode is read from the existing
``forward_clean_price_input.source_system`` field (no second override model,
no second production Forward input), the derivation reuses
:func:`resolve_s490_repo_carry_parity` unchanged, and both routes' display
dicts gain an ``effective_forward`` provenance section. ``POST
/api/case/validate`` gains the one offline precondition of derived mode (an
explicit ``spot_settlement_date`` on the case). Black-76 itself, the
discounting chain, the volatility contract, the strike, the notional, the
payoff direction and every Greek are untouched. See the module-level Issue
#177 note above :func:`apply_effective_forward_to_case` for the full
contract, including which cases it deliberately never touches.

**Trader-draft revision.** ``GET /api/base`` is kept unchanged for automated
regression and developer use only -- the trader-facing ``index.html``/
``script.js`` no longer calls it on load or expose a "Load Case JSON"
control. The normal trader workflow now starts with no active case at all;
a successful ``POST /api/bloomberg/bond`` lookup below seeds an in-memory
draft client-side (the bundled synthetic base case is never copied into it,
and its ``curve_points`` start empty), and completing that draft still goes
through ``POST /api/case`` once the trader has filled in every required
field other than the Option Discount Curve, since that route already
validates and prices a full case dict exactly like this one -- now also
injecting the live production curve (Issue #171) rather than requiring the
trader to key it manually.

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
  complete result or a full HTTP 400 failure, never a partial one. The
  loader's ``"bond_master"`` sub-dict (coupon, coupon frequency, dates,
  redemption amount, callable/sinkable flags, bond type, yield convention,
  business-day convention -- seven of these confirmed against real
  Bloomberg DAPI evidence as of PR #141's third revision) and its
  ``"bond_master_raw"`` sub-dict (three further Bloomberg mnemonics
  confirmed to return a value but kept display-only, never coerced into a
  typed field) both pass through unchanged; this route performs no Bond
  Master field mapping, parsing, or confirmation of its own. This is a
  distinct concern from ``/api/case/bloomberg`` above, which still requires
  and reprices an active case; this route never touches or requires one.

**Advanced-field profile (Issues #157, #161).** Two more stateless routes:

- ``POST /api/bond/advanced-profile`` -- body is ``{"convention_profile",
  "isin", "currency", "bond_master", "bond_master_raw", "valuation_date",
  "expiry_date"}``. ``convention_profile`` (Issue #157 P1-1 correction) is
  required **browser state**, not a server-computed value -- it names which
  convention profile the browser currently has selected; the server never
  defaults or infers it, and never falls back to any profile for a
  missing/blank/unregistered value. ``isin``, ``currency``, ``bond_master``
  and ``bond_master_raw`` come verbatim from one already-completed
  ``POST /api/bloomberg/bond`` response;
  ``valuation_date``/``expiry_date`` from the run itself (``expiry_date``
  may be omitted or ``null`` while the trader has not entered the expiry
  yet). Calls ``resolve_bond_advanced_field_profile`` exactly once and
  returns its result as ``{"supported", "convention_profile",
  "rejection_reasons", "fields", "pending_field_paths",
  "unresolved_fields"}``. This route makes no Bloomberg call, reads no
  clock, prices nothing, and stores nothing: it is a pure function of the
  body, so the browser can call it again whenever expiry or the selected
  profile changes. A bond whose *product shape or coupon schedule* the
  current pricing path cannot carry is a normal HTTP 200 answer with
  ``supported: false`` and its reasons -- not an error, and never a claim
  about who issued the bond -- while a malformed body, unregistered
  ``convention_profile``, or a missing QuantLib install returns HTTP 400.
  Since Issue #161 a supported answer may still be partial: ``fields``
  carries every field that resolved and ``unresolved_fields`` carries the
  per-field ``BLOCKED`` reasons, so one unknown field never blanks the seven
  that are known. (Issue #161 renamed this route from ``/api/ust/profile``:
  the resolver behind it is no longer UST-specific.)

- ``POST /api/bond/convention-profile/candidates`` -- body is ``{"currency",
  "bond_master"}``, both verbatim from the same Bloomberg response. Returns
  ``{"candidates", "reasons", "supported_convention_profiles"}``.
  ``candidates`` holds every registered profile whose stated currency and
  coupon frequency cover this bond. It is a narrowing, never a choice: an
  empty list is a refusal (no profile covers this bond, so there is nothing
  to select), and a non-empty list is what the trader selects from. **Shiori
  never picks for the trader, even when one name is left** -- narrowing by
  currency and coupon frequency is not issuer classification, and the
  Bloomberg field that would be (``SECURITY_TYP``) is still unconfirmed. No
  ISIN, CUSIP, or security name is read, and no issuer classification is
  claimed. ``supported_convention_profiles`` is the registry's own list, so
  the browser's selector cannot drift from the server's.

**Markets curve viewer (Issue #167).** One more stateless, read-only route:

- ``POST /api/bloomberg/option-discount-curve`` -- takes no request body.
  Calls two existing, unmodified production loaders, each exactly once:
  ``load_bloomberg_usd_sofr_option_discount_curve`` (Issue #165/#166, the
  ``S0490Z``/``S0490D`` Zero Rate / DF contract) and
  ``load_bloomberg_usd_sofr_par_rate_curve`` (Issue #168, the ``USOSFR*``
  SWAP Par Rate contract), joined by tenor. Returns ``{"curve_id",
  "curve_name", "source_system", "rate_basis", "acquired_at", "coverage",
  "nodes"}``, where each node is ``{"tenor", "maturity",
  "zero_rate_percent", "discount_factor", "par_rate_percent"}``. This route
  performs no bootstrap, no second curve, and no re-derivation of any field
  from another -- ``discount_factor`` comes from the Zero/DF loader's own
  discount-factor evidence and ``par_rate_percent`` from the par-rate
  loader's own points, both matched by tenor, never recomputed or converted
  into each other. A tenor the par-rate loader does not carry is ``None``
  (unavailable) on that node, never fabricated. A Bloomberg-side failure
  from either loader returns HTTP 502 with ``{"error": "..."}`` -- there is
  no sample-data fallback, live or otherwise, and a failure from one loader
  fails the whole response rather than returning a curve with only the
  other loader's data.

No route mutates the on-disk base case file. No caching, session, or
persistence of any kind: every request re-reads the base case from disk and
reprices from it, so results are always reproducible from
``examples/standalone_option_case.json`` alone.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from shiori_pricing_lab.app.standalone_option_run_export import (
    render_standalone_run_as_json,
    render_standalone_run_as_markdown,
)
from shiori_pricing_lab.app.standalone_option_workbench import (
    build_request_from_standalone_option_case,
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
from shiori_pricing_lab.data._validation import (
    _parse_iso_date,
    _require_finite_number,
    _require_non_blank,
)
from shiori_pricing_lab.data.bli_snapshot import (
    BLIBondQuote,
    BLICurvePoint,
    BLIForwardCleanPriceInput,
)
from shiori_pricing_lab.data.bli_standalone_contract import BLIStandaloneBondReferenceData
from shiori_pricing_lab.data.bli_standalone_option_request_builder import (
    resolve_standalone_bond_reference_by_isin,
)
from shiori_pricing_lab.data.bloomberg_bond_quote import (
    BLIBloombergDapiError,
    load_bloomberg_bond_identity_and_quote,
    parse_bond_identifier,
)
from shiori_pricing_lab.data.bloomberg_option_discount_curve import (
    DEFAULT_USD_SOFR_TENORS,
    USD_SOFR_CURVE_ID,
    USD_SOFR_CURVE_NAME,
    USD_SOFR_SOURCE_SYSTEM,
    load_bloomberg_usd_sofr_option_discount_curve,
)
from shiori_pricing_lab.data.bloomberg_usd_sofr_par_rate_curve import (
    load_bloomberg_usd_sofr_par_rate_curve,
)
from shiori_pricing_lab.pricing.bli_bond_advanced_field_resolver import (
    resolve_bond_advanced_field_profile,
)
from shiori_pricing_lab.pricing.bli_bond_convention_profile import (
    SUPPORTED_CONVENTION_PROFILE_NAMES,
    UST_CONVENTION_PROFILE,
    convention_profile_candidates,
    get_convention_profile,
)
from shiori_pricing_lab.pricing.bli_effective_forward import (
    EFFECTIVE_FORWARD_SOURCES,
    SHIORI_DERIVED_S490_FORWARD_SOURCE,
    TRADER_FORWARD_OVERRIDE_FORWARD_SOURCE,
    forward_clean_price_input_dict,
    select_effective_forward,
)
from shiori_pricing_lab.pricing.bli_repo_carry_forward import repo_carry_forward_clean_price
from shiori_pricing_lab.pricing.bli_s490_funding_resolver import (
    resolve_s490_repo_carry_funding,
)
from shiori_pricing_lab.pricing.bli_treasury_price_format import (
    format_price_as_treasury_fraction,
)
from shiori_pricing_lab.pricing.bli_ust_coupon_payment_date import (
    UST_COUPON_PAYMENT_ROLL_CONVENTION,
)
from shiori_pricing_lab.products.enums import Currency, coerce_enum

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
#
# Bumped to -v5 for Bloomberg Bond Master loading/display: POST
# /api/bloomberg/bond's response gained a new "bond_master" key (see
# lookup_bloomberg_bond below), and the served index.html/script.js changed
# to read and display it -- a stale process predating this key must not be
# reused either, even though the frontend degrades gracefully if it is.
#
# Bumped to -v6 for PR #141's third revision: POST /api/bloomberg/bond's
# response gained a new "bond_master_raw" key (confirmed Bloomberg mnemonics
# that are display-only, never coerced into a typed BondReferenceData
# field), and the served index.html/script.js/styles.css changed
# substantially again (every main workbench section is now individually
# collapsible/expandable) -- a stale process predating either change must
# not be reused.
#
# Bumped to -v7 for Issue #143's manual explicit-forward completion path: no
# route signature changed, but the served index.html/script.js/styles.css
# changed substantially (trader inputs for the option dates, the eight
# still-missing BondReferenceData fields, the timing/settlement dates, and an
# Option Discount Curve node editor), and POST /api/case now normalizes
# offset-aware datetime instants to UTC at the contract boundary. A stale
# already-running process must not be reused and keep serving the old page.
#
# Bumped to -v8 for Issue #143's minimum-input trader workflow: POST
# /api/case/validate is a new route (the Price gate now asks the real typed
# builder, not the browser's own form state), and the served page was
# restructured into nine ordinary workflow groups plus one collapsed Advanced
# section. A stale process predating either change must not be reused.
#
# Bumped to -v9: POST /api/case/bloomberg now returns {"case", "display"}
# rather than the bare display dict, so the browser adopts the envelope that
# was actually priced instead of reassembling it. A stale process would still
# return the old shape, and the page would read `payload.display` as undefined.
#
# Bumped to -v10 for Issue #157's UST Advanced-field profile: POST
# /api/ust/profile is a new route the served page now calls after every
# Bloomberg Load and on every expiry change, and index.html/script.js changed
# to auto-fill the eight Advanced technical fields with provenance. A stale
# process predating the route would 404 it and silently leave the trader back
# on the field-by-field form.
#
# Bumped to -v11 for Issue #157 P1-1's correction: POST /api/ust/profile now
# requires a "convention_profile" request key (browser state, never
# defaulted server-side) and its response gained "convention_profile" and
# "unresolved_fields" keys. A stale process predating this would 400 every
# request the current page sends (missing key) or omit fields the current
# page reads.
#
# Bumped to -v12 for Issue #161: the same route's answer became field-level.
# "supported: false" now means only a product/schedule refusal no Advanced
# edit can repair, "fields" may be partial, and "unresolved_fields" actually
# carries per-field BLOCKED reasons the current page renders and gates its
# Go-to route on. A stale -v11 process would still answer, but with the
# all-or-nothing semantics that reject a real UST returning MTY_TYP=NORMAL.
#
# Bumped to -v13 for Issue #161's common-resolver split: POST
# /api/ust/profile is renamed POST /api/bond/advanced-profile (the resolver
# behind it is no longer UST-specific), and a convention-profile route is
# added so the page can render a visible, overridable Convention Profile
# selector sourced from the server's own registry. A stale -v12 process would
# 404 both, leaving the page with no profile selection and every Advanced
# field back on manual entry.
#
# Bumped to -v14 for Issue #161's follow-up: the second route is
# /api/bond/convention-profile/candidates (was .../suggest) and its response
# no longer carries "suggested" at all -- Shiori narrows the registry but
# never picks a profile, since no confirmed Bloomberg classification field
# exists to pick with. US_CORPORATE and GERMAN_GOVT are registered alongside
# UST, so a -v13 page would offer only UST, and a -v13 server would answer a
# current page's candidates request with a 404.
#
# Bumped to -v15 for Issue #167's read-only Markets curve viewer: POST
# /api/bloomberg/option-discount-curve is a new route, and the served page
# gained a Markets sidebar view (chart, summary strip, node table) alongside
# the unchanged Pricing view. A stale -v14 process would 404 the new route
# and the Markets nav item would have nothing to switch to.
#
# Bumped to -v16 once Issue #168's USOSFR* par-rate loader landed on main:
# POST /api/bloomberg/option-discount-curve now joins that loader's own
# points onto the Zero/DF nodes by tenor, so every node's par_rate_percent
# is a real Bloomberg value instead of always None. A stale -v15 process
# would still answer the route but every SWAP (Par Rate) cell would read
# unavailable even though the loader now exists.
#
# Bumped to -v17 for Issue #171's live Option Discount Curve wiring: POST
# /api/case and POST /api/case/bloomberg now inject the live Curve #490
# curve whenever the trader has entered no manual nodes, and the served
# script.js no longer blocks Price on an empty curve editor -- it treats
# zero manually-entered nodes as "will be sourced live from Bloomberg" and
# blocks only on a genuinely incomplete manual override. A stale -v16
# process would still require the trader to type curve nodes by hand, and a
# stale -v16 page would gate Price on that same now-removed requirement.
#
# Bumped to -v18 for Issue #174's S490 repo-carry Forward parity panel:
# POST /api/case/s490-repo-carry is a new route, and the served page gained
# a small read-only Trade-card section that auto-recomputes it whenever
# Expiry (or any other ticket input) changes and a Spot Settlement Date is
# entered. A stale -v17 process would 404 the new route and the panel would
# show nothing but a transport-error status forever.
#
# Bumped to -v19 for Issue #175's interim-coupon (Case B) support: an expiry
# with a coupon in the window now resolves instead of failing closed, the
# S490 repo-carry response carries the per-coupon trace (scheduled date,
# Federal Reserve payment date, roll, reinvestment leg) and the
# coupon-treatment label, and the served page gained the interim-coupon
# summary plus the collapsible derivation trace that read them. A stale -v18
# process would still refuse every interim-coupon horizon, and a stale -v18
# page would render neither.
#
# Bumped to -v20 for Issue #177's promotion of the Shiori Derived Forward to
# the Black-76 default: POST /api/case and POST /api/case/bloomberg now
# resolve the effective Forward server-side (see
# :func:`apply_effective_forward_to_case`) and their display dicts gained an
# "effective_forward" section, POST /api/case/validate gained the
# derived-mode fail-closed gate, the case envelope gained two optional keys
# (spot_settlement_date, convention_profile), and the served page auto-fills
# the single main Forward field from the derivation and offers "Use Shiori
# Derived". A stale -v19 process would silently price whatever number the
# page last put in that field -- including one derived against a spot quote
# the refresh has since replaced -- which is precisely the stale-Forward
# failure this contract exists to prevent.
API_CONTRACT_ID = "shiori-standalone-workbench-api/case-json-export-bloomberg-v20"


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


# --- Issue #171: live Bloomberg Option Discount Curve wiring --------------------
#
# Wires the existing production Curve #490 loader
# (``load_bloomberg_usd_sofr_option_discount_curve``, Issue #165/#166) directly
# into the normal Bloomberg-backed Workbench pricing routes (``POST /api/case``,
# ``POST /api/case/bloomberg``) so a Trader no longer has to key Option Discount
# Curve nodes manually. This performs no curve construction, bootstrap, or
# rate-basis conversion of its own -- it only places the loader's own
# ``BLICurvePoint`` rows (``OPTION_DISCOUNT_CURVE`` / ``CONTINUOUS_ZERO_RATE``,
# Bloomberg's own ``MATURITY`` preserved verbatim) into the same
# ``curve_points`` case-envelope field ``build_request_from_standalone_option_case``
# already parses -- the exact same contract a trader's manually-typed node or a
# test fixture already uses, never a second/parallel curve representation.
#
# **Trigger: an actual empty ``curve_points`` list, or a curve this same
# injector previously produced.** A fresh trader draft
# (``buildInitialDraftFromBloomberg`` in script.js) always starts with
# ``curve_points: []`` -- the manual node editor is optional, not required --
# so an empty list is exactly the live-Workbench-flow signal: the trader
# supplied no override, so the verified Bloomberg production curve is used.
# The check is exact-empty-list, not falsy (Codex P2 review of PR #172): a
# malformed non-list value (``null``, ``""``, ``{}``, ...) is never silently
# treated as "no override" -- it is left for the existing, more specific
# ``curve_points must be a JSON array`` schema error to report.
#
# ``POST /api/case`` echoes back whichever curve it actually priced with
# (Issue #143's existing "adopt the priced case" contract), so after one
# successful live-priced request the browser's own ``currentDraft.curve_points``
# is no longer empty -- it now holds the *previous* live acquisition. Without
# further care a second Price/Refresh click would then read as "the trader
# already supplied an override" and skip both the loader and the RED-gate
# same-as-of check entirely (Codex P1 review of PR #172), silently reusing a
# stale curve and never re-checking it against a possibly-rolled-over
# ``valuation_date``. ``_is_previously_injected_live_curve`` closes this by
# recognizing this injector's own output -- every row it writes matches a
# full fixed-field fingerprint (``_LIVE_CURVE_POINT_FIXED_FIELDS``: this
# injector's own ``curve_id``, ``curve_name``, ``currency``,
# ``curve_purpose``, ``rate_basis``, ``source_system``, ``status``), never
# just its ``curve_id`` alone (Codex P2 review of PR #172, round 2 -- a
# single shared token let a malformed or deliberately caller-supplied row
# using only that id slip past as a "previously injected" match instead of
# reaching the builder's own missing-field schema error). The manual editor
# never writes this fingerprint at all (its rows always carry the distinct
# ``SHIORI_MANUAL_OPTION_DISCOUNT_CURVE`` id and no ``maturity_date``) --
# and re-fetching fresh from Bloomberg every time, exactly like every other
# live Bloomberg call in this module (no cache, no polling, no stale reuse).
#
# A case that already carries explicit *manual* curve nodes (a trader
# override, or an existing deterministic/API test fixture) is left completely
# untouched: this is what keeps every pre-#171 test and every pre-#171
# fixture-based route call byte-for-byte unchanged, and it is also what keeps
# a manual override from being silently discarded once the trader has
# deliberately entered one.
#
# **RED-gate same-as-of contract (Issue #171 explicit audit requirement).**
# Curve #490 is a live, point-in-time observation with no historical query --
# there is no way to ask it for "the curve as of last Tuesday". Downstream,
# ``pricing/bli_zero_curve_nodes.py`` already prices a Bloomberg-sourced
# ``maturity_date`` row using the *request's* ``valuation_date`` as the curve's
# ``as_of_date`` (Issue #165 P1 follow-up, unchanged here) -- so pairing a
# curve fetched *now* with a ``valuation_date`` that is not *today* would
# silently misdate every discount factor the curve produces, exactly the
# failure mode Issue #171 named as a stop condition. This module already has
# an approved precedent for exactly this class of problem:
# ``price_standalone_option_case_with_bloomberg_quote`` requires its freshly
# acquired bond quote's acquisition date to equal ``valuation_date`` (the
# existing, unmodified ``pricing_timestamp.date() != valuation_date`` builder
# invariant). ``_require_valuation_date_matches_today`` below applies that
# same acquisition-date-must-equal-valuation_date discipline to the live
# curve fetch, using the same platform clock read
# (``_shiori_acquisition_now``) already defined in this module -- it never
# invents a curve date, never substitutes the system date for
# ``valuation_date``, and never weakens the existing explicit-date node
# contract; it only refuses to pair *today's* curve with a *different*
# ``valuation_date``, failing closed with a clear message rather than pricing
# silently against a mismatched as-of.
#
# **Fail closed.** Both ``load_bloomberg_usd_sofr_option_discount_curve`` and
# ``_require_valuation_date_matches_today`` raise on any problem
# (``BLIBloombergDapiError`` / ``ValueError``) -- neither is caught here, so
# the existing HTTP handlers' own broad ``except Exception`` -> HTTP 400
# already used by every other error case in these two routes reports it, and
# pricing never proceeds with old/manual/sample/stale curve data.


def _require_valuation_date_matches_today(valuation_date: object) -> None:
    """Raise ``ValueError`` unless ``valuation_date`` is today's local date.

    See the module-level "RED-gate same-as-of contract" note above. Reads the
    clock exactly once, via :func:`_shiori_acquisition_now` (this module's
    own, already-reviewed platform-clock seam -- monkeypatched directly by
    tests, so no real clock is read in CI). ``valuation_date`` is compared as
    a plain string; a missing/non-string value simply fails the equality
    check and is reported the same way, since the envelope parser rejects a
    missing ``valuation_date`` on its own regardless.
    """

    today = _shiori_acquisition_now().date().isoformat()
    if valuation_date != today:
        raise ValueError(
            "live Bloomberg Option Discount Curve pricing requires valuation_date "
            f"({valuation_date!r}) to equal today's date ({today!r}) -- Bloomberg "
            "Curve #490 is a live, point-in-time observation with no historical "
            "query, so it cannot be paired with a different valuation_date; enter "
            "Option Discount Curve nodes manually to price a non-current "
            "valuation_date"
        )


def _live_option_discount_curve_points_as_dicts() -> list[dict]:
    """Fetch the live Curve #490 zero-rate nodes as case-JSON-shaped dicts.

    Calls the existing, unmodified ``load_bloomberg_usd_sofr_option_discount_curve``
    exactly once, with its own default (full 32-tenor) universe -- never a
    caller-narrowed subset. ``dataclasses.asdict`` round-trips each returned
    ``BLICurvePoint`` into exactly the same field-name shape
    ``build_request_from_standalone_option_case`` already parses
    ``curve_points`` rows into via ``BLICurvePoint(**point)`` -- the same
    typed contract a manually-typed node or a JSON fixture already uses, not
    a second representation. Every field -- ``curve_purpose``,
    ``rate_basis``, Bloomberg's own ``maturity_date``, source system,
    provenance -- is preserved exactly as the loader returned it; this
    function performs no filtering, conversion, or reordering of its own
    (the loader itself already sorts and validates its returned tuple).
    """

    result = load_bloomberg_usd_sofr_option_discount_curve()
    return [asdict(point) for point in result.curve_points]


# Every fixed (never-varying-per-tenor) field the loader itself writes on
# every row it returns (see load_bloomberg_usd_sofr_option_discount_curve).
# Codex P2 review of PR #172, round 2: matching on curve_id alone let a
# malformed or deliberately caller-supplied row using that one id slip
# through as "previously injected" and be silently discarded -- e.g.
# ``[{"curve_id": "USD_SOFR_OPTION_DISCOUNT_CURVE"}]`` would have been
# treated as a stale echo instead of reaching the builder's real
# missing-field schema error. Requiring every one of these fields to match
# exactly is a full fingerprint of this injector's own output, not a single
# reusable token -- a row that matches every field this precisely either
# genuinely came from this injector, or is a caller deliberately
# constructing something indistinguishable from it, for which refetching
# the real live curve is the safe outcome, never a masked schema error.
_LIVE_CURVE_POINT_FIXED_FIELDS: dict[str, str] = {
    "curve_id": USD_SOFR_CURVE_ID,
    "curve_name": USD_SOFR_CURVE_NAME,
    "currency": "USD",
    "curve_purpose": "OPTION_DISCOUNT_CURVE",
    "rate_basis": "CONTINUOUS_ZERO_RATE",
    "source_system": USD_SOFR_SOURCE_SYSTEM,
    "status": "ACTIVE",
}
# Exactly BLICurvePoint's own field names (Codex P2 review of PR #172,
# round 5): the seven fixed fields above plus the three that vary per row.
# A row carrying any key outside this set -- or missing one inside it --
# would raise TypeError from BLICurvePoint(**point) (an unexpected keyword
# argument, for an extra key) even after matching every fixed value and
# passing every per-field validator, so the key set itself must match
# exactly before a row is trusted as an echoed acquisition.
_LIVE_CURVE_POINT_EXPECTED_KEYS = frozenset(_LIVE_CURVE_POINT_FIXED_FIELDS) | {
    "tenor",
    "rate",
    "maturity_date",
}


def _is_previously_injected_live_curve(curve_points: object) -> bool:
    """True when every row of ``curve_points`` matches this injector's own
    full field fingerprint (``_LIVE_CURVE_POINT_FIXED_FIELDS``, plus a
    genuinely present, individually-valid ``tenor``/``rate``/``maturity_date``)
    -- i.e. ``curve_points`` is not a genuine manual/fixture override but a
    prior live Curve #490 acquisition the browser adopted (see the
    module-level Issue #171 note above), safe and expected to be re-fetched
    fresh on every call.

    Every one of the three per-row fields ``BLICurvePoint.__post_init__``
    itself validates (``tenor`` via ``_require_non_blank``, ``rate`` via
    ``_require_finite_number``, ``maturity_date`` via ``_parse_iso_date``) is
    checked here with those exact same validators (Codex P2 review of PR
    #172, rounds 3-4: a whitespace-only ``tenor``, a non-finite ``rate``, or
    a non-ISO ``maturity_date`` each independently satisfied an earlier,
    narrower version of this predicate and would have been misclassified as
    a trusted echo). The row's own key set must also match
    ``_LIVE_CURVE_POINT_EXPECTED_KEYS`` exactly -- no missing key and no
    extra one (Codex P2 review of PR #172, round 5: an otherwise-matching
    row carrying one unexpected key still satisfied every check above, even
    though ``BLICurvePoint(**point)`` raises ``TypeError`` for that key). A
    row this predicate accepts is therefore guaranteed to also construct
    successfully via ``BLICurvePoint(**point)`` -- there is no field, value,
    or key left that the constructor checks and this predicate does not.

    Every check above is per-row; it says nothing about whether
    ``curve_points`` as a *collection* is this injector's own output. This
    injector only ever calls the loader with its own default (full 32-tenor)
    universe (see ``_live_option_discount_curve_points_as_dicts``), and that
    loader "never returns a partial curve" (its own module docstring) -- so
    a genuine echo of this injector's prior output always carries exactly
    ``DEFAULT_USD_SOFR_TENORS``' 32 tenor labels, in that same acquisition
    order, no more and no fewer. ``curve_points``' own tenor sequence must
    match that exactly (Codex P2 review of PR #172, round 6: a caller-
    supplied collection of only a few otherwise constructor-valid rows
    sharing this fingerprint satisfied every per-row check above and would
    have been misclassified as a trusted echo of the full acquisition,
    discarding what may have been a deliberate explicit-subset override).

    The loader also guarantees its own returned ``MATURITY`` strictly
    increases from one tenor to the next in that same order (one of its own
    fail-closed conditions, see its module docstring) -- so a genuine echo
    of a prior injection has strictly increasing ``maturity_date`` values in
    row order too. A collection with a repeated or reversed date cannot be
    this loader's output (Codex P2 review of PR #172, round 7: the tenor-
    sequence check alone still accepted such a collection, since duplicate/
    reversed dates are individually still valid ISO dates), so that
    invariant is checked here as well.

    ``curve_points`` that is not a non-empty list of dicts (including the
    fresh-draft ``[]``), whose tenor sequence does not exactly equal
    ``DEFAULT_USD_SOFR_TENORS``, whose ``maturity_date`` values do not
    strictly increase in that same order, or any row missing, disagreeing on
    even one fixed field, or failing any one of the three per-field checks,
    is never this shape -- it is left alone and reaches the builder's own,
    more specific validation unchanged.

    **Known, accepted boundary (Codex P2 review of PR #172, round 8).**
    This predicate matches *shape*, not provenance: it cannot tell "this is
    a genuine echo of my own prior output" apart from "this is a value a
    caller deliberately constructed that happens to be shape-identical to
    one" -- e.g. a caller who takes exactly the 32 rows a prior live price
    returned and edits one ``rate``, then reposts the case, produces
    something this predicate still accepts, and it is treated as a stale
    echo and re-fetched rather than honored as that one edited value. This
    is a deliberate trade-off, not an oversight: no route in this codebase
    can produce that shape any other way (the browser's manual editor
    always writes ``CURVE_ID`` = ``SHIORI_MANUAL_OPTION_DISCOUNT_CURVE``,
    never this loader's own ``curve_id``, so a real trader override is
    never shape-identical to an acquisition), and the only way to close it
    completely -- an explicit, out-of-band provenance marker the browser
    round-trips outside ``curve_points`` itself -- would add a new field to
    the shared case-envelope schema ``standalone_option_workbench.py``
    already validates strictly (unknown top-level keys are rejected
    outright), which is a bigger surface than this wiring slice's own scope
    (Issue #171: "Do not create a parallel pricing-input schema or second
    curve representation"). Failing toward a fresh, genuine Bloomberg
    acquisition is also the safe direction: the worst outcome of this
    boundary is pricing with real live data instead of a caller-supplied
    value that exactly impersonates it, never the reverse.
    """

    if not isinstance(curve_points, list) or not curve_points:
        return False
    if [point.get("tenor") if isinstance(point, dict) else None for point in curve_points] != list(
        DEFAULT_USD_SOFR_TENORS
    ):
        return False
    previous_maturity_date = None
    for point in curve_points:
        if not isinstance(point, dict):
            return False
        if point.keys() != _LIVE_CURVE_POINT_EXPECTED_KEYS:
            return False
        if any(point.get(key) != value for key, value in _LIVE_CURVE_POINT_FIXED_FIELDS.items()):
            return False
        try:
            _require_non_blank(point.get("tenor"), "tenor")
            _require_finite_number(point.get("rate"), "rate")
            maturity_date = _parse_iso_date(point.get("maturity_date"), "maturity_date")
        except ValueError:
            return False
        if previous_maturity_date is not None and maturity_date <= previous_maturity_date:
            return False
        previous_maturity_date = maturity_date
    return True


def inject_live_option_discount_curve_if_absent(case: dict) -> dict:
    """Return ``case`` unchanged, or a copy with a live Curve #490 curve injected.

    Triggered when ``case["curve_points"]`` is exactly an empty list, or is
    entirely rows this same injector previously produced (see
    ``_is_previously_injected_live_curve`` and the module-level Issue #171
    note above for why a repeat trigger is required) -- never for any other
    value, including a malformed non-list (left for the existing
    ``curve_points must be a JSON array`` schema error to report) or a case
    that already carries explicit manual curve nodes (a manual trader
    override, or an existing deterministic/API fixture), which is returned
    completely untouched.

    Raises whatever ``_require_valuation_date_matches_today`` or
    ``load_bloomberg_usd_sofr_option_discount_curve`` itself raises -- never
    caught or remapped here, and never any fallback to manual/sample/stale
    curve data.

    **Checked before *and* after the Bloomberg call (Codex P2 review of PR
    #172, round 5).** A single pre-fetch clock read leaves a real
    midnight-rollover race: a request starting at 23:59:59 could pass the
    gate against *today*, then have the Bloomberg round-trip itself cross
    into the next calendar day before returning, silently pricing a curve
    acquired tomorrow against yesterday's ``valuation_date`` -- precisely
    the mismatched-as-of failure this gate exists to prevent. Re-running the
    same check immediately after the loader returns, with a fresh clock
    read, closes that window: the acquisition is only ever accepted while
    both the moment it was requested *and* the moment it completed still
    agree with ``valuation_date``.
    """

    curve_points = case.get("curve_points")
    if curve_points != [] and not _is_previously_injected_live_curve(curve_points):
        return case
    valuation_date = case.get("valuation_date")
    _require_valuation_date_matches_today(valuation_date)
    live_curve_points = _live_option_discount_curve_points_as_dicts()
    _require_valuation_date_matches_today(valuation_date)
    return {**case, "curve_points": live_curve_points}


# --- S490 forced live acquisition (Issue #173 CLI, Issue #174 Workbench route) ---
#
# The injector above deliberately leaves a case that already carries
# explicit curve nodes completely untouched -- a manual trader override, an
# uploaded fixture, or the bundled sanitized-synthetic example all price
# exactly as supplied. That is correct for *pricing* (every route above this
# one relies on it), but it is the wrong contract for the S490 repo-carry
# Forward: that number is presented as "S490 Funding Rate" / "sourced from
# the live Bloomberg Curve #490 acquisition", and a case carrying manual
# nodes would silently derive it from hand-typed rates instead --
# fabricated Bloomberg evidence (AGENTS.md rule 6), and misleading on a
# ticket where the trader deliberately entered a manual curve override for
# Black-76 pricing (a legitimate, supported thing to do) without meaning to
# affect this separate parity display.
#
# Codex review of PR #174 (Issue #173's CLI tool, rounds 1-2) already
# established -- and rejected -- the alternative: checking each row's
# ``curve_id``/``source_system`` against the production loader's own
# published constants. Those are ordinary case JSON; any caller can stamp
# them onto hand-typed rows. Row metadata can never prove an acquisition
# happened, so this does not classify the supplied curve at all -- it
# **replaces** it. Clearing ``curve_points`` first makes the injector's own
# empty-list trigger always fire, so a fresh production Curve #490
# acquisition always runs (with its same-as-of RED gate applied both before
# and after the round trip) and the rows the S490 funding resolver reads
# are, by construction, the ones that acquisition just returned. Whatever
# curve nodes the case carried are discarded and counted; they cannot
# influence a single reported S490 number, no matter what they claim to be.
#
# Used by both ``resolve_s490_repo_carry_parity`` below (the Workbench
# route) and ``tools/ust_s490_repo_carry_forward_parity.py`` (the CLI) --
# one implementation, not two.
S490_CURVE_ACQUISITION_CONTRACT = "LIVE_PRODUCTION_CURVE_490_ACQUIRED_THIS_RUN"


def acquire_production_curve_490_for_s490_parity(
    case: dict, inject_curve=inject_live_option_discount_curve_if_absent
) -> tuple[dict, int]:
    """Return ``(case priced from a freshly acquired Curve #490, rows discarded)``.

    Clears ``curve_points`` first so :func:`inject_live_option_discount_curve_if_absent`'s
    own empty-list trigger always fires -- see the module-level note above
    for why a supplied curve is replaced rather than inspected or trusted.
    ``inject_curve`` defaults to that same production injector and is an
    injectable seam only for the CLI tool's own tests (no ``blpapi``
    available there); the Workbench route below never overrides it. Raises
    whatever the injector raises (a Bloomberg failure, the same-as-of
    gate); nothing is caught here.
    """

    supplied = case.get("curve_points")
    discarded = len(supplied) if isinstance(supplied, list) else 0
    return inject_curve({**case, "curve_points": []}), discarded


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

    **Issue #171:** before pricing, ``case`` passes through
    :func:`inject_live_option_discount_curve_if_absent` -- a case with no
    manually-entered ``curve_points`` gets the live Curve #490 curve injected
    (fail-closed on any Bloomberg problem); a case that already carries
    explicit curve nodes is priced exactly as before. The returned ``case``
    reflects whichever happened, so a caller adopting it (as the trader-draft
    browser flow does) sees the curve that was actually priced.

    **Issue #177:** the case then passes through
    :func:`apply_effective_forward_to_case`, which resolves the Forward
    Black-76 actually prices from -- the Shiori Derived S490 repo-carry
    Forward by default, or an active Trader Forward Override. The returned
    ``case`` again reflects what was priced (so an adopting caller's Forward
    field shows the authoritative number rather than its own last guess), and
    ``display`` gains an ``effective_forward`` provenance section. A case
    outside the two Issue #177 Forward modes is untouched and gains no such
    section, exactly as before.
    """

    # Before the live-curve fetch, so a declared override this run cannot
    # price under is refused for its own deterministic reason rather than
    # behind a Bloomberg failure -- see
    # :func:`validate_declared_trader_forward_override`.
    validate_declared_trader_forward_override(case)
    case = inject_live_option_discount_curve_if_absent(case)
    case, effective_forward = apply_effective_forward_to_case(case)
    _, _, display = price_standalone_option_case(case)
    if effective_forward is not None:
        display = {**display, "effective_forward": effective_forward}
    return {
        "case": case,
        "overlay": extract_standalone_option_case_overlay(case),
        "context": extract_standalone_option_case_context(case),
        "display": display,
    }


def _validation_only_placeholder_curve_points(case: dict) -> list[dict]:
    """Return a wide, deliberately fake curve for :func:`validate_case`'s
    structural check only -- see that function's Issue #171 docstring note
    for the full rationale. Never Bloomberg-sourced, never returned to a
    caller, and never used to price anything: it exists only so the real
    typed builder's ``curve_points`` non-empty check does not itself decide
    readiness for the live Workbench flow, whose ``curve_points`` is
    legitimately empty until the live curve is fetched at actual Price time.

    Spans ``1D`` to ``50Y`` -- comfortably wider than the real 32-tenor
    Curve #490 universe (``1W``-``50Y``) -- so it never itself becomes the
    reason a structurally sound draft reads not-ready. ``currency`` is read
    from ``case["bond_option"]["currency"]`` (the required standalone curve
    purpose gate in ``bli_standalone_option_request.py`` requires the
    Option Discount Curve in the *product's own* currency); a case whose
    ``bond_option`` is not yet a well-formed mapping gets no placeholder at
    all, so the original, more relevant build error surfaces unchanged.

    **Gated on USD (Codex P2 review of PR #172, round 2).** The live
    ``inject_live_option_discount_curve_if_absent`` can only ever inject a
    USD curve (the loader itself is USD SOFR only), so certifying
    ``ready: True`` for a non-USD draft with empty ``curve_points`` would be
    a false positive -- ``/api/case`` would still reject that same draft for
    lacking a matching-currency Option Discount Curve. A non-USD currency
    gets no placeholder either, so the real ``curve_points must not be
    empty`` error reports ``ready: False``, exactly matching what a real
    Price attempt would do (and exactly mirroring script.js's own
    ``LIVE_CURVE_CURRENCY`` gate on the browser side).
    """

    bond_option = case.get("bond_option")
    currency = bond_option.get("currency") if isinstance(bond_option, dict) else None
    if not isinstance(currency, str) or not currency:
        return []
    if currency != "USD":
        return []
    return [
        {
            "curve_id": "SHIORI_VALIDATION_ONLY_PLACEHOLDER_CURVE",
            "curve_name": "SHIORI_VALIDATION_ONLY_PLACEHOLDER_CURVE",
            "currency": currency,
            "curve_purpose": "OPTION_DISCOUNT_CURVE",
            "tenor": tenor,
            "rate": 0.03,
            "rate_basis": "CONTINUOUS_ZERO_RATE",
            "source_system": "SHIORI_VALIDATION_ONLY_PLACEHOLDER",
            "status": "ACTIVE",
        }
        for tenor in ("1D", "50Y")
    ]


def validate_case(case: dict) -> dict:
    """Return ``{"ready": bool, "error": str | None}`` for ``case``.

    Issue #143 requirement 5: whether Price is enabled must be decided by the
    real typed builder and route validation, never by whether the browser's
    own form fields happen to be non-empty. This route runs exactly
    :func:`build_request_from_standalone_option_case` -- the same envelope
    parsing, the same typed constructors, the same ISIN resolver, and the
    same ``is_standalone_bond_reference_data_eligible`` gate the real pricing
    call runs -- and then throws the request away. It prices nothing, calls
    no engine, reads no clock, and makes no Bloomberg call, so it is safe to
    call while the trader is still typing.

    A successful build returns ``{"ready": True, "error": None}``. Any
    envelope / schema / builder failure is reported as
    ``{"ready": False, "error": <that exception's own message>}`` -- verbatim,
    never reinterpreted or replaced with a friendlier guess, so the browser
    shows the reviewed contract's own words.

    **Issue #171:** an empty ``curve_points`` is the expected shape of a
    live-Bloomberg-pending draft (the real curve is only fetched, from
    Bloomberg, at actual Price time -- see
    :func:`inject_live_option_discount_curve_if_absent`), so it must not by
    itself make this readiness check fail. When ``case["curve_points"]`` is
    exactly an empty list (never a malformed non-list value -- Codex P2
    review of PR #172: that is left for the real ``curve_points must be a
    JSON array`` schema error to report), a copy with
    :func:`_validation_only_placeholder_curve_points` substituted is what is
    actually built and discarded here -- the caller's own ``case`` is never
    mutated, this route still makes no Bloomberg call and reads no clock,
    and a case that already carries explicit curve nodes (manual or a prior
    live acquisition -- either is already a real, structurally valid curve)
    is validated exactly as before.

    **Issue #177: the derived-Forward mode's own offline precondition.** A
    case in Shiori-derived Forward mode prices from a Forward this route
    cannot compute (that needs a live Bloomberg acquisition, which this route
    must never make) -- but one precondition of that derivation is a pure,
    offline fact: an explicit ``spot_settlement_date`` must be on the case.
    Without it there is no ``tS``, the derivation cannot run at Price time,
    and the run would fail closed there. Reporting it here instead is what
    keeps the Price button disabled until the ticket can actually price,
    rather than enabled onto a guaranteed failure. Every other derivation
    outcome stays where it belongs -- at Price time, against real Bloomberg
    data. A case in Trader-Forward-Override mode, or outside the two Issue
    #177 modes entirely, is unaffected by this check.
    """

    try:
        if isinstance(case, dict) and case.get("curve_points") == []:
            case = {**case, "curve_points": _validation_only_placeholder_curve_points(case)}
        _require_spot_settlement_date_for_derived_forward(case)
        build_request_from_standalone_option_case(case)
    except Exception as exc:  # noqa: BLE001
        return {"ready": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ready": True, "error": None}


def _require_spot_settlement_date_for_derived_forward(case: object) -> None:
    """Raise ``ValueError`` for a derived-Forward case with no ``spot_settlement_date``.

    Pure and offline -- reads two case keys and nothing else. See
    :func:`validate_case`'s own Issue #177 note for why this one precondition
    is checked at readiness time while every other derivation outcome is not.
    """

    if not isinstance(case, dict):
        return
    forward_input = case.get("forward_clean_price_input")
    if not isinstance(forward_input, dict):
        return
    if forward_input.get("source_system") != SHIORI_DERIVED_S490_FORWARD_SOURCE:
        return
    spot_settlement_date = case.get("spot_settlement_date")
    if not isinstance(spot_settlement_date, str) or not spot_settlement_date.strip():
        raise ValueError(
            "spot_settlement_date must be present: this run's Forward source is the "
            "Shiori Derived S490 repo-carry Forward, whose repo term starts at an "
            "explicit spot settlement date (tS). This repository never derives a "
            "settlement date from a lag, weekend, holiday or business-day convention, "
            "so it has to be entered"
        )


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
    expected-ISIN input.

    Returns ``{"case": <the envelope that was actually priced>, "display":
    <the display dict, including its ``live_bloomberg_quote`` section>}``.
    The case is the one the pricing workflow itself built and priced -- the
    only place the live quote and the acquisition timestamp are substituted --
    and is never reassembled here or by the browser (Issue #143, Codex review
    round 4). A client that adopts it cannot end up holding a case that
    differs from the one behind the result it is showing. This mirrors
    ``POST /api/case``, which already returns the case beside its display.

    Raises whatever
    ``price_standalone_option_case_with_bloomberg_quote`` itself raises for
    a blank security, invalid quote side, envelope/date problem, or
    Bloomberg DAPI failure -- never caught or remapped here, and the case's
    original bond quote is never used as a fallback.

    **Issue #171:** the overlaid case also passes through
    :func:`inject_live_option_discount_curve_if_absent`, exactly like
    :func:`price_uploaded_case` -- see that function's docstring. This runs
    before the bond-quote refresh below, so a live-curve same-as-of failure
    (see ``_require_valuation_date_matches_today``) is reported without ever
    calling the Bloomberg bond-quote loader.

    **Issue #177:** the effective Forward is resolved through
    :func:`apply_effective_forward_to_case` as the workflow's own
    ``case_transform`` -- i.e. *after* the freshly acquired spot quote has
    been substituted into the envelope and before it is priced. That ordering
    is the whole point: the Shiori Derived Forward is a function of the spot
    clean price, so deriving it from the case's previous quote and pricing
    the new one against it would be exactly the stale-Forward failure this
    wiring exists to prevent. A refresh therefore re-derives the default
    Forward from the quote it just acquired -- and leaves an active Trader
    Forward Override untouched, which is Issue #177's other explicit
    requirement. The returned ``case`` is still the exact envelope that was
    priced, and ``display`` gains the same ``effective_forward`` provenance
    section :func:`price_uploaded_case` returns.
    """

    overlaid_case = apply_standalone_option_case_overlay(case, overlay)
    # Same ordering as POST /api/case: the deterministic override check runs
    # before any Bloomberg call, curve or quote.
    validate_declared_trader_forward_override(overlaid_case)
    curve_points_before_injection = overlaid_case.get("curve_points")
    overlaid_case = inject_live_option_discount_curve_if_absent(overlaid_case)
    live_curve_acquired = overlaid_case.get("curve_points") is not curve_points_before_injection
    # Captured out of the transform rather than returned by it: the workflow's
    # seam is deliberately a plain dict -> dict case rewrite (see its own
    # docstring), so the provenance the same call produces is collected here
    # instead of widening that contract.
    captured: dict = {}

    def _apply_effective_forward(bloomberg_case: dict) -> dict:
        effective_case, effective_forward = apply_effective_forward_to_case(bloomberg_case)
        captured["effective_forward"] = effective_forward
        return effective_case

    _, _, _, display, priced_case = price_standalone_option_case_with_bloomberg_quote(
        overlaid_case,
        bloomberg_security=bloomberg_security,
        quote_side=quote_side,
        case_transform=_apply_effective_forward,
    )
    effective_forward = captured.get("effective_forward")
    if effective_forward is not None:
        display = {**display, "effective_forward": effective_forward}
    display = _with_accurate_refresh_scope(
        display,
        live_curve_acquired=live_curve_acquired,
        derived_forward_replaced=(
            effective_forward is not None
            and effective_forward["forward_source"] == SHIORI_DERIVED_S490_FORWARD_SOURCE
        ),
    )
    return {"case": priced_case, "display": display}


# What a Bloomberg refresh on this route actually re-sourced. The workflow's
# own ``prepare_live_bloomberg_quote_display`` states ``BOND_QUOTE_ONLY`` /
# ``CASE_JSON_UNCHANGED``, which is true of that function -- it substitutes
# exactly the quote -- but not of this route, which wraps it (Codex P1 review
# of PR #178, round 4). Two inputs beyond the quote can be re-sourced here
# before pricing:
#
# - the Option Discount Curve, since Issue #171, whenever the case carried no
#   manual nodes (a pre-#177 inaccuracy in the same sentence, corrected here
#   rather than left standing beside the new one);
# - the Forward, since Issue #177, whenever the run's source is the Shiori
#   derived one -- which is now the *default*, so on an ordinary derived-mode
#   refresh the quote-only claim is false about the single most important
#   number on the ticket.
#
# Stating otherwise in the display and the exported run would be a provenance
# claim contradicting the inputs actually priced (AGENTS.md rule 6), so the
# two fields are corrected here from what this route genuinely did.
# ``refreshed_inputs`` carries the same fact as an explicit list, so a reader
# never has to parse a compound label.
_REFRESHED_INPUT_BOND_QUOTE = "BOND_QUOTE"
_REFRESHED_INPUT_OPTION_DISCOUNT_CURVE = "LIVE_OPTION_DISCOUNT_CURVE"
_REFRESHED_INPUT_DERIVED_FORWARD = "SHIORI_DERIVED_FORWARD"
_OTHER_MARKET_INPUTS_UNCHANGED = "CASE_JSON_UNCHANGED"
_OTHER_MARKET_INPUTS_EXCEPT_REFRESHED = "CASE_JSON_UNCHANGED_EXCEPT_THE_REFRESHED_INPUTS"


def _with_accurate_refresh_scope(
    display: dict, *, live_curve_acquired: bool, derived_forward_replaced: bool
) -> dict:
    """Return ``display`` with its live-quote refresh scope corrected.

    See the note above for why the workflow's own quote-only labels are not
    the whole truth on this route. A refresh that genuinely re-sourced
    nothing but the quote is left byte-for-byte unchanged, so the pre-#171
    manual-curve path and every existing test keep their exact wording.
    """

    live_quote = display.get("live_bloomberg_quote")
    if not isinstance(live_quote, dict):
        return display
    refreshed = [_REFRESHED_INPUT_BOND_QUOTE]
    if live_curve_acquired:
        refreshed.append(_REFRESHED_INPUT_OPTION_DISCOUNT_CURVE)
    if derived_forward_replaced:
        refreshed.append(_REFRESHED_INPUT_DERIVED_FORWARD)
    if refreshed == [_REFRESHED_INPUT_BOND_QUOTE]:
        # Genuinely quote-only: returned completely untouched, so a legacy
        # (manual-curve, explicit-forward) refresh is still byte-for-byte the
        # workflow function's own output, and the presence of
        # ``refreshed_inputs`` at all means "more than the quote was
        # re-sourced".
        return display
    return {
        **display,
        "live_bloomberg_quote": {
            **live_quote,
            "refreshed_scope": "_AND_".join(refreshed),
            "other_market_inputs": _OTHER_MARKET_INPUTS_EXCEPT_REFRESHED,
            "refreshed_inputs": refreshed,
        },
    }


def resolve_s490_repo_carry_parity(
    case: dict, spot_settlement_date: str, convention_profile: str | None = None
) -> dict:
    """Resolve one Issue #173/#174/#175 S490 repo-carry Forward for ``case``'s own Expiry.

    Parity/testing display only -- not a pricing route, and it does not
    touch the existing explicit ``forward_clean_price_input`` override
    contract or any Black-76/discounting/volatility path. Composes,
    unmodified:

    - :func:`acquire_production_curve_490_for_s490_parity` for the S490
      curve. **This route always acquires a fresh production Curve #490,
      discarding whatever ``curve_points`` the case carried** -- unlike
      every pricing route in this module, which leaves a case's own manual
      curve nodes untouched. A ticket may legitimately carry a manual curve
      override for its Black-76 pricing; that override must never silently
      become the source of a number this panel presents as "S490 Funding
      Rate" (Codex P1 review of PR #174). See that function's own module-
      level note for why classifying the supplied curve's row metadata
      instead (an earlier, rejected answer to this same problem on the CLI
      tool) does not work;
    - :func:`shiori_pricing_lab.data.bli_standalone_option_request_builder.
      resolve_standalone_bond_reference_by_isin` for the resolved bond
      (Issue #96's own exact-ISIN match/eligibility logic, unmodified);
    - ``pricing/bli_s490_funding_resolver.resolve_s490_repo_carry_funding``
      (Issue #173) for the funding, under its own default prototype method;
    - ``pricing/bli_repo_carry_forward.repo_carry_forward_clean_price``
      (Issues #173/#175) for the forward, including its interim-coupon
      (Case B) carry. A coupon scheduled in ``(spot_settlement_date,
      forward_settlement_date]`` is carried to that settlement date from its
      own Federal Reserve payment date and netted off, so such a horizon
      returns HTTP 200 with the per-coupon trace on
      ``forward.interim_coupons`` -- it is not a refusal. **This route reads
      no coupon schedule, resolves no payment calendar and selects no coupon
      treatment of its own**: the primitive
      resolves the coupons from the same resolved bond reference data this
      route already passes it, rolls each date through
      ``pricing/bli_ust_coupon_payment_date``, and the whole trace reaches
      the response through the same ``dataclasses.asdict`` as every other
      forward field. Issue #175 required no new S490 transformation, so the
      funding resolver and its default method are byte-for-byte the ones
      Case A already used.

    **Deliberately does not call ``build_request_from_standalone_option_case``
    (Issue #174 round 6).** That builder requires a *complete* pricing
    case -- ``option_type``, ``position``, ``strike_price``, ``notional``,
    ``volatility_input``, ``forward_clean_price_input``, and six more
    top-level keys this route reads none of -- so gating this route on it
    made the S490 Forward wait for full Black-76 readiness, even though the
    derivation touches none of those fields. This route instead reads
    exactly the six inputs it needs directly off ``case``: the resolved
    bond (via ``bond_option.underlying_isin`` against
    ``bond_reference_data_universe``), the live spot clean quote
    (``bond_quote``), ``valuation_date``, ``spot_settlement_date`` (the
    caller argument), ``forward_settlement_date``, and the freshly acquired
    Curve #490. A Bloomberg-loaded bond alone already supplies the first
    three; ``forward_settlement_date`` comes from the selected convention
    profile as soon as Expiry is entered, so only Expiry and Spot Settlement
    Date remain genuinely trader-entered. No strike, notional, volatility, option type, position, or
    manual Forward override is read, required, or affected.

    **The forward settlement date (``tF``) is the case's own
    ``forward_settlement_date`` (Codex P1 review of PR #178).** Issue #174
    derived at ``bond_option.expiry_date`` instead, on the explicit premise
    that this route fed a read-only parity panel and *not* "the other,
    explicit-forward pricing path". Issue #177 removes that premise: this
    derivation now produces the Forward Black-76 prices from, and the
    reviewed pricing contract defines that number at
    ``request.forward_settlement_date`` -- ``resolve_standalone_option_pricing_inputs``
    converts the explicit forward clean price to dirty using
    ``AI(request.forward_settlement_date)``. Deriving a clean price at Expiry
    (i.e. netting off ``AI(expiry)``) and then adding back
    ``AI(forward_settlement_date)`` would pair a clean price and an accrual
    struck at two different dates, shifting the forward the option prices
    against by the accrual between them -- and those two dates differ in the
    ordinary workflow, where the convention profile derives forward
    settlement as Expiry plus the bond's settlement lag. Since Black-76,
    the accrual convention and the pricing-input contract are all out of
    scope for Issue #177, the wiring is what has to agree with them.

    Expiry-driven recomputation is preserved rather than lost: on the
    Workbench ``forward_settlement_date`` is itself filled from the selected
    convention profile the moment Expiry changes, so changing Expiry still
    moves this Forward -- it now does so through the date that actually
    governs the bond forward, instead of past it. A trader who overrides
    forward settlement by hand gets a Forward for the settlement date they
    stated, which is the point of the field.

    ``spot_settlement_date`` (``tS``) remains the one genuinely new input
    this route needs: repo-carry has no spot settlement date anywhere in the
    existing case schema, and this repository never derives a settlement date
    from a lag or calendar, so it must be typed, exactly like every other
    settlement date already on this ticket.

    No repo rate is ever read from ``case`` or from the caller -- the only
    caller-supplied number this route consumes beyond the case itself is
    ``spot_settlement_date``.

    Returns ``{"case": <the case actually priced, freshly acquired curve
    included>, "s490_repo_carry": {...}}``. ``case`` mirrors the same
    "adopt what was actually priced" contract every other live-curve route
    in this module already returns -- here that means the browser's own
    ``currentDraft.curve_points`` is replaced by whatever this route just
    acquired, even if the trader had a manual override on screen; the
    browser never resends this route's own response back into a Price
    click (see the panel's own script.js wiring), so that adoption never
    silently overwrites a trader's manual pricing override with a live
    curve. ``s490_repo_carry`` carries the funding resolver's and forward
    primitive's own fields (``dataclasses.asdict``, nested under
    ``funding``/``forward``), two convenience top-level fields
    (``forward_clean_price_per_100``, ``forward_clean_price_treasury_fraction``),
    plus ``curve_acquisition`` (always
    ``S490_CURVE_ACQUISITION_CONTRACT``) and ``case_curve_points_discarded``
    (how many rows, if any, the case's own ``curve_points`` carried before
    being replaced).

    **Coherence checks restored without the full builder (Codex P1 review of
    PR #174, round 6).** The removed ``BLIStandaloneBondOptionRequest``
    construction enforced that ``bond_quote`` genuinely describes the same
    security ``bond_option``/``resolved_bond_reference_data`` resolved to --
    exact ISIN match (``require_exact_isin_match``) and matching currency in
    both directions. Reading ``bond_quote``/``bond_option`` as independent
    dict lookups dropped that for free, so this route re-checks the same
    three coherence facts directly (``bond_option.currency`` against
    ``resolved_bond_reference_data.currency``, ``bond_quote.isin`` against
    ``resolved_bond_reference_data.isin``, ``bond_quote.currency`` against
    ``bond_option.currency``) -- without pulling in the full envelope's other
    thirteen keys.

    Raises ``ValueError`` if ``bond_option`` / ``bond_reference_data_universe``
    / ``bond_quote`` / ``curve_points`` are missing or malformed, if
    ``forward_settlement_date`` is absent, if any of the three coherence
    checks above fails, or if ``bond_quote.clean_price_per_100`` is absent (a
    yield-only quote carries no spot clean price to start from); or whatever
    the composed functions raise (Bloomberg failure, same-as-of mismatch,
    bond NOT_FOUND / FOUND_INELIGIBLE, curve node range, a window reaching
    the bond's maturity date, a malformed ``spot_settlement_date``, ...) --
    never caught or remapped here; the HTTP handler maps every failure to
    HTTP 400 exactly like every other route in this module.
    """

    priced_case, discarded_curve_point_count = acquire_production_curve_490_for_s490_parity(case)

    bond_option = priced_case.get("bond_option")
    if not isinstance(bond_option, dict):
        raise ValueError("bond_option must be a JSON object")
    forward_settlement_date = priced_case.get("forward_settlement_date")
    if not forward_settlement_date:
        raise ValueError(
            "forward_settlement_date must be present -- it is the date the bond "
            "forward settles (tF), and therefore the date this derivation carries "
            "the spot to. On the Workbench it is filled from the selected "
            "convention profile once Expiry is entered; this repository never "
            "derives a settlement date from a lag, weekend, holiday or "
            "business-day convention"
        )
    currency = coerce_enum(bond_option.get("currency"), Currency, "bond_option.currency")

    universe_raw = priced_case.get("bond_reference_data_universe")
    if not isinstance(universe_raw, list):
        raise ValueError(
            "bond_reference_data_universe must be a JSON array of "
            "BLIStandaloneBondReferenceData objects"
        )
    bond_reference_data_universe = [
        BLIStandaloneBondReferenceData(**record) for record in universe_raw
    ]
    resolved_bond_reference_data = resolve_standalone_bond_reference_by_isin(
        bond_option.get("underlying_isin"), bond_reference_data_universe
    )
    if currency is not resolved_bond_reference_data.currency:
        raise ValueError(
            f"bond_option currency ({currency.value}) does not match "
            "resolved_bond_reference_data.currency "
            f"({resolved_bond_reference_data.currency.value})"
        )

    bond_quote_raw = priced_case.get("bond_quote")
    if not isinstance(bond_quote_raw, dict):
        raise ValueError("bond_quote must be a JSON object")
    bond_quote = BLIBondQuote(**bond_quote_raw)
    if bond_quote.isin != resolved_bond_reference_data.isin:
        raise ValueError(
            f"bond_quote.isin ({bond_quote.isin!r}) does not exactly match "
            f"resolved_bond_reference_data.isin ({resolved_bond_reference_data.isin!r})"
        )
    if bond_quote.currency is not currency:
        raise ValueError(
            f"bond_quote.currency ({bond_quote.currency.value}) does not match "
            f"bond_option currency ({currency.value})"
        )
    spot_clean_price = bond_quote.clean_price_per_100
    if spot_clean_price is None:
        raise ValueError(
            "bond_quote.clean_price_per_100 must be present -- a yield-only quote "
            "carries no spot clean price for the S490 repo-carry Forward to start from"
        )

    curve_points_raw = priced_case.get("curve_points")
    if not isinstance(curve_points_raw, list):
        raise ValueError("curve_points must be a JSON array of BLICurvePoint objects")
    curve_points = [BLICurvePoint(**point) for point in curve_points_raw]

    # Issue #175 / Codex P1: the Federal Reserve coupon-payment convention is
    # approved for US Treasuries only, and neither this route nor the
    # primitive can tell a Treasury from a USD corporate bullet on reference
    # data alone -- this repository deliberately never infers issuer
    # classification (Issues #157/#161). `convention_profile` is therefore
    # required *browser state*, exactly as POST /api/bond/advanced-profile
    # already treats it: the trader's own selection, never defaulted or
    # guessed here. Only the UST selection asserts the UST payment
    # convention; anything else leaves it unset and the primitive fails
    # closed on any interim coupon while Case A is unaffected.
    interim_coupon_payment_convention = (
        UST_COUPON_PAYMENT_ROLL_CONVENTION
        if convention_profile == UST_CONVENTION_PROFILE.name
        else None
    )

    funding = resolve_s490_repo_carry_funding(
        curve_points,
        currency=currency,
        curve_as_of_date=priced_case.get("valuation_date"),
        spot_settlement_date=spot_settlement_date,
        forward_settlement_date=forward_settlement_date,
    )
    forward = repo_carry_forward_clean_price(
        bond=resolved_bond_reference_data,
        spot_clean_price_per_100=spot_clean_price,
        spot_settlement_date=spot_settlement_date,
        forward_settlement_date=forward_settlement_date,
        repo_rate_decimal=funding.derived_repo_rate_decimal,
        interim_coupon_payment_convention=interim_coupon_payment_convention,
    )

    return {
        "case": priced_case,
        "s490_repo_carry": {
            "funding": asdict(funding),
            "forward": asdict(forward),
            "forward_clean_price_per_100": forward.forward_clean_price_per_100,
            "forward_clean_price_treasury_fraction": format_price_as_treasury_fraction(
                forward.forward_clean_price_per_100
            ),
            "curve_acquisition": S490_CURVE_ACQUISITION_CONTRACT,
            "case_curve_points_discarded": discarded_curve_point_count,
        },
    }


# --- Issue #177: the effective Forward Black-76 actually prices from -----------
#
# Before Issue #177 the Forward was a required manual trader entry: whatever
# number reached ``forward_clean_price_input.forward_clean_price_per_100`` was
# priced, and the S490 repo-carry derivation above was a read-only parity
# panel beside it. Issue #177 promotes that derivation to the *default*
# Forward for the approved UST path, leaving a typed Forward as an explicit
# Trader Forward Override.
#
# **The selection is resolved here, server-side, on every priced run --
# never adopted from whatever the browser last displayed.** That is not
# defensiveness about the client; it is the only correct place for it.
# ``POST /api/case/bloomberg`` substitutes a *fresh live spot quote* into the
# case and prices it in the same request, and the S490 repo-carry Forward is
# a function of that spot. A Forward carried in from the browser was derived
# from the *previous* spot, so pricing it against the new one would be
# exactly the "silently use the last stale Forward" failure Issue #177
# forbids. Re-deriving after the substitution is what makes Refresh honest.
#
# **Which cases this touches, and which it deliberately does not.** The mode
# is read from the existing ``forward_clean_price_input.source_system`` field
# -- no new envelope field, no parallel override model (Issue #177's explicit
# constraint). Only the two canonical Issue #177 tokens
# (``pricing/bli_effective_forward.py``) select into this wiring:
# ``SHIORI_DERIVED_S490`` re-derives, ``TRADER_FORWARD_OVERRIDE`` prices the
# typed number. **Every other value is left completely untouched** -- the
# bundled synthetic example's ``SANITIZED_SYNTHETIC_MARKET_SOURCE``, an
# uploaded Case JSON's own label, the pre-#177 ``MANUAL_TRADER_ENTRY``. That
# is what keeps every pre-#177 case, fixture and test priced byte-for-byte as
# before, and it is why no case-schema migration exists anywhere in this
# slice.
#
# **The two new envelope keys are inputs to the derivation, not to the
# request.** ``spot_settlement_date`` (the repo-carry ``tS``) and
# ``convention_profile`` (which approved coupon-payment convention the trader
# asserts, Issue #175) are optional top-level case keys
# (``app/standalone_option_workbench.py``) read only here. Neither is passed
# to ``build_bli_standalone_option_request``, so the reviewed typed request
# contract is unchanged; they exist on the envelope so a saved/re-sent case
# reproduces its own Forward instead of depending on live browser state.
#
# **Two live Curve #490 acquisitions per priced run, deliberately.** The
# derivation goes through :func:`resolve_s490_repo_carry_parity` unchanged,
# which always forces its own fresh acquisition and discards the case's
# ``curve_points`` (Codex P1 review of PR #174: a manual curve override
# entered for Black-76 discounting must never silently become the source of a
# number presented as the S490 Funding Rate). The option-discounting curve is
# then resolved separately by the existing
# :func:`inject_live_option_discount_curve_if_absent`, exactly as before.
# Sharing one acquisition between the two roles would mean either weakening
# that refusal or bypassing the injector's own same-as-of gate, so the extra
# round trip on an explicit Price/Refresh click is the accepted cost of
# leaving both reviewed contracts exactly as they are.


def validate_declared_trader_forward_override(case: object) -> None:
    """Validate a declared Trader Forward Override as the observation it claims to be.

    A no-op for every case outside ``TRADER_FORWARD_OVERRIDE`` mode --
    including the Shiori-derived mode, whose own ``forward_clean_price_input``
    is about to be replaced wholesale and is therefore not validated as an
    observation this run stands behind.

    An override, by contrast, is the trader's own explicit forward
    observation and is carried through verbatim, so every rule the reviewed
    ``BLIForwardCleanPriceInput`` contract has about such an observation must
    still apply to it -- the finite/strictly-positive number, the quote-side
    enum, the non-blank source system, and the ACTIVE-only ``status`` (Codex
    P2 review of PR #178, round 2: rebuilding the input hard-coded ``ACTIVE``
    and so promoted a ``STALE``/``INVALID``/``MISSING`` observation into
    usable market data). Constructing the typed object is exactly that check;
    the result is deliberately discarded, because this is a validation and
    never a rewrite.

    **Called before the live-curve injector, not only from inside
    :func:`apply_effective_forward_to_case`** (Codex P2 review of PR #178,
    round 4). Both pricing routes fetch the live Option Discount Curve first,
    so validating only at forward-selection time meant an
    invalid-or-inactive override on a live-curve draft reached Bloomberg
    first: a DAPI request wasted on a run that cannot price, and -- worse --
    a Bloomberg failure reported in place of the deterministic, entirely
    local reason the run was actually going to be refused for. This check
    reads two case keys, makes no network call and reads no clock, so running
    it first costs nothing and always reports the real reason.
    """

    if not isinstance(case, dict):
        return
    forward_input = case.get("forward_clean_price_input")
    if not isinstance(forward_input, dict):
        return
    if forward_input.get("source_system") != TRADER_FORWARD_OVERRIDE_FORWARD_SOURCE:
        return
    BLIForwardCleanPriceInput(**forward_input)


def apply_effective_forward_to_case(case: dict) -> tuple[dict, dict | None]:
    """Return ``(case priced with the effective Forward, provenance payload)``.

    See the module-level Issue #177 note above for the mode contract. A case
    whose ``forward_clean_price_input.source_system`` is not one of the two
    canonical Issue #177 tokens is returned **unchanged**, with ``None``
    provenance -- no derivation is attempted and no Bloomberg call is made.

    For the two Issue #177 modes:

    - the Shiori Derived Forward is resolved through the unmodified
      :func:`resolve_s490_repo_carry_parity` whenever the case carries a
      ``spot_settlement_date``, and its whole ``s490_repo_carry`` trace
      (funding, carry, per-coupon interim carry, curve ids, source systems)
      is carried onto the provenance payload;
    - the selection itself is
      ``pricing/bli_effective_forward.select_effective_forward``, told which
      source this case declared. It never infers the source from which
      number happens to be present, in either direction (Codex P2 review of
      PR #178): a case declaring ``TRADER_FORWARD_OVERRIDE`` with an
      unusable override is refused rather than silently priced from the
      derived Forward under an override label;
    - in derived mode the winning number is written back as a freshly
      constructed ``BLIForwardCleanPriceInput`` stamped with its own source
      token, preserving the case's own ``quote_side`` (the request contract
      still requires it to equal the spot side, and still enforces that
      itself). In override mode the case's own input is left **exactly** as
      supplied and is instead validated as the reviewed contract defines it,
      before any Bloomberg call -- see the two inline notes below for why
      rebuilding it is the wrong move there.

    **Fail closed.** In Shiori-derived mode a derivation failure -- a missing
    ``spot_settlement_date``, a Bloomberg/curve/coupon-window failure, an
    interim coupon on a non-UST convention selection -- raises
    ``ForwardSourceUnavailableError`` carrying that failure's own text, and
    nothing is priced. The number the case arrived with is never used as a
    fallback in that mode; it is replaced on every run, so a stale value left
    in the envelope cannot survive into a priced result. With a valid Trader
    Forward Override active, a failed derivation is recorded on the
    provenance payload and pricing proceeds under the existing override
    contract.
    """

    forward_input = case.get("forward_clean_price_input")
    if not isinstance(forward_input, dict):
        return case, None
    forward_source = forward_input.get("source_system")
    if forward_source not in EFFECTIVE_FORWARD_SOURCES:
        return case, None

    is_trader_override = forward_source == TRADER_FORWARD_OVERRIDE_FORWARD_SOURCE
    validate_declared_trader_forward_override(case)

    spot_settlement_date = case.get("spot_settlement_date")
    convention_profile = case.get("convention_profile")

    derived_forward = None
    derived_trace = None
    derived_error = None
    if isinstance(spot_settlement_date, str) and spot_settlement_date.strip():
        try:
            derived_trace = resolve_s490_repo_carry_parity(
                case, spot_settlement_date, convention_profile
            )["s490_repo_carry"]
        except Exception as exc:  # noqa: BLE001 -- recorded verbatim, see below
            # Preserved verbatim rather than remapped (Issue #177: "the
            # Forward source error must keep its real reason"). In derived
            # mode select_effective_forward re-raises it inside its own
            # fail-closed message; with an override active it is only
            # recorded, since the override prices without it.
            derived_error = f"{type(exc).__name__}: {exc}"
        else:
            derived_forward = derived_trace["forward_clean_price_per_100"]
    else:
        derived_error = (
            "spot_settlement_date is missing from the case -- the Shiori Derived "
            "S490 repo-carry Forward needs an explicit spot settlement date (tS), "
            "and this repository never derives one from a settlement lag or calendar"
        )

    effective = select_effective_forward(
        requested_forward_source=forward_source,
        shiori_derived_forward_clean_price_per_100=derived_forward,
        trader_forward_override_per_100=forward_input.get("forward_clean_price_per_100"),
        shiori_derived_forward_error=derived_error,
    )

    # An override's own ``forward_clean_price_input`` is left exactly as
    # supplied (Codex P2 review of PR #178, round 2). It already is the
    # explicit forward observation this run prices from, already carries its
    # own quote side, status and source token, and was validated above as the
    # reviewed contract defines it -- rebuilding it could only lose or
    # overwrite one of those, which is precisely how a non-ACTIVE observation
    # was being promoted to ACTIVE. Only the derived Forward is written, and
    # only because it genuinely is a new observation Shiori has just computed:
    # the value it replaces is the *previous* derivation, whose own status
    # describes a number being discarded. That mirrors how a Bloomberg refresh
    # already replaces ``bond_quote`` wholesale rather than merging it
    # field-by-field with the quote it supersedes.
    effective_case = (
        case
        if is_trader_override
        else {
            **case,
            "forward_clean_price_input": forward_clean_price_input_dict(
                effective, quote_side=forward_input.get("quote_side")
            ),
        }
    )
    provenance = {
        "forward_source": effective.forward_source,
        "effective_forward_clean_price_per_100": effective.forward_clean_price_per_100,
        "shiori_derived_forward_clean_price_per_100": (
            effective.shiori_derived_forward_clean_price_per_100
        ),
        "trader_forward_override_per_100": effective.trader_forward_override_per_100,
        "shiori_derived_forward_error": effective.shiori_derived_forward_error,
        "spot_settlement_date": spot_settlement_date,
        "convention_profile": convention_profile,
        "shiori_derived_forward": derived_trace,
    }
    return effective_case, provenance


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


def fetch_usd_sofr_option_discount_curve() -> dict:
    """Load the live production USD SOFR Option Discount Curve for the Markets view.

    Calls two existing, unmodified production loaders, each exactly once:

    - ``load_bloomberg_usd_sofr_option_discount_curve`` (Issue #165/#166) --
      the authoritative ``S0490Z``/``S0490D`` Zero Rate / DF contract. Every
      node's ``maturity``/``zero_rate_percent`` come from its own
      ``curve_points``, and ``discount_factor`` from its own
      ``discount_factor_evidence`` -- matched by tenor, never recomputed.
    - ``load_bloomberg_usd_sofr_par_rate_curve`` (Issue #168) -- the
      ``USOSFR*`` SWAP (Par Rate) contract, joined onto the same nodes by
      tenor. This is the same canonical 32-tenor Curve #490 universe both
      loaders share (enforced at import time by the par-rate module itself),
      so every node ordinarily gets a match; a tenor the par-rate loader
      does not carry is left ``None`` (unavailable) rather than fabricated,
      derived from the zero rate, or bootstrapped.

    No bootstrap, no re-derivation of zero from discount factor or vice
    versa, no second Zero Rate curve, no par-to-zero/zero-to-par conversion,
    no sample-data fallback. A Bloomberg-side failure from either loader
    raises ``BLIBloombergDapiError`` -- never caught or remapped here, and
    the whole call fails rather than returning a curve with only one of the
    two loaders' data. The clock is never read before both loaders have
    actually succeeded.
    """

    result = load_bloomberg_usd_sofr_option_discount_curve()
    par_rate_result = load_bloomberg_usd_sofr_par_rate_curve()
    discount_factor_by_tenor = {
        evidence.tenor: evidence.discount_factor for evidence in result.discount_factor_evidence
    }
    par_rate_percent_by_tenor = {
        point.tenor: point.par_rate_percent for point in par_rate_result.points
    }
    nodes = [
        {
            "tenor": point.tenor,
            "maturity": point.maturity_date,
            "zero_rate_percent": point.rate * 100.0,
            "discount_factor": discount_factor_by_tenor[point.tenor],
            "par_rate_percent": par_rate_percent_by_tenor.get(point.tenor),
        }
        for point in result.curve_points
    ]
    acquired_at = _shiori_acquisition_now().isoformat(timespec="seconds")
    coverage = {
        "first_tenor": nodes[0]["tenor"] if nodes else None,
        "last_tenor": nodes[-1]["tenor"] if nodes else None,
        "first_maturity": nodes[0]["maturity"] if nodes else None,
        "last_maturity": nodes[-1]["maturity"] if nodes else None,
    }
    return {
        "curve_id": USD_SOFR_CURVE_ID,
        "curve_name": USD_SOFR_CURVE_NAME,
        "source_system": USD_SOFR_SOURCE_SYSTEM,
        "rate_basis": result.curve_points[0].rate_basis.value if result.curve_points else None,
        "acquired_at": acquired_at,
        "coverage": coverage,
        "nodes": nodes,
    }


_ADVANCED_PROFILE_REQUIRED_KEYS = (
    "convention_profile",
    "isin",
    "currency",
    "bond_master",
    "bond_master_raw",
    "valuation_date",
)

_CANDIDATES_REQUIRED_KEYS = ("currency", "bond_master")


def resolve_bond_convention_profile_candidates(body: dict) -> dict:
    """Return which convention profiles this bond's confirmed terms leave open.

    Calls ``convention_profile_candidates`` exactly once and serializes its
    result, plus the registry's own list of profiles so the browser renders a
    selector that cannot drift from the server. This function classifies
    nothing and chooses nothing: it narrows by the bond's Bloomberg-confirmed
    currency and coupon frequency, and the trader selects from what is left.
    An empty ``candidates`` list is a refusal (no registered profile covers
    this bond); a non-empty one is a request for a profile selection --
    never a request to hand-type the Advanced technical fields.

    Raises ``ValueError`` for a body that is not a JSON object with the two
    required keys.
    """

    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    missing = [key for key in _CANDIDATES_REQUIRED_KEYS if key not in body]
    if missing:
        raise ValueError(f"request body is missing required key(s): {missing}")

    result = convention_profile_candidates(
        currency=body["currency"], bond_master=body["bond_master"]
    )
    return {
        "candidates": list(result.candidates),
        "reasons": list(result.reasons),
        "supported_convention_profiles": list(SUPPORTED_CONVENTION_PROFILE_NAMES),
    }


def resolve_bond_advanced_profile(body: dict) -> dict:
    """Return the Advanced-field profile for one Bloomberg-loaded bond.

    Calls the common resolver ``resolve_bond_advanced_field_profile`` exactly
    once and serializes its result -- this function decides nothing about
    conventions, calendars, schedules or eligibility itself, and adds no
    fallback for ``convention_profile`` that resolver does not already have
    (Issue #157 P1-1 correction: it is required browser state, never inferred
    or defaulted here or in the resolver). ``expiry_date`` is optional (absent
    or ``null`` while the trader has not entered an expiry yet); the two
    settlement dates then come back under ``pending_field_paths`` instead of
    being guessed.

    A bond whose product shape or coupon schedule the current pricing path
    cannot carry is *not* an error: it returns ``supported: false`` with the
    profile's own reasons, verbatim. A supported bond may still come back
    with a partial ``fields`` list plus per-field ``unresolved_fields``
    (Issue #161) -- also serialized verbatim, and also not an error.
    Raises ``ValueError`` for a body that is not a JSON object
    with the six required keys, or whose ``convention_profile`` is not a
    registered profile, and propagates ``BLIQuantLibNotAvailableError``
    unchanged when the optional QuantLib dependency is missing -- the coupon
    schedule and the selected profile's settlement calendar both come from it
    and neither is approximated.
    """

    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    missing = [key for key in _ADVANCED_PROFILE_REQUIRED_KEYS if key not in body]
    if missing:
        raise ValueError(f"request body is missing required key(s): {missing}")

    profile = resolve_bond_advanced_field_profile(
        convention_profile=body["convention_profile"],
        isin=body["isin"],
        currency=body["currency"],
        bond_master=body["bond_master"],
        bond_master_raw=body["bond_master_raw"],
        valuation_date=body["valuation_date"],
        expiry_date=body.get("expiry_date"),
    )
    return {
        "supported": profile.supported,
        "convention_profile": profile.convention_profile,
        # This profile's own explicit run-export label (Issue #161 follow-up
        # item 6/compatibility correction). Read from the registry record
        # itself -- never re-derived here -- so the browser's export never
        # drifts from `BLIConventionProfile.source_system`, and so UST's
        # already-shipped `SHIORI_UST_FIXED_COUPON_PROFILE` value cannot be
        # accidentally recomputed as something else. `profile.convention_profile`
        # is always a name `resolve_bond_advanced_field_profile` already
        # validated via `get_convention_profile`, whether or not this bond's
        # shape was admitted.
        "source_system": get_convention_profile(profile.convention_profile).source_system,
        "rejection_reasons": list(profile.rejection_reasons),
        "fields": [
            {"path": field.path, "value": field.value, "provenance": field.provenance}
            for field in profile.fields
        ],
        "pending_field_paths": list(profile.pending_field_paths),
        "unresolved_fields": [
            {"path": item.path, "reason": item.reason} for item in profile.unresolved_fields
        ],
    }


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

    def _handle_api_case_validate(self, raw_body: bytes) -> None:
        try:
            case = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            self._write_json(400, {"error": f"invalid JSON body: {exc}"})
            return
        # A draft the builder rejects is a normal, expected outcome while the
        # trader is still completing the ticket -- it is reported as
        # ``ready: false`` on HTTP 200, never as a bridge error.
        self._write_json(200, validate_case(case))

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

    def _handle_api_case_s490_repo_carry(self, raw_body: bytes) -> None:
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            self._write_json(400, {"error": f"invalid JSON body: {exc}"})
            return
        required_keys = {"case", "spot_settlement_date"}
        if not isinstance(body, dict) or not required_keys.issubset(body):
            self._write_json(
                400,
                {
                    "error": (
                        "request body must be a JSON object with 'case' and "
                        "'spot_settlement_date'"
                    )
                },
            )
            return
        try:
            payload = resolve_s490_repo_carry_parity(
                body["case"],
                body["spot_settlement_date"],
                body.get("convention_profile"),
            )
        except Exception as exc:  # noqa: BLE001
            self._write_json(400, {"error": str(exc)})
            return
        self._write_json(200, payload)

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

    def _handle_api_bond_advanced_profile(self, raw_body: bytes) -> None:
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            self._write_json(400, {"error": f"invalid JSON body: {exc}"})
            return
        try:
            payload = resolve_bond_advanced_profile(body)
        except Exception as exc:  # noqa: BLE001
            self._write_json(400, {"error": f"{type(exc).__name__}: {exc}"})
            return
        # A bond outside the profile is a normal answer, not a bridge error.
        self._write_json(200, payload)

    def _handle_api_bond_profile_candidates(self, raw_body: bytes) -> None:
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            self._write_json(400, {"error": f"invalid JSON body: {exc}"})
            return
        try:
            payload = resolve_bond_convention_profile_candidates(body)
        except Exception as exc:  # noqa: BLE001
            self._write_json(400, {"error": f"{type(exc).__name__}: {exc}"})
            return
        # "no profile covers this bond" is a normal answer, not an error.
        self._write_json(200, payload)

    def _handle_api_bloomberg_option_discount_curve(self, raw_body: bytes) -> None:
        # No request body is read: the Markets view always asks for the one
        # full production curve, never a caller-chosen tenor subset -- this
        # route takes no input at all.
        try:
            payload = fetch_usd_sofr_option_discount_curve()
        except BLIBloombergDapiError as exc:
            self._write_json(502, {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            self._write_json(500, {"error": str(exc)})
            return
        self._write_json(200, payload)

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
        "/api/case/validate": _handle_api_case_validate,
        "/api/case/bloomberg": _handle_api_case_bloomberg,
        "/api/case/s490-repo-carry": _handle_api_case_s490_repo_carry,
        "/api/bloomberg/bond": _handle_api_bloomberg_bond,
        "/api/bond/advanced-profile": _handle_api_bond_advanced_profile,
        "/api/bond/convention-profile/candidates": _handle_api_bond_profile_candidates,
        "/api/bloomberg/option-discount-curve": _handle_api_bloomberg_option_discount_curve,
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
