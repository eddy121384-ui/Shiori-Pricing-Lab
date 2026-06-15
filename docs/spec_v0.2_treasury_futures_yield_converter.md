# Treasury Futures Yield Converter Specification v0.2

## 1. Purpose

This feature adds a small trader workflow tool for converting between:

- CBOT Treasury futures price;
- CTD cash bond clean price;
- CTD implied yield-to-maturity;
- futures price implied by a target CTD yield.

The core desk question is simple:

> If the Treasury futures price is here, where is the CTD yield? If the boss asks for a target yield, what futures price is that?

## 2. Reference methodology

CME Treasury Analytics is the methodology anchor for this workflow. CME describes the tool as showing Treasury product analytics including deliverable baskets, CTD/OTR securities, futures/cash yield curves, and conversion between strike prices and implied yields.

For futures implied yield, CME defines the yield as the yield-to-maturity of the CTD cash security using these inputs:

- settlement date = last delivery day for the futures contract;
- maturity date = maturity date of the CTD cash security;
- coupon rate = annual coupon rate of the CTD cash security;
- bond price = futures price × conversion factor + accrued coupon interest;
- coupon frequency = semiannual;
- day count basis = Actual/Actual;
- par value = 100.

This repo implements the same conceptual workflow, but it remains a personal research and sanity-check tool, not an official valuation system.

## 3. In scope

### 3.1 Deterministic pricing helpers

Add a Python module under `src/shiori_pricing_lab/pricing/` that can:

- parse Treasury futures prices in common 32nds notation:
  - `110-16`;
  - `110-16+`;
  - `110-165`;
  - `110-16.5`;
  - decimal price input;
- convert futures price into CTD clean price using conversion factor;
- calculate accrued interest using a semiannual Actual/Actual coupon-period approximation;
- calculate clean price from yield;
- solve yield from clean price;
- calculate implied CTD yield from futures price;
- calculate futures price from target CTD yield.

### 3.2 Browser HTML tool

Add a local HTML tool under:

```text
src/shiori_pricing_lab/app/treasury_futures_yield_converter.html
```

The tool should:

- load the normalized CTD cache if served from the repo root;
- fall back to an embedded synthetic sample if the browser blocks local file loading;
- allow manual CTD JSON paste;
- allow manual override of futures price, conversion factor, coupon, maturity, settlement date, net basis, and target yield;
- display parsed futures price, CTD clean price, implied yield, and target-yield futures price.

### 3.3 CTD data cache

Add a normalized JSON cache under:

```text
data/cme_ctd_latest.json
```

The cache should contain only normalized fields required by the converter:

```json
{
  "updated_at": "2026-06-15T08:00:00Z",
  "source_url": null,
  "records": [
    {
      "contract_code": "ZNM6",
      "futures_symbol": "ZN",
      "delivery_month": "2026-06",
      "ctd_cusip": "SAMPLE-ZN-CTD",
      "coupon_rate": 0.04,
      "maturity_date": "2034-05-15",
      "conversion_factor": 0.9012,
      "last_delivery_date": "2026-06-30",
      "source": "synthetic sample",
      "description": "Synthetic 10Y Treasury futures CTD example for development only"
    }
  ]
}
```

Synthetic sample data is allowed for tests and UI development. Real or licensed data should not be committed unless its redistribution is explicitly permitted.

### 3.4 CME update workflow

Add a script:

```text
scripts/update_cme_ctd.py
```

The script should:

- accept a configured `CME_CTD_SOURCE_URL`;
- fetch JSON or CSV;
- normalize likely CTD field names into the cache schema;
- write `data/cme_ctd_latest.json`.

Add a GitHub Actions workflow:

```text
.github/workflows/update-cme-ctd.yml
```

The workflow should:

- run on schedule and manual dispatch;
- read `CME_CTD_SOURCE_URL` from workflow input, repository variable, or secret;
- commit `data/cme_ctd_latest.json` only if it changed;
- skip gracefully if no URL is configured.

## 4. Out of scope

- Hard-coding a non-public CME internal endpoint.
- Committing CME credentials or entitlement-protected raw data.
- Treating the result as official settlement, accounting, risk, P&L, or compliance output.
- Full CTD optimization from the whole deliverable basket.
- Implied repo calculation.
- Bloomberg integration.
- Futures option strike grid and option analytics.

## 5. Known limitations

- The first version assumes semiannual coupons.
- Actual/Actual is implemented as a coupon-period approximation, not a full QuantLib-grade schedule engine.
- CTD selection itself is not calculated. The tool consumes a current CTD record.
- Browser-side URL fetches may fail because of CORS. The reliable automatic path is the GitHub Actions cache update.
- CME data access can depend on login state, entitlement, or official market-data channels, so the source URL is configurable rather than hard-coded.

## 6. How to use locally

From the repository root:

```bash
python -m http.server 8000
```

Then open:

```text
http://localhost:8000/src/shiori_pricing_lab/app/treasury_futures_yield_converter.html
```

To refresh the CTD cache from a configured source:

```bash
CME_CTD_SOURCE_URL="https://example.com/ctd.json" python scripts/update_cme_ctd.py
```

## 7. Testing

Unit tests should cover:

- futures price parsing;
- futures price formatting;
- futures-to-clean-price conversion;
- clean-price/yield roundtrip;
- futures-price/yield roundtrip.
