// Manual functional prototype wiring (PR #136). This file performs no
// pricing, discounting, accrual, scaling, or Greek math of any kind -- it
// only reads/writes the six approved form fields, calls the local HTTP
// bridge (see src/shiori_pricing_lab/app/standalone_option_workbench_server.py),
// and renders the returned display dict verbatim. Every numeric value shown
// is formatted with toFixed(6), the same display precision the existing
// Streamlit workbench uses -- never rounded, rescaled, or re-signed.

(function () {
  "use strict";

  const els = {
    optionTypeToggle: document.getElementById("option-type-toggle"),
    positionToggle: document.getElementById("position-toggle"),
    strikePrice: document.getElementById("strike-price-input"),
    notional: document.getElementById("notional-input"),
    volatility: document.getElementById("volatility-input"),
    forwardPrice: document.getElementById("forward-price-input"),
    priceBtn: document.getElementById("price-btn"),
    clearBtn: document.getElementById("clear-btn"),
    statusIndicator: document.getElementById("status-indicator"),
    statusText: document.getElementById("status-text"),
    errorBanner: document.getElementById("pricing-error-banner"),
    priceTotal: document.getElementById("price-total"),
    priceTotalCcy: document.getElementById("price-total-ccy"),
    pricePer100: document.getElementById("price-per-100"),
    resultCurrency: document.getElementById("result-currency"),
    greekDelta: document.getElementById("greek-delta"),
    greekGamma: document.getElementById("greek-gamma"),
    greekVega: document.getElementById("greek-vega"),
    greekTheta: document.getElementById("greek-theta"),
    instrTitle: document.getElementById("instr-title"),
    instrIsin: document.getElementById("instr-isin"),
    quoteSideBadge: document.getElementById("quote-side-badge"),
    statCleanPrice: document.getElementById("stat-clean-price"),
    statYieldMid: document.getElementById("stat-yield-mid"),
    statValuationDate: document.getElementById("stat-valuation-date"),
    statOptionSettlement: document.getElementById("stat-option-settlement"),
    optionTermsExpiry: document.getElementById("option-terms-expiry"),
    forwardSettlementNote: document.getElementById("forward-settlement-note"),
    snapshotCleanPrice: document.getElementById("snapshot-clean-price"),
    snapshotYield: document.getElementById("snapshot-yield"),
    snapshotAccruedInterest: document.getElementById("snapshot-accrued-interest"),
    detailsIssuer: document.getElementById("details-issuer"),
    detailsCoupon: document.getElementById("details-coupon"),
    detailsMaturity: document.getElementById("details-maturity"),
    detailsDayCount: document.getElementById("details-day-count"),
    detailsFrequency: document.getElementById("details-frequency"),
    detailsCurrency: document.getElementById("details-currency"),
    sidebarAsOf: document.getElementById("sidebar-as-of-timestamp"),
  };

  let baseOverlay = null;
  let baseDisplay = null;

  function fmt(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      return "—"; // em dash: never a fabricated zero
    }
    return value.toFixed(6);
  }

  // Displays a raw, dimensionless coupon fraction (e.g. 0.0325) as a
  // percentage (3.250%). This is the one display-only arithmetic transform
  // this file applies anywhere: value * 100, deterministic, no market
  // assumption or model involved -- showing the raw fraction unlabeled
  // would misrepresent a 3.25% coupon as an apparent 0.0325% one.
  function fmtCouponPercent(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      return "Not available";
    }
    return (value * 100).toFixed(3) + "%";
  }

  function naIfNull(value) {
    return value === null || value === undefined ? "Not available" : String(value);
  }

  function setTextOrNotAvailable(el, value) {
    const text = naIfNull(value);
    el.textContent = text;
    el.classList.toggle("pending-value", text === "Not available");
  }

  // Renders the bounded, read-only context dict verbatim (see
  // standalone_option_workbench_context.py) -- every value here is exactly
  // as it appears in examples/standalone_option_case.json, with the single
  // coupon percent transform above. Called once, right after /api/base
  // resolves: none of the six overlay fields change the underlying
  // instrument's identity, so this never needs to be re-rendered by Price
  // or Clear.
  function renderContext(context) {
    els.instrTitle.textContent = context.issuer;
    els.instrIsin.textContent = context.underlying_isin;
    els.quoteSideBadge.textContent = context.quote_side;

    setTextOrNotAvailable(els.statCleanPrice, context.clean_price_per_100);
    setTextOrNotAvailable(els.statYieldMid, context.yield_value);
    els.statValuationDate.textContent = context.valuation_date;
    els.statOptionSettlement.textContent = context.option_settlement_date;

    els.optionTermsExpiry.textContent = context.expiry_date;
    els.forwardSettlementNote.textContent =
      "Forward settlement: " + context.forward_settlement_date;

    setTextOrNotAvailable(els.snapshotCleanPrice, context.clean_price_per_100);
    setTextOrNotAvailable(els.snapshotYield, context.yield_value);
    setTextOrNotAvailable(els.snapshotAccruedInterest, context.accrued_interest_per_100);

    els.detailsIssuer.textContent = context.issuer;
    els.detailsCoupon.textContent = fmtCouponPercent(context.coupon);
    els.detailsMaturity.textContent = context.maturity_date;
    els.detailsDayCount.textContent = context.day_count;
    els.detailsFrequency.textContent = context.coupon_frequency;
    els.detailsCurrency.textContent = context.currency;

    els.sidebarAsOf.textContent = context.as_of_timestamp;
  }

  function setToggle(toggleEl, value) {
    toggleEl.querySelectorAll(".opt").forEach((opt) => {
      opt.classList.toggle("on", opt.dataset.value === value);
    });
  }

  function getToggleValue(toggleEl) {
    return toggleEl.querySelector(".opt.on").dataset.value;
  }

  function setFormFromOverlay(overlay) {
    setToggle(els.optionTypeToggle, overlay.option_type);
    setToggle(els.positionToggle, overlay.position);
    els.strikePrice.value = overlay.strike_price;
    els.notional.value = overlay.notional;
    els.volatility.value = overlay.volatility;
    els.forwardPrice.value = overlay.forward_clean_price_per_100;
  }

  function readOverlayFromForm() {
    return {
      option_type: getToggleValue(els.optionTypeToggle),
      position: getToggleValue(els.positionToggle),
      strike_price: Number(els.strikePrice.value),
      notional: Number(els.notional.value),
      volatility: Number(els.volatility.value),
      forward_clean_price_per_100: Number(els.forwardPrice.value),
    };
  }

  function renderDisplay(display) {
    if (display.status === "FAILED") {
      els.priceTotal.textContent = "—";
      els.priceTotalCcy.textContent = "";
      els.pricePer100.textContent = "—";
      els.resultCurrency.textContent = "—";
      els.greekDelta.textContent = "—";
      els.greekGamma.textContent = "—";
      els.greekVega.textContent = "—";
      els.greekTheta.textContent = "—";

      const messages = (display.errors || [])
        .map((e) => `${e.code}: ${e.message}`)
        .join(" | ");
      els.errorBanner.textContent = messages || "Pricing failed.";
      els.errorBanner.hidden = false;

      els.statusIndicator.classList.add("failed");
      els.statusText.textContent = "Pricing failed";
      return;
    }

    els.errorBanner.hidden = true;
    els.errorBanner.textContent = "";
    els.statusIndicator.classList.remove("failed");
    els.statusText.textContent = "Local synthetic case loaded";

    els.priceTotal.textContent = fmt(display.total_notional_model_fair_premium);
    els.priceTotalCcy.textContent = display.result_currency || "";
    els.pricePer100.textContent = fmt(display.model_fair_premium_per_100);
    els.resultCurrency.textContent = display.result_currency || "—";
    els.greekDelta.textContent = fmt(display.forward_price_delta_per_100);
    els.greekGamma.textContent = fmt(display.forward_price_gamma_per_100);
    els.greekVega.textContent = fmt(display.vega_per_vol_point_per_100);
    els.greekTheta.textContent = fmt(display.theta_per_calendar_day_per_100);
  }

  function showFetchError(message) {
    els.errorBanner.textContent = message;
    els.errorBanner.hidden = false;
    els.statusIndicator.classList.add("failed");
    els.statusText.textContent = "Request failed";
  }

  async function loadBase() {
    const response = await fetch("/api/base");
    const payload = await response.json();
    if (!response.ok) {
      showFetchError(payload.error || "Failed to load base case.");
      return;
    }
    baseOverlay = payload.overlay;
    baseDisplay = payload.display;
    renderContext(payload.context);
    setFormFromOverlay(baseOverlay);
    renderDisplay(baseDisplay);
  }

  async function priceCurrentForm() {
    const overlay = readOverlayFromForm();
    const response = await fetch("/api/price", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(overlay),
    });
    const payload = await response.json();
    if (!response.ok) {
      showFetchError(payload.error || "Pricing request failed.");
      return;
    }
    renderDisplay(payload);
  }

  function clearToBase() {
    if (!baseOverlay || !baseDisplay) {
      return;
    }
    setFormFromOverlay(baseOverlay);
    renderDisplay(baseDisplay);
  }

  els.optionTypeToggle.addEventListener("click", (event) => {
    const opt = event.target.closest(".opt");
    if (opt) {
      setToggle(els.optionTypeToggle, opt.dataset.value);
    }
  });
  els.positionToggle.addEventListener("click", (event) => {
    const opt = event.target.closest(".opt");
    if (opt) {
      setToggle(els.positionToggle, opt.dataset.value);
    }
  });

  els.priceBtn.addEventListener("click", priceCurrentForm);
  els.clearBtn.addEventListener("click", clearToBase);

  loadBase();
})();
