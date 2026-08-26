---
title: Bond Linked Structured Pricer IT Specification v1.4
version: 1.4
source: GPT direct markdown output（非 PDF 反向解析）
authoritative: true
---

# Bond Linked Structured Pricer  
# IT 需求規格書 v1.4  
## v1.3 + Annex A v1.4 Consistency Update

---

## v1.3 → v1.4 修訂摘要表

| 章節 | 修訂類型 | 修訂重點 |
|---|---|---|
| 全文 | 對齊 | 正式 pricing methodology reference 由 Annex A v1.3 升級為 Annex A v1.4。 |
| §3.2 | 改寫 | European price-based option 的預設 implied-vol 路徑改為 `VCUB_NORMAL_PROXY`：VCUB normal swaption vol → normal bond yield vol → duration → lognormal bond price vol。 |
| §3.3 | 改寫 | 移除 `PRICE_VOL_CONVERSION_MODE` / MODE_1 / MODE_2 作為 Bloomberg-parity 主路徑；改採 `BOND_VOL_SOURCE_MODE` 與 Annex A.8 的 Bloomberg OVME methodology。 |
| §7.4 | 改寫 | Vol data governance 改為 canonical VCUB normal-vol surface + resolver / direct price-vol override / lognormal-yield-vol override；移除 flat-vol silent fallback 概念。 |
| §8 / §9 | 對齊 | Pricing snapshot 與 Internal Pricing Report 增加 VCUB proxy coordinates、normal-vol normalization、`λ_vcub`、day-count total-variance adjustment、normal yield vol、duration、final price vol。 |
| §20 | 對齊 | UAT 增加 OVME vol-chain parity：`FY / KY / Kproxy / σ_vcub / σ_Y^N / Duration / σ_P / premium`。 |
| §22.3 | 對齊 | Canonical mode codes 移除 MODE_1 / MODE_2，改列 `VCUB_NORMAL_PROXY`、`DIRECT_PRICE_VOL`、`LOGNORMAL_YIELD_VOL_OVERRIDE` 等 v1.4 mode。 |
| Annex A | 取代 | 以 Annex A v1.4 為正式 methodology source；v1.3 的 lognormal-yield `Y × ModDur` / convexity MODE_2 不再代表 Bloomberg parity。 |

---

### 歷史：v1.2 → v1.3 修訂摘要

## v1.2 → v1.3 修訂摘要表

| 章節 | 修訂類型 | 修訂重點 |
|---|---|---|
| 全文 | 改寫 | 將 pricing methodology ownership 改為 Trading Desk 自有，不再假設有獨立 Quant team。 |
| §1A.4 | 改寫 | Phase Gate 中 Pricing Methodology Sign-off 改由 Trading Desk Lead / Product Owner / Risk Owner if required 負責。 |
| §3.2 | 改寫 | Pricing Model 四象限保留，但新增 Annex A v1.3 mode switch 為正式 methodology source。 |
| §3.3 | 改寫 | Equivalent price vol 改為 MODE_1 / MODE_2 系統切換，預設 MODE_1 first-order approximation。 |
| §3.4 | 保留 / 對齊 | Forward 推導維持不引入 repo / specialness，對齊 Annex A.5 自幹版預設。 |
| §6.3.2 | 改寫 | Yield-based option 改為 European 可選 MODE_A / MODE_B，American yield-based 強制 MODE_B。 |
| §14 | 改寫 | Greeks 方法論改為 Annex A.9 自幹版：MVP closed-form + bump consistency check。 |
| §20 | 改寫 | UAT 保留 Bloomberg / vendor official benchmark，同時納入 Annex A.13 self-validation framework。 |
| §24 | 改寫 | 移除已由 Annex A v1.3 寫死的 Quant TBD，保留真正需要 IT / Data / Ops 確認的 TBD。 |
| Annex A | 取代 | 以使用者提供之 Annex A v1.3 自幹版完整取代 v1.2 Annex A。 |
| Annex B | 保留 / 對齊 | 保留 v1.2 Annex B，並確認 Bond Master File 欄位支援 Annex A v1.4 yield / proxy-yield conventions。 |
| Annex C | 保留 | UI/UX 與品牌視覺維持 Phase 3 指引，MVP 僅 header logo。 |

---

# 1. 需求背景

交易室需要建置一套 Bond Linked Structured Pricer，用於結構型商品報價、bond option standalone pricing，以及後續 front-to-risk 流程擴充。

產品核心概念為：

- 客戶端：一筆存款或類存款結構。
- 交易室端：賣出或管理一筆與單一 cash bond 連結的 bond option。
- MVP 用途：支援前台快速 pricing、保存 Quote、匯出 Internal Pricing Report，以及產生繁中 / 英文 client-facing termsheet。
- Phase 2 用途：支援 Quote-to-Deal、Deal Ticket、Warehouse Position、EOD Revaluation、完整 Greeks / Scenario。
- Phase 3 用途：支援 Portfolio Risk、Risk Alert / Hard Limit、Downstream Interface、日文與完整品牌視覺規範。

本系統的初始版本不應被設計成完整 front-to-back trading platform。MVP 應聚焦於可驗收、可對價、可重現的 pricing tool 與 market data governance。

本 v1.4 版本採用 **Trading Desk 自有 methodology**。本專案不假設有獨立 Quant team；pricing methodology 以 Annex A v1.4 為正式依據。任何 methodology 變更需由 Trader 提出、Trading Desk Lead 簽核，並依 §21 Pricing Model Version Control 與 Annex A.13 Self-validation Framework 管理。

---

# 1A. Scope & Phasing

## 1A.1 MVP：v1.0 上線目標

MVP 需交付以下功能：

1. Standalone Bond Option Pricing Tool。
2. Structured Product Pricer：Deposit + Sell Bond Option。
3. FTP market data 匯入：
   - Bond price / yield
   - Yield curve
   - Volatility
   - Credit spread
4. Trader override：
   - bond price / yield
   - volatility
   - credit spread
   - reason 必填
   - 不覆蓋 FTP 原始資料
5. Internal Pricing Report 匯出：
   - Excel
   - PDF
6. Quote 保存與版本：
   - Quote ID
   - Quote Version
   - Pricing snapshot
   - Market data snapshot
   - Model version
7. English UI。
8. 繁體中文 / 英文 Client-facing Termsheet。
9. 基本 audit log：
   - pricing
   - override
   - quote save
   - export
   - FTP import
   - basic permission check
   - MVP 不要求完整 5 年查詢匯出 UI，但 audit 寫入需保留 future retention design。

MVP 不包含：

- 完整 Quote Lifecycle Status Machine。
- Quote-to-Deal。
- Warehouse Position。
- EOD Revaluation。
- 完整 Scenario / Portfolio Risk。
- Risk Alert / Hard Limit engine。
- Downstream system interface。
- 日文 UI / 日文 termsheet。
- 完整品牌視覺規範。

---

## 1A.2 Phase 2

Phase 2 需交付：

1. Quote Lifecycle Status Machine。
2. Quote-to-Deal。
3. Deal Ticket。
4. Warehouse Position。
5. EOD Revaluation。
6. On-demand Revaluation。
7. 完整 Greeks / Sensitivities / Scenario。
8. Audit Trail 5 年保存 + 查詢匯出。
9. Permission Matrix 細到 action-level。
10. Deal / Warehouse / Valuation history。

---

## 1A.3 Phase 3

Phase 3 需交付：

1. Portfolio-level Aggregation。
2. Risk Alert / Hard Limit / Notification。
3. Limit Exception 流程。
4. 日文 UI / 日文 termsheet。
5. Downstream Interface：
   - API
   - File
   - FTP
6. 品牌視覺完整規範。
7. Portfolio Risk Report。
8. Full notification channel integration：
   - Email
   - Teams / Slack / internal chat，由 IT 評估。

---

## 1A.4 Phase Gate 原則

每階段上線前需確認：

| Gate | Owner | 要確認內容 |
|---|---|---|
| Business Scope Sign-off | Trading Desk / Product Owner | 該 Phase 功能範圍與限制是否可接受 |
| Pricing Methodology Sign-off | Trading Desk Lead / Product Owner / Risk Owner if required | Model、curve、vol、forward、settlement convention、mode switch default 是否核准 |
| Market Data Readiness | Market Data Owner / IT | FTP file、欄位、cut-off、batch control 是否可用 |
| IT Architecture Review | IT Architecture Owner | 技術棧、部署、認證、DB、log、batch 是否符合行內標準 |
| UAT Benchmark Readiness | Trading Desk / Product Owner | Bloomberg / vendor benchmark 是否可取得 |
| NPA / Internal Governance | Product Owner / Compliance / Risk / IT | 是否需走 NPA、model governance、IT security 或其他內部審核流程 [TBD: Product Owner, due date = before MVP UAT start] |

---

# 2. 需求目標

系統需達成以下目標，並依 Phase 標註交付範圍。

| # | 需求目標 | Phase |
|---:|---|---|
| 1 | 支援交易員輸入 Bond Linked Structured Product 條件並計算報價。 | MVP |
| 2 | 支援單獨 bond option pricing tool，供對價與風險分析使用。 | MVP |
| 3 | 支援 price-based 與 yield-based bond option。 | MVP |
| 4 | 支援 Call / Put、European / American、Cash Settlement / Physical Delivery。 | MVP |
| 5 | 支援 FTP market data 匯入與 Trader override。 | MVP |
| 6 | 支援 Quote ID、Quote Version、Internal Pricing Report 與繁中 / 英文 termsheet。 | MVP |
| 7 | 支援 Quote Lifecycle、Quote-to-Deal 與 Deal Ticket。 | Phase 2 |
| 8 | 支援成交後 warehouse position、EOD MTM、on-demand revaluation。 | Phase 2 |
| 9 | 支援完整 Greeks、sensitivities、scenario analysis。 | Phase 2 |
| 10 | 支援 portfolio-level aggregation、risk alert、hard limit、notification。 | Phase 3 |
| 11 | 支援完整 audit trail 5 年保存、查詢與匯出。 | Phase 2 |
| 12 | 支援繁中、英文、日文三語 UI 與報表；其中日文為 Phase 3。 | MVP / Phase 3 |

---

# 3. 已定案的核心業務與金融規則

## 3.1 Product Universe

| 類別 | 已定案規則 |
|---|---|
| Underlying | 單一 cash bond |
| MVP Underlying 限制 | 僅支援 non-callable、non-sinkable、plain vanilla bullet cash bond |
| 明確排除於 MVP | callable bond、sinkable bond、amortizing bond、convertible bond、perpetual bond、structured bond、bond basket、bond future |
| Coupon | MVP 預設 fixed coupon bond；floating-rate bond / inflation-linked bond 屬 Phase 2 評估 |
| Option Payoff Basis | Price / Yield 皆支援 |
| Option Type | Call / Put 皆支援 |
| Exercise Style | European / American 皆支援 |
| Settlement Type | Cash Settlement / Physical Delivery 皆支援 |

---

## 3.2 Pricing Model

MVP pricing model 明確定義如下，不得由開發者自行替換或腦補。  
本 v1.4 版本之正式 pricing methodology 以 Annex A v1.4 為準。

| Option 類型 | MVP Pricing Model | Vol Convention |
|---|---|---|
| European price-based option | Black-76 on forward clean price | Model input 為 annualized lognormal bond price vol `σ_P`；預設 `BOND_VOL_SOURCE_MODE=VCUB_NORMAL_PROXY`，依 Annex A.8 由 VCUB normal swaption vol 推導 `σ_Y^N` 再以 duration 轉為 `σ_P` |
| European yield-based option | Annex A.3 `YIELD_OPTION_MODE`，預設 MODE_A：DV01-based closed-form；可切換 MODE_B：Numerical Conversion at Expiry | 使用 lognormal yield vol；與 VCUB-normal proxy 的 European price-based 主路徑分開治理 |
| American price-based option | CRR binomial tree on clean price state | 使用 price vol；若來源為 VCUB proxy，沿用 Annex A.8 產生的 `σ_P` |
| American yield-based option | CRR binomial tree on yield state；系統強制 MODE_B numerical conversion | 使用 lognormal yield vol |
| Future extension | Hull-White short-rate tree、finite difference、Least-squares Monte Carlo | Phase 2 / Phase 3 評估 |

Model choice 需在每次 pricing result 中保存：

- Pricing Model Name
- Pricing Model Version
- Pricing Engine Version
- Model Parameter Set Version
- Vol Basis
- Curve IDs
- Market Data Snapshot ID
- Annex A Mode Switches：
  - `YIELD_OPTION_MODE`
  - `BOND_VOL_SOURCE_MODE`
  - `CRR_STEPS`
  - `ENABLE_SHIFTED_BLACK`
  - `SHIFTED_BLACK_EPSILON`
  - `VCUB_RESOLVER_VERSION`
  - `VCUB_EXTRAPOLATION_MODE`
  - `CURVE_INTERP`
  - `AMERICAN_GREEKS_TREE_STEPS`

---

## 3.3 Volatility 預設類型

European price-based bond option 的預設 volatility sourcing 採 **Bloomberg OVME-style VCUB normal-vol proxy**，正式方法依 Annex A.8。系統不得把 VCUB normal vol / bp vol 直接當成 Black price vol。

MVP 規則：

1. European price-based option 的 model input 為 annualized lognormal bond price vol `σ_P`。
2. 預設 `BOND_VOL_SOURCE_MODE = VCUB_NORMAL_PROXY`：
   - 從已確認的 canonical VCUB ATM + OTM/SABR surface 取得 proxy normal swaption vol `σ_vcub`；
   - proxy coordinate 固定為 `Texp = TF`、`Ttenor = TB - TF`、`Kproxy = KATM + (KY - FY)`；
   - OTM `Display=Spread` 先以 `ATM absolute normal vol + skew spread` 重建 absolute `σ_vcub`；
   - 若來源以 bp normal vol 表示，先明確 normalize 為 absolute decimal yield units (`1 bp = 1e-4`)；
   - 套用 underlying-bond-specific `λ_vcub`；
   - 依 Annex A.8.5 對齊 `DCF_VCUB` / `DCF_BondVol` total variance，得到 normal bond yield vol `σ_Y^N`；
   - 最終以 `σ_P = |D_B| × σ_Y^N` 取得 Black price vol。
3. `DIRECT_PRICE_VOL`：若有經核准的 official / trader-overridden price vol，可直接作為 `σ_P`；source、unit、override reason 與 version 必須進 audit。
4. `LOGNORMAL_YIELD_VOL_OVERRIDE`：僅在 Trader 明確 override 時可用；依 Annex A.8.7 先以 Black/Bachelier price equivalence 轉回 normal yield vol，再走 duration conversion。它不是 VCUB 主路徑。
5. v1.3 的 `PRICE_VOL_CONVERSION_MODE = MODE_1 / MODE_2` 不再是 Bloomberg-parity 主路徑；不得以 `σ_Y(lognormal) × Y × ModDur` 或 convexity MODE_2 冒充 OVME methodology。
6. VCUB resolver 在完成 live parity 前，MVP 預設仍為 `VCUB_RESOLVER_VERSION = EXACT_NODE_ONLY`；超出已核准 resolver domain 時 fail closed。另提供已版本化的 in-grid resolver `IN_GRID_BILINEAR_V1`（方法論見 Annex A.8.3a）：僅在 confirmed canonical surface 的 expiry × tenor × strike 覆蓋範圍**之內**解析，且輸出**止於 `σ_vcub`**。
   - 其 volatility unit 必須由 surface 明確聲明、expiry/tenor 數值座標必須由呼叫端明確提供；兩者皆不得由 label、日期或數值大小推得，未提供即 fail closed。
   - smile model 由呼叫端明確聲明；本版本僅實作 PWL，SABR 在 Bloomberg calibration contract 與 calibrated parameters 未 pin 前 fail closed，不得以 PWL 冒充。
   - `VCUB_EXTRAPOLATION_MODE` 維持 `FAIL_CLOSED`，不得 flat extrapolate。
   - resolver 輸出在 `DCF_VCUB` / `DCF_BondVol` RED 解除前不得流入 `σ_Y^N` / `σ_P` / premium。
7. 當 `BOND_VOL_SOURCE_MODE = VCUB_NORMAL_PROXY` 時，`DCF_VCUB` 與 `DCF_BondVol` 的 **convention identifiers 與對應 year-fraction 計算規則都必須已 pin 且可審計**；任一 unresolved 時，在推導 `σ_Y^N` 前即 Market Data Blocking，禁止假設 ratio = 1、禁止猜測 convention、禁止省略 total-variance adjustment。此處的 convention 同時包含 day count 與 start / end date roles（start 為 `t0` 抑或 spot settlement date、end 為 `TE` 抑或 `TF`）；兩者必須一起 pin，只 pin day count 不足以解除 blocking（Annex A §A.8.5）。
8. 不得 silent fallback 到 flat vol、鄰近 vol 或任意插補。唯一可繞過 VCUB unresolved/blocking 的路徑是 Trader 明確選擇且可審計的 approved `DIRECT_PRICE_VOL` override；切換 source mode 不得修改或偽造 unresolved VCUB convention。

---

## 3.4 Forward Bond Price 與 Coupon Adjustment

若 option expiry 前有 coupon 支付，forward clean price 必須扣除該 coupon 的 PV，不能直接使用 spot clean price 當 forward。

MVP forward clean price 推導原則：

1. 以 pricing date 的 dirty price 作為起點。
2. 找出 pricing date 與 option expiry date 之間會支付的 coupon cash flow。
3. 使用 Bond Reference Curve 折現該 coupon cash flow。
4. 從 spot dirty price 扣除 PV(coupon before expiry)。
5. 推導 expiry date 的 forward dirty price。
6. 扣除 expiry date 的 accrued interest，得到 forward clean price。

概念公式：

```text
Spot Dirty Price = Spot Clean Price + Accrued Interest(pricing date)

Forward Dirty Price(expiry)
= [Spot Dirty Price - PV(coupons paid before expiry)] / DF_bond_reference(pricing date, expiry)

Forward Clean Price(expiry)
= Forward Dirty Price(expiry) - Accrued Interest(expiry date)
```

### 3.4.1 Forward 推導假設

業界實務上，cash bond forward price 可能受 repo、financing、specialness、bond borrow / lend、squeeze 或 issue-specific liquidity 影響。

本系統 MVP 明確採以下假設：

1. MVP 不引入 bond-specific repo curve。
2. MVP 不引入 specialness adjustment。
3. Forward bond price 採 Bond Reference Curve（含 credit spread）做 cost-of-carry approximation。
4. 若未來引入 bond repo / financing curve，forward 推導方式需 Trading Desk Lead review，並可能影響：
   - Annex A.2 European price-based option forward 計算
   - Annex A.3 European yield-based option forward yield / conversion
   - Annex A.4 American tree state evolution
5. 假設限制：本方法在 specialness 顯著的 bond，例如熱門 on-the-run treasury、squeeze 期間的特定 issue，可能與市場 forward 有顯著偏差。

---

## 3.5 Discount Curve 與 Bond Reference Curve 不可混用

系統需明確區分：

| Curve | 用途 |
|---|---|
| Option Discount Curve | 折現 option payoff / premium / PV |
| Bond Reference Curve | 推導 bond forward clean price、yield-to-price conversion、bond valuation |
| Credit Spread / Bond-specific Spread | 加入 Bond Reference Curve 或 bond valuation adjustment |
| Deposit Curve | Deposit leg discounting / funding calculation |

規則：

- Option payoff 折現必須使用 Option Discount Curve。
- Bond forward 推導必須使用 Bond Reference Curve，且需考慮 credit spread。
- Deposit leg 不得直接共用 Option Discount Curve，除非 mapping rule 明確設定。
- 系統需保存每個 pricing component 使用的 curve ID 與 curve version。

---

## 3.6 Settlement Lag 與 Accrued Interest

Physical delivery 的 accrued interest 計算至 **settlement date**，不是 expiry date。

規則：

1. Payoff comparison 使用 exercise date / expiry date 的 clean price。
2. Cash settlement amount 使用 clean price difference。
3. Physical delivery invoice 使用 settlement date 的 dirty price。
4. Dirty price 用於 invoice 與實物交割，不用於 payoff comparison。
5. 若 settlement lag 跨 coupon date，accrued interest 必須依 settlement date 重新計算。

---

## 3.7 Notional 與 Participation Ratio

Deposit Notional 與 Bond Option Notional 必須分開。

| 欄位 | 定義 |
|---|---|
| Deposit Notional | 客戶投入本金，用於 deposit leg、customer return / yield |
| Bond Option Notional | Option 對應的 underlying bond face amount，用於 premium、payoff、Greeks、warehouse exposure |
| Participation Ratio | `Bond Option Notional / Deposit Notional` |

Option payoff 必須使用 Bond Option Notional，不得誤用 Deposit Notional。

---

## 3.8 Override Discipline

Override 規則必須保留：

| Market Data | Trader 是否可 Override | Reason | 是否覆蓋 FTP |
|---|---:|---:|---:|
| Bond clean price / yield | 可 | 必填 | 否 |
| Volatility | 可 | 必填 | 否 |
| Credit spread | 可 | 必填 | 否 |
| Yield curve point | 不可 | 不適用 | 否 |

Override 僅影響該次 pricing / quote / intraday revaluation。  
Override 不可覆蓋 FTP 原始 market data。  
Override 必須保存 original value、override value、reason、user、timestamp、market data snapshot ID。

---

# 4. 使用者角色

系統至少需支援以下角色。

| 角色 | MVP | Phase 2 / Phase 3 |
|---|---|---|
| Trader | 建立 pricing、recalculate、override、save quote、export report、選擇 Annex A mode switch | Convert to Deal、on-demand revaluation、scenario、warehouse view |
| Trading Desk Lead | 簽核 methodology 變更、維護 desk-level model configuration | 簽核 model version change、review self-validation result |
| Trading Desk Designated User | 可協助確認 pricing / report / template | 維護 risk parameter set、scenario template |
| Product Owner | 需求範圍、NPA / governance coordination | Release scope sign-off |
| Admin | basic config、user mapping | action-level permission matrix、alert rules、curve mapping |
| Market Data Owner | FTP import monitoring | data quality workflow、market data dashboard |
| Middle Office / Operations | MVP 不強制 | Deal Ticket、manual booking reference、warehouse review |
| IT Support | batch / log / issue support | interface / alert / audit support |
| Viewer / Read-only User | 依權限查看 pricing / quote | 查看 deal、position、risk report |

若行內另有 Quant / Model Validation / Risk Model Owner 角色，可在 phase gate 或 model version governance 中加入，但本 v1.4 規格不假設有獨立 Quant team。

---

# 5. High-level 流程

## 5.1 MVP Pricing to Quote

1. Trader 透過 English UI 登入系統。
2. 選擇 Product Type、Book、Desk、Currency。
3. 輸入 Deposit Leg 與 Bond Option 條件。
4. 系統依 FTP market data 與 mapping rule 自動帶入：
   - bond clean price / yield
   - yield curve
   - vol
   - credit spread
5. Trader 可 override bond price / yield、vol、credit spread，reason 必填。
6. Trader 可依 Annex A.12 選擇可用 mode switch。
7. Python pricing engine 計算：
   - deposit leg PV
   - bond option fair value
   - premium
   - structured product fair value
   - client return / yield
   - margin, if applicable
   - MVP European closed-form Greeks
   - Annex A.13 self-validation checks
8. Trader 保存 Quote，系統產生 Quote ID / Quote Version。
9. Trader 匯出 Internal Pricing Report。
10. Trader 可匯出繁中 / 英文 Client-facing Termsheet。

---

## 5.2 Phase 2 Quote to Deal

1. Trader 選擇有效 Quote Version。
2. 系統執行 pre-trade check：
   - permission
   - quote validity
   - required fields
   - market data snapshot status
   - override reason
   - blocking validation
   - self-validation critical error
3. 若通過，Trader 可 Convert to Deal。
4. 系統產生 Deal ID / Trade ID。
5. 系統鎖定 selected Quote Version。
6. 系統建立 Deal Snapshot。
7. 系統匯出 Deal Ticket。
8. 系統建立 Warehouse Position。

---

## 5.3 Phase 2 Deal to Warehousing

1. Converted Deal 自動建立 warehouse position。
2. Position 納入 EOD revaluation。
3. 每日 FTP market data 完成後自動重估。
4. Trader 可 on-demand revalue 作為 intraday reference。
5. 系統保存 valuation history、Greeks、scenario、audit。

---

## 5.4 技術棧與資料模型建議

本節為建議架構，實際技術棧需由 IT architecture review 確認，不可視為替行內 IT 標準做最終決定。

### 5.4.1 推薦技術棧

| Layer | 建議 | 備註 |
|---|---|---|
| Backend | Python 3.11+ / FastAPI | 適合 pricing API、batch、internal tool；可由 IT 替換 |
| Frontend | Jinja2 + HTMX | Internal tool 取向，低維護成本 |
| Frontend Alternative | React | 若未來需重前端、複雜互動、portfolio risk dashboard，可改 React |
| DB | PostgreSQL | 建議使用 JSONB 保存 immutable snapshot；實際 DB 由 IT 確認 |
| Pricing Core | QuantLib-Python + custom wrapper | QuantLib 處理 bond cash flow、curve、day count、calendar；option payoff、tree、scenario wrapper 自行實作 |
| Batch | Python scheduler / Airflow / IT standard batch tool | 由 IT 評估 [TBD: IT Architecture Owner, due date = technical kickoff] |
| Authentication | SAML / OIDC / Windows Auth 擇一 | 依行內 IT 標準；kickoff 前確認 [TBD: IT Security Owner, due date = technical kickoff] |
| Deployment | Container / VM / internal app platform | 依行內標準 [TBD: IT Infrastructure Owner, due date = solution design sign-off] |
| Logging | Structured application log + audit table | Audit append-only，application log 可依 IT 標準保存 |

---

### 5.4.2 核心資料模型

#### MarketDataSnapshot

用途：保存一次 pricing / quote / valuation 使用的 market data 版本。

關聯：

```text
MarketDataSnapshot
  ├── BondPriceFeed
  ├── CurvePoint
  ├── VolPoint
  ├── CreditSpreadPoint
  └── FTPImportBatch
```

核心欄位：

- market_data_snapshot_id
- business_date
- as_of_timestamp
- source_batch_ids
- bond_price_feed_id
- curve_set_id
- vol_surface_id
- spread_curve_id
- created_at

---

#### Quote → QuoteVersion → PricingResult → Greeks / ScenarioResult

MVP 需支援 Quote / QuoteVersion / PricingResult。  
MVP 需輸出 European closed-form Greeks 與 self-validation result。完整 ScenarioResult UI 屬 Phase 2。

關聯：

```text
Quote
  └── QuoteVersion
        └── PricingResult
              ├── Greeks
              ├── SelfValidationResult
              └── ScenarioResult
```

核心規則：

- Quote 是交易員可識別的報價主檔。
- QuoteVersion 保存每次 reprice 後的 immutable snapshot。
- PricingResult 保存該版本計算結果。
- Greeks / ScenarioResult / SelfValidationResult 不可覆蓋原結果；重算需產生新 record。
- Annex A mode switches 必須保存於 PricingResult snapshot。

---

#### OverrideRecord

MVP 需建立 OverrideRecord 作為獨立實體，不可僅將 override 塞入 JSONB snapshot。

關聯：

```text
PricingRequest / QuoteVersion ──< OverrideRecord
MarketDataSnapshot ────────────< OverrideRecord
```

核心欄位：

- override_id
- pricing_request_id, nullable
- quote_version_id, nullable
- market_data_snapshot_id
- market_data_field：
  - BOND_PRICE
  - BOND_YIELD
  - VOLATILITY
  - CREDIT_SPREAD
  - SETTLEMENT_LAG
  - SHIFTED_BLACK_EPSILON, if enabled
- original_value
- override_value
- override_reason
- user_id
- timestamp
- effective_pricing_run_id

規則：

1. OverrideRecord 為 append-only，不可修改或刪除。
2. 同一 pricing run 對同一 field 只能有一筆有效 override。
3. 若 Trader 變更 override 值，須產生新 OverrideRecord，不得 update 原 record。
4. OverrideRecord 需保留 original FTP / mapped value。
5. 查詢「特定 ISIN 在特定區間的 override 歷史」需可由 index 查詢，不得僅依靠 JSONB scan。
6. 建議 index：
   - isin
   - market_data_field
   - timestamp
   - user_id
   - quote_version_id
   - market_data_snapshot_id

---

#### ModeSwitchSnapshot

MVP 需保存每筆 pricing run 使用的 methodology mode。

關聯：

```text
PricingResult ──< ModeSwitchSnapshot
```

核心欄位：

- pricing_result_id
- yield_option_mode
- bond_vol_source_mode
- crr_steps
- enable_shifted_black
- shifted_black_epsilon
- vcub_resolver_version
- vcub_extrapolation_mode
- curve_interp
- american_greeks_tree_steps
- changed_by
- changed_timestamp
- change_reason, if required

---

#### QuoteVersion → Deal → WarehousePosition → ValuationSnapshot

Phase 2 開始使用。

關聯：

```text
QuoteVersion
  └── Deal
        └── WarehousePosition
              ├── ValuationSnapshot(EOD)
              └── ValuationSnapshot(Intraday)
```

核心規則：

- Deal 必須連回 source QuoteVersion。
- WarehousePosition 僅可由 Converted Deal 建立。
- EOD ValuationSnapshot 為 official daily valuation。
- Intraday ValuationSnapshot 為 reference，不覆蓋 EOD。

---

#### AuditEvent

AuditEvent 為 append-only polymorphic event table。

可連到：

- PricingRequest
- Quote
- QuoteVersion
- PricingResult
- Deal
- WarehousePosition
- ValuationSnapshot
- MarketDataSnapshot
- FTPImportBatch
- ExportRecord
- PermissionRule
- RiskParameterSet
- OverrideRecord
- ModeSwitchSnapshot
- SelfValidationResult

核心規則：

- 不允許 update / delete。
- 更正只能新增 correction / comment event。
- MVP 需先寫入 basic audit log。
- Phase 2 實作完整查詢、匯出與 5 年 retention operation。

---

#### PermissionMatrix

Phase 2 完整實作 action-level permission matrix。  
MVP 可先實作 basic role-based permission，但資料模型需預留完整維度。

關聯：

```text
PermissionMatrix
  = User / Role / Group
    × Desk / Book / Product
    × Action
    × Effective Date / Status
```

核心欄位：

- permission_rule_id
- user_id
- role
- user_group
- desk
- book
- product_type
- action_type
- effective_from
- effective_to
- status

---

### 5.4.3 NFR：非功能性需求

| 項目 | 要求 |
|---|---|
| 同時上線 Trader 數 | 預估 ≤ 20 |
| 併發 pricing | 需支援 ≤ 50 concurrent pricing requests |
| 單筆 European pricing latency | ≤ 1 秒 |
| 單筆 American tree pricing latency | ≤ 3 秒，CRR HIGH(500) |
| Snapshot replay latency | 給定 Quote Version 重現 pricing ≤ 5 秒 |
| MVP report export | 單筆 Internal Pricing Report ≤ 10 秒 |
| EOD revaluation | 每晚 ≤ 30 分鐘完成所有 active positions，Phase 2 |
| Audit log 寫入 | 不可阻塞 pricing 主流程；建議 async write |
| Audit log 寫入失敗 | 不得使 pricing result 消失；需產生告警 / retry queue [TBD: IT Architecture Owner, due date = solution design sign-off] |
| DB query timeout | 使用者查詢 ≤ 30 秒，批次查詢 ≤ 5 分鐘 |
| 歷史查詢深度 | MVP 至少支援 90 天熱資料、5 年冷資料的查詢路徑設計 |
| Snapshot 保存 | Pricing / Quote / Market Data / Model version / Mode switches 必須可重現 |
| Availability | 依內部交易室工具標準 [TBD: IT Production Support, due date = solution design sign-off] |
| Backup / Restore | 依行內 DB 標準 [TBD: DBA, due date = solution design sign-off] |

---

# 6. 功能需求

## 6.1 Structured Product Pricer

### 6.1.1 MVP 輸入欄位

#### 基本資訊

- Product Type
- Book
- Desk
- Trader
- Currency
- Pricing Date
- Market Data As-of Date
- Quote Validity Period
- Language for report export

#### Deposit Leg

- Deposit Notional
- Deposit Currency
- Start Date
- Maturity Date
- Tenor
- Deposit Rate / Yield
- Day Count
- Business Day Convention
- Principal Repayment Rule
- Deposit Curve ID

#### Bond Option Leg

- Underlying Bond ID / ISIN
- Issuer
- Bond Currency
- Coupon
- Coupon Frequency
- Maturity
- Bond Clean Price per 100
- Bond Yield
- Accrued Interest
- Payoff Basis：Price / Yield
- Option Type：Call / Put
- Position：Buy / Sell
- Strike Price per 100 or Strike Yield
- Exercise Style：European / American
- Expiry Date
- Exercise Start Date, if American
- Settlement Type：Cash / Physical
- Settlement Date Rule
- Settlement Lag (T+n)
- Settlement Calendar
- Cash Settlement Currency：
  - 預設 = bond currency
  - Quanto / non-bond-currency settlement 屬 Phase 2 / Phase 3
- Ex-coupon flag, if applicable
- Bond Option Notional / Bond Face Amount
- Deposit Notional
- Participation Ratio

Settlement Lag 預設依 Annex A.7.3 market convention。Trader 可調整 Settlement Lag；調整需 reason，並進 audit。

#### Pricing Methodology Mode Switches

依 Annex A.12：

- `YIELD_OPTION_MODE`
- `BOND_VOL_SOURCE_MODE`
- `CRR_STEPS`
- `ENABLE_SHIFTED_BLACK`
- `SHIFTED_BLACK_EPSILON`
- `VCUB_RESOLVER_VERSION`
- `VCUB_EXTRAPOLATION_MODE`
- `CURVE_INTERP`
- `AMERICAN_GREEKS_TREE_STEPS`

#### Market Data

- Yield Curve ID
- Option Discount Curve ID
- Bond Reference Curve ID
- Vol Surface / Vol Input ID
- Used Volatility
- Credit Spread Source
- Used Credit Spread
- Market Data Source
- Override Indicator
- Override Reason
- Fallback Indicator

---

## 6.2 Bond Option Standalone Pricing Tool

MVP 需支援 standalone bond option pricing，用於：

- Trader 對價。
- Bloomberg / vendor benchmark comparison。
- Warehousing 之前的單筆 option valuation。
- Internal Pricing Report。

MVP 支援：

- New standalone option pricing。
- Reprice。
- Save Quote。
- Export Internal Pricing Report。
- Market data override。
- Benchmark input snapshot。
- Annex A.13 self-validation result。

Phase 2 支援：

- 從 structured quote 帶入 option leg。
- 連結 Deal / Warehouse Position。
- Full Greeks。
- Scenario analysis。
- Revaluation history。

---

## 6.3 Payoff 與計算規則

### 6.3.1 Price-based Bond Option

Price Call：

```text
Payoff = max(Final Clean Price - Strike Clean Price, 0)
         × Bond Option Notional / 100
```

Price Put：

```text
Payoff = max(Strike Clean Price - Final Clean Price, 0)
         × Bond Option Notional / 100
```

Price-based option 的 strike 與 final price 皆使用 clean price per 100。

---

### 6.3.2 Yield-based Bond Option

#### 方向慣例註解

Yield option 的 Call / Put 方向需依 bond / IR option 業界標準定義：

- **Yield Call ≡ 對 yield 上升有 payoff ≡ 在 clean price 空間等同於 Price Put on clean price。**
- **Yield Put ≡ 對 yield 下降有 payoff ≡ 在 clean price 空間等同於 Price Call on clean price。**
- 此為 bond / IR option 的業界標準方向慣例，與 equity / FX option 直覺相反。
- UI label、Internal Pricing Report、Client-facing Termsheet、Audit Log 均須沿用此方向慣例，不得自行翻轉。
- Trader 在 UI 上選擇 Yield Call / Yield Put 時，系統需顯示對應的 clean price 方向 hint，避免操作誤解。

UI hint 範例：

| Trader 選項 | UI Hint |
|---|---|
| Yield Call | Yield up / Clean price down payoff |
| Yield Put | Yield down / Clean price up payoff |

#### 正式 methodology

European yield-based option 使用 Annex A.3 `YIELD_OPTION_MODE`：

| Mode | 用途 |
|---|---|
| MODE_A | DV01-based Closed-form Conversion，MVP 預設 |
| MODE_B | Numerical Conversion at Expiry |

American yield-based option：

- 系統自動 force MODE_B。
- Trader 不可選 MODE_A。

#### Payoff direction

Yield Call：

```text
Yield Call means final yield > strike yield.

Clean-price-space payoff direction:
max(Strike Yield Converted Clean Price
    - Final Yield Converted Clean Price, 0)
× Bond Option Notional / 100
```

Yield Put：

```text
Yield Put means final yield < strike yield.

Clean-price-space payoff direction:
max(Final Yield Converted Clean Price
    - Strike Yield Converted Clean Price, 0)
× Bond Option Notional / 100
```

重要規則：

- Yield-to-price conversion 使用 expiry date 的 remaining cash flow。
- Accrued interest 在 payoff conversion 時以 expiry date 為準。
- Physical delivery invoice 的 accrued interest 另以 settlement date 計算。
- MODE_A 為 sell-side 常用 DV01-based closed-form approximation。
- MODE_B 為 numerical conversion，直接反映 yield-to-price 非線性。
- Mode selection 必須寫入 PricingResult、QuoteVersion snapshot、Internal Pricing Report 與 audit log。

---

### 6.3.3 Clean / Dirty Price 與時序

系統需明確區分 payoff timing 與 settlement timing。

#### 時序流程

```text
Pricing Date
  ├─ 取得 Spot Clean Price / Yield / Curve / Vol / Spread
  ├─ 推導 Forward Clean Price 或 Forward Yield
  └─ 計算 Option Fair Value

Expiry / Exercise Date
  ├─ 用 clean price 或 yield-to-clean-price 判斷 payoff
  ├─ Cash Settlement：以 clean price difference 計算 cash payoff
  └─ Physical Delivery：決定是否交割 underlying bond

Settlement Date
  ├─ 重新計算 settlement date accrued interest
  ├─ Dirty Price = Clean Settlement Price + Accrued Interest(settlement date)
  └─ 產生 physical delivery invoice amount
```

#### 規則

| 用途 | Price Basis |
|---|---|
| Strike input | Clean price per 100 |
| Price-based payoff comparison | Clean price per 100 |
| Yield-based payoff conversion | Clean price per 100 |
| Cash settlement payoff | Clean price difference |
| Physical delivery invoice | Dirty price |
| Accrued interest for payoff conversion | Expiry date |
| Accrued interest for physical invoice | Settlement date |

Dirty price 用於 invoice 與實物交割，不用於 payoff comparison。

---

# 7. Market Data / FTP 需求

## 7.1 FTP 匯入資料

MVP FTP 至少需匯入：

1. Underlying bond clean price / yield。
2. Yield curve。
3. Volatility。
4. Credit spread / issuer spread。
5. Bond master。
6. Calendar / holiday。
7. Curve mapping。
8. Spread mapping。

每一批 market data 需產生：

- FTP Import Batch ID
- Market Data Snapshot ID
- Source file name
- Business date
- As-of timestamp
- Import status
- Validation status
- Error records

---

## 7.2 Market Data Override 規則

| 資料 | FTP 預設 | Trader Override | Reason | 是否覆蓋 FTP |
|---|---:|---:|---:|---:|
| Bond clean price / yield | 是 | 可 | 必填 | 否 |
| Volatility | 是 | 可 | 必填 | 否 |
| Credit spread | 是 | 可 | 必填 | 否 |
| Yield curve | 是 | 不開放 Trader 改點 | 不適用 | 否 |
| Settlement Lag | market convention | 可 | 必填 | 否 |
| Shifted Black epsilon | Annex A default | 可，僅 when enabled | 必填 | 否 |

Override 僅影響該次 pricing / quote / intraday revaluation。  
若 underlying market data snapshot 被新批次取代，原 Quote status 不變，但 Convert to Deal 前必須由 Trader 重新確認 override 是否仍適用。

邊界條件：

1. Override reason 缺漏：
   - 不允許 Recalculate。
   - 不允許 Save Quote。
   - 顯示 blocking validation message。
2. Override 後 FTP 新批次進來：
   - Quote status 不自動變更。
   - QuoteVersion 保留原 market data snapshot。
   - 若進入 Convert to Deal，需提示 Trader：
     - 沿用原 snapshot。
     - Reprice 後再轉。
   - 選擇與確認須留 audit。
3. Override 不可寫回正式 FTP market data table。
4. Reset to FTP 僅還原 current pricing screen，不刪除原 override audit。
5. 若 quote 已過期，不允許以確認 override 的方式繞過 reprice rule。
6. Override 僅允許在白名單欄位：
   - bond clean price
   - bond yield
   - volatility
   - credit spread
   - settlement lag
   - shifted black epsilon, if enabled
7. 同一 pricing run 對同一 market data field 只能有一筆有效 override。
8. 若 Trader 變更 override 值，系統需新增 OverrideRecord，不得 update 原 OverrideRecord。
9. Override value 需通過 basic validation：
   - numeric format
   - non-null
   - not NaN / infinity
   - 是否超出 configurable sanity threshold [TBD: Trading Desk Lead / Market Data Owner, due date = MVP SIT start]
10. Client-facing Termsheet 不顯示 override reason；Internal Pricing Report 必須顯示 override indicator、original value、override value、reason。
11. Override 不得被系統自動帶入下一筆 quote 作為預設 market data。
12. 若 Quote 後續被轉成 Deal，該 QuoteVersion 的 OverrideRecord 必須被鎖定並隨 Deal snapshot 保存。

---

## 7.3 Yield Curve Mapping

Curve selection 需依下列維度決定：

- Currency
- Book
- Desk
- Product Type
- Pricing Purpose
- Effective Date
- Status

Curve usage 至少分為：

| Curve | 用途 |
|---|---|
| Deposit Curve | Deposit leg discounting |
| Option Discount Curve | Option payoff / premium discounting |
| Bond Reference Curve | Bond forward clean price / yield-to-price conversion |
| Funding Curve | Funding adjustment, if applicable |

若找不到 curve mapping 或 curve data invalid，pricing blocked。

---

## 7.4 Volatility Data

MVP 支援的 bond-option vol source hierarchy / governance：

1. **VCUB Normal Proxy（預設）**：已經 Trader Confirm 的 canonical VCUB ATM + OTM/SABR snapshot。
2. **Direct Price Vol**：經核准的 official price-vol source，或 Trader 明確 override。
3. **Lognormal Yield Vol Override**：僅 Trader explicit override；依 Annex A.8.7 轉回 normal yield vol 後再產生 price vol。
4. 不提供 silent flat-vol / nearest-vol fallback。若 VCUB resolver 無法安全解析，pricing blocked，除非改走經核准且有 audit 的 direct-vol source mode。

使用 `VCUB_NORMAL_PROXY` 時系統需保存至少：

- `BOND_VOL_SOURCE_MODE`
- VCUB canonical surface / snapshot identity
- VCUB source type / display contract（ATM absolute normal vol、OTM spread-to-ATM）
- `Texp`, `Ttenor`, `KATM`, `FY`, `KY`, `Kproxy`, `KY-FY`
- `σ_vcub` 原始數值、原始 unit、normalized decimal value
- ATM absolute vol + skew spread（若非 ATM）
- underlying-bond-specific `λ_vcub` + parameter-set version
- `DCF_VCUB`, `DCF_BondVol` + convention
- derived normal bond yield vol `σ_Y^N`
- bond duration `D_B`
- final lognormal bond price vol `σ_P`
- `VCUB_RESOLVER_VERSION`
- `VCUB_EXTRAPOLATION_MODE`
- Interpolation / extrapolation method and version, if future approved resolver is used
- Override indicator / reason, if applicable

Vol basis / semantic codes至少需能區分：

- `NORMAL_SWAPTION_VOL`
- `NORMAL_BOND_YIELD_VOL`
- `PRICE_VOL`
- `LOGNORMAL_YIELD_VOL_OVERRIDE`

不得將 normal/bp vol 與 price vol 儲存在同一語義欄位後靠 numeric magnitude 猜測。

---

## 7.5 Credit Spread

Spread mapping 優先順序：

1. Bond-specific spread by ISIN。
2. Issuer spread。
3. Rating / sector proxy spread。
4. Manual override spread。
5. 若無有效 fallback，依設定 warning 或 blocking。

Credit spread 不可 silent default to zero，除非明確設定並顯示 assumption。

---

## 7.6 FTP Error Handling / Market Data Blocking

本節所述 blocking 為 **Market Data Blocking**，屬 MVP 範圍，原因為市場資料缺漏 / 無效。  
Market Data Blocking 與 §17 Risk Hard Limit 不同；Risk Hard Limit 屬 Phase 3，並以 risk threshold / limit breach 為觸發原因。

| 情境 | MVP 處理方式 |
|---|---|
| Bond price / yield 缺漏 | Warning，Trader 可 override |
| Vol 缺漏 / VCUB resolver unresolved | Market Data Blocking；Trader 僅可透過經核准且有 audit 的 direct-vol override 繞過 |
| `VCUB_NORMAL_PROXY` 的 `DCF_VCUB` / `DCF_BondVol` convention 任一 unresolved | **Market Data Blocking，block pricing before `σ_Y^N` derivation**；不得假設 ratio = 1、不得猜 convention；僅可顯式切換到經核准且有 audit 的 `DIRECT_PRICE_VOL` source mode |
| Credit spread 缺漏 | Warning，Trader 可 override |
| Yield curve 缺漏 | Market Data Blocking，block pricing |
| Curve mapping 缺漏 | Market Data Blocking，block pricing |
| FTP file not arrived | 依資料類型 warning 或 Market Data Blocking |
| FTP format error | Import failed，記錄 error，通知 IT / Data Owner |
| Partial import | valid records 可入庫，invalid records rejected |
| Batch failure | 保存 batch status、error log、retry history |
| Bond Master yield_convention missing | Reject record |
| Bond Master unsupported product type | Reject record for MVP pricing pool |

Market Data Blocking 可阻擋：

- Pricing calculation
- Recalculate
- Save Quote
- Report export, if pricing result cannot be produced

---

# 8. Quote Management

## 8.1 MVP Quote 保存與版本

MVP 需支援：

- Quote ID
- Quote Version
- Save Quote
- Save as New Quote
- Pricing Request Snapshot
- Pricing Result Snapshot
- Market Data Snapshot
- Model Version
- Mode Switch Snapshot
- Self-validation Result
- Export History
- Basic Audit Log

MVP 不要求完整 status machine，但至少需保存：

- Draft
- Priced
- Saved
- Exported
- Expired

---

## 8.2 Phase 2 Quote Lifecycle Status Machine

Phase 2 補上完整 status：

- Draft
- Priced
- Saved
- Exported
- Sent to Client
- Accepted by Client
- Expired
- Reprice Required
- Converted to Deal
- Cancelled
- Rejected

---

## 8.3 Quote Validity

Quote validity 預設 15 分鐘，可由 Admin / Config 依 Product Type / Book / Desk 調整。

規則：

1. Quote 在有效期內可作為報價依據。
2. Quote 過期後：
   - 可查詢。
   - 可匯出歷史報表。
   - 不可 Convert to Deal。
   - 必須 Reprice 產生新 Quote Version。
3. 若 market data snapshot 在有效期內被新批次取代，Quote 不自動過期，但 Convert to Deal 前需提示 Trader 確認或 reprice。

---

# 9. Reports / Export

## 9.1 MVP Internal Pricing Report

格式：

- Excel
- PDF

內容包含：

- Quote ID / Version
- Trader / Book / Desk
- Product terms
- Deposit leg
- Bond option leg
- Market data snapshot
- Override / fallback
- Pricing model version
- Annex A Mode Switches
- Fair value / premium
- Client return / yield
- Bank margin, if applicable
- Benchmark reference fields
- Warning / error
- Export user / timestamp
- Self-validation results

若使用 `VCUB_NORMAL_PROXY`，Internal Pricing Report 必須顯示：

- vol source mode = `VCUB_NORMAL_PROXY`
- VCUB surface / snapshot identity
- `Texp`, `Ttenor`, `KATM`, `FY`, `KY`, `Kproxy`, `KY-FY`
- resolved `σ_vcub`（原始 unit + normalized decimal）
- ATM absolute normal vol + skew spread（若非 ATM）
- `λ_vcub` + parameter-set version
- `DCF_VCUB`, `DCF_BondVol` + conventions
- derived normal bond yield vol `σ_Y^N`
- bond duration `D_B`
- final lognormal bond price vol `σ_P`
- `VCUB_RESOLVER_VERSION` / extrapolation mode
- interpolation / extrapolation method/version, if applicable

若使用 yield-based option，Internal Pricing Report 必須顯示：

- Yield Option Mode = MODE_A / MODE_B
- Yield Call / Yield Put clean price direction hint
- DV01_expiry definition, if MODE_A
- quadrature / tree settings, if MODE_B

Phase 2 補充：

- Full Greeks
- Scenario
- Benchmark reconciliation table
- Warehouse linkage
- Deal linkage

---

## 9.2 MVP Client-facing Termsheet

格式：

- PDF

MVP 語言：

- 繁體中文
- English

內容包含：

- Product Name
- Currency
- Tenor
- Deposit Notional
- Underlying Bond
- Payoff description
- Strike
- Expiry / observation date
- Settlement type
- Indicative return / coupon / yield
- Basic scenario illustration
- Quote validity time
- Contact / desk information

Client-facing report 不顯示：

- internal model detail
- FTP batch ID
- override reason
- internal margin
- internal benchmark reconciliation
- audit detail
- full Annex A mode switch list, unless configured

Yield Call / Yield Put 的方向描述需符合 §6.3.2 與 Annex A.3 慣例，不得翻轉。

---

# 10. Quote-to-Deal

Quote-to-Deal 屬 Phase 2，不納入 MVP critical path。

## 10.1 轉 Deal 條件

Convert to Deal 前需檢查：

- User permission
- Quote status eligible
- Quote validity not expired
- Required fields complete
- Market data snapshot exists
- Override reason completed
- No blocking validation
- No self-validation critical error
- Quote has not already been converted, unless multiple conversion explicitly allowed

---

## 10.2 Market Data Snapshot 更新後的處理

若 Quote 在有效期內，但 underlying market data snapshot 已被新批次取代，系統需於 Convert to Deal 前提示 Trader：

1. 沿用原 snapshot。
2. Reprice 後再轉。

規則：

- Trader 選擇沿用原 snapshot 時，系統需顯示：
  - original snapshot ID
  - latest snapshot ID
  - changed market data categories
  - whether manual override was used
  - Annex A mode switch used
  - self-validation result
- Trader 必須確認 override 是否仍適用。
- 操作須留 audit。
- 若 Quote 已過期，不能沿用原 snapshot，必須 Reprice。
- 若新批次顯示原先資料存在重大 invalidation，是否強制 Reprice 需由 config 控制 [TBD: Trading Desk Lead / Risk Owner, due date = Phase 2 design sign-off]。

---

## 10.3 Convert to Deal 後系統需執行

- Generate Deal ID / Trade ID。
- Lock selected Quote Version。
- Create Deal Snapshot。
- Link Deal ID to Source Quote ID / Version。
- Record Converted By / Timestamp。
- Create warehouse position。
- Enable Deal Ticket export。
- Preserve downstream interface-ready fields。

---

## 10.4 Approval

有權限 Trader 可直接 Convert to Deal，不需主管 approval。  
但 Convert permission 需依 Book / Desk / Product Type / Action permission matrix 控制。

若 self-validation critical error 存在，Phase 2 不允許 Convert to Deal，除非未來 Phase 3 建立 formal exception flow。

---

# 11. Downstream Interface Readiness

Downstream Interface 屬 Phase 3。  
Phase 2 僅需支援 Deal Ticket 與 manual booking reference。  
Downstream interface field 詳細 schema 參見 Annex B 對應節，本節僅描述 status 與 readiness。

## 11.1 Downstream System Status

下游交易系統尚未指定：

`[TBD: Trading Desk / IT Architecture Owner, due date = Phase 3 initiation]`

系統不得 hard-code downstream-specific format、field name、product code 或 booking template。

---

## 11.2 Phase 2 Manual Booking

Phase 2 需支援：

- Deal Ticket Excel / PDF export。
- Manual Booking Reference。
- Manual Booking Status。
- Downstream System Name = TBD。
- Interface Status = Interface Not Enabled。

---

## 11.3 Phase 3 Interface-ready Fields

需預留：

- Downstream System Name
- Downstream Product Code
- Downstream Book Code
- Downstream Counterparty Code
- Downstream Trade Reference
- Outbound Payload Version
- Interface Submission Timestamp
- Downstream Response Timestamp
- Interface Error Message
- Idempotency Key

---

# 12. Warehousing / Position View

Warehousing 屬 Phase 2，不納入 MVP critical path。

只有 Converted Deal 進正式 warehouse position。  
Draft / Saved / Indicative Quote 不納入正式 position exposure。

Position Status 至少包含：

- Live
- Exercised
- Expired
- Terminated
- Cancelled
- Matured
- Pending Settlement
- Settled

Warehouse Position 需顯示：

- Deal ID
- Source Quote ID / Version
- Book / Desk / Trader
- Underlying ISIN
- Payoff Basis
- Option Type
- Position
- Exercise Style
- Settlement Type
- Strike
- Expiry
- Bond Option Notional
- Deposit Notional
- Participation Ratio
- Premium
- Current MTM
- Daily P&L
- Current bond price / yield
- Used curve / vol / spread
- Manual override indicator
- Annex A mode switches used at deal conversion
- Last revaluation timestamp

---

# 13. Revaluation

Revaluation 屬 Phase 2。

## 13.1 EOD Revaluation

- 每個營業日 FTP market data 完成後自動執行。
- EOD revaluation 為 official daily valuation。
- 每筆 active position 產生 official EOD valuation snapshot。
- 單筆失敗不可中斷整批。
- EOD revaluation 需在 ≤ 30 分鐘內完成所有 active position。

---

## 13.2 On-demand Revaluation

- Trader 可針對 selected position 觸發。
- 結果為 intraday reference。
- 不覆蓋 official EOD valuation。
- 可對 bond price / yield、vol、spread 做 override，reason 必填。
- 不可由 Trader 修改 curve point。

---

## 13.3 Official vs Intraday Discipline

| Valuation Type | 是否 official | 是否覆蓋 EOD | 用途 |
|---|---:|---:|---|
| EOD Revaluation | 是 | 是，作為當日 official snapshot | Official daily valuation |
| On-demand Revaluation | 否 | 否 | Intraday reference |
| On-demand with Override | 否 | 否 | Trader scenario / intraday explain |

---

# 14. Greeks / Sensitivities

## 14.1 Phase Scope

Greeks / Sensitivities 分階段交付：

| Phase | 範圍 |
|---|---|
| MVP | 歐式 Black-76 closed-form Greeks：Delta、Gamma、Vega、Theta；DV01 / CS01 採 bump-and-revalue；Internal Pricing Report 顯示 closed-form 與 bump-and-revalue 結果一致性檢查欄位 |
| Phase 2 | American tree Greeks 採 bump-and-revalue；Scenario engine；完整 risk parameter set 與維護 UI |
| Phase 3 | Portfolio-level Greeks aggregation |

MVP 若 American option pricing 已納入，American option 的 full Greeks 可先不作為 MVP mandatory output，但 pricing result 需保留欄位。

---

## 14.2 Required Measures

系統最終需支援：

- Price Delta
- Yield Delta
- Gamma
- Vega（Black price-vol Vega）
- Market-source Vega（依 active `BOND_VOL_SOURCE_MODE` 定義）
- Theta
- DV01 / PVBP
- CS01
- IR Delta
- Scenario P&L

---

## 14.3 預設 Risk Parameter

| 項目 | 預設 |
|---|---|
| Delta / Gamma | Central difference for bump check；European primary output uses closed-form if applicable |
| Price shock | ±0.01 clean price per 100 |
| Yield shock / DV01 | ±1bp |
| Black price-vol Vega check | final model input `σ_P` ±0.01（±1 price-vol percentage point），central difference；European primary output uses closed-form if applicable |
| Market-source Vega | `VCUB_NORMAL_PROXY`：bump resolved `σ_vcub` ±1.00 bp normal vol（normalize 後 ±1e-4 absolute yield-vol unit）並重跑 `λ_vcub → DCF adjustment → σ_Y^N → σ_P → pricing`；`DIRECT_PRICE_VOL`：bump approved `σ_P` ±0.01；`LOGNORMAL_YIELD_VOL_OVERRIDE`：bump override lognormal yield vol ±0.01 並重跑 reverse-normal conversion 與 downstream chain |
| CS01 | ±1bp，central difference |
| Theta | +1 business day |
| IR Delta | ±1bp parallel curve shift |
| Scenario | Price ±1、Yield ±10bp、Vol ±5 vol points、Spread ±10bp |

---

## 14.4 方法論

詳細方法論以 Annex A.9 為準。

MVP 需實作：

- European Black-76 closed-form Greeks。
- DV01 / CS01 bump-and-revalue。
- European closed-form vs bump-and-revalue consistency check。
- Put-call parity check。
- Internal Pricing Report 顯示：
  - closed-form Greeks
  - bump-and-revalue Greeks
  - difference percentage
  - warning if difference > 5%

Phase 2 補：

- American Greeks。
- Scenario engine。
- Risk parameter set UI。

---

# 15. Scenario Analysis

Scenario Analysis 屬 Phase 2。

Default scenario templates：

- Base Case
- Bond Price Up / Down
- Yield Up / Down
- Rates Up / Down
- Spread Widening / Tightening
- Vol Up / Down
- Combined Stress Up
- Combined Stress Down

Scenario 可用於：

- Pricing Result
- Quote Detail
- Deal Detail
- Warehousing Position
- Internal Report

Portfolio-level scenario 屬 Phase 3。

---

# 16. Portfolio-level Aggregation

Portfolio-level Aggregation 屬 Phase 3。

Aggregation dimensions：

- Book
- Desk
- Trader
- Currency
- ISIN
- Issuer
- Rating
- Sector
- Product Type
- Option Type
- Payoff Basis
- Exercise Style
- Settlement Type
- Expiry Bucket
- Bond Maturity Bucket
- Position Status

Portfolio view 需顯示：

- Number of Positions
- Total Deposit Notional
- Total Bond Option Notional
- Total MTM
- Daily P&L
- Price Delta
- Yield Delta
- Gamma
- Vega
- Theta
- DV01 / PVBP
- CS01
- IR Delta
- Scenario P&L
- Warning / Error Count

---

# 17. Risk Limit / Alert / Notification

Risk Limit / Alert / Notification 屬 Phase 3，不納入 MVP critical path。

## 17.1 Alert Types

系統需支援 configurable risk alert / hard limit。

Alert types：

- MTM Loss Alert
- Daily P&L Alert
- DV01 Alert
- CS01 Alert
- Vega Alert
- Gamma Alert
- Scenario Loss Alert
- Concentration Alert
- Revaluation Failure Alert
- Market Data Quality Alert
- Self-validation Critical Error Alert

---

## 17.2 Alert Severity

- Info
- Warning
- Breach
- Critical

---

## 17.3 Alert Behavior

| Behavior | 說明 |
|---|---|
| Soft Alert | 提醒但不阻擋 |
| Hard Limit | 阻擋指定 action |

預設：

- Warehousing EOD breach = Soft Alert + acknowledgement。
- Quote-to-Deal 若觸發 hard limit = blocked。

---

## 17.4 Notification

In-app notification 必做。  
Email / Teams / Slack / internal chat 可設定，實際通道待 IT 評估：

`[TBD: IT Architecture Owner / IT Security Owner, due date = Phase 3 design sign-off]`

---

## 17.5 Hard Limit Blocking

本節僅描述 Risk-driven Hard Limit，不包含 §7.6 的 Market Data Blocking。

Risk Hard Limit 可阻擋：

- Convert to Deal
- Downstream submission
- Deal booking status update
- Quote export, if configured

Risk Hard Limit 不可被 Trader 自行 release。

---

## 17.6 Limit Exception 流程

### 初始治理規則

Hard Limit 一律不可由 Trader release。

若需要解除 Hard Limit，只能透過修改 limit / rule 設定完成。  
修改 limit 需 Admin + Risk Owner 雙人簽核。

最低要求：

1. Trader 不可自行 release。
2. Admin 可提交 limit change request。
3. Risk Owner 必須 approve。
4. Approved change 生效後才可解除 blocking。
5. 所有變更必須留 audit。
6. 原 blocked action 不可自動重送，需使用者重新觸發。

### Phase 3 擴充

Phase 3 可擴充「Limit Exception 暫時 release」流程：

- Exception ID
- Affected Book / Desk / Product / Risk Measure
- Temporary threshold
- Effective From / To
- Requested By
- Approved By
- Reason
- Expiry timestamp
- Auto-expire rule
- Audit trail

Exception 到期後必須自動失效，不得永久開放。

---

# 18. Audit Trail

## 18.1 原則

所有關鍵操作必須留 audit trail。

Audit trail 規則：

- Append-only。
- 一般使用者不可刪除。
- 一般使用者不可修改。
- 若需更正，只能新增 correction / reversal / comment。
- 保存 5 年。
- 5 年內需可查詢與匯出。

---

## 18.2 MVP Basic Audit Log

MVP 至少需寫入：

- FTP import start / success / failed
- Pricing created
- Recalculate
- Market data override
- Override reason
- Annex A mode switch selected / changed
- Save Quote
- Export Report
- Permission failed
- Self-validation warning / critical error
- System error

MVP 不要求完整 audit search UI，但 audit data model 需支援後續 5 年 retention。

---

## 18.3 Phase 2 Full Audit Trail

Phase 2 補上：

- 5 年保存機制
- Audit search UI
- Audit export
- Archive / restore / purge control
- Quote-to-Deal audit
- Warehouse / valuation audit
- Permission matrix change audit
- Risk parameter change audit
- Mode switch history query

---

## 18.4 Audit Event 欄位

Audit event 至少包含：

- Audit Event ID
- Event Timestamp
- Event Type
- Action
- Entity Type
- Entity ID
- Quote ID, if applicable
- Quote Version, if applicable
- Deal ID, if applicable
- Position ID, if applicable
- User ID
- User Role
- Desk
- Book
- Product Type
- Old Value, if applicable
- New Value, if applicable
- Reason / Comment
- Market Data Snapshot ID
- Pricing Model Version
- Mode Switch Snapshot ID
- Self-validation Result ID
- Result：Success / Failed / Blocked
- Error Message
- System Generated Flag

---

# 19. Authentication / Authorization

## 19.1 Authentication

登入來源：

- 公司 AD / SSO。

實際協定需由 IT 確認，不由本規格決定：

`[TBD: IT Security Owner, due date = technical kickoff]`

可能選項：

- SAML
- OIDC
- Windows Auth
- 其他行內標準

---

## 19.2 MVP Authorization

MVP 可先支援 basic role-based permission：

- Trader
- Trading Desk Lead
- Product Owner
- Admin
- IT Support
- Viewer

MVP 敏感操作需檢查：

- pricing
- override
- save quote
- export report
- mode switch selection
- methodology configuration maintenance
- admin config
- FTP batch rerun, if enabled

---

## 19.3 Phase 2 Action-level Permission Matrix

Phase 2 補上完整 permission matrix：

- User
- Role
- Group
- Desk
- Book
- Product Type
- Action
- Effective Date
- Status

Action-level permission 至少包含：

- View Pricing
- Create Pricing
- Recalculate Pricing
- Save Quote
- Export Internal Report
- Export Client Summary
- Convert to Deal
- Export Deal Ticket
- Update Manual Booking Reference
- Trigger On-demand Revaluation
- Run Scenario
- Maintain Risk Parameter
- Maintain Alert Rule
- Maintain Curve Mapping
- Maintain Permission Matrix
- Maintain Methodology Mode Defaults
- View / Export Audit Trail

---

# 20. Benchmark / UAT

## 20.1 Benchmark 來源

Benchmark 來源：

- Bloomberg
- Vendor pricing tool

每個 UAT case 手動指定 official benchmark source。  
若 Bloomberg 與 vendor 不一致，只有被指定為 official benchmark 的結果決定 pass / fail，另一個保留作 reconciliation reference。

---

## 20.2 UAT Evidence Package

每個 UAT case 需保存：

- Product terms
- Pricing model version
- Annex A mode switch values
- Self-validation results
- Market data as-of
- Bloomberg screenshot / export
- Vendor screenshot / export
- Official benchmark source
- Python pricer result
- Difference
- Tolerance
- Pass / Fail
- Reconciliation comment

若 case 使用 `VCUB_NORMAL_PROXY`，evidence package 另需逐層保存並對價：

- `FY` / `KY`
- `Texp` / `Ttenor` / `KATM` / `Kproxy` / `KY-FY`
- resolved `σ_vcub` 與 unit normalization
- ATM absolute normal vol + OTM skew spread（若適用）
- `λ_vcub`
- `DCF_VCUB` / `DCF_BondVol` 與 convention
- derived normal bond yield vol `σ_Y^N`
- bond duration `D_B`
- final lognormal price vol `σ_P`
- Black premium

Bloomberg parity 不得只以 premium coincidence 作為 methodology 證據；可觀察的 intermediate values 應逐層 reconciliation。

---

## 20.3 MVP Required UAT Cases

MVP 至少需測：

- European price call cash
- European price put cash
- European yield call cash with MODE_A
- European yield put cash with MODE_A
- European yield call cash with MODE_B
- European yield put cash with MODE_B
- American price call cash
- American price put cash
- American yield call cash forced MODE_B
- American yield put cash forced MODE_B
- Physical settlement invoice calculation
- FTP import success
- FTP missing bond price with override
- Missing yield curve blocking
- Missing curve mapping blocking
- Vol override
- Spread override
- Quote save / version
- Report export
- Audit log creation
- 繁中 / 英文 termsheet export
- Put-call parity check
- American ≥ European lower bound
- Closed-form vs bump-and-revalue consistency check
- VCUB exact-node ATM normal vol → normal bond yield vol → price vol
- VCUB OTM `ATM + spread` reconstruction
- Proxy coordinate mapping `Texp = TF`, `Ttenor = TB - TF`, `Kproxy = KATM + (KY - FY)`
- Normal-vol bp normalization (`1bp = 1e-4`)
- `λ_vcub` scaling and parameter-set audit
- `DCF_VCUB` / `DCF_BondVol` total-variance convention adjustment
- **任一 `DCF_VCUB` / `DCF_BondVol` convention unresolved 時，`VCUB_NORMAL_PROXY` pricing fail-closed，且不得默認 ratio = 1**
- `σ_P = |D_B| × σ_Y^N` duration conversion
- Vega source-mode semantics：VCUB mode bump `σ_vcub` market input 而非 derived `σ_Y^N`；direct price-vol mode bump approved `σ_P`；lognormal-yield override mode bump override input並重跑 conversion chain
- VCUB off-grid unresolved blocks under `EXACT_NODE_ONLY`
- `DIRECT_PRICE_VOL` approved override path
- `LOGNORMAL_YIELD_VOL_OVERRIDE` reverse normal-vol conversion path
- Shifted Black disabled default
- Shifted Black enabled with reason

---

# 21. Pricing Model Version Control

每次 calculation 必須保存：

- Pricing Model Name
- Pricing Model Version
- Pricing Engine Version
- Code Version
- Model Parameter Set Version
- Risk Parameter Set Version
- Market Data Snapshot ID
- Curve Version
- Vol Version
- Credit Spread Version
- Benchmark Source
- Calculation Timestamp
- Annex A Mode Switch Snapshot
- Self-validation Result

模型改版後：

- 歷史 Quote / Deal / Warehouse Valuation 不可被覆蓋。
- 使用新模型重算時需產生新 Quote Version 或新 Valuation Snapshot。
- 需可做新舊模型結果比較。

系統不得 silent switch model version。  
若使用新 model version，Internal Pricing Report、audit trail、valuation history 必須清楚顯示。

Methodology 變更流程依 Annex A.14：

1. Trader 提出變更建議。
2. Trading Desk Lead 簽核。
3. 重跑 Annex A.13 self-validation。
4. 寫入 Model Version Control。
5. 以新 model version 上線。
6. 舊版 Quote / Deal / Valuation 不可被覆蓋。

---

# 22. Multi-language

## 22.1 MVP 語言範圍

MVP 僅支援：

| 項目 | MVP |
|---|---|
| UI | English |
| Internal Pricing Report | English |
| Client-facing Termsheet | 繁體中文 / English |
| Error Message | English |
| Audit Log | English canonical event/action |

---

## 22.2 Phase 3 語言範圍

Phase 3 支援：

- Japanese UI
- Japanese termsheet
- Japanese report template
- 三語 glossary version control

---

## 22.3 Canonical Code

內部資料、API、DB、status code 使用 English canonical code。

例如：

- PRICE / YIELD
- CALL / PUT
- CASH / PHYSICAL
- EUROPEAN / AMERICAN
- SAVED / EXPORTED / CONVERTED_TO_DEAL
- MODE_A / MODE_B
- VCUB_NORMAL_PROXY / DIRECT_PRICE_VOL / LOGNORMAL_YIELD_VOL_OVERRIDE
- EXACT_NODE_ONLY / IN_GRID_BILINEAR_V1 / FAIL_CLOSED

UI translation 不得影響 pricing logic、DB value 或 API payload。

---

## 22.4 Glossary Review

Glossary review 不得綁定 MVP release critical path。  
MVP 需至少完成 termsheet 上會出現的繁中 / 英文關鍵詞。

日文 glossary 屬 Phase 3：

`[TBD: Product Owner / Japanese Desk Reviewer, due date = Phase 3 UAT start]`

---

# 23. 品牌視覺 / 大樹圖像

品牌視覺完整規範已移至 Annex C。

MVP 僅需：

- Header 放置單一 logo png。
- 不要求完整品牌動線。
- 不要求 dashboard illustration。
- 不要求空狀態插圖。
- 不要求 PDF 封面視覺規範。

MVP UI 不得因 logo 影響 pricing fields、market data、warning、error 或 report readability。

---

# 24. 待確認事項

所有 TBD 必須標註 owner 與 due date。

| 項目 | TBD 標註 |
|---|---|
| NPA / internal governance 是否適用 | [TBD: Product Owner / Compliance / Risk / IT, due date = before MVP UAT start] |
| FTP filename pattern 與欄位格式 | [TBD: Market Data Owner / IT, due date = Kickoff + 10 business days] |
| FTP cut-off time | [TBD: Market Data Owner, due date = Kickoff + 10 business days] |
| Bloomberg function / screen | [TBD: Trading Desk Lead / Product Owner, due date = MVP UAT preparation] |
| Vendor tool name and export format | [TBD: Trading Desk Lead / Product Owner, due date = MVP UAT preparation] |
| Physical delivery invoice detail | [TBD: Operations / Trading Desk Lead, due date = MVP UAT preparation] |
| Initial curve mapping table | [TBD: Market Data Owner / Trading Desk Lead, due date = MVP SIT start] |
| Initial spread mapping table | [TBD: Market Data Owner / Trading Desk Lead, due date = MVP SIT start] |
| Initial vol mapping table | [TBD: Market Data Owner / Trading Desk Lead, due date = MVP SIT start] |
| Override sanity threshold | [TBD: Trading Desk Lead / Market Data Owner, due date = MVP SIT start] |
| Authentication protocol | [TBD: IT Security Owner, due date = technical kickoff] |
| Batch scheduler | [TBD: IT Architecture Owner, due date = technical kickoff] |
| Deployment model | [TBD: IT Infrastructure Owner, due date = solution design sign-off] |
| Audit async retry / alert design | [TBD: IT Architecture Owner, due date = solution design sign-off] |
| Phase 2 permission matrix user list | [TBD: Trading Desk Lead / Admin, due date = Phase 2 design start] |
| Phase 3 downstream system | [TBD: Trading Desk Lead / IT Architecture Owner, due date = Phase 3 initiation] |
| Phase 3 risk limit threshold values | [TBD: Risk Owner / Trading Desk Lead, due date = Phase 3 UAT preparation] |
| Phase 3 repo / financing curve enhancement | [TBD: Trading Desk Lead / Product Owner, due date = Phase 2 model review] |
| Japanese glossary reviewer | [TBD: Product Owner / Japanese Desk Reviewer, due date = Phase 3 UAT start] |

---
