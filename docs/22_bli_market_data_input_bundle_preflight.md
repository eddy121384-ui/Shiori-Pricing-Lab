# 22 BLI Market Data / MVP Input Bundle Preflight

Status: docs-only preflight. No `MarketDataSnapshot` class, MVP input
bundle class, bundle builder, pricing engine, payoff skeleton, cash-flow
generation, schedule engine, yield-to-price calculation, curve
interpolation, Treasury FTP parser, ingestion, Bloomberg/API connector,
QuantLib adapter, UI, screenshot/OCR capture, product-schema change, or
reference-data resolver change is added by this doc.

## 1. Why this doc exists

Four prior slices now exist:

- `BondOption`, `DepositLeg`, `BondLinkedStructuredProduct` (PRs #50,
  #54, #56) — the product-term schemas.
- `BondReferenceData`, `BondType`, `BondStatus`,
  `is_mvp_pricing_eligible` (PR #58, `docs/20`) — the reference-data
  schema, controlled vocabulary, and eligibility gate.
- `resolve_bond_reference_data` / `BondReferenceResolutionResult` /
  `BondResolutionStatus` (PR #60, `docs/21`) — the exact-ISIN-match
  resolver that answers found-eligible / found-ineligible / not-found
  for `BondOption.underlying_isin` against a caller-supplied
  reference-data iterable.

Nothing yet connects any of this to an actual market observation. A
future pricing engine cannot price a `BondLinkedStructuredProduct` from
product terms and reference data alone — it also needs the bond's
current clean price or yield, a discount curve, and the deposit leg's
funding rate, all as of a specific valuation date. `docs/17` §6/§7
listed the minimum reference-data and market-data field sets at a high
level; `docs/18` §2 detailed the Treasury FTP rate matrix; neither doc
designed the actual `MarketDataSnapshot` / MVP input bundle boundary.
This doc is that design step, following the same "preflight before
code" pattern already used for `DepositLeg` (`docs/18`),
`BondReferenceData` (`docs/20`), and ISIN resolution (`docs/21`).

This doc does not implement anything. It defines the boundary the next
several coding slices must respect.

---

## 2. The four-layer boundary

```text
1. Product terms         BondLinkedStructuredProduct / DepositLeg / BondOption
2. Reference data         BondReferenceData / resolve_bond_reference_data /
                           is_mvp_pricing_eligible
3. Market data             bond clean price/yield, yield curves,
                           deposit/FTP rate observations, curve mapping,
                           pricing date, source/status
4. Future input bundle     the single validated object a pricing engine consumes
```

Each layer answers a different question, restated from `docs/20` §9 and
extended:

```text
Product terms   -- what did the two counterparties agree to trade?
Reference data  -- what did the bond issuer promise, and is that bond
                    plain-vanilla enough for the MVP pricing pool?
Market data     -- what does the market say right now (or as of a
                    specific historical date)?
Input bundle    -- given one valuation context, do we have everything a
                    pricing engine needs, validated and blocked-on-gap?
```

Nothing in this doc moves a field between layers 1 and 2 — those
boundaries are already decided (`docs/15`, `docs/18`, `docs/19`,
`docs/20`) and are not re-opened here. This doc's job is to define layer
3 and layer 4, and the validation gate between "layers 1-3 exist" and
"layer 4 may be constructed."

---

## 3. What is a `MarketDataSnapshot` (BLI-scoped), conceptually?

A future BLI `MarketDataSnapshot` represents **market observations and
curve inputs for one specific valuation context** — one `business_date`
/ `valuation_date`, one `as_of_timestamp`, frozen and immutable, exactly
like the existing vanilla-rates-core `MarketDataSnapshot`
(`docs/02_data_and_market_snapshots.md`) is a frozen normalized dataset
for one valuation date. This doc does not redesign that existing concept
— it scopes what a *BLI* snapshot must additionally carry, conceptually:

```text
business_date / valuation_date / pricing_date
as_of_timestamp
source_system
bond market quote for the resolved underlying bond
  (clean_price_per_100 and/or yield, per Annex B §B.1)
yield curve data / curve references
  (per Annex B §B.2, and SPEC §3.5/§7.3's curve-purpose distinctions, §7)
deposit / FTP rate observation
  (per docs/18 §2, when DepositLeg.deposit_rate_mode is
  TREASURY_FTP_REFERENCE)
option volatility / used volatility, with an explicit vol basis
  (per SPEC §§3.2/3.3/7.4, §6.5)
credit spread / spread adjustment, if required by mapping/methodology
  (per SPEC §7.5, §6.6)
quote side / price type
status / data-quality flag
```

**No class is implemented here.** This is a conceptual field list only,
grounded in Annex B §B.1 (Bond Price/Yield File) and §B.2 (Yield Curve
File), which already enumerate concrete field names a future
implementation slice should confirm against, not re-derive from
scratch.

---

## 4. Product schema vs. reference data vs. market data: the exclusion lists

Restated and consolidated from `docs/04`, `docs/15` §2.2, `docs/18` §8,
`docs/19` §9, and `docs/20` §3, because a market-data/input-bundle
design is exactly the kind of change that could accidentally blur these
boundaries by "just adding one convenience field."

### 4.1 Product schema (`BondOption`, `DepositLeg`,
`BondLinkedStructuredProduct`) must not contain

```text
business_date
valuation_date
as_of_timestamp
clean price
yield
curve rate
FTP rate
source system
resolved market quote
pricing result
```

This is unchanged by this doc — all three product schemas already
satisfy this (`docs/15` §2.2, `docs/18` §8, `docs/19` §9), and this doc
does not modify any of them.

### 4.2 Reference data (`BondReferenceData`) must not contain

```text
pricing-date market quote
clean price
yield observation
curve observation
FTP observation
PV / result
```

Also unchanged by this doc — `docs/20` §3 already excludes all of these,
and `BondReferenceData` (PR #58) satisfies that boundary today. Restated
here only so the market-data design does not accidentally treat
`BondReferenceData` as a convenient place to cache a market observation.

### 4.3 Market data must not rewrite

```text
coupon
maturity_date
issue_date
first_coupon_date / last_coupon_date
bond_type
callable_flag / sinkable_flag
product notional
strike
settlement rules
```

This is the boundary this doc adds explicitly: a market-data snapshot
observes prices, yields, curves, and rates — it never carries, infers,
or overrides a bond's own static terms (those are `BondReferenceData`'s
job, already resolved by `resolve_bond_reference_data`) or a product's
deal terms (those are `BondOption`/`DepositLeg`/
`BondLinkedStructuredProduct`'s job). A future bundle builder that
"corrects" a coupon or notional from a market-data file would be a
methodology violation, not a data-quality fix — any such mismatch must
be a blocking validation error (§8), never a silent overwrite.

---

## 5. MVP input bundle, conceptually

The future MVP input bundle is the single object produced by combining:

```text
one BondLinkedStructuredProduct                (already validated at
                                                 construction, docs/19)
resolved BondReferenceData                      (via
                                                 resolve_bond_reference_data,
                                                 docs/21)
resolver status / eligibility status            (BondResolutionStatus +
                                                 EligibilityResult.reasons)
one point-in-time MarketDataSnapshot            (§3)
explicit curve selections / mappings            (§7)
explicit assumptions and validation results      (§8/§9)
```

**This is what a future pricing engine consumes** — not the product, not
the reference data, and not the market snapshot individually. This
mirrors the existing spine contract (`docs/09` §1: `Product Definition +
ValuationContext + MarketDataSnapshot → price(...) → PricingResult`) —
the BLI input bundle is the BLI-specific instantiation of "everything
`price(...)` needs to actually price this product," not a second,
parallel contract.

### 5.1 The bundle must not be constructed if

```text
product schema validation fails
underlying_isin is not found                    (BondResolutionStatus.NOT_FOUND)
resolved bond is ineligible                      (BondResolutionStatus.FOUND_INELIGIBLE)
required bond price/yield market data is missing
required curve mapping is missing                (SPEC §7.3: "若找不到 curve
                                                   mapping ... pricing blocked")
required deposit / FTP rate is missing
required option volatility is missing            (SPEC §§3.2/3.3/7.4, §6.5)
required credit spread is missing                (SPEC §7.5, §6.6)
volatility basis is ambiguous or unrecorded      (§6.5)
credit spread treatment is ambiguous or unrecorded
                                                  (§6.6)
quote side is ambiguous
an override or fallback (vol, spread, quote side, or otherwise) exists
  without an explicit audit record
data status is inactive / stale / invalid
```

Every row above is a **hard block**, not a warning: a bundle either
exists complete and valid, or it does not exist at all. This restates
`docs/21` §5's "missing or ineligible bond must block" rule and extends
it to every other required input this doc introduces. A future
implementation must not return a "partial bundle" that a pricing engine
could accidentally consume as if it were complete.

---

## 6. Required market-data categories for BLI MVP

Grounded in Annex B and SPEC §3.5/§7.3 (frozen sources, not re-derived):

### 6.1 Bond price/yield data

For the exact `isin` resolved by `resolve_bond_reference_data`
(`docs/21`) — never a different bond, never a proxy. Annex B §B.1's
field list (`business_date`, `as_of_timestamp`, `isin`, `currency`,
`clean_price_per_100`, `yield`, `accrued_interest_per_100`,
`source_system`, `price_type`, `status`) is the reference shape a future
implementation should confirm against.

### 6.2 Yield curve data

Per SPEC §3.5 and §7.3, BLI distinguishes **at least** these curve
purposes — they are separate concepts with separate curve IDs, not
interchangeable:

| Curve purpose | Used for |
| --- | --- |
| Bond Reference Curve | Bond forward clean price, yield-to-price conversion, bond valuation (incl. credit spread) |
| Option Discount Curve | Discounting option payoff / premium / PV |
| Deposit Curve | Deposit leg discounting / funding calculation |
| Funding Curve | Funding adjustment, if applicable per mapping |

**Explicit rules restated from the frozen spec, not invented here:**

- **Option Discount Curve and Bond Reference Curve are separate
  concepts and must not be mixed** (SPEC §3.5: "Discount Curve 與 Bond
  Reference Curve 不可混用").
- **The deposit leg must not silently reuse the Option Discount Curve**
  unless an explicit mapping rule says so (SPEC §3.5: "Deposit leg 不得
  直接共用 Option Discount Curve，除非 mapping rule 明確設定").
- **Curve selection is a mapping decision**, keyed at minimum by
  currency, book, desk, product type, pricing purpose, effective date,
  and status (SPEC §7.3) — a future bundle builder resolves *which*
  curve ID applies for *which* purpose from an explicit mapping table,
  never by guessing "there's only one curve for this currency so it
  must be the right one for every purpose."
- **Missing curve mapping blocks bundle creation** (SPEC §7.3: "若找不到
  curve mapping 或 curve data invalid，pricing blocked") — restated in
  §5.1/§8 above/below.

Annex B §B.2's field list (`business_date`, `as_of_timestamp`,
`curve_id`, `curve_name`, `currency`, `curve_type`, `tenor`, `rate`,
`day_count`, `compounding`, `interpolation_method`, `source_system`,
`status`) is the reference shape a future implementation should confirm
against.

### 6.3 Deposit / FTP reference rate data

Required when `DepositLeg.deposit_rate_mode` is
`TREASURY_FTP_REFERENCE` (`docs/18` §4.2). The resolved rate for the
leg's `ftp_rate_selector` (currency/tenor/quote_side) is market data,
resolved per pricing run from the snapshot — never stored on
`DepositLeg` itself (unchanged, `docs/18` §2.1/§4.2, restated in §4.1
above).

### 6.4 Manual verified rate audit input

Required when `DepositLeg.deposit_rate_mode` is `MANUAL_VERIFIED_RATE`
(`docs/18` §4.3). The actual manual rate value and its audit metadata
(source, as-of, entered-by, run id) belong to this future input-bundle /
audit layer, not `DepositLeg.manual_input_reference` (which stays an
opaque marker only, unchanged).

### 6.5 Option volatility input

**Missing from the original version of this doc (Codex P2 review of
PR #61) — added here.** BLI option valuation requires an explicit
volatility input, or an explicitly audited override, for every priced
option; this is not optional MVP scope creep, it is a required pricing
input the frozen spec already states (SPEC §§3.2/3.3/7.4, `docs/17` §7).
This doc does not implement volatility handling — it states the
boundary a future bundle must satisfy:

```text
volatility (or "used volatility") must be an explicit market-data /
  input-bundle field -- never invented, never silently defaulted.
the volatility basis used for pricing must be explicit and recorded
  (e.g. YIELD_VOL / PRICE_VOL / EQUIVALENT_PRICE_VOL, per SPEC §7.4's
  vocabulary -- transcribed for context, not re-derived or extended
  here).
a yield-vol-to-price-vol conversion, if the selected pricing
  methodology needs one, must be recorded as a conversion (basis,
  mode, formula version), never silently substituted as if it were an
  observed price vol (SPEC §3.3 point 4).
no silent fallback to a flat vol (SPEC §3.3 point 5: "不得 silent
  fallback 到 flat vol；任何 fallback 必須在 Internal Pricing Report
  顯示") -- a flat-vol or manual-override fallback is only acceptable
  if explicitly configured and explicitly recorded, never a silent
  default (SPEC §7.4's vol hierarchy already lists "Flat Vol, only if
  explicitly configured" and "Manual Override Vol" as the last two,
  least-preferred tiers, not defaults).
no use of stale volatility without an explicit stale-data policy /
  assumption record (same "no stale data without an explicit
  assumption" rule this doc already applies to every other market
  observation, §7).
if volatility is missing and required for the selected pricing
  methodology, bundle construction must BLOCK -- it must never proceed
  with an assumed, interpolated-from-nothing, or zero volatility.
```

**No volatility surface, vol interpolation, or yield-vol-to-price-vol
conversion is implemented by this doc.** This section only states that
a future `MarketDataSnapshot` / input bundle must carry an explicit
volatility input and basis, and that a future bundle builder must block
on a missing one — the actual vol surface/curve representation and
conversion methodology are future implementation-slice work, out of
scope here exactly as they were before this fix.

### 6.6 Credit spread / spread adjustment

**Also missing from the original version of this doc (Codex P2 review
of PR #61) — added here.** Credit spread is a required market-data /
input-bundle category **if required by the selected mapping or pricing
methodology** (SPEC §7.5, `docs/17` §7) — not always required, but never
silently assumed to be zero or "not applicable" when it is required.

```text
credit spread must not silently default to zero (SPEC §7.5: "Credit
  spread 不可 silent default to zero，除非明確設定並顯示 assumption").
if credit spread is required by the selected mapping/methodology and
  missing, bundle construction must BLOCK.
any spread override, fallback (e.g. down the SPEC §7.5 priority chain
  of bond-specific -> issuer -> rating/sector proxy -> manual override),
  or an explicit "spread not applicable" decision must be recorded as
  an audited assumption, never applied silently.
if credit spread is already embedded in a selected bond quote or curve
  methodology (so no separate spread input is needed), that must be an
  explicit statement made by the future implementation slice that
  designs the actual bundle -- this doc does not assume embedding
  either way, and a future bundle builder must not silently assume
  "the curve probably already has it in there somewhere."
```

**No spread model, spread mapping table, or spread-to-price adjustment
is implemented by this doc.** This section only states that a future
bundle must carry an explicit credit-spread input (or an explicit,
audited "not required" decision) when the methodology needs one, and
that a missing required spread blocks bundle construction.

---

## 7. Quote side / price type policy

Restated from `docs/18` §5 and extended to bond price/yield data, since
the same silent-choice risk applies to both:

```text
Do not silently choose quote side.
Do not silently convert BID to MID.
Do not silently use latest data.
Do not use stale data without an explicit status/assumption record.
```

- **Default quote side is `MID`** for Treasury FTP rates (`docs/18`
  §2.4/§5, unchanged); the same default-plus-configurable-override
  pattern applies to any bond price/yield `price_type` /
  `quote_side`-shaped field a future snapshot carries.
- **A currency/instrument with no bid/mid/offer breakdown is treated as
  MID-equivalent only when explicitly documented as such** — this is a
  recorded normalization, not an inferred spread (`docs/18` §2.4/§5).
- **`status` must be checked, not ignored.** Annex B's own files (§B.1,
  §B.2) carry a `status` field; a future bundle builder must treat a
  non-acceptable status (however that vocabulary is eventually defined —
  §11 leaves it open) as a blocking condition, not a warning to be
  logged and skipped past.
- **Source system is audit context, not a silent tiebreaker.** If more
  than one `source_system` could plausibly supply the same observation,
  a future implementation must pick one explicitly and record which, not
  silently prefer "whichever loaded last."

---

## 8. As-of / point-in-time policy

This section exists specifically because of the Codex P2 finding fixed
in `docs/21` §7.1 (PR #59) — that finding was about the *resolver*, and
this section makes the same principle explicit for the *market-data and
bundle* layer, since the risk is structurally identical.

```text
MarketDataSnapshot is point-in-time.
The reference-data iterable supplied to resolve_bond_reference_data
  must already be point-in-time / as-of-correct for the intended
  valuation date (docs/21 §7.1, restated, not re-opened).
Market data and reference data must share a coherent valuation context
  -- the same business_date/valuation_date, not two different "current"
  states silently combined.
A future bundle builder must not mix "latest" reference data with
  historical market data, or vice versa.
No look-ahead bias: a bundle for valuation date D must never be built
  from reference data or market data that only became known after D.
```

**Whose job is this?** Exactly as `docs/21` §7.1 concluded for the
resolver: **the caller / a future input-resolution layer**, not the
resolver and not (by extension) the future `MarketDataSnapshot` class
itself. `MarketDataSnapshot` records one `business_date` and is
immutable once built — it does not reach out and "pick" data; something
upstream must supply it a coherent, already-as-of-correct set of
reference data and market observations. A future bundle builder's job is
to **validate** that the reference-data resolution and the market-data
snapshot it is given agree on the same valuation date (§9) — not to
select or reconcile which point-in-time state to use.

---

## 9. Treasury FTP rate boundary

Restated in full from `docs/18` §2.2/§2.4, because a market-data/bundle
design is exactly where this rule could be silently violated by a
careless unit conversion:

```text
Treasury FTP values are percentages: 3.5500 means 3.5500%, i.e. decimal
  0.035500 -- never treat 3.5500 as a 3.55x decimal rate.
Some currencies publish BID/MID/OFFER; others publish a single rate.
A single published rate may be treated as MID-equivalent only if that
  normalization is explicitly documented, not silently assumed.
The resolved FTP rate value (and its business_date) belongs to market
  data / the input bundle, never to DepositLeg itself (docs/18 §2.1,
  §4.2, unchanged, not re-opened here).
```

---

## 10. Bundle validation gates (conceptual, future work)

The full gate list a future bundle builder must check before a bundle
may exist, consolidating §5.1 with the product/wrapper-level checks that
already exist:

```text
1. product valid                          (BondOption / DepositLeg /
                                            BondLinkedStructuredProduct
                                            construction already
                                            validates this, docs/15/18/19)
2. wrapper currency consistency valid      (already enforced at
                                            BondLinkedStructuredProduct
                                            construction, docs/19)
3. resolver status is FOUND_ELIGIBLE       (docs/21; FOUND_INELIGIBLE and
                                            NOT_FOUND both block)
4. market snapshot valuation context present
                                            (business_date/valuation_date
                                            + as_of_timestamp exist and
                                            are coherent with the
                                            reference-data as-of date, §8)
5. bond price/yield available for the exact resolved ISIN
6. curve mapping available for each required curve purpose
                                            (Bond Reference Curve, Option
                                            Discount Curve, Deposit
                                            Curve, and Funding Curve if
                                            mapped, §6.2)
7. deposit rate available (FIXED_RATE value already on DepositLeg,
   or a resolved TREASURY_FTP_REFERENCE rate, or a MANUAL_VERIFIED_RATE
   audit record present) -- matching DepositLeg.deposit_rate_mode
8. option volatility available, with an explicit recorded vol basis, OR
   an explicit audited override / methodology exemption (§6.5) -- no
   silent volatility fallback (no invented value, no silent flat-vol
   default, no unrecorded yield-vol-to-price-vol conversion)
9. credit spread available if required by the selected mapping/
   methodology, OR an explicit audited "not required / embedded /
   not applicable" decision (§6.6) -- no silent zero-spread fallback
10. quote side explicit                    (never ambiguous or silently
                                            defaulted without recording
                                            which side was used, §7)
11. source / status acceptable             (§7; a future implementation
                                            slice defines the acceptable-
                                            status vocabulary, §11)
12. no stale / inactive data, unless a future, explicit policy allows it
                                            (§14 -- not decided here)
```

Gates 1-2 already exist today at product-schema construction time; gates
3 onward are new work this doc scopes but does not implement. **Any
single failed gate blocks bundle creation entirely** — this doc does not
introduce a partial-bundle or best-effort concept (§5.1).

---

## 11. Error / audit shape (conceptual only)

A future bundle-construction failure may conceptually carry one of the
following categories — **naming only, not implemented here**, following
the same "shape, not commitment" caveat `docs/21` §8.1 used for the
resolver's result:

```text
product validation error
reference data not found
reference data ineligible
missing bond market quote
missing curve mapping
missing curve tenor/rate
missing FTP/deposit rate
missing volatility input
ambiguous volatility basis
missing credit spread
ambiguous credit spread treatment
unauthorized silent fallback / default
ambiguous quote side
stale/as-of mismatch
unsupported convention
source status invalid
```

Several of these already have a natural home in the existing pricing
contract: `PricingErrorCode.MISSING_REFERENCE_DATA` (PR #45) fits
"reference data not found/ineligible"; `PricingErrorCode.
MISSING_MARKET_DATA` (existing, `docs/09` §8) fits "missing bond market
quote / missing curve mapping / missing FTP rate / missing volatility
input / missing credit spread." **"unauthorized silent fallback /
default" is added specifically for the vol/spread gap this section
fixes (Codex P2 review of PR #61):** an unrecorded flat-vol substitution
or an unrecorded zero-spread default are not the same failure as "data
is absent" — they are a *methodology* violation (a value was used
without the audit trail SPEC §3.3/§7.5 require), so a future
implementation slice should confirm whether this needs its own code or
folds into `MISSING_REFERENCE_DATA`/`MISSING_MARKET_DATA` with a
distinguishing `detail` payload. A future implementation slice should
confirm whether the existing codes are sufficient or whether BLI's
bundle layer needs additional, more granular codes (e.g. distinguishing
"curve mapping missing" from "curve mapping present but curve data
invalid," or "volatility missing" from "volatility present but basis
ambiguous") — that is left open, not decided here.

---

## 12. Recommended future implementation sequence

Small, independently reviewable slices, matching the "preflight, then
smallest useful version" pattern already used for every prior BLI slice:

```text
1. MarketDataSnapshot schema docs/code preflight
   -- confirm the BLI-scoped field list (§3/§6) against Annex B/SPEC
   again, INCLUDING the volatility (§6.5, SPEC §§3.2/3.3/7.4) and
   credit-spread (§6.6, SPEC §7.5) fields and their audit-record
   treatment -- both must be confirmed and resolved before any class is
   written, not treated as an afterthought once price/curve/FTP fields
   are done. Resolve open items from §11/§14 below, before any class is
   written.
2. Minimal MarketDataSnapshot dataclass, synthetic fixture only
   -- mirrors BondReferenceData's PR #58 pattern: schema + validation +
   a small, manually reviewed synthetic fixture, no parser, no
   ingestion, no Bloomberg/API connector.
3. MVP input bundle docs/code
   -- the bundle type itself (§5), with the validation gates (§10) as
   its acceptance-criteria checklist.
4. Bundle builder combining product + resolver + synthetic market data
   -- the function/class that actually runs the gates in §10 and
   either returns a valid bundle or a structured block result (§11);
   tests only, still no pricing.
5. Only then: pricing engine skeleton
   -- consumes the bundle from step 4; out of scope for every slice
   this doc recommends.
```

None of steps 1-5 is started by this PR.

---

## 13. Relationship to prior docs (no re-opening)

- `docs/02_data_and_market_snapshots.md`: the existing vanilla-rates-core
  `MarketDataSnapshot` concept and its invariants (explicit valuation
  date, no system date, defensive copies) are the pattern a future BLI
  snapshot should follow, not replace. This doc does not redesign that
  existing module.
- `docs/15`, `docs/18` §8, `docs/19` §9, `docs/20` §3: the product-schema
  and reference-data exclusion lists (§4.1/§4.2 above) are restated, not
  changed.
- `docs/18` §2, §4, §5: the Treasury FTP rate matrix format,
  percent-vs-decimal rule, and quote-side policy are restated (§7, §9),
  not changed.
- `docs/21` §7.1 (Codex P2 fix, PR #59): the point-in-time boundary this
  doc's §8 extends from the resolver to the market-data/bundle layer.
  This doc does not weaken or reinterpret that finding — it applies the
  same reasoning one layer up.
- `docs/17` §7/§10: the original high-level "minimum market data" and
  "MVP audit trail" field lists this doc refines with the actual Annex B
  field names and the curve-purpose distinctions SPEC §3.5/§7.3 add.
  `docs/17` §7 already named "volatility input" and "credit spread if
  required" as minimum market-data inputs; §6.5/§6.6 (added by the
  Codex P2 fix to this doc) are where that high-level naming is finally
  detailed against the frozen spec, closing a gap this doc's original
  version left open.
- SPEC §§3.2/3.3/7.4 (volatility) and §7.5 (credit spread): frozen
  methodology sections transcribed, not edited, for §6.5/§6.6's
  no-silent-fallback and no-silent-zero-spread rules.

---

## 14. Deferred items

Explicitly not decided or built by this doc:

- **The `MarketDataSnapshot` (BLI-scoped) class itself** — fields,
  module location (e.g. a BLI-specific snapshot vs. extending the
  existing `data/snapshot.py` concept), and validation are choices for
  implementation slice 1/2 (§12), not fixed here beyond §3's conceptual
  list.
- **The MVP input bundle class itself and its bundle builder** —
  slices 3/4 (§12).
- **The acceptable-status vocabulary** (§7, §10 gate 11, §11) — this doc
  states that status must be checked, not what values are acceptable.
- **Whether existing `PricingErrorCode` members are sufficient for the
  bundle layer, or new, more granular codes are needed** (§11).
- **Exact curve-mapping table shape** (keys: currency/book/desk/product
  type/pricing purpose/effective date/status, per SPEC §7.3) — this doc
  states the required dimensions, not the concrete schema.
- **Whether a stale-but-explicitly-allowed override policy will ever
  exist** (§10 gate 12) — this doc does not design one; today, stale or
  inactive data blocks.
- **The concrete volatility-basis vocabulary and vol-hierarchy fallback
  policy** (§6.5) and **the concrete credit-spread mapping priority
  chain** (§6.6) — this doc states that both must be explicit and
  audited when required, and carries the frozen spec's own vocabulary
  (SPEC §§3.3/7.4/7.5) for context, but does not design the future
  `MarketDataSnapshot`/bundle's concrete fields or fallback-selection
  logic for either.
- **The `DayCount` vocabulary decision (A-14)** and **`docs/14` F-08**
  (`m`/compounding-frequency gap) — unrelated to this doc, carried
  forward unresolved.

---

## 15. Scope boundaries of this PR

Docs only. No `MarketDataSnapshot` class, MVP input bundle class, bundle
builder, pricing engine, payoff skeleton, cash-flow generation, schedule
engine, yield-to-price calculation, curve interpolation, Treasury FTP
parser, ingestion, Bloomberg/API connector, QuantLib adapter, UI,
screenshot/OCR capture, product-schema change, or reference-data
resolver change is added. `BondOption`, `DepositLeg`,
`BondLinkedStructuredProduct`, `BondReferenceData`, and
`resolve_bond_reference_data` are all unmodified. No frozen BLI v1.3
source spec file is edited — this doc only reads and transcribes Annex B
§B.1/§B.2 and SPEC §3.5/§7.3. Issue #38 is unaffected and remains open.
