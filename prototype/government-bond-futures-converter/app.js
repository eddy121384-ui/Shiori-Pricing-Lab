// Government Bond Futures Converter -- app shell front end (Issue #206).
//
// **This file performs no bond mathematics whatsoever.** It does not parse a
// futures quote, apply a conversion factor, build a coupon schedule, accrue
// interest, discount a cashflow, solve a yield, or round a price to a tick.
// Every one of those lives in `pricing/treasury_futures_implied_yield` and
// `pricing/treasury_futures_contract`, reached through
// POST /api/treasury-futures/convert and rendered here exactly as received.
// Even the tick size, the legal sub-32nd digits and each market's methodology
// sentence come from GET /api/treasury-futures/contracts rather than a
// constant in this file, so the window and Python can never disagree about
// what a contract trades in. This is the same canonical-path discipline the
// Workbench's `treasury_futures_yield.js` is held to, and the same test
// enforces it here.
//
// What this file does own, and the Workbench module does not, is the desk
// app's flow: the contract list loads at startup, picking a contract fetches
// its Bloomberg CTD automatically, and "Reload Bloomberg CTD" is a retry, not
// a step the trader has to remember.
(function () {
  "use strict";

  const el = (id) => document.getElementById(id);

  const els = {
    bloombergPill: el("bloomberg-pill"),
    bloombergPillText: el("bloomberg-pill-text"),
    errorBanner: el("error-banner"),
    errorTitle: el("error-title"),
    errorDetail: el("error-detail"),
    retryBtn: el("retry-btn"),
    contractSelect: el("contract-select"),
    loadCtdBtn: el("load-ctd-btn"),
    methodology: el("methodology"),
    tickSummary: el("tick-summary"),
    convertSummary: el("convert-summary"),
    futuresPrice: el("futures-price"),
    targetYield: el("target-yield"),
    convertBtn: el("convert-btn"),
    impliedYield: el("implied-yield"),
    impliedYieldNote: el("implied-yield-note"),
    futuresPriceOut: el("futures-price-out"),
    futuresPriceNote: el("futures-price-note"),
    ctdSummary: el("ctd-summary"),
    dCtd: el("d-ctd"),
    dIdentifier: el("d-identifier"),
    dCoupon: el("d-coupon"),
    dMaturity: el("d-maturity"),
    dCf: el("d-cf"),
    dDelivery: el("d-delivery"),
    dSource: el("d-source"),
    dAsOf: el("d-as-of"),
    advanced: el("advanced"),
    clearCtdBtn: el("clear-ctd-btn"),
  };

  const DASH = "—";

  // Deliberately explicit, never a generic string-humanizer: these are the
  // only two source values the server sends, so the label is a reviewed
  // mapping. An unrecognized value falls back to the raw string, verbatim.
  const SOURCE_LABELS = {
    BLOOMBERG_DAPI: "Bloomberg DAPI — confirmed live",
    MANUAL_UNCONFIRMED: "Manual — unconfirmed",
  };

  const MANUAL_FIELD_IDS = {
    contract_symbol: "m-contract-symbol",
    ctd_identifier: "m-ctd-identifier",
    ctd_coupon_percent: "m-ctd-coupon",
    ctd_maturity_date: "m-ctd-maturity",
    conversion_factor: "m-conversion-factor",
    last_delivery_date: "m-last-delivery",
    first_accrual_start: "m-first-accrual-start",
    first_coupon_date: "m-first-coupon-date",
    as_of: "m-as-of",
  };
  const MANUAL_FIELD_KEYS = Object.keys(MANUAL_FIELD_IDS);
  const manual = {};
  MANUAL_FIELD_KEYS.forEach((key) => {
    manual[key] = el(MANUAL_FIELD_IDS[key]);
  });

  let contracts = [];

  // Which source the *next* conversion should use. A confirmed provenance is
  // never asserted by this page: in BLOOMBERG mode the server re-fetches the
  // CTD itself, because everything loaded here lands in editable fields and
  // could have been changed since. Any human edit to a CTD field drops this
  // back to MANUAL.
  let ctdSourceMode = "MANUAL";

  // What Retry should do. Set by whichever step failed.
  let retryAction = null;

  // Request-identity fence: clearing the DOM does not cancel a request already
  // in flight. Without this, a conversion started for ZN could resolve *after*
  // the trader switched to FGBL and repaint ZN's yield and conversion factor
  // beside FGBL's inputs -- a stale answer that looks freshly computed, which
  // is the one failure this app must not have. Every action that invalidates
  // what is on screen bumps this token, and every awaited continuation drops
  // its result unless the token is still the one it started with.
  let requestGeneration = 0;
  const beginRequest = () => (requestGeneration += 1);
  const isCurrentRequest = (generation) => generation === requestGeneration;

  // ---------------------------------------------------------------- status

  function setBloombergStatus(state, text) {
    els.bloombergPill.classList.remove("is-ok", "is-warn", "is-error", "is-busy");
    if (state) els.bloombergPill.classList.add(state);
    els.bloombergPillText.textContent = text;
  }

  function showError(title, detail, retry) {
    els.errorTitle.textContent = title;
    els.errorDetail.textContent = detail;
    retryAction = retry || null;
    els.retryBtn.hidden = !retryAction;
    els.errorBanner.hidden = false;
  }

  function clearError() {
    els.errorBanner.hidden = true;
    retryAction = null;
  }

  // ---------------------------------------------------------------- render

  const contractByCode = (code) => contracts.find((c) => c.code === code) || null;
  const selectedContract = () => contractByCode(els.contractSelect.value);

  function renderContractDetail() {
    const contract = selectedContract();
    if (!contract) {
      els.tickSummary.textContent = DASH;
      els.convertSummary.textContent = DASH;
      return;
    }
    // Every label below is the server's own; this module does no arithmetic
    // at all, display arithmetic included.
    const tick = contract.minimum_tick_label;
    els.tickSummary.textContent =
      contract.quote_convention === "DECIMAL"
        ? `${contract.market_label} · tick ${tick} (decimal)`
        : `${contract.market_label} · tick ${tick} · sub-32nd digits ${contract.sub_32nd_digits.join(", ")}`;
    els.convertSummary.textContent = `${contract.code} · min tick ${tick}`;
    if (contract.methodology_note) els.methodology.textContent = contract.methodology_note;
    els.futuresPrice.placeholder =
      contract.quote_convention === "DECIMAL" ? "105.065" : "112-165 or 112.515625";
  }

  const CTD_DETAIL_KEYS = [
    "dCtd", "dIdentifier", "dCoupon", "dMaturity", "dCf", "dDelivery", "dSource", "dAsOf",
  ];

  function renderCtdDetail(ctd) {
    if (!ctd) {
      els.ctdSummary.textContent = DASH;
      CTD_DETAIL_KEYS.forEach((key) => {
        els[key].textContent = DASH;
      });
      return;
    }
    els.ctdSummary.textContent = ctd.ctd_description
      ? `${ctd.contract_symbol} · ${ctd.ctd_description}`
      : `${ctd.contract_symbol} · CTD ${ctd.ctd_identifier}`;
    els.dCtd.textContent = ctd.ctd_description || ctd.ctd_identifier;
    els.dIdentifier.textContent = ctd.ctd_identifier;
    els.dCoupon.textContent = `${ctd.ctd_coupon_percent}%`;
    els.dMaturity.textContent = ctd.ctd_maturity_date;
    els.dCf.textContent = String(ctd.conversion_factor);
    els.dDelivery.textContent = ctd.last_delivery_date;
    els.dSource.textContent = SOURCE_LABELS[ctd.source] || ctd.source;
    els.dAsOf.textContent = ctd.as_of;
    if (ctd.is_confirmed_source) {
      setBloombergStatus("is-ok", "Bloomberg connected");
    } else {
      setBloombergStatus("is-warn", "Manual CTD — not live market data");
    }
  }

  function fillCtdFields(ctd) {
    MANUAL_FIELD_KEYS.forEach((key) => {
      const value = ctd[key];
      manual[key].value = value === null || value === undefined ? "" : String(value);
    });
  }

  function clearCtdFields() {
    MANUAL_FIELD_KEYS.forEach((key) => {
      manual[key].value = "";
    });
  }

  function clearAnswers() {
    // A stale answer next to an error banner is the one genuinely dangerous
    // state this app can be in: the number would still look current. Both
    // answers are cleared before every attempt, successful or not.
    els.impliedYield.textContent = DASH;
    els.impliedYieldNote.textContent = DASH;
    els.futuresPriceOut.textContent = DASH;
    els.futuresPriceNote.textContent = DASH;
  }

  function renderImpliedYield(payload) {
    if (!payload.implied_yield) {
      els.impliedYield.textContent = DASH;
      els.impliedYieldNote.textContent = payload.implied_yield_error || DASH;
      return;
    }
    const r = payload.implied_yield;
    els.impliedYield.textContent = `${r.implied_yield_percent.toFixed(4)}%`;
    // The priced decimal is the exact value the calculation used; the exchange
    // quote is the nearest tradable price. They are equal only when on-tick.
    const offTick = r.on_tick
      ? ""
      : ` — off-tick (entered ${r.futures_price}, nearest ${r.exchange_quote})`;
    els.impliedYieldNote.textContent =
      `entered ${r.futures_price} → CTD clean ${r.converted_clean_price.toFixed(6)}, ` +
      `accrued ${r.accrued_interest.toFixed(6)}, settled ${r.settlement_date}${offTick}`;
  }

  function renderFuturesPrice(payload) {
    if (!payload.futures_price) {
      els.futuresPriceOut.textContent = DASH;
      els.futuresPriceNote.textContent = payload.futures_price_error || DASH;
      return;
    }
    const r = payload.futures_price;
    els.futuresPriceOut.textContent = r.exchange_quote;
    const offTick = r.on_tick
      ? ""
      : ` — off-tick (implied ${r.futures_price}, nearest ${r.exchange_quote})`;
    els.futuresPriceNote.textContent =
      `implied ${r.futures_price.toFixed(6)} → CTD clean ${r.converted_clean_price.toFixed(6)}, ` +
      `min tick ${r.minimum_tick_label}, settled ${r.settlement_date}${offTick}`;
  }

  // ------------------------------------------------------------ networking

  async function getJson(path) {
    const response = await fetch(path);
    let payload = null;
    try {
      payload = await response.json();
    } catch (parseError) {
      throw new Error(`${path} returned a non-JSON response (HTTP ${response.status})`);
    }
    if (!response.ok) throw new Error((payload && payload.error) || `HTTP ${response.status}`);
    return payload;
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
    if (!response.ok) throw new Error((payload && payload.error) || `HTTP ${response.status}`);
    return payload;
  }

  // A typed value that is not a number is forwarded as the text it is, not as
  // `NaN` -- `JSON.stringify(NaN)` is `null`, which the server would read as
  // "this field was left blank" and report as missing rather than as the typo
  // it is.
  function numberOrRaw(text) {
    if (text === "") return null;
    const parsed = Number(text);
    return Number.isFinite(parsed) ? parsed : text;
  }

  const NUMERIC_MANUAL_KEYS = ["ctd_coupon_percent", "conversion_factor"];

  // Sent verbatim. A blank field stays blank rather than becoming 0 or today's
  // date: the server rejects an incomplete CTD, and a fabricated default is
  // exactly the silent-wrong-answer this utility must not give.
  function manualCtdPayload() {
    const contract = selectedContract();
    const payload = { contract_code: contract ? contract.code : null };
    MANUAL_FIELD_KEYS.forEach((key) => {
      const text = manual[key].value.trim();
      payload[key] = NUMERIC_MANUAL_KEYS.includes(key) ? numberOrRaw(text) : text || null;
    });
    return payload;
  }

  // -------------------------------------------------------------- actions

  async function loadContracts() {
    const payload = await getJson("/api/treasury-futures/contracts");
    if (!payload || !Array.isArray(payload.contracts) || payload.contracts.length === 0) {
      throw new Error('malformed response: expected a non-empty "contracts" array');
    }
    contracts = payload.contracts;
    els.contractSelect.innerHTML = "";
    // Grouped by market, in registry order. The labels come from the server;
    // this only groups, it never names a market.
    const groups = new Map();
    contracts.forEach((contract) => {
      const label = contract.market_label || contract.market || "";
      if (!groups.has(label)) {
        const group = document.createElement("optgroup");
        group.label = label;
        groups.set(label, group);
        els.contractSelect.appendChild(group);
      }
      const option = document.createElement("option");
      option.value = contract.code;
      option.textContent = `${contract.code} — ${contract.name}`;
      groups.get(label).appendChild(option);
    });
    renderContractDetail();
  }

  async function loadBloombergCtd() {
    const contract = selectedContract();
    if (!contract) return;
    const generation = beginRequest();
    clearError();
    setBloombergStatus("is-busy", `Loading ${contract.code} CTD…`);
    els.loadCtdBtn.disabled = true;
    try {
      const payload = await postJson("/api/treasury-futures/ctd", {
        contract_code: contract.code,
      });
      if (!isCurrentRequest(generation)) return;
      // A successful load replaces every CTD field, and a programmatic
      // `value =` assignment does not fire the `input`/`change` listeners that
      // would otherwise invalidate the answers -- so they are cleared here
      // explicitly. Deliberately only on this branch: a *failed* load leaves
      // the fields exactly as the trader left them, so whatever is on screen
      // is still the answer to the inputs beside it.
      clearAnswers();
      fillCtdFields(payload);
      renderCtdDetail(payload);
      // The server just fetched this; the next Convert asks it to fetch again
      // rather than send these values back as operator input.
      ctdSourceMode = "BLOOMBERG";
    } catch (error) {
      if (!isCurrentRequest(generation)) return;
      // Bloomberg being unavailable is an answer, not a crash: show exactly
      // what the server said is missing, offer Retry, and leave the manual
      // fields usable underneath.
      ctdSourceMode = "MANUAL";
      setBloombergStatus("is-error", "Bloomberg unavailable");
      showError(
        `Could not load the ${contract.code} cheapest-to-deliver bond from Bloomberg`,
        error.message,
        loadBloombergCtd
      );
    } finally {
      if (isCurrentRequest(generation)) els.loadCtdBtn.disabled = false;
    }
  }

  async function convert() {
    // Starting a conversion supersedes any earlier one still in flight, so two
    // rapid clicks can never race each other onto the screen.
    const generation = beginRequest();
    clearError();
    clearAnswers();
    const futuresPrice = els.futuresPrice.value.trim();
    const targetYield = els.targetYield.value.trim();
    if (!futuresPrice && !targetYield) {
      showError("Nothing to convert", "Enter a futures price, a target yield, or both.", null);
      return;
    }
    const contract = selectedContract();
    els.convertBtn.disabled = true;
    try {
      const payload = await postJson("/api/treasury-futures/convert", {
        ctd_source: ctdSourceMode,
        contract_code: contract ? contract.code : null,
        ctd: manualCtdPayload(),
        futures_price: futuresPrice || null,
        // Sent as typed. `Number("abc")` is NaN and JSON.stringify turns that
        // into null -- which the server would read as "no target yield" and
        // answer nothing, instead of naming the typo.
        target_yield_percent: targetYield || null,
      });
      if (!isCurrentRequest(generation)) return;
      // A BLOOMBERG-sourced conversion re-fetches the CTD server-side, and the
      // delivery month or the CTD itself can have rolled since the load.
      // Refresh the fields from what was actually priced, so the form and the
      // answer can never describe different records.
      if (payload.ctd && payload.ctd.is_confirmed_source) fillCtdFields(payload.ctd);
      renderCtdDetail(payload.ctd);
      renderImpliedYield(payload);
      renderFuturesPrice(payload);
    } catch (error) {
      if (!isCurrentRequest(generation)) return;
      showError("Unable to convert", error.message, null);
    } finally {
      if (isCurrentRequest(generation)) els.convertBtn.disabled = false;
    }
  }

  // ---------------------------------------------------------------- wiring

  // The invariant, stated once so a future edit reasons from it rather than
  // from a list of cases: **every element showing a result of the last
  // submitted request is cleared as soon as any input that fed that request
  // changes.** A number a trader can read off next to inputs that did not
  // produce it is the one failure this app must not have -- and "result" means
  // the CTD readout and the source label just as much as the two headline
  // answers, since both are rendered from the response.
  //
  // The two lists differ because the inputs feeding them differ: the answers
  // depend on the price, the target yield, every CTD field and the contract;
  // the CTD readout depends on the CTD fields and the contract but not on the
  // price or the target yield, so retyping a price must not blank a CTD that
  // is still perfectly current.
  function invalidateOnInput(element, alsoClearCtdDetail) {
    // Both events: a text field reports "input" per keystroke, while a date
    // field picked from the browser's own calendar widget can report only
    // "change" -- and the maturity and delivery dates are date fields.
    ["input", "change"].forEach((eventName) => {
      element.addEventListener(eventName, () => {
        beginRequest();
        clearAnswers();
        clearError();
        if (alsoClearCtdDetail) {
          renderCtdDetail(null);
          // Edited by hand, so it is operator input now whatever its origin.
          ctdSourceMode = "MANUAL";
          setBloombergStatus("is-warn", "Manual CTD — not live market data");
        }
      });
    });
  }

  MANUAL_FIELD_KEYS.forEach((key) => invalidateOnInput(manual[key], true));
  [els.futuresPrice, els.targetYield].forEach((element) => invalidateOnInput(element, false));

  // The schedule belongs to the bond named by the identifier: retyping the
  // identifier orphans it, so it is cleared visibly in the form rather than
  // silently submitted against the new bond. Programmatic fills never fire
  // input/change, so a fresh Bloomberg load still lands intact.
  ["input", "change"].forEach((eventName) => {
    manual.ctd_identifier.addEventListener(eventName, () => {
      manual.first_accrual_start.value = "";
      manual.first_coupon_date.value = "";
    });
  });

  // Changing the contract is not an edit, it is a different instrument. The
  // CTD fields belong to the contract they were loaded for, and `contract_code`
  // is taken from this selector -- so leaving them behind would submit, say,
  // ZN's CTD and conversion factor as ZB and format the answer on ZB's tick.
  // They are cleared outright rather than carried over and validated: a
  // half-migrated CTD is exactly the silent-wrong-answer this app must not
  // give. Then the new contract's CTD loads automatically, which is the whole
  // point of the desk flow.
  els.contractSelect.addEventListener("change", () => {
    beginRequest();
    ctdSourceMode = "MANUAL";
    renderContractDetail();
    clearCtdFields();
    renderCtdDetail(null);
    clearAnswers();
    clearError();
    loadBloombergCtd();
  });

  els.loadCtdBtn.addEventListener("click", () => loadBloombergCtd());
  els.convertBtn.addEventListener("click", () => convert());
  els.retryBtn.addEventListener("click", () => {
    const action = retryAction;
    clearError();
    if (action) action();
  });
  els.clearCtdBtn.addEventListener("click", () => {
    beginRequest();
    ctdSourceMode = "MANUAL";
    clearCtdFields();
    renderCtdDetail(null);
    clearAnswers();
    clearError();
    setBloombergStatus("is-warn", "No CTD loaded");
  });

  // Enter converts, from either input: a desk tool is typed at, not clicked.
  [els.futuresPrice, els.targetYield].forEach((element) => {
    element.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        convert();
      }
    });
  });

  async function start() {
    clearError();
    setBloombergStatus("is-busy", "Starting…");
    try {
      await loadContracts();
    } catch (error) {
      setBloombergStatus("is-error", "Not ready");
      showError("Could not load the supported contracts", error.message, start);
      return;
    }
    // Picking a contract loads its CTD, and one is selected from the moment
    // the list renders -- so the first contract loads without being touched.
    await loadBloombergCtd();
  }

  // Read-only accessors for the browser tests, mirroring the pattern the
  // Workbench modules already use. No production behavior depends on them.
  window.__converterTestContracts = () => contracts;
  window.__converterTestRequestGeneration = () => requestGeneration;
  window.__converterTestCtdSourceMode = () => ctdSourceMode;

  start();
})();
