# Future Capability — Screenshot-assisted Data Capture

Status: future capability / docs-only concept note.
No implementation is included.
No pricing engine, product schema, market snapshot, OCR pipeline, UI, or model integration is defined here.

## 1. Purpose

Shiori Pricing Lab should be **API-first** for external market data whenever possible. Bloomberg API / BQL, vendor APIs, internal APIs, and other structured connectors should be preferred over manual data handling.

However, some useful market or internal reference data may not be available through API, may not have an exportable file, or may only be visible on a screen such as an internal web portal, vendor terminal, spreadsheet view, PDF viewer, Bloomberg screen, or legacy system UI.

For those cases, the system may eventually support **Screenshot-assisted Data Capture**: a fallback ingestion helper that lets the user capture a screen, extract visible tabular or labeled data, review the extracted values, and convert them into a structured staging record.

This capability is intended to reduce manual retyping, not to bypass market-data governance.

## 2. Position in the data-ingestion hierarchy

The intended data-ingestion policy is:

1. **API-based ingestion first**
   - Bloomberg API / BQL
   - vendor APIs
   - internal service APIs
   - structured data connectors

2. **Manual file upload only where needed**
   - The first expected MVP manual-upload surface is Treasury FTP / Funding Curve, because it is an internal funding-cost input rather than a public/vendor market-data feed.
   - Other file-based import paths should be treated as fallback or future extensions, not the default architecture.

3. **Screenshot-assisted data capture as fallback**
   - Used only when API is unavailable and no structured export/file exists.
   - Produces provisional extracted data, not final trusted data.

4. **Manual override / manual entry as final fallback**
   - Used for limited corrections or trader-supplied inputs.
   - Must be audited and never silently overwrite source data.

## 3. What this capability is

Screenshot-assisted Data Capture is a **visual extraction helper**.

It may eventually support the following workflow:

```text
Screenshot / pasted image / captured screen
→ visual extraction / OCR / table detection
→ field mapping and normalization
→ provisional structured staging record
→ user review and correction
→ confirmed data record with audit trail
→ eligible input to market snapshot / funding curve / reference-data staging
```

It should be treated as an ingestion helper, not as a source of truth.

## 4. What this capability is not

This capability must not be treated as:

- a pricing engine;
- a market-data snapshot by itself;
- a product schema field;
- a replacement for Bloomberg API / BQL or vendor APIs;
- an automatic trusted market-data source;
- a way to bypass validation, provenance, or human review;
- a generic permission to scrape every screen the user can see;
- a silent fallback that fills missing market data without warning.

A screenshot must never flow directly into pricing.

The pricing engine should only consume resolved, validated, provenance-tagged data.

## 5. Candidate use cases

Possible future use cases include:

- internal Treasury FTP / funding-cost table visible only on an internal portal;
- manually displayed deposit-rate tables;
- small quote tables visible on a vendor screen without API access;
- holiday/calendar tables displayed on a website or internal system;
- legacy system output that has no export function;
- occasional reference-data screens where manual retyping would be error-prone.

This capability should not become the default path for ordinary external market data if API access is available.

## 6. Required review flow

Every screenshot-extracted dataset must pass through a review stage before it can be committed.

Minimum required states:

```text
PENDING_EXTRACTION
PENDING_REVIEW
CONFIRMED
REJECTED
SUPERSEDED
```

The system should show the user:

- original screenshot;
- extracted fields;
- normalized fields;
- confidence score if available;
- warnings for uncertain values;
- source and capture metadata;
- editable correction fields;
- final confirmation action.

No extracted value should become production input until confirmed.

## 7. Required provenance

Each extracted record should preserve enough information for audit and replay.

Minimum provenance fields:

```text
source_type = SCREENSHOT
source_system
captured_at
captured_by
screenshot_reference
extraction_model_or_parser_version
raw_extracted_text
normalized_fields
confidence_by_field
reviewed_by
reviewed_at
review_status
correction_log
```

If the original screenshot cannot be stored for legal, compliance, licensing, or privacy reasons, the system must store a policy-compliant reference or hash plus enough metadata to reconstruct the review trail.

## 8. Confidence and validation rules

Screenshot extraction is inherently fallible. OCR / vision models may confuse:

- `0` and `O`;
- `1` and `I`;
- decimal points;
- percent signs;
- negative signs;
- tenor labels such as `1Y`, `1M`, `1W`;
- row and column alignment;
- dates;
- currencies;
- units such as bp, %, price per 100, or decimal yield.

Therefore:

- low-confidence fields must be flagged;
- missing units must block confirmation or require explicit user selection;
- ambiguous tenors must require review;
- values must be normalized before use;
- validation rules must be applied after extraction;
- the system must not silently coerce uncertain values into accepted market data.

## 9. Relationship to Treasury FTP / Funding Curve

Treasury FTP / Funding Curve is a business funding-cost input, usually shaped like:

```text
business_date
as_of_timestamp
currency
tenor
rate
rate_type
source_system
status
```

The preferred MVP path for Treasury FTP / Funding Curve may be manual file upload.

However, if Treasury FTP is only visible on an internal screen and no file/API is available, Screenshot-assisted Data Capture may eventually be used as a fallback to extract the table into a provisional funding-curve staging record.

Even in that case:

- the screenshot is not the funding curve itself;
- extracted values require review;
- confirmed values must be stored with provenance;
- pricing should consume only confirmed, normalized funding-curve records.

## 10. Relationship to Bloomberg / vendor data

Bloomberg API / BQL or vendor API should remain the preferred path when available.

Screenshot-assisted extraction from a Bloomberg or vendor screen should be treated as an exception, not the default. It may be useful when:

- API entitlement is unavailable;
- a screen contains derived values not exposed through the current connector;
- a quick manual capture is needed for analysis;
- the data is not being used as an official production source.

Any vendor-screen capture must respect licensing, compliance, and internal data-use policies.

## 11. Guardrails for future implementation

A future implementation issue must define:

- supported screenshot sources;
- supported table shapes;
- supported data categories;
- review UI;
- extraction model or OCR method;
- field normalization rules;
- validation rules;
- storage and retention policy for screenshots;
- provenance schema;
- audit trail;
- failure behavior;
- permission and compliance boundaries.

A future implementation must not:

- auto-commit extracted values without review;
- auto-price from a screenshot;
- overwrite API or official source data;
- hide OCR uncertainty;
- create broad market-data ingestion scope beyond the specific use case;
- mix Screenshot-assisted Data Capture with Treasury FTP terminology;
- mix screenshot extraction with product schema design.

## 12. Suggested future issue title

```text
docs: preflight screenshot-assisted data capture for non-API non-file sources
```

## 13. Suggested future issue scope

The first issue should be docs-only and should answer:

1. Which data types are eligible for screenshot-assisted capture?
2. What metadata and provenance must be stored?
3. What review states are required before data can be used?
4. What validation rules are mandatory?
5. Where does extracted data sit before confirmation?
6. Which parts are explicitly out of scope?
7. How does this interact with API-first market-data ingestion and Treasury FTP / Funding Curve?

No code should be written until those boundaries are reviewed.
