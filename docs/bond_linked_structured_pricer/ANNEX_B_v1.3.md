---
title: Annex B v1.3 - FTP File Specification
version: 1.3
source: GPT direct markdown output（非 PDF 反向解析）
authoritative: true
---

# Annex B：FTP File Specification

本附錄建立 FTP file specification 結構。  
實際欄位與檔名需由 Market Data Owner / IT 補齊。

---

## B.1 Bond Price / Yield File

| 項目 | 規格 |
|---|---|
| Filename Pattern | [TBD: Market Data Owner / IT, due date = Kickoff + 10 business days] |
| Cut-off Time | [TBD: Market Data Owner, due date = Kickoff + 10 business days] |
| Load Type | Full / Delta [TBD: IT, due date = Kickoff + 10 business days] |
| Partial Import | Valid records 可入庫，invalid records rejected |
| Null Handling | Required fields null = reject record |

Required fields：

- business_date
- as_of_timestamp
- isin
- currency
- clean_price_per_100
- yield
- accrued_interest_per_100
- source_system
- price_type
- status

---

## B.2 Yield Curve File

| 項目 | 規格 |
|---|---|
| Filename Pattern | [TBD: Market Data Owner / IT, due date = Kickoff + 10 business days] |
| Cut-off Time | [TBD: Market Data Owner, due date = Kickoff + 10 business days] |
| Load Type | Full |
| Partial Import | Curve-level validation；invalid curve rejected |
| Null Handling | tenor / rate null = reject curve |

Required fields：

- business_date
- as_of_timestamp
- curve_id
- curve_name
- currency
- curve_type
- tenor
- rate
- day_count
- compounding
- interpolation_method
- source_system
- status

---

## B.3 Volatility File

| 項目 | 規格 |
|---|---|
| Filename Pattern | [TBD: Market Data Owner / IT, due date = Kickoff + 10 business days] |
| Cut-off Time | [TBD: Market Data Owner, due date = Kickoff + 10 business days] |
| Load Type | Full / Delta [TBD: IT, due date = Kickoff + 10 business days] |
| Partial Import | Surface-level validation preferred |
| Null Handling | vol null = reject point |

Required fields：

- business_date
- as_of_timestamp
- vol_surface_id
- vol_basis
- currency
- isin / issuer / proxy_id
- expiry_tenor
- strike / moneyness
- volatility_value
- volatility_unit
- interpolation_method
- source_system
- status

---

## B.4 Credit Spread File

| 項目 | 規格 |
|---|---|
| Filename Pattern | [TBD: Market Data Owner / IT, due date = Kickoff + 10 business days] |
| Cut-off Time | [TBD: Market Data Owner, due date = Kickoff + 10 business days] |
| Load Type | Full / Delta [TBD: IT, due date = Kickoff + 10 business days] |
| Partial Import | Valid records 可入庫，invalid records rejected |
| Null Handling | spread null = reject record |

Required fields：

- business_date
- as_of_timestamp
- spread_curve_id
- isin
- issuer_id
- issuer_name
- currency
- rating
- sector
- tenor
- spread_value
- spread_unit
- spread_type
- source_system
- status

---

## B.5 Bond Master File

| 項目 | 規格 |
|---|---|
| Filename Pattern | [TBD: Market Data Owner / IT, due date = Kickoff + 10 business days] |
| Cut-off Time | [TBD: Market Data Owner, due date = Kickoff + 10 business days] |
| Load Type | Full / Delta [TBD: IT, due date = Kickoff + 10 business days] |
| Partial Import | Valid records 可入庫 |
| Null Handling | ISIN / maturity / coupon / yield_convention null = reject record |

Required fields：

- isin
- issuer
- currency
- coupon
- coupon_frequency
- maturity_date
- issue_date
- day_count
- business_day_convention
- redemption_amount
- callable_flag
- sinkable_flag
- bond_type
- yield_convention：
  - SEMI_ANNUAL_COMPOUND
  - ANNUAL_COMPOUND
  - SIMPLE_YIELD
  - JAPANESE_COMPOUND
  - OTHER
- ex_dividend_days
- first_coupon_date
- last_coupon_date
- status

Validation：

- callable_flag = true → reject for MVP pricing。
- sinkable_flag = true → reject for MVP pricing。
- non-plain-vanilla type → reject for MVP pricing。
- yield_convention 為空 → reject record。
- yield_convention = OTHER → reject for MVP pricing pool，除非 Trader 已於 Bond Master maintenance 補完 m 與 day_count 並留 audit。
- first_coupon_date / last_coupon_date 若存在不規則期間，需進入 cash flow generation，不可忽略。

---

## B.6 Calendar / Holiday File

| 項目 | 規格 |
|---|---|
| Filename Pattern | [TBD: Market Data Owner / IT, due date = Kickoff + 10 business days] |
| Cut-off Time | [TBD: Market Data Owner, due date = Kickoff + 10 business days] |
| Load Type | Full |
| Partial Import | Calendar-level validation |
| Null Handling | calendar_id / holiday_date null = reject record |

Required fields：

- calendar_id
- currency
- market
- holiday_date
- holiday_name
- business_day_flag
- source_system
- status

---

## B.7 FTP Batch Control

Each import batch must store:

- import_batch_id
- file_name
- file_type
- business_date
- received_timestamp
- start_timestamp
- end_timestamp
- status
- total_records
- success_records
- rejected_records
- error_message
- re-run flag
- triggered_by

Statuses：

- RECEIVED
- PROCESSING
- SUCCESS
- SUCCESS_WITH_REJECTS
- FAILED
- MISSING
- LATE
- REJECTED

---

## B.8 Downstream Interface Payload / File Placeholder

Downstream Interface 屬 Phase 3。  
本節僅建立 schema placeholder，避免 Phase 2 Deal data model 未預留欄位。

| 項目 | 規格 |
|---|---|
| Interface Type | API / File / FTP [TBD: IT Architecture Owner, due date = Phase 3 initiation] |
| Downstream System | [TBD: Trading Desk Lead / IT Architecture Owner, due date = Phase 3 initiation] |
| Filename Pattern | [TBD: IT / Downstream System Owner, due date = Phase 3 design sign-off] |
| Payload Format | JSON / CSV / XML / fixed-width [TBD: IT / Downstream System Owner, due date = Phase 3 design sign-off] |
| Acknowledgement Format | [TBD: Downstream System Owner, due date = Phase 3 design sign-off] |
| Retry Rule | [TBD: IT Production Support, due date = Phase 3 design sign-off] |
| Idempotency Rule | [TBD: IT Architecture Owner, due date = Phase 3 design sign-off] |

Reserved fields：

- downstream_system_name
- downstream_product_code
- downstream_book_code
- downstream_counterparty_code
- downstream_trade_reference
- outbound_payload_version
- interface_submission_timestamp
- downstream_response_timestamp
- downstream_response_message
- interface_error_message
- idempotency_key

---
