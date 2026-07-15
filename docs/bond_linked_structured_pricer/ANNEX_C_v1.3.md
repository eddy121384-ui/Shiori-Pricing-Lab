---
title: Annex C v1.3 - UI/UX 與品牌視覺指引
version: 1.3
source: GPT direct markdown output（非 PDF 反向解析）
status: future-state reference only; does not authorize implementation
---

> **Implementation authority notice:** This document is future-state
> reference material. It does not authorize implementation on its own. See
> `AGENTS.md` for the current implementation authority order.

# Annex C：UI/UX 與品牌視覺指引

本附錄為未來 UI/UX 與品牌視覺指引。  
MVP 僅需 header 放置單一 logo png，不要求完整導入。

---

## C.1 MVP Logo 使用

MVP 規則：

- Header 左上放置 logo png。
- 高度約 32–40px。
- 不放大型背景圖。
- 不遮擋 pricing fields、market data、warning、error。
- Internal Pricing Report 可放小型 header logo。
- Client-facing Termsheet 可放 header logo。

---

## C.2 Future Desktop UI

Phase 3 可擴充：

- Dashboard brand area。
- Empty state illustration。
- Report cover visual。
- Product detail page visual anchor。

---

## C.3 Pricing / Risk Table 禁止項

以下區域不應放置背景圖或強品牌視覺：

- Pricing input fields
- Market data table
- Override warning
- Error message
- Greeks table
- Scenario result
- Portfolio risk table
- Hard limit / breach message
- Convert to Deal button

---

## C.4 Responsive Rule

| Device | 指引 |
|---|---|
| Desktop | Header logo 32–40px |
| Tablet | Logo 縮至 top navigation |
| Mobile | Compact header icon |
| PDF | Header logo，避免壓縮商品條件與數字 |

---

## C.5 Future Brand Tree Guidance

Phase 3 若導入品牌大樹圖像：

- 可作為頁首識別。
- 可作為空狀態淡色插圖。
- 可作為 client-facing PDF 封面視覺。
- 不可影響資料可讀性。
- 不可影響權限、錯誤、警示與交易按鈕辨識。
