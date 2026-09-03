// Futures Yield view (Issue #190) -- Treasury futures <-> CTD implied yield.
//
// A fifth self-contained IIFE, sharing no state with the Pricing, Markets or
// Capture modules and touching nothing any of them owns.
//
// **This file performs no bond mathematics whatsoever.** It does not parse a
// futures quote, apply a conversion factor, build a coupon schedule, accrue
// interest, discount a cashflow, solve a yield, or round a price to a tick.
// Every one of those is `pricing/treasury_futures_implied_yield` and
// `pricing/treasury_futures_contract`, reached through
// POST /api/treasury-futures/convert, and rendered here exactly as received.
// That is the whole point of Issue #190's canonical-path requirement: PR #9
// re-implemented the entire bond pricer in browser JavaScript, so the page
// and Python could silently disagree. Even the tick size and the legal
// sub-32nd digits shown next to the input come from
// GET /api/treasury-futures/contracts rather than a constant in this file.
//
// The CTD source status is rendered from the server's own
// `is_confirmed_source` flag, never inferred here, and an unconfirmed source
// is always visible next to the answer it produced.
(function () {
  "use strict";

  const navFuturesYield = document.getElementById("nav-futures-yield");
  const viewFuturesYield = document.getElementById("view-futures-yield");
  if (!navFuturesYield || !viewFuturesYield) return; // view not present on this page

  const els = {
    sourcePill: document.getElementById("fy-source-pill"),
    errorSection: document.getElementById("fy-error"),
    errorDetail: document.getElementById("fy-error-detail"),
    contractSelect: document.getElementById("fy-contract-select"),
    ctdSummary: document.getElementById("fy-ctd-summary"),
    tickSummary: document.getElementById("fy-tick-summary"),
    loadBloombergBtn: document.getElementById("fy-load-bloomberg-btn"),
    automaticNote: document.getElementById("fy-automatic-note"),
    contractSymbol: document.getElementById("fy-contract-symbol"),
    ctdIdentifier: document.getElementById("fy-ctd-identifier"),
    ctdCoupon: document.getElementById("fy-ctd-coupon"),
    ctdMaturity: document.getElementById("fy-ctd-maturity"),
    conversionFactor: document.getElementById("fy-conversion-factor"),
    lastDelivery: document.getElementById("fy-last-delivery"),
    asOf: document.getElementById("fy-as-of"),
    futuresPrice: document.getElementById("fy-futures-price"),
    targetYield: document.getElementById("fy-target-yield"),
    convertBtn: document.getElementById("fy-convert-btn"),
    impliedYield: document.getElementById("fy-implied-yield"),
    impliedYieldNote: document.getElementById("fy-implied-yield-note"),
    futuresPriceOut: document.getElementById("fy-futures-price-out"),
    futuresPriceNote: document.getElementById("fy-futures-price-note"),
    detailCtd: document.getElementById("fy-detail-ctd"),
    detailCoupon: document.getElementById("fy-detail-coupon"),
    detailMaturity: document.getElementById("fy-detail-maturity"),
    detailCf: document.getElementById("fy-detail-cf"),
    detailDelivery: document.getElementById("fy-detail-delivery"),
    detailTick: document.getElementById("fy-detail-tick"),
    detailSource: document.getElementById("fy-detail-source"),
    detailAsOf: document.getElementById("fy-detail-as-of"),
  };

  const NBSP_DASH = "—";

  // Deliberately explicit, never a generic string-humanizer: these are the
  // only two source values the server sends, so the label is a reviewed
  // mapping. An unrecognized value falls back to the raw string, verbatim.
  const SOURCE_LABELS = {
    BLOOMBERG_DAPI: "Bloomberg DAPI",
    MANUAL_UNCONFIRMED: "Manual — unconfirmed",
  };

  let contracts = [];
  let contractsLoaded = false;

  // Which source the *next* conversion should use. A confirmed provenance
  // is never asserted by this page: the server re-fetches the CTD itself in
  // BLOOMBERG mode, because everything loaded here lands in editable fields
  // and could have been changed since. This flag only says which of the two
  // the server should do, and any edit to a CTD field or the contract drops
  // it back to MANUAL.
  let ctdSourceMode = "MANUAL";

  // Request-identity fence (Codex review, PR #191). Clearing the DOM does not
  // cancel a request already awaiting a response: without this, a conversion
  // started for ZN could resolve *after* the trader switched to ZB and
  // repaint ZN's yield, CTD and conversion factor beside ZB's inputs -- a
  // stale answer that looks freshly computed, which is the one failure this
  // panel must not have. Every action that invalidates what is on screen
  // bumps this token, and every awaited continuation drops its result unless
  // the token is still the one it started with. Same idea as the Pricing
  // module's own `conventionProfileGeneration` fence.
  let requestGeneration = 0;

  function beginRequest() {
    requestGeneration += 1;
    return requestGeneration;
  }

  function isCurrentRequest(generation) {
    return generation === requestGeneration;
  }

  function contractByCode(code) {
    return contracts.find((contract) => contract.code === code) || null;
  }

  function selectedContract() {
    return contractByCode(els.contractSelect.value);
  }

  function showError(detail) {
    els.errorDetail.textContent = detail;
    els.errorSection.hidden = false;
  }

  function clearError() {
    els.errorSection.hidden = true;
  }

  function renderTickSummary() {
    const contract = selectedContract();
    if (!contract) {
      els.tickSummary.textContent = NBSP_DASH;
      els.detailTick.textContent = NBSP_DASH;
      return;
    }
    const digits = contract.sub_32nd_digits.join(", ");
    // The label is the server's, not derived from minimum_tick here: this
    // module does no arithmetic at all, display arithmetic included.
    const tick = contract.minimum_tick_label;
    els.tickSummary.textContent = `${contract.code} tick ${tick} — sub-32nd digits ${digits}`;
    els.detailTick.textContent = `${tick} (${contract.minimum_tick})`;
  }

  function renderSourceStatus(ctd) {
    if (!ctd) {
      els.sourcePill.textContent = "No CTD loaded";
      els.sourcePill.classList.remove("is-unconfirmed", "is-confirmed");
      return;
    }
    const label = SOURCE_LABELS[ctd.source] || ctd.source;
    els.sourcePill.textContent = ctd.is_confirmed_source
      ? `${label} — confirmed`
      : `${label} — NOT confirmed current market data`;
    els.sourcePill.classList.toggle("is-unconfirmed", !ctd.is_confirmed_source);
    els.sourcePill.classList.toggle("is-confirmed", Boolean(ctd.is_confirmed_source));
  }

  const CTD_DETAIL_ELEMENT_KEYS = [
    "detailCtd",
    "detailCoupon",
    "detailMaturity",
    "detailCf",
    "detailDelivery",
    "detailSource",
    "detailAsOf",
  ];

  function renderCtdDetail(ctd) {
    renderSourceStatus(ctd);
    if (!ctd) {
      els.ctdSummary.textContent = NBSP_DASH;
      CTD_DETAIL_ELEMENT_KEYS.forEach((key) => {
        els[key].textContent = NBSP_DASH;
      });
      return;
    }
    els.ctdSummary.textContent = ctd.ctd_description
      ? `${ctd.contract_symbol} — ${ctd.ctd_description} (${ctd.ctd_identifier})`
      : `${ctd.contract_symbol} — CTD ${ctd.ctd_identifier}`;
    els.detailCtd.textContent = ctd.ctd_identifier;
    els.detailCoupon.textContent = `${ctd.ctd_coupon_percent}%`;
    els.detailMaturity.textContent = ctd.ctd_maturity_date;
    els.detailCf.textContent = String(ctd.conversion_factor);
    els.detailDelivery.textContent = ctd.last_delivery_date;
    els.detailSource.textContent = SOURCE_LABELS[ctd.source] || ctd.source;
    els.detailAsOf.textContent = ctd.as_of;
  }

  function fillCtdFields(ctd) {
    els.contractSymbol.value = ctd.contract_symbol || "";
    els.ctdIdentifier.value = ctd.ctd_identifier || "";
    els.ctdCoupon.value = ctd.ctd_coupon_percent == null ? "" : String(ctd.ctd_coupon_percent);
    els.ctdMaturity.value = ctd.ctd_maturity_date || "";
    els.conversionFactor.value =
      ctd.conversion_factor == null ? "" : String(ctd.conversion_factor);
    els.lastDelivery.value = ctd.last_delivery_date || "";
    els.asOf.value = ctd.as_of || "";
  }

  // A typed value that is not a number is forwarded as the text it is, not
  // as `NaN` -- `JSON.stringify(NaN)` is `null`, which the server would read
  // as "this field was left blank" and report as missing rather than as the
  // typo it is.
  function numberOrRaw(text) {
    if (text === "") return null;
    const parsed = Number(text);
    return Number.isFinite(parsed) ? parsed : text;
  }

  // Sent verbatim. A blank field stays blank rather than becoming 0 or
  // today's date: the server rejects an incomplete CTD, and a fabricated
  // default is exactly the silent-wrong-answer this utility must not give.
  function ctdRequestPayload() {
    const contract = selectedContract();
    return {
      contract_code: contract ? contract.code : null,
      contract_symbol: els.contractSymbol.value.trim() || null,
      ctd_identifier: els.ctdIdentifier.value.trim() || null,
      ctd_coupon_percent: numberOrRaw(els.ctdCoupon.value.trim()),
      ctd_maturity_date: els.ctdMaturity.value || null,
      conversion_factor: numberOrRaw(els.conversionFactor.value.trim()),
      last_delivery_date: els.lastDelivery.value || null,
      as_of: els.asOf.value.trim() || null,
    };
  }

  async function postJson(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch (parseError) {
      throw new Error(`${path} returned a non-JSON response (HTTP ${response.status})`);
    }
    if (!response.ok) {
      throw new Error((payload && payload.error) || `HTTP ${response.status}`);
    }
    return payload;
  }

  async function loadContracts() {
    if (contractsLoaded) return;
    const response = await fetch("/api/treasury-futures/contracts");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (!payload || !Array.isArray(payload.contracts) || payload.contracts.length === 0) {
      throw new Error('malformed response: expected a non-empty "contracts" array');
    }
    contracts = payload.contracts;
    contractsLoaded = true;
    els.contractSelect.innerHTML = "";
    contracts.forEach((contract) => {
      const option = document.createElement("option");
      option.value = contract.code;
      option.textContent = `${contract.code} — ${contract.name}`;
      els.contractSelect.appendChild(option);
    });
    renderTickSummary();
  }

  async function loadBloombergCtd() {
    const contract = selectedContract();
    if (!contract) return;
    const generation = beginRequest();
    clearError();
    els.automaticNote.hidden = true;
    try {
      const payload = await postJson("/api/treasury-futures/ctd", {
        contract_code: contract.code,
      });
      if (!isCurrentRequest(generation)) return;
      // A successful load replaces every CTD input, and a programmatic
      // `value =` assignment does not fire the `input`/`change` listeners
      // that would otherwise invalidate the answers -- so the answers are
      // cleared here explicitly (Codex review, PR #191). Deliberately only
      // on this branch: a *failed* load leaves the CTD fields exactly as the
      // trader left them, so whatever is on screen is still the answer to
      // the inputs beside it, and clearing it would destroy a valid result
      // for nothing.
      clearAnswers();
      fillCtdFields(payload);
      renderCtdDetail(payload);
      // The server just fetched this; the next Convert may ask it to fetch
      // again rather than send these values back as operator input.
      ctdSourceMode = "BLOOMBERG";
    } catch (error) {
      // The automatic path being unavailable is an answer, not a crash: show
      // exactly what the server said is missing and leave the manual fields
      // usable. Still fenced: this note names a contract, and a note about
      // the contract the trader has already navigated away from is wrong.
      if (!isCurrentRequest(generation)) return;
      els.automaticNote.textContent = error.message;
      els.automaticNote.hidden = false;
    }
  }

  function renderImpliedYield(payload) {
      if (!payload.implied_yield) {
        els.impliedYield.textContent = NBSP_DASH;
        els.impliedYieldNote.textContent = payload.implied_yield_error || NBSP_DASH;
        return;
      }
      const result = payload.implied_yield;
      els.impliedYield.textContent = `${result.implied_yield_percent.toFixed(4)}%`;
      // The priced decimal is the exact value the calculation used.
      // The exchange quote is the nearest tradable price for this contract.
      // They are equal only when on-tick.
      const offTick = result.on_tick
        ? ""
        : ` — off-tick (entered ${result.futures_price}, nearest ${result.exchange_quote})`;
      els.impliedYieldNote.textContent =
        `entered ${result.futures_price} → CTD clean ` +
        `${result.converted_clean_price.toFixed(6)}, accrued ` +
        `${result.accrued_interest.toFixed(6)}, settled ${result.settlement_date}${offTick}`;
    }

  function renderFuturesPrice(payload) {
      if (!payload.futures_price) {
        els.futuresPriceOut.textContent = NBSP_DASH;
        els.futuresPriceNote.textContent = payload.futures_price_error || NBSP_DASH;
        return;
      }
      const result = payload.futures_price;
      els.futuresPriceOut.textContent = result.exchange_quote;
      // The priced decimal is the exact value the calculation used.
      // The exchange quote is the nearest tradable price for this contract.
      // They are equal only when on-tick.
      const offTick = result.on_tick
        ? ""
        : ` — off-tick (target yield implies ${result.futures_price}, nearest ${result.exchange_quote})`;
      els.futuresPriceNote.textContent =
        `target yield implies ${result.futures_price} → CTD clean ` +
        `${result.converted_clean_price.toFixed(6)}, min tick ${result.minimum_tick_label}, ` +
        `settled ${result.settlement_date}${offTick}`;
    }

  function clearAnswers() {
    // A stale answer next to an error banner is the one genuinely dangerous
    // state this panel can be in: the number would still look current. Both
    // answers are cleared before every attempt, successful or not.
    els.impliedYield.textContent = NBSP_DASH;
    els.impliedYieldNote.textContent = NBSP_DASH;
    els.futuresPriceOut.textContent = NBSP_DASH;
    els.futuresPriceNote.textContent = NBSP_DASH;
  }

  async function convert() {
    // Starting a conversion also supersedes any earlier one still in flight,
    // so two rapid clicks can never race each other onto the screen.
    const generation = beginRequest();
    clearError();
    clearAnswers();
    const futuresPrice = els.futuresPrice.value.trim();
    const targetYield = els.targetYield.value.trim();
    if (!futuresPrice && !targetYield) {
      showError("Enter a futures price, a target yield, or both.");
      return;
    }
    try {
      const contract = selectedContract();
      const payload = await postJson("/api/treasury-futures/convert", {
        ctd_source: ctdSourceMode,
        contract_code: contract ? contract.code : null,
        ctd: ctdRequestPayload(),
        futures_price: futuresPrice || null,
        // Sent as typed. `Number("abc")` is NaN, and JSON.stringify turns
        // that into null -- which the server would read as "no target yield"
        // and answer nothing, instead of telling the trader what is wrong.
        target_yield_percent: targetYield || null,
      });
      if (!isCurrentRequest(generation)) return;
      // A BLOOMBERG-sourced conversion re-fetches the CTD server-side, and the
      // delivery month or the CTD itself can have rolled since Load. Refresh
      // the editable fields from what was actually priced, so the form and the
      // answer can never describe different records -- otherwise editing one
      // field would drop to MANUAL and submit the *stale* record plus the edit
      // (Codex review, PR #191). Only in BLOOMBERG mode: in MANUAL mode these
      // fields are the trader's own input and must never be overwritten.
      if (payload.ctd && payload.ctd.is_confirmed_source) fillCtdFields(payload.ctd);
      renderCtdDetail(payload.ctd);
      renderImpliedYield(payload);
      renderFuturesPrice(payload);
    } catch (error) {
      if (!isCurrentRequest(generation)) return;
      showError(error.message);
    }
  }

  const CTD_FIELD_KEYS = [
    "contractSymbol",
    "ctdIdentifier",
    "ctdCoupon",
    "ctdMaturity",
    "conversionFactor",
    "lastDelivery",
    "asOf",
  ];

  function clearCtdFields() {
    CTD_FIELD_KEYS.forEach((key) => {
      els[key].value = "";
    });
    renderCtdDetail(null);
  }

  // The invariant, stated once so a future edit reasons from it rather than
  // from a list of cases (Codex review, PR #191): **every element showing a
  // result of the last submitted request is cleared as soon as any input
  // that fed that request changes.** A number a trader can read off next to
  // inputs that did not produce it is the one failure this panel must not
  // have, and "result" means the CTD small print and source pill just as
  // much as the two headline answers -- both are rendered from the response.
  //
  // Which inputs feed which result is what makes the two lists differ:
  //
  // - the answers depend on the futures price, the target yield, every CTD
  //   field and the contract;
  // - the CTD small print, summary and source pill depend on the CTD fields
  //   and the contract, but *not* on the price or the target yield, so
  //   retyping a price must not blank the CTD that is still perfectly
  //   current;
  // - the tick readout depends on the contract alone and is re-rendered by
  //   `renderTickSummary`, so it is deliberately not cleared here.
  const CTD_INPUT_KEYS = CTD_FIELD_KEYS;
  const ANSWER_ONLY_INPUT_KEYS = ["futuresPrice", "targetYield"];

  function invalidateOnInput(key, alsoClearCtdDetail) {
    // Both events: a text field reports "input" per keystroke, while a date
    // field picked from the browser's own calendar widget can report only
    // "change" -- and the CTD maturity and delivery dates are date fields.
    ["input", "change"].forEach((eventName) => {
      els[key].addEventListener(eventName, () => {
        beginRequest();
        clearAnswers();
        clearError();
        if (alsoClearCtdDetail) {
          renderCtdDetail(null);
          // Edited by hand, so it is operator input now whatever it was.
          ctdSourceMode = "MANUAL";
        }
      });
    });
  }

  CTD_INPUT_KEYS.forEach((key) => invalidateOnInput(key, true));
  ANSWER_ONLY_INPUT_KEYS.forEach((key) => invalidateOnInput(key, false));

  // Changing the contract is not an edit, it is a different instrument. The
  // CTD fields belong to the contract they were entered or loaded for, and
  // `contract_code` is taken from this selector -- so leaving them behind
  // would submit, say, ZN's CTD and conversion factor as ZB, and format the
  // answer on ZB's tick (Codex review, PR #191). They are cleared outright
  // rather than carried over and validated: there is nothing to validate a
  // vendor contract symbol against, and a half-migrated CTD is exactly the
  // silent-wrong-answer this utility must not give.
  els.contractSelect.addEventListener("change", () => {
    beginRequest();
    ctdSourceMode = "MANUAL";
    renderTickSummary();
    clearCtdFields();
    clearAnswers();
    clearError();
    els.automaticNote.hidden = true;
  });
  els.loadBloombergBtn.addEventListener("click", () => loadBloombergCtd());
  els.convertBtn.addEventListener("click", () => convert());

  document.addEventListener("shiori:viewchange", (event) => {
    if (!event.detail || event.detail.view !== "futures-yield") return;
    loadContracts().catch((error) => {
      showError(`Unable to load the supported contracts: ${error.message}`);
    });
  });

  // Read-only accessors for the browser tests, mirroring the pattern the
  // Pricing module already uses. No production behavior depends on them.
  window.__shioriTestFuturesYieldContracts = () => contracts;
  window.__shioriTestFuturesYieldRequestGeneration = () => requestGeneration;
  window.__shioriTestFuturesYieldCtdSourceMode = () => ctdSourceMode;
})();
