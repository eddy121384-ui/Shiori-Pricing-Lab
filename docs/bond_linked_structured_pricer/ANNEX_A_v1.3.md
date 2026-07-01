---
title: Annex A v1.3 - Pricing Methodology Specification
version: 1.3
source: GPT direct markdown output（非 PDF 反向解析）
authoritative: true
---

# Annex A：Pricing Methodology Specification（自幹版 v1.3）

> 本版本為**純自幹專案**使用：沒有獨立 Quant team。  
> 所有原本標為 `[TBD: Quant sign-off]` 的決策，已依市場慣例寫死預設值。  
> 凡是有兩種合理寫法的地方，做成**系統內 Mode Switch**，由 Trader 在 UI 上選擇，並把選擇連同 pricing run 一起寫入 audit 與 Internal Pricing Report。

---

## A.0 適用範圍與所有權

- 本附錄為 Bond Linked Structured Pricer MVP 的 pricing methodology 正式定義。
- 本專案無獨立 Quant team，方法論由 **Trading Desk 自有**。
- 任何方法論變更必須：
  1. 由 Trader 提出變更建議。
  2. Trading Desk Lead 簽核。
  3. 在 §A.13 self-validation framework 下重跑驗證。
  4. 寫入 Model Version Control（§21）。
- 任何 pricing run 必須在 Internal Pricing Report 顯示當下使用的所有 mode 設定（§A.12）。

---

## A.1 Product Universe

**MVP 支援：**

- Single cash bond underlying
- Non-callable、Non-sinkable、Plain vanilla bullet
- Fixed coupon
- Clean price per 100 報價慣例
- Yield 慣例依 Bond Master `yield_convention` 欄位（見 §A.6.2 預設表）

**MVP 不支援（一律於 Bond Master 階段 reject）：**

- Callable、Sinkable、Amortizing、Convertible、Perpetual
- Bond basket、Bond future、Structured bond
- Floating-rate bond / Inflation-linked bond（Phase 2 評估）

---

## A.2 European Price-based Option（Black-76 on Forward Clean Price）

### A.2.1 方向慣例

- Price Call：對 final clean price 上升有 payoff。
- Price Put：對 final clean price 下降有 payoff。
- 與 equity / FX option 方向慣例一致。

---

### A.2.2 變數

```text
F  = forward clean price per 100
K  = strike clean price per 100
σ  = price volatility（per annum）
T  = time to expiry（year fraction，ACT/365F）
DF = discount factor from Option Discount Curve (pricing date → expiry date)
N  = Bond Option Notional
```

---

### A.2.3 公式

```text
d1 = [ln(F / K) + 0.5 σ² T] / (σ √T)
d2 = d1 - σ √T

Price Call PV per 100 = DF × [F × Φ(d1) - K × Φ(d2)]
Price Put  PV per 100 = DF × [K × Φ(-d2) - F × Φ(-d1)]

Price Call Option PV = (PV per 100) × N / 100
Price Put  Option PV = (PV per 100) × N / 100
```

---

### A.2.4 Boundary Conditions

- F > 0、K > 0、σ > 0、T > 0；否則 pricing blocked。
- 若 vol input 為 yield vol，必須先透過 §A.8 轉換為 equivalent price vol。

---

### A.2.5 Closed-form Greeks（MVP 必做）

```text
Delta_F   = DF × Φ(d1)            # for Call
Delta_F   = -DF × Φ(-d1)          # for Put
Gamma_F   = DF × φ(d1) / (F σ √T)
Vega      = DF × F × φ(d1) × √T          # per 1.00 vol unit；UI 顯示時除以 100
Theta     = -DF × F × φ(d1) × σ / (2√T) - r × DF × [F Φ(d1) - K Φ(d2)]   # for Call
DV01      = bump-and-revalue ±1bp underlying yield（見 §A.9）
CS01      = bump-and-revalue ±1bp credit spread（見 §A.9）
```

其中 Φ = standard normal CDF；φ = standard normal PDF；r = 對應 T 的 discount rate。

---

## A.3 European Yield-based Option（雙模式系統切換）

### A.3.1 方向慣例（業界標準）

- **Yield Call** ≡ 對 yield 上升有 payoff  
  ≡ 在 clean price 空間等同於 **Price Put on clean price**。
- **Yield Put** ≡ 對 yield 下降有 payoff  
  ≡ 在 clean price 空間等同於 **Price Call on clean price**。

此方向與 equity / FX option 直覺相反。UI、報表、termsheet、audit 一律沿用此慣例。  
Trader 在 UI 選擇 Yield Call / Put 時，系統需即時顯示 clean price 方向 hint。

---

### A.3.2 Mode Switch：`YIELD_OPTION_MODE`

| Mode | 名稱 | MVP 預設 | 適用範圍 |
|---|---|---:|---|
| `MODE_A` | DV01-based Closed-form (Black-76) | ✅ 預設 | European yield-based only |
| `MODE_B` | Numerical Conversion at Expiry |  | European, American, path-dependent |

**切換規則：**

- Trader 在 pricing form 上可選擇 mode；預設 `MODE_A`。
- American yield-based option 系統自動 force `MODE_B`，Trader 不可選 `MODE_A`。
- 同一 Quote 內若混合多筆 option，每筆獨立記錄 mode。
- mode 寫入 PricingResult、QuoteVersion snapshot 與 audit log。
- Internal Pricing Report 顯示「Yield Option Mode = MODE_A / MODE_B」。

---

### MODE_A：DV01-based Closed-form Conversion

**理論基礎**：先在 yield 空間用 Black-76 算 option 期望值（單位 = yield × T），再用 underlying bond 在 expiry 的 DV01 換算成 price 空間 PV。這是 sell-side 報 bond option 時最常用的快速封閉解。

**變數**

```text
YF   = forward yield at expiry
YK   = strike yield
σY   = yield volatility（per annum，lognormal yield 假設）
T    = time to expiry
DF   = discount factor from Option Discount Curve
DV01_expiry = DV01 of underlying bond at expiry date
              （以 expiry 後 remaining cash flows 計算，per 1bp，per 100 face）
N    = Bond Option Notional
```

**公式**

```text
d1 = [ln(YF / YK) + 0.5 σY² T] / (σY √T)
d2 = d1 - σY √T

Yield Call yield-space value = DF × [YF × Φ(d1) - YK × Φ(d2)]   # 單位 = decimal yield
Yield Put  yield-space value = DF × [YK × Φ(-d2) - YF × Φ(-d1)]

# 換算成 price-space PV（per Bond Option Notional）
# DV01_expiry 為 1bp 對應的 price 變動（per 100 face），因此 1.0 decimal yield 對應 10000 × DV01
Yield Call Option PV = yield-space value × 10000 × DV01_expiry × N / 100
Yield Put  Option PV = yield-space value × 10000 × DV01_expiry × N / 100
```

**Closed-form Greeks**

```text
Yield Delta = DF × Φ(d1) × 10000 × DV01_expiry × N / 100     # for Yield Call
Yield Delta = -DF × Φ(-d1) × 10000 × DV01_expiry × N / 100   # for Yield Put
Vega        = DF × YF × φ(d1) × √T × 10000 × DV01_expiry × N / 100

Price Delta = Yield Delta × (-1 / DV01_underlying)
              （符號反向：yield ↑ → price ↓）
```

**優點**：封閉解、與市場 yield vol quote 一致、latency 極短。  
**限制**：lognormal yield 假設、DV01 為線性近似、不適用 American / path-dependent。

---

### MODE_B：Numerical Conversion at Expiry

**理論基礎**：直接在 yield 空間模擬 final yield 分布，逐情境做 yield-to-price conversion 算 clean price payoff，最後折現。

**流程**

1. 在 yield 空間以 Black-76 lognormal yield 推導 final yield 分布。
2. 對 final yield 分布做數值積分（European）或 tree backward induction（American）。
3. 每個情境執行 §A.6 yield-to-price conversion → clean price → 計算 payoff。
4. 折現使用 Option Discount Curve。

**European 預設**：Black-76 yield model + Gauss-Hermite quadrature（21 nodes）。  
**American 預設**：見 §A.4 CRR yield-state tree。

**優點**：對 yield-to-price 非線性處理較準，框架統一適用 American / path-dependent。  
**限制**：無封閉解，pricing latency 較長，需 convergence control。  
**Greeks**：一律 bump-and-revalue。

---

### A.3.3 Boundary Conditions

- `YF > 0`、`YK > 0`、`σY > 0`、`T > 0` 才可使用 standard lognormal Black-76。
- `YF ≤ 0` 或 `YK ≤ 0`：
  - MVP 預設 **blocking**。
  - Shifted Black 見 §A.11（MVP 未啟用，需 Trader 在 UI 顯式打開）。

---

## A.4 American Option

### A.4.1 預設模型：CRR Binomial Tree

**Price-state Tree（Price-based American）**

- State variable = clean price。
- Vol = price vol 或 equivalent price vol（§A.8）。
- Early exercise value 依 clean price payoff。
- 折現使用 Option Discount Curve。

**Yield-state Tree（Yield-based American）**

- State variable = yield。
- Vol = yield vol（lognormal yield assumption）。
- 每個 node 透過 §A.6 將 yield 轉 clean price。
- Early exercise value 依 converted clean price payoff + §A.3.1 方向慣例。
- 折現使用 Option Discount Curve。

---

### A.4.2 Coupon Node 處理（預設規則，不再 TBD）

**Price-state tree：**

- 在每個 coupon payment date 對應的 tree slice 上，所有 node 的 clean price state **向下跳一個 coupon 金額（per 100 face）**。
- 若 coupon date 落在 tree node 之間，採「**前移**」處理：將 coupon 效應提前到 **前一個 tree node** 執行（保守處理，避免 early exercise 時誤判 in-the-money）。

**Yield-state tree：**

- Coupon 不修改 yield state；coupon 對 cash flow 的影響在 §A.6 yield-to-price conversion 中自動處理（remaining cash flow 已扣掉 past coupons）。
- Early exercise payoff 永遠用「ex-coupon clean price」計算，避免重複計入 accrued。

---

### A.4.3 Tree Parameters：Mode Switch `CRR_STEPS`

| 選項 | Steps | 適用情境 |
|---|---:|---|
| FAST | 100 | 報價快速試算、Greeks 試算 |
| STD | 250 | 一般報價 |
| HIGH | 500 | ✅ MVP 預設，正式報價與 EOD |
| ULTRA | 1000 | Vega-sensitive 或 long-dated 結構 |
| MAX | 2000 | UAT benchmark 對 Bloomberg |

**Convergence Check（系統自動執行）**：

- 美式 PV 須 ≥ 對應歐式 PV（不可違反 American premium ≥ 0）。
- 違反時顯示 warning 並建議升一級 step。
- STD 與 HIGH 之間結果差異 > 0.5% per 100 face 時顯示 warning。

---

### A.4.4 Future Extension（非 MVP）

- Hull-White short-rate tree
- Finite difference (Crank-Nicolson)
- Least-squares Monte Carlo (Longstaff-Schwartz)

---

## A.5 Forward Clean Price 推導

### A.5.1 假設聲明（自幹版預設，不引入 repo curve）

- MVP **不引入** bond-specific repo curve 或 specialness adjustment。
- Forward 採 **Bond Reference Curve（含 credit spread）** 做 cost-of-carry approximation。
- 在 specialness 顯著的 bond（on-the-run treasury、squeeze 期間 issue）本方法可能偏差顯著——MVP 暫不處理，UAT 對 Bloomberg 時記錄誤差，必要時改用 trader override。
- 若未來引入 repo curve，§A.2、§A.3、§A.4 之 forward 推導需重新檢視。

---

### A.5.2 公式

```text
Spot Dirty Price       = Spot Clean Price + AI(pricing date)

PV(coupons before expiry) = Σ Coupon_i × DF_bond_reference(pricing date, coupon_date_i)
                           其中 coupon_date_i ∈ (pricing date, expiry date]

Forward Dirty Price(expiry) = [Spot Dirty Price - PV(coupons before expiry)]
                              / DF_bond_reference(pricing date, expiry)

Forward Clean Price(expiry) = Forward Dirty Price(expiry) - AI(expiry date)
```

---

### A.5.3 Curve 使用

- PV(coupons before expiry)：**Bond Reference Curve**。
- Forward discount：**Bond Reference Curve**。
- Option PV 折現：**Option Discount Curve**（不可混用）。

---

## A.6 Yield-to-Price Conversion

### A.6.1 公式骨架

At expiry date：

```text
Dirty Price(y) = Σ CF_i / (1 + y / m)^(m × t_i)
                其中 t_i = year fraction 從 expiry 到 cash flow i，依 day_count 計算。

Clean Price(y) = Dirty Price(y) - Accrued Interest(expiry date)
```

---

### A.6.2 `yield_convention` 預設對照表（市場慣例，寫死）

| Market / Bond Type | `yield_convention` | m | day_count | 備註 |
|---|---|---:|---|---|
| US Treasury Notes/Bonds | SEMI_ANNUAL_COMPOUND | 2 | ACT/ACT | 業界標準 |
| US Corporate | SEMI_ANNUAL_COMPOUND | 2 | 30/360 | 業界標準 |
| US TIPS（MVP 不支援） | — | — | — | inflation-linked，Phase 2 評估 |
| Euro Govt (DE, FR, IT…) | ANNUAL_COMPOUND | 1 | ACT/ACT | ICMA 標準 |
| Euro Corporate | ANNUAL_COMPOUND | 1 | ACT/ACT | ICMA 標準 |
| UK Gilt | SEMI_ANNUAL_COMPOUND | 2 | ACT/ACT |  |
| JGB | JAPANESE_COMPOUND | 2 | ACT/365F | 日本特殊複利慣例；MVP 可用 SEMI 近似 |
| JGB（短票 < 1Y） | SIMPLE_YIELD | 1 | ACT/365F | discount basis |
| TW Govt Bond | ANNUAL_COMPOUND | 1 | ACT/365 | 本系統台幣預設 |
| TW Corporate / Financial | ANNUAL_COMPOUND | 1 | 30/360 |  |
| AU / NZ Govt | SEMI_ANNUAL_COMPOUND | 2 | ACT/ACT |  |
| KR Govt (KTB) | SEMI_ANNUAL_COMPOUND | 2 | ACT/365 |  |
| HK / SG Govt | SEMI_ANNUAL_COMPOUND | 2 | ACT/365F |  |
| 其他 | OTHER → 系統 reject | — | — | Bond Master 必須補完才能 pricing |

- `yield_convention` 與 `day_count` 由 **Bond Master FTP 匯入時帶入**。
- Bond Master 沒帶值 → 進不來 MVP pricing pool。
- 若 `yield_convention = OTHER`，Trader 需手動在 Bond Master maintenance 補上對應的 m 與 day_count，並寫入 audit。

---

### A.6.3 規則

- **不採用** modified duration 線性近似作為正式 payoff conversion。
- **不採用** par-yield approximation。
- Accrued Interest 計算依 Bond Master `day_count` 與 coupon schedule。
- Ex-coupon 期間（若 Bond Master `ex_dividend_days > 0`）AI 計算需考慮負值情境。

---

## A.7 Physical Delivery Settlement

### A.7.1 Payoff 判斷

- Payoff comparison：**exercise / expiry date 的 clean price**。

---

### A.7.2 Invoice

- Invoice 使用 **settlement date 的 dirty price**：

```text
Dirty Settlement Price = Clean Settlement Price + AI(settlement date)
Settlement Amount      = Dirty Settlement Price × Deliverable Face Amount / 100
```

---

### A.7.3 Settlement Lag 預設表（市場慣例，寫死）

| Market | Default Settlement Lag | Calendar Source |
|---|---|---|
| US Treasury | T+1 | US Federal Reserve |
| US Corporate | T+2 | US SIFMA |
| Euro Govt / Corporate | T+2 | TARGET |
| UK Gilt | T+1 | UK London |
| JGB | T+1 | Japan TSE |
| TW Govt / Corporate | T+1 | TW Taipei |
| AU / NZ | T+2 | Sydney / Wellington |
| KR Govt | T+1 | Seoul |
| HK / SG | T+2 | HKEX / SGX |

- Trader 可手動覆蓋，需 reason，進 audit。
- Settlement lag 跨 coupon date → AI **必須**依 settlement date 重算。
- Settlement currency 預設 = bond currency。Quanto 屬 Phase 2 / Phase 3。

---

## A.8 Equivalent Price Vol Conversion（雙模式系統切換）

### A.8.1 Mode Switch：`PRICE_VOL_CONVERSION_MODE`

| Mode | 名稱 | MVP 預設 |
|---|---|---:|
| `MODE_1` | First-order approximation | ✅ 預設 |
| `MODE_2` | Convexity-corrected |  |

- Trader 可在 pricing form 上切換；預設 `MODE_1`。
- 切換選擇寫入 PricingResult、Internal Pricing Report 與 audit。
- 同一 underlying 在不同 quote 上可使用不同 mode（trader 自負方法論一致性）。

---

### A.8.2 MODE_1：First-order Approximation（業界 sell-side 常用）

```text
σ_P ≈ σ_Y × Y × ModDur
```

其中：

- σ_Y = underlying bond yield vol（per annum，lognormal yield）
- Y = forward yield at expiry
- ModDur = modified duration of underlying bond at expiry date

**性質**：first-order，未考慮 convexity 與 yield curve shape。short-dated 與 mid yield 情境精度足夠。

---

### A.8.3 MODE_2：Convexity-corrected

```text
σ_P ≈ σ_Y × Y × ModDur × (1 - 0.5 × Convexity × σ_Y² × Y² × T)
```

- Convexity 來自 underlying bond 在 expiry 的 cash flow。
- long-dated bond（> 5Y）或 high yield environment（Y > 6%）建議切到 MODE_2。

---

### A.8.4 透明性要求（不可省略）

任何 pricing run 使用 equivalent price vol，Internal Pricing Report 必須顯示：

- original vol basis = `YIELD_VOL`
- pricing vol basis = `EQUIVALENT_PRICE_VOL`
- conversion mode = `MODE_1` / `MODE_2`
- σ_Y、Y、ModDur（及 MODE_2 時的 Convexity、T）三 / 五個 input 數值
- 推導出的 σ_P 數值

**不得 silent fallback**。Trader 若覺得結果不合理，必須用 trader override 改 σ_P 直接輸入。

---

## A.9 Greeks 方法論

### A.9.1 MVP 範圍（自幹版）

- **European Black-76 closed-form Greeks**（Delta、Gamma、Vega、Theta）：MVP 必做。
- **DV01、CS01**：MVP 採 bump-and-revalue ±1bp central difference。
- **American Greeks**：MVP 可選做 bump-and-revalue（FAST tree, 100 steps）以節省 latency。
- **Scenario engine、Portfolio aggregation**：Phase 2 / Phase 3。

---

### A.9.2 DV01

- **Yield-based MODE_A**：σ_Y 不變，bump underlying yield ±1bp → 重算 YF、DV01_expiry → 重算 PV → central difference。
- **Yield-based MODE_B**：σ_Y 不變，bump underlying yield ±1bp → 重做數值積分 → 重算 PV → central difference。
- **Price-based**：bump underlying yield ±1bp → 透過 Bond Reference Curve 重算 forward clean price → 重算 PV → central difference。

---

### A.9.3 CS01

- bump credit spread ±1bp → 更新 Bond Reference Curve / bond-specific spread → 重新推導 forward → 重新計算 PV → central difference。

---

### A.9.4 Vega

- bump σ ±1 vol point → 其他資料不變 → 重算 PV → central difference。
- 使用 equivalent price vol 時，**bump σ_Y**（market input 端），σ_P 自動隨之更新。

---

### A.9.5 Consistency Check（自幹版必做）

歐式 option 的 Internal Pricing Report 必須同時顯示：

- closed-form Greeks
- bump-and-revalue Greeks
- 差異百分比

**Warning threshold：> 5% 顯示警示**。這是自幹版的最後一道防線，因為沒有 Quant 把關，只能靠系統自己抓 bug。

---

## A.10 Vol & Curve Interpolation 預設（市場慣例，寫死）

### A.10.1 Vol Surface Interpolation

- **Maturity 方向**：linear in variance (`σ² × T`)。
- **Strike / Moneyness 方向**：linear in log-moneyness `ln(K/F)`。
- **超出 surface 範圍**：flat extrapolation（不外推），並標示 fallback flag。

---

### A.10.2 Yield Curve Interpolation

- **方法**：piecewise linear on **zero rates**（continuously compounded）。
- **Day count**：ACT/365F 統一在 curve 內部運算，匯入時依 FTP 提供的 convention 轉換。
- **超出 curve 範圍**：flat extrapolation，並標示 fallback flag。

---

### A.10.3 Curve 建構

- MVP 不自建 bootstrapping engine。
- FTP curve 已是 zero / par curve 任一形式時，依 source 標示處理：
  - FTP 直接給 zero curve → 直接使用。
  - FTP 給 par curve → MVP 採 piecewise linear par→zero 簡化轉換（不做 forward-rate smoothing）。

---

## A.11 Shifted Black（MVP 未啟用）

- MVP 預設 **不啟用** shifted Black。
- 若市場進入負利率 / 負 yield 環境，Trader 需在 UI 上顯式打開 `ENABLE_SHIFTED_BLACK` flag。
- **預設 shift size = 3.00%**（即 ε = 300bp）。
  - 此預設值參考 EUR rates option 市場（2015–2022 期間 ECB 政策利率區間）。
  - Trader 可在 UI 調整，調整需 reason 並進 audit。
- 啟用 shifted Black 時，§A.3 MODE_A 公式變成：

```text
d1 = [ln((YF + ε) / (YK + ε)) + 0.5 σY² T] / (σY √T)
d2 = d1 - σY √T
```

- 同一 Quote 內所有 leg 必須使用相同 shift size，避免內部不一致。

---

## A.12 Mode Switch Configuration（總表）

> 自幹版的核心治理思路：把所有「兩種寫法都對」的方法論變成系統內 switch，每筆 pricing 都記下選擇，UAT 與事後檢視都有跡可循。

| Switch | 預設值 | 可選值 | 誰可改 | Audit |
|---|---|---|---|---|
| `YIELD_OPTION_MODE` | `MODE_A` | `MODE_A` / `MODE_B` | Trader (per pricing) | ✅ |
| `PRICE_VOL_CONVERSION_MODE` | `MODE_1` | `MODE_1` / `MODE_2` | Trader (per pricing) | ✅ |
| `CRR_STEPS` | `HIGH(500)` | `FAST(100)` / `STD(250)` / `HIGH(500)` / `ULTRA(1000)` / `MAX(2000)` | Trader (per pricing) | ✅ |
| `ENABLE_SHIFTED_BLACK` | `false` | `true` / `false` | Trader (per pricing) | ✅ |
| `SHIFTED_BLACK_EPSILON` | `3.00%` | 0.00% – 5.00% | Trader (per pricing) | ✅ |
| `VOL_INTERP_MATURITY` | `LINEAR_VARIANCE` | `LINEAR_VARIANCE` / `LINEAR_VOL` | Trading Desk Lead | ✅ |
| `VOL_INTERP_STRIKE` | `LINEAR_LOG_MONEYNESS` | `LINEAR_LOG_MONEYNESS` / `LINEAR_STRIKE` | Trading Desk Lead | ✅ |
| `CURVE_INTERP` | `LINEAR_ZERO` | `LINEAR_ZERO` / `LINEAR_DF` | Trading Desk Lead | ✅ |
| `AMERICAN_GREEKS_TREE_STEPS` | `FAST(100)` | `FAST(100)` / `STD(250)` | Trader (per pricing) | ✅ |

**規則：**

1. 所有 mode 寫入 PricingResult snapshot，Quote / Deal reproduce 時必須使用相同 mode。
2. Internal Pricing Report 頂端 metadata 區塊顯示完整 mode 清單。
3. Mode 切換不影響 audit 之前的紀錄；切換後產生新 PricingResult。

---

## A.13 Self-validation Framework（自幹版專屬）

沒有獨立 Quant team 的代價，是必須把驗證流程**系統化**到 Trader 自己跑得起來。以下是 MVP 必須建立的四道自我檢驗線：

---

### A.13.1 Closed-form vs Bump-and-revalue Consistency

- 適用：歐式 Black-76 option。
- 規則：closed-form Greeks 與 bump-and-revalue Greeks 差異 < 5%。
- 違反：顯示 warning，禁止 Convert to Deal（Phase 2）。

---

### A.13.2 Put-Call Parity Check

- 適用：歐式 price-based 與 yield-based option。

Price-based parity：

```text
C - P = DF × (F - K)
```

Yield-based parity（MODE_A 路徑）：

```text
C_yield - P_yield = DF × (YF - YK) × 10000 × DV01_expiry × N / 100
```

- 差異 > 0.1% per 100 face → 顯示 critical warning。

---

### A.13.3 American ≥ European Lower Bound

- 適用：所有 American option。
- 規則：American PV ≥ 同條件 European PV。
- 違反：顯示 critical error，pricing blocked，要求升 tree step。

---

### A.13.4 Bloomberg / Vendor Benchmark

- 適用：每個新 underlying bond 第一次入庫、每次 mode switch 變更、每月一次 sample check。
- 流程：
  1. 取 Bloomberg OVME 或 vendor 對價結果。
  2. 用相同 market data 在系統內重跑。
  3. 差異 < 2%（per 100 face）= pass；2%–5% = warning；> 5% = fail，需查因。
- Bloomberg / vendor 二者擇一作為 official benchmark（§20）。

---

### A.13.5 Self-validation 報表

- MVP 必做：每次 pricing run，A.13.1 與 A.13.2 自動執行並顯示在 report。
- Phase 2 補：A.13.3 跨入 American、A.13.4 Bloomberg 對價工具整合到 UAT module。

---

## A.14 變更管理

- 本附錄為「凍結」狀態，任何變更需依以下流程：
  1. Trader 提出變更建議（含原因、影響範圍、預期影響的 underlying 類型）。
  2. Trading Desk Lead 簽核。
  3. 重跑 §A.13 全部四道檢驗，存檔。
  4. 寫入 Model Version Control（§21），舊版 Quote / Deal / Valuation 不可被覆蓋。
  5. 在系統內以「新 model version」上線，並通知所有 Trader。

---

## A.15 簽核欄位

| 簽核角色 | 簽名 | 日期 |
|---|---|---|
| Trading Desk Lead | ____________ | ______ |
| Risk Owner（如有） | ____________ | ______ |
| Product Owner | ____________ | ______ |
| IT Architecture Owner | ____________ | ______ |

---
