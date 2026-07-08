# 30 BLI MVP Demo Runbook

A reproducible demo runbook for the European bond option (BLI) MVP, covering
the supported price-based cash-settled Black-76 case wired in #44 (MVP-8) and
the Streamlit UI added in #84 (MVP-9). This is a runbook, not a design doc:
for the pricing methodology see `docs/bond_linked_structured_pricer/SPEC_v1.3.md`
and Annex A; for the engine's own composition and result-mapping rules see the
module docstring in `src/shiori_pricing_lab/pricing/bli_pricing_engine.py`.

**No pricing number in this document is invented.** Every expected value below
is copied from a pinned literal constant in
`tests/test_bli_pricing_engine.py`, independently derived by hand from Annex
A's formulas (see that test file's own derivation comment) and asserted by
`test_supported_case_returns_success_with_pinned_pv`. Re-run that test locally
to reproduce these numbers yourself rather than trusting this document.

## 1. Fresh checkout / local setup

```bash
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\Activate.ps1 on Windows PowerShell
pip install -e ".[dev,quant]"
```

QuantLib (the `quant` extra) is required for the happy-path bond-option
pricing case. Without it:

- the Streamlit UI shows a clear warning and does not attempt pricing;
- QuantLib-gated tests (marked `@_requires_quantlib` / `skipif`) are skipped,
  not failed.

## 2. Test commands

```bash
# Engine pinned-value test (the source of truth for the numbers in section 5)
pytest tests/test_bli_pricing_engine.py::test_supported_case_returns_success_with_pinned_pv -q

# UI display tests
pytest tests/test_bli_mvp_ui.py -q

# UI demo fixture tests
pytest tests/test_bli_mvp_ui_demo_fixture.py -q

# Full suite
pytest -q
```

## 3. Launch the UI

```bash
streamlit run src/shiori_pricing_lab/app/streamlit_app.py
```

In the sidebar, select **"Bond Option (BLI MVP)"**.

## 4. Demo cases

The page offers two deterministic fixture-backed cases (from
`src/shiori_pricing_lab/app/bli_mvp_ui_demo_fixture.py`):

- **SUCCESS**: select **"Synthetic supported case (short-tenor demo curves)"**
  (`SYNTHETIC_UI_DEMO_BUNDLE`).
- **Deterministic FAILED / `ENGINE_ERROR`**: select **"Known curve-range
  ENGINE_ERROR demo"** (`SYNTHETIC_UI_DEMO_BUNDLE_CURVE_RANGE_ERROR` — the
  unmodified shared `SYNTHETIC_BLI_MVP_INPUT_BUNDLE`, whose curve nodes do
  not bracket its own option expiry).

## 5. Expected happy-path values

Source of truth: `tests/test_bli_pricing_engine.py`, test
`test_supported_case_returns_success_with_pinned_pv`, pinned constants
`_EXPECTED_FORWARD_CLEAN_PRICE_PER_100`, `_EXPECTED_TIME_TO_EXPIRY`,
`_EXPECTED_OPTION_DISCOUNT_FACTOR`, `_EXPECTED_BLACK76_PV_PER_100`,
`_EXPECTED_PV`. Do not treat the values below as authoritative on their own —
treat the constant names and test name as the pointer to the current
authoritative value, since a future change to the test file is what would
actually update these numbers.

| Quantity | Value |
| --- | --- |
| Forward clean price per 100 | `101.22605288103159` |
| Time to expiry (year fraction) | `0.2465753424657534` |
| Option discount factor | `0.9929452501091504` |
| Black-76 PV per 100 | `4.474769848529296` |
| Full PV (premium) | `2.237384924264648` |

## 6. Option-leg-only scope

The engine prices the bond-option leg only. On every `SUCCESS` result,
`PricingResult.assumptions` states this explicitly and machine-readably:

- `assumptions["priced_component"] == "bond_option_leg"`
- `assumptions["priced_component_scope"] == "option_leg_only_not_full_structured_product"`
- `assumptions["excluded_components"]` lists what is **not** priced:
  - `deposit_leg`
  - `principal_redemption`
  - `physical_delivery`

The returned `pv` must never be read as a whole-structured-product value.

## 7. Supported MVP boundary

The MVP supports, and only supports:

- European exercise;
- price-based payoff;
- cash settlement;
- explicit clean-price inputs (spot clean price / strike clean price);
- an explicit Bond Reference Curve (forward clean price only);
- an explicit Option Discount Curve (option PV discount factor only);
- a `PRICE_VOL` or `EQUIVALENT_PRICE_VOL` volatility feed (used directly as
  sigma, with no yield-vol conversion).

## 8. Intentionally unsupported

The MVP intentionally does not support, and fails explicitly (deterministic
`FAILED` results, never a fabricated number) for:

- American exercise;
- yield-based payoff;
- yield-vol conversion (`YIELD_VOL` volatility basis);
- physical delivery;
- principal/redemption economics;
- Greeks (`dv01` stays `None`);
- full structured-product valuation (the deposit leg is never priced here);
- Bloomberg / FTP / warehouse / audit integration.

See `tests/test_bli_pricing_engine.py`'s guard-rejection-path tests
(`test_unsupported_exercise_style_fails`, `test_unsupported_payoff_basis_fails`,
`test_unsupported_volatility_basis_fails`) for the deterministic proof of each
boundary above.
