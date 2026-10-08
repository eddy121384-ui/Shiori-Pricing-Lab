# 34 — C++ Rates build, QuantLib isolation, concurrency, caching, benchmark methodology (Issue #226)

Parent: #222 — C++ Rates Engine Foundation.
This issue: #226 — ARCHITECTURE / PERFORMANCE CONTRACT ONLY.

| Field | Value |
|---|---|
| Issue | #226 |
| Parent roadmap | #222 |
| Branch | `arch/226-cpp-build-quantlib-perf` |
| Base SHA | `5b63f86f28b3dd738f0bd884e6e35e4bc08c8093` (main, merge of PR #248 / #225) |
| Deliverable | This document only |
| Scope | C++20 build contract, QuantLib version + isolation, concurrency posture, immutable-input consumption, cache + calibration-cache identity, test architecture, benchmark methodology, Python+C++ CI, diagnostics (two-lane: #225 result diagnostics + `RATES_RUNTIME_TELEMETRY_V1`, §12.2) |
| Non-goals | No production pricing, no executable CMake/build/engine scaffolding (deferred to #227), no methodology values, no bond-option behavior change |
| Methodology authority | Sophira (architecture/methodology scope, RED decisions, review interpretation) |
| Final merge authority | Eddy |
| Independent reviewer | Codex |
| Status | `PENDING CODEX REVIEW — DO NOT MERGE` |

Upstream contracts that remain authoritative and are **not** reopened here:

| Contract | Document | Relationship to #226 |
|---|---|---|
| Reuse map + Python↔C++ integration boundary | `docs/31_rates_reuse_boundary_223.md` (#223) | #226 locks the build/execution model *inside* the boundary #223 drew. §8 of that document explicitly leaves the target layout "tentative — not created in this issue"; #226 locks it. |
| `SwapTrade` / `ConventionSet` / `ResolvedSwap` | `docs/32_usd_sofr_ois_convention_224.md` (#224) | `ResolvedSwap` is the resolved TRADE input consumed by the kernel. #226 does not change it. |
| Market / model / result contracts | `docs/33_rates_market_model_result_contracts_225.md` (#225) | #226 keys caches on the identity fields #225 defined and implements the ownership model #225 §6.3/§6.4 requires. |
| Python-first backend strategy | `docs/08_performance_engine_backend_strategy.md` | #226 is the concrete instantiation of its "Stage 3 — specialized backends only after profiling" rule for the Rates module. |

---

## 0. Claim classification and evidence discipline

Every architecture statement in this document carries exactly one class. The classes are never blended inside a single sentence.

| Class | Meaning | Test a reader can apply |
|---|---|---|
| **OBSERVED** | Directly supported by current repository evidence at the base SHA | A reader can re-run the cited `git grep` / `git ls-tree` / file read and see the same thing |
| **EXTERNAL EVIDENCE** | Supported by official QuantLib or toolchain documentation/source, with the source cited | A reader can open the cited upstream file/section and see the same statement |
| **PROPOSED** | An explicit #226 architecture decision, binding on #227+ | A reader can implement it and know whether they complied |
| **UNPROVEN** | Requires #227+ implementation or test evidence; **no claim is made** | A reader can see exactly which test must produce the evidence |

Rules enforced throughout:

1. A statement is never promoted from **UNPROVEN** to fact by repetition or by community consensus. Upgrades require recordable evidence.
2. **OBSERVED** statements cite repository paths and, where useful, line numbers.
3. **EXTERNAL EVIDENCE** statements cite an upstream source and name the exact version/flag/configuration they depend on. Where the upstream behavior is conditional, the condition is stated as part of the claim, never dropped.
4. **PROPOSED** decisions that would change an approved #223/#224/#225 contract are forbidden. Where this document notices tension with a prior contract, it records the tension and escalates instead of silently resolving it (§13.3).
5. No **PROPOSED** decision in this document constitutes a pricing, curve, volatility, calibration, settlement, or risk methodology choice. §13.2 lists the RED items that remain open.

Legend used in tables: `HDR` = "how determinism is restored" is documented; `W` = needs workstation/owner input (out of scope here).

---

## 1. Current-repository inventory and evidence baseline

All **OBSERVED** facts below were verified by direct repository inspection at base SHA `5b63f86f28b3dd738f0bd884e6e35e4bc08c8093` on branch `arch/226-cpp-build-quantlib-perf`.

### 1.1 Summary of the baseline

**OBSERVED.** Shiori Pricing Lab at this SHA is a **pure-Python repository with no C++ toolchain surface at all**:

| Probe | Result |
|---|---|
| `cpp/` exists | **No** |
| `arch/` exists | **No** |
| `CMakeLists.txt` anywhere | **No** |
| `vcpkg.json`, `conanfile.txt`, `third_party/`, `external/` | **No** |
| Any `.cpp` / `.hpp` / `.cc` file | **No** |
| `pybind11` / `nanobind` reference | **No** |
| Local CMake / MSVC / clang / g++ / ninja available in the audit environment | **No** (`cmake`, `g++`, `clang++`, `cl`, `ninja` all not found) |

This matches `docs/31_rates_reuse_boundary_223.md:30` ("`cpp/` does not exist. `arch/` does not exist. No `CMakeLists.txt` anywhere. Pure-Python repo") and `docs/31...:246`. **Implication (PROPOSED §2):** #226 cannot validate a build; it can only lock the build *contract*. The audit environment's lack of a C++ toolchain is an **OBSERVED** property of the audit environment, not of the repository, and is recorded here only to make explicit why this issue produces no build artifact.

### 1.2 Existing Python CI workflows and protected validation paths

**OBSERVED.** Three workflows exist under `.github/workflows/`:

| Workflow | Trigger | Jobs | Validation role |
|---|---|---|---|
| `python-tests.yml` | `push` to `main`, `pull_request` → `main` | `test` (ubuntu-latest), `windows-launcher-smoke` (windows-latest) | The authoritative product validation |
| `opencode.yml` | `issue_comment` matching `/oc ` or `/opencode ` | automation | Not product validation |
| `codex-opencode-relay.yml` | `pull_request_review` (Codex review) | `collect`, `agent`, `validate`, `publish`, `report-*` | Not product validation |

**OBSERVED** details of the authoritative job, `python-tests.yml`:

- `test`: `actions/checkout@v4` (`:15`), `actions/setup-python@v5` with `python-version: "3.11"` (`:18-20`), `pip install -r requirements.txt` **and** `pip install -e ".[quant]"` (`:25-26`) — so **QuantLib is installed on the Linux CI path today**, then `playwright install --with-deps chromium` (`:29`), then bare `pytest` (`:32`).
- `windows-launcher-smoke`: runs the **real** `start_shiori.bat` entry point (`:75-80`) via `shell: cmd`, after copying the checkout to `C:\Shiori Smoke Test (café) Folder\Shiori Pricing Lab` (`:70`). Its own comment (`:35-43`) states the launcher must never be "proven" only by its Python unit tests.
- **No `paths:` / `paths-ignore:` filter exists on either job** — every pull request to `main` runs the full Python suite and the Windows launcher smoke.
- **No `actions/cache` step exists in any workflow** — there is currently no dependency or build caching anywhere in the repository.

**OBSERVED.** The two automation workflows contain an explicit *protected file* classification that is relevant to #226's non-goal. `codex-opencode-relay.yml` defines:

```
protected_basenames = {
    'AGENTS.md', 'CLAUDE.md', 'opencode.json', 'opencode.jsonc',
    'requirements.txt', 'pyproject.toml', 'setup.py', 'setup.cfg',
    ...
    'environment.yml', 'environment.yaml', 'CMakeLists.txt', 'vcpkg.json',
    'conanfile.py', 'conanfile.txt', 'meson.build', 'meson_options.txt',
    ... }
```

**OBSERVED:** `CMakeLists.txt`, `vcpkg.json`, `conanfile.py`, `pyproject.toml`, and `requirements.txt` are all in that protected set. **PROPOSED (reinforcing the issue non-goal):** the build/dependency control plane of the future C++ subsystem is treated as a **protected path**: #227 (and later) must introduce it as an explicitly reviewed change, not as a side effect of another issue. #226 therefore introduces **no** build file.

### 1.3 Windows / Linux assumptions

**OBSERVED.**

- The product is **Windows-first**: `start_shiori.bat` is the real user entry point and is smoke-tested on `windows-latest` (`python-tests.yml:44`). Its header states it is a thin shim requiring "No PowerShell, no execution-policy change, no administrator rights, no recursive repository search" (`start_shiori.bat:1-7`).
- The full test suite is validated on **Linux** (`ubuntu-latest`, `python-tests.yml:11`).
- `start_shiori.bat` sets `PYTHONUTF8=1` "from the root of the whole process tree" (`start_shiori.bat:19`) because a repository path containing certain Unicode characters otherwise breaks the editable install on Windows. The Windows smoke job documents that a **CJK** path still fails inside `setuptools._encode_pth` against `cp1252`, and therefore deliberately uses an accented-Latin character (`python-tests.yml:55-67`).
- Bootstrapping search order (`start_shiori.bat:22-30`): `%USERPROFILE%\.venvs\shiori-bloomberg\Scripts\python.exe` → `python` on `PATH` → `py -3`.

**Implication (PROPOSED §2/§11):** the C++ build contract must be **Windows-first but not Windows-only**, and must not introduce a requirement for PowerShell, administrator rights, or a system-installed toolchain that would break the launcher's constraints.

### 1.4 Python version and environment tooling

**OBSERVED** (`pyproject.toml`):

- `requires-python = ">=3.11"`; `[tool.ruff] target-version = "py311"`, `line-length = 100`, `select = ["E","F","I","B","UP"]`.
- `[tool.pytest.ini_options] testpaths = ["tests"]`, `pythonpath = ["src"]`.
- `[build-system]` uses `setuptools>=68` + `wheel`; `[tool.setuptools.packages.find] where = ["src"]`.
- CI pins Python **3.11** (`python-tests.yml:20`, `:53`). The audit environment's bundled interpreter is 3.12.14 — **OBSERVED** environment fact, not a project requirement.

### 1.5 Dependency-management conventions

**OBSERVED.**

- Single source of truth for package metadata is `pyproject.toml` (PEP 621), with **optional extras**: `capture`, `dev`, `quant`, `storage`.
- Runtime dependencies are unpinned lower-bounds: `pandas>=2.2`, `numpy>=1.26`, `plotly>=5.20`, `streamlit>=1.35`.
- `requirements.txt` duplicates the runtime + dev subset (`pandas`, `numpy`, `plotly`, `streamlit`, `pytest`, `ruff`, `playwright`) and is what the Linux CI job installs first.
- **No lockfile exists** (`uv.lock`, `poetry.lock`, `Pipfile.lock`, `requirements.lock` all absent). **No** `tox.ini`, `setup.py`, `setup.cfg`, `Makefile`, `Taskfile`, or `environment.yml` exists.
- Keying detail: the `quant` extra carries the comment-blocked intent that QuantLib is optional — see §1.6.

**Implication (PROPOSED §2.6):** the repository has a *lower-bound* dependency culture but not a *reproducible-build* culture. A C++ dependency strategy that inherits "latest satisfying the bound" would be strictly weaker than what #226 is asked to guarantee, so §2.6 introduces explicit pinning for the C++ dependency set **without** modifying the existing Python conventions.

### 1.6 Existing QuantLib dependency, wrapper, experiment — or absence

**OBSERVED — QuantLib is present, but only as an optional Python extra, and only for bond mechanics.**

- `pyproject.toml` `[project.optional-dependencies] quant = ["QuantLib>=1.32"]`. There is **no** upper bound and **no** exact pin.
- `pyproject.toml`'s `capture` extra carries an explicit design comment that optional extras exist so "the ordinary `start_shiori.bat` install should stay as light as it is today". The same optionality principle therefore already governs QuantLib.
- **OBSERVED — there are FOUR production modules that import QuantLib under `src/`.** All four use the same **guarded optional import** pattern, and each states in its own docstring that it **prices nothing**.
- `src/shiori_pricing_lab/pricing/bli_quantlib_bond_adapter.py` is the **bond-mechanics** module. Its module docstring states the scope precisely (`:1-9`): QuantLib is used for **bond mechanics only** — regular coupon schedule generation, per-100 coupon cashflow amounts, and accrued interest at one explicit caller-supplied date — and that "**No curve, no discount factor, no forward clean price, no yield-to-price conversion, no volatility, no Black-76, no option PV, and no Greeks are computed, imported, or read here.** QuantLib never prices anything in this module."
- Its import is guarded: `import QuantLib as ql` at `:152`, with `BLIQuantLibNotAvailableError` (`:157`), `is_quantlib_available()` (`:212`), and `_require_quantlib()` (`:218`).
- **OBSERVED — the complete list of production QuantLib consumers** (this matters because an incomplete inventory would make §3.4's isolation boundary wrong):

| Module | Import site | QuantLib used for | Stated scope |
|---|---|---|---|
| `pricing/bli_quantlib_bond_adapter.py` | `:152` | coupon schedule generation, per-100 cashflow amounts, accrued interest | "QuantLib never prices anything in this module." |
| `pricing/bli_bond_advanced_field_resolver.py` | `:364` | coupon grid / `last_coupon_date` derivation for the standalone route (Issue #161) | "**This module prices nothing.** It computes no forward, no volatility, no discount factor, no curve node, no Black-76 value and no Greek" |
| `pricing/bli_bond_convention_profile.py` | `:70` | **settlement calendars** reused verbatim, per market convention profile (Issue #161) | market-specific convention values and rules only |
| `pricing/bli_ust_coupon_payment_date.py` | `:58` | **payment-date calendar** (Fedwire funds) mapping scheduled → actually-paid date (Issue #175) | "one small, explicit, deterministic function" — no schedule, amount, accrual, price, or curve |

- Each of the latter three uses the guarded form `try: import QuantLib as ql / except ImportError: ql = None`, with a comment pointing at the optional extra (e.g. `bli_bond_advanced_field_resolver.py:363-365`).
- Calendars: the **adapter** uses `ql.NullCalendar()` deliberately (`:11-15`) and declares calendar-adjusted payment dates out of scope "until a separate, reviewed calendar-source contract exists". **Separately, and importantly for §3.6 D2**, the two convention modules above already reuse **named** QuantLib calendars by approved convention (SIFMA settlement in `bli_bond_convention_profile.py`; Fedwire funds in `bli_ust_coupon_payment_date.py`, deliberately distinct from it). So the repository's precedent is that a calendar source is an **explicit convention choice**, never an inherited default.
- **OBSERVED:** `docs/31...:213` states the intended governance of this optionality: "core never requires QuantLib (`pyproject.toml`: QuantLib in `quant` extra only; CI installs `[quant]`, local minimal installs may not — the missing-QuantLib error must propagate, never be caught into `FAILED`)".
- **OBSERVED:** tests guard on availability, e.g. a module-level `_requires_quantlib = pytest.mark.skipif(...)` in `tests/test_bli_black76_european_greeks.py:33`.
- **OBSERVED:** the legacy `SPEC_v1.4.md:473` still describes a "Pricing Core | QuantLib-Python + custom wrapper" and `README.md:61` mentions installing "including QuantLib" into the repo-local `.venv`. These are **legacy reference** statements; the authoritative post-#222 direction is `docs/31` §4/§8 (C++ owns new Rates pricing; Python keeps acquisition/normalization/persistence/UI).

**Implication (PROPOSED §3):** QuantLib already exists in Shiori in exactly two *roles* — an **optional** Python dependency, and modules confined to **bond mechanics and date/convention resolution** that explicitly price nothing. **Four** modules occupy the second role, and all four sit on the protected bond-option line (§1.8). #226 must not silently convert either role into "QuantLib is the Rates pricing engine by default", and §3.4's dependency boundary is drawn against this four-module reality rather than a single-module one.

### 1.7 Packaging / launcher constraints

**OBSERVED.**

- `start_shiori.bat` is a thin shim that locates a Python 3.11+ interpreter and hands off to the testable `scripts/launch_workbench.py` ("repo-root resolution, venv, dependency install, server start, readiness wait, browser open").
- The launcher creates/reuses a **repo-local `.venv`** and must work with **no administrator rights** and **no execution-policy change**.
- The launcher must survive a repository path containing spaces, parentheses, and non-ASCII characters (enforced by the `windows-launcher-smoke` job).
- `.gitignore` already ignores Python build output `build/`, `dist/`, `*.egg-info/`, plus `/cache/`, `*.db`, `*.sqlite3`, `*.parquet`, `*.log`.

**Implications (PROPOSED §2, §11):**

1. The future C++ build must not require the launcher's Python path to contain a compiler, SDK, or administrator install step.
2. A **broad** `build/` ignore already exists. **PROPOSED:** C++ build trees must live under an explicitly named out-of-source directory (e.g. `cpp/rates_engine/build/`) and **no** tracked source directory may be named `build/`, because the existing ignore rule would silently hide it. This is a concrete trap for #227 and is called out again in §2.4.

### 1.8 Existing bond-option validation path that must remain protected

**OBSERVED.** Per `docs/31...:7` §7 and `docs/31...:210-215`, the validated bond-option line is defined by these modules, and the rule is that they may be **read** (and pure math ported with pinned tests) but **never modified** in a way that changes bond-option behavior:

- `pricing/bli_quantlib_bond_adapter.py` (schedule/accrual only, `NullCalendar`, optional import, `BLIQuantLibNotAvailableError` propagates)
- the other **three** guarded QuantLib consumers identified in §1.6, which sit on this same line: `pricing/bli_bond_advanced_field_resolver.py`, `pricing/bli_bond_convention_profile.py`, `pricing/bli_ust_coupon_payment_date.py`
- the BLI curve chain: `bli_curve_selector`, `bli_zero_curve_nodes`, `bli_zero_rate_interpolation`, `bli_discount_factor`, `bli_curve_discount_factor`
- `data/bli_snapshot.py`, `data/bli_standalone_option_request.py`
- the bond-option pricing modules in `src/shiori_pricing_lab/pricing/` (`bli_bond_option_price_basis`, `bli_forward_clean_price`, `bli_black76_price_option`, `bli_bond_modified_duration`, `bli_repo_carry_forward`, …) and their tests under `tests/test_bli_*`.

**OBSERVED — the guard extends to tests.** Eight test modules import QuantLib or guard on its availability (`test_bli_black76_european_greeks`, `test_bli_bond_advanced_field_resolver`, `test_bli_bond_convention_profile`, `test_bli_mvp_ui`, `test_bli_quantlib_bond_adapter`, `test_bli_ust_coupon_payment_date`, `test_corporate_direct_vol_pricing`, `test_standalone_option_workbench_prototype_browser`). A change that made QuantLib mandatory would surface here first, which is why §11.2 PR1 keeps the Python suite mandatory and unfiltered.

**OBSERVED:** the build-level expression of this boundary (`docs/31...:213`) — the Python core must never *require* QuantLib, and a missing QuantLib must surface as the typed error rather than being swallowed into a `FAILED` pricing status.

**PROPOSED (§11.5):** the C++ CI plan must not weaken this. Specifically, the existing `test` and `windows-launcher-smoke` jobs remain mandatory and unfiltered; new C++ jobs are **additive**.

### 1.9 Repository layout relevant to adding a future C++ subsystem

**OBSERVED** layout at the base SHA:

```text
Shiori-Pricing-Lab/
├── .github/workflows/     python-tests.yml, opencode.yml, codex-opencode-relay.yml
├── .claude/
├── docs/                  30+ numbered architecture/preflight documents, incl. 31/32/33 (#223/#224/#225)
├── examples/              sample_market_data.csv, standalone_option_case.json
├── prototype/bond-option-workbench/   HTML/JS prototype (not production)
├── scripts/               launch_workbench.py (testable launcher)
├── src/shiori_pricing_lab/
│   ├── app/ charts/ data/ journal/ pricing/ products/ reference_data/ valuation/
├── tests/                 123 test_*.py files
├── tools/                 23 Bloomberg probe / acceptance scripts
├── AGENTS.md  CLAUDE.md  README.md  SPEC_v1.4.md  ANNEX_A_v1.4.md
├── pyproject.toml  requirements.txt  start_shiori.bat  .gitignore
```

**OBSERVED:** `src/shiori_pricing_lab/` contains 103 `.py` files; `tests/` contains 123 `test_*.py` files. There is no existing top-level directory reserved for compiled code, so the new subsystem needs a **new root-level namespace** rather than living under `src/`.

**PROPOSED (§2.3):** that namespace is `cpp/rates_engine/`, matching the tentative tree in `docs/31...:227-233`, now locked by this document.

### 1.10 Existing tests and CI behaviors that #227 must preserve

**OBSERVED.**

- `pytest` runs with `testpaths = ["tests"]` and `pythonpath = ["src"]` (`pyproject.toml`), invoked as bare `pytest` in CI.
- Naming convention is file-per-concern `tests/test_*.py`, using `@pytest.mark.parametrize` and `@pytest.mark.skipif` (e.g. `tests/test_bli_black76_european_greeks.py:33`).
- **OBSERVED primary C++ cross-language regression anchor** (`docs/31...:93`): `tests/test_irs_reference_engine.py`, a synthetic 2-point curve (`6M 0.04`, `1Y 0.042`) over fixture `IRS-USD-1Y`, pinning `pv == 1506.7928142153469` (abs `1e-9`), `SUCCESS_WITH_WARNINGS + FORWARD_STARTING`, period counts 2/4, determinism, the full fail-closed error table (`UNSUPPORTED_PRODUCT` / `MISSING_MARKET_DATA` / `INVALID_PRODUCT`), input-immutability, and no-provider-import hygiene.
- **OBSERVED:** other authoritative anchors that #223 §3.1 designates for porting are the SOFR loader-contract tests, the vol-surface family, and the VCUB template/capture tests.
- **OBSERVED:** `docs/31...:98-104` explicitly distinguishes **authoritative** tests (deterministic, synthetic, no live values — port + gate CI) from **probes/acceptance tools** that assert no parity and must **not** be ported as truth.
- **OBSERVED:** the only concurrency primitive in `src/` is a `threading.Lock` in `app/vcub_capture_review.py:224` (a capture-review HTTP server, not pricing). Pricing code today is effectively single-threaded per request.

**PROPOSED (§9, §11):** the C++ test tree must carry the §3.1 anchors forward as *cross-language* regression gates, and the Python jobs must remain green and mandatory, so a C++ regression that changes a ported value fails CI rather than silently diverging.

### 1.11 Inventory conclusions that bound the rest of this document

| # | Conclusion | Class |
|---|---|---|
| IC1 | #226 is documentation-only; no build file, no source file, no workflow change is required or permitted | PROPOSED (from issue AC 26 + §1.2 protected-path evidence) |
| IC2 | The C++ subsystem needs a new root namespace; `cpp/rates_engine/` is locked here | PROPOSED |
| IC3 | A C++ toolchain is absent from the repo and from this audit environment; build claims stay UNPROVEN until #227 CI | OBSERVED + PROPOSED |
| IC4 | QuantLib exists today only as an optional Python extra and a bond-mechanics adapter; it is not a Rates pricing engine | OBSERVED |
| IC5 | The dependency culture is lower-bound, not pinned; C++ needs stricter pinning than the Python side currently has | OBSERVED + PROPOSED |
| IC6 | `CMakeLists.txt` / `vcpkg.json` / `conanfile*` / `pyproject.toml` / `requirements.txt` are automation-protected paths | OBSERVED |
| IC7 | `build/` is already git-ignored repo-wide — a naming trap for any tracked C++ source directory | OBSERVED + PROPOSED |
| IC8 | No path filters exist on the authoritative CI; #226 must not introduce a filter that can bypass validation | OBSERVED + PROPOSED |

---

## 2. C++ toolchain and build contract

**This section contains no build artifact.** Per AC 22–26 and §1.2's protected-path evidence, every decision below is a contract for #227 to implement, not an implementation. No `CMakeLists.txt`, `vcpkg.json`, `CMakePresets.json`, or source file is created by #226.

### 2.1 Language standard

**PROPOSED.**

| Decision | Value |
|---|---|
| Standard | **C++20, required, no fallback** |
| Extensions | **Disabled** on GCC/Clang (`-std=c++20`, never `gnu++20`); `/std:c++20` on MSVC |
| Ratchet | Later versions (C++23/26) require an explicit engine-dependency change under §3.7, not an opportunistic bump |
| Warning policy | Warnings-as-errors in CI for the project's own sources (§2.5); **not** applied to third-party targets |

Rationale: C++20 is fixed by #222/#223 (`docs/31...:238`) and is the minimum that gives designated initializers (useful for readable DTO construction), concepts (constrain the adapter interface), `std::span`/`std::string_view` (zero-copy views — permitted **only** as request-local views over engine-owned immutable storage, §6.4 N5/N6; **never** retained as published request state), and three-way comparison — all of which directly reduce the risk surface #226 is asked to close. "No extensions" matters because extension mode changes behaviour silently and is not available on MSVC, which would fork semantics between Windows-first and Linux CI.

**UNPROVEN:** that no required dependency mandates a language-extension or a lower standard. #227 must prove this at configure time on both runners.

### 2.2 CMake strategy

**PROPOSED.**

| Decision | Value |
|---|---|
| Minimum CMake | **3.25** (or newer required by a pinned dependency, whichever is higher) |
| Preset mechanism | Tracked `cpp/rates_engine/CMakePresets.json` (schema version matching the minimum CMake), with `CMakeUserPresets.json` git-ignored |
| Build style | **Out-of-source only.** In-source builds are refused, not merely discouraged |
| Build directory | `cpp/rates_engine/build/<preset-name>/` |
| Toolchain selection | Via `CMAKE_TOOLCHAIN_FILE` supplied by the vcpkg environment (§2.6), never hard-coded to an absolute user path |
| Install/export | `install()` + `QuantLib`-style config packages deferred to #227; the *names* are locked in §2.3 so consumers do not have to re-decide |
| Dependency resolution | `find_package(<Dep> CONFIG REQUIRED)` for all third-party code, resolved from the pinned manifest. **`FetchContent` is not the production configure path** (§2.6) — with the single, explicitly scoped exception of §2.6.2's documented fallback when vcpkg cannot be provisioned (exact tag **and** verified `URL_HASH` for every dependency, identical target names, acquisition mode recorded in build/Lane-B metadata, and **network-capable environments only** — a clean offline runner requires pre-seeded hash-verified sources or a verified pre-provisioned cache and otherwise **fails closed** with an explicit dependency-provisioning error, §2.6.2). An unconditional ban would have made §2.6.2's sanctioned fallback impossible to satisfy |

**PROPOSED naming and target conventions** (locked now so #227 does not re-decide them):

- CMake project name: `shiori_rates_engine`
- Target naming: `shiori_rates_core`, `shiori_rates_quantlib_adapter`, `shiori_rates_dto`, `shiori_rates_diagnostics`
- Namespace for exported/aliased targets: `Shiori::`
- Test/benchmark executables: `shiori_rates_tests_*`, `shiori_rates_bench_*` (§2.9, §2.10)

**Note (OBSERVED → PROPOSED):** `.gitignore` already contains a repo-wide `build/` rule (§1.7). **PROPOSED:** the build root lives under `cpp/rates_engine/build/`, and **no tracked source directory may be named `build/`**, because that rule would silently hide it. #227 must add a narrow, explicit ignore for the C++ build tree rather than relying on the broad existing rule, and must verify with `git check-ignore` that no intended source path is ignored.

### 2.3 Locked directory layout and library boundaries

**PROPOSED.** This locks the tree that `docs/31...:227-233` left explicitly tentative ("exact path/name not locked here (locked no later than #227)"). #226 locks the layout; #227 creates it.

```text
cpp/
└── rates_engine/
    ├── CMakeLists.txt                 # top-level project (created in #227)
    ├── CMakePresets.json              # configure/build presets (§2.2)
    ├── vcpkg.json                     # manifest: baseline + pinned versions (§2.6/§2.7)
    ├── include/shiori_rates/          # PUBLIC headers — the only installable surface
    │   ├── dto/                       # versioned DTO / wire-shape types  [BOUNDARY]
    │   ├── engine/                    # kernel entry points + result types
    │   └── diagnostics/               # runtime-telemetry types only (Lane B, §12.2)
    ├── src/
    │   ├── dto/                       # (de)serialization implementation — nlohmann::json
    │   ├── quantlib_adapter/          # ONLY place QuantLib headers may appear [ADAPTER]
    │   ├── engine/                    # pricing kernel implementation (empty skeleton in #227)
    │   └── diagnostics/               # Lane B telemetry emission + serialization
    ├── tests/                         # GoogleTest/DooDoo unit + contract + concurrency tests
    ├── benchmarks/                    # Google Benchmark targets (§10)
    └── fuzz/                          # OPTIONAL later; not created in #227 unless required
```

**PROPOSED library boundaries (the load-bearing part of this section):**

| Layer | CMake target | May include QuantLib headers? | May expose QuantLib types publicly? | Purpose |
|---|---|---|---|---|
| **DTO / serialization boundary** | `shiori_rates_dto` | **No** | **No** | Versioned wire shapes + canonical (de)serialization. Depends only on `nlohmann::json` and the standard library |
| **QuantLib adapter** | `shiori_rates_quantlib_adapter` | **Yes — exclusively** | **No** (returns/accepts only DTO types) | Translates DTO → request-local QuantLib objects and back; the sole containment boundary for QuantLib |
| **Production Rates core** | `shiori_rates_core` | **No** | **No** | Deterministic kernel over DTO inputs. If it needs QuantLib behaviour, it calls the adapter through a DTO-shaped interface |
| **Diagnostics / telemetry** | `shiori_rates_diagnostics` | No | No | **Lane B** runtime telemetry (§12.2): engine/QuantLib/compiler identity echoes, cache counters, timing hooks, gate contention. **Lane A** result diagnostics are **not** here — they are carried by the `shiori_rates_dto` result types per #225, and this target must never become the carrier of result-contract content |
| **Tests** | `shiori_rates_tests_*` | **Only** in the **two** E1a allow-listed **test** paths — `tests/adapter/` (adapter-targeted) and `tests/defaults/` (the defaults audit, which must read QuantLib's own defaults). No other `tests/` subdirectory may include `ql/` | n/a | §2.9, §3.9 **E1/E1a** |
| **Benchmarks** | `shiori_rates_bench_*` | **No** — benchmarks measure the engine through its public **DTO-shaped** API and include no `ql/` headers. §2.10's benchmark tree has **no** adapter directory, and §10.4's phases are measured across the pipeline's public boundary. Permitting direct QuantLib here would require a **third** E1a **test** allow-list path (the permitted test paths are exactly two — `tests/adapter/` and `tests/defaults/`; `src/quantlib_adapter/` is the separate *production* location, not a test path) for a measuring-only target that does not need one | n/a | §10 |
| **Integration glue (optional)** | deferred to #227 | No | No | The Python-facing binding/CLI, if any. Not designed here |

**PROPOSED enforcement (not just prose):** the architecture is only real if a build fails when it is violated.

1. `shiori_rates_core` and `shiori_rates_dto` are declared with a dependency set that **does not link** the adapter or QuantLib; a QuantLib include from those targets therefore cannot compile.
2. The adapter's public headers live under `include/shiori_rates/` **only if** they mention no QuantLib type under **§3.9 E2's syntax-aware check** and match no `ql/` include in **either** delimiter form (§3.9 E1's canonical pattern); otherwise they stay private to `src/quantlib_adapter/`.
3. A CI **isolation guard** (§11.3, job `cpp-isolation-guard`) applies the **§3.9 E1 canonical include pattern** — `#include` with **either** delimiter (`<ql/` or `"ql/`) under arbitrary preprocessor whitespace — together with the **syntax-aware public-type check of §3.9 E2** (not a literal-token grep, which `QuantLib :: Date`, a forward-declared namespace, and a `using` directive each bypass), and **fails the build** on a match. Both patterns are stated **once**, in §3.9; this section references them rather than restating narrower literals. Prose alone is not accepted as enforcement.

**UNPROVEN:** that the chosen CMake target graph can express (1) with zero transitive leakage. #227 must demonstrate it with a deliberately-wrong include that fails to compile, and record that negative test.

### 2.4 Build configurations

**PROPOSED.**

| Configuration | CMake build type | Purpose | Determinism-relevant settings |
|---|---|---|---|
| `dev` | `Debug` | Local development, assertions on | Optimisation off; no fast-math |
| `ci-debug` | `Debug` | CI correctness job, sanitizers where supported | ASan/UBSan on Linux; MSVC ASan if available |
| `ci-release` | `RelWithDebInfo` | CI regression + benchmark job | Optimisation **on**, debug info retained |
| `release` | `Release` | Packaging/consumption | Optimisation on |
| `bench` | `Release` | Benchmark builds only | Same flags as `release` so numbers are comparable |

**PROPOSED invariants:**

- **Fast-math is forbidden in every configuration.** `-ffast-math`, `-Ofast`, and `/fp:fast` are prohibited; MSVC uses `/fp:precise`. Rationale: fast-math permits reassociation and contraction, which would make the kernel's results compiler- and flag-dependent and would silently break the bit-level regression anchors that #225 §15.4 and `docs/31...:93` require (`pv == 1506.7928142153469`, abs `1e-9`). This is a **determinism** decision, not a performance-methodology decision.
- Floating-point contraction: `FP_CONTRACT` explicitly `OFF` / `-ffp-contract=off`, for the same reason.
- Coverage builds are **not** the benchmark build and never produce published numbers.
- `NDEBUG` differs between `dev` and `release`; no correctness assertion may be the *only* guard of a contract (assertions are a development aid, while fail-closed behaviour must be a returned error — see §5.6 and §13.1).

### 2.5 Compiler and toolchain matrix

**PROPOSED.**

| Platform | Compiler | Role | CI |
|---|---|---|---|
| Windows x64 | **MSVC** (VS 2022 or newer) | **Primary / Windows-first** | `windows-latest` |
| Linux x64 | **GCC** (recent major) | Required, guards portability | `ubuntu-latest` |
| Linux x64 | **Clang** (recent major) | Second Linux compiler, catches GCC-only assumptions | `ubuntu-latest` |
| MinGW / Cygwin | — | **Not supported** for production | — |
| Windows ARM64, macOS | — | Out of scope for #226 | — |

**PROPOSED:** *the exact minimum compiler versions are intentionally not frozen in #226*, because freezing a number that no runner can satisfy would be a fabricated constraint. Instead #227 must (a) record the versions actually provided by the chosen runner images, (b) set the minimum to the lowest version it can prove green, and (c) record that version in the diagnostics output (§12.2 **Lane B**) and the benchmark metadata (§10.5).

**UNPROVEN (explicitly):** that a single source tree compiles cleanly on all three compilers, and what the real minimum versions are. No such claim is made here.

### 2.6 Dependency acquisition strategy

**PROPOSED.** Dependency acquisition uses **vcpkg manifest mode** with the toolchain file, and the manifest is the single pinned source of truth.

| Decision | Value |
|---|---|
| Mechanism | vcpkg manifest mode (`vcpkg.json` + `CMAKE_TOOLCHAIN_FILE=<vcpkg>/scripts/buildsystems/vcpkg.cmake`) |
| Mode | Manifest mode, **not** classic mode (classic mode has no per-project pinning) |
| Linkage | Per-triplet; see the Windows static constraint below |
| Instrumentation triplet | A **separate vcpkg triplet** for the TSan-instrumented dependency build (§5.7 T3, §11.3), with its **own binary-cache identity**, so an instrumented QuantLib and an ordinary one can never be confused for one another |
| Binary caching | CI uses vcpkg binary caching to bound build time; the cache is **keyed on the pinned manifest + baseline + toolchain identity**, never on "latest" |
| Fallback | Documented in §2.6.2, so #227 is not blocked if vcpkg is unavailable. **vcpkg remains the primary path**; §2.6.2 locks when a substitute is permitted and requires an explicit dependency-provisioning error otherwise |

**EXTERNAL EVIDENCE — the QuantLib port exists and is directly usable:**

- vcpkg ships a `quantlib` port at `ports/quantlib/` (files `portfile.cmake`, `vcpkg.json`, `usage`), source: <https://github.com/microsoft/vcpkg/tree/master/ports/quantlib>.
- As inspected at the time of writing, `ports/quantlib/vcpkg.json` declares `"version": "1.43"`, `"license": "BSD-3-Clause AND Minpack"`, and the `supports` expression **`"!(windows & !static)"`**.
- `ports/quantlib/portfile.cmake` calls `vcpkg_check_linkage(ONLY_STATIC_LIBRARY)` when the target is Windows, with the in-file comment: *"This can (and should) be removed if QuantLib ever supports dynamically linking on Windows"*, and configures with `-DQL_BUILD_EXAMPLES=OFF -DQL_BUILD_TEST_SUITE=OFF`, installing a CMake package config at `lib/cmake/QuantLib` with `PACKAGE_NAME QuantLib`.
- **EXTERNAL EVIDENCE:** the upstream QuantLib project's latest release at the time of writing is **v1.43**, published 2026-07-14, with a published source archive digest (e.g. `QuantLib-1.43.tar.gz`, `sha256:b41206ccc4ba39b5e86bdc940d51138222b352f79d2c6fc68649f9c9bbe58701`), source: <https://github.com/lballabio/QuantLib/releases/tag/v1.43>.

**ARCHITECTURAL CONSEQUENCE (PROPOSED, high importance).** Because the Windows linkage is **static-only**, QuantLib is linked *into* the Shiori process on the primary platform. Therefore:

1. There is **no** "separate QuantLib instance per module" isolation available on Windows. Isolation must be achieved by **containment rules** (which layer may include it), **request-local construction**, and **state discipline**, not by dynamic-library partitioning.
2. Any process-global QuantLib state is process-global for the whole Shiori process on Windows. §5 treats this as the central concurrency risk.
3. Dynamic/shared linkage must **not** be assumed anywhere in the design, because the design must be identical on Windows-first.

#### 2.6.1 Considered alternatives

| Option | Why not chosen |
|---|---|
| `FetchContent` (network at configure time) | No per-project lock semantics by default; makes offline and reproducible CI harder; mixes "configure" with "download"; each clean CI run rebuilds QuantLib and Boost. Retained only as §2.6.2's documented fallback, and there only in **network-capable** environments (it cannot provision a clean offline runner) |
| Conan | Viable and stronger at version modelling, but adds a second ecosystem and a second config language for a repository that has **zero** existing native-toolchain surface. Not justified for the first C++ slice |
| System packages (`apt install quantlib-dev`) | Version differs between Linux and Windows and from any pinned number; fails the pinning policy in §2.7 outright |
| Vendoring QuantLib into the repository | Vendors ~1.4 M lines plus Boost into a repo whose stated style is one small document/change at a time; makes upgrades and licence auditing worse |

#### 2.6.2 Documented fallback (so #227 is not blocked)

**PROPOSED — locked dependency-provisioning contract (owner decision of record).** Primary acquisition remains the pinned **vcpkg** path above. A substitute is permitted **only** in the two environments below, and in **no** case does the fallback change §2.7's pinning policy — only the acquisition *mechanism*, recorded in the build identity and **Lane B** metadata (§10.5 **M4**, §12.2).

**Network-capable fallback.** If vcpkg is unavailable but network access **is** allowed, #227 may use `FetchContent` **only** with: an exact immutable source version/tag; a hash-verified archive or source (`URL_HASH` or equivalent); the **identical** target/interface boundaries from §2.3; and the acquisition mode recorded in the build/Lane-B metadata.

**Offline / restricted environment.** A clean runner with **no network** must already be supplied with **one** of:

- a **pre-seeded, hash-verified local source/archive directory** for the exact pinned dependency set; or
- a trusted **pre-provisioned dependency/binary cache**, produced from the exact pinned manifest/triplet/configuration and **verified before use**.

`FETCHCONTENT_FULLY_DISCONNECTED` may be used **only after** content has actually been pre-populated. It suppresses network access; it **is not a provisioning mechanism** and must **not** be described as one. An earlier revision of this section claimed it could bootstrap a clean, network-less runner; that claim was **incorrect and is withdrawn**.

**Fail closed.** If neither a valid pre-seeded source/archive nor a valid pre-provisioned cache is available, configure/build must **fail closed with an explicit dependency-provisioning error**. #227 must **not** silently fetch, substitute a system package, change versions, or vendor dependencies to make the build pass. **Vendoring QuantLib or Boost into this repository remains prohibited by #226** unless a future owner decision changes that policy (§2.6.1).

This section locks the **contract**; #227 owns the executable implementation and its proof.

### 2.7 Dependency version pinning policy

**PROPOSED.**

| Rule | Statement |
|---|---|
| P1 | Every C++ dependency is pinned to an **exact version** in `vcpkg.json` `overrides`, not a range and not "latest" |
| P2 | The vcpkg registry itself is pinned by an exact **`builtin-baseline` commit SHA**; a floating baseline is forbidden because it silently changes dependency versions |
| P3 | Integrity is verified by the hash recorded in the port (`SHA512` for the QuantLib port, per its `portfile.cmake`) |
| P4 | **QuantLib's pinned version is a first-class part of the build and cache identity**, recorded in **benchmark metadata** (§10.5 **M4**), in the §7.2 **cache key**, and in **Lane B** telemetry — but **not** in the #225 result contract. #225 approves no field for dependency provenance, so #226 must **not** place it in **Lane A** and must **not** add it to #225's replay identity: doing so would force #227 to overload `engine_version` or invent a field (§12.3 **G-2**, open item **U-O**). The pin is what makes the identity exact; *where it is recorded* is bounded by #225's schema |
| P5 | The Python side is **not** changed by this policy. `pyproject.toml`'s `QuantLib>=1.32` lower bound stays as-is; #226 neither tightens nor loosens it |
| P6 | The C++ pinned version and the Python `quant` extra version may differ, but the *difference must be visible and deliberate*: they are separate dependency planes. A shared version is **not** required and must not be assumed |
| P7 | No `CMakeLists.txt` / `vcpkg.json` is added by #226 (§1.2), and any future change to them is a protected-path change requiring explicit review |

**OBSERVED tension worth naming:** `pyproject.toml`'s `QuantLib>=1.32` is a lower bound, while the vcpkg port currently carries `1.43`. P6 is what keeps this from becoming a silent inconsistency: the Python binding version and the C++ library version are independently owned, and the **build identity** — **Lane B** runtime/build telemetry, §10.5 **M4** and the §7.2 cache key (§3.2) — records which one it was built against, **not** #225's result `engine` identity, for which no dependency-provenance field is approved (**U-O**).

### 2.8 Reproducibility expectations

**PROPOSED — and deliberately limited.**

| Guarantee | Status |
|---|---|
| Same source + same pinned manifest + same baseline + same compiler major + same build type ⇒ same *behaviour* on the regression fixtures | **PROPOSED**, to be proven in #227 |
| Dependency *versions* are reproducible from the manifest alone (no floating resolution) | **PROPOSED** (P1/P2/P3) |
| The exact toolchain identity used for a given artifact is *recorded* (compiler, version, flags, QuantLib version, baseline) | **PROPOSED** (§10.5 **M4**, §12.2 **Lane B**) |
| **Bit-identical binaries** across platforms, compilers, or machines | **NOT CLAIMED.** The build does not reproduce bit-identical binaries, and no design decision depends on it |
| Bit-identical *numeric results* across compilers | **NOT CLAIMED.** Regression anchors assert agreement within a documented absolute tolerance (the `1e-9` anchor from `docs/31...:93`), not bit equality |

Rationale: claiming binary or bit-level reproducibility would be an unevidenced promise. What the architecture actually needs — and what is therefore promised — is that *the inputs to any number are knowable*, and that a changed dependency or compiler is **detectable** rather than silent.

### 2.9 Test executable layout

**PROPOSED.**

```text
cpp/rates_engine/tests/
├── unit/                # per-component logic; pure logic, no QuantLib
├── dto/                 # JSON round-trip + schema-version + fail-closed tests (TL0)
├── adapter/             # the ONLY test area that may use QuantLib directly
├── defaults/            # QuantLib default-audit matrix, §3.6 D1–D17 (TL1)
├── determinism/         # replay identity, §9.5 / #225 §15.4 (TL3)
├── cache/               # §7.7 CT1–CT8 + CT3b (TL4)
├── calibration/         # §8.3 outcome preservation (TL5)
├── concurrency/         # §5.7 T1–T8 (TL6) — quarantined per §9.8
├── benchmark/           # §10.8 correctness-checked benchmarks (TL7)
├── regression/          # ports of docs/31 §3.1 authoritative anchors
├── parity/              # Python ↔ C++ boundary, §9.3 PB1–PB6
└── fixtures/            # synthetic, deterministic fixture data (no live market values)
```

**PROPOSED — this tree is the single canonical test layout for the whole document.** §9.1 previously published a *different* tree (it had `contract/`/`determinism/`/`cache/`/`parity/` but no `adapter/`, `defaults/`, `calibration/`, `regression/`) while this section had the reverse. Two disagreeing layouts would leave #227 unable to build the test target list without re-deciding it, which is exactly what #226 exists to prevent. §9.1 now reproduces this tree verbatim rather than defining its own. `dto/` here is the directory §9.1's earlier draft called `contract/`; the name `dto/` is retained because it matches §2.3's `dto/` target.

**PROPOSED:** one CMake test executable per directory above, registered with CTest (`add_test`), so `ctest` is the single CI entry point and a failing directory is individually identifiable. Fixtures are **synthetic and committed**; no live Bloomberg value and no workstation evidence is ever a test input (consistent with `docs/31...:98-104`).

**PROPOSED — the QuantLib-permitted test directories are exactly two** and are the only ones the E1a allow-list may name: `adapter/` (the declared QuantLib-using test area) and `defaults/` (the default-audit matrix, which must read QuantLib's own defaults to detect a change in them). `unit/` is declared **no-QuantLib**, so it is deliberately **not** on that list.

### 2.10 Benchmark executable layout

**PROPOSED.**

```text
cpp/rates_engine/benchmarks/
├── startup/             # process startup / static-init cost     — #227, **available now** (§10.3)
├── dto/                 # decode / encode                        — #227, **available now** (§10.3)
├── materialize/         # non-price-bearing object construction  — #227, **available now** only where executable without unresolved methodology (§10.3)
│                      # curve/model materialization             — **DEFINED / DEFERRED**, owning implementation/methodology issue — NOT created by #227
├── kernel/              # pricing kernel                         — **DEFINED / DEFERRED**, no earlier than **#229**
├── risk/                # risk                                   — **DEFINED / DEFERRED**, owning risk implementation/methodology issue
├── calibration/         # calibration                            — **DEFINED / DEFERRED**, owning calibration-methodology issue
└── fixtures/            # benchmark fixture descriptors (identity-bearing, §10.5)
```

**PROPOSED:** one executable per stage, mirroring §10.3's phase rows. A single monolithic "benchmark the engine" binary is rejected because it would produce exactly the blended number §10.6 forbids. **Only the phases marked available in §10.3 are created by #227.** The remaining directories above are declared **placeholders**: they must not be populated, built, or made executable before their owning issue lands (#229 for the kernel, the owning risk/calibration/curve issues otherwise).

### 2.11 Non-production statement for this section

**PROPOSED / non-goal.** §2 creates nothing. It deliberately contains no pricing algorithm, no curve construction, no calibration, and no engine logic. Where a name such as `engine/` appears, it denotes an **empty skeleton landing zone** for #227, not an implementation. The absence of production behaviour here is a scope guarantee, not an omission.

---

## 3. QuantLib versioning and isolation boundary

### 3.1 The governing rule

**PROPOSED — this is the single most important rule in the document, and everything in §3 is a mechanism for enforcing it:**

> **QuantLib defaults MUST NOT silently become Shiori pricing methodology.**

"Silently" is the operative word. It is not a rule that QuantLib may never be used, and it is not a rule that a Shiori value may never equal a QuantLib default. It is a rule that **no behaviour-affecting quantity may reach a Shiori result through a QuantLib default that no Shiori contract names.**

This is the direct C++-engine expression of an already-approved #225 requirement. **OBSERVED:** `docs/33_rates_market_model_result_contracts_225.md` §16 rule 12 states that any hidden dependence on QuantLib defaults or Bloomberg conventions is a **methodology violation** requiring the §1.4 stop-and-escalate path. §3 therefore does not invent a new rule; it defines how the C++ layer obeys an existing one.

### 3.2 QuantLib version pinning strategy

**PROPOSED.**

| Decision | Value |
|---|---|
| Pin form | **One exact version**, recorded in `vcpkg.json` `overrides` and reinforced by the pinned `builtin-baseline` (§2.7 P1/P2) |
| **Initial pin — LOCKED** | **`1.43`**, and it is **locked, not a candidate**. Every fact in §4 — the process-global singletons, the macro defaults, the `LazyObject` ordering, the OpenMP pragma sites — is explicitly scoped to `v1.43`. Another exact version would invalidate that evidence base rather than merely being a different pin |
| Who may change the pin | Only via the §3.8 upgrade procedure **and** a repeat of the §4 audit for the candidate version. No incidental bump, no "while I was in there" |
| Deviation from `1.43` on a first implementation | **Re-audit is mandatory.** §3.8's audit is *change*-triggered, and a first implementation has no prior recorded pin for it to trigger from — so the obligation is stated here instead: selecting any version other than `1.43` requires repeating the §4 version-specific audit and obtaining §3.8 approval **before** implementation, because §4's defaults, singleton and OpenMP findings are version-conditioned |
| Where the pin is recorded | The manifest (authority), **Lane B** runtime/build telemetry, the benchmark metadata (§10.5 **M4**), the §7.2 **cache key** / version tagging (§7.6), and CI/build evidence. **Not** in `PricingResult.diagnostics`, `RiskResult.diagnostics`, `replay`, or any other existing #225 Lane-A field — see the placement rule below |
| Range/`>=` pins | **Forbidden** for the C++ dependency. A range pin is what makes a hidden default change possible on a clean CI machine |
| Python side | **Unchanged.** `pyproject.toml`'s `quant` extra keeps its existing lower bound. The two planes are independent (§2.7 P6) |

**PROPOSED — the dependency-provenance placement rule (reconciled; authoritative).** The approved #225 result schemas contain **no** dedicated dependency-provenance field for the QuantLib pin or its configuration. Therefore:

- #226 does **not** require the QuantLib version, pin, or macro configuration to be encoded into `PricingResult.diagnostics`, `RiskResult.diagnostics`, `replay`, or any other existing #225 Lane-A/result field, unless #225 already defines that field **for that semantic**.
- #226 must **not** overload `engine_version`, `method`, assumptions, diagnostics, or any other existing field merely to smuggle dependency provenance into the result contract.
- The pin remains **required** in **Lane B** runtime/build telemetry, benchmark metadata (§10.5 **M4**), build identity, cache compatibility / version tagging (§7.6), and CI/build evidence.
- Result-level dependency provenance remains **U-O — unresolved placement** until an owning schema issue explicitly adds a field. #226 may identify the need; **#227 must not invent the encoding** (§12.3 G-2, Appendix C).
- **This does not weaken cache compatibility.** The QuantLib version/configuration may still be part of internal cache validity and version identity without becoming a #225 result-DTO field.

Every other site in this document that mentions where the pin or dependency identity is "recorded" is normalized to this rule (§2.5, §2.7 P4 **and the P6 paragraph**, §2.8, §3.9 U5, §4.5, §5.8 PE2, §7.6, §12.2, §12.3, Appendix C).

**PROPOSED — "version is locked" and "the build is green" are two different claims, and only the first is made here.** #226 locks the **version** to `1.43`, because that is the version every §4 fact is scoped to. #226 does **not** claim the build has ever run: no local C++ toolchain exists in this environment (§1.10), so the build outcome remains **UNPROVEN**. #227 must confirm that `1.43` is (a) available from the pinned baseline and (b) green on all three compilers. If `1.43` cannot satisfy (a) or (b), that is a §3.8/§13 matter to raise with the owner — **not** a licence to select a different version silently, which is exactly the gap this rule closes. An earlier draft said the pin was "a candidate, not a locked value", which would have let #227 choose a version that §4 never audited while §3.8's change-triggered audit stayed silent for want of a prior pin.

### 3.3 How QuantLib is acquired and linked

**PROPOSED.** On the **normal** path, via the vcpkg port (§2.6), consumed through `find_package(QuantLib CONFIG REQUIRED)` and linking the `QuantLib::QuantLib` imported target. **When vcpkg cannot be provisioned**, §2.6.2's documented fallback applies instead (`FetchContent` with an exact tag and a verified `URL_HASH`, identical target names, the substitution recorded — **network-capable environments only**; a clean offline runner requires pre-provisioned verified inputs or fails closed, §2.6.2) — this sentence describes the normal path and does **not** forbid the sanctioned one; an earlier revision stated it unconditionally, which made §2.6.2's fallback unsatisfiable. **No** QuantLib header or source is vendored into the repository; and **no** `ql/` include in **either** delimiter form appears outside the §3.9 **E1a allow-list** — that is, outside `src/quantlib_adapter/` in the production tree, plus the **two** test directories §2.9 declares as the only QuantLib-permitted test areas (`tests/adapter/`, `tests/defaults/`). The earlier revision of this sentence also contradicted §2.9/§3.9 E1a, because the default-audit matrix exists precisely to *read* QuantLib's own defaults.

**EXTERNAL EVIDENCE (recap of §2.6, restated here because it constrains this section):** `ports/quantlib/portfile.cmake` calls `vcpkg_check_linkage(ONLY_STATIC_LIBRARY)` on Windows, installing `lib/cmake/QuantLib`. <https://github.com/microsoft/vcpkg/tree/master/ports/quantlib>

**PROPOSED consequence:** the design assumes **static linkage on Windows** everywhere. Therefore isolation is *not* obtained from dynamic linking, and any argument of the form "a separate QuantLib instance will protect us" is rejected. Containment is obtained by compile-time reachability rules (§3.5) and by state discipline (§3.7, §5).

### 3.4 Which parts of Shiori may depend directly on QuantLib

**PROPOSED. The QuantLib-dependent surface is exactly these, and the two planes are disjoint:**

| Consumer | Nature | Change by #226 |
|---|---|---|
| `src/shiori_pricing_lab/pricing/bli_quantlib_bond_adapter.py` (+ its tests) | **Existing Python** bond-mechanics-only usage, optional import, `NullCalendar` | **None.** Protected path (§1.8) |
| `pricing/bli_bond_advanced_field_resolver.py`, `pricing/bli_bond_convention_profile.py`, `pricing/bli_ust_coupon_payment_date.py` (+ their tests) | **Existing Python** date/convention resolution using guarded optional imports and named QuantLib calendars; each documents that it prices nothing (§1.6) | **None.** Same protected line (§1.8) |
| `shiori_rates_quantlib_adapter` (C++ target) | **New C++** adapter; the only C++ TU allowed to include `ql/...` | Not created in #226; contract defined here |

**PROPOSED:** the two planes are independent — the **Python** consumers are the protected bond-option line and must not be disturbed (AC 20), while the **new C++** adapter is the only place C++ may see QuantLib (AC 6). Note that the Python plane already establishes the discipline §3.6 D2/D3 rely on: calendars and day counts arrive from an **explicit convention**, not from a library default.

**PROPOSED prohibitions:**

- `shiori_rates_core` and `shiori_rates_dto` must not include QuantLib headers, link QuantLib, or name a QuantLib type — enforced by the target graph **and** the CI guard (§3.9).
- No *new* Python module may import QuantLib as a result of #226. #226 adds no Python code.
- The existing Python QuantLib usage is **not** to be rewritten, moved, or "unified" with the C++ adapter. Two planes, one optional Python extra for bond mechanics, one C++ adapter for the Rates engine. Unifying them is out of scope and would touch a protected path.

### 3.5 The adapter / wrapper boundary

**PROPOSED.** The adapter's job is translation in one direction at a time, with no state retained between calls:

| Direction | Input | Output |
|---|---|---|
| Inbound | A DTO value (JSON-decoded, version-checked) | A **request-local** QuantLib object graph |
| Outbound | A QuantLib result or exception | A DTO value (or a Shiori error value) |

**PROPOSED rules for the adapter:**

- **A1 — No retained state.** The adapter holds no QuantLib object, handle, or pointer after a call returns. Request-local objects die with the request.
- **A2 — No exception escape.** A QuantLib exception must be caught at the adapter boundary and converted into a Shiori error value. A `ql::` exception type must never appear in a caller-visible signature or cross the DTO boundary.
- **A3 — No global mutation without a documented restore.** If the adapter must touch QuantLib global/session state (e.g. an evaluation date), it must set it explicitly, and it must be restored/owned in a documented, exception-safe manner **that reproduces the original *raw* state — including the raw-null / clock-following state, which `SavedSettings` alone cannot represent (§4.3) and §5.5 locks**. What is actually required, and what is UNPROVEN, is the subject of §4 and §5 — **A3 does not assert that global mutation is safe; it asserts that if it happens it is explicit, narrow, and provably restored.**
- **A4 — Every behaviour-affecting setting is set explicitly** (§3.6).
- **A5 — DTO in, DTO out.** The adapter's externally visible interface mentions only DTO and standard-library types.

**PROPOSED header placement rule (mechanically decidable):** an adapter header may live in the public `include/shiori_rates/` tree **only if** it mentions no QuantLib type under **§3.9 E2's syntax-aware check** — a literal `QuantLib::` grep would accept a header that spells `QuantLib :: Date` or leans on a `using` directive — and includes no `ql/...` header in **either** `<...>` or `"..."` form (§3.9 E1). Otherwise it stays private under `src/quantlib_adapter/`. This makes "does the adapter leak?" a grep question rather than a judgement call.

### 3.6 Policy for QuantLib defaults

**PROPOSED — the mechanism that makes §3.1 enforceable.**

The rule is implemented by **inversion**: rather than starting from QuantLib's defaults and documenting the ones Shiori overrides, the adapter **starts from an explicit value for every behaviour-affecting setting**, so that a QuantLib default changing in a version upgrade cannot change a Shiori number.

**PROPOSED: every item in the following table must be either (a) explicitly set from a named DTO field, or (b) explicitly refused.** "Refused" means the request fails closed with an `UNRESOLVED_METHODOLOGY` reason (per #225 §16 and the `ValueOrReason` shape in `docs/33...` §4.1) — never "fall back to the library default".

| # | Behaviour-affecting setting | Required handling |
|---|---|---|
| D1 | **Evaluation date** | Set explicitly from the valuation context; never inherited, never "today" |
| D2 | Calendar / holiday source | From an explicit contract. **OBSERVED precedent:** the existing Python adapter deliberately uses `ql.NullCalendar()` and refuses calendar-adjusted dates until an approved calendar-source contract exists (`bli_quantlib_bond_adapter.py:11-15`) |
| D3 | Day-count convention | Explicit, literal mapping; no nearest-match guessing |
| D4 | Business-day convention | Explicit; no default |
| D5 | Schedule generation: stub rule, end-of-month anchoring, first/last coupon dates, forward/backward generation, `DateGeneration` rule | Explicit. **OBSERVED precedent:** the existing adapter refuses to *infer* EOM mode and validates two candidate grids against declared dates (`bli_quantlib_bond_adapter.py:17-41`) — the same "never infer" discipline applies in C++ |
| D6 | Payment/observation lag, lookback/shift/lockout/cutoff | Explicit (#224 convention authority) |
| D7 | Compounding mechanics / overnight compounding formula | Explicit (#224 convention authority) |
| D8 | **Interpolation method** | Explicit. A library interpolation default is a RED methodology item (§13.2) |
| D9 | **Extrapolation behaviour** | Explicit from an approved contract. **No default posture is applied by the adapter — not even `FAIL_CLOSED`.** #225 §7.5 pre-approves `FAIL_CLOSED` only as a *fallback contract **shape*** (`{method_id, method_version, parameters}` are required fields whose *values* are `UNRESOLVED`), and that section is titled "shape, not values". Turning that shape into a default **value** would select a RED methodology item (§13.2) without an owner decision, and would let an input that names no extrapolation policy proceed to valuation instead of being refused. An input carrying no owner-approved extrapolation policy is **refused** with `UNRESOLVED_METHODOLOGY` — the adapter may substitute neither an extrapolation method nor a refusal posture of its own, because both are methodology choices |
| D10 | Curve construction/bootstrap methodology | Explicit or absent — **RED-01 is unresolved**; neither Path A nor Path B is chosen here |
| D11 | Volatility type/unit/smile model and any bp↔decimal conversion | Explicit; mirrors the Python resolver's stated-unit discipline (`docs/31...:65`) |
| D12 | Model parameters and calibration objective/optimizer/tolerances | Explicit or refused; **RED-225 items** (§13.2). No optimizer default from QuantLib |
| D13 | Settlement / exercise conventions | Explicit (#225 §10/§11 contracts) |
| D14 | `Settings` flags beyond the evaluation date (e.g. historic-fixing enforcement, reference-date-event inclusion, same-day cashflow inclusion) | **Explicit from an owner-approved input, or refused with `UNRESOLVED_METHODOLOGY`** — the same treatment as D9, and for the same reason. These flags change **which cashflows enter a result**, so they are pricing/methodology choices, not adapter settings. **"Record and pin the pinned version's value" is *not* an approved alternative**: pinning prevents upgrade drift but does not approve the choice, and it would let the engine adopt QuantLib 1.43's preference by the act of recording it. A flag that is not yet exposed as an approved input is therefore **refused, not inherited**. The D1–D17 pinning test still asserts each *effective* value so a version upgrade fails the build rather than changing a price — but that test is a drift detector, never an approval mechanism |
| D15 | Index fixing history / any index-level state | Explicitly scoped per request; see §4 and §5 for what is UNPROVEN |
| D16 | Day counter, tenor, and frequency defaults in convenience constructors | Avoided by using explicit constructors; no convenience overload that silently applies a default |
| D17 | Internal QuantLib parallelism (the optional OpenMP code paths — see §4.11; **no thread pool exists in the library**) | Explicitly configured or disabled (§5), and recorded in **Lane B** runtime telemetry (§12.2) |

**PROPOSED default-audit test (falsifiable, required of #227).** A test constructs the adapter's request with no optional fields set and asserts that **every** restricted DTO field is either present or produces a fail-closed error — i.e. the test proves that the adapter cannot silently proceed on a default. A second test pins the *effective* value of each D1–D17 setting so that a QuantLib upgrade changing a default produces a **test failure** rather than a changed price. This converts §3.1 from a documented intention into a regression gate.

**UNPROVEN:** that the D1–D17 enumeration is complete. #227 must derive the enumeration from the pinned QuantLib version's own headers/constructors (not from memory) and extend it; §3.6 is the *policy and the test shape*, not a claim of exhaustiveness.

### 3.7 Policy for QuantLib global / session state

**PROPOSED (short form; the evidence and the full analysis are §4 and §5).**

- The existence, scope (process-global vs thread-local), and configurability of QuantLib global/session state is a **version-, build-flag-, and configuration-dependent** property. §4 states exactly what is proven and what stays **UNPROVEN**.
- **PROPOSED:** the engine does **not** assume bank-level thread safety of QuantLib global state, and **production parallel pricing remains DISABLED** until the dedicated concurrency-correctness tests in §5.7 prove the chosen configuration safe (§5.2).
- **PROPOSED:** the adapter treats any global/session state it must touch as a **serialized, explicitly-owned critical section**, and **Lane B** telemetry records which mode was in effect (§12.2).

### 3.8 Policy for future QuantLib upgrades

**PROPOSED.** A QuantLib upgrade is an **explicit engine-dependency change**, never a routine bump:

| Step | Requirement |
|---|---|
| U1 | Its own change (own PR / own commit scope), citing the old → new version |
| U2 | **Regression evidence** over the authoritative anchors of `docs/31...` §3.1 — the ported tests must run and their values must hold within the documented tolerance |
| U3 | A re-run of the default-audit tests (§3.6). A changed default must be an intentional, documented decision — which, if it changes comparison semantics rather than costs, is a **RED** item and stops for owner input |
| U4 | A re-run of the concurrency-correctness tests (§5.7), because a new version may change observer/lazy-object or session behaviour |
| U5 | Recorded identity update on upgrade: **Lane B** runtime/build telemetry, benchmark metadata (§10.5 **M4**), and the §7.2 cache key / version tag (§7.6). **No Lane-A / #225 result field is written**, because none is approved for dependency provenance — see **U-O** and §3.2's placement rule |
| U6 | The pinned baseline commit is updated deliberately, with the resulting dependency-version deltas visible in the diff |
| U7 | No upgrade is bundled with unrelated work (AGENTS.md rule 2) |

**PROPOSED:** if U3 finds that an upgrade would change a *methodology* result (rather than only performance or an unrelated fix), the upgrade **stops** and returns to the owner under §13.2 — the engine may not absorb a methodology change through a dependency bump.

### 3.9 Enforcement checklist (what #227 must demonstrate)

**PROPOSED.** Each item is checkable, and each is a build/CI failure rather than a review comment:

| # | Check | Mechanism |
|---|---|---|
| E1 | No `ql/` include **anywhere under `cpp/rates_engine/`** except **two separately-scoped permissions**: (i) the **production** adapter source `src/quantlib_adapter/` — the only *production* location permitted to include `ql/...` (§2.3); and (ii) E1a's **exactly two test paths**. The scan covers the **whole C++ tree** — `src/engine/`, `src/dto/`, `src/diagnostics/`, `include/shiori_rates/`, `benchmarks/`, and **every** `tests/` subdirectory — not the production/public trees alone, because a forbidden include can enter the production kernel under a directory the grep does not enumerate, or a test area that is not on the allow-list (e.g. `tests/cache/`) | CI grep over `cpp/rates_engine/` with the **production** adapter path and E1a's **exactly two test** paths subtracted; an explicit **allow-list**, so any other match on `#include <ql/` fails the build. **An earlier revision enumerated `core/`, which is not a path in §2.3's locked layout** — the production kernel directory is `src/engine/` — so the grep would have targeted a directory that does not exist and silently passed the real kernel while never scanning `tests/` at all. **The canonical pattern matches both include forms and arbitrary preprocessor whitespace** — `#include` followed by either `<ql/` **or** `"ql/` (e.g. `#include "ql/settings.hpp"` is a valid and otherwise guard-bypassing form) — together with E2's **syntax-aware** public-type check (E2 shows why a literal token grep cannot close this). A guard matching only the angle-bracket literal is trivially bypassable. **This E1 row is the single canonical statement of the pattern**; §2.3 and §3.5 reference it instead of restating a narrower literal |
| E1a | The exception is *required*, not a convenience, and the permitted **test** paths are **exactly two**, both named by §2.9's canonical layout: (1) `tests/adapter/` — which §2.9 declares as "the ONLY test area that may use QuantLib directly"; and (2) `tests/defaults/` — the default-audit matrix (TL1) whose stated job is to "detect a change in the underlying QuantLib default" and which cannot do that without reading QuantLib. **These two are the only *test* paths E1a names.** The production adapter source `src/quantlib_adapter/` is a **separate production permission** under E1/§2.3 — it is **not** a test path and is **not** counted here. No third allow-list path may be created. The guard must therefore **enumerate** those paths rather than forbid everything outside the adapter. **`tests/unit/` is deliberately excluded**, because §2.9 declares it "pure logic, no QuantLib" — permitting it would silently allow QuantLib into the directory whose whole purpose is to be free of it, while the adapter tests that legitimately need QuantLib were previously unlisted. **A guard that rejects the adapter/defaults tests defeats AC 7** — the mechanical enforcement of "QuantLib defaults MUST NOT silently become Shiori methodology" | Guard is an explicit **allow-list** of directories, not a deny-list; adding a QuantLib-reading test directory is a reviewed one-line change to that list |
| E2 | **No QuantLib type appears in any public header or exported signature**, in any spelling: not only `QuantLib::Date` but also `QuantLib :: Date` (whitespace around `::`), a forward-declared `namespace QuantLib { class Date; }` followed by a bare type name, and `using namespace QuantLib;` / `using QuantLib::Date;` followed by an unqualified name — none of which need a `ql/` include. Also: no `ql::` exception type in a caller-visible signature | **Syntax-aware, not a literal-token grep.** Every form above is invisible to a grep for the literal strings `QuantLib::`/`ql::`, so the check must **parse** the public headers (libclang AST or `-ast-dump`/exported-symbol inspection) or, at minimum, match `namespace\s+QuantLib`, `using\s+(namespace\s+)?QuantLib`, and `QuantLib\s*::` under arbitrary whitespace. **A compile-fail consumer test is not sufficient on its own:** accepting a pointer or reference to an *incomplete* type compiles, so the consumer must **use the type by value** or otherwise require it complete. §2.3 records this as `UNPROVEN` and requires #227 to demonstrate and record the negative test |
| E3 | `shiori_rates_dto` / `shiori_rates_core` do not link QuantLib | Target graph **plus a link-artifact scan over every artifact shape the build can actually produce** — `.lib` and `.a` **static archives**, `.so` shared objects, and the final linked executables or link map. **A `.lib`/`.so`-only scan is insufficient:** §2.6 makes Windows static-only and §11.3 deliberately does not assert Linux linkage, so a compliant **static Linux build** produces `.a` archives that such a scan never opens, and a forbidden QuantLib reference escapes E3 entirely on a build that satisfies every other rule. The platform-specific artifact set must be **derived from the built targets**, not hard-coded (MSVC `.lib`, GNU/LLVM `.a`) |
| E4 | No `ql::` exception is part of a caller-visible signature | E2 plus an exception-mapping test (A2) |
| E5 | Every D1–D17 setting is explicit or refused | §3.6 default-audit tests |
| E6 | QuantLib version is emitted as **dependency provenance** in **Lane B** telemetry and in benchmark metadata (§10.5 **M4**) — **not** in the #225 result contract, which approves no field for it (§12.3 **G-2**, Appendix C **U-O**) | §12.2–§12.3, §10.5 |

**PROPOSED:** E1/E1a are stated as an **allow-list** deliberately. A deny-list formulation ("no `ql/` anywhere else") is the natural phrasing but it is wrong here, because the tests that make the isolation auditable must themselves see QuantLib. **The list is two-tier and must be read that way:** the *production* include boundary admits exactly one location (`src/quantlib_adapter/`), and the *test* boundary admits exactly two (`tests/adapter/`, `tests/defaults/`). The two tiers are counted **separately** and are never summed into a single number. The isolatable property is that **production targets** do not depend on QuantLib — which E3 enforces structurally at the link level — not that the string `ql/` appears nowhere outside one directory.

---

## 4. QuantLib evidence audit (the basis for §3 and §5)

This section is the **evidence** for the claims §3 and §5 rely on. It deliberately contains no Shiori policy. Every row states the **exact configuration** the behaviour depends on.

### 4.1 Method and pinned evidence base

**EXTERNAL EVIDENCE — method.** Claims were taken from the **QuantLib source at the `v1.43` release tag**, read directly, because that is the **locked** pin in §3.2 (not a candidate — see §3.2). Source files read (all at tag `v1.43`):

| File | What it establishes |
|---|---|
| `ql/settings.hpp`, `ql/settings.cpp` | `Settings` contents, the `DateProxy` today-fallback, `SavedSettings`, and the optional explicit-evaluation-date behaviour |
| `ql/patterns/singleton.hpp` | The singleton implementation and the official thread-safety sentence |
| `ql/patterns/observable.hpp`, `ql/patterns/observable.cpp` | `Observable`/`Observer`/`ObservableSettings`, both branches |
| `ql/patterns/lazyobject.hpp` | Lazy caching, the `calculate()` ordering, and the `LazyObject::Defaults` singleton |
| `ql/handle.hpp` | `Handle` / `RelinkableHandle` relinking semantics and reference-returning accessors |
| `ql/termstructure.hpp` | Term-structure observer wiring, mutable cached state, and the global-evaluation-date constructor |
| `ql/termstructures/yieldtermstructure.hpp` | Discount/zero/forward access, jump handling, and the `extrapolate` parameters |
| `ql/math/interpolations/extrapolation.hpp` | The non-atomic, non-observable extrapolation switch |
| `ql/termstructures/interpolatedcurve.hpp` | The mutable `times_`/`data_`/`interpolation_` working state a bootstrap writes |
| `ql/indexes/indexmanager.hpp` | The global fixing repository |
| `ql/models/calibrationhelper.hpp` | Calibration-helper mutability |
| `ql/math/optimization/method.hpp`, `…/problem.hpp`, `…/levenbergmarquardt.hpp`, `ql/models/model.hpp` | Optimizer/`Problem`/model statefulness (§4.10) |
| `ql/time/ecb.cpp`, `ql/time/calendars/target.cpp` | A mutable non-singleton global, and the shared calendar-implementation pattern (§4.2.1) — shared state, and **not** immutable |
| `ql/qldefines.hpp` | The Boost floor required by the thread-safe observer pattern |
| `CMakeLists.txt`, `configure.ac`, `ql/userconfig.hpp` | Every relevant compile-time option and its **default**, from three independent declarations |

**EXTERNAL EVIDENCE — corroborating secondary sources.** The reference manual (`config.html`) for the official option descriptions, the vcpkg `ports/quantlib/` recipe for what the pinned port actually passes, and maintainer statements for the official position (§4.12). These are labelled as secondary/advisory where used, and in every case the primary source is cited alongside.

**IMPORTANT SCOPE LIMIT (stated so no reader over-reads this audit).** These are facts about **QuantLib 1.43 as configured by its own default CMake options**, which is what a default vcpkg build of the pinned port produces (the port passes only `-DQL_BUILD_EXAMPLES=OFF -DQL_BUILD_TEST_SUITE=OFF`, per §2.6). They are **not** claims about the `master` branch, about older versions such as the Python-side `1.32` lower bound, or about a build that enables sessions or the thread-safe observer pattern. Where a fact is conditional, the condition is named.

### 4.2 Process-global state inventory

**EXTERNAL EVIDENCE.** QuantLib exposes several process-wide objects. Under the default build configuration they are **one instance per process, shared by all threads**.

| # | Global object | Declared as | Holds | Mutated by |
|---|---|---|---|---|
| G1 | `Settings` | `class Settings : public Singleton<Settings>` (`ql/settings.hpp:37`) | `evaluationDate_`, `includeReferenceDateEvents_`, `includeTodaysCashFlows_`, `enforcesTodaysHistoricFixings_` | client code setting the evaluation date or any flag |
| G2 | `ObservableSettings` | `class ObservableSettings : public Singleton<ObservableSettings>` (`ql/patterns/observable.hpp`) | update-enable/disable/deferred state and a deferred-observer registry | `disableUpdates` / `enableUpdates` |
| G3 | `LazyObject::Defaults` | `class LazyObject::Defaults : public Singleton<LazyObject::Defaults>` (`ql/patterns/lazyobject.hpp:149`) | `forwardsAllNotifications_` | `forwardFirstNotificationOnly()` / `alwaysForwardNotifications()` |
| G4 | `IndexManager` | `class IndexManager : public Singleton<IndexManager>` (`ql/indexes/indexmanager.hpp:38`) | `data_`: `std::map<std::string, TimeSeries<Real>>` of **past fixings**; `notifiers_` | `addFixing`, `addFixings`, `setHistory`, `clearHistory`, `clearHistories` |
| G5 | `ExchangeRateManager` | `Singleton<ExchangeRateManager>` (`ql/currencies/exchangeratemanager.hpp:41`) | `mutable std::map<Key, std::list<Entry>> data_` | exchange-rate registration/clearing |
| G6 | `SeedGenerator` | `Singleton<SeedGenerator>` (`ql/math/randomnumbers/seedgenerator.hpp:38`) | `MersenneTwisterUniformRng rng_` | `get()` mutates it |
| G7 | `Money::Settings` | `Singleton` (`ql/money.hpp:102`) | conversion type, base currency | conversion settings |
| G8 | `IborCoupon::Settings` | `Singleton` (`ql/cashflows/iborcoupon.hpp:111`) | `usingAtParCoupons_` | coupon-convention setting |
| G9 | `Tracing` | `Singleton` (`ql/utilities/tracing.hpp:35`) | global tracing state | tracing enablement |
| G10 | `CommoditySettings`, `UnitOfMeasureConversionManager` | `Singleton` (`ql/experimental/commodities/…`) | commodity pricing configuration | commodity settings |

**Condition that makes them process-global (not thread-local):** the sessions mechanism must be **disabled**, which is the default — see §4.4.

**EXTERNAL EVIDENCE — the `Global` template argument is dead code in-tree, which makes the scope blunt.** An exhaustive search of the audited tree for `Singleton<…, …>` with a second template argument, and for `integral_constant`, found **no in-tree class that passes `Global = true`**. Every singleton the library ships uses the default `Global = false`. **Consequence: enabling `QL_ENABLE_SESSIONS` makes *every* QuantLib singleton per-thread — not a selected subset.** The documented "global across all sessions" capability is real API surface but unused by the library itself.

**EXTERNAL EVIDENCE — at least one genuinely mutable process-global that is *not* a singleton.** `ql/time/ecb.cpp` declares `std::set<Date> ecbKnownDateSet` at namespace scope, exposed through `static void addDate(const Date&)` / `static void removeDate(const Date&)` / `static const std::set<Date>& knownDates()`. `addDate`/`removeDate` mutate it **with no synchronisation**, and `knownDates()` hands out a `const&` to the global. So "look for `Singleton` subclasses" is **not** a complete inventory of process-global mutable state.

#### 4.2.1 What is *positively* safe to share (stated so the audit is not over-read)

**EXTERNAL EVIDENCE.** Being precise about the unsafe parts requires being equally precise about the safe ones, so this audit does **not** conclude that "all QuantLib objects are unsafe":

| Type family | Finding | Classification for §5.3 |
|---|---|---|
| **Calendars** | Realisations share one implementation instance (e.g. `static ext::shared_ptr<Calendar::Impl> impl(new TARGET::Impl);` in `ql/time/calendars/target.cpp:25`), but that implementation is **not immutable**. `Calendar::Impl` holds non-`const` `std::set<Date> addedHolidays, removedHolidays`, and `Calendar` exposes `addHoliday()`, `removeHoliday()` and `resetAddedAndRemovedHolidays()`, which mutate that state through the shared `impl_`. `isBusinessDay()` is declared `const` yet **reads** those sets, so a `const Calendar` can observe another thread's mutation — **through any copy**, because copies share `impl_` (verified directly at the `v1.43` tag, `ql/time/calendar.hpp`). `TARGET` does not consult the ECB registry. **An earlier revision of this document called this pattern "immutable" and is corrected here** | **Conditionally safe — read-only, and only while the mutators are never called** (§5.5 C13). **Not** classifiable as immutable in general |
| **Day counters** | `DayCounter::Impl` is genuinely immutable in itself: it has **no data members** and **every** virtual method is `const` (verified at the `v1.43` tag, `ql/time/daycounter.hpp`; the files originally checked were `actual365fixed.cpp`, `thirty360.cpp`, `actualactual.cpp`). **The claim is not universal, however, and the earlier wording was broader than its evidence**: an `Impl` may **embed a `Calendar`** — `Business252::Impl` holds a `Calendar calendar_` and defaults it to `Brazil()` (`ql/time/daycounters/business252.hpp`, verified at the same tag) — in which case the day counter carries the calendar's shared mutable state and is only as immutable as that calendar | Safe to share **except** an implementation that embeds a `Calendar`, which is subject to C13 |
| **Currencies** | Each constructor assigns a function-local `static auto …Data = ext::make_shared<Data>(…)` shared by all instances of that currency; C++11 guarantees thread-safe initialisation of function-local statics. **Immutable by convention, not by construction**: `Currency::Data`'s members are non-`const` and the struct is shared through `data_`, but `data_` is `protected` and **no public mutator exists**, so there is no supported mutation path (verified at the `v1.43` tag, `ql/currency.hpp`). Subclassing `Currency` would expose `Data` | Safe to read; **not** immutable by construction |
| **`Date`, `Period`, `Time`, `DayCounter` values** | Value semantics, and the shared `impl_` behind `DayCounter` is immutable (see the Day counters row above). **`Calendar` is deliberately excluded from this row** — its value semantics do *not* extend to its shared `impl_` (see the Calendars row above) — and a `DayCounter` whose `Impl` embeds a `Calendar` (e.g. `Business252`) inherits that exclusion | Safe to share (IMMUTABLE, §5.3), subject to C13 for any type that reaches a `Calendar` |
| **`Settings`-reading, `LazyObject`-derived, handle-carrying, bootstrapped, engine and calibration types** | See G1–G10 and §4.5–§4.10 | **UNSAFE / UNPROVEN** by default |

**PROPOSED:** this table matters operationally. It means the adapter may freely use *dates, day counters and period/time value types* (with explicit convention values per §3.6 D2–D5) without a concurrency concern, and the concurrency risk is concentrated in exactly the places §5 targets: settings, fixings, relationships, and cached derivations — **plus calendar mutation**. Calendars are usable read-only under §5 (C13), but they do **not** belong on the unconditional "no concern" list: the earlier claim that calendars are simply immutable was **wrong**, and the rows above record the correction rather than the original claim.

**EXTERNAL EVIDENCE — the official thread-safety sentence.** `ql/patterns/singleton.hpp` states verbatim:

> "Notice that the creation and retrieval of (local or global) singleton instances through `instance()` is thread safe, but obviously subsequent operations on the singleton have to be synchronized within the singleton implementation itself."

**This single sentence is the whole of the guarantee, and it is a guarantee about `instance()` returning a reference — not about any operation performed on the returned object.** §5.1 records the consequence.

### 4.3 `Settings` and the evaluation date

**EXTERNAL EVIDENCE.**

- `Settings::instance().evaluationDate()` returns a `DateProxy&`, where `DateProxy : public ObservableValue<Date>` (`ql/settings.hpp`). Writing it notifies registered observers.
- **A defaulted evaluation date falls back to the system clock.** `Settings::DateProxy::operator Date()` is implemented as: if the stored value is the null `Date()`, return `Date::todaysDate()`, else return the stored value. So "the evaluation date" is, when unset, **the wall clock**, and the header's own documentation says "today's date is returned if the evaluation date is set to the null date (its default value)".
- **Midnight rollover is a silent state change.** The same header warns: "a notification is not sent when the evaluation date changes for natural causes — i.e., a date was not explicitly set (which results in today's date being used for pricing) and the current date changes as the clock strikes midnight."
- `anchorEvaluationDate()` exists to freeze the date and is documented to gain "quite a bit of performance"; `resetEvaluationDate()` re-enables the midnight behaviour.
- The other three flags have **documented non-obvious defaults**: `includeReferenceDateEvents_ = false`, `includeTodaysCashFlows_` is an *unset* `optional<bool>`, `enforcesTodaysHistoricFixings_ = false`.
- **A sanctioned save/restore helper exists — but it is *insufficient* for Shiori's contract** (`ql/settings.hpp`): `class SavedSettings` — "helper class to temporarily and safely change the settings" — whose constructor/destructor save and restore the evaluation date and all three flags. It is an RAII tool; it contains **no synchronisation of its own**. **It also cannot represent the raw-unset state:** `DateProxy` publicly inherits `ObservableValue<Date>`, and `DateProxy::operator Date()` converts a raw null `Date()` to `Date::todaysDate()`; `SavedSettings` stores `Settings::instance().evaluationDate()` **through that conversion**, so an initially-unset raw-null state is captured — and later restored — as a **concrete date**. `Settings::resetEvaluationDate()` is the member that explicitly restores the raw-null / clock-following state. Therefore **`SavedSettings` alone cannot satisfy the exact-restoration contract §5.5 locks**, and Shiori's adapter guard — not upstream `SavedSettings` — must provide it.
- **EXTERNAL EVIDENCE — an upstream option that would turn the silent fallback into an error.** A later development tree contains `QL_REQUIRE_EXPLICIT_EVALUATION_DATE`, under which an unset evaluation date **throws** rather than silently returning today's date (`QL_FAIL("the evaluation date has not been set; with QL_REQUIRE_EXPLICIT_EVALUATION_DATE defined it must be set explicitly through Settings::instance().evaluationDate() before it is used")`). **It was not verified as present in the pinned release (§4.13 U-I2)**, so this document records it as a **candidate hardening for §5.5 C1** and does not depend on it. Note also that `SavedSettings`' destructor restores inside a `catch (...) { /* nothing we can do except bailing out. */ }`, i.e. a failure to restore is **swallowed** — which is an independent reason C2 requires the guard to be structured so that restoration is not the only thing standing between a request and a polluted global.

**Why this matters to Shiori (bridging statement, not a policy):** G1 is exactly the "system clock" and "implicit current evaluation date" hazard that §7 forbids as a cache identity, and it is the mechanism behind the #225 §16 rule-12 prohibition on hidden dependence. Both are unavoidable unless G1 is controlled explicitly.

### 4.4 The sessions mechanism (`QL_ENABLE_SESSIONS`)

**EXTERNAL EVIDENCE.** This is the only mechanism that changes the *scope* of the globals in §4.2.

- `CMakeLists.txt` declares: `option(QL_ENABLE_SESSIONS "Singletons return different instances for different sessions" OFF)` — **the default is OFF.**
- `ql/patterns/singleton.hpp` is templated as `template <class T, class Global = std::integral_constant<bool, false>> class Singleton`, and its two implementations differ completely:

```cpp
#ifdef QL_ENABLE_SESSIONS
    template <class T, class Global>
    T& Singleton<T, Global>::instance() {
        if (Global()) { static T global_instance; return global_instance; }
        else          { thread_local static T local_instance; return local_instance; }
    }
#else
    template <class T, class Global>
    T& Singleton<T, Global>::instance() { static T instance; return instance; }
#endif
```

- The header documents the template parameter: "Global can be used to distinguish Singletons that are local to a session (Global = false) or that are global across all sessions. This is only relevant if `QL_ENABLE_SESSIONS` is enabled."
- **"Session" in `Singleton<T, false>` means `thread_local`.** With sessions enabled, the `Global = false` singletons — which includes `Settings`, `ObservableSettings`, `LazyObject::Defaults`, and `IndexManager`, since all four use the default — become **per-thread instances**, i.e. a per-thread evaluation date, a per-thread fixing store, and so on.
- With sessions **enabled**, `CMakeLists.txt` also adds a `Threads` dependency and applies a documented workaround: with GCC below 8.4, `Singleton::instance()` "is always compiled with `-O0`" to dodge GCC bug 91757 — a build-configuration-dependent performance consequence.

**EXTERNAL EVIDENCE — what the proposed build actually gets.** The vcpkg `quantlib` port configures with only `-DQL_BUILD_EXAMPLES=OFF -DQL_BUILD_TEST_SUITE=OFF` (§2.6), so it does **not** pass `-DQL_ENABLE_SESSIONS=ON`; the default `OFF` therefore applies. **Consequence: on the proposed build, the §4.2 globals are process-global, and the evaluation date is one process-wide value.**

**UNPROVEN / open:** whether enabling sessions would be *safe or sufficient* for Shiori's concurrent pricing. It changes scope, but it does not by itself address the unsynchronised observer registry (§4.5), the unsynchronised lazy caches (§4.6), the global fixing store being per-thread rather than per-request, or the ODR hazard below. Nothing in this document asserts that enabling sessions solves concurrency.

**EXTERNAL EVIDENCE — an ODR/ABI hazard that follows from the macro being a compile-time switch.** `QL_ENABLE_SESSIONS` and `QL_ENABLE_THREAD_SAFE_OBSERVER_PATTERN` change **class layouts** (e.g. the thread-safe `Observer` adds a `Proxy`, mutexes and `enable_shared_from_this`; the non-thread-safe `Observer` is a bare `std::set` holder). They are macros expanded in the **headers**. Therefore a translation unit compiled with a different value of these macros than the one used to build the linked QuantLib library would violate the One Definition Rule. **PROPOSED build rule (derived from this evidence):** the values of these macros are fixed by the *pinned dependency port*, are **never** set by Shiori's own CMake, and are **verified** to match the linked library (e.g. by a compile-time/`config.hpp` check or a link-time assertion) rather than assumed.

### 4.5 `Observer` / `Observable` and the thread-safe observer pattern

**EXTERNAL EVIDENCE.** `ql/patterns/observable.hpp` contains two mutually exclusive implementations selected by `#ifndef QL_ENABLE_THREAD_SAFE_OBSERVER_PATTERN`.

`CMakeLists.txt` declares: `option(QL_ENABLE_THREAD_SAFE_OBSERVER_PATTERN "Enable the thread-safe observer pattern" OFF)` — **default OFF**, and it is not enabled by the vcpkg port.

**Default branch (in effect for the proposed build):**

| Aspect | Behaviour |
|---|---|
| Registry | `Observable::observers_` is a plain `std::set<Observer*>` — **no mutex** |
| Notification | `notifyObservers()` iterates that set; the inline code in this header shows **no locking** around iteration |
| Registration | `registerObserver` / `unregisterObserver` perform bare `set::insert` / `set::erase` |
| Global settings | `ObservableSettings` stores **plain `bool`** members (`updatesEnabled_`, `updatesDeferred_`, `runningDeferredUpdates_`) in a process-global singleton, with **no mutex** |
| Deferred registry | `std::map<Observer*, bool> deferredObservers_` — **no mutex** |
| Raw pointers | `Observer*` is used as the *key type*, so observers must outlive their observables; teardown order is a lifetime hazard |

**Thread-safe branch (NOT in effect by default):** `Observer` gains a `Proxy` holding a `std::recursive_mutex`; `Observable` gains a `mutable std::recursive_mutex mutex_` and a `detail::Signal` (a `boost::signals2` signal parameterised with a `std::recursive_mutex`); `ObservableSettings` uses `std::atomic<int> updatesType_` plus a `std::mutex`; `Observer` additionally derives from `ext::enable_shared_from_this<Observer>` and the `Proxy` uses `weak_from_this()` to detect a destroyed observer.

**EXTERNAL EVIDENCE — what that switch is actually *for*, and what it is not.** The official reference manual documents `QL_ENABLE_THREAD_SAFE_OBSERVER_PATTERN` as being for consumers that need the observer pattern to cooperate with an **asynchronous garbage collector** — i.e. the SWIG-mediated JVM and .NET ecosystems — and it is "Undefined by default". It is **not** documented as making QuantLib thread-safe. This is corroborated by a long-time QuantLib committer on the issue tracker: *"`--enable-thread-safe-observer-pattern` doesn't actually make the whole library thread-safe - only the observer pattern - and is mainly designed to be compatible with the garbage collector… you'll probably need to use locks to ensure that only one thread accesses the library at a time."* Enabling it also **hard-requires Boost ≥ 1.58** — `ql/qldefines.hpp` contains `#error Boost version 1.58 or higher is required for the thread-safe observer pattern`.

**PROPOSED (important scoping correction):** the thread-safe branch changes the **observer pattern only**. Under no configuration audited here does it make `Settings`, `IndexManager`, `Handle`, `LazyObject` or a term structure safe to share. A design that relies on this switch as its concurrency story is relying on something the switch does not provide.

**EXTERNAL EVIDENCE — and it is not a "thread affinity" rule.** It is tempting to summarise the default behaviour as "an observer must be notified on the thread that created it". **That requirement is not documented, and this audit found no such statement.** The accurate finding is narrower and different in kind: notification is **unsynchronised**, so cross-thread notification is a **data race** on `observers_`, on the observer's own `observables_`, and on `ObservableSettings`' deferral map — not a rule violation that a misuse-detector would catch. §5 therefore treats it as a race to be excluded, not a convention to be followed.

**EXTERNAL EVIDENCE — a behaviour difference that is a methodology hazard, not merely a performance one.** A runtime switch exists for the lazy-object notification policy — `LazyObject::Defaults` (§4.6) — and the two observer branches are **not** functionally identical: the thread-safe branch uses a `Proxy` + weak-reference scheme, and the default branch's `Observable::operator=` sends a notification *before* the copy has changed its members (the header warns about exactly this ordering). **PROPOSED (reinforcing §3.6 D17):** the observer pattern configuration is treated as an **engine dependency property** that must be *recorded* in **Lane B** runtime/build telemetry and benchmark metadata (§10.5 **M4**) — **never** in a result-DTO field — and never toggled opportunistically, because toggling it changes both thread-behaviour and notification ordering.

### 4.6 `LazyObject` — cached results and the global default switch

**EXTERNAL EVIDENCE.** From `ql/patterns/lazyobject.hpp`:

- `class LazyObject : public virtual Observable, public virtual Observer` — so a lazy object is *both* a notification source and a notification sink.
- Cached/derived state is **plain mutable members**: `mutable bool calculated_ = false, frozen_ = false, failed_ = false, alwaysForward_;` plus `bool updating_ = false;`. **There is no mutex and no atomic in this header.**
- `void calculate() const` is the read path for derived values: it tests and **writes** `calculated_`, calls `performCalculations()`, and writes `failed_` on the failure path. It is therefore a **mutating read**.
- `recalculate()` / `freeze()` / `unfreeze()` / `setCalculated(bool) const` all mutate the same fields; `freeze()` in particular means "return the presently cached results on successive invocations, even if arguments upon which they depend should change" — a **stale-by-construction** mode.
- **A global runtime switch exists:** `LazyObject::Defaults` is a `Singleton<LazyObject::Defaults>` ("Per-session settings for the LazyObject class") holding `forwardsAllNotifications_`, whose compile-time default is governed by `QL_FASTER_LAZY_OBJECTS` (declared **ON** in `CMakeLists.txt`). The header states the run-time change "won't affect lazy objects already created", so the effective behaviour of a lazy object depends on **global state at the moment it was constructed**.
- `update()` is guarded against recursion only by the non-atomic `updating_` flag with an `UpdateChecker` RAII, and it **silently returns** on a detected cycle unless `QL_THROW_IN_CYCLES` is enabled — which `CMakeLists.txt` declares **OFF** by default, i.e. a notification cycle is silently swallowed rather than reported.

**EXTERNAL EVIDENCE — the precise mechanism that makes a "read" unsafe.** `LazyObject::calculate()` is:

```cpp
inline void LazyObject::calculate() const {
    if (!calculated_ && !frozen_) {
        calculated_ = true;   // prevent infinite recursion in case of bootstrapping
        try { performCalculations(); failed_ = false; }
        catch (...) { calculated_ = false; failed_ = true; throw; }
    }
}
```

The flag is set **before** `performCalculations()` runs. A second thread that reads `calculated_ == true` in that window therefore **skips the calculation and reads a partially-built object**. There is no lock in this header in **any** build configuration — this is not a sessions or observer-pattern issue, and enabling either switch does not fix it. It is also the concrete mechanism behind the lead maintainer's warning that *"not triggering full construction before calculations might lead to two instruments triggering two simultaneous bootstraps, which wouldn't end well for any concerned parties"* (§4.12).

**EXTERNAL EVIDENCE — the library's own sanctioned workaround.** QuantLib's own parallel pricing engine states the rule in a source comment: *"a lazy object is not thread safe, neither is the caching in gsrprocess. therefore we trigger computations here such that neither lazy object recalculation nor write access during caching occurs in the paralized loop below."* (`ql/pricingengines/swaption/gaussian1dswaptionengine.cpp:107`). The idiom is: **force every lazy recalculation single-threaded first, then enter the parallel region.**

**PROPOSED (derived from the above):** this becomes a hard rule of the adapter — *all* lazy recalculation for a request is forced to completion **inside the serialization gate**, before any result is read, and no QuantLib object is ever handed outside the gate in a "calculated on first access" state. This is stricter than "don't share across threads" and is what makes the single-threaded posture actually sufficient rather than merely cautious.

**Consequence for §5:** a `LazyObject`-derived QuantLib object (and almost every curve, model, instrument and calibration helper is one) is **not safe for concurrent read-only access in the default configuration**, because the "read" method mutates shared mutable state — and can be observed mid-construction. This is why §5.3 classifies QuantLib objects as **UNSAFE / UNPROVEN by default** rather than "safe if you only read them".

### 4.7 `Handle` / `RelinkableHandle`

**EXTERNAL EVIDENCE.** From `ql/handle.hpp`:

- `Handle<T>` holds `ext::shared_ptr<Link>`, where `class Link : public Observable, public Observer`. All copies of a handle share one link, so **relinking propagates to every copy**.
- `Link::linkTo(...)` unregisters (if it was an observer), reassigns the pointer, re-registers, and then calls `notifyObservers()` — all with **no lock** in the default build (§4.5).
- `RelinkableHandle<T>::linkTo()` and `reset()` expose this mutation publicly.
- The header carries explicit lifetime warnings: `registerAsObserver` "should be set to `false` when the passed shared pointer does not own the pointee … Failure to do so can very likely result in a program crash."
- Dereferencing an empty handle throws (`QL_REQUIRE(!empty(), "empty Handle cannot be dereferenced")`).

**EXTERNAL EVIDENCE — a second hazard that survives even single-threaded use.** The handle accessors return a **reference into the shared link**, not a copy: `const ext::shared_ptr<T>& currentLink() const`, `const ext::shared_ptr<T>& operator->() const`, `const ext::shared_ptr<T>& operator*() const`. A caller that holds such a `const&` is holding a reference to a `shared_ptr` that another thread's `linkTo()` can reassign underneath it — and, independently of threads, holding that reference while the link is relinked is unsafe if the referenced `shared_ptr` was the pointee's only keeper. So the aliasing is not purely a concurrency problem.

**Consequence:** a relinkable handle is a **mutation channel into an otherwise shared object**. "Shared read-only" is therefore not a property that can be inferred from a type; it must be established by construction and enforced by the adapter (§5.3). **PROPOSED:** the adapter never retains a handle-derived reference across a statement boundary — it dereferences to a value (or to a request-local object) immediately, inside the gate.

### 4.8 `TermStructure` / `YieldTermStructure`

**EXTERNAL EVIDENCE.** From `ql/termstructure.hpp` and `ql/termstructures/yieldtermstructure.hpp`:

- `class TermStructure : public virtual Observer, public virtual Observable, public Extrapolator` — it **is** an observer, and it includes `<ql/settings.hpp>`.
- The constructor `TermStructure(Natural settlementDays, Calendar, DayCounter)` is documented as: "calculate the reference date based on the **global evaluation date**", and the class documentation states that in that mode "the term structure and its observers will be **notified when the evaluation date changes**." **A moving term structure therefore reads process-global state (G1) at query time.**
- Cached state is mutable and unsynchronised: `mutable bool updated_ = true;`, `mutable Date referenceDate_;`, `bool moving_`.
- `Extrapolator` is inherited, i.e. the extrapolation policy is an **object-level switch**, and `checkRange(d, extrapolate)` enforces it at access time.
- **The public API defaults extrapolation to `false`** — `discount(Date, bool extrapolate = false)`, `zeroRate(..., bool extrapolate = false)`, `forwardRate(..., bool extrapolate = false)` — while `discountImpl` is documented to "assume that extrapolation is required" once the range check has passed. Two different defaults in one call chain is exactly the kind of detail that must not be left implicit (§3.6 D9).
- **The extrapolation switch is unsynchronised, object-level shared state.** `Extrapolator` (in `ql/math/interpolations/extrapolation.hpp`) holds a plain `bool extrapolate_ = false` exposed through `enableExtrapolation(bool b = true)`, `disableExtrapolation(bool b = true)` and `allowsExtrapolation()`. It is **not** `std::atomic`, it takes **no lock**, and — importantly — **`Extrapolator` is not an `Observable`**, so flipping it notifies nobody. Because `TermStructure` inherits it publicly, the flag is mutable state shared by every user of that curve object. **PROPOSED:** the extrapolation policy is fixed at construction as part of the curve's identity and is never toggled on a live curve (§7.4 L3 must key on it if it can differ).
- **Interpolated curves carry mutable working state that the bootstrap writes.** `InterpolatedCurve<Interpolator>` — the base of every interpolated curve — holds `mutable std::vector<Time> times_; mutable std::vector<Real> data_; mutable Interpolation interpolation_;`, and a bootstrapped curve is a `LazyObject` whose `calculate()` **writes** them. So the "curve" a caller holds is only immutable *after* the bootstrap has completed (§4.6).
- `YieldTermStructure` adds jump handling (`jumps_`, `jumpDates_`, `jumpTimes_`, `nJumps_`, `latestReference_`) configured through `Handle<Quote>` objects, and `void update() override` — the latter a mutating override.
- **The only endorsed sharing discipline is a three-phase one, and it is maintainer advice, not a documented contract** (§4.12): set all quotes and stop changing them; force full construction single-threaded; *then* treat the curve as read-only. This audit found **no** formal documented contract for concurrent curve access (**PARTIAL**), and §5 does not depend on such a contract existing.

### 4.9 `IndexManager` — global fixing history

**EXTERNAL EVIDENCE.** From `ql/indexes/indexmanager.hpp`:

- Declared as `class IndexManager : public Singleton<IndexManager>`, documented as the "global repository for past index fixings"; index names are **case-insensitive**; the fixings store is `mutable std::map<std::string, TimeSeries<Real>> data_` plus `mutable std::map<std::string, ext::shared_ptr<Observable>> notifiers_`.
- Mutating entry points: `addFixing`, `addFixings`, `setHistory`, `clearHistory`, `clearHistories`. `addFixings` writes into the map and then calls `notifyObservers()` on the index notifier.
- The store is a `Singleton` with the default `Global = false`, so it is **process-global when sessions are off** and **per-thread when sessions are on** — in neither case is it **per-request** or per-`MarketSnapshot`.

**Consequence — directly relevant to an approved contract.** #225 makes fixings an **explicit input** (`FixingStore`, `docs/33...` §8, carried on `MarketSnapshot`). If the C++ adapter populated or read the QuantLib **global** fixing repository to obtain fixings, the engine's inputs would come from mutable process state that no `RatesKernelInput` names — which is precisely the #225 §16 rule-12 violation and would also breach §7's cache-identity requirement. **PROPOSED (§5.5, §7.4):** fixings are injected request-locally from the DTO `FixingStore`, and the global `IndexManager` is not used as an input channel.

### 4.10 Calibration objects

**EXTERNAL EVIDENCE.** From `ql/models/calibrationhelper.hpp`:

- `class BlackCalibrationHelper : public LazyObject, public CalibrationHelper` — it inherits the unsynchronised lazy cache of §4.6 **and** the observer machinery of §4.5.
- `performCalculations() const override` writes `mutable Real marketValue_;` — so `marketValue() const` (which calls `calculate()`) is a **mutating read**.
- `void setPricingEngine(const ext::shared_ptr<PricingEngine>& engine)` is a **public non-const mutator** that replaces the helper's `engine_`; a shared helper can therefore have its engine swapped underneath another thread.
- `Handle<Quote> volatility() const { return volatility_; }` returns a **handle**, i.e. aliased mutable state, and the constructor registers the helper as an observer of it.
- `calibrationErrorType_` is `const`, as are `volatilityType_` and `shift_`; `CalibrationError` types include `RelativePriceError`, `PriceError`, `ImpliedVolError`.

**Consequence:** even a helper that is only *read* is not safe to share across threads, and "read-only calibration objects" is not an available assumption.

**EXTERNAL EVIDENCE — the optimizer side is stateful too.** The helper is only half of a calibration; the solver and the model carry working state as well:

- `OptimizationMethod::minimize(Problem& P, const EndCriteria& endCriteria)` takes the `Problem` by **non-const reference** and returns `EndCriteria::Type` — the call mutates its argument.
- `Problem` holds the cost function by reference and carries mutable members (`functionEvaluation`, `gradientEvaluation`, `value`, `squaredNorm`, `currentIteration`, …), so a `Problem` is a per-calibration work object, not a reusable immutable input.
- Concrete minimizers such as `LevenbergMarquardt` keep **instance state** across `minimize` calls (lambda/parameter bookkeeping), so a shared optimizer instance is a shared mutable object.
- `CalibratedModel::calibrate(...)` is likewise a **non-const** operation: it mutates the model's parameter vector and notifies observers, which is also the path by which a curve object becomes mutable (see §4.8).
- No synchronisation was found in these types outside the optional observer-pattern build, i.e. the same condition as §4.12.

**Consequence for §8:** a calibration is a *transaction over mutable library objects* (helpers, `Problem`, minimizer, model). This is the structural reason the calibration cache is **keyed but not productive** until the RED-owned objective/optimizer/tolerance policies have identities (§8.4), and it is why "reuse a warm optimizer" is not an available optimisation — reuse would carry one calibration's mutable work state into another's.

### 4.11 QuantLib's own internal parallelism

**EXTERNAL EVIDENCE — a commonly repeated premise is REFUTED.** `GlobalThreadPool`, `ThreadPool` and `GaussianPathGenerator` **do not exist in QuantLib**. A whole-tree search for `SessionSettings|GlobalThreadPool|ThreadPool|threadPool|thread_pool` returns **zero matches**, there is no thread-pool header in `ql/patterns/` or `ql/utilities/`, and probing `ql/patterns/threadpool.hpp` and `ql/utilities/threadpool.hpp` across a series of historical tags returns HTTP 404 for every combination. **Any design, README or secondary summary that assumes QuantLib exposes a global thread pool — or that pricing can be parallelised by configuring one — rests on a false premise.** This is recorded as a **refutation**, not as an UNPROVEN item, so that #227 does not inherit it.

**EXTERNAL EVIDENCE — what concurrency the library actually ships:**

| Facility | State | Scope |
|---|---|---|
| **OpenMP pragmas** | Four active `#pragma omp parallel for` sites (`ql/methods/lattices/lattice.hpp:173`, `ql/methods/finitedifferences/operators/triplebandlinearop.cpp:199`, `ql/pricingengines/swaption/gaussian1dswaptionengine.cpp:136`, `ql/termstructures/volatility/zabrsmilesection.hpp:217`) | **Intra-calculation** parallelism inside a single call — never a general worker pool |
| `QL_ENABLE_OPENMP` | `option(… "Detect and use OpenMP" OFF)` | Governs **detection and link flags only**; it does **not** gate the pragmas |
| `QL_ENABLE_PARALLEL_UNIT_TEST_RUNNER` | OFF by default | Parallelises the **test suite**, not pricing |
| Experimental latent/credit-model threading | User-driven mode, not a pool | Carries explicit restrictions: a "multithread version" requires a pre-initialised sequence generator, and the Box–Muller **rejection** variant and `PolarT` are documented as *"do not use … within a multithreaded simulation"* |

**EXTERNAL EVIDENCE — a precise and non-obvious caveat about the OpenMP pragmas.** Only the swaption engine's loop is wrapped in `#ifdef _OPENMP` (around lines 113–134). The other three sites are **bare** `#pragma omp`, which a non-OpenMP compiler silently ignores with a warning rather than a build failure. Consequently the option and the pragmas are **decoupled**: a build can have the pragmas' effects vary by compiler while the option reports a single value.

**EXTERNAL EVIDENCE — how QuantLib itself chose to parallelise its benchmark.** The library's own benchmark harness states: *"The overall benchmark is parallelised using Boost::IPC. QuantLib is not thread safe, so any kind of shared memory paralellism is ruled out."* (`test-suite/quantlibbenchmark.cpp:105`). **The upstream maintainers' own solution to parallel benchmarking was multi-process** — which is the posture §5.2 proposes for Shiori, and therefore external corroboration rather than an independent invention.

**PROPOSED consequences:**

| # | Rule |
|---|---|
| O1 | Shiori does **not** enable `QL_ENABLE_OPENMP` in the pinned build |
| O2 | Shiori does **not** rely on intra-calculation parallelism for speed; if it were ever enabled, the four sites above are the only paths affected, and each would need its own correctness review |
| O3 | No design may reference a QuantLib thread pool, because none exists |
| O4 | The parallel unit-test runner is a **test-harness** option and is never conflated with production concurrency |
| O5 | **Verifying the absence of library-internal parallelism is a build obligation, not an inference from O1.** O1 is necessary but **not sufficient**: the option governs detection and link flags only, while three of the four pragma sites are **bare** (caveat above). The build must therefore **also** verify that **`_OPENMP` is undefined** for the QuantLib translation units, and **fail closed** if any OpenMP flag (`-fopenmp`, `-openmp`, `/openmp`) reaches them. A triplet, toolchain, or environment that injects such flags independently would otherwise activate those loops *while the option still reports "off"* — silently breaking §5.2's single-threaded posture with no configuration value to point at |
| O6 | O5 is mechanically checkable and must be checked, not asserted: the §2.4 CMake contract inspects the QuantLib compile line for OpenMP flags, a build-time check rejects a QuantLib target compiled with `_OPENMP` defined, and §11.6's CI asserts the same property on the produced binary |

**UNPROVEN (narrow, and independent of the above):** whether any *individual pricing engine* that Shiori later calls spawns threads of its own. Shared infrastructure has been audited; a per-engine audit was **not** performed, and the safe default is "not safe unless shown otherwise" (**U-D**).

### 4.12 The official thread-safety position

**EXTERNAL EVIDENCE.** Four independent authoritative statements establish the position. They are quoted verbatim because the *exact* wording is the evidence; two are first-party code/doc statements and two are maintainer statements (advisory, not a formal specification — classified as such).

**(a) First-party, in the library's own merged source** (`test-suite/quantlibbenchmark.cpp:105`, the benchmark harness):

> "The overall benchmark is parallelised using Boost::IPC. **QuantLib is not thread safe**, so any kind of shared memory paralellism is ruled out."

**(b) Lead maintainer (Luigi Ballabio), accepted answer to "What is the right way to use QuantLib from multiple threads?"** — the most complete statement located:

> "There are almost no locks or safety nets in QuantLib, so most people use **multiprocessing** if they need to distribute calculations over processors---and with good reason."
> "you might have multiple threads running at once if they don't share any objects, **and** if they don't try to modify globals such as the evaluation date (look for classes inheriting from `Singleton` for the list of globals)."
> "… you can use another compilation switch to build QuantLib so that the singletons are not actually singletons, but there's an instance per thread. **Caveat: this switch is not compatible with thread-safe initialization of singletons. You still shouldn't share objects between threads.**"
> "if you want to share objects, you might be in for more trouble than it's worth. … not triggering full construction before calculations might lead to **two instruments triggering two simultaneous bootstraps**, which wouldn't end well for any concerned parties."
> "As I said, it's probably more trouble than it's worth. **Ideally, don't share objects between threads and don't touch the globals. Otherwise, prefer multiprocessing.**"

**(c) A long-time QuantLib committer, on the issue tracker** (about the observer-pattern switch):

> "`--enable-thread-safe-observer-pattern` doesn't actually make the whole library thread-safe - only the observer pattern - and is mainly designed to be compatible with the garbage collector. … you'll probably need to use **locks to ensure that only one thread accesses the library at a time**."

**(d) The header's own scope-limiting disclaimer** (`ql/patterns/singleton.hpp`), already quoted in §4.2: `instance()` *creation and retrieval* are thread-safe; "subsequent operations on the singleton have to be synchronized within the singleton implementation itself."

**EXTERNAL EVIDENCE — a documentation gap that explains why vague claims circulate.** The official QuantLib FAQ contains **no thread-safety entry at all** (its entries concern help channels, contributing, build problems, Excel/add-in tooling, missing features, language bindings and FpML/serialization). The README does not mention thread safety either. The most formal available statement is the reference-manual description of `QL_ENABLE_SESSIONS`, which is **descriptive rather than advisory**. **Therefore the relative scarcity of a formal contract — not any endorsement — is why unqualified "QuantLib is thread-safe" claims persist.**

**What this means for #226, stated plainly.** The officially blessed multi-threaded configuration requires **all** of: no shared objects, no global mutation, and optionally per-thread singletons. Even then the lead maintainer calls sharing "probably more trouble than it's worth" and recommends **multiprocessing**. §5.2's posture — single-threaded serialized execution per process, scaling by multiple processes — is therefore **the conservative reading of the official position**, and its provenance is external (statements (a)–(c)), not invented here.

**PROPOSED — community claims rejected as engineering inputs.**

| Community claim | Status | Why it is rejected |
|---|---|---|
| "QuantLib is thread-safe" | **REJECTED — contradicted by first-party source** | Statement (a) says the opposite in the library's own code; (b) says "almost no locks or safety nets"; under defaults `ObservableSettings`, the observer registry, `LazyObject` caches, `Settings` flags and `IndexManager` are all unsynchronised (§4.2, §4.5, §4.6) |
| "QuantLib `Settings` is thread-local, so concurrent pricing is fine" | **REJECTED as unqualified** | Thread-local **only if** `QL_ENABLE_SESSIONS` is `ON`; that option is `OFF` by default and is **not** enabled by the pinned port (§4.4). The value must also be consistent between Shiori's TUs and the linked library, so it cannot be inferred from a version number. It addresses none of §4.5–§4.9 |
| "Enable `QL_ENABLE_THREAD_SAFE_OBSERVER_PATTERN` and QuantLib becomes thread-safe" | **REJECTED — contradicted by a committer statement** | Statement (c): it makes *the observer pattern* thread-safe only, and exists for async-GC integration. It leaves `LazyObject`, `Handle`, `Settings` and `IndexManager` untouched |
| "Just use QuantLib's thread pool" | **REJECTED — the class does not exist** | §4.11: no `GlobalThreadPool`/`ThreadPool` in the library |
| "Implement `QuantLib::sessionId()` to get per-thread state" | **REJECTED — describes QuantLib ≤ 1.28** | §4.14: `sessionId()` was removed when the singleton was simplified; modern QuantLib uses `thread_local` and needs no user-supplied function |

### 4.13 What remains UNPROVEN (no claim is made)

| # | UNPROVEN item | Evidence required |
|---|---|---|
| U-A | Whether any specific concurrency configuration is safe for Shiori pricing | The §5.7 concurrency-correctness tests, run under TSan and/or a deterministic harness |
| U-B | Whether enabling `QL_ENABLE_SESSIONS` would improve or merely relocate the hazards | A dedicated investigation; explicitly **not** performed or authorised here |
| U-C | Whether the singleton inventory (G1–G10) and the non-singleton globals are exhaustive for the **pinned** version | A systematic re-derivation from the pinned source tree in #227. The inventory here was audited on the `v1.43` tree and on a later development tree |
| U-D | Whether any individual pricing engine Shiori calls spawns threads of its own | A per-engine audit — the shared infrastructure does not establish it |
| U-E | Whether `QL_THROW_IN_CYCLES` should be enabled (it would convert a silently-swallowed notification cycle into an exception) | A decision with behaviour consequences; recorded, not taken |
| U-F | Whether the thread-safe observer pattern is ABI-compatible with anything already linked | Verified at configure time in #227, if ever considered |
| U-G | Whether the Python `QuantLib>=1.32` binding and the C++ `1.43` pin **share library/global instances when both are loaded in one process**, for each supported linkage | **An earlier revision recorded this as "not required by this architecture … therefore not investigated", which was wrong.** §2.7 P6 makes the two **dependency planes** independent, but independence of versions does not keep their in-process **state** separate. The gap it leaves is load-bearing for gate completeness and is now a requirement with three permitted resolutions (§5.2.1). **Unproven for every linkage**; #227 must establish one resolution per supported configuration and record the evidence |
| U-H2 | The exact macro configuration of any **pre-built** QuantLib binary used instead of the pinned port | Source-level defaults are established for the pinned version; a packager's flags are not. §5.5 C10's verification requirement exists so this is checked rather than assumed |
| U-I2 | Whether `QL_REQUIRE_EXPLICIT_EVALUATION_DATE` (an upstream option that makes an *unset* evaluation date **throw** instead of silently using today's date) is present in the pinned release | Confirmed present on a later development tree and **not** verified for `v1.43`. If available it is a **strong candidate hardening** for §5.5 C1; recorded as a candidate, and this document does **not** depend on it |
| U-J2 | Any maintainer discussion in the mailing-list archives | Not audited; §4.12's statements cover the same ground more authoritatively, but the archives were not searched |
| U-K2 | Whether the ≤1.28 `sessionId()` design and the ≥1.29 `thread_local` design differ beyond thread-locality (instance lifetime, destruction order) | Not enumerated; irrelevant to the pin, but relevant whenever "sessions" is cited from older literature |

### 4.14 Contradiction check

**EXTERNAL EVIDENCE — the audited code is internally consistent.** The CMake options match the `#ifdef` branches that consume them, and the doc comments match the implementations. Everything §3 and §5 rely on comes from a single mutually consistent base: **the pinned configuration leaves the process-global state of §4.2 unsynchronised, and the library's own statements (§4.12) agree.**

**EXTERNAL EVIDENCE — but the *literature* around QuantLib contradicts the current source in three specific, load-bearing ways.** These matter because they are exactly the kind of stale claim that would otherwise enter an architecture document unexamined:

| # | Apparent contradiction | Resolution |
|---|---|---|
| X-1 | Widely circulated documentation states that `QL_ENABLE_SESSIONS` requires the user to **implement and link `QuantLib::sessionId()`**, and that `QL_ENABLE_SINGLETON_THREAD_SAFE_INIT` exists | **Superseded.** `sessionId()` and that macro were removed when the singleton was simplified (commit `738c9677be`, 2022-05-10), first shipping in **1.29**. Modern QuantLib uses `thread_local` and requires **no** user-supplied function. Any source describing `sessionId()` describes **≤ 1.28** |
| X-2 | The header documents a `Global = true` mode for "global across all sessions", implying per-session and global singletons coexist | **Documented capability, unused in-tree.** No in-tree class passes `Global = true` (verified across the pinned tree and an earlier release), so enabling sessions makes **every** singleton per-thread. The documented distinction is real API surface but **dead code** as far as the library is concerned |
| X-3 | `ObservableSettings` is commented as a "**global** repository for run-time library settings" | **Copy-paste inaccuracy.** It is a `Singleton` with the default `Global = false`, so under sessions its update-deferral flags and deferred-observer registry become **per-thread** — i.e. `disableUpdates(true)` on one thread would not defer updates on another. The word "global" and the behaviour under sessions disagree. Recorded as a **latent design inconsistency**, not a proven bug, so #227 does not rely on the comment |

**One deliberate non-claim.** Where a widely repeated statement is broader than the source supports (§4.12's rejected claims), this document records the **narrow, sourced** version and leaves the broad version unproven rather than adopting it. That is a scoping decision, not a contradiction.

**No contradiction was found with #223, #224 or #225.** The audit produced no finding that requires reinterpreting an approved contract; specifically, nothing here changes the DTO boundary, the identity/replay rules, or the resolved V1 convention decisions. §13.3 records the escalation condition that would apply if that changed.

---

## 5. Thread-safety and concurrency model

### 5.1 What the evidence forces

**PROPOSED — reasoning, stated explicitly so it can be challenged.**

Given §4, the proposed build has all of the following simultaneously true:

1. **Ten** process-global singletons (G1–G10 in §4.2 — `Settings`, `ObservableSettings`, `LazyObject::Defaults`, `IndexManager`, `ExchangeRateManager`, `SeedGenerator`, `Money::Settings`, `IborCoupon::Settings`, `Tracing`, and the commodity settings) **plus** at least one mutable process-global that is not a singleton (the ECB known-date registry) — §4.2.
2. An **unsynchronised** observer registry and unsynchronised global update flags — §4.5.
3. **Unsynchronised mutable caches** on `LazyObject`, and therefore on curves, models, instruments and calibration helpers, where even the *read* path writes state **and can be observed mid-construction** — §4.6, §4.10.
4. A **public relink channel** into shared objects — §4.7.
5. Term structures that read the **global evaluation date at query time** — §4.8.
6. A **global fixing history** whose scope is process-wide (or at best per-thread) but never per-request — §4.9.
7. The library's own guarantee covering **only** `Singleton::instance()` retrieval — §4.12.
8. **No synchronisation at all** outside the optional observer-pattern build: outside that `#ifdef`, `Settings`, `IndexManager`, `Handle`, `LazyObject`, term structures and the optimizers contain no locks — §4.5, §4.10, §4.12.

The conclusion is not "QuantLib is unusable"; it is narrower and firmer: **the default, pinned configuration offers no compiled-in guarantee that two threads may concurrently price with shared QuantLib objects, and several mechanisms that are demonstrably racy if they do.** Therefore Shiori must not begin from a parallel posture and then look for races.

### 5.2 Initial production concurrency posture

**PROPOSED — this is the concurrency contract.**

> **Production parallel pricing is DISABLED.** The engine's initial posture is **single-threaded serialized execution per process**. Concurrency is obtained by running **multiple independent single-threaded processes**, not by threads inside one process. **Correctness dominates performance.**

**EXTERNAL EVIDENCE — this posture is externally corroborated, not invented here.** §4.12 shows that the library's own benchmark harness parallelises with **multi-processing** ("QuantLib is not thread safe, so any kind of shared memory paralellism is ruled out", `test-suite/quantlibbenchmark.cpp:105`) and that the lead maintainer's guidance is *"don't share objects between threads and don't touch the globals. Otherwise, prefer multiprocessing."* The posture below is therefore the **conservative reading of upstream's own position**, and it is additionally the *only* posture that is available on Windows regardless of preference — QuantLib is static-linked there (§2.6), so the library's globals live inside the Shiori process and cannot be isolated by a DLL boundary.

| Aspect | Decision |
|---|---|
| Threads for pricing inside a process | **1** (the request-executing thread) |
| Parallel pricing | **DISABLED**, and not reachable by configuration in the first implementation |
| Recommended scaling mechanism | **Multiple processes**, each single-threaded (§5.4) |
| Any thread that does touch the QuantLib layer | Must hold the single process-wide **serialization gate** (§5.2.1) |
| Enablement condition | Only by satisfying §5.8, which requires new, *recorded* evidence |

**PROPOSED:** the posture is a **contract property, not an accident of implementation**. It must be visible: **Lane B** telemetry reports the concurrency mode (§12.2), and the benchmark metadata records it (§10.5), so a benchmark number can never be silently produced under a different concurrency posture than the one stated.

#### 5.2.1 The serialization gate

**PROPOSED.** The only permitted multi-threaded structure in the first implementation is: threads may exist (an embedding host may have them), but **every call that can reach the QuantLib adapter passes through exactly one process-wide gate**.

| Property | Required behaviour |
|---|---|
| Scope | Process-wide, one gate per process |
| Coverage | **All adapter entry points, without exception. A bypass is a defect.** The gate must additionally cover **every other in-process caller that can reach the same QuantLib library** — not only the adapter's public API. See the cross-plane rule below |
| Ordering | No reentrancy; the gate is not held across a callback into the caller |
| Failure | A holder that throws must release (RAII), never leak the gate |
| Observability | **Lane B** telemetry exposes acquisition count and wait time (§12.2), so contention is *measured* rather than guessed |
| Removal | The gate is removed only under §5.8 |

**PROPOSED rationale:** an explicit gate is strictly better than an implicit one. Without it, the serialization is real but invisible and untestable; with it, the constraint is enforced in one place and its cost is measurable. It also makes the "disabled" posture *checkable* rather than merely documented.

**PROPOSED — the cross-plane coverage rule (gate completeness).** The gate is sound only if **no** other in-process caller can touch the same QuantLib library while it is held. Shiori already has **four** production Python QuantLib consumers (§1.6, §1.8), and §2.7 **P6** deliberately makes the Python binding and the C++ pin **separate dependency planes**. If a host loads the future C++ engine *and* still invokes those Python callers in the **same process**, and both resolve to the **same** QuantLib library instance, those calls sit **outside** the gate: they can overlap the adapter while it temporarily changes `Settings` or uses other unsynchronised library state (§4.2–§4.10), defeating the one-thread posture and making results **call-order dependent**. Independence of *versions* (P6) does not by itself keep their in-process **state** separate, so this gap cannot be waved away as out of scope. **Per supported linkage, #227 must satisfy exactly one of the following and record which:**
(1) **a process boundary** — the C++ engine and any in-process Python QuantLib consumer never share a process (§5.4's multi-process scaling path already provides this); or
(2) **proven disjoint instances** — the binding and the pin are demonstrated to hold separate library/global instances for that linkage, so no shared mutable state exists; or
(3) **extend the gate** — every in-process caller that can reach the shared library is brought under the same process-wide gate, Python callers included.

Option (2) is the only one that is a **proof obligation** rather than an architecture choice, and it must be demonstrated for **every** supported linkage (§2.6 deliberately leaves static-vs-shared open on Linux), not asserted from the fact that the two planes version independently.

### 5.3 State classification

**PROPOSED.** Every state class below maps to a permission. The classification is the operative part of §5.

| Class | Meaning | Permitted sharing | Examples in this engine |
|---|---|---|---|
| **IMMUTABLE** | Written once during construction, never mutated; no lazy caches, no observers, no handles | Share freely across threads **within a process**; intended to be shared | Shiori-owned DTO values; the fingerprint/identity strings; resolved convention values that are frozen at construction |
| **REQUEST-LOCAL** | Constructed for one request, used, destroyed; never escapes | Never shared. One per request | QuantLib object graph built by the adapter for a single `RatesKernelInput` |
| **THREAD-LOCAL** | Genuinely per-thread by construction, with no cross-thread handoff | Shareable only with the thread that owns it | **Not used for pricing state.** The QuantLib session mechanism would create such state, and it is **not enabled** (§4.4) |
| **PROCESS-GLOBAL** | One instance for the whole process | Must be treated as mutable and unsynchronised | `Settings`, `ObservableSettings`, `LazyObject::Defaults`, `IndexManager` (§4.2) |
| **SHARED READ-ONLY** | Shiori-**defined**, **caller-owned** input DTOs whose *published state* is owning immutable value state or provably deep-immutable (§6.4 N4–N7) | Shareable across threads **only because the storage backing the identity cannot change for the request lifetime** (N4–N7) — **not** because the engine merely reads it | Caller-owned input DTOs (§6) |
| **UNSAFE / UNPROVEN** | No evidence of safety in the pinned configuration | **Never shared across threads** | Any QuantLib `LazyObject` (curves, models, instruments, calibration helpers); anything holding a `RelinkableHandle`; anything reading a moving term structure without an explicit evaluation date |
| **SERIALIZED BEHIND THE GATE** | May be touched only while holding the gate | Mutually exclusive | Every adapter entry point; every read or write of a PROCESS-GLOBAL; construction/destruction of QuantLib objects |

**PROPOSED:** the classification is **not** inferable from a type name. In particular, "SHARED READ-ONLY" is a Shiori-**defined** property (a DTO whose *published state* is owning immutable value state, §6.4 N4–N7), and must **never** be asserted of a QuantLib object — because §4.6 and §4.10 show that QuantLib "reads" mutate, and §4.7 shows a handle can be relinked. **`UNSAFE / UNPROVEN` is the default class for any QuantLib-typed object**, and a QuantLib object may leave it only by a positive construction argument recorded in review.

### 5.4 What concurrency is permitted today

**PROPOSED.**

| Permitted | Notes |
|---|---|
| **Multiple processes**, each single-threaded | The recommended scaling path. Process isolation makes PROCESS-GLOBAL state genuinely isolated, which the Windows static-link constraint (§2.6) makes impossible within one process |
| Concurrent read-only *Shiori-DTO* access | Safe because the input's identity-bearing **storage** is owning immutable value state for the request lifetime (§6.4 N4–N7) — **not** because the engine merely reads it. Caller mutation after fingerprint publication is an **explicit failure case** (§9.2 TL3) |
| Threads elsewhere in the host that never reach the adapter | Allowed; the gate makes this explicit |
| Benchmarking single-thread vs multi-thread | **Permitted pre-gate for evidence collection only**, when an enablement proposal names a candidate configuration — §5.8 PE5 *requires* that benchmark to produce its own evidence, so forbidding it here was **circular** (PE5 required a benchmark that this row forbade until PE5 was satisfied). Such a run is **evidence-only**: never a published performance claim, never a production speedup, and it **cannot itself enable** production parallelism. Production multi-thread benchmarking and use remain prohibited, and §5.2 stays DISABLED until PE1–PE6 hold, including the **PE6 owner decision**. §10.3 states the same exception |

**PROPOSED prohibitions for the first implementation:**

| Prohibited | Why |
|---|---|
| Two threads pricing through the adapter concurrently | Would race on the unsynchronised state of §4.2–§4.10. This is a **production** prohibition: the single exception is the **evidence-only** benchmark of a named candidate configuration in the row above, which exists to produce §5.8 PE5's evidence, is never a production path, and cannot itself enable parallelism |
| Sharing a QuantLib object (curve, model, instrument, helper, handle) between requests | UNSAFE / UNPROVEN by §4.6, §4.7, §4.10 |
| A cache that returns a **shared mutable QuantLib object** across requests | The central cache hazard; §7.3 forbids it structurally |
| Relying on `QL_ENABLE_SESSIONS` for isolation | Not enabled, and not demonstrated sufficient (§4.4, §4.13 U-B) |
| Adding a thread pool "for performance" | Correctness dominates performance; and it would be an unmeasurable change without §10's methodology |
| Loading the C++ engine **and** the Python QuantLib binding into the **same process** | Permitted **only** under one of §5.2.1's three cross-plane resolutions. Absent one, the Python callers sit outside the gate and can overlap the adapter while it mutates library-global state — the failure §5.2.1's cross-plane rule exists to prevent |

**PROPOSED:** "add a worker pool" is explicitly **not** an available response to a slow benchmark in this architecture. The available responses are §10's staged measurement and the multi-process scaling path.

### 5.5 Hard rules for the adapter under the initial posture

**PROPOSED.** These are the operational consequences of §4; each maps to an evidence item.

| # | Rule | Because |
|---|---|---|
| C1 | The evaluation date is **set explicitly** from the valuation context before any QuantLib work, and **restored** afterwards by an RAII guard **that preserves the raw null-vs-explicit state** (§5.5's locked restoration contract) | §4.3: unset means wall-clock, and a midnight rollover is a silent change |
| C2 | The restore is exception-safe RAII — **not** delegation to `SavedSettings` alone, which converts a raw-null date to a concrete one and cannot represent the original state (§4.3, §5.5), and never manual `try/catch` — **and it is verified, never assumed**. After the RAII scope the guard **compares the restored globals against its own snapshot**; if restoration failed it **fails closed before releasing the gate**: the gate is not released into a usable state, the process is **marked poisoned**, and **every subsequent request in that process is refused** rather than priced on polluted global state | §4.3: `SavedSettings` restores inside `catch (...) { /* nothing we can do except bailing out. */ }`, so a failed restore is **swallowed** — RAII alone cannot detect it, and C2 previously left the production contract silent on what happens next. Without the verification, a request could run with another request's evaluation date or flags and silently produce a wrong price. §5.7 **T4** is the test that must expose this |
| C3 | The engine **never reads the process-global fixing store**; fixings come from the DTO `FixingStore` | §4.9, and #225's explicit-input requirement |
| C4 | No `freeze()` on a lazy object in a pricing path | §4.6: `freeze()` is stale-by-construction |
| C5 | The global lazy-object default switch is **never** changed at run time | §4.6: it is global and affects only objects created afterwards |
| C6 | No `RelinkableHandle` is shared or retained across requests | §4.7 |
| C7 | Any moving term structure is used only with an explicitly pinned evaluation date for its whole lifetime | §4.8 |
| C8 | Extrapolation is explicit at each access — never left to the `extrapolate` parameter default | §4.8: the public default is `false`, while `discountImpl` is documented to assume extrapolation is required; this asymmetry must not be implicit |
| C9 | Every QuantLib object is destroyed **inside** the request, before the gate is released | §4.5, §4.7: observer/lifetime teardown order is a crash hazard |
| C10 | The macro set (`QL_ENABLE_SESSIONS`, `QL_ENABLE_THREAD_SAFE_OBSERVER_PATTERN`, `QL_FASTER_LAZY_OBJECTS`, `QL_THROW_IN_CYCLES`, `QL_ENABLE_OPENMP`) is fixed by the dependency port and **verified** against the linked library | §4.4: these change class layouts, so a mismatch is an ODR violation |
| C11 | **Every lazy recalculation is forced to completion inside the gate** before any result is read, so no object is ever handed out in a "calculated on first access" state | §4.6: `calculate()` sets `calculated_ = true` *before* `performCalculations()`, so an unforced object can be observed mid-construction. This is the rule the library's own engines use (`gaussian1dswaptionengine.cpp`) |
| C12 | No design may rely on `QL_ENABLE_THREAD_SAFE_OBSERVER_PATTERN` for concurrency, and `enableExtrapolation()`/`disableExtrapolation()` is never called on a live curve | §4.5: the switch covers the observer pattern only. §4.8: `Extrapolator`'s flag is a non-atomic `bool` on an object that is **not** an `Observable`, so changing it is unsynchronised shared-state mutation that notifies nobody |
| C13 | **The calendar mutators are never called.** `addHoliday()`, `removeHoliday()` and `resetAddedAndRemovedHolidays()` appear nowhere in the engine, the adapter, or any host path the engine controls, and **no externally-mutated calendar may be accepted as an input**. Calendars are therefore treated as **read-only shared state — never as immutable** (§4.2.1). Because a calendar *copy* shares the same `impl_`, "the engine does not mutate it" is **not** sufficient on its own: a mutation performed anywhere else in the process is visible to every request, and one request could silently change another's schedule | §4.2.1: `Calendar::Impl::addedHolidays`/`removedHolidays` are non-`const` and are read by a `const` `isBusinessDay()` through a shared `ext::shared_ptr<Calendar::Impl>` (verified at the `v1.43` tag) |
| C14 | **No in-process QuantLib caller outside the gate.** Any process hosting the adapter must satisfy one of §5.2.1's three cross-plane resolutions. A second in-process caller reaching the same library without the gate is a **defect**, not a configuration choice | §5.2.1, §1.8; evidence item **§5.7 T7** (or the recorded verification where resolution (1)/(2) is chosen) |

**PROPOSED — the locked evaluation-date restoration contract (C1/C2; owner decision of record).**

**EXTERNAL EVIDENCE, scoped to the pinned QuantLib `1.43` and its §2.6/§4.4 configuration** (verified against the `v1.43` source). It is **version-specific** and must not be generalised to another release or to a sessions-enabled build:

- `Settings::DateProxy` publicly inherits `ObservableValue<Date>`; `ObservableValue<T>::value() const` exposes the **raw** stored value.
- `DateProxy::operator Date()` converts a raw null `Date()` to `Date::todaysDate()`.
- `SavedSettings` stores `Settings::instance().evaluationDate()` **through that conversion**, so an initially-unset raw-null state becomes a **concrete date** in the saved object.
- `Settings::resetEvaluationDate()` explicitly restores the raw-null / clock-following state.

**Consequence:** `SavedSettings` alone is **insufficient** for Shiori's exact-restoration contract. **Shiori's adapter guard — not upstream `SavedSettings` — provides this property**, and this document does not claim otherwise.

The guard contract, **locked**:

1. Before any request mutation, capture the **raw** evaluation-date state from `Settings::instance().evaluationDate().value()` — **not** through implicit conversion to `Date`.
2. Record whether that raw value is null (`Date()`) or explicitly set.
3. On restoration: if it was originally null, restore with `Settings::instance().resetEvaluationDate()`; if it was originally explicit, restore that exact raw date.
4. Restore the other `Settings` flags under the existing C2 contract.
5. **Verify** restoration before the process is considered usable again.
6. If verification fails, the existing fail-closed / poison-process behaviour applies unchanged (§5.6): a polluted process is **never** released for pricing.
7. §5.7 **T4** must contain **both** cases — an initially *explicit* evaluation date and an initially *unset/raw-null* one. The raw-null case must prove the engine returns to the **raw-null / clock-following state**, not merely to the same calendar date observed before the request.

### 5.6 Fail closed, not "assert safe"

**PROPOSED.** A concurrency contract cannot be enforced by assertions:

- No correctness-critical guarantee may rely solely on `assert`/`NDEBUG` (which is off in release, §2.4).
- Where a caller can request a posture the engine does not support, the engine **fails closed** with an explicit reason (the #225 `ValueOrReason` shape), rather than silently serializing, silently racing, or silently degrading to single-threaded behaviour.
- Specifically: if a future entry point is asked for parallel execution before §5.8 is satisfied, the correct behaviour is a **refusal with a named reason** — not best-effort parallel execution.

### 5.7 Concurrency-correctness tests (the evidence gate)

**PROPOSED.** These tests do not exist yet. They are the **only** route from §5.2's disabled posture to anything else, and **#227** must create Tier S and **#229** Tier K (§5.7 tiers).

| # | Test | What it must prove |
|---|---|---|
| T1 | **Gate-serialized N-thread / N-request equivalence (current posture)** — price the same `RatesKernelInput` set sequentially and, from N threads contending on the serialization gate, again | Identical results, bit-for-bit on the fixture set; no divergence, no ordering dependence. **Note the scope: this validates the gate that is *present*, i.e. the disabled posture.** It cannot be extrapolated to a gate-removed configuration |
| T2 | **Request-isolation test** — concurrent requests whose evaluation dates and fixing sets differ | No request observes another's evaluation date, fixings, or market data. This is the test that would catch an accidental `IndexManager`/`Settings` read |
| T3 | **ThreadSanitizer run** of **T1, T2 and T6** on the Linux CI job — and of **T8** as well, **once** a candidate configuration exists (§9.8 Q6) | No data race reported in Shiori code **or** in the QuantLib paths the engine exercises. **A TSan run counts as enablement evidence only when the dependency is instrumented too.** Clang documents that TSan generally requires *all* code to be compiled with `-fsanitize=thread`; against a precompiled, uninstrumented QuantLib a race **inside the library cannot be reported**, so PE1 could accept an unsafe QuantLib path on the strength of a run that was structurally incapable of seeing it. The TSan leg must therefore build QuantLib **from source with compatible TSan instrumentation** on a **dedicated triplet/build configuration** that carries its **own binary-cache identity**, so an ordinary non-TSan binary can never be silently substituted (§11.3/§11.4), and the instrumented dependency configuration must be recorded in the evidence metadata (§10.5 M4). **T6 is included deliberately.** T1 prices the input set sequentially first, so its threaded phase can only exercise **already-warm** cache entries and would miss a race in **first population or eviction** — exactly the races T6 exists to provoke. Leaving T6 out of the TSan leg would let a race on the shared process cache pass while T6 succeeded by luck, and a corrupted cache entry breaks deterministic pricing. TSan findings in third-party code are still evidence and must be triaged, not suppressed |
| T4 | **Global-state set/restore test** — assert that after every request, `Settings` and the other globals are exactly as before, including on the exception path. **Two mandatory cases:** (i) an initially **explicit** evaluation date is restored to that exact date; and (ii) an initially **unset/raw-null** evaluation date is restored to the **raw-null / clock-following state** — not merely to the same calendar date observed before the request, which a `SavedSettings`-only implementation would appear to pass (§4.3, §5.5). **Plus the forced-failure leg**: with a restoration failure deliberately injected (a throwing observer during restore, which §4.3's swallowed `catch (...)` makes reachable rather than hypothetical), assert that (i) the globals *may* differ — so the test must **not** require "exactly as before" on that path, (ii) the process is **marked poisoned**, (iii) the guard **does not release the gate into a usable state**, and (iv) **every subsequent request is refused** rather than priced, with that refusal **not deadlocking on the gate** | C1/C2 hold under failure, not only on the happy path. The forced-failure leg is what actually exercises **C2's verification path** — an implementation that swallowed the restoration failure and carried on would pass the happy-path assertion while serving prices computed on polluted process-global settings |
| T5 | **Lifetime/teardown test** — destroy QuantLib objects under load while observers exist | No crash, no use-after-free (C9) |
| T6 | **Cache-under-concurrency test** — a shared cache hit/miss under N threads | No torn entries, no duplicate *observable* population beyond the documented policy (§7.5), and no mutation of the returned object. **Run under TSan as part of T3** (§5.7), not only later via T8: it is the only test that provokes first-population and eviction races |
| T7 | **Gate-accounting test** — attempt a bypass, **including a second in-process caller (e.g. an in-process Python QuantLib consumer, §5.2.1) attempting to reach the shared library without the gate** | The bypass is detected and fails closed. This is the **evidence item for C14**; where §5.2.1's chosen resolution is (1) a process boundary or (2) proven disjoint instances, the matching **recorded verification** is the evidence instead (§5.2.1, §5.5 C14) |
| T8 | **Candidate-configuration test — the configuration PE would actually enable.** Rerun T1, T2, T5 and T6 with the gate **removed or narrowed exactly as the enablement proposal would remove or narrow it**, and subject that same candidate configuration to TSan — and T8's TSan run is subject to the **same instrumented-dependency requirement** as T3: it is admissible only from the §5.7 T3 TSan-instrumented build (§11.3) | Equivalence, isolation, lifetime and cache safety hold **without** the gate. This is the only test that speaks to the enabled posture |

**PROPOSED — two execution tiers, because #227 cannot price and #229 owns the kernel.** `docs/33_rates_market_model_result_contracts_225.md` `:1324–1327` assigns **#227** only the C++20 skeleton, the versioned DTO/JSON contract and CI, and assigns the **first swap kernel** to **#229**; the curve and extrapolation values #228 implements are still `RED-01`. A test that **prices a `RatesKernelInput`** therefore cannot be executed, satisfied, or meaningfully failed by #227.

| Tier | Tests | Requires | Obligation |
|---|---|---|---|
| **S — skeleton-executable** | **T4, T5, T7**, plus §7.7's non-concurrent `CT1–CT8` | Only the skeleton surface: the QuantLib isolation adapter, the serialization gate, the cache and the DTO/JSON boundary, driven by a **non-price-bearing** adapter call | **Required of #227 now**, in the §11.3 jobs |
| **K — kernel-dependent** | **T1, T2, T3, T6, T8** | The first swap kernel — a real `RatesKernelInput` priced end to end | **Becomes required when #229 supplies the kernel** (§9.8 Q7). #227 must **not** add pricing to satisfy it, must **not** invent a `RED-01` value, and must **not** leave a required job red |

T3 is Tier K even though it is a sanitizer run, because it is defined as a TSan run **of T1, T2 and T6**, none of which can exist before the kernel. T6's *non-concurrent* cache mechanics are Tier S via §7.7 `CT1–CT8`; T6 itself is the **concurrent** leg and needs priced requests to populate a cache.

**PROPOSED — why T8 is not optional *for enablement*, and why T1–T7 cannot substitute for it.** T1–T7 are tests *of the serialized posture and of the gate itself*. Enabling parallel pricing does not merely relax a policy: it **changes the configuration under test**, because the gate is what serializes execution today. Evidence gathered with the gate held is therefore evidence about a configuration that enablement would delete. Without T8 the engine could pass every gate in §5.8 and then run a configuration that was never equivalence-checked or TSan-checked. **T8 is therefore mandatory in the change that proposes enablement, and inapplicable before one exists (§9.8 Q6).** The rule is accordingly:

> **No configuration may be enabled unless that exact configuration has itself passed the equivalence, isolation and TSan checks (T8).** Evidence from the gated configuration is necessary but cannot be promoted.

**PROPOSED:** T3's TSan run is required because T1/T2/T6 alone cannot distinguish "no race" from "no race *observed today*". A passing equivalence test is necessary but not sufficient.

### 5.8 Conditions required before parallel pricing may be enabled

**PROPOSED — all must be true, and each is recorded evidence, not an argument:**

| # | Condition |
|---|---|
| PE1 | **§5.7 Tier S (T4, T5, T7)** exists and passes **now**, on the gated posture, on **both Windows and Linux** — the part #227 can and must satisfy. **Tier K (T1, T2, T3, T6, T8) cannot be satisfied before #229 supplies the first swap kernel** (`docs/33…225.md` `:1324–1327`), because every test in it prices a `RatesKernelInput`; requiring it of #227 would force out-of-scope pricing or an invented `RED-01` value (§5.7 tiers). PE1's full force therefore applies **at enablement**, when the kernel exists: **T1–T7 must then all pass**, with **T3 (TSan, covering T1, T2 and T6) on Linux**, the only platform in the §11.3 matrix providing ThreadSanitizer — and that TSan evidence is admissible **only** as produced by the **TSan-instrumented dependency build** (§5.7 T3, §11.3), never by a TSan run over an uninstrumented QuantLib. **T8 is conditional and is not part of this "exist and pass now" set**: it is defined as a rerun of T1/T2/T5/T6 under *the gate removed or narrowed exactly as an enablement proposal would*, so it is **not applicable until an enablement proposal supplies a candidate configuration**. While no such proposal exists, T8 is **reported as not-applicable with a reason** (§9.8 Q1/Q6) and is **not** a green-gate requirement — otherwise §11.3's required jobs could never be green without #227 inventing and passing a parallel concurrency configuration that has not been approved. **When** a proposal is submitted, T8 becomes **required and blocking for that proposal**: PE1 then means T1–T8 all pass *for that named candidate configuration*. **T8 is the load-bearing one *for enablement*** — it is the only test run in the configuration enablement would actually enable. (An earlier draft required all of T1–T7 on both platforms while assigning all test execution to Linux, which made this gate unattainable; a later draft omitted the ungated configuration entirely; a third made T8 unconditionally required, which no candidate configuration existed to satisfy) |
| PE2 | The specific QuantLib configuration used is **named** in **Lane B** runtime/build telemetry and benchmark metadata (§10.5 **M4**) — not in a result-DTO field (version + every relevant macro), and the reason it is safe is derived from §4-style source evidence — not from a community claim |
| PE3 | The evaluation-date / fixing / observer / lazy-cache mechanisms are each shown either unused or provably isolated per request |
| PE4 | Reproducibility under concurrency is demonstrated per #225 §15.4's replay identity — concurrent execution must not change a result |
| PE5 | A benchmark serves as a **verification** step (does parallelism still reproduce results), with published concurrency semantics per §10.5. **Producing this evidence is explicitly permitted *before* the gate passes**: an evidence-only multi-thread benchmark of the named candidate configuration (§10.3, keyed to §9.8 Q6). Without that exception PE5 would depend on itself — it requires a benchmark that the §10.3 matrix would otherwise forbid until PE5 was already satisfied |
| PE6 | An owner decision accepts the posture, with the risks of **PE1–PE5** stated |

**PROPOSED:** until **PE1–PE6** are satisfied, the answer to "can we parallelise pricing?" is **no**, and the answer to "the single thread is too slow" is §10 measurement plus the multi-process path — not a thread pool.

### 5.9 UNPROVEN (concurrency)

| # | UNPROVEN | Note |
|---|---|---|
| U-H | That the serialization gate is *sufficient* for safety in an arbitrary host | The gate serializes adapter entry; a host that touches QuantLib elsewhere would bypass it. The contract states the gate's coverage; it cannot police code outside the library |
| U-I | The magnitude of gate contention in realistic use | Measurable only after §10's benchmark harness exists |
| U-J | Whether multi-process scaling meets the performance need | A performance question, deliberately not answered by this architecture |
| U-N | Whether a **TSan-instrumented QuantLib build** is achievable in CI at acceptable cost, and whether it covers the paths the engine exercises | The instrumented dependency build is what makes TSan evidence admissible (§5.7 T3), but no such build has been produced in this environment (no local C++ toolchain, §2.11). #227 must produce it, record it, and confirm coverage |

---

## 6. Immutable input strategy

### 6.1 The authoritative input contract

**OBSERVED (contract ownership).** The input contract is owned by #225 and is **not** redefined or extended here. Per `docs/33_rates_market_model_result_contracts_225.md` §5/§5.1, the canonical boundary receives one composition object, `RatesKernelInput`, comprising:

| Component | Owner | #226's relationship |
|---|---|---|
| `ResolvedSwap` (resolved trade) | #224 | Consumed unchanged |
| `MarketSnapshot` | #225 §6 | Consumed unchanged |
| `CurveSelection` (`discount_curve_id`, `forecast_curve_by_index`) | #225 §5.1 | Consumed unchanged |
| `ExerciseTerms` / `SettlementTerms` as `ValueOrReason<T>` | #225 §10/§11 | Consumed unchanged |
| `ModelInput` as `ValueOrReason<ModelInput>` | #225 §9.6/§14.1 | Consumed unchanged |
| `ValuationContext` (valuation date, reporting currency, identity) | #225 | Consumed unchanged |

**OBSERVED.** #225 §5.1 states these inputs are joined by the kernel "at valuation time", that market/model objects carry **no** trade economics and `ResolvedSwap` carries **no** market data, and that exercise/settlement/model inputs cross the boundary explicitly and "are never resolved from ids inside `MarketSnapshot` and never defaulted from market data." #226 depends on that; it does not restate or alter it.

**PROPOSED:** #226 adds **no DTO field**. Where this section needs a property of an input (for example, "is it frozen?"), it is expressed as an engine-side **rule about how the engine treats the input**, not as a new field. #225 §18 already records the division: caching requires every cache-keyable field to be present and typed (§6.4 of that document); the implementation belongs here.

### 6.2 Ownership and lifetime

**PROPOSED.**

| Question | Decision |
|---|---|
| Who owns the input? | **The caller.** The engine never takes ownership and never frees it |
| How does the engine hold it? | By an **owning immutable snapshot**, or by a **non-owning view over engine-owned immutable backing storage**, for the duration of one request — **never** a retained alias into caller-mutable storage (§6.4 N4–N7) |
| May the engine retain it beyond the request? | **No**, with one exception: if an entry is cached, the retained artifact is a cache entry keyed by value identity — never a pointer to the caller's object (§7) |
| How long must the input stay valid? | For the whole of the request, including any work performed under the serialization gate |
| May a caller mutate an input during a request? | **No.** The engine's correctness assumes immutability from the moment it is accepted (§6.4) |

**PROPOSED:** because the engine **does not own** the input, an input may be shared across concurrent requests **only** when it is genuinely immutable (§5.3 SHARED READ-ONLY) — and "genuinely immutable" means the **storage backing the identity** cannot change, **not** merely that the engine holds a `const` view of it (§6.4 N4–N7). This is what makes "share the DTO, never share the QuantLib object" the central rule of the design.

### 6.3 No live acquisition inside pricing

**PROPOSED.** The kernel performs **no I/O**. Specifically, inside the pricing path there is:

| Forbidden inside the kernel | Why |
|---|---|
| Network access of any kind — including Bloomberg/BPIPE/BLPAPI | #223 §4 assigns acquisition to Python; the kernel is deterministic by contract (`docs/33...` §5: "deterministic, no I/O, no clock") |
| Filesystem reads for market data, curves, fixings, or configuration | An input read from disk is invisible to the fingerprint and breaks replay |
| Reading the system clock | `docs/33...` §5 requires "no clock"; §4.3 shows QuantLib would otherwise supply one implicitly |
| Reading process environment variables to alter behaviour | Same reason; a hidden input |
| Reading the process-global QuantLib fixing store | §4.9 / C3 |

**PROPOSED:** #223 §4 already draws this boundary ("No live Bloomberg fetch inside pricing"); §6.3 is its C++-layer enforcement. Any adapter call that could perform I/O is a defect, and the test strategy (§9.5) includes a guard against it.

### 6.4 Immutability after publication

**PROPOSED.** Define *publication* as the moment an input's fingerprint is computed (§6.7). The locked rule is:

> Once an input DTO/content fingerprint is published for pricing, every byte/value that participates in that identity must be backed by storage whose contents **cannot change for the lifetime of that published request snapshot**.

The rules are:

| # | Rule |
|---|---|
| N1 | After publication, an input is **never** mutated — not by the engine, not by the caller, not by a cache, not by **either** diagnostics lane (§12.2). Compliance is established by **N4–N7** (immutable backing storage), **not** by caller cooperation |
| N2 | A DTO type carries **no mutation API** reachable after construction: no setters, no public non-const members, no `mutable` members, no lazy caches, no observer registration |
| N3 | A DTO type **must not** contain a QuantLib type, a `Handle`/`RelinkableHandle`, a `shared_ptr<Observable>`, or any type whose value can change after construction |
| N4 | Public/published DTO state must be **owning immutable value state**, or otherwise **prove** equivalent deep immutability — and that proof must be over **storage the engine owns**, or equally immutable storage whose lifetime dominates the published snapshot. **Non-owning views never appear in published DTO state** (N6 governs). **`const` is not that proof:** `const` on a view or reference does **not** prevent mutation of the backing storage through another alias, so "the engine receives it as `const`" must **not** appear anywhere in this design as an immutability argument |
| N5 | A `std::span`, `std::string_view`, pointer, reference, or any other **non-owning alias into caller-mutable storage** must **not** be retained as published request state |
| N6 | Zero-copy/non-owning views may exist **only** as request-local implementation views over **engine-owned immutable backing storage** whose lifetime dominates the view and whose contents cannot mutate after fingerprint publication |
| N7 | **Fingerprinting and then pricing mutable caller-owned backing storage is forbidden.** A request whose identity-bearing bytes can change between fingerprint computation and pricing has **no valid identity** |
| N8 | Any derived value computed during a request is either **request-local** (§5.3) or, where §7 permits caching, stored only as an **owning immutable value** (§6.6 V3). It is **never written back into the input** |
| N9 | If a caller needs a modified input, it constructs a **new** object with its own identity and fingerprint. In-place "update" is not a supported workflow |

**PROPOSED — why the rule is stated over storage, not syntax.** N1–N3 are only meaningful if violating them fails. `const`-qualified accessors plus non-`mutable` members catch *in-type* mutation, but they **cannot** catch mutation through a **second alias to the same backing bytes**. That is why the invariant is stated over the **storage** that backs the identity, and why N4–N7 exist: they are precisely the part a `const`-only formulation silently misses. A design that skipped them would let the engine price **different content under the published cache/replay identity** — a correctness failure, not a style one.

### 6.5 The Python ↔ C++ DTO boundary

**OBSERVED / PROPOSED.** #225 §15.2 defines the wire-schema versions (`RATES_KERNEL_INPUT_V1`, `MARKET_SNAPSHOT_V1`, `CURVE_SET_V1`, `DISCOUNT_CURVE_V1`, `FORWARD_CURVE_V1`, `FIXING_STORE_V1`, `VOL_QUOTE_V1`, `VOLATILITY_INPUT_V1`, `MODEL_INPUT_V1`, `EXERCISE_TERMS_V1`, `SETTLEMENT_TERMS_V1`, `RATES_PRICING_RESULT_V1`, `RATES_RISK_RESULT_V1`, `MODEL_CALIBRATION_INPUT_V1`, `MODEL_CALIBRATION_RESULT_V1`), the `<CONTRACT>_V<n>` pattern, and the rule that "a wire-schema revision ships under a new version string, never by mutating a published shape".

**PROPOSED — #226's contribution to that boundary:**

| # | Decision |
|---|---|
| B1 | The boundary is **serialized data** (versioned JSON per #225 §15.4's canonical form), not a shared-memory C++ ABI. Format/hash details are owned by #227 per #225 |
| B2 | **No QuantLib type appears in any DTO field, in either direction.** This is the §3.5 rule stated at the wire level |
| B3 | Equality of inputs is by **content fingerprint**, computed over the canonical form — never by pointer or object identity |
| B4 | Decoding is **fail-closed**: an unknown `<CONTRACT>_V<n>` is refused *before* any other content is read (per #225 §15.2/§16) |
| B5 | `NULL_WITH_REASON` is a **first-class, fingerprint-participating** value, per #225 §15.4 — the engine must not treat an absent-with-reason field as "unset" |
| B6 | The boundary carries **no implicit defaults** on decode. A missing field is either handled by an explicit contract rule or is a decode failure |
| B7 | The wire form is the **only** input channel. There is no "out-of-band" parameter (env var, global, config file) that can change a result |

### 6.6 What the engine derives, and where it lives

**PROPOSED.** Materializing a resolved input into QuantLib objects is a **derivation**, and every derivation is request-local:

```text
caller-owned input DTO — *published state* owning / deep-immutable (§6.4 N4–N7)  (SHARED READ-ONLY, §5.3)
        |
        |  adapter translates under the serialization gate (§5.2.1)
        v
request-local QuantLib object graph  (REQUEST-LOCAL, §5.3)
        |
        |  used and destroyed within the request
        v
DTO result  (immutable, returned by value)
```

**PROPOSED rules:**

| # | Rule |
|---|---|
| V1 | A request-local QuantLib object is **never** stored in a cache, returned to a caller, or shared with another request (§7.3) |
| V2 | A request-local object is destroyed before the gate is released (C9) |
| V3 | If a derivation is expensive enough to cache, what is cached is the **derived value** or a value-identified recipe — not the QuantLib object (§7.2) |
| V4 | A derivation that cannot be represented without a QuantLib object is **not cacheable** in the first implementation, and must be reported as such rather than cached unsafely |

### 6.7 Publication and identity

**PROPOSED.** Per #225 §15.4, replay identity is `inputs_fingerprint + engine_version + method + tolerance`, the fingerprint preimage is **non-recursive** (each object's own identity field excluded from its own preimage; child/input fingerprints participate as ordinary fields), and the canonical serialization is sorted-key/fixed-separator.

**PROPOSED — engine-side consequences:**

| # | Rule |
|---|---|
| ID1 | The kernel treats the input fingerprint as **read-only identity**, never recomputing it differently |
| ID2 | A fingerprint is never used as a *substitute* for a required field's presence or meaning |
| ID3 | Any cache key that includes a fingerprint includes the **whole** required identity set (§7.2), not a convenient subset |
| ID4 | `engine_version` participates in replay identity, and therefore in cache compatibility (§7.6) |

### 6.8 Fail-closed input behaviour

**OBSERVED.** #225 §16 defines the fail-closed table, including: unknown `schema_version` → refuse before reading other content; `VALUATION_DATE_MISMATCH`; any `NULL_WITH_REASON` in a section the requested calculation requires → `MISSING_MARKET_DATA` naming the section and reason; no silent date roll, no silent unit coercion, no silent cross-snapshot mixing.

**PROPOSED — #226 adds only these engine-side enforcement points:**

| # | Point |
|---|---|
| X1 | The engine refuses an input whose schema version it does not implement, before decoding content |
| X2 | The engine refuses a `NULL_WITH_REASON` in a required position; it never substitutes a default, a zero, or a QuantLib default (§3.6) |
| X3 | The engine refuses `UNRESOLVED_METHODOLOGY` for any calculation that would require choosing a methodology — it does not pick one (§13.2) |
| X4 | Cross-input mixing is impossible by construction: a request carries exactly one `RatesKernelInput`, and no global may supply a missing part (this is the structural enforcement of §4.9/C3) |
| X5 | Refusals are **values with reasons**, not exceptions thrown across the boundary, and not silent fallbacks (§5.6) |

---

## 7. Cache architecture

### 7.1 The governing principle

**PROPOSED.**

> **Cache identity must be reconstructible from explicit input contracts.**

A cache entry may be found again using only values that a `RatesKernelInput` (or a documented derivation of one) explicitly carries. If reconstructing a key requires the *history* of the process — what was set globally, what time it is, what was constructed before — the cache is not a cache; it is hidden state with a hit rate. **Explicitness includes the recorded build/engine identity, not only input fields:** `engine_version` (§6.7, K6, §7.2.1) and the pinned-version tag of §7.6 are *explicit recorded identity*, not ambient state, and remain required key components. This rule excludes **hidden** state — it does not exclude the documented version tags.

### 7.2 Forbidden key components

**PROPOSED.** These may **never** participate in a cache key. They are prohibited as *identity*, not merely discouraged:

| # | Forbidden | Why it is unsafe |
|---|---|---|
| K1 | **Pointer address / object identity** | Addresses are reused after free; two different values can share an address over time, and two equal values can have different addresses. Guarantees wrong hits *and* wrong misses |
| K2 | **Hidden global state** (any PROCESS-GLOBAL from §4.2, or any other ambient setting — but **not** the *explicit* recorded engine/pinned-version tag of §6.7 K6 and §7.6, which is identity rather than ambient state) | Not reconstructible from the input; makes behaviour depend on call order (§4.6's lazy default is the archetype) |
| K3 | **System clock** (now, "today", elapsed time, timestamps) | Unreconstructible and non-reproducible; also the exact mechanism by which an unset evaluation date leaks the wall clock (§4.3) |
| K4 | **Implicit current evaluation date** | A *derived* value from K2/K3 unless explicitly supplied; using it implicitly reintroduces the midnight-rollover hazard |
| K5 | **Mutable object identity** (a `Handle`, a relinkable target, a shared mutable QuantLib object) | The key would change value without changing identity, or vice versa (§4.7) |

**PROPOSED — the correct reading, which the test must encode.** A forbidden component is one whose value must **not** be able to change the key. The required assertion is therefore the **opposite** of "the keys differ":

> **Two otherwise-identical explicit inputs that differ *only* in a forbidden component (K1–K5) must produce the *same* key — and may therefore legitimately hit the same entry.**

That is precisely what makes explicit-input determinism real. A cache that folded hidden state (a pointer, an ambient global, a timestamp) into the key would produce *different* keys for the **same** business input, which is the defect K1–K5 exist to prevent. **An implementation must never "fix" a failing invariance test by adding the forbidden value to the key** — that would convert a cache defect into a determinism defect.

#### 7.2.1 The complement: required identity

**PROPOSED.** Forbidden components have a complement, and the two directions must not be confused:

| # | Required identity | Rule |
|---|---|---|
| K6 | **Engine version — recorded, never unknown** | The engine version is a **mandatory, present** key component, *not* a forbidden value. Changing it **must** change the key (or render the entry unusable), because two different engines must never share entries. What is prohibited is an **unknown / unrecorded** version, not a known one. |

**PROPOSED:** K6 is stated here rather than in the table above precisely because it is a **presence** requirement. Classifying it as a forbidden value (as an earlier draft of this document did) yields a self-contradictory test that would simultaneously demand a change of engine version *must* and *must not* alter the key.

**PROPOSED — the two catch tests run in opposite directions** (§7.7 CT3/CT3b): changing a **forbidden** component must leave the key **unchanged**, while changing any **required** component (including the recorded engine version) must change the key. A cache whose tests lack either direction is unverified.

### 7.3 The central cache rule: never cache a QuantLib object

**PROPOSED.**

> **A cache may store an immutable value or a value-identified construction recipe. It must never store, return, or share a mutable QuantLib object.**

Rationale is §4: a QuantLib curve/model/instrument/helper is a `LazyObject` with unsynchronised mutable caches (§4.6), may contain relinkable handles (§4.7), and may read the global evaluation date at query time (§4.8). Returning one from a cache would make a UNSAFE/UNPROVEN object SHARED-READ-ONLY by accident, and would violate §6.6 V1/V3.

**PROPOSED permitted cache payloads:**

| Payload | Permitted? | Note |
|---|---|---|
| Immutable POD/DTO values (e.g. a materialized discount-factor table, a schedule's dates, a fixing lookup result) | **Yes** | The default and preferred form |
| A value-identified *recipe* sufficient to rebuild a request-local object deterministically | **Yes**, if rebuilding is cheaper than the cached derivation and the recipe is fully value-identified | Must not embed global state |
| A request-local QuantLib object graph | **No** | §6.6 V1 |
| A `Handle` / relinkable / anything holding one | **No** | §4.7 |
| An object with a lazy cache or observer registration | **No** | §4.6 |

### 7.4 Cache layer evaluation

**PROPOSED.** Each layer named in the issue is evaluated and given an explicit verdict. A layer may be **CACHED**, **NOT CACHED (now)**, or **DEFERRED** — and a "no" is a legitimate architectural outcome that must be justified, not an omission.

| # | Layer | Verdict | Key (required identity) | Owner | Lifetime | Invalidation | Concurrency | Methodology/version compatibility |
|---|---|---|---|---|---|---|---|---|
| L1 | **Parsed DTO** | **CACHED (process-local)** | **Fingerprint recomputed from the received canonical bytes** — *never* the declared/embedded `content_fingerprint` field — + `schema_version` set + engine version | Engine | Process | Eviction/backpressure only; entries immutable once stored | Read-only after publish; population under the gate or a documented single-writer policy | A mismatch of schema or engine version is a **miss** (§7.6); a declared fingerprint that does not match its recomputation is a **refusal, never a hit** (§7.4.1) |
| L2 | **Schedule / calendar** | **CACHED (process-local)** | Resolved schedule inputs: convention-set identity + resolved dates + calendar identity + day-count id + BDC + EOM/stub policy + `ResolvedSwap` identity | Engine | Process | Never invalidated by time (all inputs explicit); eviction only | Immutable *resolved-date* result, safe to share — but only because it is computed against a calendar the engine has verified is **unmutated** (§4.2.1, C13) | Must include convention/method version ids |
| L3 | **Materialized curve (values)** | **CACHED (process-local)** | Curve id + curve content fingerprint + `valuation_date` + extraction grid + **construction methodology id/version** | Engine | Process | New snapshot or new methodology version ⇒ different key, not an invalidation of the old one | Immutable numeric table; safe to share | **Must include construction methodology version**; RED-01 unresolved ⇒ no default methodology may be assumed |
| L4 | **Term structure (QuantLib object)** | **NOT CACHED** | — | — | — | — | — | §7.3 forbids caching the object. The *values* may be cached as L3 |
| L5 | **Model** | **CACHED only after model selection is resolved** | `model_id` + `model version` + model-parameter fingerprint + curve/vol fingerprints it depends on | Engine | Process | New parameters ⇒ new key | Depends on representation; prefers immutable parameter sets over model objects | **RED-225 items unresolved**; see §8.4 |
| L6 | **Calibration** | **SEPARATE — see §8** | §8.1 | Engine | Process | §8.3 | §8.5 | §8.2 |
| L7 | **Benchmark-only** | **PERMITTED but physically separated** | Benchmark fixture identity (§10.5) | Benchmark harness **only** | Process, benchmark lifetime | Harness-defined | Harness-defined | **Must be unreachable from production code paths** |
| L8 | **Cross-process / persisted** | **DEFERRED — not in the first implementation** | — | — | — | — | — | Requires an explicit serialization-compatibility story and a cache-version policy; out of scope for #226 |

**PROPOSED notes on specific verdicts:**

- **L1** is the cheapest and safest layer, but must not be *assumed* beneficial: an input that is parsed once per process is not worth caching. #227 must show a measured benefit or not add it (AGENTS.md rule 2, and `docs/08`'s "profiling before optimization").
- **L2** is attractive because it is pure immutability: resolved schedules are values. Note the deliberate omission of **"today"** from every column — a schedule cache keyed implicitly on the current date is exactly K4.
- **L3 vs L4 is the load-bearing distinction of this section.** The values are cacheable; the object is not.
- **L5** is **conditional, and currently blocked**: caching a model requires a resolved model identity, and per #225 the model/methodology items are RED. Until resolved, L5 must not be implemented (a cache key that omits model identity is forbidden by §7.6).
- **L7** exists so that benchmark speedups are never achieved by a cache that production cannot use. It is **physically separated** at the target/link level, not merely by convention.
- **L8** is refused because process-local lifetime is the only lifetime for which this document can state a coherent compatibility and concurrency story. A persistent cache would require its own issue.

#### 7.4.1 Trust boundary: an embedded fingerprint is verified, never trusted

**PROPOSED — this rule exists because L1 is keyed on a fingerprint, and a fingerprint carried *inside* the payload is attacker- and staleness-controlled.**

> **No cache lookup — L1 especially — may be keyed on a `content_fingerprint` that the engine has not itself recomputed from the bytes it actually received.**

The failure this prevents is concrete and severe. If L1 were keyed on the **declared** fingerprint field:

1. a caller (or a serialization bug) could present payload bytes that do **not** match the fingerprint they declare — stale after an edit, truncated, or deliberately forged;
2. the key would match a previously cached parsed DTO, and the lookup would **hit**;
3. the engine would then price the *cached* input rather than the payload it received, and return a result whose `inputs_fingerprint` describes something the caller never sent;
4. and because decoding was skipped, the fail-closed checks — `UNKNOWN_SCHEMA_VERSION` and the unknown-methodology refusal (§6.5 B4/B5, #225 §16) — would be **bypassed**, converting a fail-closed boundary into a fail-open one.

**PROPOSED rules:**

| # | Rule |
|---|---|
| T-L1a | The L1 key is the fingerprint **recomputed from the received canonical bytes**, or is derived directly from those bytes. It is never the embedded/declared field |
| T-L1b | When a payload also *declares* a fingerprint, that declaration is **checked, not believed**: recompute and compare. A mismatch is a **refusal with a reason** (never a miss, never a hit, never a silently-priced stale entry) |
| T-L1c | **No cache lookup may precede schema-version validation.** Decode-and-validate is fail-closed and comes first; the version is part of the key so a valid payload can still hit, but an unknown version can never reach the cache |
| T-L1d | The same rule generalizes: any cache layer whose key contains a fingerprint of a **received payload** uses the recomputed value. Fingerprints the engine computes over data it owns (§6.7) are unaffected |

**PROPOSED:** adding the embedded fingerprint to the key *as well* is permitted and harmless — but it can never be the *only* fingerprint component, because then the payload would be choosing its own cache identity. This is the cache-side expression of the same principle §6.7 and #225 §15.4 apply on the replay side: identity is **derived**, not **asserted**.

### 7.5 Invalidation and population policy

**PROPOSED.**

| # | Rule |
|---|---|
| I1 | **Invalidation is by key, not by mutation.** Because every key component is an explicit input value, a changed input produces a *different key*; there is no in-place update of an entry. Entries are immutable once published |
| I2 | The **only** invalidation mechanism is eviction (capacity/backpressure, or process exit). Time-based expiry is **not** a correctness mechanism and must not be used to compensate for an incomplete key |
| I3 | A time-based or size-based eviction policy is **performance** policy and must never be able to change a result: **for an ordinary, fully deterministic derivation**, a hit and a miss must produce identical output. **For calibration results this is narrowed** — a recomputation of the same calibration input is a *new run instance* and may legitimately differ in run-instance provenance fields, so I3 must **not** be read as requiring instance-identity equality there. The governing rule is §8.3.1's calibration equivalence invariant. This is testable (§9.4, §7.7 CT1/CT9) |
| I4 | Entry publication is **atomic** — consumers observe either no entry or a complete entry, never a partial one |
| I5 | Duplicate population of the same key concurrently must converge to one logically equivalent entry; a "thundering herd" may recompute, but must never publish a torn or conflicting entry |
| I6 | A **failed** derivation is **not** silently cached as a success. Negative caching, if ever introduced, requires an explicit reason value and its own version identity, and must not mask a transient failure as a permanent one |
| I7 | A cache must not become an authority: a hit must not change *which* methodology is used, only *whether* it is recomputed (§13.1) |

**PROPOSED:** I3 is the correctness property that makes caching safe to add; I1 is what makes it auditable. Together they mean a cache can only ever be a performance optimization, never a semantic one.

### 7.6 Version compatibility

**PROPOSED.** Every cache entry is **version-tagged**, and a version mismatch is a **miss**, never a hit and never an error:

| Tag | Source | Effect of mismatch |
|---|---|---|
| Wire `schema_version` set | #225 §15.2 | Miss |
| `engine_version` | Engine identity | Miss |
| QuantLib version (pinned) | §3.2 | Miss |
| Construction / methodology `*_id` + version | #225 §15.2 (methodology versions evolve independently of wire versions) | Miss |
| Data `content_fingerprint` | #225 §15.4 | Different key (not a mismatch) |

**PROPOSED:** the engine version is **not optional** (K6). An entry whose engine version is unknown is not usable, and an engine that cannot state its own version must not populate caches at all.

### 7.7 Required cache tests

**PROPOSED.** Implement these before any cache is enabled. **CT1–CT8 are skeleton-executable (#227, §5.7 Tier S).** **CT9 is deferred** with the concrete calibration methodology, because it cannot be written against a calibration path that has no approved objective, optimizer or tolerance (§8.4, §9.2 TL5).

| # | Test | Proves |
|---|---|---|
| CT1 | **Hit/miss equivalence** — **ordinary entries only** | Cached and uncached paths return identical results on the fixture set. **Does not apply to calibration results**, whose hit/recompute invariant is §8.3.1 (CT9) |
| CT2 | **Required-field sensitivity** | Two inputs differing in exactly one **required** identity field produce different keys (§7.4's key column, field by field) |
| CT3 | **Forbidden-component invariance (K1–K5)** | Two otherwise-identical explicit inputs differing **only** in pointer identity / ambient global state / clock / implicit evaluation date / mutable identity produce the **same** key (and may legitimately hit) — proving the key carries no hidden state. This test fails if an implementation wrongly adds a forbidden value to the key |
| CT3b | **Required-component sensitivity, incl. engine version (K6)** | Changing the recorded engine version — or any other required identity field — changes the key or renders the entry unusable; an entry whose engine version is **unknown** is never returned. This is the direction that must *not* be inverted |
| CT4 | **Immutability test** | A returned cached value cannot be mutated by a caller; mutating the caller's view does not corrupt the entry; and **mutating the storage that backed a published input's identity fails explicitly** (§6.4 N4–N7, §9.2 TL3) |
| CT5 | **Methodology-version test** | A methodology-version change invalidates the relevant entries (miss), and never returns a stale result. **Also covers the §7.6 pinned-version tag:** an entry tagged with a different QuantLib version/configuration is a **miss** (this is the cache-compatibility half of §3.2's invariant, and it does **not** require any result-DTO field) |
| CT6 | **Eviction-safety test** — **ordinary entries only** | Eviction cannot change a result (I3). **Calibration is excepted**: evicting and recomputing yields a *new instance* by design (§8.3.1, CT9) |
| CT7 | **No-QuantLib-object test** | No cache value is, or contains, a QuantLib-typed object or handle (§7.3) — enforced by a type-level check where possible |
| CT8 | **Fingerprint-trust test (§7.4.1)** | A payload whose **declared** `content_fingerprint` does not match the fingerprint recomputed from its own bytes never yields a cache **hit**: it is refused with a reason and never priced. Also asserts that an **unknown schema version** can never reach the L1 cache, i.e. that no lookup precedes fail-closed decode. This is the test that would catch a fail-open regression in L1 |
| CT9 | **Calibration instance-identity test (§8.3.1)** | A calibration cache **hit** returns the **original** `ModelCalibrationResult` content **unchanged** — including its original `calibrated_at`, `calibration_result_id`, provenance, warnings and replay fingerprint; **no** fresh timestamp or identity is fabricated on a hit. A forced **recompute** of the same input under the same complete key is a **new instance** and is permitted to differ in exactly those run-instance provenance fields, while preserving the contract-defined calibration/economic semantics and deterministic fields. Requires the concrete calibration methodology, so it is **not** #227's (§9.2 TL5, §5.7 tiers) |

**PROPOSED:** CT3 and CT3b are deliberately *opposite* in direction, and both are required. A suite with only one of them can be satisfied by a cache that is simultaneously over-keyed (CT3 would fail) or under-keyed (CT3b would fail) in a way the other test cannot see. **CT8 is separate again**, because it tests neither key sensitivity nor key invariance but the *trust* placed in a key component: a cache can have a perfectly composed key and still be wrong if it computes that key from a value the caller supplied.

---

## 8. Calibration cache policy

### 8.1 Why it is separate

**PROPOSED.** Calibration is kept in its **own** cache namespace, with its own identity, because it differs from ordinary pricing caches in three ways that make a shared mechanism unsafe:

1. Its inputs include **methodology choices** (objective, optimizer, tolerances) that are RED items, not ordinary data.
2. Its result may be **non-converged**, which is a *distinct* outcome from "no result", and must not be conflated with success.
3. Its cost is typically far higher, so an incorrect hit is far more damaging, and a thundering herd is far more expensive.

**PROPOSED:** calibration entries are never returned by, nor looked up in, the ordinary pricing cache; the two namespaces do not share keys.

### 8.2 Identity participation

**PROPOSED.** A calibration cache key includes **all** of the following. Omitting any one is a defect:

| # | Component | Note |
|---|---|---|
| CC1 | Calibration **input fingerprint** | The canonical `MODEL_CALIBRATION_INPUT_V1` content identity (#225 §15.2) |
| CC2 | **Market snapshot identity** | Per #225 §6 — the snapshot the calibration saw |
| CC3 | **Curve identity** | Including construction methodology id/version (L3) |
| CC4 | **Volatility input identity** | Including vol unit/type/smile-model identity (#225 §9) |
| CC5 | **Model id + version** | Model identity is not inferable from data |
| CC6 | **Engine version** | K6; and the QuantLib version (calibration paths are sensitive to optimizer implementation) |
| CC7 | **Objective / optimizer / convergence-policy identity** | These are RED-governed methodology items; they must be *named* in the key even though their *values* are unresolved (§8.4) |
| CC8 | Any other **contract-represented methodology identity** | Anything the #225 contracts name as methodology, per its "methodology versions evolve independently from wire-schema versions" rule |

**PROPOSED:** CC7 is deliberately phrased as *identity* participation. The key must be able to distinguish two calibrations performed under two different objective/optimizer policies. This does **not** require choosing those policies, and choosing them is prohibited here (§13.2).

### 8.3 Outcome policy

**PROPOSED.** Calibration outcomes are explicitly typed, and only one of them is cacheable as a reusable success:

| Outcome | Cacheable? | Policy |
|---|---|---|
| **Converged, no warnings** | **Yes** — cacheable as a success | Reusable under the full key, **as the original immutable result instance** (§8.3.1) |
| **Converged with warnings** | **NOT reusable from cache until warning stability is proven.** The *numeric* outcome may be stored, but a cached entry may **not** be served as a hit | The warnings are part of the result and must not be discarded — but if two runs with identical inputs can produce **different warning sets** (warning-set stability is **UNPROVEN**: §8.6 **U-L**, and the **TL5 warning-stability test** is what discharges it — TL5 **does** require that test (§9.2 TL5)), a cache **hit** would return an earlier run's warnings while an uncached run returns the current ones. That breaks I3's hit/miss equivalence and the end-to-end warning semantics promised below. A warning-bearing calibration therefore stays **non-reusable** until **U-L** is discharged by a deterministic test proving the warning set is a function of the key. A caller must still see the warnings — produced by the run that returns them, not replayed from an entry |
| **Failed calibration** (numeric failure, invalid input, exception) | **No** | Not cached as a reusable success (I6). A failure is reported, not memoized as if it were a result |
| **Non-converged result** | **No, not as a success.** Per #225 §16 rule 15, a non-converged calibration may not be consumed without an explicit RED-approved override | Must not be silently cached, and must not be silently reusable |
| **Refused** (`UNRESOLVED_METHODOLOGY`, missing input) | **No** | A refusal is cheap to reproduce and must stay visible |

**PROPOSED:** the distinction between "converged with warnings", "non-converged", and "failed" is preserved **end to end** — through the cache, the DTO result, and its **result diagnostics (Lane A**, §12.2). Collapsing them would destroy exactly the information #225 §16 rule 15 relies on.

### 8.3.1 Calibration instance identity and the hit/recompute invariant

**PROPOSED — owner decision.** `MODEL_CALIBRATION_RESULT_V1.calibrated_at` is the timestamp of the calibration run that **actually occurred**, and it participates in the result instance identity and fingerprint. A cache hit must therefore **not** manufacture a new `calibrated_at`, `calibration_result_id`, provenance envelope, or result fingerprint. The locked rule is:

> A reusable calibration-cache entry is the **complete immutable `ModelCalibrationResult` instance** produced by the original successful calibration run. A cache hit returns that original result instance content **unchanged** (a value copy or other immutable representation is fine), including its original `calibrated_at`, `calibration_result_id`, provenance, warnings, and replay fingerprint.

Consequences:

1. A hit truthfully means *"reuse this prior calibration result"*; it does **not** claim that a new calibration ran.
2. A forced miss or recomputation with the same calibration input is a **new calibration instance**, and may legitimately carry a different `calibrated_at`, `calibration_result_id`, provenance and result content fingerprint.
3. **I3 is narrowed accordingly** (§7.5). I3's "a hit and a miss must produce identical output" is a rule about *ordinary deterministic derivations*; it must **not** be read as requiring instance-identity equality between a cached calibration result and a later recomputation of the same input.
4. **Calibration-specific equivalence invariant** — replacing raw instance equality with the property that actually matters:
   - a cache **hit** returns the exact cached result content **unchanged**;
   - a **recomputation under the same complete cache key** must preserve the contract-defined calibration and economic semantics, and every deterministic field required by the owning calibration methodology and its tests — but **run-instance provenance fields (`calibrated_at`, `calibration_result_id`) are explicitly exempt from equality**;
   - **no fresh timestamp or identity may be fabricated on a hit.**
5. Warning-bearing results remain **non-reusable** until the warning-stability gate (§8.6 **U-L**) is discharged.
6. Failed, refused and non-converged outcomes remain governed by §8.3's existing policy; this decision does **not** make them reusable.

**PROPOSED — scope.** This is an architecture/cache/provenance decision. It selects **no** pricing or calibration methodology value and changes **no** #225 schema (§8.4, §13.2).

### 8.4 Unresolved methodology must stay unresolved

**PROPOSED.** The following are **not** chosen, defaulted, or inferred by this document or by the engine:

- the calibration **objective**;
- the **optimizer** and its configuration;
- **convergence tolerances** and iteration limits;
- any **fallback** to a QuantLib default when a policy is unspecified.

These are RED-governed (§13.2). **PROPOSED:** until they are resolved, an engine that is asked to calibrate with an unspecified policy must **refuse with `UNRESOLVED_METHODOLOGY`** (X3), which also means the calibration cache is *keyed but not productive* — it cannot serve a hit for a policy that has no identity. This is the intended, honest state.

### 8.5 Concurrency

**PROPOSED.** Under the initial posture (§5.2), calibration runs inside the serialization gate, so concurrent duplicate calibration requests cannot execute simultaneously **within a process**. The policy must still be stated, because it must remain correct if that changes:

| # | Rule |
|---|---|
| CR1 | Concurrent duplicate requests for the same key: it is **acceptable to recompute**, and unacceptable to serve a torn or partially-populated entry (I4) |
| CR2 | There is **no** "in-flight" placeholder that a second requester can observe as a completed result. A requester either gets a complete entry or computes its own |
| CR3 | Publication is atomic and last-writer-wins **only** between logically equivalent entries (same full key ⇒ equivalent result). **For calibration, "equivalent" means §8.3.1's invariant — NOT instance identity**: two runs of the same input may legitimately differ in `calibrated_at` and `calibration_result_id`. **The "⇒" is an obligation on the entry type, not an assumption**: for a warning-bearing calibration it is exactly what §8.6 **U-L** leaves unproven, which is why §8.3 makes such entries non-reusable and CR4 must therefore never return one |
| CR4 | A calibration entry is only returned to a caller if its full key matches, including CC7 identity — **and the entry is of a reusable kind**. A warning-bearing entry (§8.3) is **never** returned from the cache, key match notwithstanding, until §8.6 U-L is discharged |
| CR5 | Cross-process calibration sharing is **not** designed here (L8) |

### 8.6 Calibration UNPROVEN

| # | UNPROVEN |
|---|---|
| U-K | Whether calibration is worth caching at all in realistic use (measurement, §10) |
| U-L | Whether "warning" sets are stable enough to reuse an entry |
| U-M | Whether the eventual objective/optimizer choices make keys comparable across engine versions |

---

## 9. Test architecture

### 9.1 Framework and layout

**PROPOSED — framework: GoogleTest.** Issue #226 allows "GoogleTest or Catch2, or another explicitly approved equivalent". GoogleTest is selected because: it is the framework the pinned dependency manager already carries a port for (so it does not add a second acquisition mechanism); it has first-class CTest integration including XML/JUnit output; it provides **death tests** and typed/parameterised tests, which the defaults-policy matrix (§3.6 D1–D17) and the cache-key matrix (§7.7 CT2/CT3/CT3b) need in order to be exhaustive rather than sampled; and it is the framework the sanitizer CI jobs (§11.6) are best understood with. **Trade-off recorded:** Catch2 needs no build step and reads more fluently for small suites; the deciding factor is the matrix tooling, not aesthetics. Switching later is possible but would touch every test file, so the choice is made now, once.

**PROPOSED — layout.** Two authorities are combined here and must not be confused: the **production paths reproduce §2.3 verbatim** (which is the locked layout), and the **`tests/` subtree is identical to §2.9** (which is the canonical test layout). §9 adds no third tree.

```text
cpp/rates_engine/
  include/shiori_rates/     PUBLIC headers: dto/, engine/, diagnostics/   (§2.3)
  src/dto/                  contracts + canonical serialization (no QuantLib)
  src/quantlib_adapter/     the ONLY QuantLib-dependent target
  src/engine/               production pricing kernel (no QuantLib)
  src/diagnostics/          runtime telemetry + diagnostics emitters (Lane B, §12.2)
  tests/                    identical to §2.9, which is canonical
    unit/                   per-component logic; pure logic, no QuantLib
    dto/                    JSON round-trip + schema-version + fail-closed (TL0)
    adapter/                the ONLY test area that may use QuantLib directly
    defaults/               QuantLib default-audit matrix, §3.6 D1–D17 (TL1)
    determinism/            replay identity, §9.5 / #225 §15.4 (TL3)
    cache/                  §7.7 CT1–CT8 + CT3b (TL4)
    calibration/            §8.3 outcome preservation (TL5)
    concurrency/            §5.7 T1–T8 (TL6) — quarantined per §9.8
    benchmark/              §10.8 correctness-checked benchmarks (TL7)
    regression/             ports of docs/31 §3.1 authoritative anchors
    parity/                 Python ↔ C++ boundary, §9.3 PB1–PB6
    fixtures/               synthetic, identity-stamped fixtures
  benchmarks/               §10 (separate executable, §2.10)
```

**PROPOSED — why the production lines are repeated rather than omitted.** An earlier draft of this block listed the production directories at the *root* of `cpp/rates_engine/` as `core/`, `dto/`, `quantlib_adapter/`, `diagnostics/`. That was a **competing layout**: §2.3 locks those under `include/shiori_rates/` and `src/`, and names the kernel target `engine`, not `core`. Since the document tells #227 not to reopen the layout, two trees would have left both the implementation paths and the §3.9 E1a include allow-list ambiguous. The production lines are therefore reproduced from §2.3 exactly — and if §2.3 and this block ever diverge, **§2.3 is authoritative**.

**PROPOSED:** the `tests/` list is not re-derived here. It is §2.9's list, reproduced so a reader of §9 sees the same directories; if the two ever diverge, **§2.9 is authoritative** and this block is the stale one.

### 9.2 Test layers

**PROPOSED.** Each layer exists to make a specific #226 decision *falsifiable*. A decision without a test is a preference.

| Layer | Tests | The #226 decision it protects |
|---|---|---|
| **TL0 Contract / schema** | Version decode, round-trip, unknown-version refusal, `NULL_WITH_REASON` preservation, canonical-form stability | §6.5 B4/B5, AC 6 |
| **TL1 Defaults audit** | One test per row of §3.6 D1–D17: assert the engine **either sets the value explicitly from an owner-approved input *or* refuses with `UNRESOLVED_METHODOLOGY`** — never silently proceeds on a default — **and pin the effective value** so a QuantLib upgrade that changes a default produces a test failure rather than a changed price. The wording matches §3.6's own (a)/(b) contract deliberately: requiring a *set* would be **unsatisfiable** for D9 and D14, whose owner-approved values do not yet exist, and the only ways to make such a test green would be to invent methodology or to leave the suite permanently red | §3.6, AC 7 — this is the mechanical enforcement of "QuantLib defaults MUST NOT silently become Shiori methodology" |
| **TL2 Boundary isolation** | Structural no-QuantLib-type assertion per §3.9 **E2** (syntax-aware — a compile-fail test alone is insufficient), supplemented by the `cpp-isolation-guard` CI job (§11.3) | §3.5 A1–A5, §3.9 E2, §6.5 B2, AC 6 |
| **TL3 Determinism / replay** | Same input twice ⇒ identical output; fingerprint stability; no clock/global influence; **alias mutation of identity-bearing storage after fingerprint publication is an explicit failure case** (§6.4 N4–N7) | §6.7, AC 11 |
| **TL4 Cache correctness** | §7.7 CT1–CT8 + CT3b | §7, AC 12/13 |
| **TL5 Calibration outcomes** | Converged / warnings / non-converged / failed / refused are distinguishable and survive the cache — **plus the warning-stability test** that discharges §8.6 **U-L**: identical inputs produce an identical warning set, which is what the §8.3 "converged with warnings" rule waits on before such an entry may ever be reused. **#227 cannot discharge this half.** #227 has skeleton/DTO scope only, and there is no approved calibration objective, optimizer, tolerance, or concrete calibration implementation (`RED-225-*`, §8), so a mock or refusal-only test proves nothing about the real path's warning behaviour — and treating it as proof would allow warning-bearing entries to be served from cache without that implementation ever being validated. The **distinguishability** half is #227's (a DTO/cache-shape property); the **stability** half is required **in the change that introduces the concrete calibration methodology**, and **U-L stays open until then** | §8.3, §8.6 U-L, AC 14 |
| **TL6 Concurrency correctness** | §5.7 T1–T8 — **tiered**: Tier S (T4, T5, T7) is #227's now; Tier K (T1, T2, T3, T6, T8) requires the #229 kernel | §5, AC 16 — **the sole gate for AC 10** |
| **TL7 Benchmark correctness** | §10.8 — every benchmark **that #227 implements** validates its result | §10, AC 17/18 — **tiered**: only non-price-bearing phases are #227's (§10.3) |
| **TL8 Failure / fail-closed** | Every refusal path returns a reason; nothing silently degrades | §6.8, §13 |

### 9.3 Python ↔ C++ boundary tests

**PROPOSED.** The cross-language boundary is tested as a **contract**, not as an implementation detail:

| # | Test |
|---|---|
| PB1 | A fixture serialized by Python decodes in C++ to the same semantic values, and vice versa (round-trip across the language boundary) |
| PB2 | The C++ decoder refuses an unknown `<CONTRACT>_V<n>` before reading content (AC 6 / §6.5 B4) |
| PB3 | `NULL_WITH_REASON` and `UNRESOLVED_METHODOLOGY` survive the crossing **unchanged** — never collapsed to null/absent |
| PB4 | A fingerprint computed on one side matches the other side's computation for the same input |
| PB5 | **No QuantLib type appears in any serialized form** — enforced structurally (a generated type list, or a build-time check), not by review |
| PB6 | The existing Python suite keeps passing with the C++ artifacts absent (the engine is optional to the Python app) |

**PROPOSED:** PB5 is deliberately *structural*. A review-based check would decay; a generated/compile-time check cannot be forgotten.

### 9.4 Determinism and isolation rules for tests

**PROPOSED.**

| # | Rule |
|---|---|
| TD1 | Every test that touches QuantLib **sets an explicit evaluation date**; no test may depend on the machine date. **Sole exception:** §5.7 **T4**'s raw-null case deliberately *starts* from the unset state, and must then prove the raw-null / clock-following state is restored (§5.5) |
| TD2 | Tests are **order-independent**. A test that only passes in a particular sequence is a defect in the test or in hidden state — and given §4.2, the likely cause is process-global leakage |
| TD3 | No test performs network I/O, reads a live feed, or requires Bloomberg |
| TD4 | Fixtures are **versioned and identity-stamped**; a fixture change is an explicit, reviewed change |
| TD5 | Any tolerance used by a test is **documented and justified**; tolerances are never widened to make a test pass |
| TD6 | Randomised paths use a **fixed, recorded seed** |
| TD7 | Tests must not silently leak global state: a teardown asserts restoration (§5.7 T4) |
| TD8 | Release and debug configurations both run the suite; a test that depends on `assert` being enabled is a defect (§5.6) |

### 9.5 Reference-implementation parity (obligation inherited from `docs/08`)

**OBSERVED.** `docs/08_performance_engine_backend_strategy.md` requires that "every accelerated backend must match a reference implementation within documented tolerance", and that no performance claim be made without a benchmark.

**PROPOSED.** The obligation is honoured **structurally** by TD1–TD8 plus a fixture set that is *shared* between the Python and C++ planes, so parity is testable. **Important scope limit:** parity is asserted **at the DTO/numeric boundary**, and only for behaviour the approved contracts actually define. Where no reference behaviour exists yet — because the pricing methodology is unresolved (§13.2) — **no parity test and no expected value is invented**. A missing reference is recorded as missing; it is never replaced by a QuantLib output. This is the specific mechanism that prevents `docs/08`'s parity rule from becoming a back door that legitimises QuantLib defaults as Shiori results.

### 9.6 Concurrency and cache test requirements

Fully specified in **§5.7 (T1–T8)** and **§7.7 (CT1–CT8 + CT3b)**. Summary of the gating logic: **TL6 is the only evidence that can satisfy AC 10**, and TL4/CT2/CT3/CT3b are what make AC 13 auditable rather than aspirational.

### 9.7 Bloomberg / UAT fixture integration (future)

**PROPOSED.** The issue asks that the strategy allow later Bloomberg/UAT fixture integration without an architectural rewrite. The design accommodates it as follows:

| # | Rule |
|---|---|
| BF1 | Fixtures are **opaque, versioned blobs plus a metadata sidecar**. The test harness reads the metadata for identity and never reaches into the fixture for market conventions |
| BF2 | A future captured market snapshot is integrated by **adding a fixture**, not by adding a live call. This preserves §6.3 |
| BF3 | Captured data is treated as **data**, never as authority for a convention or methodology value. A capture that would settle a RED item is escalated, not adopted (§13.3) |
| BF4 | Fixture provenance (`source`, capture date/`captured_at`, system) is recorded as **explicit metadata** and **does participate in identity** — but only through the approved #225 canonical identity, i.e. `snapshot_id` and the canonical content fingerprint (`docs/33` §6.3 identity rules, §6.4 embedding vs referencing, §15.4 replay identity). **Correction recorded:** an earlier draft of this rule required provenance to "never participate in a cache key". That was a defect, not a hardening — it would let two *distinct* explicit snapshots share a key and return a result carrying the wrong `inputs_fingerprint`. What §7.2 K3 actually forbids is **ambient** derivation of time or state (reading the system clock), not a caller-supplied capture timestamp that is already part of the declared input. The K3/K4 distinction is "implicit vs explicit", and provenance here is explicit |
| BF5 | Consequently, provenance is part of the **cache key only via that canonical identity** — never as free-form, un-hashed, or host-derived text, and never as a second, parallel notion of snapshot identity that #225 does not define |

**PROPOSED:** BF3 is the load-bearing rule. It is what allows a captured desk snapshot to be *reproduced* without letting a workstation convention silently become Shiori methodology.

### 9.8 Quarantine policy for not-yet-enabled tests

**PROPOSED.** The concurrency suite (§5.7) must **exist** from the start. "Quarantine" means exactly two things, and **must not** be read as a third:

1. the suite does **not** run as part of the default/gating configuration; and
2. **no claim of concurrency safety** may rest on it.

It must **not** mean "the suite cannot be executed". An earlier draft said the suite "must not run in the default configuration until §5.8's preconditions hold", which is **circular**: §5.8 PE1 requires the concurrency suite to *pass* — **Tier S now, Tier K once #229 supplies the kernel** (§5.7 tiers), so if the suite cannot run until PE1 holds, the very evidence PE1 demands can never be produced, and the §11.3 jobs that are supposed to run these tests would contradict this section. The resolution is an explicit evidence-only opt-in:

| # | Rule |
|---|---|
| Q0 | The suite is runnable in an explicit **evidence-collection mode** — a dedicated CMake option plus a CTest label (e.g. `-DSHIORI_EVIDENCE_RUNS=ON` and `ctest -L concurrency-evidence`), which the §11.3 jobs invoke to produce PE1's evidence. **Quarantine governs the default configuration and the claims made, never the ability to gather evidence** |
| Q1 | In the **default** configuration, quarantined tests are **listed in the test executable** and reported as skipped with a reason naming §5.8. In evidence mode the same tests run and report normally, **except Tier K, which is governed by Q7, and T8, which is governed by Q6** |
| Q2 | A skip count of zero is **not** evidence of correctness; the suite's status is reported in CI output and **Lane B** telemetry (§12.2) |
| Q3 | Nothing may be described as "concurrency-safe" on the basis of a skipped test |
| Q4 | When a test is running and applicable, a **failure is a blocker**, never a flake to be retried (§11.8) |
| Q5 | Evidence gathered in evidence mode is valid **only as input to §5.8's gate**. It never by itself changes the production posture: §5.2 stays DISABLED until PE6 (an owner decision). A green evidence run is a precondition, not an enablement |
| Q6 | **T8 is not-applicable until a candidate configuration exists.** T8 is defined as a rerun *"with the gate removed or narrowed exactly as the enablement proposal would"* (§5.7), so it has no meaning before such a proposal. Evidence mode therefore runs **T1–T7**; T8 is reported **not-applicable with that reason**, and is **not** a green-gate requirement. T8 becomes **required and blocking in the change that submits an enablement proposal**, naming the candidate configuration it exercised. This is what keeps §11.3's *required* jobs green without #227 inventing an unapproved parallel configuration |
| Q7 | **Tier K is not-applicable until #229 supplies the kernel.** T1, T2, T3, T6 and T8 all price a `RatesKernelInput`, which `docs/33…225.md` `:1324–1327` assigns to **#229**, not #227. Evidence mode therefore runs **Tier S (T4, T5, T7)** in #227 and reports Tier K not-applicable with a reason; Tier K becomes required in the change that lands the kernel (§5.7 tiers, §11.3) |

**PROPOSED:** Q0 and Q6 together are what make the architecture self-consistent. Q0 prevents the circularity in one direction (the suite must be runnable to produce the evidence PE1 needs); Q6 prevents it in the other (T8 must not be required before the artefact that defines it exists). Without Q6, the §11.3 jobs would have to be permanently red, or #227 would have to fabricate a candidate configuration purely to satisfy CI — which would be worse than naming the conditional explicitly, because an invented configuration is exactly the "enablement by accident" this document exists to prevent.

### 9.9 What the test suite must not do

**PROPOSED.** No test may: select a RED methodology value; invent an expected price for an unresolved product; call the network or a live feed; assert a QuantLib default as the expected Shiori behaviour; or pass only by loosening a tolerance. Each of these is a defect that would convert a governance rule into a test that certifies the opposite.

---

## 10. Benchmark methodology

### 10.1 The governing rule

**PROPOSED.**

> **Never publish one blended number that hides setup and pricing cost.** A timing is publishable only with the phase decomposition, the cache state, the concurrency mode, and the reproducibility metadata of §10.5. A single "swaps per second" figure is **not** a deliverable of this architecture.

**PROPOSED rationale:** the issue requires measurement of DTO decode, object construction, curve/model materialization, pricing kernel, risk and calibration *separately* (AC 17, §8 of the issue, and `docs/08`'s no-unbenchmarked-claims rule). A blended number cannot distinguish "the kernel is fast" from "the cache answered", and it therefore cannot guide any decision #227 will have to make. **This list is the target architecture, not a #227 execution set**: availability per phase is decided by §10.3, and only the non-price-bearing phases are #227's.

### 10.2 Tooling

**PROPOSED.** **Google Benchmark**, in a **separate executable** from the test binaries (§2.10), so that benchmark-only state (§7.4 L7) is *physically* unable to reach production or the test suite. Rationale for the choice: the issue names it explicitly; it supports per-phase state control, custom counters, and machine-readable output, and it is available as a pinned dependency.

### 10.3 Required dimensions

**PROPOSED.** The matrix is deliberately **not** fully populated: multi-thread rows are defined but **unused for production claims** until §5.8 is satisfied (AC 10 / AC 17) — with **one explicit exception**, because otherwise §5.8 would be unsatisfiable: an **evidence-only** multi-thread benchmark of a *named candidate configuration*, which is how PE5's evidence is produced. The exception is stated in the row below and keyed to §9.8 Q6.

**PROPOSED — benchmark availability follows implementation ownership.** #226 defines the **full** benchmark taxonomy; **definition does not mean #227 must already execute every phase**. #227 must create the benchmark **framework/harness and smoke capability** only for phases **executable without production pricing or unresolved methodology** — process/startup, DTO serialization/deserialization, and other **non-price-bearing skeleton operations that have real executable behaviour**. Pricing-kernel benchmarks are deferred to the pricing-kernel owner (no earlier than **#229**); risk benchmarks to the owning risk implementation/methodology issue; calibration benchmarks to the owning calibration-methodology issue; and any curve/model-materialization benchmark that depends on unresolved construction/model methodology to its owning issue. Every unavailable phase must be explicitly marked **DEFINED / DEFERRED**, never "available now". The full phase list remains the target architecture (§15.2 D-6, §11.3). **"Non-price-bearing" is defined here as:** the operation neither computes nor depends on any pricing, risk, calibration, curve-construction or model-selection methodology outcome — its output is infrastructure state (bytes decoded, objects constructed, process initialized), not an economic number.

| Dimension | Values | Available now? |
|---|---|---|
| **Process state** | cold start (first call in a fresh process) · warm process | Yes |
| **Cache state** | cold cache · warmed cache · cache **disabled** | Yes |
| **Phase** | DTO decode | **Yes** — #227 (non-price-bearing skeleton operation) |
| **Phase** | request-local object construction (non-price-bearing; **excludes** curve/model materialization) | **Yes** — #227 |
| **Phase** | curve/model materialization | **DEFINED / DEFERRED** — owning curve/model construction issue; depends on unresolved construction/model methodology |
| **Phase** | pricing kernel | **DEFINED / DEFERRED** — pricing-kernel owner (no earlier than **#229**) |
| **Phase** | risk | **DEFINED / DEFERRED** — owning risk implementation/methodology issue |
| **Phase** | calibration | **DEFINED / DEFERRED** — concrete calibration-methodology owner (§9.2 TL5) |
| **Concurrency** | single-thread | Yes |
| **Concurrency** | multi-thread | **DEFINED; production use BLOCKED** until §5.8. **Exception — evidence-only, keyed to §9.8 Q6:** when an enablement proposal names a candidate configuration, that configuration **may** be benchmarked (single-thread baseline vs the proposed multi-thread mode) *for the proposal's own evidence*, because §5.8 PE5 requires exactly that verification and would otherwise be unattainable — the same circularity §9.8 Q0/Q6 resolves for the test suite. Such a run is labelled **evidence-only**: it is never a published performance claim, it never appears as a production speedup, and it does **not** enable production parallelism (§5.2 stays DISABLED until PE6). A multi-threaded benchmark is additionally a *correctness verification*, not only a timing (§5.8 PE5) |

**PROPOSED:** "cache disabled" is a required *third* cache state, not an afterthought. Without it, a warm-cache number cannot be attributed, and a regression in real computation can be hidden behind a better hit rate.

### 10.4 Phase decomposition requirement

**PROPOSED.** Each phase is reported **separately and additively**, with the boundary between phases defined by §6.6's pipeline (DTO → adapter translation → request-local construction → kernel → result). Aggregate figures are permitted only as a *derived presentation* of the decomposed results, never as the primary artifact, and never without the decomposition alongside.

### 10.5 Reproducibility metadata (mandatory)

**PROPOSED.** A benchmark result is **invalid** without all of the following. This is the **AC 18** set, extended with the two items §4/§7 require, marked (+).

| # | Field | Source |
|---|---|---|
| M1 | Engine version + commit SHA | Build metadata |
| M2 | Compiler **and** compiler version | Build metadata |
| M3 | Build type (and the §2.4 flags in effect) | Build metadata |
| M4 | QuantLib version **+ the macro configuration** **+ the dependency's instrumentation state** (+) — specifically whether the dependency was built with TSan instrumentation, and on which triplet | §3.2 / §5.5 C10 — a version number alone is insufficient because §4.4 shows behaviour is macro-dependent. The instrumentation state is required because a TSan run over an uninstrumented dependency is **not race evidence** (§5.7 T3, §11.3): without it, M4 cannot distinguish an admissible TSan result from one that could not have detected the race |
| M5 | Fixture identity (versioned, §9.7 BF1) | Fixture sidecar |
| M6 | Cache state (cold/warm/disabled) | Harness |
| M7 | Thread count | Harness |
| M8 | Platform: OS + version + architecture, **plus host identity** — and these are two different things. (i) **Class attributes**: CPU vendor/model, physical core count, memory class, and physical vs container vs shared CI runner (with the runner label/image). (ii) **Host identity — the part that makes a comparison legitimate at all**: the actual host (hostname / instance id) **and** its boot or session id (for hosted CI, the concrete run/job id that pins the machine). **Class attributes are *not* unique identity**: two GitHub-hosted runners of the same class match on *every* class attribute while being different physical machines, so class attributes alone cannot make BM3's prohibition enforceable | Harness |
| M9 | Warm-up policy | Harness |
| M10 | Repetition policy | Harness |
| M11 | **Concurrency mode (configured)** *and* **observed gate contention** (+) — these two halves have different comparability roles. The **configured mode** (serialized / N threads / gate removed) is part of the comparability set. **Observed contention is a measured outcome, not a configuration**, so it is **excluded** from the equality requirement (§10.7 BM3) and is reported instead | §5.2.1 / §12 — required so a serialized number is never mistaken for a parallel one |

**PROPOSED:** M4 and M11 are the two *added* fields this document contributes to the issue's list, and both are forced by evidence: M4 by §4.4 (macro-dependent class layouts and semantics), M11 by §5.2.1 (an explicit gate whose cost must be visible). **M8 is extended rather than added** — the issue lists "platform", and this document makes that field carry machine identity as well, because §10.7 BM3's prohibition on cross-machine comparison is otherwise unenforceable by its own metadata.

**PROPOSED — why M11 is split.** Gate contention is measured by *acquisition counts and wait times*, which vary with scheduling and machine load even between two runs of the **same** build with the **same** configured mode. Treating it as an equality prerequisite would therefore make two otherwise perfectly controlled runs formally incomparable, and would be self-defeating: the metadata that exists to make comparisons trustworthy would forbid them. The rule is that **configuration is compared and measurement is reported** — the same distinction §10.7 BM1 already draws between a point estimate and its dispersion.

### 10.6 Cache-state semantics

**PROPOSED.** Cache state is reported as an **enumeration**, never as prose: `COLD` · `WARM` · `DISABLED`. A benchmark that cannot state its cache state is not reported. Additionally:

| # | Rule |
|---|---|
| CS1 | `WARM` requires a documented population procedure (which keys were primed, by what input), not merely "ran twice" |
| CS2 | `DISABLED` must genuinely bypass the cache, and is the only state that measures real computation |
| CS3 | A regression is only comparable across runs that match on **M2–M11** — with **M1 (engine version / commit SHA) the intentional comparison variable** (§10.7 BM3), and with **M11 matched only on its *configured* concurrency mode**. M11's other half is **observed gate acquisition/wait contention**, which is a *measurement* that varies with scheduling and load even between two runs of the same build; it is therefore **reported, never matched** (§10.5–§10.7 BM3). An earlier draft made CS3 require all of M2–M11 to match, which would have declared two otherwise perfectly controlled runs incomparable |

### 10.7 Statistical and comparison policy

**PROPOSED.** These rules exist so that a "speedup" cannot be manufactured:

| # | Rule |
|---|---|
| BM1 | Report **dispersion**, not a single point estimate (e.g. median **and** spread). A mean without spread is not publishable |
| BM2 | Repetition and warm-up policies are fixed and recorded (M9/M10) |
| BM3 | Two runs are comparable only if **M2–M11** match (compiler, build type, QuantLib version + macros, fixture identity, cache state, thread count, platform **including the M8 machine identity**, warm-up, repetition policy, **configured** concurrency mode). M8 carries machine identity precisely because OS + architecture cannot detect a machine change: without it, two runs on different CPUs or different CI runners would satisfy every M2–M11 equality check, and this rule's own prohibition would be unenforceable. A **machine mismatch is therefore itself a comparability failure**, not merely an unclaimed comparison. Because M8 records **host identity** in addition to class attributes, comparability requires that baseline and candidate were measured **on the same recorded host session** — co-located, i.e. the same host and boot session (for CI, the same job, run sequentially). Where that is genuinely impossible, comparability may be claimed **only at hardware-class level** and must be **labelled as such** (“same class”); a class-level comparison must never be presented as a machine-level one, and no claim may be extrapolated from one class to another. **Two fields are deliberately excluded from the equality requirement:** **M1 — engine version / commit SHA — is the intentional comparison variable and is *expected* to differ** (a proposed performance change is *defined* by a different M1, so requiring M1 to match would make every improvement claim and every regression comparison impossible), and **M11's *observed* gate contention is a measurement, not a configuration**, so it is compared only for reporting, never as a precondition. Cross-machine comparison is **explicitly not claimed** |
| BM4 | Outlier removal, if any, must be **pre-declared and identical** across the compared runs; post-hoc trimming to obtain a desired result is prohibited |
| BM5 | A claimed improvement requires a **threshold and a noise estimate** stated in advance. "Faster by an unspecified amount" is not a claim |
| BM6 | **No benchmark may gate a build on an absolute wall-clock threshold.** Performance gates on shared CI runners are inherently flaky; the correct gate is correctness plus relative-regression detection with a generous, documented band |
| BM7 | Bit-identical reproducibility across runs is **not claimed** (§2.8). Determinism is claimed at the *result* level, not the timing level |

### 10.8 Correctness-checked benchmarks

**PROPOSED.** Every benchmark **that exists also validates its output** against a fixture expectation (L7). Benchmarks for **DEFERRED** phases (§10.3) are **not written by #227 at all**, so this rule has nothing to validate for them until their owning issue lands. A benchmark that measures a wrong answer quickly is worse than no benchmark, and — given `docs/08`'s parity requirement — a timing without a correctness assertion would be exactly the "claim performance improvement without a benchmark" failure the repository already prohibits. Where no correct expected value can exist yet, the benchmark must **refuse to report a timing** rather than report one whose correctness is unknown.

### 10.9 Separation from production

**PROPOSED.** The benchmark executable is a separate target (§2.10); benchmark-only caches (§7.4 L7) are unreachable from production and test code by **link-level** separation, not convention. A speedup obtained only under benchmark-only caching is not a production result, and the metadata (M6) must make that visible.

### 10.10 What may not be claimed

**PROPOSED.** No performance claim may be made without a benchmark (per `docs/08`). No claim may be extrapolated across platforms, compilers, build types, cache states or concurrency modes. **No benchmark result may be used as evidence of correctness** — that is the role of §9's suite, and specifically of §5.7 T1–T8 for concurrency (T1–T7 for the gated posture, T8 for the candidate configuration).

### 10.11 CI treatment

**PROPOSED.** CI runs a **benchmark smoke test** (does the benchmark build and complete a minimal run, and do its correctness checks pass?) on every C++ change, and does **not** attempt a statistically meaningful full benchmark run (AC from issue §9: "benchmark smoke vs full benchmark separation"). **The smoke test covers only the phases #227 actually implements** (§10.3): it must **not** require fabricated pricing, risk, or calibration behaviour merely to turn green, and deferred phases are simply absent from the smoke set until their owning issue lands. Full runs are performed deliberately, on a recorded machine, and published with §10.5's metadata. This keeps the CI signal about correctness while preserving the ability to measure.

---

## 11. Python + C++ CI strategy

### 11.1 OBSERVED baseline

Recorded in §1.2. In summary: two workflows exist — `test` (ubuntu-latest, Python 3.11, installs `requirements.txt` + `-e ".[quant]"`, installs Chromium, runs `pytest` under the default `testpaths = ["tests"]`) and `windows-launcher-smoke` (windows-latest, copies the repo to a path containing spaces **and non-ASCII characters**, and runs the launcher through `cmd`). **Neither workflow declares `paths:` filters, and neither uses dependency caching.** The protected-path set is enforced by the automation workflows (§1.2), not by branch protection.

### 11.2 Preservation rules

**PROPOSED — non-negotiable.**

| # | Rule |
|---|---|
| PR1 | **Existing Python tests remain mandatory.** No change may make `pytest` optional, conditional on the C++ result, or skippable for a C++/contract change |
| PR2 | The **Windows launcher smoke test** remains, including its space-and-non-ASCII path, because that is a real user-facing constraint (§1.7) |
| PR3 | The **validated bond-option path remains protected** (AC 20): no new workflow may alter it, and no C++ target may link it |
| PR4 | The existing workflows' **behaviour is preserved**; additions are additive |
| PR5 | No workflow may be made to depend on a secret that is absent on fork PRs, which would silently turn a required job into a no-op |

### 11.3 New C++ jobs

**PROPOSED.** Additions to CI, matching the compiler matrix of §2.5:

| Job | Runner | Config | Purpose |
|---|---|---|---|
| `cpp-build-test-windows` | windows-latest (MSVC) | `ci-release` | Primary toolchain; proves the static-link QuantLib path (§2.6); and **runs the C++ test suite on Windows, including the platform-applicable Tier S concurrency tests (T4, T5, T7)** so that the skeleton-executable half of §5.8 PE1 is actually satisfiable. **Tier K (T1, T2, T3, T6, T8) is deliberately *not* in #227's required set**: every test in it prices a `RatesKernelInput`, which `docs/33…225.md` `:1324–1327` assigns to **#229**, so requiring it here would force #227 to add out-of-scope pricing, invent a `RED-01` methodology value, or leave a required job permanently red (§5.7 tiers, §9.8 Q7). Tier K becomes required — on both platforms — in the change that lands the kernel. **T8 is additionally gated on a candidate configuration existing** (§9.8 Q6) |
| `cpp-test-linux` | ubuntu-latest (GCC **and** Clang) | `ci-debug` + `ci-release` | Portability + both build types; runs the **full skeleton-executable suite (Tier S: T4, T5, T7, plus §7.7 `CT1–CT8`)**, and **the full Tier K set (T1, T2, T3, T6, T8) once #229 supplies the kernel** |
| `cpp-sanitizers` | ubuntu-latest (Clang) | ASan/UBSan, and **TSan** (Linux-only) with a **TSan-instrumented QuantLib** built from source on a dedicated triplet | Memory and race defects that equivalence tests cannot see; owns **§5.7 T3**, which is a Linux/Clang requirement because MSVC ships no ThreadSanitizer. **The TSan leg instruments QuantLib itself, not only Shiori** (§5.7 T3): the job builds the dependency from source with `-fsanitize=thread` on a dedicated triplet and must **not** draw that dependency from the ordinary binary cache — the instrumented build carries its own cache identity (§11.4). A TSan job that reused the standard prebuilt QuantLib would satisfy this row's letter while being unable to detect a race inside the library, which is the entire point of the leg |
| `cpp-isolation-guard` | ubuntu-latest | n/a (static analysis only) | **The mechanical enforcement of §2.3 and §3.9.** Fails the build on (a) a `ql/` include outside the **production** adapter location `src/quantlib_adapter/` **or** E1a's **exactly two** allow-listed **test** paths (`tests/adapter/`, `tests/defaults/`) — two test paths, not three, in **either** delimiter form, and (b) a QuantLib type in a public header or exported signature under E2's **syntax-aware** check. §2.3 states the architecture is "only real if a build fails when it is violated" and §3.4 calls it "enforced by the target graph **and** the CI guard" — but the guard was described in §2.3/§3.9 while **no job existed in §11**, so neither sentence was true and a violation could only ever produce a review comment. Required on every PR |
| `cpp-benchmark-smoke` | ubuntu-latest | `bench` | §10.11 — **non-price-bearing phases only** (§10.3); must not require fabricated pricing/risk/calibration behaviour |
| existing `test`, `windows-launcher-smoke` | unchanged | — | §11.2 |

**PROPOSED — why the platform split is explicit.** Concurrency correctness is a *platform* property (the Windows build uses MSVC and the static-only QuantLib linkage §2.6 establishes for that platform; the Linux build uses GCC/Clang), so the test **must** run on both. **Linkage on Linux is deliberately *not* asserted here**: §2.6 leaves static-vs-shared to the selected triplet and forbids assuming shared linkage anywhere in the design, so this rationale rests on the **compiler and runtime**, not on an unproven linkage choice — an earlier draft called the Linux build "dynamic", which the build contract does not guarantee. Only TSan is platform-restricted. The matrix therefore delivers: **T1, T2, T4–T7 on Windows and Linux; T3 (TSan, covering T1, T2 and T6) on Linux; T8 on both platforms once — and only once — an enablement proposal names the candidate configuration it exercises (§9.8 Q6).** Every *currently applicable* concurrency test is thus executed on both platforms, and the three deliberate earlier failures are avoided: one draft required all tests on both platforms while assigning all test execution to Linux; a later draft left T8 off the Windows job while PE1 required it there; and a third made T8 unconditionally required, which no existing candidate configuration could satisfy.

**PROPOSED — the concurrency tests run in CI in §9.8's evidence mode.** They are not awaited on a gated default run, because §5.8's preconditions are not yet satisfied; CI invokes them as explicit evidence collection so that PE1's evidence can actually exist (§9.8 Q0). Evidence mode runs **Tier S (T4, T5, T7)** in #227. **Tier K (T1, T2, T3, T6, T8) reports not-applicable with a reason** until #229 supplies the kernel (§9.8 Q7), and **T8** additionally until a candidate configuration is supplied (§9.8 Q6) — so the required jobs can be green without #227 pricing out of scope, inventing a `RED-01` value, or exercising an unapproved configuration.

**PROPOSED:** the **TSan-instrumented configuration — Shiori *and* the dependency** (§5.7 T3, §11.3) — is **always built** even while the concurrency suite is quarantined (§9.8), so that enabling the suite later does not require new CI plumbing. Building the instrumented dependency is the expensive part; doing it on every run rather than only when the suite is enabled is what keeps the evidence path from rotting.

### 11.4 Dependency caching and cost

**PROPOSED.** QuantLib + Boost is an expensive build (boost is a ~26-port dependency closure, §2.6). Therefore: cache the dependency manager's binary cache keyed on **the manifest and the port versions**, not on a timestamp. **Correctness constraint:** a cache miss must produce a *correct* build, never a silently different one — so the cache key includes the pinned version and the triplet, and the built library's macro configuration is **verified** (§5.5 C10) rather than assumed from the cache. A cache that could change M4 (§10.5) is a correctness hazard, not just a speed feature. **The TSan triplet is a separate cache identity.** The instrumented dependency build required by §5.7 T3 uses its own triplet name and therefore its own binary-cache key; an ordinary non-TSan QuantLib binary must be **impossible to substitute silently**, and the rule runs in the other direction too (a TSan build must never be published as the general cache entry). Because M4 records the dependency's instrumentation state, a run whose reported dependency configuration is not the instrumented one is **not TSan evidence at all**.

### 11.5 Coverage requirements

**PROPOSED.** Debug and release are both covered (§11.3), because §5.6 identifies `assert`-dependent behaviour as a defect class and §2.4 fixes floating-point flags per configuration. Sanitizers cover the classes that neither build type detects.

### 11.6 Failure behaviour

**PROPOSED.** If the C++ configure/build/test job fails, the PR **fails**. There is no "C++ is optional" mode, no allow-failure, and no `continue-on-error` on a required job (AC 26 is about *scope*, not about permitting silent breakage). A flaky test is fixed or reported, never retried to green (§9.8 Q4).

### 11.7 Path filtering

**PROPOSED — the mandated rule, enforced:**

> **Path filters MUST NOT allow relevant C++/contract changes to bypass required validation.**

| # | Rule |
|---|---|
| F1 | Any workflow change is validated against the question: *"can a change to C++, a contract, a manifest, or a workflow itself skip a required job?"* If yes, the filter is wrong |
| F2 | A **docs-only fast path may exist only as an additional job**, never as a replacement for a required one, and never as the condition that makes a required job's absence acceptable |
| F3 | Contract-affecting files (DTO schemas, canonical-serialization code, fixture definitions, engine-version metadata) are treated as **C++-relevant**, so a change to them always triggers the C++ jobs |
| F4 | The safest initial position — and the **PROPOSED default** — is **no `paths:` filters on required jobs at all**, accepting the cost, because every filter is a bypass surface that must be re-argued whenever a file is added |
| F5 | If a filter is ever introduced, the *inverse* condition is tested at review: for each required job, name a file whose change would skip it, and show why skipping is safe |

### 11.8 Protected control plane

**OBSERVED.** The automation workflows treat `CMakeLists.txt`, `vcpkg.json`, `conanfile.py`, `conanfile.txt`, `meson.build`, `pyproject.toml`, `requirements.txt`, `AGENTS.md`, `CLAUDE.md` and `docs/12_pr_review_rubric.md` as protected basenames (§1.2).

**PROPOSED.** This is corroborating evidence for a rule this architecture already needs: **the C++ build and dependency control plane is an explicitly reviewed change.** Concretely — the manifest, the port pin, the macro configuration, and the CI workflow are all changes that require human review, and AGENTS.md rule 11 forbids an automated agent from modifying its own governance/dependency control plane. #226 therefore **documents** the control plane and **does not create it** (AC 26).

### 11.9 Permissions and no-merge

**PROPOSED.** New workflows declare least privilege (`contents: read` unless a specific need exists). **No workflow may merge** (AGENTS.md rule 12), and no workflow added for #226 may publish, tag, or alter branch protection. The merge gate remains manual (§13.5).

### 11.10 Local / CI parity

**PROPOSED.** The CMake presets of §2.2 are the single source of configuration, so `cmake --preset ci-release` locally is the same configuration CI runs. A CI job must not pass ad-hoc flags that no preset expresses; if a job needs different flags, that is a new preset, reviewed once.

### 11.11 Exact-head requirement

**PROPOSED.** Validation is meaningful only at the **exact** PR head (AC 24/25). A green run on an earlier commit is not evidence about the current one, and a review finding anchored to a superseded head does not block the current head (issue Governance: "Review findings attached only to stale heads must not block a newer clean head").

---

## 12. Diagnostics and observability boundaries

### 12.1 Principle

**PROPOSED.** #226 defines **two diagnostics lanes**, each with **one authority**:

- **Lane A — result diagnostics.** The deterministic, replay-relevant diagnostic content of the `PricingResult` / `RiskResult` result DTOs defined by #225. **Authoritative, owned by #225; #226 does not define, extend, rename, move, or remove it.**
- **Lane B — runtime telemetry.** `RATES_RUNTIME_TELEMETRY_V1`, an out-of-band operational channel **owned by #226**, which is **not** part of `PricingResult`/`RiskResult` and does **not** participate in their content fingerprints.

Neither lane may **define** execution; both only **describe** it. Enabling, disabling, or altering either lane must not change any pricing result, any cache key, or any methodology selection.

> **Diagnostics must not become a second source of pricing methodology.**

**PROPOSED — why the lanes are separated.** A single "diagnostics" channel forces one rule to cover two incompatible things: facts needed to *reproduce an economic result*, and measurements of *how this invocation happened*. The first must be bit-stable and fingerprint-participating; the second legitimately varies with cache population, scheduling, load and elapsed time. Merging them produces exactly the defect this section removes — a rule that either forbids emitting operational measurements, or drags volatile values into the result fingerprint. **An earlier revision of this section stated that diagnostics "are not part of the pricing DTO", which was wrong and would have instructed #227 to omit the field #225 requires at `docs/33_rates_market_model_result_contracts_225.md:937` and `:1014`.**


### 12.2 The two lanes

**PROPOSED.**

| Lane | Channel / carrier | Authority | Member of the result DTO? | Deterministic? | In the result content fingerprint? | Content |
|---|---|---|---|---|---|---|
| **A — result diagnostics** | #225 `PricingResult` / `RiskResult` — their `diagnostics` map, plus the enclosing `engine` / `status` / `warnings` / `errors` / `replay` content (docs/33 §12); **bounded to fields #225 already approves** — #226 creates no result field (§12.3, §12.7) | **#225** | **Yes** | Yes — deterministic output records | **Yes**, exactly as #225's non-recursive preimage rule (`:1236`) specifies | replay-relevant, result-semantic facts: leg PVs, period counts, weights, deterministic fallback facts, refusal reason, calibration outcome |
| **B — runtime telemetry** | `RATES_RUNTIME_TELEMETRY_V1` | **#226** | **No** — never a member of `PricingResult` / `RiskResult` | No — varies with cache state, scheduling, load, elapsed time | **No** | per-layer cache hit/miss, execution path served, gate acquisitions/waits/contention, timing breakdown, process/build/runtime observations |

> **#226 MUST NOT instruct #227 to omit, rename, or move #225's result-diagnostics fields out of the result DTO.** Wording in #226 that appears to do so is a defect in #226, not a licence to alter the contract.

**PROPOSED — the placement test.** *If a fact is required to reproduce or interpret the economic result, it belongs to **Lane A**, deterministically and fingerprint-participating. If it describes **how this invocation happened operationally** and can vary with cache state, scheduling, load, or elapsed time, it belongs to **Lane B** only.* No fact has two authorities: lane B may **echo a stable correlation identifier** (the result's content/inputs fingerprint, engine identity), but the result DTO remains authoritative and the echo never enters its preimage. **Deliberate exception:** dependency provenance (**G-2**) is stable and replay-relevant, but #225 approves **no** field for it, so it is **Lane B + §10.5 M4 + the §7.2 cache key**, and its result-contract placement is **U-O** (§3.2, §12.3 G-2).

### 12.3 Required fields, by lane

**PROPOSED.** Minimum set, mapped to the issue's list (AC from issue §10) and assigned to a lane by the §12.2 placement test:

| # | Field | Lane | Note |
|---|---|---|---|
| G-1 | Engine version | **A** (+ B correlation echo) | Part of #225's result `engine` identity; also required for cache validity (§7.6) |
| G-2 | QuantLib version, **and** its macro configuration | **B** — **not** a result-DTO field in #226 | §4.4 — the version alone is insufficient, but the real problem is that **this fact has no approved home**: #225 approves only `engine_name`, `engine_version`, `method` (`docs/33…225.md` `:919–922`), its **P1** forbids collapsing source + methodology + version into one field and **P4** requires provenance to be **structural** (`:191`, `:194`), and its handoff reserves DTO semantics to #225 (`:1324–1325`). So #226 must **not** overload `engine_version`, must **not** invent a field, and must **not** claim the value is machine-readable in the result. It is **required** in Lane B telemetry, in §10.5 **M4** and in the §7.2 cache key, and its **placement in the result contract is open item U-O**, pending an approved schema decision (Appendix C) |
| G-3 | Build / configuration identity (build type, compiler + version, flags that affect numerics) | **B** | A process/build observation. Independently a §7.2 **cache-key** component — that cache role is not telemetry and is unaffected by this split |
| G-4 | Cache hit/miss, **per layer**, with the key hashed | **B** | §7; hashed so keys are comparable without leaking values |
| G-5 | Selected execution path | **B** | Which layer served the result (cache vs computed; which construction path) — varies with cache state |
| G-6 | Concurrency mode + gate contention (acquisitions, waits) | **B** | §5.2.1 |
| G-7 | Timing breakdown hooks | **B** | §10.4 phases |
| G-8 | Evaluation date actually used | **A** | §4.3 — makes an accidental wall-clock fallback visible rather than invisible; it is replay-relevant, so it is a result-diagnostic fact |
| G-9 | Input fingerprint | **A** | §6.7 — #225 `replay.inputs_fingerprint`; the identity a result can be replayed from |
| G-10 | Refusal reason / `NULL_WITH_REASON` category | **A** | §6.8, §8.3 — #225 makes reason state fingerprint-participating |
| G-11 | Calibration outcome (converged / warnings / non-converged / failed) | **A** | §8.3, carried by #225's result `status`/`warnings` — preserving the distinction end to end |

**PROPOSED:** the G-numbers are an **issue-scoped checklist**, not a #226-authored schema. Where a Lane A field is defined by #225, #225's placement and naming govern; #226 records only that the fact must be emitted deterministically. **#226 adds no field to any #225 result type.** Where a fact that is *semantically* result-relevant is **not** already carried by an approved #225 field, it is **not** promoted into the result DTO on #226's authority and **not** squeezed into a field #225 already uses for something else: it is emitted through **Lane B**, §10.5 **M4** and the §7.2 cache key, and an **open item is raised for the schema decision it needs** (Appendix C **U-O**, G-2). #226 does not answer a canonical-schema question by picking an encoding — that is `RED` (AGENTS.md rule 5).

### 12.4 Schema and versioning

**PROPOSED.** Both lanes are **structured, versioned** data carried through the pinned JSON library and following #225's `<CONTRACT>_V<n>` convention — but they version **separately**:

- **Lane A** uses #225's **existing** result-contract versions. #226 **adds nothing, renumbers nothing, and does not modify `docs/33_rates_market_model_result_contracts_225.md`** (§12.7).
- **Lane B** is `RATES_RUNTIME_TELEMETRY_V1`, a #226-owned version. The earlier working name `RATES_DIAGNOSTICS_V1` is **retired**: naming this channel "diagnostics" invited precisely the conflation with #225's result diagnostics that §12.2 removes.

A consumer of **either** channel must detect an unknown version and fail closed rather than mis-parse it (§13.1). `RATES_RUNTIME_TELEMETRY_V1` is **not** part of `PricingResult`/`RiskResult` and must never be merged into them — and, symmetrically, **#225's result diagnostics must never be moved into it or dropped in favour of it**.

### 12.5 Determinism and privacy

**PROPOSED.**

| # | Rule |
|---|---|
| DG1 | **Lane A content** is deterministic for a given input over the replay-relevant fields — **G-1, G-8, G-9, G-10, G-11** (every one carried by an approved #225 field): what was computed, from what, with which engine, and why if refused — and **participates in the result content fingerprint exactly as #225 §15.4 specifies**. These describe *what happened* and must be reproducible. **Only *ambient* timestamps and other wall-clock observations are excluded** — they may exist as log annotations but must never define identity. **Contract-defined timestamps are not ambient and MUST be preserved in canonical identity:** an explicit input such as `MarketSnapshot.captured_at` **does** participate in `snapshot_id` and the canonical content fingerprint (§9.7 **BF4**; #225 §6.3 identity rules), so forbidding it would collapse two distinct snapshots onto one cache key and serve a result with the wrong input provenance. **The test is provenance, not type:** if #225's contract names the value as an input, it is identity; if the engine merely observed the clock, it is not |
| DG1a | **Lane B measurements are explicitly nondeterministic and must not be asserted deterministic.** **G-2** (QuantLib version + macro configuration) and **G-3** (build identity) are stable **per build**; **G-4** (cache hit/miss per layer), **G-5** (execution path served), **G-6** (concurrency mode + observed gate contention: acquisitions, waits) and **G-7** (timing breakdown) legitimately vary for the *same* input with cache population, concurrent load and elapsed time. Requiring them to be stable would force an implementation to either suppress them or fabricate them — destroying the observability §12 exists to provide. Tests assert their **presence and structural validity**, never their values. (An earlier draft required *all* diagnostic content to be deterministic, which was incompatible with emitting G-4/G-5/G-6/G-7 at all) |
| DG2 | No secrets, credentials, or raw captured market data are emitted in **either** lane |
| DG3 | Fingerprints are logged **hashed**; the preimage is not emitted (§6.7) |
| DG4 | **Attribution.** A **Lane A** diagnostic is unusable without the engine identity (**G-1**); a **Lane B** telemetry record is unusable without the build identity (**G-3**) and a correlation identifier (§12.2). Neither lane may be the sole carrier of the other's identity facts |
| DG5 | **No fact has two authorities.** A lane B echo of a result fingerprint, inputs fingerprint, or engine identity is **correlation only** and never becomes part of the referenced result's fingerprint preimage (§12.2) |
| DG6 | Neither lane may affect pricing/risk results, cache identity, or methodology selection (§12.1), and telemetry must never be treated as an input to any of them |

**PROPOSED:** the DG1/DG1a split is the same "identity vs observation" boundary drawn in §7.2 (K3 vs K4) and §10.7 (configuration vs measurement) — and it is now also a **channel** boundary (§12.2), which is what makes it enforceable rather than merely stated. A determinism rule that sweeps in runtime measurements is not a stricter rule — it is an unsatisfiable one, and the cheapest apparent way to satisfy it would be to stop emitting the measurements.

### 12.6 Fail-closed introspection

**PROPOSED.** Following §7.6: an engine that **cannot state its own version** must not populate caches, and must not present its output as reproducible. This applies to **Lane A**: **#225's** engine identity (`engine_name`, `engine_version`, `method`) is result-diagnostic content, so it may not be relegated to lane B telemetry as a substitute. **Scope note:** this does **not** license #226 to fold QuantLib *dependency* provenance into that approved identity — #225 defines `engine_version` as the engine's own version (`:919–922`) and its **P1/P4** forbid collapsing dependency provenance into it. That fact's placement is **U-O** (§12.3), and until it is approved it lives in Lane B, §10.5 M4 and the §7.2 cache key — never in the result contract. This is a deliberate coupling — the inability to self-describe is treated as a correctness condition, not a logging inconvenience. It is what makes AC 18 (benchmark metadata) and AC 13 (cache invalidation) enforceable at runtime rather than only in review.

### 12.7 No revision to #225

**PROPOSED.** #226 **does not modify** `docs/33_rates_market_model_result_contracts_225.md`, and adds no field, version, or rule to the #225 result contracts. The two-lane model above is a **#226 clarification of #226's own channel** that preserves the already-approved #225 contract intact: lane A states that #225's result diagnostics remain authoritative and untouched, and lane B introduces only a new out-of-band telemetry channel that #226 owns.

---

## 13. Governance, fail-closed behaviour, and RED boundaries

### 13.1 QuantLib defaults must not become Shiori methodology

**PROPOSED.** This is the single most important rule in this document, and it is enforced at three independent levels so that no one of them is load-bearing alone:

| Level | Mechanism |
|---|---|
| **Structural** | The adapter layer is the only QuantLib-dependent target (§3.4); the DTO layer cannot see QuantLib (§3.5, §6.5 B2) |
| **Behavioural** | Every default listed in §3.6 D1–D17 is **either set explicitly from an owner-approved input or refused** — never silently inherited — and the **TL1** defaults-audit test asserts exactly that contract (set-or-refuse, matching §3.6). The behavioural level enforces the *same* contract as the fail-closed level below, deliberately not a stronger one: a rule demanding that every default be *set* would be unsatisfiable for D9/D14, whose approved values do not yet exist, and would leave #227 no way to be green except by selecting methodology |
| **Fail-closed** | Where an explicit value is required but unspecified, the engine refuses `UNRESOLVED_METHODOLOGY` rather than falling through to a QuantLib default (§3.6, X3) |

### 13.2 RED items — explicitly NOT resolved here

**PROPOSED.** This document **does not choose, default, or infer** any of the following. They remain owned by their existing owners and change only through an explicit owner decision:

| # | Item | Status here |
|---|---|---|
| RED-01 | Interpolation / extrapolation methodology | **NOT CHOSEN.** §3.6 D9/§4.8 make the *switch* explicit and prohibit changing it on a live curve; the *methodology* is untouched |
| RED-02 | Volatility methodology | **NOT CHOSEN** |
| RED-225-* | #225's issue-scoped RED namespace | **NOT CHOSEN, NOT RENUMBERED** (#225 §17 requires the namespace stay scoped) |
| — | Calibration objective / optimizer / tolerances | **NOT CHOSEN** (§8.4) |
| — | Settlement methodology | **NOT CHOSEN** |
| — | Production DV01 / bump methodology | **NOT CHOSEN** |
| — | Bloomberg / workstation conventions | **NOT CHOSEN** |
| — | Production curve-construction conventions | **NOT CHOSEN** |

**PROPOSED:** consistent with the above, the calibration cache (§8) is deliberately **keyed but non-productive** until those policies have identities (§8.4), and §1.9/§2.11 introduce no priced product at all.

### 13.3 Escalation conditions

**PROPOSED.** This work outputs the decision back to the architecture owner rather than proceeding, if any of these occurs:

| # | Condition |
|---|---|
| E-1 | A RED methodology value would have to be chosen to make progress |
| E-2 | An approved #223/#224/#225 contract would have to change materially |
| E-3 | Production pricing code, or executable #227 scaffolding, appears necessary for #226 |
| E-4 | QuantLib evidence is contradictory such that owner judgement is required — **note:** §4.14's literature inconsistencies (X-1…X-3) were resolvable from the pinned source and are **not** such a case |
| E-5 | Another writer moves the branch, or a merge conflict appears |

**OBSERVED (and negative):** no E-1…E-4 condition was encountered during this work. §4.14 records the finding that the audit produced **no** contradiction with #223/#224/#225.

### 13.4 Scope discipline

**PROPOSED.** This PR is **documentation-only** (AC 26). It adds no `cpp/` tree, no `CMakeLists.txt`, no `vcpkg.json`, no workflow, and no test. Everything marked PROPOSED here is a **contract for #227 to implement**, and this document deliberately stops at the boundary of "the decision is locked" — it does not begin the implementation (`docs/08`'s prohibition on rewriting the platform in C++ without explicit approval, and the issue's own non-goal list, both apply).

### 13.5 Merge gate

**PROPOSED / OBSERVED.** Per AGENTS.md rule 12 and the issue's Governance section: no agent or automation may merge; Eddy is the sole merge authority. The token below is a **conditional gate token and publication policy**, **not** this document's current status:

`READY TO MERGE — 等待 Eddy 明確批准`

**It may be asserted only when every precondition holds:** a **stable exact PR HEAD**; the exact-head Codex review has **no unresolved P0/P1/P2** (AC 24); the exact-head required CI is **green** (AC 25); and the scope, worktree and governance checks are satisfied (AC 26). **Eddy's explicit approval is *not* a precondition for emitting this token** — the token *means* "technically ready, and now awaiting that approval". Approval is the precondition for **performing the merge after** the token has been emitted. The gate is therefore a two-step state machine: `TECHNICALLY READY → READY TO MERGE token → WAIT FOR EDDY APPROVAL → MERGE`. **No agent or automation may perform the merge at any step** (AGENTS.md rule 12). While any precondition is pending — which is the case for this document — the terminal status must **not** be asserted, and the document's **current** status is the one recorded in §1's status row (`PENDING CODEX REVIEW — DO NOT MERGE`). AC 24 and AC 25 are **external PR-state conditions**: they are **never** marked satisfied by editing this document (§14 honesty note).

---

## 14. Acceptance-criteria conformance map

**PROPOSED (this section is a self-assessment).** Each criterion of issue #226 is mapped to where it is satisfied, with an honest status. "Satisfied" means the document contains the required decision; where a criterion depends on later evidence (`AC 24`, `AC 25`) the status says so.

| AC | Criterion (abridged) | Where | Status |
|---|---|---|---|
| 1 | Architecture document exists in `docs/` | This file | **Satisfied** |
| 2 | Current-repository inventory, observed vs proposed vs unproven separated | §0, §1, §1.11 | **Satisfied** |
| 3 | QuantLib concurrency claims tied to explicit version/build/config evidence | §4 (v1.43 pinned; macro-conditioned), §4.12, §4.13 | **Satisfied** — every claim names its version *and* configuration |
| 4 | C++20 / CMake / dependency strategy explicit and implementable | §2.1–§2.7, §2.9, §2.10 | **Satisfied** |
| 5 | QuantLib pinning and isolation boundary explicit | §3.1–§3.5 | **Satisfied** |
| 6 | No QuantLib type required across the DTO boundary | §3.5 A1–A5, §6.5 B2, §9.3 PB5, §13.1 | **Satisfied** |
| 7 | QuantLib defaults cannot silently become Shiori methodology | §3.6 D1–D17 + default audit, §13.1 | **Satisfied** |
| 8 | Initial concurrency posture explicit | §5.2, §5.4 | **Satisfied** |
| 9 | Known global/session-state risks documented | §4.2–§4.10, §4.13–§4.14 | **Satisfied** |
| 10 | Production parallel pricing remains disabled unless correctness proven | §5.2, §5.7, §5.8, §10.3, §13 | **Satisfied** (disabled by contract; gate defined) |
| 11 | Immutable snapshot / ownership / lifetime rules explicit | §6.2, §6.4 (**N1–N9**, deep immutability over storage), §6.6, §9.2 **TL3** (alias mutation after publication is an explicit failure case) | **Satisfied** |
| 12 | Allowed cache layers enumerated | §7.4 L1–L8 | **Satisfied** |
| 13 | Every cache layer has deterministic key + invalidation rules | §7.4 (key/lifetime/invalidation columns), §7.5, §7.7 | **Satisfied** |
| 14 | Calibration-cache identity and reuse rules explicit | §8.2, §8.3, §8.5, §8.6 **U-L**, §9.2 TL5 | **Satisfied** — reuse is explicit *and* explicitly conditional: a warning-bearing entry is not reusable until §8.6 U-L is discharged by the TL5 warning-stability test — which **#227 cannot supply** (§9.2 TL5) |
| 15 | C++ test framework and layout defined | §9.1, §9.2 | **Satisfied** |
| 16 | Concurrency and cache correctness test requirements defined | §5.7 T1–T8 (**tiered S/K** — Tier S required of #227, Tier K with #229), §7.7 CT1–CT8 + CT3b, §9.6, §9.8 Q7 | **Satisfied** |
| 17 | Benchmark methodology separates cold/warm and cache state | §10.3, §10.4, §10.6 | **Satisfied** — **availability is tiered** (§10.3): only non-price-bearing phases exist in #227; kernel/risk/calibration benchmarks are **DEFINED / DEFERRED** |
| 18 | Benchmark reproducibility metadata defined | §10.5 M1–M11 | **Satisfied** |
| 19 | Python + C++ CI integration strategy defined | §11.1–§11.11 | **Satisfied** |
| 20 | Existing Python / bond-option validation remains protected | §11.2 PR1–PR5, §11.8, §1.8 | **Satisfied** |
| 21 | No production pricing code introduced | §2.11, §13.4, §15 | **Satisfied** (documentation-only) |
| 22 | No Bloomberg / desk pricing methodology invented | §3.6 D8, §6.3, §9.7 BF3, §13.2 | **Satisfied** |
| 23 | No RED-01 / RED-02 / RED-225-* value silently resolved | §13.2, §8.4, §6.8 X3 | **Satisfied** |
| 24 | Codex reviewed the exact final HEAD with no unresolved P0/P1/P2 | PR review record | **Pending** — external, tracked in the PR (closure loop) |
| 25 | Exact-head required CI green | CI run record | **Pending** — external, tracked in the PR |
| 26 | PR scope documentation-only; #227 scaffolding deferred | §13.4, §15 | **Satisfied** |
| 27 | Eddy explicitly authorizes merge | — | **Pending — reserved to Eddy** (never satisfied by any agent). This is the precondition for the **merge**, not for emitting the readiness token (§13.5) |

**PROPOSED honesty note.** AC 24, AC 25 and AC 27 are **not** satisfiable by writing a document, and this document does not claim them. AC 24 and AC 25 are pursued under the PR closure process; AC 27 is explicitly not an agent's to claim (§13.5).

---

## 15. Non-goals and deferred-to-#227 register

### 15.1 Explicit non-goals of #226

**PROPOSED.** Consistent with the issue's Boundaries section, this work does **not** and must not:

| # | Non-goal |
|---|---|
| NG1 | Implement vanilla swap, swaption, callable, or range-accrual pricing |
| NG2 | Add production curve construction or production model calibration |
| NG3 | Choose interpolation/extrapolation, volatility, settlement, risk-bump, Bloomberg/workstation, or curve-construction methodology (§13.2) |
| NG4 | Enable production parallel pricing (§5.2) |
| NG5 | Add live Bloomberg access inside the C++ engine (§6.3) |
| NG6 | Expose QuantLib types in the public DTO boundary (§3.5, §6.5) |
| NG7 | Redesign #224/#225 contracts (§4.14 records no contradiction requiring this) |
| NG8 | Modify the validated bond-option production path (§1.8, §11.2) |
| NG9 | Add executable CMake/build/engine scaffolding (§13.4) |
| NG10 | Perform speculative general-purpose framework work unrelated to the Rates Engine |

### 15.2 Deferred to #227 (locked here, implemented there)

**PROPOSED.** The distinguishing feature of this document: each item below is **decided here** so that #227 does not reopen it.

| # | Deferred implementation | Decision locked in |
|---|---|---|
| D-1 | `cpp/rates_engine/` tree creation | §2.3 (target names, layers) |
| D-2 | `CMakeLists.txt` + `CMakePresets.json` | §2.2, §2.4 |
| D-3 | Dependency manifest + pinned QuantLib port | §2.6, §2.7, §3.2 |
| D-4 | Canonical serialization + hashing format | #225 §15.4 owns it; §6.5 B1 confirms the boundary |
| D-5 | GoogleTest wiring + the **TL0–TL8** suite, **split by tier and owner** — not a blanket #227 deliverable. **#227** implements the framework and only the tests executable at the skeleton/boundary stage: **TL0**, **TL1**, **TL2**, **TL3**, **TL4** (§7.7 CT1–CT8 + CT3b), **TL8**, TL7's benchmark **framework** and any non-price-bearing benchmark, and **§5.7 Tier S** (T4, T5, T7). **Deferred to their already-named later owners:** TL5's **warning-stability** half and §7.7 **CT9** require the concrete calibration methodology (§8, §9.2 TL5); **TL6's Tier K** (T1, T2, T3, T6, T8) and any benchmark that **prices** require the pricing kernel (**#229**, §5.7 tiers). #227 must **not** invent calibration or pricing behaviour to make a deferred test pass | §9, §5.7 tiers |
| D-6 | Google Benchmark **framework/harness + smoke capability**, for **executable, non-price-bearing phases only**; price-bearing phases are **DEFINED / DEFERRED** to their owning issues (§10.3, §10.11) | §10 |
| D-7 | CI workflows | §11 |
| D-8 | Result-diagnostics (Lane A) and runtime-telemetry (Lane B) emitters | §12.2 |
| D-9 | `.gitignore` narrowing for the C++ tree | §1.9 / §2.2 — the repo-wide `build/` rule is a **naming trap**; #227 must add a narrow explicit ignore and verify with `git check-ignore` |
| D-10 | The concurrency-correctness suite's enablement | §5.7/§5.8 |
| D-11 | The defaults-audit test matrix | §3.6 D1–D17 + **TL1** |

### 15.3 What this document deliberately leaves open

**PROPOSED.** Honest remainder, all recorded as UNPROVEN (§4.13, §5.9, §8.6, appendices): whether a specific concurrency configuration is safe; whether sessions would help; the exact pinned options to be verified against the built library; per-engine thread behaviour; and every measurement in §10. None of these blocks #227 from beginning — each is a verification task, not an architecture question.

---

## 16. Self-audit performed

**OBSERVED / PROPOSED — recorded so the checks are reproducible rather than asserted.** Before publication, the document was audited as a *family of rules* (not as prose), for the defect classes the issue names:

| # | Defect class audited | Result |
|---|---|---|
| A-1 | Contradictory ownership rules | Checked §3.5 vs §6.5 vs §9.3: the "no QuantLib across the boundary" rule is stated once (§3.5) and referenced, not redefined. No competing ownership claims |
| A-2 | Hidden global-state assumptions | Checked every rule that consumes QuantLib; §4.2's inventory (G1–G10) plus ECB is the single reference. C3/C5/C7 and §7.2 K2 all point at it. No rule silently depends on an unlisted global |
| A-3 | Cache keys missing methodology/version identity | Checked §7.4 column-by-column for L1–L8; L3 and L5 explicitly carry methodology identity, and §7.6 makes an unknown engine version unusable (K6). §8.2 CC7 carries calibration-policy identity |
| A-4 | Undeterminable invalidation | Checked §7.5: invalidation is by key (I1), eviction is the only mechanism (I2), and I3 makes a hit/miss difference in the *result* a testable defect (CT1/CT6) — **for ordinary entries**; calibration is governed by §8.3.1 instead |
| A-5 | Ambiguous process/thread/request lifetime | Checked §5.3's classes against §6.2/§6.6/§7.3: PROCESS-GLOBAL, THREAD-LOCAL, REQUEST-LOCAL, IMMUTABLE and SHARED-READ-ONLY are defined once and used consistently — including the §5.3-vs-§6.2 ownership wording and the storage qualifier, both corrected in this reconciliation |
| A-6 | QuantLib types leaking across public boundaries | Checked §3.5 A1–A5, §6.4 N3, §6.5 B2, §7.3, §9.3 PB5. Structurally enforced, not merely stated |
| A-7 | Benchmark warm/cold ambiguity | Checked §10.3/§10.6: process state and cache state are *separate* axes, both enumerated, and CS1 requires a documented priming procedure |
| A-8 | Benchmark cache-state ambiguity | Checked §10.6 CS1–CS3 and §10.5 M6/M11: cache state and concurrency mode are mandatory metadata |
| A-9 | Concurrency claims without evidence | Checked §4 *is* the evidence; §4.12's rejected-claims table names each unevidenced claim; §4.13 lists what stays UNPROVEN. **No unsourced concurrency guarantee appears** |
| A-10 | CI gaps | Checked §11.7 F1–F5 against the bypass question; F3 explicitly covers contract-affecting files; §11.6 forbids allow-failure on a required job |
| A-11 | Conflicts with #223/#224/#225 | Checked §4.14 and §1's upstream table; the audit found no contradiction. Where #225 owns a rule (replay identity, RED namespace), this document references it rather than restating it |
| A-12 | Accidental methodology choices | Checked §13.2 item-by-item, plus §8.4 and §6.8 X3. No RED value is selected anywhere; §3.6's defaults policy recuses itself explicitly (D8) |
| A-13 | Accidental #227 implementation | Checked: no code, no build files, no workflows added (§13.4). Every normative item is marked as a contract for #227 |
| A-14 | Claim-classification discipline | Checked that each section's claims carry OBSERVED / EXTERNAL EVIDENCE / PROPOSED / UNPROVEN, and that no statement blends a sourced fact with an unsourced one |
| A-15 | **Benchmark phase availability vs implementation ownership** | Checked §10.3 against §2.10's tree, §10.8, §10.11, §15.2 D-6, §9.2 TL7 and Appendix C: every price-bearing or methodology-dependent phase is marked **DEFINED / DEFERRED**, is not a #227 deliverable, and cannot be required by the CI smoke test. *(This class was added because the §2.10 sibling survived an earlier audit that had no such row.)* |
| A-16 | **Immutability asserted by `const`/read-only rather than by backing storage** | Checked §5.3/§5.4 against §6.2/§6.4 N1–N9: shareability is justified by **owning immutable storage**, never by "the engine only reads"; alias mutation of published identity is an explicit failure case (§9.2 TL3). *Added because §5.3/§5.4 still carried the pre-fix justification.* |
| A-17 | **Dependency provenance placed in a #225 result field** | Checked §3.2's placement rule against §2.5, §2.7 P4/P6, §2.8, §3.9 U5, §4.5, §5.8 PE2, §12.3 G-2 and Appendix C U-O: the pin is Lane B + M4 + cache key everywhere, no existing field is overloaded, and no site names a Lane-A home. *Added because the §2.7 P6 paragraph survived an earlier audit that had no such row.* |

**PROPOSED:** A-9 and A-12 are the two classes where a plausible-looking document fails most easily, and both are addressed by *removing* claims (rejecting the community assertions in §4.12, listing the UNPROVEN remainder) rather than by adding assurance.

---

## Appendix A. Evidence index

**EXTERNAL EVIDENCE — all QuantLib facts in this document derive from these, at tag `v1.43` unless noted.** Line numbers cited for the pinned tree.

| Source | Used for |
|---|---|
| `ql/settings.hpp`, `ql/settings.cpp` | §4.2 G1, §4.3 |
| `ql/patterns/singleton.hpp` | §4.2 (official sentence), §4.4 |
| `ql/patterns/observable.hpp`, `ql/patterns/observable.cpp` | §4.5 |
| `ql/patterns/lazyobject.hpp` | §4.6 |
| `ql/handle.hpp` | §4.7 |
| `ql/termstructure.hpp`, `ql/termstructures/yieldtermstructure.hpp`, `ql/math/interpolations/extrapolation.hpp`, `ql/termstructures/interpolatedcurve.hpp` | §4.8 |
| `ql/indexes/indexmanager.hpp` | §4.9 |
| `ql/models/calibrationhelper.hpp`, `ql/math/optimization/*` | §4.10 |
| `ql/time/ecb.cpp`, `ql/time/calendars/target.cpp` | §4.2, §4.2.1 |
| `CMakeLists.txt`, `configure.ac`, `ql/userconfig.hpp` | §4.4, §4.5, §4.11 — option defaults |
| `test-suite/quantlibbenchmark.cpp` | §4.11, §4.12 (a) |
| `ql/qldefines.hpp` | §4.5 (Boost ≥ 1.58 for the thread-safe observer pattern) |
| Reference manual (`config.html`) | §4.4, §4.5 — official option descriptions |
| Maintainer statements (StackOverflow accepted answer; issue-tracker comment) | §4.12 (b), (c) |
| vcpkg `ports/quantlib/` | §2.6, §3.2, §3.3 |

**Consumer note.** A reader who wants to challenge §4 should start with `CMakeLists.txt`'s option defaults and then the `#ifdef` branches that consume each macro — that pairing is the crux of every configuration-dependent claim.

---

## Appendix B. Classification summary

**PROPOSED.** Approximate distribution, stated so a reader can see where the evidence is thin:

| Class | Where it dominates |
|---|---|
| **OBSERVED** | §1 (all of it), §1.11, §11.1, §11.8, §9.5's `docs/08` obligation, §4.14's negative finding |
| **EXTERNAL EVIDENCE** | §3.2–§3.6 (defaults policy grounded in sources), §4 (all), §10.6's rationale |
| **PROPOSED** | §2, §3.5, §5, §6, §7, §8, §9, §10, §11 (additions), §12, §13, §15 |
| **UNPROVEN** | §2.8 (no local toolchain), §4.13, §5.9, §8.6, §10's measurements, and the concurrency configuration itself |

**PROPOSED:** the single most important honesty statement in this document is that the **concurrency posture's safety is UNPROVEN** — it is *chosen conservatively* because the evidence (§4) shows the alternative is unproven, not because safety has been demonstrated. Demonstrating it is §5.7's job, and it has not been done.

---

## Appendix C. Open-items register

**PROPOSED.** Every open item, in one place, so #227 can take them as a work list:

| Ref | Open item | Type | Owner |
|---|---|---|---|
| U-A | Is any concurrency configuration safe for Shiori? | Evidence | #227 (+owner decision per §5.8 PE6) |
| U-B | Would enabling sessions help? | Investigation | Unassigned; not authorised here |
| U-C | Is the G1–G10 inventory exhaustive for the pin? | Verification | #227 |
| U-D | Per-engine thread behaviour | Verification | #227 |
| U-E | Should `QL_THROW_IN_CYCLES` be enabled? | Decision (behaviour) | Owner |
| U-F | Thread-safe observer ABI compatibility | Verification | #227 if considered |
| U-G | Do the Python binding and the C++ pin share library/global instances in one process, per supported linkage? | **Verification (requirement)** | #227 — must satisfy one of §5.2.1's three cross-plane resolutions and record it (§5.5 **C14**) |
| U-H2 | Macro config of any pre-built binary | Verification | #227 (§5.5 C10) |
| U-I2 | Is `QL_REQUIRE_EXPLICIT_EVALUATION_DATE` in the pin? | Verification | #227 |
| U-J2 | Mailing-list archives | Not audited | — |
| U-K2 | ≤1.28 vs ≥1.29 session differences | Not enumerated | — |
| U-O | **Approved placement of QuantLib dependency provenance (version + macro configuration) in the #225 result contract** | **Schema decision (owner / #225)** | Owner — #226 must not invent DTO semantics (`docs/33…225.md` `:1324–1325`); until approved it is emitted via Lane B + §10.5 M4 + §7.2 cache key (§12.3 G-2) |
| U-H/I/J (concurrency) | Gate sufficiency, contention magnitude, multi-process scaling | Measurement | **#227** only for the **non-price-bearing harness path** (§10.3); **realistic-use contention magnitude and multi-process scaling are DEFERRED** to the #229-kernel era and require a §9.8 Q6 enablement proposal (§5.8 PE5) |
| U-K/L/M (calibration) | Is calibration caching worthwhile; warning stability; key comparability across engines | Measurement | **Deferred** with the concrete calibration methodology (§8.4, §9.2 TL5) — not #227's scope |
| D-9 | Narrow `.gitignore` for the C++ tree (`build/` trap) | Implementation | #227 |

---

## Appendix D. Related documents

| Document | Relationship |
|---|---|
| `docs/31_rates_reuse_boundary_223.md` | Reuse boundary, authoritative test anchors, protected modules; §10 sequencing permits #226 in parallel with #224 |
| `docs/32_usd_sofr_ois_convention_224.md` | `SwapTrade` → `ConventionSet` → `ResolvedSwap`; owner decisions R1/R2 |
| `docs/33_rates_market_model_result_contracts_225.md` | **Authoritative input/output contract.** §5/§5.1 composition, §6 snapshot and identity, §15 versioning/replay, §16 failure semantics, §17 RED namespace |
| `docs/08_performance_engine_backend_strategy.md` | Backends only after profiling; parity-with-reference obligation; no unbenchmarked performance claims |
| `docs/12_pr_review_rubric.md` | Review severity definitions referenced by AGENTS.md rule 10 |
| `AGENTS.md` | Rules 1–12, including the no-merge rule and the terminal merge gate |
| This document (`docs/34`) | The #226 execution model; input to #227 |

---

## Appendix E. One-page summary for #227

**PROPOSED.**

1. **Build:** C++20, no extensions, CMake ≥ 3.25 with presets, out-of-source, four locked targets, Windows-first without being Windows-only, dependencies pinned by a manifest, no fast-math.
2. **QuantLib:** pinned, acquired through the manifest, isolated behind one adapter target; it **never** crosses the DTO boundary; **every default is set explicitly** or the engine refuses.
3. **Concurrency:** **single-threaded serialized per process; parallel pricing DISABLED**; scale by multiple processes; all QuantLib access behind one measured gate; enabling parallelism requires §5.7 T1–T8 — **including T8, which tests the configuration actually being enabled** — **and** an owner decision; the absence of library-internal parallelism must be **verified** (`_OPENMP` undefined), not inferred from the OpenMP option.
4. **Inputs:** caller-owned and immutable — published state must be **owning immutable value state** or equivalently deep-immutable (§6.4 N4–N7); **no** non-owning alias into caller-mutable storage is retained as published request state; never mutated after publication; no I/O in the kernel; no global fixing store.
5. **Caching:** cache **values**, never QuantLib objects; keys from explicit input identity only; no pointer, clock, global or unknown-version identity; version mismatch is a miss.
6. **Calibration:** separate namespace, full key including methodology-policy identity, failures and non-convergence **not** reusable, **warning-bearing entries not reusable until warning stability is proven (U-L)**, unresolved policy ⇒ refuse; a **hit returns the original immutable calibration result instance unchanged — never a fabricated `calibrated_at`/id** (§8.3.1).
7. **Tests:** GoogleTest, layers **TL0–TL8** (**tiered by owner** — #227 owns only the skeleton-executable layers; TL5's stability half, TL6's Tier K and CT9 are deferred, §9.2 / §5.7 tiers), defaults-audit matrix, structural no-QuantLib check, Python↔C++ parity, quarantined concurrency suite.
8. **Benchmarks:** Google Benchmark, separate target; cold/warm × cache-state × phase; no blended number; mandatory reproducibility metadata; correctness-checked; no absolute-time CI gate. **Taxonomy complete, availability tiered** — #227 builds the harness and smoke for non-price-bearing phases only, and price-bearing phases stay **DEFINED / DEFERRED** (§10.3).
9. **CI:** existing Python and launcher jobs preserved; new C++ build/test/sanitizer/benchmark-smoke jobs; no path filter may bypass required validation.
10. **Diagnostics — two lanes (§12.2).** **Lane A:** #225's result diagnostics stay inside `PricingResult`/`RiskResult`, deterministic and fingerprint-participating; **#226 never removes, renames, or moves them**. **Lane B:** `RATES_RUNTIME_TELEMETRY_V1` carries operational measurements (per-layer cache hit/miss, execution path, gate contention, timing, build/runtime identity) **out of band**, is **not** part of any result DTO, and does **not** enter a result fingerprint. Neither lane is a second source of methodology.

**The rule that governs everything above:** *correctness dominates performance, and an unproven configuration is not adopted merely because it is faster.*

---
