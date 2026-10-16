# 35 Market Data Reconnection Phase 1 — Read-Only Vendor Probes

Phase 1 of reconnecting Shiori to live market data. **This phase builds two
read-only workstation probes and nothing else.** No production pricing path,
no Black-76 wiring, no vol-surface ingestion, no SQLite schema change, and no
new production data module.

The question Phase 1 poses to the licensed workstation:

> Can Shiori obtain a real USD swaption **normal** volatility — first target
> **1Y expiry × 5Y tenor, ATM, MID** — directly from Bloomberg or from LSEG,
> and what does each vendor itself say about the field that returns it?

Phase 1 *answers nothing by itself*. Both probes record evidence and assign
no verdict; whether the evidence adds up to a usable non-visual route is
Eddy's judgment on the reports, exactly as
`tools/bloomberg_vcub_field_hunter.py` established for Issue #179.

---

## 1. What already exists (inventory — do not rebuild)

The repository already holds a complete, disciplined Bloomberg DAPI toolchain
and a canonical vol-surface layer. Phase 1 reuses both and duplicates
neither.

### 1.1 Bloomberg DAPI — production loaders

| Module | What it does |
| --- | --- |
| `src/shiori_pricing_lab/data/bloomberg_bond_quote.py` | Production `BLIBondQuote` loader against DAPI at `localhost:8194`. Only Eddy-confirmed mnemonics reach its field maps. |
| `src/shiori_pricing_lab/data/bloomberg_bond_yield_history.py` | Bloomberg historical bond-*yield* series (Issue #196) — the input to the historical-vol path. |
| `src/shiori_pricing_lab/data/bloomberg_option_discount_curve.py` | Option discount curve ingestion. |
| `src/shiori_pricing_lab/data/bloomberg_usd_sofr_par_rate_curve.py` | USD SOFR par-rate curve ingestion. |

The governing rule, stated in `bloomberg_bond_quote.py`'s own docstring: a
mnemonic is written into a production field map **only after a live probe
confirmed it**. Phase 1 changes no field map.

### 1.2 Bloomberg DAPI — the existing probe suite (`tools/`)

Seventeen standalone diagnostic CLIs, none imported by `src/`. The ones
Phase 1 builds on:

| Probe | Reused for |
| --- | --- |
| `tools/bloomberg_dapi_probe.py` | `_send_request` (the single session/event-loop implementation) and `probe_fields` (per-field `returned` / `absent` / `field_exception`). |
| `tools/bloomberg_curve_discovery_probe.py` | `discover_service` — session start plus service open and schema introspection. |
| `tools/bloomberg_curve_field_documentation_probe.py` | Two-pass expansion of a field's **Bloomberg-published** documentation, including the override ids a field documents. |
| `tools/bloomberg_vcub_field_hunter.py` | `hunt_vol_fields` (the `//blp/apiflds` `FieldSearchRequest.searchSpec` search) and `probe_candidate_values` (the overrides-aware value probe). |
| `tools/bloomberg_input_sourcing_probe.py` | `sanitize_external_text` — the existing no-leak guard. |

### 1.3 Vol Surface Store + canonical model

| Module | Fact that matters here |
| --- | --- |
| `src/shiori_pricing_lab/data/vol_surface.py` | The vendor-neutral canonical model. **It contains no field for a vendor ticker/RIC, no fetch timestamp, and no quote / "data as of" time.** The only time fields are `VolSurfaceProvenance.captured_at` and `confirmed_at`; the only date-ish field is the unparsed free-form `VolSurfaceIdentity.business_date`. |
| `src/shiori_pricing_lab/data/vol_surface_store.py` | `SCHEMA_VERSION = 3`, four tables, DB at `<repo>/data/vol_surfaces.sqlite3` (overridable via `SHIORI_VOL_SURFACE_DB`). `save_confirmed_surface` is the **only** creating/migrating entry point; `fetch_surface` / `fetch_points` / `list_surfaces` read through `mode=ro` + `PRAGMA query_only = 1`. |
| `src/shiori_pricing_lab/data/vcub_vol_surface_adapter.py` | Screenshot capture → canonical surface. States the volatility *unit* (`bp`) from the screen's stated type, per Eddy's PR #189 decision. |
| `src/shiori_pricing_lab/data/vcub_normal_vol_resolver.py` | Canonical surface → in-grid `sigma_vcub`. **Deliberately stops there** — no `lambda_vcub`, no `DCF_VCUB` alignment, no Black-76. |

### 1.4 Workbench wiring

- `POST /api/vol-surface/atm/list` → `list_confirmed_atm_vol_surfaces()`
- `POST /api/vol-surface/atm/surface` (payload key `surface_id`) → `fetch_confirmed_atm_vol_surface()`

Both are read-only. **No route accepts an externally-sourced surface**: the
only two callers of `save_confirmed_surface` are reachable solely through the
screenshot capture confirm/reject routes, and a `capture_id` must already
exist in the in-process review store minted by `/api/vcub/{atm,otm}/parse`.

The historical-vol seam is a *different* path:
`src/shiori_pricing_lab/app/standalone_option_historical_vol_source.py` reads
a **Bloomberg bond-yield history**, not the vol-surface store, and is selected
by `volatility_input.source_system == HISTORICAL_YIELD_VOL_MO`.

### 1.5 The structural finding this phase surfaces

The VCUB path is **screenshot-and-OCR**, end to end. There is no API-sourced
vol ingestion anywhere in the repository, and the canonical model has nowhere
to record a vendor ticker, a quote timestamp, or a fetch time. So an
API-sourced surface **cannot be represented without a canonical-model /
schema decision** — see §7. That is precisely why Phase 1 stops at probing.

### 1.6 Prior live evidence (Issue #179) — do not re-litigate by habit

Eddy ran the full non-visual route hunt for the OVME *proxy* coordinate
(3Mo × 4Yr, ticker `USSNAC4 BVOL Curncy`). Verbatim outcomes, none fabricated:

```
refdata USSNAC4 BVOL Curncy                        -> INVALID_SECURITY
refdata USSNAC4 Curncy      + PX_LAST / LAST_PRICE -> absent
refdata USSNAC4 BGN Curncy  + SWPN_IMPLIED_VOL     -> field not applicable
refdata USSNAC4 BGN Curncy  + BVOL_OIS_SWAPTION,
                              IR_VOL_FMT=normal    -> field not applicable
mktdata USSNAC4 Curncy                             -> resolution failed
mktdata USSNAC4 BGN Curncy                         -> resolution failed
instruments lookup "USSNAC4"                       -> empty results[]
instruments lookup "USSNAC4 BVOL Curncy"           -> empty results[]
```

Issue #179's verdict is `NO NON-VISUAL ROUTE FOUND` — **bounded** to that
ticker, those mnemonics and those overrides across `//blp/refdata`,
`//blp/mktdata` and `//blp/instruments`. It never tested the ATM
swaption-surface coordinate Phase 1 targets.

---

## 2. The Phase 1 probes

Two new CLIs plus one shared vocabulary module. All three live under `tools/`,
are never imported by `src/`, and open no database.

| File | Role |
| --- | --- |
| `tools/market_data_probe_evidence.py` | The vocabulary both probes share: `EvidenceClass`, `AuthorizationStatus`, `ReportItem`, `utc_now`, `sanitize_tree`. Deliberately the *only* shared thing, so the two vendor reports are directly comparable. |
| `tools/bloomberg_swaption_vol_probe.py` | Bloomberg DAPI half. Reuses `discover_service`, `hunt_vol_fields`, `probe_candidate_values`, `sanitize_external_text`; adds only the session/authorisation stage and the report contract. |
| `tools/lseg_swaption_vol_probe.py` | LSEG Workspace desktop-session half. Discovers the installed client, introspects its swaption-surface capability, and probes only operator-supplied candidates. |

### 2.1 No ticker, RIC, mnemonic or surface parameter is ever invented

Neither probe has a default `--security` / `--ric` / `--field` / `--ipa-param`,
and neither falls back to the phase's own nominal target. With no
operator-supplied candidate the value stage reports `not_attempted` and says
why. This is enforced by tests, not by convention
(`test_run_value_probes_returns_nothing_without_candidates`,
`test_main_with_no_candidate_reports_that_it_requested_no_value`,
`test_main_with_no_candidate_requests_no_data_and_says_so`).

Every candidate must come from a *discovered* source: the Bloomberg field
search's own hits, a VCUB cell's generated Excel formula, or LSEG's own
discovery/symbol search — never from a plausible-looking pattern.

### 2.2 The report contract — ten items, four evidence classes

Both probes emit the same ten items. Each carries exactly one evidence class:

| Class | Meaning |
| --- | --- |
| `VENDOR_RETURNED` | The vendor's own response establishes it. |
| `OPERATOR_DECLARED` | The operator asserted it on the command line. Recorded verbatim, and **not** vendor evidence. |
| `PROBE_OBSERVED` | This run observed it locally (the clock, an opened session/service). |
| `NOT_ESTABLISHED` | Nothing established it. Never filled in. |

| Item | What establishes it |
| --- | --- |
| `connection` | `PROBE_OBSERVED` — a service/session actually opened. |
| `authorization` | `VENDOR_RETURNED` at session level; per-field entitlement only from each field's own response or exception text. |
| `value` | `VENDOR_RETURNED` per returned cell. |
| `vol_type` | `OPERATOR_DECLARED` at best. |
| `vol_unit` | `OPERATOR_DECLARED` at best. |
| `expiry` | `OPERATOR_DECLARED` at best. |
| `tenor` | `OPERATOR_DECLARED` at best. |
| `quote_side` | `OPERATOR_DECLARED` at best. |
| `data_date` | `OPERATOR_DECLARED` at best. |
| `fetched_at` | `PROBE_OBSERVED` — this run's own UTC clock. |

**The load-bearing rule:** a returned *number* never establishes its own vol
type or unit. "This mnemonic is a normal vol quoted in bp" is a claim about a
field's *meaning*, and no returned magnitude can settle it. Both probes
report the operator's declaration plus whatever the vendor publishes about
the field itself, and refuse to label a value "normal vol, bp" on the
strength of its size — the silent unit coercion Annex A.8.1 forbids. Tests:
`test_a_returned_value_never_establishes_its_own_vol_type_or_unit` (both
suites).

### 2.3 Handling rules — licensed data

Both probes write to a **git-ignored** directory
(`shiori_swaption_vol_probe_output/`, `shiori_lseg_swaption_vol_probe_output/`).
Reports can contain licensed market values and the operator's own inputs.
They are workstation evidence: **never commit them, never attach them to a
PR, never paste them into an external model or service.** Every external
string passes through `sanitize_external_text` first, which strips the
override values actually sent, credential-shaped `key=value` pairs,
filesystem paths and `host:port` pairs.

---

## 3. Windows runbook

Run at the licensed workstation, from the repository root:

```
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[quant]" -r requirements.txt
.venv\Scripts\python -m pip install blpapi            :: Bloomberg's own package
.venv\Scripts\python -m pip install lseg-data         :: LSEG's current client
```

`blpapi` must be installed from Bloomberg's own distribution (it is not on
PyPI under that name as a generally available wheel); if the workstation's
Bloomberg installation already provided it, use that interpreter.

The editable install is convenient, not required: both probes put the
checkout's own `src/` on `sys.path` at import time, so they also run from a
bare checkout — and they must, because the reused `bloomberg_dapi_probe`
imports `shiori_pricing_lab` at module level. Without that bootstrap the CLI
would die on an import traceback instead of printing the diagnostics in §4.
Two tests guard it (`test_the_cli_starts_without_an_editable_install` in both
test modules), with `PYTHONPATH` scrubbed so a developer's environment cannot
mask a regression.

### 3.1 Stage A — connectivity and authorisation only (start here)

```
.venv\Scripts\python tools\bloomberg_swaption_vol_probe.py --session-only
.venv\Scripts\python tools\lseg_swaption_vol_probe.py    --session-only
```

This proves the plumbing before any licensed value is requested. A Bloomberg
Terminal must be running and logged in locally; LSEG Workspace must be running
and logged in **on the same machine** (a web/Workspace-for-Web session is not
addressable).

### 3.2 Stage B — discover, then probe (Bloomberg)

```
.venv\Scripts\python tools\bloomberg_swaption_vol_probe.py --skip-discovery

.venv\Scripts\python tools\bloomberg_swaption_vol_probe.py ^
  --search-term swaption --search-term "normal vol" --search-term VCUB ^
  --security "TICKER-FROM-STAGE-B-REPORT" ^
  --field MNEMONIC_FROM_STAGE-B-REPORT ^
  --expiry 1Y --tenor 5Y --strike ATM --quote-side MID ^
  --vol-type Normal --vol-unit bp --data-as-of "OPERATOR-DECLARED-DATE"
```

Stage B's report carries every `//blp/apiflds` description Bloomberg publishes
for the matched fields, plus the override ids those fields document — which is
how the expiry/tenor/strike parameterisation is *obtained*, not guessed. Feed
the report's own hits back in as `--security` / `--field`, and its own override
ids back in as `--override FIELD=VALUE`, e.g.:

```
.venv\Scripts\python tools\bloomberg_swaption_vol_probe.py ^
  --security "TICKER-FROM-STAGE-B-REPORT" --field MNEMONIC_FROM_STAGE-B_REPORT ^
  --override "OVERRIDE-ID-FROM-THE-FIELDS-OWN-DOCUMENTATION=OPERATOR-VALUE" ^
  --expiry 1Y --tenor 5Y --strike ATM --quote-side MID ^
  --vol-type Normal --vol-unit bp
```

`--vol-type` / `--vol-unit` / `--data-as-of` are **operator declarations**, and
the report labels them as such. They are not decoration: report items 4, 5 and
8 cannot be answered any other way, because no returned magnitude can establish
what a field *means*. They are also why the probe is useful — if the operator
declares "Normal, bp" and Bloomberg's own documentation for that field says
otherwise, the report shows both side by side instead of silently preferring
one.

### 3.3 Stage B — discover, then probe (LSEG)

```
.venv\Scripts\python tools\lseg_swaption_vol_probe.py

.venv\Scripts\python tools\lseg_swaption_vol_probe.py ^
  --ric "RIC-FROM-THE-CLIENTS-OWN-DISCOVERY" ^
  --field FIELD_FROM_WORKSPACE_DATA_ITEM_BROWSER ^
  --expiry 1Y --tenor 5Y --strike ATM --quote-side MID ^
  --vol-type Normal --vol-unit bp --data-as-of "OPERATOR-DECLARED-DATE" ^
  --expiry-label 1Y --tenor-label 5Y

.venv\Scripts\python tools\lseg_swaption_vol_probe.py ^
  --ipa-param PARAMETER=PARAMETER_VALUE --expiry-label 1Y --tenor-label 5Y
```

The LSEG report's `surface_capability` block is the important half: it reports
whether the installed package exposes a swaption volatility-surface
`Definition`, the **real signature** of that callable, the volatility-type
enum members the package actually publishes, and which discovery callables
exist for obtaining a RIC. `--ipa-param` names are read off that signature
block; none is guessed, and a parameter shape that fails is reported with the
client's own error rather than worked around.

`--expiry-label` / `--tenor-label` perform an **exact string match** against
the returned table's own labels. A near-miss reports `NOT_FOUND` *with the
labels the table really carries*, and an ambiguous orientation (both
orientations resolve and disagree) is refused rather than picked.

### 3.4 Reported output

Each run prints a summary and writes two files into the output directory:
`*.json` (the machine-readable record) and `*.md` (the same, readable).

---

## 4. Failure diagnosis

### 4.1 Bloomberg

| Symptom in the report | Meaning | Action |
| --- | --- | --- |
| `error: blpapi is not installed` (exit 2) | The SDK is missing for that interpreter. | Install Bloomberg's `blpapi` for the interpreter running the probe. |
| every service `REFUSED`, `open_error` says the session failed to start | No Terminal session on `localhost:8194`. | Start the Bloomberg Terminal and log in as the licensed user; confirm `bbcomm.exe` is up. |
| `//blp/refdata` opens, `//blp/apiflds` refused | Field metadata is unavailable, so discovery cannot run. | Check the account's API entitlement; then use `--skip-discovery` and supply candidates directly. |
| `field_exception` with permission wording | Bloomberg's own per-field entitlement refusal. | That field is not entitled for this account — report it; do not substitute another mnemonic. |
| `absent` on every field | The mnemonic does not exist, does not apply, or is unentitled — all three look identical. | Use the field-discovery block to find a mnemonic Bloomberg documents; do not invent a suffix. |
| `request_error: ... securityError ... INVALID_SECURITY` | Bloomberg does not resolve that ticker. | Return to discovery; the ticker needs its own evidence. |
| `request_error: ... timed out` | The whole-request deadline elapsed. | Retry once; if it repeats, the workstation/network path is the problem, not the probe. |
| `field discovery failed: ...` warning | The search stage failed but the run continued. | Expected behaviour: the value stage is independent, and the report records `field_discovery: null` rather than a fabricated result. |

### 4.2 LSEG

| Symptom in the report | Meaning | Action |
| --- | --- | --- |
| `no LSEG client module is installed` (exit 2) | None of `lseg.data` / `refinitiv.data` / `eikon` imports. | `pip install lseg-data` for the interpreter running the probe. |
| `connection` NOT_ESTABLISHED with a message about the session | Workspace is not running, or not on this machine. | Start and log in to LSEG Workspace on the same PC. |
| `error_code: 403` | The account may not run the workspace API proxy, or is not entitled to the field. | Confirm proxy permission with LSEG; a per-field 403 is an entitlement question, not a code question. |
| `error_code: 400` with "app key" wording | App-key problem on an older Workspace build. | Only relevant to Workspace/Eikon builds predating Feb 2022 — check the Workspace version before chasing an app key. |
| `no open_session-shaped entry point` | The installed client exposes a different session API than this probe recognises. | Report it: the probe lists the candidate module it did import. Do **not** patch in an assumed call. |
| `surface_capability.available_module: None` | The installed package has no swaption-surface module at the candidate paths. | Report it with the package version; this is a capability finding, not a probe bug. |
| `--ipa-param` reported as `not attempted` | No discovered `Definition`, or no parameter was supplied. | Read `definition_signature` in the report and supply the real names. |

---

## 5. Offline verification (no vendor, no network, no licence)

Both probes can be verified end to end without a Terminal, a Workspace or a
licence. Bloomberg is faked via `sys.modules["blpapi"]`; the LSEG client is
faked with module objects carrying real `ModuleSpec`s so
`importlib.util.find_spec` behaves as it does against an installed package.

```
.venv\Scripts\python -m pytest tests/test_bloomberg_swaption_vol_probe.py tests/test_lseg_swaption_vol_probe.py -q
```

Float-free, deterministic: every timestamp is injected
(`run_value_probes(..., clock=...)`, `market_data_probe_evidence.utc_now`),
so no test reads the system clock.

---

## 6. Explicitly out of scope for Phase 1

- No change to `src/` at all — production pricing, the vol store, the
  resolver, the workbench and every production field map are untouched.
- No Black-76, no `PRICE_VOL` contract change, no vol conversion, no
  interpolation, no calibration, no auto-fill.
- No SQLite schema change and no new persistence path.
- Both probes import nothing from `shiori_pricing_lab.pricing` or
  `shiori_pricing_lab.data`.
- Neither probe assigns a verdict.

---

## 7. What Phase 2 has to decide first (the RED gate)

**A vendor-sourced vol surface cannot be stored today.** The canonical model
requires image-shaped provenance (`source_image_sha256` as 64-hex,
`source_image_bytes` as a positive int, `captured_at`, `parser_name`,
`parser_version`, `confirmed_by`, `confirmed_at`) and provides **nowhere** to
record a vendor ticker/RIC, a quote time, or a fetch time. There is also no
route that accepts an externally-sourced surface.

So the shortest adapter is not a code question yet. Phase 2 must first settle
two decisions that AGENTS.md reserves for Eddy/Sophira:

1. **Which carrier** an API-sourced swaption vol lands in — the canonical
   vol-surface store (needs a new provenance kind and a vendor/quote-time
   field, i.e. a schema decision), or the existing `volatility_input` seam
   (needs a `volatility_basis` convention for a bp normal swaption vol, and a
   `source_system` token).
2. **Whether the value may be priced from at all** — that is Annex A.8.5's
   methodology gate (`lambda_vcub`, DCF alignment, normal yield vol, duration),
   not an ingestion question.

Everything upstream of those decisions is already built or probed. The
*shortest* candidate adapter, once decided, is a single read-only `data/`
module that returns exactly the evidence record these probes already produce
— no new abstraction, no factory, no plugin layer.

### 7.1 Where each vendor currently stands

- **LSEG** appears to be the better bet: LSEG documents a first-class
  interest-rate **swaption volatility surface** capability whose parameters
  are keyed by currency and underlying swap structure rather than by a RIC,
  and whose calculation parameters expose a documented normal-vs-lognormal
  output choice and a price side. The probe reports whether the *installed*
  package exposes it; nothing is assumed. References:
  [Swaption volatility surfaces](https://cdn.refinitiv.com/public/lseg-lib-python-doc/2.0.0.2/book/en/sections/content-layer/ipa/surfaces/volatility-surfaces/swaption/intro.html),
  [LSEG Data Library for Python quick start](https://developers.lseg.com/en/api-catalog/lseg-data-platform/lseg-data-library-for-python/quick-start).
  Two documented traps the probe is built to survive rather than trust: an
  official example that calls a function which does not exist in the current
  package, and more than one enum named for "volatility type" where the
  surface module re-exports the wrong one. That is why the probe introspects
  the installed package instead of following the doc.
- **Bloomberg** starts from Issue #179's bounded `NO NON-VISUAL ROUTE FOUND`
  for the OVME proxy coordinate. Phase 1's probe is the instrument for
  testing the ATM-surface coordinate; until it is run live, Bloomberg's status
  for this target is **unknown, not negative**.

---

## 8. Reporting back

Bring back the JSON report from each probe, or the `required_report_items`
block alone. That block is the answer to this phase's question, with each
answer labelled by what actually establishes it — which is what makes it safe
to act on.
