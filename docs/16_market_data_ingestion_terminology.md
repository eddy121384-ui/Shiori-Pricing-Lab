# 16 Market Data Ingestion Terminology

Status: docs-only terminology / policy clarification. No implementation.

## 0. Why this doc exists

A terminology problem was found across BLI specs and docs: "FTP" was often
used as if it meant a broad, generic file-based market-data import mechanism.
In this project's business context, **"Treasury FTP" means Funds Transfer
Pricing** — an internal funding-cost curve, usually a currency × tenor × rate
table. That is a different concept from FTP/SFTP file transport and different
from generic market-data file import.

This doc disambiguates the terms and states the ingestion policy so future
agents do not build a generic FTP-file-import market-data warehouse by
accident.

## 1. Ingestion hierarchy

```text
1. API-based Market Data Ingestion
   - Bloomberg API / BQL
   - vendor APIs
   - internal service APIs
   - structured data connectors
   This is the preferred path for external market data.

2. Manual File Upload
   - Minimized by default.
   - The first expected MVP manual-upload surface is Treasury FTP / Funding Curve.
   - Do not assume bond price, yield curve, volatility, credit spread, Bond Master,
     or calendar should be manually uploaded unless a later issue explicitly
     requires it.

3. Screenshot-assisted Data Capture
   - Future fallback helper only.
   - Used when API is unavailable and no structured export/file exists.
   - Must produce provisional data that requires user review and provenance.
   - See `docs/future_screenshot_assisted_data_capture.md` for the existing
     concept note; do not duplicate it here.

4. Manual Override / Manual Entry
   - Final fallback.
   - Must be audited.
   - Must never silently overwrite official source data.
```

## 2. Glossary

```text
FTP / SFTP transport:
Technical file-transfer protocol only. It means "how a file is moved," not
the business meaning of Treasury FTP.

Market Data Ingestion:
The broader process by which data enters the system. It may be API-based,
file-based, screenshot-assisted, or manually entered.

API Connector:
A source connector such as Bloomberg API / BQL, vendor API, internal API, or
other structured data service.

File-based Import:
A fallback or future ingestion method where a structured file is uploaded or
received. It should not be treated as the default market-data architecture.

Treasury FTP / Funding Curve:
Business funding-cost input, usually currency × tenor × rate. It is not the
same as FTP/SFTP file transport and not the same as generic market-data file
import.

Screenshot-assisted Data Capture:
A future fallback ingestion helper for screen-only data. It is not a source
of truth and must require review before use.
```

## 3. Rule for future docs and issues

```text
Do not use "FTP" alone in future docs or implementation issues.

Use:
- "FTP/SFTP transport" when referring to file-transfer protocol.
- "Market Data Ingestion" when referring to the overall data-entry process.
- "API Connector" when referring to Bloomberg API / BQL or vendor APIs.
- "File-based Import" when referring to uploaded or batch files.
- "Treasury FTP" or "Funding Curve" when referring to internal funding-cost
  rates.
```

## 4. Product direction

```text
Shiori Pricing Lab is API-first and file-minimal.

External market data should prefer API-based ingestion where available.
Manual file upload is not the default path for ordinary external market data.
The first MVP manual-upload candidate is Treasury FTP / Funding Curve.
Generic file import for bond price/yield, curves, vol, spread, Bond Master,
or calendar must not be implemented unless a later reviewed issue explicitly
asks for it.
```

## 5. Relationship to BLI / Issue #38

```text
Issue #38 BondOption implementation does not depend on Treasury FTP / Funding
Curve.
BondOption must not touch Market Data Ingestion, File-based Import, Treasury
FTP, Funding Curve, Deposit Curve, BondLinkedStructuredProduct, deposit leg,
market snapshots, pricing engines, Bloomberg connectors, or screenshot
capture.

The terminology cleanup is a guardrail for future deposit-leg / funding-curve
/ market-data work, not a prerequisite for BondOption pricing logic.
```

## 6. Scope of this doc

This doc is terminology and policy only. It does not implement Bloomberg
API / BQL, any API connector, file upload, Treasury FTP / Funding Curve, or
screenshot capture. It does not modify the pricing engine, product schemas,
market snapshots, Bond Master, valuation context, or Black-76 methodology.
See `docs/14_bond_linked_spec_teardown_and_integration_preflight.md` for the
existing BLI market-data readiness teardown (unchanged by this doc, aside
from a forward-looking terminology note).
