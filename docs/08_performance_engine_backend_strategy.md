# 08 Performance and Engine Backend Strategy

## Purpose

Shiori Pricing Lab should remain readable by AI agents while still leaving a path toward high-performance valuation.

The project should not start as a full C++ system. It also should not assume pure Python loops can handle every future pricing workload.

The chosen strategy is:

> Python owns orchestration, schemas, APIs, UI, backtesting workflow, and AI-native interaction. Heavy numerical pricing kernels may use optimized or compiled backends behind stable Python interfaces.

## Core decision

Use a Python-first architecture with replaceable high-performance pricing backends.

Python is the cockpit. Optimized libraries or compiled code may become the engine room.

This means:

- product definitions stay readable and serializable;
- valuation context stays explicit;
- pricing engines expose stable Python interfaces;
- heavy computation can later be delegated to QuantLib, NumPy, Numba, C++, Rust, or other optimized backends;
- UI, backtesting, and AI inquiry layers should not care which backend is used.

## Why not pure Python for everything?

Pure Python is excellent for readability, orchestration, workflow design, testing, and AI maintenance.

Pure Python is risky for heavy workloads such as:

- large Monte Carlo simulations;
- path-dependent structured product valuation;
- callable product tree or lattice calculations;
- large portfolio valuation;
- scenario grid repricing;
- historical backtesting across many dates and products.

The problem is not Python as a language. The problem is naive CPU-heavy Python loops.

## Why not all C++ from day one?

C++ can be fast, but it increases build complexity, debugging cost, cross-platform friction, onboarding difficulty, and AI-agent maintenance risk.

The project should not become a C++ quant library before product requirements and interfaces are stable.

## Recommended stages

### Stage 1 — Python-first correctness

Use Python to define the architecture:

- product definitions;
- market data snapshots;
- valuation context;
- pricing engine interfaces;
- valuation result schemas;
- backtesting workflow;
- tests;
- UI and AI orchestration.

The first goal is correctness and clean boundaries.

### Stage 2 — Optimized Python and existing libraries

Use optimized libraries before custom compiled code:

- NumPy for vectorized numerical work;
- pandas or Polars for data handling;
- QuantLib-Python for fixed-income analytics where useful;
- DuckDB and Parquet for local analytical storage;
- Numba for numerical hot loops that are convenient to keep near Python.

### Stage 3 — Specialized backends only after profiling

Move hot components to faster backends only after benchmarks show a real bottleneck.

Possible backend choices include:

- QuantLib through Python bindings;
- Numba-compiled kernels;
- C++ extensions;
- Rust extensions;
- GPU-based simulation later, only if justified.

## Pricing engine interface rule

All engines should expose stable Python-facing interfaces.

Different backend implementations may exist behind the same interface:

- Pure Python reference engine;
- QuantLib-backed engine;
- Numba Monte Carlo engine;
- compiled callable swap engine;
- compiled range accrual engine.

The UI, backtesting layer, and AI inquiry layer should call the interface, not a backend-specific implementation.

## Portfolio valuation strategy

Portfolio valuation cost can explode:

```text
number of trades × number of valuation dates × number of scenarios × model complexity
```

A faster language alone is not enough. The system also needs:

- batch pricing;
- market object reuse;
- curve and discount factor cache;
- schedule and cashflow cache;
- shared scenario contexts;
- parallel execution where safe;
- incremental recalculation;
- profiling before optimization.

Do not rebuild the same curve, schedule, fixing table, or discount factors for every trade unless required.

## Monte Carlo strategy

Monte Carlo should not be the default answer for every optionality product.

Before using Monte Carlo, consider analytic, semi-analytic, lattice, tree, or approximation methods.

When Monte Carlo is needed, use vectorization, path reuse, deterministic seeds for tests, variance reduction where appropriate, and optimized backends only after profiling.

## Benchmarking rules

Do not optimize based on fear alone.

Before introducing a faster backend, create a benchmark that records:

- workload size;
- baseline runtime;
- bottleneck function;
- target runtime;
- correctness comparison;
- test coverage.

Every accelerated backend must match a reference implementation within documented tolerance.

## AI-agent rules

AI coding agents must not:

- write large pure Python Monte Carlo loops without discussing performance;
- rewrite the platform in C++ without explicit approval;
- add optimized backends without tests against reference results;
- claim performance improvement without a benchmark;
- bypass the stable pricing engine interface.

AI coding agents should:

- start with clear Python interfaces;
- implement reference calculations first;
- add profiling before optimization;
- isolate hot loops;
- keep financial assumptions visible.
