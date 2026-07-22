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
  };

  let baseOverlay = null;
  let baseDisplay = null;

  function fmt(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      return "—"; // em dash: never a fabricated zero
    }
    return value.toFixed(6);
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
