// Manual functional prototype wiring (PR #136, extended by Issue #138, #140).
// This file performs no pricing, discounting, accrual, scaling, or Greek
// math of any kind -- it only reads/writes trader-editable form fields,
// calls the local HTTP bridge (see
// src/shiori_pricing_lab/app/standalone_option_workbench_server.py), and
// renders the returned display dict verbatim. Every numeric value shown is
// formatted with toFixed(6), the same display precision the existing
// Streamlit workbench uses -- never rounded, rescaled, or re-signed. It also
// performs no JSON-schema validation of its own -- that is delegated to the
// bridge, which itself delegates to the existing, unmodified
// pricing/export helpers.
//
// Trader-draft revision (Issue #140 second revision): the bundled synthetic
// case and its "Load Case JSON" control are no longer part of the trader
// workflow at all -- the normal page starts with no active instrument,
// pricing result, market data, or provenance. A trader-driven Bloomberg
// bond lookup is the only way to start a run; it seeds a brand-new
// in-memory pricing draft containing only the fields Bloomberg itself
// returned, never anything copied from a prior draft or the bundled
// fixture. Every other pricing input starts empty and stays empty until the
// trader enters it. Price and Refresh Bloomberg & Price stay disabled, with
// a concrete list of still-missing fields shown, until the draft can
// actually be built and priced through the existing reviewed builder
// (POST /api/case, reused unchanged -- the same route the developer-only
// Case JSON workflow already used).

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
    provenancePill: document.getElementById("provenance-pill"),
    provenanceBadge: document.getElementById("provenance-badge"),
    statCleanPrice: document.getElementById("stat-clean-price"),
    statYieldMid: document.getElementById("stat-yield-mid"),
    statValuationDate: document.getElementById("stat-valuation-date"),
    statOptionSettlement: document.getElementById("stat-option-settlement"),
    forwardSettlementNote: document.getElementById("forward-settlement-note"),
    // --- Manual completion inputs (Issue #143) ---
    expiryDate: document.getElementById("expiry-date-input"),
    settlementLag: document.getElementById("settlement-lag-input"),
    volatilityBasis: document.getElementById("volatility-basis-select"),
    valuationDate: document.getElementById("valuation-date-input"),
    reportingDate: document.getElementById("reporting-date-input"),
    forwardSettlementDate: document.getElementById("forward-settlement-date-input"),
    optionSettlementDate: document.getElementById("option-settlement-date-input"),
    asOfTimestamp: document.getElementById("as-of-timestamp-input"),
    asOfTimestampUtc: document.getElementById("as-of-timestamp-utc"),
    pricingTimestamp: document.getElementById("pricing-timestamp-input"),
    pricingTimestampUtc: document.getElementById("pricing-timestamp-utc"),
    expiryTimestamp: document.getElementById("expiry-timestamp-input"),
    expiryTimestampUtc: document.getElementById("expiry-timestamp-utc"),
    timingSnapshotId: document.getElementById("timing-snapshot-id"),
    dayCount: document.getElementById("day-count-select"),
    bondTypeSelect: document.getElementById("bond-type-select"),
    yieldConvention: document.getElementById("yield-convention-select"),
    businessDayConvention: document.getElementById("business-day-convention-select"),
    redemptionAmount: document.getElementById("redemption-amount-input"),
    exDividendDays: document.getElementById("ex-dividend-days-input"),
    lastCouponDate: document.getElementById("last-coupon-date-input"),
    bondStatus: document.getElementById("bond-status-select"),
    hintDayCount: document.getElementById("hint-day-count"),
    hintBondType: document.getElementById("hint-bond-type"),
    hintYieldConvention: document.getElementById("hint-yield-convention"),
    curveRows: document.getElementById("curve-rows"),
    curveAddRowBtn: document.getElementById("curve-add-row-btn"),
    curveCoverage: document.getElementById("curve-coverage"),
    curveCurrencyLabel: document.getElementById("curve-currency-label"),
    timingHead: document.getElementById("timing-head"),
    timingBody: document.getElementById("timing-body"),
    timingIndicator: document.getElementById("timing-indicator"),
    timingSummary: document.getElementById("timing-summary"),
    bondRefHead: document.getElementById("bond-ref-head"),
    bondRefBody: document.getElementById("bond-ref-body"),
    bondRefIndicator: document.getElementById("bond-ref-indicator"),
    bondRefSummary: document.getElementById("bond-ref-summary"),
    curveHead: document.getElementById("curve-head"),
    curveBody: document.getElementById("curve-body"),
    curveIndicator: document.getElementById("curve-indicator"),
    curveSummary: document.getElementById("curve-summary"),
    snapshotCleanPrice: document.getElementById("snapshot-clean-price"),
    snapshotYield: document.getElementById("snapshot-yield"),
    snapshotAccruedInterest: document.getElementById("snapshot-accrued-interest"),
    detailsIssuer: document.getElementById("details-issuer"),
    detailsIsin: document.getElementById("details-isin"),
    detailsCusip: document.getElementById("details-cusip"),
    detailsCoupon: document.getElementById("details-coupon"),
    detailsCouponFrequency: document.getElementById("details-coupon-frequency"),
    detailsIssueDate: document.getElementById("details-issue-date"),
    detailsMaturity: document.getElementById("details-maturity"),
    detailsDayCount: document.getElementById("details-day-count"),
    detailsFirstCouponDate: document.getElementById("details-first-coupon-date"),
    detailsLastCouponDate: document.getElementById("details-last-coupon-date"),
    detailsRedemptionAmount: document.getElementById("details-redemption-amount"),
    detailsCallable: document.getElementById("details-callable"),
    detailsSinkable: document.getElementById("details-sinkable"),
    detailsBondType: document.getElementById("details-bond-type"),
    detailsYieldConvention: document.getElementById("details-yield-convention"),
    detailsBusinessDayConvention: document.getElementById("details-business-day-convention"),
    detailsBloombergDayCount: document.getElementById("details-bloomberg-day-count"),
    detailsBloombergMaturityType: document.getElementById("details-bloomberg-maturity-type"),
    detailsBloombergCalcType: document.getElementById("details-bloomberg-calc-type"),
    detailsSource: document.getElementById("details-source"),
    detailsAcquiredAt: document.getElementById("details-acquired-at"),
    detailsCurrency: document.getElementById("details-currency"),
    bondMasterHead: document.getElementById("bond-master-head"),
    bondMasterToggleBtn: document.getElementById("bond-master-toggle-btn"),
    bondMasterSummary: document.getElementById("bond-master-summary"),
    bondMasterBody: document.getElementById("bond-master-body"),
    underlyingBondHead: document.getElementById("underlying-bond-head"),
    underlyingBondIndicator: document.getElementById("underlying-bond-indicator"),
    underlyingBondBody: document.getElementById("underlying-bond-body"),
    optionTermsHead: document.getElementById("option-terms-head"),
    optionTermsIndicator: document.getElementById("option-terms-indicator"),
    optionTermsBody: document.getElementById("option-terms-body"),
    pricingResultsHead: document.getElementById("pricing-results-head"),
    pricingResultsIndicator: document.getElementById("pricing-results-indicator"),
    pricingResultsBody: document.getElementById("pricing-results-body"),
    forwardCarryHead: document.getElementById("forward-carry-head"),
    forwardCarryIndicator: document.getElementById("forward-carry-indicator"),
    forwardCarrySummary: document.getElementById("forward-carry-summary"),
    forwardCarryBody: document.getElementById("forward-carry-body"),
    underlyingSnapshotHead: document.getElementById("underlying-snapshot-head"),
    underlyingSnapshotIndicator: document.getElementById("underlying-snapshot-indicator"),
    underlyingSnapshotBody: document.getElementById("underlying-snapshot-body"),
    sidebarLiveRow: document.getElementById("sidebar-live-row"),
    sidebarSourceSystem: document.getElementById("sidebar-source-system"),
    sidebarAsofRow: document.getElementById("sidebar-asof-row"),
    sidebarAsOf: document.getElementById("sidebar-as-of-timestamp"),
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
    resolvedBondCoupon: document.getElementById("resolved-bond-coupon"),
    resolvedBondMaturity: document.getElementById("resolved-bond-maturity"),
    resolvedBondAcquiredAt: document.getElementById("resolved-bond-acquired-at"),
    resolvedBondSource: document.getElementById("resolved-bond-source"),
    draftIncompleteNote: document.getElementById("draft-incomplete-note"),
    missingCategories: document.getElementById("missing-categories"),
    remainingInputSummary: document.getElementById("remaining-input-summary"),
    missingDetailsToggleBtn: document.getElementById("missing-details-toggle-btn"),
    missingFieldsList: document.getElementById("missing-fields-list"),
    timingProductId: document.getElementById("timing-product-id"),
    timingSnapshotMetadata: document.getElementById("timing-snapshot-metadata"),
    timingBondQuoteMetadata: document.getElementById("timing-bond-quote-metadata"),
    timingForwardMetadata: document.getElementById("timing-forward-metadata"),
    timingVolatilityMetadata: document.getElementById("timing-volatility-metadata"),
    timingCreditMetadata: document.getElementById("timing-credit-metadata"),
    instrumentHeaderSection: document.getElementById("instrument-header-section"),
    workspaceSection: document.getElementById("workspace-section"),
    instrumentDetailsSection: document.getElementById("instrument-details-section"),
  };

  const CATEGORY_OPTION_TERMS = "Option terms incomplete";
  const CATEGORY_BOND_REFERENCE_DATA = "Bond reference data incomplete";
  const CATEGORY_MARKET_CURVES = "Market curves unavailable";
  const CATEGORY_FORWARD_CLEAN_PRICE = "Forward clean price unavailable";
  const CATEGORY_PRICE_VOLATILITY = "Price volatility required";
  const CATEGORY_VALUATION_METADATA = "Valuation & snapshot metadata incomplete";

  // The resolved Bloomberg bond identity from the instrument-first lookup --
  // null until a lookup succeeds, and reset to null only by Clear or a
  // newer successful lookup.
  let resolvedBloombergBond = null;

  // The in-memory pricing draft (trader-draft revision): a full standalone-
  // option-case-shaped object, created fresh by buildInitialDraftFromBloomberg
  // on every successful lookup -- never derived from, or merged with, a
  // prior draft or the bundled synthetic fixture. Only values supplied by
  // Bloomberg or fixed mechanically by the existing route/contract are
  // seeded; every genuine trader decision starts null. null until a bond has
  // been resolved.
  let currentDraft = null;

  // Collapsible sections (PR #141 third revision): purely a display toggle
  // on each section's own body element, entirely independent of the
  // draft/pricing/Bloomberg state machines below -- collapsing a section
  // never refetches data, clears a form, or mutates currentDraft/
  // currentDisplay. One click listener per section head (never a second one
  // on the indicator/summary spans nested inside it) toggles that section's
  // body `hidden` state, so clicking anywhere in the header row never
  // double-toggles. Registration order doubles as each section's default
  // (collapsed vs expanded) state, restored verbatim by resetCollapseStates
  // on Clear.
  function registerCollapsibleSection({
    head,
    body,
    indicator,
    summary,
    defaultCollapsed,
    summaryText,
    alwaysShowSummary,
  }) {
    function refreshSummary() {
      if (!summary || !summaryText) return;
      // A count of what a section still needs stays useful while the section
      // is open, so the manual-completion sections show it either way.
      summary.textContent = alwaysShowSummary || body.hidden ? summaryText() : "";
    }
    function applyState(collapsed) {
      body.hidden = collapsed;
      head.setAttribute("aria-expanded", String(!collapsed));
      indicator.textContent = collapsed ? "Expand" : "Collapse";
      refreshSummary();
    }
    head.addEventListener("click", () => {
      applyState(!body.hidden);
    });
    applyState(defaultCollapsed);
    return {
      body,
      expand: () => applyState(false),
      reset: () => applyState(defaultCollapsed),
      refreshSummary,
    };
  }

  // Live count of what one manual-completion section still needs. Reads the
  // same computeMissingDraftFields the Price gate uses, so the header count,
  // the category badges and the enabled/disabled Price button can never
  // disagree.
  function missingCountSummary(category) {
    return () => {
      if (!currentDraft) return "";
      const count = computeMissingDraftFields(currentDraft).filter(
        (item) => item.category === category
      ).length;
      return count === 0 ? "Complete" : `${count} required`;
    };
  }

  const collapsibleSections = [
    registerCollapsibleSection({
      head: els.underlyingBondHead,
      body: els.underlyingBondBody,
      indicator: els.underlyingBondIndicator,
      defaultCollapsed: false,
    }),
    registerCollapsibleSection({
      head: els.optionTermsHead,
      body: els.optionTermsBody,
      indicator: els.optionTermsIndicator,
      defaultCollapsed: false,
    }),
    registerCollapsibleSection({
      head: els.pricingResultsHead,
      body: els.pricingResultsBody,
      indicator: els.pricingResultsIndicator,
      defaultCollapsed: false,
    }),
    registerCollapsibleSection({
      head: els.forwardCarryHead,
      body: els.forwardCarryBody,
      indicator: els.forwardCarryIndicator,
      summary: els.forwardCarrySummary,
      defaultCollapsed: false,
      alwaysShowSummary: true,
      summaryText: missingCountSummary(CATEGORY_FORWARD_CLEAN_PRICE),
    }),
    registerCollapsibleSection({
      head: els.underlyingSnapshotHead,
      body: els.underlyingSnapshotBody,
      indicator: els.underlyingSnapshotIndicator,
      defaultCollapsed: true,
    }),
    // Advanced timing and bond-reference fields default collapsed, but their
    // live required counts stay visible in the header. The Option Discount
    // Curve is a primary trader decision and remains expanded.
    registerCollapsibleSection({
      head: els.timingHead,
      body: els.timingBody,
      indicator: els.timingIndicator,
      summary: els.timingSummary,
      defaultCollapsed: true,
      alwaysShowSummary: true,
      summaryText: missingCountSummary(CATEGORY_VALUATION_METADATA),
    }),
    registerCollapsibleSection({
      head: els.bondRefHead,
      body: els.bondRefBody,
      indicator: els.bondRefIndicator,
      summary: els.bondRefSummary,
      defaultCollapsed: true,
      alwaysShowSummary: true,
      summaryText: missingCountSummary(CATEGORY_BOND_REFERENCE_DATA),
    }),
    registerCollapsibleSection({
      head: els.curveHead,
      body: els.curveBody,
      indicator: els.curveIndicator,
      summary: els.curveSummary,
      defaultCollapsed: false,
      alwaysShowSummary: true,
      summaryText: missingCountSummary(CATEGORY_MARKET_CURVES),
    }),
    registerCollapsibleSection({
      head: els.bondMasterHead,
      body: els.bondMasterBody,
      indicator: els.bondMasterToggleBtn,
      summary: els.bondMasterSummary,
      defaultCollapsed: true,
      // Instrument Details is only ever shown once a bond has been resolved
      // via Bloomberg (see syncDraftGating's hasDraft gate), so its source
      // is always Bloomberg DAPI whenever this summary is visible at all.
      summaryText: () => "Bloomberg DAPI",
    }),
  ];

  function resetCollapseStates() {
    collapsibleSections.forEach((section) => section.reset());
  }

  // The currently displayed, exportable run -- set to a display dict only
  // once a genuine POST /api/case or POST /api/case/bloomberg call against
  // the current draft succeeds (HTTP 200), whether that outcome is SUCCESS
  // or a structured FAILED PricingResult. Cleared (and export disabled) by
  // a fresh Bloomberg lookup, by Clear, and by a Price/Refresh transport-
  // level failure that leaves nothing valid on screen.
  let currentDisplay = null;

  // Codex review (PR #139): a pending export request captures the display
  // it was asked to export; if a later action changes currentDisplay before
  // that export's response arrives, the download must not happen at all.
  // displayGeneration is bumped every time currentDisplay changes (via
  // setCurrentDisplay, the only place that ever assigns it), and
  // downloadCurrentRun checks it after the response arrives, before ever
  // building the Blob/download.
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

  // Reused exactly, mirroring standalone_option_workbench.build_request_from_
  // standalone_option_case's own required-field shape (products/bond_option.py,
  // data/bli_snapshot.py, reference_data/bond_reference_data.py) -- not a new
  // financial rule, just this workbench's own record of which leaves those
  // existing typed constructors require non-null. Anything not listed here
  // either has a real default on the dataclass itself (e.g. strike_yield,
  // exercise_start_date, product_type) or is always populated verbatim from
  // Bloomberg the moment a draft exists (isin/currency/issuer/quote_side/
  // clean_price_per_100/accrued_interest_per_100/source_system), so it can
  // never appear in this workbench's own missing-input list.
  // Category labels shown to the trader as an actionable summary (Issue
  // #140 third revision) -- grouping this workbench's own established
  // required-field list, never a new financial rule.
  const REQUIRED_DRAFT_FIELD_CHECKS = [
    ["bond_option.product_id", "Product ID", CATEGORY_OPTION_TERMS],
    ["bond_option.payoff_basis", "Payoff Basis", CATEGORY_OPTION_TERMS],
    ["bond_option.option_type", "Call / Put", CATEGORY_OPTION_TERMS],
    ["bond_option.exercise_style", "Exercise Style", CATEGORY_OPTION_TERMS],
    ["bond_option.settlement_type", "Settlement Type", CATEGORY_OPTION_TERMS],
    [
      "bond_option.settlement_lag_days",
      "Settlement Lag (days)",
      CATEGORY_VALUATION_METADATA,
    ],
    ["bond_option.expiry_date", "Expiry Date", CATEGORY_OPTION_TERMS],
    ["bond_option.notional", "Notional", CATEGORY_OPTION_TERMS],
    ["bond_option.position", "Direction (Buy/Sell)", CATEGORY_OPTION_TERMS],
    ["bond_option.strike_price", "Strike (per 100)", CATEGORY_OPTION_TERMS],

    ["bond_reference_data_universe.0.coupon", "Coupon", CATEGORY_BOND_REFERENCE_DATA],
    [
      "bond_reference_data_universe.0.coupon_frequency",
      "Coupon Frequency",
      CATEGORY_BOND_REFERENCE_DATA,
    ],
    ["bond_reference_data_universe.0.maturity_date", "Maturity Date", CATEGORY_BOND_REFERENCE_DATA],
    ["bond_reference_data_universe.0.issue_date", "Issue Date", CATEGORY_BOND_REFERENCE_DATA],
    ["bond_reference_data_universe.0.day_count", "Day Count", CATEGORY_BOND_REFERENCE_DATA],
    [
      "bond_reference_data_universe.0.business_day_convention",
      "Business Day Convention",
      CATEGORY_BOND_REFERENCE_DATA,
    ],
    [
      "bond_reference_data_universe.0.redemption_amount",
      "Redemption Amount",
      CATEGORY_BOND_REFERENCE_DATA,
    ],
    [
      "bond_reference_data_universe.0.callable_flag",
      "Callable Flag",
      CATEGORY_BOND_REFERENCE_DATA,
    ],
    [
      "bond_reference_data_universe.0.sinkable_flag",
      "Sinkable Flag",
      CATEGORY_BOND_REFERENCE_DATA,
    ],
    ["bond_reference_data_universe.0.bond_type", "Bond Type", CATEGORY_BOND_REFERENCE_DATA],
    [
      "bond_reference_data_universe.0.yield_convention",
      "Yield Convention",
      CATEGORY_BOND_REFERENCE_DATA,
    ],
    [
      "bond_reference_data_universe.0.ex_dividend_days",
      "Ex-Dividend Days",
      CATEGORY_BOND_REFERENCE_DATA,
    ],
    [
      "bond_reference_data_universe.0.first_coupon_date",
      "First Coupon Date",
      CATEGORY_BOND_REFERENCE_DATA,
    ],
    [
      "bond_reference_data_universe.0.last_coupon_date",
      "Last Coupon Date",
      CATEGORY_BOND_REFERENCE_DATA,
    ],
    ["bond_reference_data_universe.0.status", "Status", CATEGORY_BOND_REFERENCE_DATA],

    ["valuation_date", "Valuation Date", CATEGORY_VALUATION_METADATA],
    ["as_of_timestamp", "As-Of Timestamp", CATEGORY_VALUATION_METADATA],
    ["pricing_timestamp", "Pricing Timestamp", CATEGORY_VALUATION_METADATA],
    ["expiry_timestamp", "Expiry Timestamp", CATEGORY_OPTION_TERMS],
    ["reporting_date", "Reporting Date", CATEGORY_VALUATION_METADATA],
    ["forward_settlement_date", "Forward Settlement Date", CATEGORY_VALUATION_METADATA],
    ["option_settlement_date", "Option Settlement Date", CATEGORY_VALUATION_METADATA],
    ["source_system", "Source System", CATEGORY_VALUATION_METADATA],
    ["snapshot_id", "Snapshot ID", CATEGORY_VALUATION_METADATA],
    ["snapshot_status", "Snapshot Status", CATEGORY_VALUATION_METADATA],
    ["bond_quote.price_type", "Bond Quote Price Type", CATEGORY_VALUATION_METADATA],
    ["bond_quote.status", "Bond Quote Status", CATEGORY_VALUATION_METADATA],

    [
      "forward_clean_price_input.forward_clean_price_per_100",
      "Forward Clean Price (per 100)",
      CATEGORY_FORWARD_CLEAN_PRICE,
    ],
    [
      "forward_clean_price_input.quote_side",
      "Forward Clean Price Quote Side",
      CATEGORY_FORWARD_CLEAN_PRICE,
    ],
    [
      "forward_clean_price_input.source_system",
      "Forward Clean Price Source System",
      CATEGORY_FORWARD_CLEAN_PRICE,
    ],
    ["forward_clean_price_input.status", "Forward Clean Price Status", CATEGORY_FORWARD_CLEAN_PRICE],

    ["volatility_input.volatility", "Price Vol (σ)", CATEGORY_PRICE_VOLATILITY],
    ["volatility_input.volatility_basis", "Volatility Basis", CATEGORY_PRICE_VOLATILITY],
    ["volatility_input.source_system", "Volatility Source System", CATEGORY_PRICE_VOLATILITY],
    ["volatility_input.status", "Volatility Status", CATEGORY_PRICE_VOLATILITY],

    ["credit_spread_input.spread_treatment", "Credit Spread Treatment", CATEGORY_MARKET_CURVES],
    ["credit_spread_input.source_system", "Credit Spread Source System", CATEGORY_MARKET_CURVES],
    ["credit_spread_input.status", "Credit Spread Status", CATEGORY_MARKET_CURVES],
  ];

  // BLICreditSpreadInput's requirements are conditional on the treatment
  // (data/bli_snapshot.py). #142 found this workbench demanding
  // credit_spread / credit_spread_basis unconditionally -- fields the
  // contract actually forbids for EMBEDDED / NOT_REQUIRED -- while never
  // demanding override_or_fallback_audit, which the contract does require
  // for every treatment except OBSERVED. These are the real rules:
  //
  //   OBSERVED               -> spread + basis required; audit must be absent
  //   OVERRIDE / FALLBACK    -> spread + basis required; audit required
  //   EMBEDDED / NOT_REQUIRED-> spread + basis must be absent; audit required
  const CREDIT_TREATMENTS_REQUIRING_SPREAD_VALUE = ["OBSERVED", "OVERRIDE", "FALLBACK"];
  const CREDIT_TREATMENTS_REQUIRING_AUDIT = [
    "OVERRIDE",
    "FALLBACK",
    "EMBEDDED",
    "NOT_REQUIRED",
  ];

  function computeMissingCreditSpreadFields(creditSpreadInput) {
    const missing = [];
    const input = creditSpreadInput || {};
    const treatment = input.spread_treatment;
    if (!isPresent(treatment)) {
      // The treatment itself is already reported by the flat checklist
      // above; without it no conditional rule can be evaluated.
      return missing;
    }
    if (CREDIT_TREATMENTS_REQUIRING_SPREAD_VALUE.includes(treatment)) {
      if (!isPresent(input.credit_spread)) {
        missing.push({ label: "Credit Spread", category: CATEGORY_MARKET_CURVES });
      }
      if (!isPresent(input.credit_spread_basis)) {
        missing.push({ label: "Credit Spread Basis", category: CATEGORY_MARKET_CURVES });
      }
    }
    if (
      CREDIT_TREATMENTS_REQUIRING_AUDIT.includes(treatment) &&
      !isPresent(input.override_or_fallback_audit)
    ) {
      missing.push({
        label: "Credit Spread Audit Explanation",
        category: CATEGORY_MARKET_CURVES,
      });
    }
    return missing;
  }

  // Fixed display order: the trader-facing categories in a stable,
  // intentional sequence, independent of REQUIRED_DRAFT_FIELD_CHECKS'
  // own declaration order.
  const MISSING_CATEGORY_ORDER = [
    CATEGORY_OPTION_TERMS,
    CATEGORY_BOND_REFERENCE_DATA,
    CATEGORY_MARKET_CURVES,
    CATEGORY_FORWARD_CLEAN_PRICE,
    CATEGORY_PRICE_VOLATILITY,
    CATEGORY_VALUATION_METADATA,
  ];

  // Every unresolved item can take the trader to the control or source detail
  // that owns it. The mapping changes no completion rule: labels still come
  // exclusively from computeMissingDraftFields, and the target is navigation
  // only. Bloomberg-owned fields that unexpectedly arrive null point to their
  // Bond Master detail rather than pretending there is a safe manual default.
  const MISSING_FIELD_LOCATORS = new Map([
    ["Call / Put", { selector: "#option-type-toggle", bodyId: "option-terms-body" }],
    ["Direction (Buy/Sell)", { selector: "#position-toggle", bodyId: "option-terms-body" }],
    ["Strike (per 100)", { selector: "#strike-price-input", bodyId: "option-terms-body" }],
    ["Notional", { selector: "#notional-input", bodyId: "option-terms-body" }],
    ["Expiry Date", { selector: "#expiry-date-input", bodyId: "option-terms-body" }],
    ["Expiry Timestamp", { selector: "#expiry-timestamp-input", bodyId: "option-terms-body" }],
    ["Settlement Lag (days)", { selector: "#settlement-lag-input", bodyId: "timing-body" }],
    ["Price Vol (σ)", { selector: "#volatility-input", bodyId: "option-terms-body" }],
    ["Volatility Basis", { selector: "#volatility-basis-select", bodyId: "option-terms-body" }],
    [
      "Forward Clean Price (per 100)",
      { selector: "#forward-price-input", bodyId: "forward-carry-body" },
    ],
    ["Day Count", { selector: "#day-count-select", bodyId: "bond-ref-body" }],
    ["Business Day Convention", { selector: "#business-day-convention-select", bodyId: "bond-ref-body" }],
    ["Redemption Amount", { selector: "#redemption-amount-input", bodyId: "bond-ref-body" }],
    ["Ex-Dividend Days", { selector: "#ex-dividend-days-input", bodyId: "bond-ref-body" }],
    ["Last Coupon Date", { selector: "#last-coupon-date-input", bodyId: "bond-ref-body" }],
    ["Bond Type", { selector: "#bond-type-select", bodyId: "bond-ref-body" }],
    ["Yield Convention", { selector: "#yield-convention-select", bodyId: "bond-ref-body" }],
    ["Status", { selector: "#bond-status-select", bodyId: "bond-ref-body" }],
    ["Valuation Date", { selector: "#valuation-date-input", bodyId: "timing-body" }],
    ["As-Of Timestamp", { selector: "#as-of-timestamp-input", bodyId: "timing-body" }],
    ["Pricing Timestamp", { selector: "#pricing-timestamp-input", bodyId: "timing-body" }],
    ["Reporting Date", { selector: "#reporting-date-input", bodyId: "timing-body" }],
    [
      "Forward Settlement Date",
      { selector: "#forward-settlement-date-input", bodyId: "timing-body" },
    ],
    [
      "Option Settlement Date",
      { selector: "#option-settlement-date-input", bodyId: "timing-body" },
    ],
    [
      "Option Discount Curve",
      { selector: "#curve-rows .curve-tenor-input", bodyId: "curve-body" },
    ],
    ["Coupon", { selector: "#details-coupon", bodyId: "bond-master-body" }],
    ["Coupon Frequency", { selector: "#details-coupon-frequency", bodyId: "bond-master-body" }],
    ["Maturity Date", { selector: "#details-maturity", bodyId: "bond-master-body" }],
    ["Issue Date", { selector: "#details-issue-date", bodyId: "bond-master-body" }],
    ["Callable Flag", { selector: "#details-callable", bodyId: "bond-master-body" }],
    ["Sinkable Flag", { selector: "#details-sinkable", bodyId: "bond-master-body" }],
    ["First Coupon Date", { selector: "#details-first-coupon-date", bodyId: "bond-master-body" }],
  ]);

  const MISSING_CATEGORY_FALLBACKS = new Map([
    [CATEGORY_OPTION_TERMS, { selector: "#option-terms-head", bodyId: "option-terms-body" }],
    [
      CATEGORY_BOND_REFERENCE_DATA,
      { selector: "#bond-ref-head", bodyId: "bond-ref-body" },
    ],
    [CATEGORY_MARKET_CURVES, { selector: "#curve-head", bodyId: "curve-body" }],
    [
      CATEGORY_FORWARD_CLEAN_PRICE,
      { selector: "#forward-carry-head", bodyId: "forward-carry-body" },
    ],
    [CATEGORY_PRICE_VOLATILITY, { selector: "#volatility-input", bodyId: "option-terms-body" }],
    [
      CATEGORY_VALUATION_METADATA,
      { selector: "#timing-head", bodyId: "timing-body" },
    ],
  ]);

  function revealMissingField(label, category) {
    const locator = MISSING_FIELD_LOCATORS.get(label) || MISSING_CATEGORY_FALLBACKS.get(category);
    if (!locator) return;
    const section = collapsibleSections.find((candidate) => candidate.body.id === locator.bodyId);
    if (section) section.expand();
    const target = document.querySelector(locator.selector);
    if (!target) return;
    if (!target.matches("input, select, button, textarea, [tabindex]")) {
      target.setAttribute("tabindex", "-1");
    }
    target.focus({ preventScroll: true });
    target.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function readDottedPath(root, path) {
    return path.split(".").reduce((node, key) => (node == null ? undefined : node[key]), root);
  }

  // Structural presence only -- never a financial judgment about whether a
  // present value is itself valid (that stays the existing typed
  // constructors' job, applied when Price/Refresh actually call the real
  // builder). `0` and `false` are real values, never treated as missing.
  // Returns a flat list of {label, category} -- grouping into the
  // trader-facing category summary is renderMissingFieldsSummary's job.
  function isPresent(value) {
    return value !== null && value !== undefined && value !== "";
  }

  function computeMissingDraftFields(draft) {
    const missing = REQUIRED_DRAFT_FIELD_CHECKS.filter(
      ([path]) => !isPresent(readDottedPath(draft, path))
    ).map(([, label, category]) => ({ label, category }));

    missing.push(...computeMissingCreditSpreadFields(draft.credit_spread_input));

    // The Option Discount Curve is not a single field: it needs at least two
    // valid continuous-zero nodes that actually cover every date the request
    // discounts to. An uncovered date is a hard stop -- the reviewed
    // interpolator rejects an out-of-range target rather than extrapolating.
    const coverage = computeCurveCoverage(draft);
    if (coverage.state !== "covered") {
      missing.push({ label: "Option Discount Curve", category: CATEGORY_MARKET_CURVES });
    }
    return missing;
  }

  // Bond-lookup-only request generation/abort tracking.
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

  // Pricing-only request generation/abort tracking.
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

  // Bloomberg-refresh-only request generation/abort tracking.
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

  // A blank trader-entered numeric field must never silently become 0
  // (Number("") === 0 in JS) -- blank means "not entered", not zero.
  function numberOrNull(raw) {
    const trimmed = (raw || "").trim();
    if (trimmed === "") return null;
    const value = Number(trimmed);
    return Number.isFinite(value) ? value : null;
  }

  // Displays a raw, dimensionless coupon fraction (e.g. 0.0325) as a
  // percentage (3.250%). This is the one display-only arithmetic transform
  // this file applies anywhere: value * 100, deterministic, no market
  // assumption or model involved.
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

  // Coupon is stored/priced as a decimal fraction (e.g. 0.0375) throughout
  // the draft, API response, and Bloomberg conversion -- this only changes
  // how it's *displayed* to the trader (as a percentage), via the same
  // fmtCouponPercent already used for the priced case's own coupon in
  // renderContext.
  function setCouponPercentOrNotAvailable(el, value) {
    const text = fmtCouponPercent(value);
    el.textContent = text;
    el.classList.toggle("pending-value", text === "Not available");
  }

  // Codex review (PR #139): shows the case's own declared source_system
  // field verbatim -- never a guess at whether it is synthetic or real.
  function describeProvenance(context) {
    const sourceSystem = context && context.source_system;
    return sourceSystem ? String(sourceSystem) : "Source not declared";
  }

  // Renders the bounded, read-only context dict verbatim (see
  // standalone_option_workbench_context.py) -- called only once a real
  // Price or Refresh Bloomberg & Price call has actually succeeded against
  // the current draft, since only then does every field this reads
  // genuinely exist.
  function renderContext(context) {
    els.instrTitle.textContent = context.issuer;
    els.instrIsin.textContent = context.underlying_isin;
    els.provenancePill.hidden = false;
    els.provenanceBadge.textContent = describeProvenance(context);
    els.quoteSideBadge.hidden = false;
    els.quoteSideBadge.textContent = context.quote_side;

    setTextOrNotAvailable(els.statCleanPrice, context.clean_price_per_100);
    setTextOrNotAvailable(els.statYieldMid, context.yield_value);
    els.statValuationDate.textContent = context.valuation_date;
    els.statOptionSettlement.textContent = context.option_settlement_date;

    els.forwardSettlementNote.textContent =
      "Forward settlement: " + context.forward_settlement_date;

    setTextOrNotAvailable(els.snapshotCleanPrice, context.clean_price_per_100);
    setTextOrNotAvailable(els.snapshotYield, context.yield_value);
    setTextOrNotAvailable(els.snapshotAccruedInterest, context.accrued_interest_per_100);

    els.detailsIssuer.textContent = context.issuer;
    els.detailsCoupon.textContent = fmtCouponPercent(context.coupon);
    els.detailsMaturity.textContent = context.maturity_date;
    els.detailsDayCount.textContent = context.day_count;
    els.detailsCouponFrequency.textContent = context.coupon_frequency;
    els.detailsCurrency.textContent = context.currency;

    els.sidebarLiveRow.hidden = false;
    els.sidebarSourceSystem.textContent = describeProvenance(context);
    els.sidebarAsofRow.hidden = false;
    els.sidebarAsOf.textContent = context.as_of_timestamp;

  }

  // The inverse of renderContext: hides every context-driven display back
  // to its untouched default. Called by a fresh Bloomberg lookup (a new
  // draft has no priced context yet) and by Clear.
  function hideContext() {
    els.provenancePill.hidden = true;
    els.quoteSideBadge.hidden = true;
    els.sidebarLiveRow.hidden = true;
    els.sidebarAsofRow.hidden = true;
  }

  // Client-side mirror of extract_standalone_option_case_context
  // (standalone_option_workbench_context.py), field-for-field -- used only
  // for the Bloomberg refresh-and-price path, whose response
  // (price_case_with_bloomberg_quote) carries a display dict but no
  // separate context section, unlike POST /api/case. Every value is a
  // direct read off the already-complete draft (Price/Refresh are disabled
  // until it is complete) -- no computation, no inference.
  function extractContextFromDraft(draft) {
    const bondOption = draft.bond_option;
    const isin = bondOption.underlying_isin;
    const referenceData = (draft.bond_reference_data_universe || []).find(
      (record) => record.isin === isin
    );
    const bondQuote = draft.bond_quote;
    return {
      underlying_isin: isin,
      expiry_date: bondOption.expiry_date,
      settlement_lag_days: bondOption.settlement_lag_days,
      issuer: referenceData.issuer,
      currency: referenceData.currency,
      coupon: referenceData.coupon,
      maturity_date: referenceData.maturity_date,
      coupon_frequency: referenceData.coupon_frequency,
      day_count: referenceData.day_count,
      bond_type: referenceData.bond_type,
      quote_side: bondQuote.quote_side,
      clean_price_per_100: bondQuote.clean_price_per_100,
      accrued_interest_per_100: bondQuote.accrued_interest_per_100,
      yield_value: bondQuote.yield_value,
      valuation_date: draft.valuation_date,
      pricing_timestamp: draft.pricing_timestamp,
      expiry_timestamp: draft.expiry_timestamp,
      forward_settlement_date: draft.forward_settlement_date,
      option_settlement_date: draft.option_settlement_date,
      source_system: draft.source_system,
      as_of_timestamp: draft.as_of_timestamp,
      snapshot_id: draft.snapshot_id,
    };
  }

  function setToggle(toggleEl, value) {
    toggleEl.querySelectorAll(".opt").forEach((opt) => {
      opt.classList.toggle("on", opt.dataset.value === value);
    });
  }

  // No option ever starts pre-selected in this revision (Call/Put and
  // Buy/Sell included, alongside the Quote Side toggles) -- this returns
  // null rather than throwing until the trader has explicitly clicked one.
  function getOptionalToggleValue(toggleEl) {
    const selected = toggleEl.querySelector(".opt.on");
    return selected ? selected.dataset.value : null;
  }

  // Every trader-editable input on the page, cleared as one set. A fresh
  // Bloomberg lookup and Clear both call this: a manual value entered for
  // one bond (its dates, its bond-reference terms, its curve, its forward
  // and vol) must never survive into a different bond's draft (Issue #143).
  const MANUAL_TEXT_INPUTS = () => [
    els.strikePrice,
    els.notional,
    els.volatility,
    els.forwardPrice,
    els.expiryDate,
    els.expiryTimestamp,
    els.settlementLag,
    els.reportingDate,
    els.forwardSettlementDate,
    els.optionSettlementDate,
    els.redemptionAmount,
    els.exDividendDays,
    els.lastCouponDate,
  ];

  const DERIVED_TIMING_INPUTS = () => [
    els.valuationDate,
    els.asOfTimestamp,
    els.pricingTimestamp,
  ];

  const MANUAL_SELECTS = () => [
    els.dayCount,
    els.bondTypeSelect,
    els.yieldConvention,
    els.businessDayConvention,
    els.bondStatus,
  ];

  function clearOptionTermsForm() {
    setToggle(els.optionTypeToggle, null);
    setToggle(els.positionToggle, null);
    MANUAL_TEXT_INPUTS().forEach((input) => {
      input.value = "";
    });
    DERIVED_TIMING_INPUTS().forEach((input) => {
      input.value = "";
    });
    MANUAL_SELECTS().forEach((select) => {
      select.value = "";
    });
    // Volatility basis is the one select with a real default: PRICE_VOL is
    // the direct, no-conversion basis the guard accepts.
    els.volatilityBasis.value = "PRICE_VOL";
    // Two blank rows: the minimum the curve contract needs, offered ready to
    // fill. They are structure only -- a blank row is never a node.
    clearCurveRows();
    addCurveRow("", "");
    addCurveRow("", "");
    renderTimestampUtcPreviews();
    renderCurveCoverage();
  }

  function setDerivedTimingFormFromDraft() {
    els.valuationDate.value = currentDraft ? currentDraft.valuation_date || "" : "";
    els.asOfTimestamp.value = currentDraft ? currentDraft.as_of_timestamp || "" : "";
    els.pricingTimestamp.value = currentDraft ? currentDraft.pricing_timestamp || "" : "";
    renderTimestampUtcPreviews();
  }

  function setFormFromOverlay(overlay) {
    setToggle(els.optionTypeToggle, overlay.option_type);
    setToggle(els.positionToggle, overlay.position);
    els.strikePrice.value = overlay.strike_price ?? "";
    els.notional.value = overlay.notional ?? "";
    els.volatility.value = overlay.volatility ?? "";
    els.forwardPrice.value = overlay.forward_clean_price_per_100 ?? "";
  }

  // The six trader-editable fields, read directly off the current draft
  // (kept in sync by applyManualInputsToDraft on every input change) rather
  // than re-reading the DOM -- the single source of truth for both what the
  // form shows and what gets sent to the existing overlay-shaped Bloomberg
  // refresh-and-price route.
  function extractOverlayFromDraft(draft) {
    return {
      option_type: draft.bond_option.option_type,
      position: draft.bond_option.position,
      strike_price: draft.bond_option.strike_price,
      notional: draft.bond_option.notional,
      volatility: draft.volatility_input.volatility,
      forward_clean_price_per_100: draft.forward_clean_price_input.forward_clean_price_per_100,
    };
  }

  // Writes the six Option Terms form controls into the current draft and
  // re-syncs gating. A no-op if there is no draft yet (nothing to write
  // into). Called on every relevant input's change/input event.
  // A blank field is "not entered", never an empty string the typed
  // constructors would reject with a confusing message.
  function textOrNull(raw) {
    const trimmed = (raw || "").trim();
    return trimmed === "" ? null : trimmed;
  }

  // settlement_lag_days and ex_dividend_days are `int` on their dataclasses
  // and explicitly reject a float/bool. Only a plain integer literal counts;
  // "1.5" is not silently truncated.
  function integerOrNull(raw) {
    const trimmed = (raw || "").trim();
    if (!/^-?\d+$/.test(trimmed)) return null;
    return Number.parseInt(trimmed, 10);
  }

  function selectValueOrNull(select) {
    return select.value === "" ? null : select.value;
  }

  // Reads every trader-entered value off the form into the current draft.
  // Performs no financial computation, no unit conversion, and no enum
  // inference: each value goes to the field the trader typed it into, and
  // the existing typed constructors remain the only validator.
  function applyManualInputsToDraft() {
    if (!currentDraft) return;

    const bondOption = currentDraft.bond_option;
    bondOption.option_type = getOptionalToggleValue(els.optionTypeToggle);
    bondOption.position = getOptionalToggleValue(els.positionToggle);
    bondOption.strike_price = numberOrNull(els.strikePrice.value);
    bondOption.notional = numberOrNull(els.notional.value);
    bondOption.expiry_date = textOrNull(els.expiryDate.value);
    bondOption.settlement_lag_days = integerOrNull(els.settlementLag.value);

    // The eight fields Bloomberg has no confirmed mnemonic for. The three
    // enum selects are the trader's own choice -- bond_master_raw's
    // DAY_CNT_DES / MTY_TYP / CALC_TYP_DES are displayed as a hint beside
    // them and are never read into the draft.
    const referenceData = currentDraft.bond_reference_data_universe[0];
    referenceData.day_count = selectValueOrNull(els.dayCount);
    referenceData.bond_type = selectValueOrNull(els.bondTypeSelect);
    referenceData.yield_convention = selectValueOrNull(els.yieldConvention);
    referenceData.business_day_convention = selectValueOrNull(els.businessDayConvention);
    referenceData.redemption_amount = numberOrNull(els.redemptionAmount.value);
    referenceData.ex_dividend_days = integerOrNull(els.exDividendDays.value);
    referenceData.last_coupon_date = textOrNull(els.lastCouponDate.value);
    referenceData.status = selectValueOrNull(els.bondStatus);

    // The three acquisition-derived values are deliberately not read back
    // from the DOM: their source is the recorded Bloomberg event, and editing
    // any unrelated trader control must never overwrite that provenance.
    currentDraft.reporting_date = textOrNull(els.reportingDate.value);
    currentDraft.forward_settlement_date = textOrNull(els.forwardSettlementDate.value);
    currentDraft.option_settlement_date = textOrNull(els.optionSettlementDate.value);
    currentDraft.expiry_timestamp = textOrNull(els.expiryTimestamp.value);

    currentDraft.volatility_input.volatility = numberOrNull(els.volatility.value);
    currentDraft.volatility_input.volatility_basis = selectValueOrNull(els.volatilityBasis);
    currentDraft.forward_clean_price_input.forward_clean_price_per_100 = numberOrNull(
      els.forwardPrice.value
    );

    currentDraft.curve_points = readCurveNodesFromRows();

    renderTimestampUtcPreviews();
    renderCurveCoverage();
    syncDraftGating();
  }

  // --- Datetime instant preview (Issue #143) ---------------------------------
  //
  // Transparency only. This preview never replaces or mutates what is sent:
  // every timestamp goes to the server exactly as typed. Only
  // `as_of_timestamp` is respelled in UTC at the contract boundary (its own
  // no-look-ahead check is documented as needing the UTC calendar date);
  // `pricing_timestamp` and `expiry_timestamp` keep their offset, because the
  // timing contract compares the *local* calendar date they represent against
  // the explicit date fields beside them.

  function utcInstantPreview(raw) {
    const text = (raw || "").trim();
    if (text === "") return { text: "—", instant: null, invalid: false };
    // Mirrors the server's own acceptance shape: canonical uppercase "T"
    // separator plus an explicit offset. Anything else is left to the
    // server's validator, and flagged here rather than silently "fixed".
    const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}(:\d{2}(\.\d+)?)?)(Z|[+-]\d{2}:\d{2})$/.exec(
      text
    );
    if (!match) {
      return {
        text: "Not a valid ISO-8601 datetime with an explicit offset (e.g. …T11:28:00+08:00)",
        instant: null,
        invalid: true,
      };
    }
    const parsed = new Date(text);
    if (Number.isNaN(parsed.getTime())) {
      return { text: "Not a valid datetime", instant: null, invalid: true };
    }
    const iso = parsed.toISOString().replace(/\.000Z$/, "Z");
    return { text: iso, instant: iso, invalid: false };
  }

  function renderTimestampUtcPreview(input, target, { normalized }) {
    const preview = utcInstantPreview(input.value);
    if (preview.instant === null) {
      target.textContent = preview.text;
    } else if (normalized) {
      target.textContent = `Normalized to UTC: ${preview.instant}`;
    } else {
      target.textContent = `Sent as entered · same instant in UTC: ${preview.instant}`;
    }
    target.classList.toggle("is-invalid", preview.invalid);
  }

  function renderTimestampUtcPreviews() {
    renderTimestampUtcPreview(els.asOfTimestamp, els.asOfTimestampUtc, { normalized: true });
    renderTimestampUtcPreview(els.pricingTimestamp, els.pricingTimestampUtc, {
      normalized: false,
    });
    renderTimestampUtcPreview(els.expiryTimestamp, els.expiryTimestampUtc, {
      normalized: false,
    });
  }

  const ACQUISITION_TIMESTAMP_PATTERN =
    /^(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/;

  function representedLocalCalendarDate(raw) {
    const text = (raw || "").trim();
    const match = ACQUISITION_TIMESTAMP_PATTERN.exec(text);
    if (!match || utcInstantPreview(text).invalid) return null;
    // This is intentionally the date written in the timestamp, not the UTC
    // date produced by JavaScript's Date object.
    return match[1];
  }

  function normalizeAsOfFromAcquisition(raw) {
    const text = (raw || "").trim();
    const match = ACQUISITION_TIMESTAMP_PATTERN.exec(text);
    if (!match) return textOrNull(text);
    // Match the approved Python boundary exactly for acquisition timestamps:
    // already-UTC spellings are preserved, while a genuinely non-zero offset
    // is respelled as the same instant in UTC.
    if (match[4] === "Z" || /^[+-]00:00$/.test(match[4])) return text;
    const preview = utcInstantPreview(text);
    return preview.instant === null ? text : preview.instant;
  }

  // --- Option Discount Curve editor (Issue #143) -----------------------------
  //
  // Builds the existing BLICurvePoint contract only: OPTION_DISCOUNT_CURVE
  // purpose, CONTINUOUS_ZERO_RATE basis, existing nD/nM/nY tenor grammar.
  // No curve is constructed, bootstrapped, converted, or extrapolated here,
  // and no OVME MMkt/repo rate is relabelled as a continuous zero rate.

  const CURVE_ID = "SHIORI_MANUAL_OPTION_DISCOUNT_CURVE";
  // Mirrors pricing/bli_curve_tenor.py's _TENOR_SHAPE exactly.
  const TENOR_SHAPE = /^([1-9][0-9]*)([DMY])$/;

  function tenorYearFraction(tenor) {
    const match = TENOR_SHAPE.exec((tenor || "").trim());
    if (!match) return null;
    const value = Number.parseInt(match[1], 10);
    if (match[2] === "D") return value / 365.0;
    if (match[2] === "M") return value / 12.0;
    return value;
  }

  function addCurveRow(tenor, rate) {
    const row = document.createElement("tr");
    row.className = "curve-row";

    const tenorCell = document.createElement("td");
    const tenorInput = document.createElement("input");
    tenorInput.className = "control curve-tenor-input";
    tenorInput.type = "text";
    tenorInput.placeholder = "3M";
    tenorInput.value = tenor || "";
    tenorCell.appendChild(tenorInput);

    const rateCell = document.createElement("td");
    const rateInput = document.createElement("input");
    rateInput.className = "control curve-rate-input";
    rateInput.type = "text";
    rateInput.inputMode = "decimal";
    rateInput.placeholder = "0.0374";
    rateInput.value = rate || "";
    rateCell.appendChild(rateInput);

    const removeCell = document.createElement("td");
    const remove = document.createElement("span");
    remove.className = "curve-row-remove";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      row.remove();
      applyManualInputsToDraft();
    });
    removeCell.appendChild(remove);

    row.appendChild(tenorCell);
    row.appendChild(rateCell);
    row.appendChild(removeCell);
    els.curveRows.appendChild(row);

    tenorInput.addEventListener("input", applyManualInputsToDraft);
    rateInput.addEventListener("input", applyManualInputsToDraft);
    return row;
  }

  function clearCurveRows() {
    els.curveRows.replaceChildren();
  }

  function readCurveRowInputs() {
    return Array.from(els.curveRows.querySelectorAll(".curve-row")).map((row) => ({
      tenor: (row.querySelector(".curve-tenor-input").value || "").trim(),
      rate: (row.querySelector(".curve-rate-input").value || "").trim(),
    }));
  }

  // Only fully-valid rows become curve points. A half-typed row is simply
  // not yet a node -- it is never guessed at, and never silently dropped
  // without the coverage line below saying so.
  function readCurveNodesFromRows() {
    const currency = currentDraft ? currentDraft.bond_option.currency : null;
    return readCurveRowInputs()
      .filter((row) => tenorYearFraction(row.tenor) !== null && numberOrNull(row.rate) !== null)
      .map((row) => ({
        curve_id: CURVE_ID,
        curve_name: CURVE_ID,
        currency: currency,
        curve_purpose: "OPTION_DISCOUNT_CURVE",
        tenor: row.tenor,
        rate: numberOrNull(row.rate),
        rate_basis: "CONTINUOUS_ZERO_RATE",
        source_system: MANUAL_SOURCE_SYSTEM,
        status: "ACTIVE",
      }));
  }

  function isoDateDaysBetween(fromIso, toIso) {
    const from = /^(\d{4})-(\d{2})-(\d{2})$/.exec(fromIso);
    const to = /^(\d{4})-(\d{2})-(\d{2})$/.exec(toIso);
    if (!from || !to) return null;
    const fromUtc = Date.UTC(Number(from[1]), Number(from[2]) - 1, Number(from[3]));
    const toUtc = Date.UTC(Number(to[1]), Number(to[2]) - 1, Number(to[3]));
    return Math.round((toUtc - fromUtc) / 86400000);
  }

  // Mirrors _option_discount_factor_to_date's own curve coordinate exactly:
  // (target - valuation).days / 365.0, with a same-day target needing no
  // node at all (its discount factor is 1.0 by definition).
  function curveCoordinateForDate(valuationDate, targetDate) {
    const days = isoDateDaysBetween(valuationDate, targetDate);
    if (days === null) return null;
    return days / 365.0;
  }

  function formatYears(value) {
    return value.toFixed(4);
  }

  // The exact blocking coverage state. Pricing is gated on this because the
  // reviewed interpolator rejects an out-of-range target outright rather
  // than flat-extrapolating -- so an uncovered date is a hard stop, never a
  // soft warning that still lets a fabricated discount factor through.
  function computeCurveCoverage(draft) {
    const rows = readCurveRowInputs();
    const invalidRows = rows.filter(
      (row) =>
        (row.tenor !== "" || row.rate !== "") &&
        (tenorYearFraction(row.tenor) === null || numberOrNull(row.rate) === null)
    );
    const nodes = (draft.curve_points || []).map((point) => ({
      tenor: point.tenor,
      years: tenorYearFraction(point.tenor),
    }));

    if (invalidRows.length > 0) {
      const shown = invalidRows
        .map((row) => `${row.tenor || "(blank tenor)"} / ${row.rate || "(blank rate)"}`)
        .join(", ");
      return {
        state: "blocking",
        message:
          `${invalidRows.length} incomplete or invalid node row(s) ignored: ${shown}. ` +
          "Tenor must match nD / nM / nY (e.g. 3M, 18M, 2Y) and the rate must be a " +
          "decimal continuous zero rate.",
      };
    }
    if (nodes.length < 2) {
      return {
        state: "blocking",
        message: `At least 2 valid curve nodes are required; ${nodes.length} entered.`,
      };
    }

    const yearFractions = nodes.map((node) => node.years);
    const lowest = Math.min(...yearFractions);
    const highest = Math.max(...yearFractions);
    const span = `[${formatYears(lowest)}, ${formatYears(highest)}] years`;

    if (!draft.valuation_date) {
      return {
        state: "pending",
        message: `Nodes span ${span}. Enter a valuation date to check date coverage.`,
      };
    }

    // Exactly the two dates the standalone pricing path discounts to.
    const targets = [
      ["Reporting Date", draft.reporting_date],
      ["Option Settlement Date", draft.option_settlement_date],
    ];
    const problems = [];
    const pending = [];
    for (const [label, targetDate] of targets) {
      if (!targetDate) {
        pending.push(label);
        continue;
      }
      const coordinate = curveCoordinateForDate(draft.valuation_date, targetDate);
      if (coordinate === null) continue;
      if (coordinate < 0) {
        problems.push(`${label} (${targetDate}) is before the valuation date.`);
        continue;
      }
      // A same-day target needs no node: its discount factor is 1.0.
      if (coordinate === 0) continue;
      if (coordinate < lowest || coordinate > highest) {
        problems.push(
          `${label} (${targetDate}) needs ${formatYears(coordinate)} years, outside the ` +
            `node range ${span}. Add a node at or beyond ${formatYears(coordinate)} years ` +
            "— the curve is interpolated in-range only and is never extrapolated."
        );
      }
    }

    if (problems.length > 0) {
      return { state: "blocking", message: problems.join(" ") };
    }
    if (pending.length > 0) {
      return {
        state: "pending",
        message: `Nodes span ${span}. Enter ${pending.join(" and ")} to check date coverage.`,
      };
    }
    return {
      state: "covered",
      message: `${nodes.length} nodes spanning ${span} cover every date this request discounts to.`,
    };
  }

  function renderCurveCoverage() {
    if (!currentDraft) {
      els.curveCoverage.textContent = "—";
      els.curveCoverage.classList.remove("is-blocking", "is-covered");
      return;
    }
    const coverage = computeCurveCoverage(currentDraft);
    els.curveCoverage.textContent = coverage.message;
    els.curveCoverage.classList.toggle("is-blocking", coverage.state === "blocking");
    els.curveCoverage.classList.toggle("is-covered", coverage.state === "covered");
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

  // The single, unified failure path: used for a FAILED PricingResult, an
  // HTTP 400/non-2xx bridge response, a network-level fetch rejection, and
  // a non-JSON response body alike. Never leaves a prior successful result
  // or its green "loaded" status on screen; never touches currentDraft, so
  // the trader can fix the form and retry.
  function renderFailure(message) {
    clearResultFields();
    els.errorBanner.textContent = message;
    els.errorBanner.hidden = false;
    els.statusIndicator.classList.add("failed");
    els.statusText.textContent = "Pricing failed";
  }

  // A Bloomberg failure -- whether an instrument-first bond lookup or the
  // existing quote-refresh-and-price path -- must preserve the previously
  // resolved bond, draft, and completed display exactly as they were; only
  // the banner communicates that this particular attempt failed.
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
    els.statusText.textContent = "Draft priced";

    els.priceTotal.textContent = fmt(display.total_notional_model_fair_premium);
    els.priceTotalCcy.textContent = display.result_currency || "";
    els.pricePer100.textContent = fmt(display.model_fair_premium_per_100);
    els.resultCurrency.textContent = display.result_currency || "—";
    els.greekDelta.textContent = fmt(display.forward_price_delta_per_100);
    els.greekGamma.textContent = fmt(display.forward_price_gamma_per_100);
    els.greekVega.textContent = fmt(display.vega_per_vol_point_per_100);
    els.greekTheta.textContent = fmt(display.theta_per_calendar_day_per_100);
  }

  // Renders the resolved Bloomberg bond identity -- the instrument-first
  // lookup's own result. Passing null (only ever done by Clear) hides the
  // panel entirely.
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
    setCouponPercentOrNotAvailable(els.resolvedBondCoupon, resolvedBloombergBond.bond_master.coupon);
    setTextOrNotAvailable(els.resolvedBondMaturity, resolvedBloombergBond.bond_master.maturity_date);
    els.resolvedBondAcquiredAt.textContent = resolvedBloombergBond.acquired_at;
    els.resolvedBondSource.textContent = resolvedBloombergBond.source_system;
  }

  // Renders the Instrument Details card as the Bloomberg Bond Master
  // display (PR #141 third revision) -- every field not yet confirmed via
  // tools/bloomberg_dapi_probe.py (see
  // shiori_pricing_lab.data.bloomberg_bond_quote._BOND_MASTER_FIELD_MAP)
  // shows "Not available", never a fabricated or synthetic value.
  // bond.bond_master_raw carries three further Bloomberg mnemonics
  // (day count / maturity type / calculation type) that are confirmed to
  // return a value but are display-only -- shown in their own "Bloomberg
  // ..." labeled rows, never written into the typed schema fields above.
  // Shown immediately once a bond is resolved (see syncDraftGating),
  // independent of whether the draft can yet be priced.
  function renderBondMaster(bond) {
    const bondMaster = bond.bond_master || {};
    const bondMasterRaw = bond.bond_master_raw || {};
    els.detailsIssuer.textContent = bond.name;
    els.detailsIsin.textContent = bond.isin;
    els.detailsCusip.textContent = bond.cusip;
    els.detailsCurrency.textContent = bond.currency;
    setCouponPercentOrNotAvailable(els.detailsCoupon, bondMaster.coupon);
    setTextOrNotAvailable(els.detailsCouponFrequency, bondMaster.coupon_frequency);
    setTextOrNotAvailable(els.detailsIssueDate, bondMaster.issue_date);
    setTextOrNotAvailable(els.detailsMaturity, bondMaster.maturity_date);
    setTextOrNotAvailable(els.detailsDayCount, bondMaster.day_count);
    setTextOrNotAvailable(els.detailsFirstCouponDate, bondMaster.first_coupon_date);
    setTextOrNotAvailable(els.detailsLastCouponDate, bondMaster.last_coupon_date);
    setTextOrNotAvailable(els.detailsRedemptionAmount, bondMaster.redemption_amount);
    setTextOrNotAvailable(els.detailsCallable, bondMaster.callable_flag);
    setTextOrNotAvailable(els.detailsSinkable, bondMaster.sinkable_flag);
    setTextOrNotAvailable(els.detailsBondType, bondMaster.bond_type);
    setTextOrNotAvailable(els.detailsYieldConvention, bondMaster.yield_convention);
    setTextOrNotAvailable(els.detailsBusinessDayConvention, bondMaster.business_day_convention);
    setTextOrNotAvailable(els.detailsBloombergDayCount, bondMasterRaw.day_count);
    setTextOrNotAvailable(els.detailsBloombergMaturityType, bondMasterRaw.maturity_type);
    setTextOrNotAvailable(els.detailsBloombergCalcType, bondMasterRaw.calc_type);
    els.detailsSource.textContent = bond.source_system;
    els.detailsAcquiredAt.textContent = bond.acquired_at;
  }

  function clearBondMaster() {
    [
      els.detailsIssuer,
      els.detailsIsin,
      els.detailsCusip,
      els.detailsCurrency,
      els.detailsSource,
      els.detailsAcquiredAt,
    ].forEach((el) => {
      el.textContent = "—";
    });
    [
      els.detailsCoupon,
      els.detailsCouponFrequency,
      els.detailsIssueDate,
      els.detailsMaturity,
      els.detailsDayCount,
      els.detailsFirstCouponDate,
      els.detailsLastCouponDate,
      els.detailsRedemptionAmount,
      els.detailsCallable,
      els.detailsSinkable,
      els.detailsBondType,
      els.detailsYieldConvention,
      els.detailsBusinessDayConvention,
      els.detailsBloombergDayCount,
      els.detailsBloombergMaturityType,
      els.detailsBloombergCalcType,
    ].forEach((el) => setTextOrNotAvailable(el, null));
  }

  // Renders the bounded, verbatim live_bloomberg_quote section (see
  // prepare_live_bloomberg_quote_display in standalone_option_workbench.py)
  // -- present only on a display produced by the existing Bloomberg
  // refresh-and-price path. Folds its currency/price/accrued/acquisition
  // fields into the same Underlying Bond panel the instrument-first lookup
  // populates, since both describe the one Bloomberg bond currently
  // selected.
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

  // Renders the actionable category summary (always visible once
  // incomplete) plus the full per-field detail list (collapsed by default
  // -- "Show details" reveals it). `missing` is computeMissingDraftFields's
  // flat {label, category} list.
  function resetMissingDetails() {
    els.missingFieldsList.hidden = true;
    els.missingDetailsToggleBtn.textContent = "Show details";
  }

  function renderMissingFieldsSummary(missing) {
    const count = missing.length;
    els.remainingInputSummary.textContent =
      count === 1
        ? "1 required input still unresolved"
        : `${count} required inputs still unresolved`;
    const byCategory = new Map();
    missing.forEach(({ label, category }) => {
      if (!byCategory.has(category)) byCategory.set(category, []);
      byCategory.get(category).push(label);
    });

    els.missingCategories.innerHTML = "";
    MISSING_CATEGORY_ORDER.filter((category) => byCategory.has(category)).forEach((category) => {
      const labels = byCategory.get(category);
      const badge = document.createElement("span");
      badge.className = "missing-category-badge";
      badge.textContent = `${category} (${labels.length})`;
      els.missingCategories.appendChild(badge);
    });

    els.missingFieldsList.innerHTML = "";
    missing.forEach(({ label, category }) => {
      const item = document.createElement("li");
      const link = document.createElement("button");
      link.type = "button";
      link.className = "missing-field-link";
      link.textContent = `${category}: ${label}`;
      link.addEventListener("click", () => revealMissingField(label, category));
      item.appendChild(link);
      els.missingFieldsList.appendChild(item);
    });
  }

  // Recomputes every visual/logic consequence of the current draft: whether
  // the pricing form is shown at all (as soon as a draft exists, so the
  // trader can start filling it in), whether the instrument header/details
  // (which need a genuinely completed price) are shown, the concrete
  // missing-input list, and whether Price/Refresh Bloomberg & Price are
  // enabled. Called after every lookup, form edit, Price/Refresh outcome,
  // and Clear.
  function syncDraftGating() {
    const hasDraft = !!currentDraft;
    const missing = hasDraft ? computeMissingDraftFields(currentDraft) : [];
    const incomplete = hasDraft && missing.length > 0;
    const hasResult = currentDisplay !== null;

    els.workspaceSection.hidden = !hasDraft;
    els.instrumentHeaderSection.hidden = !hasResult;
    // Instrument Details shows Bloomberg's own Bond Master data (PR #141
    // third revision) -- available the moment a bond is resolved, well
    // before a complete/priced request exists, unlike the instrument
    // header above (which shows the priced case's own context).
    els.instrumentDetailsSection.hidden = !hasDraft;

    els.draftIncompleteNote.hidden = !incomplete;
    renderMissingFieldsSummary(missing);
    // The manual-completion section headers read the same missing list, so
    // their counts can never disagree with the badges or the Price gate.
    collapsibleSections.forEach((section) => section.refreshSummary());

    els.priceBtn.classList.toggle("is-disabled", !hasDraft || incomplete);
    els.bloombergRefreshBtn.classList.toggle("is-disabled", !hasDraft || incomplete);
  }

  // Auto-populated provenance and the Bloomberg raw descriptions, shown so
  // the trader can see exactly what Shiori set and what Bloomberg actually
  // returned. The raw descriptions are hints beside the enum selects and are
  // never written into the typed draft.
  function renderManualCompletionContext() {
    const metadata = (source, status) => (source && status ? `${source} · ${status}` : "—");
    els.timingProductId.textContent = currentDraft ? currentDraft.bond_option.product_id : "—";
    els.timingSnapshotId.textContent = currentDraft ? currentDraft.snapshot_id : "—";
    els.timingSnapshotMetadata.textContent = currentDraft
      ? metadata(currentDraft.source_system, currentDraft.snapshot_status)
      : "—";
    els.timingBondQuoteMetadata.textContent = currentDraft
      ? metadata(currentDraft.bond_quote.source_system, currentDraft.bond_quote.status)
      : "—";
    els.timingForwardMetadata.textContent = currentDraft
      ? metadata(
          currentDraft.forward_clean_price_input.source_system,
          currentDraft.forward_clean_price_input.status
        )
      : "—";
    els.timingVolatilityMetadata.textContent = currentDraft
      ? metadata(currentDraft.volatility_input.source_system, currentDraft.volatility_input.status)
      : "—";
    els.timingCreditMetadata.textContent = currentDraft
      ? `${currentDraft.credit_spread_input.spread_treatment} · ${metadata(
          currentDraft.credit_spread_input.source_system,
          currentDraft.credit_spread_input.status
        )}`
      : "—";
    els.curveCurrencyLabel.textContent = currentDraft
      ? currentDraft.bond_option.currency
      : "—";
    const raw = (resolvedBloombergBond && resolvedBloombergBond.bond_master_raw) || {};
    setTextOrNotAvailable(els.hintDayCount, raw.day_count);
    setTextOrNotAvailable(els.hintBondType, raw.maturity_type);
    setTextOrNotAvailable(els.hintYieldConvention, raw.calc_type);
  }

  // Trader-draft revision (Issue #140 second revision): builds a brand-new
  // draft containing only the fields Bloomberg itself returned for this
  // bond -- every other required field starts null. Never reads from, or
  // is seeded by, any prior draft or the bundled synthetic fixture (never
  // loaded by this file at all any more).
  // --- Truthful provenance labels (Issue #143) -------------------------------
  //
  // Every auto-populated value is labelled for what it actually is. None of
  // these claims a market source Shiori does not have: the forward, the
  // volatility and the curve nodes are transcribed by the trader, and the
  // credit-spread state is a route policy statement, not observed data.
  const MANUAL_SOURCE_SYSTEM = "MANUAL_TRADER_ENTRY";
  const SNAPSHOT_SOURCE_SYSTEM = "SHIORI_MANUAL_WORKBENCH";
  const ROUTE_POLICY_SOURCE_SYSTEM = "SHIORI_ROUTE_POLICY";
  // BLICreditSpreadInput requires a non-blank audit explanation whenever the
  // treatment is NOT_REQUIRED -- "not required" is an audited decision, never
  // a silent assumption. This states the verified fact: the standalone
  // explicit-forward pricing path never reads credit_spread.
  const CREDIT_SPREAD_NOT_REQUIRED_AUDIT =
    "Explicit-forward OVME-aligned standalone route: the reviewed pricing path " +
    "discounts the option payoff on the Option Discount Curve and never reads " +
    "credit_spread, so no spread is applied and none is fabricated.";

  function shioriIdentifierSuffix(bond) {
    return `${bond.isin}-${(bond.acquired_at || "").replace(/[^0-9A-Za-z]/g, "")}`;
  }

  function buildInitialDraftFromBloomberg(bond) {
    // Bond Master (PR #141 third revision): populated from bond.bond_master
    // only -- never bond.bond_master_raw, which is display-only and must
    // never enter the typed schema. Every destination key is always
    // present, null unless Eddy has confirmed that field's Bloomberg
    // mnemonic (see
    // shiori_pricing_lab.data.bloomberg_bond_quote._BOND_MASTER_FIELD_MAP).
    // ex_dividend_days and status have no Bloomberg mnemonic candidate yet
    // and stay null here regardless.
    const bondMaster = bond.bond_master || {};
    const identifierSuffix = shioriIdentifierSuffix(bond);
    const pricingTimestamp = textOrNull(bond.acquired_at);
    return {
      bond_option: {
        // payoff_basis / exercise_style / settlement_type are the only
        // values the reviewed pricing guard accepts for this route, so they
        // are set rather than asked for. product_id is a Shiori identifier,
        // not market data.
        product_id: `SHIORI-WORKBENCH-${identifierSuffix}`,
        underlying_isin: bond.isin,
        currency: bond.currency,
        payoff_basis: "PRICE",
        option_type: null,
        exercise_style: "EUROPEAN",
        settlement_type: "CASH",
        settlement_lag_days: null,
        expiry_date: null,
        notional: null,
        position: null,
        strike_price: null,
        strike_yield: null,
        exercise_start_date: null,
      },
      bond_reference_data_universe: [
        {
          isin: bond.isin,
          issuer: bond.name,
          currency: bond.currency,
          coupon: bondMaster.coupon ?? null,
          coupon_frequency: bondMaster.coupon_frequency ?? null,
          maturity_date: bondMaster.maturity_date ?? null,
          issue_date: bondMaster.issue_date ?? null,
          day_count: bondMaster.day_count ?? null,
          business_day_convention: bondMaster.business_day_convention ?? null,
          redemption_amount: bondMaster.redemption_amount ?? null,
          callable_flag: bondMaster.callable_flag ?? null,
          sinkable_flag: bondMaster.sinkable_flag ?? null,
          bond_type: bondMaster.bond_type ?? null,
          yield_convention: bondMaster.yield_convention ?? null,
          ex_dividend_days: null,
          first_coupon_date: bondMaster.first_coupon_date ?? null,
          last_coupon_date: bondMaster.last_coupon_date ?? null,
          status: null,
        },
      ],
      // The acquisition event is already the source for these three values.
      // Its timestamp is preserved exactly for pricing, its represented local
      // date supplies valuation_date, and only as_of_timestamp is respelled
      // under the approved UTC normalization rule. No settlement or expiry
      // date is inferred.
      valuation_date: representedLocalCalendarDate(pricingTimestamp),
      as_of_timestamp: normalizeAsOfFromAcquisition(pricingTimestamp),
      pricing_timestamp: pricingTimestamp,
      expiry_timestamp: null,
      reporting_date: null,
      forward_settlement_date: null,
      option_settlement_date: null,
      source_system: SNAPSHOT_SOURCE_SYSTEM,
      snapshot_id: `SHIORI-SNAPSHOT-${identifierSuffix}`,
      // ACTIVE is the only status the snapshot contract accepts at
      // construction; MANUAL_VERIFIED needs an audit policy that does not
      // exist yet (see the note in the timing section).
      snapshot_status: "ACTIVE",
      bond_quote: {
        isin: bond.isin,
        currency: bond.currency,
        // Bloomberg returned a clean price, so the quote's primary basis is
        // PRICE -- a label for what was actually loaded, not a conversion.
        price_type: "PRICE",
        quote_side: bond.quote_side,
        source_system: bond.source_system,
        status: "ACTIVE",
        clean_price_per_100: bond.clean_price_per_100,
        yield_value: null,
        accrued_interest_per_100: bond.accrued_interest_per_100,
      },
      forward_clean_price_input: {
        forward_clean_price_per_100: null,
        // The contract requires the forward's side to equal the spot side,
        // so it is mirrored mechanically rather than asked for twice.
        quote_side: bond.quote_side,
        source_system: MANUAL_SOURCE_SYSTEM,
        status: "ACTIVE",
      },
      curve_points: [],
      volatility_input: {
        volatility: null,
        volatility_basis: "PRICE_VOL",
        source_system: MANUAL_SOURCE_SYSTEM,
        status: "ACTIVE",
        override_or_fallback_audit: null,
      },
      credit_spread_input: {
        // Verified in #142: the standalone pricing path never reads
        // credit_spread, so NOT_REQUIRED is the truthful state. The contract
        // demands credit_spread/credit_spread_basis stay null for this
        // treatment and requires the audit text below.
        spread_treatment: "NOT_REQUIRED",
        source_system: ROUTE_POLICY_SOURCE_SYSTEM,
        status: "ACTIVE",
        credit_spread: null,
        credit_spread_basis: null,
        override_or_fallback_audit: CREDIT_SPREAD_NOT_REQUIRED_AUDIT,
      },
      deposit_rate_observation: null,
      bond_reference_source_name: null,
    };
  }

  // Instrument-first Bloomberg bond lookup: resolves one bond's own
  // identity and one quote side's price via /api/bloomberg/bond -- no
  // active case involved, no expected-ISIN check. On success this always
  // starts a brand-new draft (never retaining any prior draft's inputs) and
  // shows the pricing form immediately.
  async function loadBloombergBond() {
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
    invalidatePendingPriceRequest();
    invalidatePendingBloombergRequest();

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
      // superseded -- either way, a failed lookup must preserve the
      // previously resolved bond, draft, and completed screen exactly as
      // they were.
      if (isStaleBondLookupRequest(generation)) return;
      renderBloombergError("Bloomberg bond lookup failed: " + err.message);
      return;
    }
    if (isStaleBondLookupRequest(generation)) return;
    if (!response.ok) {
      renderBloombergError(payload.error || "Bloomberg bond lookup failed.");
      return;
    }

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
      // Bond Master (PR #141 third revision): every field here is either a
      // confirmed Bloomberg mnemonic's real value or null pending Eddy's own
      // real-DAPI confirmation (see tools/bloomberg_dapi_probe.py) -- never
      // guessed or filled from the synthetic fixture. `payload.bond_master`
      // always carries every destination key regardless of how many are
      // currently confirmed. `payload.bond_master_raw` carries the three
      // confirmed-but-display-only Bloomberg mnemonics (day count/maturity
      // type/calc type) -- these must never be read into currentDraft's
      // typed schema, only shown verbatim in Instrument Details.
      bond_master: payload.bond_master || {},
      bond_master_raw: payload.bond_master_raw || {},
    };

    // A fresh lookup intentionally invalidates any prior draft/result -- it
    // belongs to another bond -- and never copies a bond-specific or market
    // input from it.
    currentDraft = buildInitialDraftFromBloomberg(resolvedBloombergBond);
    setCurrentDisplay(null);
    hideContext();
    clearResultFields();
    clearOptionTermsForm();
    setDerivedTimingFormFromDraft();
    resetMissingDetails();
    renderManualCompletionContext();

    els.errorBanner.hidden = true;
    els.errorBanner.textContent = "";
    els.statusIndicator.classList.remove("failed");
    els.statusText.textContent = "Bloomberg bond loaded";
    renderResolvedBondPanel();
    renderBondMaster(resolvedBloombergBond);
    syncDraftGating();
  }

  // Prices the current draft through the existing reviewed builder --
  // reuses POST /api/case unchanged (the same route the developer-only
  // Case JSON workflow already used) rather than adding any new pricing
  // route or logic: a full case dict in, {case, overlay, context, display}
  // out. Disabled (see syncDraftGating) until the draft has no missing
  // required field; this direct guard is defense-in-depth against a forced
  // click bypassing the CSS state.
  async function priceCurrentDraft() {
    if (!currentDraft) return;
    if (computeMissingDraftFields(currentDraft).length > 0) return;

    const generation = beginRequest();
    invalidatePendingBondLookupRequest();
    invalidatePendingBloombergRequest();

    if (inFlightPriceController) {
      inFlightPriceController.abort();
    }
    const controller = new AbortController();
    inFlightPriceController = controller;

    let response;
    let payload;
    try {
      response = await fetch("/api/case", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentDraft),
        signal: controller.signal,
      });
      payload = await response.json();
    } catch (err) {
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

    currentDraft = payload.case;
    renderContext(payload.context);
    setFormFromOverlay(extractOverlayFromDraft(currentDraft));
    setCurrentDisplay(payload.display);
    renderDisplay(payload.display);
    syncDraftGating();
  }

  function clearDraft() {
    invalidatePendingPriceRequest();
    invalidatePendingBloombergRequest();
    invalidatePendingBondLookupRequest();

    // Clear removes the selected Bloomberg bond and the current draft
    // entirely -- it must never restore or display the bundled synthetic
    // case, which this file no longer loads at all.
    resolvedBloombergBond = null;
    currentDraft = null;
    setCurrentDisplay(null);
    renderResolvedBondPanel();
    clearBondMaster();
    hideContext();
    clearResultFields();
    clearOptionTermsForm();
    resetMissingDetails();

    els.errorBanner.hidden = true;
    els.errorBanner.textContent = "";
    els.statusIndicator.classList.remove("failed");
    els.statusText.textContent = "No bond loaded";
    renderManualCompletionContext();
    syncDraftGating();
    resetCollapseStates();
  }

  // Bloomberg quote refresh: prices the current draft with one fresh
  // Bloomberg bond quote in place of its own -- exactly like Price, except
  // the bridge substitutes one live quote before pricing. Reuses the
  // existing POST /api/case/bloomberg route unchanged: the resolved bond's
  // qualified identifier is sent as `bloomberg_security` (Bloomberg DAPI
  // accepts it natively) and the six trader-editable fields are read
  // straight off the draft as the route's existing `overlay` shape. Only
  // runs once the draft is complete (see syncDraftGating), same gate as
  // Price. Its response carries no separate context section (unlike
  // POST /api/case), so context is derived client-side from the
  // already-complete draft via extractContextFromDraft.
  async function refreshBloombergAndPrice() {
    if (!currentDraft || !resolvedBloombergBond) return;
    if (computeMissingDraftFields(currentDraft).length > 0) return;

    const quoteSide = getOptionalToggleValue(els.bondQuoteSideToggle);
    if (!quoteSide) {
      renderBloombergError("Select a Quote Side before refreshing.");
      return;
    }

    const overlay = extractOverlayFromDraft(currentDraft);
    const generation = beginBloombergRequest();
    invalidatePendingPriceRequest();
    invalidatePendingBondLookupRequest();

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
          case: currentDraft,
          overlay,
          bloomberg_security: resolvedBloombergBond.qualifiedIdentifier,
          quote_side: quoteSide,
        }),
        signal: controller.signal,
      });
      payload = await response.json();
    } catch (err) {
      if (isStaleBloombergRequest(generation)) return;
      renderBloombergError("Bloomberg refresh failed: " + err.message);
      return;
    }
    if (isStaleBloombergRequest(generation)) return;
    if (!response.ok) {
      renderBloombergError(payload.error || "Bloomberg refresh failed.");
      return;
    }

    renderContext(extractContextFromDraft(currentDraft));
    setCurrentDisplay(payload);
    renderDisplay(payload);
    syncDraftGating();
  }

  // Download the current run as JSON/Markdown, reusing only the existing
  // pure export helpers server-side. Sends exactly the display dict active
  // at click time. Codex review (PR #139): a Price/Refresh/Clear/lookup
  // action can change currentDisplay while this request is still in
  // flight, so the response is checked against displayGeneration before it
  // is ever turned into a download.
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
      applyManualInputsToDraft();
    }
  });
  els.positionToggle.addEventListener("click", (event) => {
    const opt = event.target.closest(".opt");
    if (opt) {
      setToggle(els.positionToggle, opt.dataset.value);
      applyManualInputsToDraft();
    }
  });
  els.bondQuoteSideToggle.addEventListener("click", (event) => {
    const opt = event.target.closest(".opt");
    if (opt) {
      setToggle(els.bondQuoteSideToggle, opt.dataset.value);
    }
  });
  MANUAL_TEXT_INPUTS().forEach((input) => {
    input.addEventListener("input", applyManualInputsToDraft);
  });
  MANUAL_SELECTS().forEach((select) => {
    select.addEventListener("change", applyManualInputsToDraft);
  });
  els.volatilityBasis.addEventListener("change", applyManualInputsToDraft);
  els.curveAddRowBtn.addEventListener("click", () => {
    addCurveRow("", "");
    applyManualInputsToDraft();
  });

  els.priceBtn.addEventListener("click", priceCurrentDraft);
  els.clearBtn.addEventListener("click", clearDraft);
  els.downloadJsonBtn.addEventListener("click", () => downloadCurrentRun("json"));
  els.downloadMarkdownBtn.addEventListener("click", () => downloadCurrentRun("markdown"));
  els.bloombergRefreshBtn.addEventListener("click", refreshBloombergAndPrice);
  els.loadBloombergBondBtn.addEventListener("click", loadBloombergBond);
  // Full per-field missing-input detail is collapsed by default -- only the
  // category summary shows until the trader asks for more.
  els.missingDetailsToggleBtn.addEventListener("click", () => {
    const expanded = els.missingFieldsList.hidden;
    els.missingFieldsList.hidden = !expanded;
    els.missingDetailsToggleBtn.textContent = expanded ? "Hide details" : "Show details";
  });

  // No bootstrap: the trader-draft revision starts with nothing loaded at
  // all (Issue #140 second revision, requirement 1) -- syncDraftGating()
  // establishes the correct all-hidden/all-disabled initial state without
  // any network call.
  syncDraftGating();

  // Test-only, read-only accessor: changes no stored value, API response,
  // conversion, or pricing behavior -- it only lets a browser test confirm
  // that currentDraft's own coupon stays a decimal fraction (e.g. 0.0375)
  // even though the UI now displays it as a percentage.
  window.__shioriTestGetCurrentDraft = () => currentDraft;
  // Read-only too: lets a browser test assert the checklist's own conditional
  // rules directly (notably BLICreditSpreadInput's treatment-dependent
  // requirements) without having to drive a UI that deliberately never
  // offers a credit-spread control on this route.
  window.__shioriTestComputeMissingLabels = () =>
    currentDraft ? computeMissingDraftFields(currentDraft).map((item) => item.label) : [];
})();
