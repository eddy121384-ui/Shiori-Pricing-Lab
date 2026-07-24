// Manual functional prototype wiring (PR #136, extended by Issue #138). This
// file performs no pricing, discounting, accrual, scaling, or Greek math of
// any kind -- it only reads/writes the six approved form fields, calls the
// local HTTP bridge (see
// src/shiori_pricing_lab/app/standalone_option_workbench_server.py), and
// renders the returned display dict verbatim. Every numeric value shown is
// formatted with toFixed(6), the same display precision the existing
// Streamlit workbench uses -- never rounded, rescaled, or re-signed. It also
// performs no JSON-schema validation of an uploaded case and no Markdown/JSON
// export formatting of its own -- both are delegated to the bridge, which
// itself delegates to the existing, unmodified pricing/export helpers.

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
    downloadJsonBtn: document.getElementById("download-json-btn"),
    downloadMarkdownBtn: document.getElementById("download-markdown-btn"),
    caseFileInput: document.getElementById("case-file-input"),
    loadCaseLabel: document.getElementById("load-case-label"),
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
    provenanceBadge: document.getElementById("provenance-badge"),
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
    optionTermsPricingTimestamp: document.getElementById("option-terms-pricing-timestamp"),
    optionTermsExpiryTimestamp: document.getElementById("option-terms-expiry-timestamp"),
    optionTermsSettlementLag: document.getElementById("option-terms-settlement-lag"),
    bloombergRefreshBtn: document.getElementById("bloomberg-refresh-btn"),
    bondIdentifierInput: document.getElementById("bond-identifier-input"),
    bondQuoteSideToggle: document.getElementById("bond-quote-side-toggle"),
    loadBloombergBondBtn: document.getElementById("load-bloomberg-bond-btn"),
    resolvedBondPanel: document.getElementById("resolved-bond-panel"),
    resolvedBondName: document.getElementById("resolved-bond-name"),
    resolvedBondIsin: document.getElementById("resolved-bond-isin"),
    resolvedBondCusip: document.getElementById("resolved-bond-cusip"),
    resolvedBondCurrency: document.getElementById("resolved-bond-currency"),
    resolvedBondCleanPrice: document.getElementById("resolved-bond-clean-price"),
    resolvedBondAccrued: document.getElementById("resolved-bond-accrued"),
    resolvedBondAcquiredAt: document.getElementById("resolved-bond-acquired-at"),
    resolvedBondSource: document.getElementById("resolved-bond-source"),
    bondMismatchNote: document.getElementById("bond-mismatch-note"),
    instrumentHeaderSection: document.getElementById("instrument-header-section"),
    workspaceSection: document.getElementById("workspace-section"),
    instrumentDetailsSection: document.getElementById("instrument-details-section"),
  };

  // The active full case (Issue #138): starts as the bundled default from
  // /api/base, and is wholesale-replaced only by a fully successful upload
  // via /api/case -- never partially updated, never written anywhere but
  // this in-memory variable. baseOverlay/baseContext/baseDisplay always
  // describe this exact case; all four are assigned together, synchronously,
  // so there is no way to observe one without the other three already
  // matching it.
  let baseCase = null;
  let baseOverlay = null;
  let baseContext = null;
  let baseDisplay = null;

  // The currently displayed, exportable run (Issue #138) -- set to a
  // display dict only at the four points where a *completed* pricing
  // outcome (bootstrap success, case-load success, a real Price response,
  // or Clear) is rendered, whether that outcome is SUCCESS or a structured
  // FAILED PricingResult. Explicitly cleared (and export disabled) only when
  // a Price action fails at the transport/decode level and leaves nothing
  // valid on screen. Never set or cleared by a case-load failure -- that
  // path leaves the previously active base, and therefore this variable,
  // completely untouched.
  let currentDisplay = null;

  // Codex review (PR #139): a pending export request captures the display
  // it was asked to export; if a Price/Load/Clear action changes
  // currentDisplay before that export's response arrives, the download must
  // not happen at all -- downloading it would silently hand the user a file
  // for a run that is no longer the one on screen. displayGeneration is
  // bumped every time currentDisplay changes (via setCurrentDisplay, the
  // only place that ever assigns it), and downloadCurrentRun checks it
  // after the response arrives, before ever building the Blob/download.
  let displayGeneration = 0;

  function setCurrentDisplay(display) {
    displayGeneration++;
    currentDisplay = display;
    setExportEnabled(display !== null);
  }

  function setExportEnabled(enabled) {
    els.downloadJsonBtn.classList.toggle("is-disabled", !enabled);
    els.downloadMarkdownBtn.classList.toggle("is-disabled", !enabled);
  }

  // Codex final re-review fix (PR #136): bootstrap (the one-shot initial
  // /api/base load) and pricing requests (Price/Clear) have entirely
  // separate lifecycles/state. Bootstrap never participates in the pricing
  // generation counter below -- there is only ever one bootstrap call, so it
  // can never race against a competing bootstrap call, and it must never be
  // invalidated by a Price/Clear click that happens to fire before it
  // resolves. `bootstrapReady` gates Price/Clear/Load-Case-JSON at the JS
  // logic level (not just a visual CSS class), so it cannot be bypassed by a
  // programmatic click, a keyboard activation, or any other non-standard
  // trigger: each handler returns immediately, with zero side effects, if
  // bootstrap has not yet completed successfully.
  let bootstrapReady = false;

  // The instrument-first Bloomberg lookup's resolved bond identity (Issue
  // #140 revision) -- null until a lookup succeeds, and reset to null only
  // by Clear. Distinct from baseCase/baseOverlay/baseContext/baseDisplay:
  // this describes the bond the trader selected via Bloomberg, which may or
  // may not be the same instrument as the active pricing case.
  let resolvedBloombergBond = null;

  // True only when a resolved Bloomberg bond's own ISIN matches the active
  // case's underlying_isin -- the sole condition under which Price and the
  // existing Bloomberg quote-refresh-and-price path may run against it.
  function bondMatchesActiveCase() {
    return (
      !!resolvedBloombergBond &&
      !!baseContext &&
      resolvedBloombergBond.isin === baseContext.underlying_isin
    );
  }

  // Recomputes every visual/logic consequence of the current
  // resolvedBloombergBond + active-case pairing. Called after bootstrap,
  // after a successful bond lookup, after Clear, and after a case load --
  // every point where either side of the comparison can change. A mismatch
  // hides the old case's instrument header, workspace (option terms +
  // pricing results), and instrument details -- never mutates baseCase/
  // baseOverlay/baseContext/baseDisplay themselves, only what is shown --
  // and disables Price and the Bloomberg refresh-and-price path until a
  // matching Case JSON is loaded or the mismatched bond is cleared.
  function syncBondGating() {
    const mismatch = !!resolvedBloombergBond && !bondMatchesActiveCase();

    els.instrumentHeaderSection.hidden = mismatch;
    els.workspaceSection.hidden = mismatch;
    els.instrumentDetailsSection.hidden = mismatch;
    els.bondMismatchNote.hidden = !mismatch;

    els.priceBtn.classList.toggle("is-disabled", !bootstrapReady || mismatch);
    els.bloombergRefreshBtn.classList.toggle(
      "is-disabled",
      !bootstrapReady || !bondMatchesActiveCase()
    );
  }

  function setControlsEnabled(enabled) {
    bootstrapReady = enabled;
    els.priceBtn.classList.toggle("is-disabled", !enabled);
    els.clearBtn.classList.toggle("is-disabled", !enabled);
    els.loadCaseLabel.classList.toggle("is-disabled", !enabled);
    els.caseFileInput.disabled = !enabled;
    els.loadBloombergBondBtn.classList.toggle("is-disabled", !enabled);
    syncBondGating();
  }

  // Pricing-only request generation/abort tracking -- scoped exclusively to
  // priceCurrentForm/clearToBase (and, via invalidatePendingPriceRequest(),
  // to a case load starting or succeeding), never touched by loadBase.
  // Every async operation that can update the Pricing Results panel captures
  // the current generation number before it starts, and refuses to render
  // if the generation has moved on by the time it resolves. Clear, a new
  // Price click, and a case load (starting or succeeding) all bump the
  // generation, so a slow/stale response can never overwrite newer state --
  // only the single most recent request may render.
  let currentGeneration = 0;
  let inFlightPriceController = null;

  function beginRequest() {
    return ++currentGeneration;
  }

  function isStaleRequest(generation) {
    return generation !== currentGeneration;
  }

  function invalidatePendingPriceRequest() {
    beginRequest();
    if (inFlightPriceController) {
      inFlightPriceController.abort();
      inFlightPriceController = null;
    }
  }

  // Case-load-only request generation/abort tracking (Issue #138) -- an
  // entirely separate counter from the pricing one above, so two case loads
  // racing each other resolve the same way pricing races do: only the
  // single most recent case-load request may ever replace the active base
  // or show its outcome. A stale case-load response (superseded by a newer
  // case-load call that has already started) is discarded outright -- no
  // render at all, success or failure, since a newer attempt has already
  // superseded it.
  let caseLoadGeneration = 0;
  let inFlightCaseLoadController = null;

  function beginCaseLoadRequest() {
    return ++caseLoadGeneration;
  }

  function isStaleCaseLoadRequest(generation) {
    return generation !== caseLoadGeneration;
  }

  // Codex review (PR #139): a case-load generation was previously advanced
  // only by another case load, so a slow upload's eventual response (success
  // or failure) could still land -- and overwrite the active case, form,
  // result, status, and banner -- after a *later* Price or Clear action had
  // already produced a newer, different result. Price and Clear must each
  // call this so the latest user action always wins across request types,
  // exactly like invalidatePendingPriceRequest() already does for pricing.
  function invalidatePendingCaseLoadRequest() {
    beginCaseLoadRequest();
    if (inFlightCaseLoadController) {
      inFlightCaseLoadController.abort();
      inFlightCaseLoadController = null;
    }
  }

  // Bloomberg-refresh-only request generation/abort tracking -- a third,
  // independent counter alongside the pricing and case-load ones above, so
  // the same latest-action-wins model covers all four action types
  // symmetrically: a stale Bloomberg response can never overwrite a later
  // Price, Clear, Case Load, or newer Bloomberg action, and starting any of
  // those other three always invalidates a pending Bloomberg response too
  // (see invalidatePendingBloombergRequest() calls in each of them).
  let bloombergGeneration = 0;
  let inFlightBloombergController = null;

  function beginBloombergRequest() {
    return ++bloombergGeneration;
  }

  function isStaleBloombergRequest(generation) {
    return generation !== bloombergGeneration;
  }

  function invalidatePendingBloombergRequest() {
    beginBloombergRequest();
    if (inFlightBloombergController) {
      inFlightBloombergController.abort();
      inFlightBloombergController = null;
    }
  }

  // Bond-lookup-only request generation/abort tracking -- a fourth,
  // independent counter alongside pricing/case-load/Bloomberg-refresh above,
  // completing the latest-action-wins model across all four action types:
  // a stale lookup response can never overwrite a later Price, Clear, Case
  // Load, Bloomberg refresh, or newer lookup, and starting any of those
  // other four always invalidates a pending lookup response too (see
  // invalidatePendingBondLookupRequest() calls in each of them).
  let bondLookupGeneration = 0;
  let inFlightBondLookupController = null;

  function beginBondLookupRequest() {
    return ++bondLookupGeneration;
  }

  function isStaleBondLookupRequest(generation) {
    return generation !== bondLookupGeneration;
  }

  function invalidatePendingBondLookupRequest() {
    beginBondLookupRequest();
    if (inFlightBondLookupController) {
      inFlightBondLookupController.abort();
      inFlightBondLookupController = null;
    }
  }

  // Mirrors parse_bond_identifier in
  // src/shiori_pricing_lab/data/bloomberg_bond_quote.py -- client-side only
  // for immediate UX feedback and to build the Bloomberg symbology-qualified
  // identifier for the existing quote-refresh-and-price path below; the
  // server independently re-parses and is the actual authority. Never
  // guesses a format and never appends a yellow-key suffix.
  function parseBondIdentifier(raw) {
    if (typeof raw !== "string") return null;
    const normalized = raw.trim().toUpperCase();
    if (/^[A-Z0-9]{12}$/.test(normalized)) {
      return { kind: "ISIN", identifier: normalized, qualified: "/isin/" + normalized };
    }
    if (/^[A-Z0-9]{9}$/.test(normalized)) {
      return { kind: "CUSIP", identifier: normalized, qualified: "/cusip/" + normalized };
    }
    return null;
  }

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

  // Codex review (PR #139): the header badge was a static "Synthetic Data"
  // label, so an uploaded real case still displayed false provenance next
  // to a real pricing result. This shows the case's own declared
  // source_system field verbatim -- never a guess at whether it is
  // synthetic or real -- since that field is the one place the case itself
  // states where it came from. Provenance-neutral wording is used only when
  // the case does not declare a source_system at all.
  function describeProvenance(context) {
    const sourceSystem = context && context.source_system;
    return sourceSystem ? String(sourceSystem) : "Source not declared";
  }

  // Renders the bounded, read-only context dict verbatim (see
  // standalone_option_workbench_context.py) -- every value here is exactly
  // as it appears in the active case (the bundled default, or a
  // successfully uploaded one), with the single coupon percent transform
  // above. Called after /api/base or /api/case resolves successfully, and
  // again (idempotently, on the same cached baseContext) by Clear -- none of
  // the six overlay fields change the underlying instrument's identity, so
  // Price never needs to touch it.
  function renderContext(context) {
    els.instrTitle.textContent = context.issuer;
    els.instrIsin.textContent = context.underlying_isin;
    els.provenanceBadge.textContent = describeProvenance(context);
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

    els.optionTermsPricingTimestamp.textContent = context.pricing_timestamp;
    els.optionTermsExpiryTimestamp.textContent = context.expiry_timestamp;
    setTextOrNotAvailable(els.optionTermsSettlementLag, context.settlement_lag_days);
  }

  function setToggle(toggleEl, value) {
    toggleEl.querySelectorAll(".opt").forEach((opt) => {
      opt.classList.toggle("on", opt.dataset.value === value);
    });
  }

  function getToggleValue(toggleEl) {
    return toggleEl.querySelector(".opt.on").dataset.value;
  }

  // Unlike getToggleValue() above (Call/Put, Buy/Sell -- always exactly one
  // ".on"), the Quote Side toggle deliberately starts with no option
  // selected (no hidden BID/MID/OFFER default), so this returns null rather
  // than throwing until the trader has explicitly clicked one.
  function getOptionalToggleValue(toggleEl) {
    const selected = toggleEl.querySelector(".opt.on");
    return selected ? selected.dataset.value : null;
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

  function clearResultFields() {
    els.priceTotal.textContent = "—";
    els.priceTotalCcy.textContent = "";
    els.pricePer100.textContent = "—";
    els.resultCurrency.textContent = "—";
    els.greekDelta.textContent = "—";
    els.greekGamma.textContent = "—";
    els.greekVega.textContent = "—";
    els.greekTheta.textContent = "—";
  }

  // The single, unified failure path (Codex review follow-up, PR #136): used
  // for a FAILED PricingResult, an HTTP 400/non-2xx bridge response, a
  // network-level fetch rejection, and a non-JSON response body alike.
  // Every one of these clears every premium/currency/Greek field and shows
  // an explicit error banner plus a failed status -- never leaves a prior
  // successful result or its green "loaded" status on screen.
  function renderFailure(message) {
    clearResultFields();
    els.errorBanner.textContent = message;
    els.errorBanner.hidden = false;
    els.statusIndicator.classList.add("failed");
    els.statusText.textContent = "Pricing failed";
  }

  // Issue #138: the case-load failure path is deliberately NOT renderFailure.
  // A failed *attempt* to load a new case must never disturb the currently
  // active base -- the previous context, form values, premium, and Greeks
  // all stay exactly as they were (still a completely valid, still-current
  // run); only the banner communicates that this particular upload failed.
  // currentDisplay/export availability are untouched here for the same
  // reason: the currently exportable run has not changed.
  function renderCaseLoadError(message) {
    els.errorBanner.textContent = message;
    els.errorBanner.hidden = false;
    els.statusIndicator.classList.add("failed");
    els.statusText.textContent = "Case load failed";
  }

  // A Bloomberg failure -- whether an instrument-first bond lookup or the
  // existing quote-refresh-and-price path -- is deliberately NOT
  // renderFailure, for the same reason as renderCaseLoadError: it must
  // preserve the previously active case and completed display exactly as
  // they were, and (for a lookup) the previously resolved bond exactly as
  // it was -- never fall back to stale data, and never show partial
  // provenance or fabricated values. Only the banner communicates that this
  // particular attempt failed; the resolved-bond panel (if already showing
  // from an earlier successful lookup) is left untouched here.
  function renderBloombergError(message) {
    els.errorBanner.textContent = message;
    els.errorBanner.hidden = false;
    els.statusIndicator.classList.add("failed");
    els.statusText.textContent = "Bloomberg request failed";
  }

  function renderSuccess(display) {
    els.errorBanner.hidden = true;
    els.errorBanner.textContent = "";
    els.statusIndicator.classList.remove("failed");
    // Codex review (PR #139): this text must never claim "synthetic" --
    // it renders for both the bundled base case and any uploaded case, and
    // the provenance badge (see describeProvenance) is the one place that
    // states where the active case actually came from.
    els.statusText.textContent = "Case loaded and priced";

    els.priceTotal.textContent = fmt(display.total_notional_model_fair_premium);
    els.priceTotalCcy.textContent = display.result_currency || "";
    els.pricePer100.textContent = fmt(display.model_fair_premium_per_100);
    els.resultCurrency.textContent = display.result_currency || "—";
    els.greekDelta.textContent = fmt(display.forward_price_delta_per_100);
    els.greekGamma.textContent = fmt(display.forward_price_gamma_per_100);
    els.greekVega.textContent = fmt(display.vega_per_vol_point_per_100);
    els.greekTheta.textContent = fmt(display.theta_per_calendar_day_per_100);
  }

  // Renders the resolved Bloomberg bond identity (Issue #140 revision) --
  // the instrument-first lookup's own result, kept on screen independently
  // of whatever the active pricing case's display currently shows. Passing
  // null (only ever done by Clear) hides the panel entirely.
  function renderResolvedBondPanel() {
    if (!resolvedBloombergBond) {
      els.resolvedBondPanel.hidden = true;
      return;
    }
    els.resolvedBondPanel.hidden = false;
    els.resolvedBondName.textContent = resolvedBloombergBond.name;
    els.resolvedBondIsin.textContent = resolvedBloombergBond.isin;
    els.resolvedBondCusip.textContent = resolvedBloombergBond.cusip;
    els.resolvedBondCurrency.textContent = resolvedBloombergBond.currency;
    els.resolvedBondCleanPrice.textContent = fmt(resolvedBloombergBond.clean_price_per_100);
    els.resolvedBondAccrued.textContent = fmt(resolvedBloombergBond.accrued_interest_per_100);
    els.resolvedBondAcquiredAt.textContent = resolvedBloombergBond.acquired_at;
    els.resolvedBondSource.textContent = resolvedBloombergBond.source_system;
  }

  // Renders the bounded, verbatim live_bloomberg_quote section (see
  // prepare_live_bloomberg_quote_display in standalone_option_workbench.py)
  // -- present only on a display produced by the existing Bloomberg
  // refresh-and-price path. Folds its currency/price/accrued/acquisition
  // fields into the same Underlying Bond panel the instrument-first lookup
  // populates, since both describe the one Bloomberg bond currently
  // selected -- never the old case's own bond quote. A null/absent quote
  // (every other action: bootstrap, Price, Clear, Case Load) leaves the
  // panel exactly as the last lookup or refresh left it.
  function applyLiveBloombergQuote(quote) {
    if (!quote || !resolvedBloombergBond) {
      return;
    }
    els.resolvedBondCurrency.textContent = quote.currency;
    els.resolvedBondCleanPrice.textContent = fmt(quote.clean_price_per_100);
    els.resolvedBondAccrued.textContent = fmt(quote.accrued_interest_per_100);
    els.resolvedBondAcquiredAt.textContent = quote.acquired_at;
  }

  function renderDisplay(display) {
    if (display.status === "FAILED") {
      const messages = (display.errors || [])
        .map((e) => `${e.code}: ${e.message}`)
        .join(" | ");
      renderFailure(messages || "Pricing failed.");
      applyLiveBloombergQuote(display.live_bloomberg_quote || null);
      return;
    }
    renderSuccess(display);
    applyLiveBloombergQuote(display.live_bloomberg_quote || null);
  }

  // The one-shot bootstrap load. Runs to completion unconditionally --
  // nothing can invalidate, cancel, or race against it, since it is the
  // only bootstrap call there will ever be. It ends in exactly one of two
  // states: success (baseCase/baseOverlay/baseContext/baseDisplay cached,
  // context + form + base result rendered, controls enabled, export
  // enabled) or failure (unified failure state shown, controls left
  // disabled forever, nothing cached -- there is no retry in this round).
  //
  // Codex final re-review fix: HTTP success is not domain success. A
  // well-formed HTTP 200 /api/base response can still carry a FAILED
  // PricingResult (base-case pricing itself failed) -- that is a bootstrap
  // failure exactly like a non-2xx response, a network rejection, or a
  // JSON decode failure, and must be handled identically: render the real
  // failure, cache nothing, and never flip bootstrapReady/enable
  // Price/Clear/Load-Case-JSON. Only when the HTTP response is ok, the JSON
  // payload is well-formed, AND baseDisplay.status is not FAILED do all of
  // (cache base state, enable controls, enable export) happen together.
  async function loadBase() {
    let response;
    let payload;
    try {
      response = await fetch("/api/base");
      payload = await response.json();
    } catch (err) {
      renderFailure("Failed to load base case: " + err.message);
      return;
    }
    if (!response.ok) {
      renderFailure(payload.error || "Failed to load base case.");
      return;
    }

    const display = payload.display;
    if (!display) {
      renderFailure("Base case response is missing a display payload.");
      return;
    }
    if (display.status === "FAILED") {
      // Reuses the existing FAILED-display rendering path verbatim -- no
      // new failure UI, no partial cache, no cached case/overlay/context,
      // and bootstrapReady stays false so Price/Clear/Load-Case-JSON stay
      // disabled and no pricing request can ever be sent.
      renderDisplay(display);
      return;
    }

    baseCase = payload.case;
    baseOverlay = payload.overlay;
    baseContext = payload.context;
    baseDisplay = display;
    renderContext(baseContext);
    setFormFromOverlay(baseOverlay);
    renderDisplay(baseDisplay);
    setCurrentDisplay(baseDisplay);
    setControlsEnabled(true);
  }

  async function priceCurrentForm() {
    if (!bootstrapReady) return; // ignore any click before bootstrap has completed
    if (resolvedBloombergBond && !bondMatchesActiveCase()) return; // blocked: mismatched Bloomberg bond

    const overlay = readOverlayFromForm();
    const generation = beginRequest();
    invalidatePendingCaseLoadRequest(); // a Price click must beat any older pending Case Load
    invalidatePendingBloombergRequest(); // a Price click must beat any older pending Bloomberg refresh
    invalidatePendingBondLookupRequest(); // ...and any older pending bond lookup

    if (inFlightPriceController) {
      inFlightPriceController.abort();
    }
    const controller = new AbortController();
    inFlightPriceController = controller;

    let response;
    let payload;
    try {
      response = await fetch("/api/case/price", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ case: baseCase, overlay }),
        signal: controller.signal,
      });
      payload = await response.json();
    } catch (err) {
      // Either a genuine network/JSON failure, or this request was
      // superseded (aborted by a newer Price click, by Clear, or by a case
      // load) -- either way, a stale generation means a newer render has
      // already happened and must not be overwritten.
      if (isStaleRequest(generation)) return;
      setCurrentDisplay(null);
      renderFailure("Pricing request failed: " + err.message);
      return;
    }
    if (isStaleRequest(generation)) return;
    if (!response.ok) {
      setCurrentDisplay(null);
      renderFailure(payload.error || "Pricing request failed.");
      return;
    }
    setCurrentDisplay(payload);
    renderDisplay(payload);
  }

  function clearToBase() {
    if (!bootstrapReady) return; // ignore any click before bootstrap has completed

    invalidatePendingPriceRequest(); // invalidate any in-flight Price request's eventual response
    invalidatePendingCaseLoadRequest(); // a Clear click must beat any older pending Case Load
    invalidatePendingBloombergRequest(); // a Clear click must beat any older pending Bloomberg refresh
    invalidatePendingBondLookupRequest(); // ...and any older pending bond lookup

    // Clear also removes the selected Bloomberg bond state entirely (Issue
    // #140 revision requirement 7) -- a fresh Clear returns strictly to the
    // bundled case, with no resolved bond, no mismatch note, and no gating.
    resolvedBloombergBond = null;
    renderResolvedBondPanel();

    if (!baseCase || !baseOverlay || !baseContext || !baseDisplay) {
      syncBondGating();
      return;
    }
    renderContext(baseContext);
    setFormFromOverlay(baseOverlay);
    renderDisplay(baseDisplay);
    setCurrentDisplay(baseDisplay);
    syncBondGating();
  }

  // Issue #138: load a local Case JSON file, validate/price it through the
  // bridge's /api/case route (the only entry point -- no schema validation
  // or pricing logic is duplicated here), and, only on complete success,
  // replace the active base wholesale. A stale response (superseded by a
  // newer case-load call) is discarded outright; a failed load leaves the
  // previously active base completely untouched and fully displayed.
  async function loadCaseFile(file) {
    if (!bootstrapReady) return; // the control is disabled anyway before bootstrap succeeds

    const generation = beginCaseLoadRequest();
    invalidatePendingPriceRequest(); // a case load in flight invalidates any pending Price response
    invalidatePendingBloombergRequest(); // ...and any pending Bloomberg refresh response too
    invalidatePendingBondLookupRequest(); // ...and any pending bond lookup response too

    if (inFlightCaseLoadController) {
      inFlightCaseLoadController.abort();
    }
    const controller = new AbortController();
    inFlightCaseLoadController = controller;

    let bytes;
    try {
      // Read raw bytes (never file.text()): only the server's strict
      // bytes.decode("utf-8") is the authority on whether this upload is
      // valid UTF-8 -- the browser must never silently replace an invalid
      // byte sequence on its own before the bridge ever sees it.
      bytes = await file.arrayBuffer();
    } catch (err) {
      if (isStaleCaseLoadRequest(generation)) return;
      renderCaseLoadError("Failed to read the selected file: " + err.message);
      return;
    }

    let response;
    let payload;
    try {
      response = await fetch("/api/case", {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: bytes,
        signal: controller.signal,
      });
      payload = await response.json();
    } catch (err) {
      if (isStaleCaseLoadRequest(generation)) return;
      renderCaseLoadError("Failed to load the case file: " + err.message);
      return;
    }
    if (isStaleCaseLoadRequest(generation)) return;

    if (!response.ok) {
      renderCaseLoadError(payload.error || "Failed to load the case file.");
      return;
    }

    const display = payload.display;
    if (!display || display.status === "FAILED") {
      // Domain FAILED during a case *load* is a load failure (mirrors the
      // bootstrap FAILED fix above) -- the uploaded case never becomes the
      // active base, nothing is cached, and the previous base stays fully
      // intact and displayed.
      const messages = display
        ? (display.errors || []).map((e) => `${e.code}: ${e.message}`).join(" | ")
        : "";
      renderCaseLoadError(messages || "The uploaded case failed to price.");
      return;
    }

    // Genuine success: replace the active base atomically (all four
    // together, synchronously, so nothing can observe a partial swap), then
    // invalidate any pre-swap Price/Bloomberg/lookup response so none of
    // them can overwrite this freshly rendered base.
    invalidatePendingPriceRequest();
    invalidatePendingBloombergRequest();
    invalidatePendingBondLookupRequest();
    baseCase = payload.case;
    baseOverlay = payload.overlay;
    baseContext = payload.context;
    baseDisplay = display;
    renderContext(baseContext);
    setFormFromOverlay(baseOverlay);
    renderDisplay(baseDisplay);
    setCurrentDisplay(baseDisplay);
    // Requirement 6: a newly loaded case whose ISIN now matches the
    // selected Bloomberg bond re-enables Price and the refresh-and-price
    // path; one that still doesn't match keeps them gated.
    syncBondGating();
  }

  // Bloomberg quote refresh: prices the current active case (bundled or
  // uploaded, whichever baseCase already is) with the current form overlay,
  // using one fresh Bloomberg bond quote in place of the case's own --
  // exactly like Price, except the bridge substitutes one live quote before
  // pricing. This never changes baseCase/baseOverlay/baseContext/baseDisplay
  // (no instrument-identity change): clicking Clear afterwards still
  // restores the case's own original bond quote, not this Bloomberg-priced
  // run. Only runs at all once a Bloomberg bond has been resolved (Issue
  // #140 revision requirement 6) whose own ISIN matches the active case --
  // the same qualified identifier the lookup already validated is reused
  // here, never a separately typed Bloomberg security string. quote_side is
  // required and has no default -- a click with the toggle unselected is
  // rejected client-side before any request is sent.
  async function refreshBloombergAndPrice() {
    if (!bootstrapReady) return; // ignore any click before bootstrap has completed
    if (!resolvedBloombergBond || !bondMatchesActiveCase()) return; // gated: see syncBondGating

    const quoteSide = getOptionalToggleValue(els.bondQuoteSideToggle);
    if (!quoteSide) {
      renderBloombergError("Select a Quote Side before refreshing.");
      return;
    }

    const overlay = readOverlayFromForm();
    const generation = beginBloombergRequest();
    invalidatePendingPriceRequest(); // a Bloomberg refresh must beat any older pending Price
    invalidatePendingCaseLoadRequest(); // ...and any older pending Case Load
    invalidatePendingBondLookupRequest(); // ...and any older pending bond lookup

    if (inFlightBloombergController) {
      inFlightBloombergController.abort();
    }
    const controller = new AbortController();
    inFlightBloombergController = controller;

    let response;
    let payload;
    try {
      response = await fetch("/api/case/bloomberg", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          case: baseCase,
          overlay,
          bloomberg_security: resolvedBloombergBond.qualifiedIdentifier,
          quote_side: quoteSide,
        }),
        signal: controller.signal,
      });
      payload = await response.json();
    } catch (err) {
      // Either a genuine network/JSON failure, or this request was
      // superseded (aborted by a newer Bloomberg click, Price, Clear, or a
      // case load) -- either way, preserve the previous active case and
      // completed display; never fall back to the case's old bond quote.
      if (isStaleBloombergRequest(generation)) return;
      renderBloombergError("Bloomberg refresh failed: " + err.message);
      return;
    }
    if (isStaleBloombergRequest(generation)) return;
    if (!response.ok) {
      renderBloombergError(payload.error || "Bloomberg refresh failed.");
      return;
    }

    setCurrentDisplay(payload);
    renderDisplay(payload);
  }

  // Instrument-first Bloomberg bond lookup (Issue #140 revision): resolves
  // one bond's own identity and one quote side's price via
  // /api/bloomberg/bond -- no active case involved, no expected-ISIN check.
  // On success this updates the visible bond identity immediately and
  // re-syncs Price/refresh-and-price gating against the active case; a
  // mismatch does not clear or touch baseCase/baseOverlay/baseContext/
  // baseDisplay in any way, only what is shown and what is clickable.
  async function loadBloombergBond() {
    if (!bootstrapReady) return; // ignore any click before bootstrap has completed

    const rawIdentifier = els.bondIdentifierInput.value;
    const quoteSide = getOptionalToggleValue(els.bondQuoteSideToggle);
    const parsed = parseBondIdentifier(rawIdentifier);
    if (!parsed || !quoteSide) {
      renderBloombergError(
        "Enter a 12-character ISIN or 9-character CUSIP and select a Quote Side before loading."
      );
      return;
    }

    const generation = beginBondLookupRequest();
    invalidatePendingPriceRequest(); // a bond lookup must beat any older pending Price
    invalidatePendingCaseLoadRequest(); // ...and any older pending Case Load
    invalidatePendingBloombergRequest(); // ...and any older pending Bloomberg refresh

    if (inFlightBondLookupController) {
      inFlightBondLookupController.abort();
    }
    const controller = new AbortController();
    inFlightBondLookupController = controller;

    let response;
    let payload;
    try {
      response = await fetch("/api/bloomberg/bond", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bond_identifier: parsed.identifier, quote_side: quoteSide }),
        signal: controller.signal,
      });
      payload = await response.json();
    } catch (err) {
      // Either a genuine network/JSON failure, or this request was
      // superseded (aborted by a newer lookup, Price, Clear, or a case
      // load) -- either way, a failed lookup must preserve the previously
      // resolved bond and completed screen exactly as they were.
      if (isStaleBondLookupRequest(generation)) return;
      renderBloombergError("Bloomberg bond lookup failed: " + err.message);
      return;
    }
    if (isStaleBondLookupRequest(generation)) return;
    if (!response.ok) {
      renderBloombergError(payload.error || "Bloomberg bond lookup failed.");
      return;
    }

    // A successful lookup for a different ISIN intentionally invalidates
    // the old pricing display -- it belongs to another bond -- which
    // syncBondGating() below enforces by hiding the instrument header,
    // workspace, and instrument details until a matching case is loaded.
    resolvedBloombergBond = {
      qualifiedIdentifier: parsed.qualified,
      isin: payload.isin,
      cusip: payload.cusip,
      name: payload.name,
      currency: payload.currency,
      quote_side: payload.quote_side,
      clean_price_per_100: payload.clean_price_per_100,
      accrued_interest_per_100: payload.accrued_interest_per_100,
      acquired_at: payload.acquired_at,
      source_system: payload.source_system,
    };

    els.errorBanner.hidden = true;
    els.errorBanner.textContent = "";
    els.statusIndicator.classList.remove("failed");
    renderResolvedBondPanel();
    syncBondGating();
  }

  // Issue #138: download the current run as JSON/Markdown, reusing only the
  // existing pure export helpers server-side. Sends exactly the display
  // dict active at click time. Codex review (PR #139): a Price/Load/Clear
  // action can change currentDisplay while this request is still in
  // flight, so the response is checked against displayGeneration before it
  // is ever turned into a download -- a stale response (for a run that is
  // no longer the one on screen) is discarded outright, never downloaded.
  // Never sends a request to any pricing route.
  async function downloadCurrentRun(format) {
    if (!currentDisplay) return; // mirrors the is-disabled state; never fabricate a download

    const generation = displayGeneration;
    const displayAtRequestTime = currentDisplay;
    const route = format === "json" ? "/api/export/json" : "/api/export/markdown";
    let response;
    let payload;
    try {
      response = await fetch(route, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display: displayAtRequestTime }),
      });
      payload = await response.json();
    } catch (err) {
      return; // no display change results from a failed export attempt
    }
    if (generation !== displayGeneration) return; // stale: the displayed run changed while this was in flight
    if (!response.ok) {
      return;
    }

    const blob = new Blob([payload.content], { type: payload.mime });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = payload.filename;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
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
  els.bondQuoteSideToggle.addEventListener("click", (event) => {
    const opt = event.target.closest(".opt");
    if (opt) {
      setToggle(els.bondQuoteSideToggle, opt.dataset.value);
    }
  });

  els.priceBtn.addEventListener("click", priceCurrentForm);
  els.clearBtn.addEventListener("click", clearToBase);
  els.downloadJsonBtn.addEventListener("click", () => downloadCurrentRun("json"));
  els.downloadMarkdownBtn.addEventListener("click", () => downloadCurrentRun("markdown"));
  els.bloombergRefreshBtn.addEventListener("click", refreshBloombergAndPrice);
  els.loadBloombergBondBtn.addEventListener("click", loadBloombergBond);
  els.caseFileInput.addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    event.target.value = ""; // allow re-selecting the same filename again later
    if (file) {
      loadCaseFile(file);
    }
  });

  loadBase();
})();
