# 04 Product Definition Schema

## Purpose

A product definition is the machine-readable description of a financial product.

It should describe the deal terms, not the market state.

Product definitions are required so the same pricing engine can be used for:

- today's valuation;
- historical valuation;
- scenario analysis;
- backtesting;
- AI-assisted inquiry;
- reproducible testing.

## Implementation status

As of PR #19 (Issue #12, first slice):

- **IRS** (`InterestRateSwap`) and **OIS** (`OvernightIndexedSwap`) schemas exist
  in `src/shiori_pricing_lab/products/`, with supporting legs and enums. Schema
  only — no pricing engine.
- **CCS** and **FX Swap** schemas are still **pending**.

The sections below remain the design reference for all products, including the
ones not yet implemented.

## Product definition vs valuation context

Product definition answers:

> What is the trade?

Valuation context answers:

> How and when are we valuing it?

Market snapshot answers:

> What market data are we using?

Keep these separate.

## Base fields

Every product definition should eventually include:

```text
product_id
product_type
trade_date
effective_date
maturity_date
currency or currencies
notional or notionals
counterparty side / pay_receive where relevant
calendar
business day convention
day-count convention
payment frequency
reset frequency
metadata
```

## IRS / OIS

IRS product definition may include:

```text
product_type = IRS
currency
notional
fixed_leg:
    pay_receive
    fixed_rate
    payment_frequency
    day_count
floating_leg:
    index
    spread
    reset_frequency
    payment_frequency
schedule:
    effective_date
    maturity_date
    stub_rule
```

## CCS

CCS product definition may include:

```text
product_type = CCS
currency_1
currency_2
notional_1
notional_2
fx_initial_exchange
fx_final_exchange
leg_1:
    index_or_fixed_rate
    spread
    payment_frequency
leg_2:
    index_or_fixed_rate
    spread
    payment_frequency
basis_spread
reset_rules
collateral_currency
```

## FX Swap

FX Swap product definition may include:

```text
product_type = FX_SWAP
currency_pair
near_date
far_date
near_amount
far_amount
spot_or_near_rate
forward_points_or_far_rate
settlement_calendar
```

## Swaption

Swaption definition may include:

```text
product_type = SWAPTION
option_type = payer or receiver
exercise_type = European initially
expiry_date
underlying_swap_definition
strike
premium
settlement_type
volatility_convention
```

Start with European swaptions before Bermudan or callable structures.

## Bond Option

Bond option definition may include:

```text
product_type = BOND_OPTION
option_type = call or put
exercise_type
expiry_date
underlying_bond_definition
strike_price
settlement_type
```

This requires clean bond reference data and model choices.

## Callable Swap

Callable swap definition may include:

```text
product_type = CALLABLE_SWAP
underlying_swap_definition
call_schedule
call_right = issuer / holder / payer / receiver as defined by desk convention
exercise_dates
notice_days
settlement_rules
model_family
```

Callable swap should not be implemented as a quick patch on top of IRS.

It is an optionality product and should use an explicit exercise schedule and model settings.

## IR Daily Range Accrual

Range accrual definition may include:

```text
product_type = IR_DAILY_RANGE_ACCRUAL
currency
notional
coupon_periods
observation_calendar
observation_index
lower_bound
upper_bound
accrual_formula
leverage
cap
floor
payment_frequency
fixing_rules
callable_feature optional
```

The difficult part is not only pricing. The difficult part is representing term-sheet conditions clearly.

## Schema design rules

1. Product definitions must not fetch market data.
2. Product definitions must not contain live curves or vols.
3. Product definitions must preserve all contractual terms that affect valuation.
4. Product definitions must be serializable.
5. Product definitions must be usable in tests with synthetic data.
6. Product definitions must be strict enough for pricing but readable enough for AI agents.

## Development sequence

Start with IRS, OIS, CCS, and FX Swap.

Do not implement callable swaps, bond options, or range accruals until the shared product definition pattern is stable.

## Future structure

Product-specific specs should live under:

```text
docs/products/
```

Possible files:

```text
docs/products/irs.md
docs/products/ccs.md
docs/products/fx_swap.md
docs/products/swaptions.md
docs/products/bond_options.md
docs/products/callable_swap.md
docs/products/range_accrual.md
```
