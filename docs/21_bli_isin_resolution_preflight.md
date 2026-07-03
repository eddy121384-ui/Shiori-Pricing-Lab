# 21 BLI ISIN Resolution Preflight

Status: docs-only preflight. No resolver function, pricing, payoff
skeleton, cash-flow generation, schedule engine, `MarketDataSnapshot`,
MVP input bundle, Treasury FTP parser, ingestion, Bloomberg/API
connector, QuantLib, UI, screenshot capture, or product-schema change is
added by this doc.

## 1. Why this doc exists

Three prior slices now exist:

- `BondOption` (PR #50, Issue #38 partial) stores `underlying_isin` — a
  reference to a bond, not the bond's own data.
- `BondLinkedStructuredProduct` (PR #56) binds a `DepositLeg` and a
  `BondOption`, but does not embed Bond Master terms either.
- `BondReferenceData` (PR #58, docs/20) is the Bond Master reference-data
  schema, living in `shiori_pricing_lab.reference_data`, with
  `BondType` / `BondStatus`, the separate `is_mvp_pricing_eligible(bond)`
  eligibility function, and `SYNTHETIC_BOND_FIXTURES` (four synthetic
  bonds: one eligible plain-vanilla bullet, one zero-coupon, one
  callable, one floating-rate note).

Nothing yet connects `BondOption.underlying_isin` to
`BondReferenceData`. `docs/20` §8 stated the *rule* — a future pricing
step resolves the ISIN against the fixture and must **block**, not
guess, on a missing or ineligible bond — but explicitly deferred
*designing the lookup/resolution mechanism itself* (`docs/20` §11). This
doc is that design step: it defines the boundary a future ISIN
resolution slice must respect, before any resolver code is written.

This doc does not implement a resolver. It prepares the next coding
slice the same way `docs/18` prepared `DepositLeg` and `docs/20`
prepared `BondReferenceData` — a reviewed boundary first, code second.

---

## 2. What is being resolved

**The question a future resolver answers:** given a
`BondOption.underlying_isin` string, is there a known
`BondReferenceData` record for it, and if so, is that record
MVP-pricing-eligible?

This is deliberately narrow. Restated from prior docs, not re-opened
here:

- **Product schemas must not embed `BondReferenceData`.**
  `BondOption` stores `underlying_isin` only (`docs/15` §2.1/§2.2,
  unchanged); `BondLinkedStructuredProduct` does not carry Bond Master
  terms either (`docs/19` §2/§3, unchanged). A future resolver is a
  lookup step that happens *outside* both schemas, at pricing time — it
  does not add a field to either schema, and does not require either
  schema to change.
- **`BondReferenceData` remains reference data, not product data and not
  market data.** It answers "what did the issuer promise" (docs/20 §9);
  it is not a deal term (that is `BondOption`'s/`DepositLeg`'s job) and
  it is not a market observation (that is a future `MarketDataSnapshot`
  / MVP input bundle's job, docs/20 §9's table). Resolution does not
  change what kind of data `BondReferenceData` is — it only looks a
  record up by `isin`.
- **Resolution is not pricing.** Resolving an ISIN to a reference-data
  record (or to a structured "not found" / "ineligible" result) produces
  no PV, no cashflow, no schedule. See §6.

---

## 3. Source boundary for this slice

```text
MVP source:            SYNTHETIC_BOND_FIXTURES
                        (shiori_pricing_lab.reference_data.fixtures)

Explicitly NOT in this slice:
  Bloomberg / BQL connector
  generic vendor API integration
  file parser (CSV / JSON / Excel)
  database / persistent store
  internal bond master system integration
  generic market-data ingestion
  screenshot-assisted / OCR data capture
```

A future resolver's *only* data source for the MVP is the existing,
already-merged `SYNTHETIC_BOND_FIXTURES` tuple. This is consistent with
`docs/16`'s API-first-but-file-minimal direction and `docs/20` §7's
"fixture boundary must be deterministic and reviewable" rule — nothing
about resolution changes that. A future resolver must not, as a side
effect, grow a parser, a connector, or a generic lookup-by-any-source
abstraction; it takes a fixture-shaped `Iterable[BondReferenceData]` (or
the module-level `SYNTHETIC_BOND_FIXTURES` default) and nothing else.

**The resolver must not care where the iterable came from.** Accepting
the fixture as a plain parameter (rather than importing
`SYNTHETIC_BOND_FIXTURES` internally as a hard-coded global) is the
recommended shape (§8) so that a future real source system can be
substituted later without changing the resolver's own logic — but
building that future source system is explicitly out of scope here.

---

## 4. Lookup behavior

The table below is the **behavioral contract** a future resolver must
satisfy. It is written in prose/pseudocode, not code — no function
signature is binding yet (§8 proposes one, non-bindingly).

| Case | Expected behavior |
| --- | --- |
| **Exact ISIN match, one record, eligible** | Resolution succeeds: the caller gets the matched `BondReferenceData` record and an eligible/true signal. |
| **Exact ISIN match, one record, ineligible** (see rows below for *why* a record is ineligible) | Resolution finds the record (it is not "missing") but reports it as **found-but-ineligible**, with the specific `is_mvp_pricing_eligible(bond).reasons` attached. This is a different outcome than "not found" — a future caller must be able to tell "no such bond" apart from "that bond exists but cannot be MVP-priced." |
| **Missing ISIN** (no record in the fixture has a matching `isin`) | Resolution fails explicitly with a **not-found** result. Never returns a default/placeholder bond. |
| **Duplicate ISIN in fixture** (more than one record shares an `isin`) | Resolution must **not** silently pick the first match. This is a **fixture data-integrity error**, not a normal lookup outcome — a future resolver must detect it and fail explicitly (e.g. a raised contract violation, mirroring how `pricing/errors.py` already distinguishes domain failures from contract violations, `docs/09` §8). It must never be resolved by "return the first one" or "return the last one." |
| **Inactive bond** (`BondReferenceData.status is BondStatus.INACTIVE`) | Found, not ineligible-by-absence — reported **found-but-ineligible**, using the existing `is_mvp_pricing_eligible` reason (`"status INACTIVE is not MVP-pricing-eligible"`, landed in PR #58's Codex fix). No separate "inactive" resolution status is needed; eligibility already carries this. |
| **Bond valid as reference data but not MVP-pricing-eligible** (the general case — callable, sinkable, zero-coupon, `OTHER` yield convention, non-`FIXED_COUPON_BULLET` `bond_type`, or inactive status, alone or combined) | Found-but-ineligible, with **all** applicable reasons from `is_mvp_pricing_eligible(bond).reasons` attached — not just the first one. `is_mvp_pricing_eligible` already returns every failing reason (PR #58); a resolver must not discard any of them. |
| **Unsupported `bond_type`** (`FLOATING_RATE_NOTE`, `AMORTIZING`, `CONVERTIBLE`, `INFLATION_LINKED`, `PERPETUAL`, `STRUCTURED_NOTE`) | Same as the general ineligible case above — `is_mvp_pricing_eligible` already encodes this via `bond_type`; the resolver adds no new bond-type logic of its own. |
| **Callable / sinkable bond** | Same as the general ineligible case — already covered by `is_mvp_pricing_eligible`. |
| **Zero-coupon bond** | Same as the general ineligible case — already covered by `is_mvp_pricing_eligible` (valid-but-ineligible, PR #58 §5 decision). |
| **`yield_convention == OTHER`** | Same as the general ineligible case — already covered by `is_mvp_pricing_eligible`. |

**The resolver must not re-implement eligibility logic.** Every
ineligibility case above already has a rule inside
`reference_data.eligibility.is_mvp_pricing_eligible`; a resolver's job is
to look the record up by ISIN and then call that existing function
once — not to duplicate, approximate, or partially re-derive its rules.
This keeps eligibility defined in exactly one place, matching how
`docs/20`'s own design already separated construction validation from
eligibility.

**No fuzzy ISIN matching.** Matching is exact-string only. No case
normalization beyond what `str ==` already does, no whitespace
trimming, no check-digit correction, no partial/prefix matching. An ISIN
typo is a **missing ISIN**, not a near-match hit.

---

## 5. Blocking rules

Restated and made explicit for this slice, extending `docs/20` §8's
rule:

```text
A missing or ineligible bond must BLOCK.

No guessing.
No fallback bond.
No silent downgrade (e.g. "treat callable as bullet").
No partial pricing (a resolver that only partially resolves a bond is
  not a valid outcome -- it is found+eligible, found+ineligible, or
  not-found, never something in between).
No "use the first fixture entry if the requested ISIN is not found."
No fuzzy/approximate ISIN matching.
No default currency, coupon, or convention substituted for a missing
  field (this cannot happen anyway, since BondReferenceData already
  requires every field at construction time -- but a resolver must not
  reintroduce a defaulting behavior at the lookup layer either).
```

A future pricing engine that calls the resolver and receives a
not-found or found-but-ineligible result must itself fail the pricing
request explicitly (the existing `PricingErrorCode.MISSING_REFERENCE_DATA`
member, added in PR #45 specifically for this gap, `docs/14` §3.2, is the
natural future error code — wiring it up is future pricing-engine work,
not this doc's).

---

## 6. Separation from pricing

The resolver described here answers exactly four questions, and nothing
else:

```text
found / not found
the reference data record (if found)
eligible / ineligible (if found), via the existing eligibility function
the blocking reason (if not found, or found-but-ineligible)
```

It must not compute, approximate, or stub out:

```text
PV
DV01
cashflows
a coupon schedule
a discounted price
a yield
scenario results
```

This mirrors the exact boundary `docs/09` §3 already enforces for the
existing pricing contract ("pricing engines must not fetch market data
directly," restated here as "a resolver must not price"): a resolver is
a **lookup step upstream of pricing**, callable by a future pricing
engine's input-resolution layer, not a pricing engine itself and not
part of the `price(...)` front door.

---

## 7. Separation from market data

None of the following belong on the resolver's inputs, outputs, or any
future result type — restated from `docs/20` §3's exclusion list,
because a resolution result could otherwise become a place these
quietly reappear:

```text
business_date
valuation_date
as_of_timestamp
clean_price
dirty_price
yield
spread
volatility
curve
discount_rate
funding_rate
quote_side
FTP rate
MarketDataSnapshot (or any reference to one)
```

A resolver answers "does this ISIN correspond to an MVP-eligible bond,"
given whatever reference-data set it was called with. It does not
itself observe a market, a curve, or a price. If a future need arises to
know whether a specific bond has market data available *for a specific
valuation date*, that is a materially different question belonging to a
future `MarketDataSnapshot` / MVP input bundle design, not this
resolver.

### 7.1 Point-in-time / as-of boundary (Codex P2 review of PR #59)

An earlier draft of this section described resolution as universally
"valuation-date-independent." That was too broad: `BondReferenceData.
status` (`ACTIVE`/`INACTIVE`) is part of `is_mvp_pricing_eligible`
(docs/20, PR #58), and a Bond Master record's status can itself change
over time (a bond active today may be marked inactive later, or vice
versa). If a future historical valuation resolved every date's pricing
against whichever reference-data set happens to be "current" at
resolver-call time, a later status change could leak into an earlier
valuation date — a look-ahead bias bug, not merely a style concern.

This doc draws the boundary as follows:

- **For the current MVP synthetic fixture:** it is acceptable to treat
  `SYNTHETIC_BOND_FIXTURES` as static and deterministic. It has no
  valuation-date dimension today — there is exactly one fixture, not one
  per date, and nothing in this slice changes that.
- **For any future historical valuation or real reference-data source:**
  the `fixtures` / `Iterable[BondReferenceData]` passed into the
  resolver **must already be point-in-time / as-of-correct for the
  intended valuation date** before the resolver is ever called. The
  resolver must **not** choose "the latest" reference data on a
  historical valuation's behalf, must **not** introduce or infer
  `business_date`, `valuation_date`, or `as_of_timestamp` (or any other
  market-data field, per this section's exclusion list) to make that
  choice itself, and must **not** otherwise become the place a
  point-in-time decision gets made. Selecting the as-of-correct
  reference-data set for a given valuation date is the responsibility of
  a future caller / input-resolution layer (upstream of the resolver),
  not the resolver.
- **The resolver's job stays exactly as narrow as §6 already states:**
  look up `underlying_isin` within whatever reference-data iterable it
  was already given, and call `is_mvp_pricing_eligible` on a match. It
  never decides *which* reference-data set is the right one for a
  valuation date — that decision is made before the resolver is called,
  by whoever calls it.

In short: for the current synthetic MVP fixture, resolution has no
valuation-date dimension. For future historical valuation, the
caller/input-resolution layer must supply an as-of-correct
reference-data iterable before calling the resolver — the resolver
itself never reasons about valuation dates, "latest" data, or as-of
timestamps.

---

## 8. Recommended next implementation slice

The smallest next coding slice, **not implemented by this PR**:

```text
def resolve_bond_reference_data(
    underlying_isin: str,
    fixtures: Iterable[BondReferenceData] = SYNTHETIC_BOND_FIXTURES,
) -> ResolutionResult:
    ...
```

Sketched only to make the boundary concrete — the exact name, parameter
order, default-argument choice, and result-type shape are implementation
decisions for that future slice, not fixed by this doc.

That slice should:

- do an exact-match scan of `fixtures` by `isin`;
- raise a contract-violation-style error (not a domain "not found"
  result) if more than one record shares the requested `isin` — a
  duplicate ISIN is a fixture data-integrity bug, not a normal lookup
  outcome (§4);
- on a single match, call the existing `is_mvp_pricing_eligible` and
  return a structured result carrying: the requested ISIN, a resolution
  status (e.g. found-eligible / found-ineligible / not-found), the
  matched `BondReferenceData` record (`None` if not found), the
  eligibility reasons (empty if eligible or not found), and nothing
  market-data-shaped (§7's exclusion list);
- on no match, return the not-found variant of that same result type —
  never raise for a legitimately missing ISIN (that is an expected
  domain outcome a caller must be able to handle, distinct from the
  duplicate-ISIN case above, which is a real bug);
- add tests only, for every row of §4's table plus the duplicate-ISIN
  case;
- add **no pricing, no payoff skeleton, no cash-flow generation, no
  schedule engine, no `MarketDataSnapshot`, no MVP input bundle, no
  Treasury FTP parser, no ingestion, no Bloomberg/API connector, no
  QuantLib, no UI, no screenshots, no product-schema change, no frozen
  v1.3 spec edit**;
- not close Issue #38.

### 8.1 Error / audit shape (conceptual only, docs-only in this PR)

A future resolution result may conceptually carry the following named
concepts — **naming and typing only, not implemented here**:

```text
requested_isin        -- the ISIN string the caller asked to resolve
resolution_status      -- e.g. FOUND_ELIGIBLE / FOUND_INELIGIBLE / NOT_FOUND
bond_reference_data     -- the matched record, or None
eligibility_reasons     -- tuple of strings from is_mvp_pricing_eligible,
                           empty when eligible or not found
block_reason            -- a short human-readable summary of why pricing
                           must block (derived from resolution_status /
                           eligibility_reasons, not a new independent
                           field a caller could set inconsistently)
source_fixture_name     -- which fixture/source the record came from
                           (e.g. "SYNTHETIC_BOND_FIXTURES"), audit
                           context only, never a market-data field
```

No `business_date`, `as_of_timestamp`, resolved rate, or any field from
§7's exclusion list belongs on this result. This section documents
*shape*, not a commitment to exact field names — the future
implementation slice decides the concrete type and can deviate from
this list if it explicitly says why.

---

## 9. Relationship to prior docs (no re-opening)

- `docs/15` (`BondOption`) and `docs/19`
  (`BondLinkedStructuredProduct`): unaffected. Neither schema changes as
  a result of this doc or its recommended next slice.
- `docs/20` (`BondReferenceData` preflight) and its PR #58
  implementation: `is_mvp_pricing_eligible` is reused as-is, not
  re-implemented. This doc does not add, remove, or change any
  eligibility rule.
- `docs/17` (`BLI MVP vertical slice preflight`) §11: this doc is a
  refinement of slice B's own trailing gap (the lookup/resolution
  mechanism explicitly deferred by `docs/20` §11), not a new lettered
  slice — it precedes the future pricing/payoff slices (E onward) that
  will actually call the resolver.
- `docs/14` F-08 (`m`/compounding-frequency gap for `yield_convention =
  OTHER`) and the Issue #37 `DayCount` vocabulary decision (A-14):
  carried forward, unresolved, exactly as `docs/20` §11 already stated.
  Resolution does not touch either.

---

## 10. Deferred items

Explicitly not decided or built by this doc:

- **The resolver function itself** — signature, exact result type,
  module location (e.g. `reference_data/resolution.py`) are choices for
  the implementation slice, not fixed here beyond §8's sketch.
- **Wiring the resolver into a future pricing engine or its
  input-resolution layer** — this doc defines the resolver's own
  boundary, not how or when a pricing engine calls it.
- **`PricingErrorCode.MISSING_REFERENCE_DATA` wiring** — the error code
  already exists (PR #45); connecting it to a resolver's not-found /
  ineligible outcomes is future pricing-engine work.
- **Any real source system beyond `SYNTHETIC_BOND_FIXTURES`** (Bloomberg,
  an internal bond master, a vendor security master) — `docs/20` §7
  already listed these for context only; this doc does not change that.
- **How a future caller / input-resolution layer selects an
  as-of-correct reference-data iterable for a given historical valuation
  date** (§7.1) — this doc states that the resolver itself must not make
  that choice, but does not design the selection mechanism (e.g. a
  future point-in-time-versioned Bond Master), which belongs to whatever
  future historical-valuation slice needs it.
- **The `DayCount` vocabulary decision (A-14)** and **`docs/14` F-08** —
  unrelated to resolution, carried forward unresolved.

---

## 11. Scope boundaries of this PR

Docs only. No resolver function, pricing, payoff skeleton, cash-flow
generation, schedule engine, `MarketDataSnapshot`, MVP input bundle,
Treasury FTP parser, ingestion, Bloomberg/API connector, QuantLib, UI,
screenshot capture, or product-schema change is added. No frozen BLI
v1.3 source spec file is edited. Issue #38 is unaffected and remains
open.
