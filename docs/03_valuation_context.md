# 03 Valuation Context

## Purpose

The valuation context is the object that tells a pricing engine exactly how to value a product.

It prevents hidden assumptions such as:

- using today's date implicitly;
- using whatever curve happens to be loaded in memory;
- mixing market data from different dates;
- ignoring known fixings;
- changing model settings without recording them.

## Core idea

```text
ValuationContext
= valuation date
+ market data snapshot
+ curve selection
+ model settings
+ convention settings
+ fixing policy
+ reporting currency
+ metadata
```

A product definition describes the trade. A valuation context describes the environment in which the trade is valued.

## Minimum fields

A future `ValuationContext` should include:

```python
ValuationContext(
    valuation_date="2026-06-23",
    market_snapshot=snapshot,
    curve_set=curve_set,
    model_settings=model_settings,
    reporting_currency="USD",
    fixing_policy=fixing_policy,
    scenario=None,
    metadata={...},
)
```

## Valuation date

Valuation date must be explicit.

Do not use system date inside pricing engines.

Good:

```python
price(product, context)
```

Bad:

```python
today = date.today()
price(product, today=today)
```

The reason is simple: arbitrary valuation date is required for historical valuation and backtesting.

## Market snapshot

The valuation context must point to a market snapshot that is consistent with the valuation date.

This does not always mean every data point has the exact same timestamp, but the snapshot must record what data is used and why.

## Curve set

The curve set maps economic purposes to actual curves.

Examples:

```text
discount_curve: USD_SOFR_OIS
forecast_curve: USD_LIBOR_3M or SOFR term structure
basis_curve: USD_TWD_BASIS
fx_forward_curve: USD_TWD_FWD
```

A pricing engine should request a curve by purpose, not by hard-coded ticker.

## Model settings

Model settings should include all assumptions that change valuation results.

Examples:

- interpolation method;
- day-count convention;
- compounding convention;
- volatility type;
- model family;
- calibration date;
- tree or Monte Carlo settings;
- number of paths;
- random seed where relevant.

## Fixing policy

Fixing policy matters for swaps, backtesting, and structured products.

The context must distinguish:

- known historical fixings;
- projected future fixings;
- missing fixings;
- fallback handling.

For backtesting, the engine must not accidentally use future fixings that were not known on the historical valuation date.

## Scenario context

A scenario should be represented as a modification to the valuation context or market snapshot.

Examples:

- parallel curve shock;
- steepener / flattener;
- basis widening;
- FX shock;
- vol shock;
- combined stress scenario.

Pricing engines should not have separate one-off scenario logic hidden inside product-specific files.

## Output metadata

Every valuation result should retain enough context metadata to explain itself:

- product id;
- valuation date;
- snapshot id;
- curve set;
- model settings id;
- scenario id if any;
- warnings;
- diagnostics.

## Validation rules

Before pricing, the system should check:

- valuation date exists;
- market snapshot exists;
- required curves exist;
- required fixings are available or explicitly handled;
- product definition is valid;
- model settings are compatible with the product;
- no accidental use of system date.

## Development priority

Valuation context is a core v0.1 / v0.2 concept.

Do not wait until exotic products. If it is added late, every product engine will already have hidden assumptions and backtesting will become painful.
