// Minimum-input trader workflow (Issue #143, Milestone 1/2).
//
// This file performs no pricing, discounting, accrual, scaling, or Greek math
// of any kind -- it only reads/writes trader-editable controls, calls the local
// HTTP bridge (see
// src/shiori_pricing_lab/app/standalone_option_workbench_server.py), and
// renders the returned display dict verbatim. Every numeric value shown is
// formatted with toFixed(6), the same display precision the existing Streamlit
// workbench uses -- never rounded, rescaled, or re-signed.
//
// **What changed for #143.** The page no longer presents the backend contract
// as a form. The main screen carries exactly nine ordinary workflow groups --
// instrument, Call/Put, Buy/Sell, strike, notional, one expiry interaction, and
// a Forward / Vol / Discounting review each -- and everything else is either
// sourced read-only, mechanically derived, or a provenance-stamped override
// inside one collapsed Advanced section.
//
// **Classification is not guessed here.** Every field's placement traces to the
// #145 audit conclusions and the #149 Bloomberg workstation evidence, as
// catalogued in tools/bloomberg_input_sourcing_probe.py's INPUT_ROWS:
//
//   - BLOOMBERG_AUTO (confirmed DAPI mnemonics, PR #141): coupon, coupon
//     frequency, issue/maturity/first-coupon dates, callable/sinkable flags,
//     clean price, accrued interest, ISIN/CUSIP/issuer/currency -- read-only.
//   - SHIORI_DERIVED (already-approved acquisition-time contract): pricing
//     timestamp, valuation date, as-of timestamp -- read-only.
//   - Route-fixed: PRICE / EUROPEAN / CASH, the Shiori product and snapshot
//     identifiers, the forward's quote side (contractually equal to the spot
//     side), and the NOT_REQUIRED credit-spread policy -- never asked for.
//   - APPROVED_PROFILE_REQUIRED, with no approved UST/Gilt profile yet: day
//     count, bond type, ex-dividend days, status. SHIORI_DERIVED_CANDIDATE,
//     with no approved generator/route decision yet: last coupon date,
//     reporting date. ADVANCED_OVERRIDE_REQUIRED: forward and option
//     settlement dates. All of these live in Advanced as trader overrides
//     with provenance -- Shiori sets none of them from a Bloomberg string.
//   - ADVANCED_OVERRIDE_REQUIRED / UNRESOLVED market inputs: forward clean
//     price, direct price vol, and the Option Discount Curve. These are the
//     three main-screen review groups, each stating the real Bloomberg
//     evidence for why it cannot be sourced.
//
// **Nothing here maps a Bloomberg description into a typed enum.**
// DAY_CNT_DES, DAY_CNT, MTY_TYP, CALC_TYP_DES, SECURITY_TYP, CPN_TYP and
// PREV_CPN_DT are rendered as display-only evidence beside the control they
// are evidence for, and are never read into the draft.

(function () {
  "use strict";

  const els = {
    // Instrument group
    bondIdentifierInput: document.getElementById("bond-identifier-input"),
    bondQuoteSideToggle: document.getElementById("bond-quote-side-toggle"),
    loadBloombergBondBtn: document.getElementById("load-bloomberg-bond-btn"),
    bloombergRefreshBtn: document.getElementById("bloomberg-refresh-btn"),
    instrumentGroupStatus: document.getElementById("instrument-group-status"),
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

    // Unresolved dependency panel
    unresolvedPanel: document.getElementById("unresolved-dependency"),
    unresolvedTitle: document.getElementById("unresolved-title"),
    unresolvedMissing: document.getElementById("unresolved-missing"),
    unresolvedWhy: document.getElementById("unresolved-why"),
    unresolvedEvidence: document.getElementById("unresolved-evidence"),
    unresolvedNext: document.getElementById("unresolved-next"),
    unresolvedGotoBtn: document.getElementById("unresolved-goto-btn"),
    unresolvedAlso: document.getElementById("unresolved-also"),

    // Trade group
    optionTypeToggle: document.getElementById("option-type-toggle"),
    positionToggle: document.getElementById("position-toggle"),
    strikePrice: document.getElementById("strike-price-input"),
    notional: document.getElementById("notional-input"),
    expiryDatetime: document.getElementById("expiry-datetime-input"),
    expiryOffset: document.getElementById("expiry-offset-input"),
    expiryDerivedPreview: document.getElementById("expiry-derived-preview"),

    // Market review groups
    marketReviewSummary: document.getElementById("market-review-summary"),
    forwardPrice: document.getElementById("forward-price-input"),
    forwardReviewStatus: document.getElementById("forward-review-status"),
    forwardSourcingNote: document.getElementById("forward-sourcing-note"),
    forwardProvenance: document.getElementById("forward-provenance"),
    volatility: document.getElementById("volatility-input"),
    volatilityBasis: document.getElementById("volatility-basis-select"),
    volReviewStatus: document.getElementById("vol-review-status"),
    volSourcingNote: document.getElementById("vol-sourcing-note"),
    volProvenance: document.getElementById("vol-provenance"),
    discountingReviewStatus: document.getElementById("discounting-review-status"),
    discountingReadout: document.getElementById("discounting-readout"),
    discountingSourcingNote: document.getElementById("discounting-sourcing-note"),
    discountingProvenance: document.getElementById("discounting-provenance"),
    editCurveBtn: document.getElementById("edit-curve-btn"),

    // Results
    errorBanner: document.getElementById("pricing-error-banner"),
    priceTotal: document.getElementById("price-total"),
    priceTotalCcy: document.getElementById("price-total-ccy"),
    pricePer100: document.getElementById("price-per-100"),
    resultCurrency: document.getElementById("result-currency"),
    greekDelta: document.getElementById("greek-delta"),
    greekGamma: document.getElementById("greek-gamma"),
    greekVega: document.getElementById("greek-vega"),
    greekTheta: document.getElementById("greek-theta"),

    // Advanced
    advancedHead: document.getElementById("advanced-head"),
    advancedBody: document.getElementById("advanced-body"),
    advancedIndicator: document.getElementById("advanced-indicator"),
    advancedSummary: document.getElementById("advanced-summary"),
    dayCount: document.getElementById("day-count-select"),
    bondTypeSelect: document.getElementById("bond-type-select"),
    exDividendDays: document.getElementById("ex-dividend-days-input"),
    lastCouponDate: document.getElementById("last-coupon-date-input"),
    bondStatus: document.getElementById("bond-status-select"),
    hintDayCount: document.getElementById("hint-day-count"),
    hintBondType: document.getElementById("hint-bond-type"),
    hintLastCouponDate: document.getElementById("hint-last-coupon-date"),
    reportingDate: document.getElementById("reporting-date-input"),
    forwardSettlementDate: document.getElementById("forward-settlement-date-input"),
    optionSettlementDate: document.getElementById("option-settlement-date-input"),
    curveRows: document.getElementById("curve-rows"),
    curveAddRowBtn: document.getElementById("curve-add-row-btn"),
    curveCoverage: document.getElementById("curve-coverage"),
    curveCurrencyLabel: document.getElementById("curve-currency-label"),
    valuationDate: document.getElementById("valuation-date-input"),
    asOfTimestamp: document.getElementById("as-of-timestamp-input"),
    asOfTimestampUtc: document.getElementById("as-of-timestamp-utc"),
    pricingTimestamp: document.getElementById("pricing-timestamp-input"),
    pricingTimestampUtc: document.getElementById("pricing-timestamp-utc"),
    expiryTimestamp: document.getElementById("expiry-timestamp-input"),
    expiryTimestampUtc: document.getElementById("expiry-timestamp-utc"),
    timingProductId: document.getElementById("timing-product-id"),
    timingSnapshotId: document.getElementById("timing-snapshot-id"),
    timingSnapshotMetadata: document.getElementById("timing-snapshot-metadata"),
    timingBondQuoteMetadata: document.getElementById("timing-bond-quote-metadata"),
    timingForwardMetadata: document.getElementById("timing-forward-metadata"),
    timingVolatilityMetadata: document.getElementById("timing-volatility-metadata"),
    timingCreditMetadata: document.getElementById("timing-credit-metadata"),
    provenanceLog: document.getElementById("override-provenance-log"),
    provenanceEmpty: document.getElementById("override-provenance-empty"),

    // Bond master (read-only)
    detailsIssuer: document.getElementById("details-issuer"),
    detailsIsin: document.getElementById("details-isin"),
    detailsCusip: document.getElementById("details-cusip"),
    detailsCurrency: document.getElementById("details-currency"),
    detailsCoupon: document.getElementById("details-coupon"),
    detailsCouponFrequency: document.getElementById("details-coupon-frequency"),
    detailsIssueDate: document.getElementById("details-issue-date"),
    detailsMaturity: document.getElementById("details-maturity"),
    detailsDayCount: document.getElementById("details-day-count"),
    detailsFirstCouponDate: document.getElementById("details-first-coupon-date"),
    detailsLastCouponDate: document.getElementById("details-last-coupon-date"),
    detailsCallable: document.getElementById("details-callable"),
    detailsSinkable: document.getElementById("details-sinkable"),
    detailsBondType: document.getElementById("details-bond-type"),
    detailsBloombergDayCount: document.getElementById("details-bloomberg-day-count"),
    detailsBloombergMaturityType: document.getElementById("details-bloomberg-maturity-type"),
    detailsBloombergCalcType: document.getElementById("details-bloomberg-calc-type"),
    detailsSource: document.getElementById("details-source"),
    detailsAcquiredAt: document.getElementById("details-acquired-at"),

    // Header / chrome
    instrTitle: document.getElementById("instr-title"),
    instrIsin: document.getElementById("instr-isin"),
    quoteSideBadge: document.getElementById("quote-side-badge"),
    provenancePill: document.getElementById("provenance-pill"),
    provenanceBadge: document.getElementById("provenance-badge"),
    statCleanPrice: document.getElementById("stat-clean-price"),
    statYieldMid: document.getElementById("stat-yield-mid"),
    statValuationDate: document.getElementById("stat-valuation-date"),
    statOptionSettlement: document.getElementById("stat-option-settlement"),
    sidebarLiveRow: document.getElementById("sidebar-live-row"),
    sidebarSourceSystem: document.getElementById("sidebar-source-system"),
    sidebarAsofRow: document.getElementById("sidebar-asof-row"),
    sidebarAsOf: document.getElementById("sidebar-as-of-timestamp"),
    statusIndicator: document.getElementById("status-indicator"),
    statusText: document.getElementById("status-text"),
    priceBtn: document.getElementById("price-btn"),
    clearBtn: document.getElementById("clear-btn"),
    downloadJsonBtn: document.getElementById("download-json-btn"),
    downloadMarkdownBtn: document.getElementById("download-markdown-btn"),

    // Sections
    instrumentHeaderSection: document.getElementById("instrument-header-section"),
    workspaceSection: document.getElementById("workspace-section"),
  };

  // --- Truthful provenance labels ------------------------------------------
  //
  // None of these claims a market source Shiori does not have: the forward,
  // the volatility and the curve nodes are transcribed by the trader, and the
  // credit-spread state is a route policy statement, not observed data.
  const MANUAL_SOURCE_SYSTEM = "MANUAL_TRADER_ENTRY";
  const SNAPSHOT_SOURCE_SYSTEM = "SHIORI_MANUAL_WORKBENCH";
  const ROUTE_POLICY_SOURCE_SYSTEM = "SHIORI_ROUTE_POLICY";
  const CREDIT_SPREAD_NOT_REQUIRED_AUDIT =
    "Explicit-forward OVME-aligned standalone route: the reviewed pricing path " +
    "discounts the option payoff on the Option Discount Curve and never reads " +
    "credit_spread, so no spread is applied and none is fabricated.";

  // --- Bloomberg sourcing evidence -----------------------------------------
  //
  // Verbatim conclusions from the Issue #149 Bloomberg workstation run and the
  // #145 audit. These strings are the only thing standing between a trader and
  // an unexplained blank field, so they name the actual mnemonic and the actual
  // response rather than saying "unavailable".
  const EVIDENCE_FORWARD =
    "Issue #149 workstation run: OPT_UNDL_FORWARD_PX was sent with the two " +
    "confirmed overrides (OP046 = pricing model, bond-option token 'Black'; " +
    "OP188 = Shiori's valuation date). Both US91282CLJ89 and GB00BFX0ZL78 " +
    "returned BAD_FLD, 'Field not applicable to security'. OP131 is the " +
    "equity/index dividend yield and is never sent, never defaulted to 0.";
  const EVIDENCE_VOL =
    "Issue #149 workstation run: PRICE_VOL returned BAD_FLD and " +
    "EQUIVALENT_PRICE_VOL returned BAD_FLD. There is no confirmed " +
    "ReferenceData route for a direct price volatility on these securities.";
  const EVIDENCE_DISCOUNTING =
    "Issue #149 / docs/bloomberg_ovme_source_mapping.md: no approved Bloomberg " +
    "sourcing or curve-construction methodology exists. MMkt, repo, FTP, par and " +
    "swap rates must not be relabelled as continuous zero rates, and the SWDF " +
    "S490 stripped-zero route is only PARTIALLY_CONFIRMED, with compounding, " +
    "interpolation and date treatment unverified.";
  const EVIDENCE_BOND_REFERENCE =
    "Issue #149 workstation run: DAY_CNT_DES = ACT/ACT, DAY_CNT = 1, " +
    "SECURITY_TYP distinguishes US GOVERNMENT / UK GILT STOCK, CPN_TYP = FIXED " +
    "and PREV_CPN_DT returns a value -- but all of these are display-only " +
    "evidence. Ex-dividend and security-status candidates returned BAD_FLD. " +
    "No UST / conventional-Gilt convention profile is approved, so Shiori maps " +
    "none of these strings into a typed enum.";
  const EVIDENCE_TIMING =
    "Issue #149: no Bloomberg reference field describes an OTC option's own cash " +
    "settlement date. SETTLE_DT and DAYS_TO_SETTLE describe the cash bond's " +
    "standard settlement -- a different role that must not be conflated. Fixing " +
    "Reporting Date to Valuation Date is an unapproved route decision.";
  const EVIDENCE_BOND_MASTER =
    "Bond Master values are read verbatim from the DAPI mnemonics confirmed in " +
    "PR #141. The fields above came back empty for this security.";
  const EVIDENCE_SOURCED_STATE_INVALIDATED =
    "The Bloomberg request for this run failed, so the previously sourced quote " +
    "and instrument data can no longer be shown or priced against -- Shiori will " +
    "not fall back to them. Your own trade inputs are untouched.";

  // --- Workflow groups -----------------------------------------------------
  //
  // The ordinary trader workflow is exactly the groups whose `advanced` flag is
  // false: instrument, Call/Put, Buy/Sell, strike, notional, expiry, and the
  // three market reviews. Advanced groups are still blocking (Shiori never
  // prices without them) but never appear on the main screen.
  //
  // `resolved(draft)` is a structural presence check only -- never a financial
  // judgment about whether a present value is itself valid. That stays the
  // existing typed constructors' job, and the Price gate below additionally
  // requires the real typed builder to accept the draft.

  function present(value) {
    return value !== null && value !== undefined && value !== "";
  }

  function referenceRecord(draft) {
    return (draft.bond_reference_data_universe || [])[0] || {};
  }

  const WORKFLOW_GROUPS = [
    {
      id: "instrument",
      // Resolved only while live Bloomberg sourced state backs the draft. A
      // failed refresh sets `sourcedQuoteInvalidated` and therefore reopens
      // this group, which is what keeps Price disabled and stops the run being
      // priced against sourced data Shiori has just disowned.
      label: "Instrument",
      advanced: false,
      resolved: (draft) =>
        draft !== null && resolvedBloombergBond !== null && !sourcedQuoteInvalidated,
      locator: "#bond-identifier-input",
      unresolved: () =>
        sourcedQuoteInvalidated
          ? {
              title: "The sourced quote for this run has been invalidated",
              missing: "A current Bloomberg quote for the loaded bond.",
              why:
                "The Bloomberg refresh failed, so the quote and instrument data " +
                "this run was anchored to are no longer current, and Shiori will " +
                "not price against them or show them as if they were.",
              evidence: EVIDENCE_SOURCED_STATE_INVALIDATED,
              next:
                "Press Refresh Bloomberg & Price to re-source this bond. Your " +
                "trade inputs, overrides and curve nodes are all still here.",
            }
          : {
              title: "No bond is loaded",
              missing: "A Bloomberg-loaded bond.",
              why:
                "Every pricing input on this route is anchored to one Bloomberg " +
                "acquisition event, so nothing can be entered before a bond is " +
                "resolved.",
              evidence: "No Bloomberg request has been made yet in this run.",
              next:
                "Type a 12-character ISIN or a 9-character CUSIP, pick a quote " +
                "side, then press Bloomberg Load.",
            },
    },
    {
      id: "option-type",
      label: "Call / Put",
      advanced: false,
      resolved: (draft) => present(draft.bond_option.option_type),
      locator: "#option-type-toggle",
      unresolved: {
        title: "Call or Put has not been chosen",
        missing: "The option type.",
        why: "This is a genuine trade decision. Shiori never assumes a direction.",
        evidence: "Not applicable — this is a trader input, not a sourced value.",
        next: "Choose Call or Put in the Trade group.",
      },
    },
    {
      id: "position",
      label: "Buy / Sell",
      advanced: false,
      resolved: (draft) => present(draft.bond_option.position),
      locator: "#position-toggle",
      unresolved: {
        title: "Buy or Sell has not been chosen",
        missing: "The position direction.",
        why:
          "This is a genuine trade decision. It sets the sign of the position " +
          "Greeks, so Shiori never assumes it.",
        evidence: "Not applicable — this is a trader input, not a sourced value.",
        next: "Choose Buy or Sell in the Trade group.",
      },
    },
    {
      id: "strike",
      label: "Strike",
      advanced: false,
      resolved: (draft) => present(draft.bond_option.strike_price),
      locator: "#strike-price-input",
      unresolved: {
        title: "Strike has not been entered",
        missing: "The strike clean price, per 100.",
        why: "This is a genuine trade decision on a PRICE payoff basis.",
        evidence: "Not applicable — this is a trader input, not a sourced value.",
        next: "Enter the strike per 100 in the Trade group.",
      },
    },
    {
      id: "notional",
      label: "Notional",
      advanced: false,
      resolved: (draft) => present(draft.bond_option.notional),
      locator: "#notional-input",
      unresolved: {
        title: "Notional has not been entered",
        missing: "The trade notional.",
        why: "This is a genuine trade decision and scales the total premium.",
        evidence: "Not applicable — this is a trader input, not a sourced value.",
        next: "Enter the notional in the Trade group.",
      },
    },
    {
      id: "expiry",
      label: "Expiry",
      advanced: false,
      resolved: (draft) =>
        present(draft.bond_option.expiry_date) && present(draft.expiry_timestamp),
      locator: "#expiry-datetime-input",
      unresolved: {
        title: "Expiry is incomplete",
        missing: "The expiry date, local time of day, and its UTC offset.",
        why:
          "Expiry is a genuine trade decision, and no default expiry time or " +
          "timezone convention is approved. Bloomberg reference data does not " +
          "carry this option's expiry timestamp.",
        evidence:
          "Issue #149 recorded expiry timing as UNRESOLVED and kept it a trade " +
          "input: no market-close, timezone or holiday convention is approved, " +
          "so Shiori will not invent one.",
        next:
          "Set the expiry date and time, then enter the UTC offset it is quoted " +
          "in (for example +08:00 or Z). Shiori keeps that offset exactly.",
      },
    },
    {
      id: "forward-review",
      label: "Forward",
      advanced: false,
      resolved: (draft) =>
        present(draft.forward_clean_price_input.forward_clean_price_per_100),
      locator: "#forward-price-input",
      unresolved: {
        title: "Forward clean price cannot be sourced from Bloomberg",
        missing: "The forward clean price, per 100, at forward settlement.",
        why:
          "OPT_UNDL_FORWARD_PX is the only candidate route and it is not " +
          "applicable to a cash bond on the current DAPI route.",
        evidence: EVIDENCE_FORWARD,
        next:
          "Enter the forward clean price you are pricing against. It is recorded " +
          "as a trader override with provenance. Shiori will not reconstruct a " +
          "forward from spot price, repo, FTP, MMkt or any par rate.",
      },
    },
    {
      id: "vol-review",
      label: "Volatility",
      advanced: false,
      resolved: (draft) => present(draft.volatility_input.volatility),
      locator: "#volatility-input",
      unresolved: {
        title: "Direct price volatility cannot be sourced from Bloomberg",
        missing: "A direct PRICE_VOL or EQUIVALENT_PRICE_VOL, as a decimal.",
        why:
          "No confirmed ReferenceData route supplies a direct price volatility " +
          "for these securities.",
        evidence: EVIDENCE_VOL,
        next:
          "Enter a direct price volatility and its basis. YIELD_VOL is not a " +
          "substitute and no yield-to-price volatility conversion is approved, so " +
          "neither is offered anywhere on this page.",
      },
    },
    {
      id: "discounting-review",
      label: "Discounting",
      advanced: false,
      resolved: (draft) => computeCurveCoverage(draft).state === "covered",
      locator: "#curve-rows .curve-tenor-input",
      revealAdvanced: true,
      unresolved: {
        title: "Option Discount Curve has no approved Bloomberg source",
        missing:
          "Continuous-zero-rate nodes covering the Reporting Date and the Option " +
          "Settlement Date.",
        why:
          "No approved Bloomberg sourcing or curve-construction methodology " +
          "exists for the Option Discount Curve.",
        evidence: EVIDENCE_DISCOUNTING,
        next:
          "Open Advanced and enter genuine continuously-compounded zero rates. " +
          "Shiori interpolates in range only and never bootstraps, converts or " +
          "extrapolates a curve.",
      },
    },
    {
      id: "bloomberg-reference",
      label: "Bloomberg Bond Master",
      advanced: true,
      resolved: (draft) => {
        const record = referenceRecord(draft);
        return (
          present(record.coupon) &&
          present(record.coupon_frequency) &&
          present(record.maturity_date) &&
          present(record.issue_date) &&
          present(record.first_coupon_date) &&
          present(record.callable_flag) &&
          present(record.sinkable_flag)
        );
      },
      locator: "#details-coupon",
      revealAdvanced: true,
      unresolved: {
        title: "Bloomberg did not return every confirmed Bond Master field",
        missing:
          "One or more of coupon, coupon frequency, issue date, maturity date, " +
          "first coupon date, and the callable / sinkable flags.",
        why:
          "These are the fields Bloomberg is confirmed to supply, so Shiori will " +
          "not accept a substitute for them from anywhere else.",
        evidence: EVIDENCE_BOND_MASTER,
        next:
          "Run Bloomberg Load again for this security. Shiori will not fill a " +
          "Bond Master field with a convention default.",
      },
    },
    {
      id: "advanced-bond-reference",
      label: "Bond reference override",
      advanced: true,
      resolved: (draft) => {
        const record = referenceRecord(draft);
        return (
          present(record.day_count) &&
          present(record.bond_type) &&
          present(record.ex_dividend_days) &&
          present(record.last_coupon_date) &&
          present(record.status)
        );
      },
      locator: "#day-count-select",
      revealAdvanced: true,
      unresolved: {
        title: "Bond reference fields have no approved UST / Gilt profile",
        missing:
          "Day count, bond type, ex-dividend days, last coupon date and bond " +
          "status.",
        why:
          "These are profile-owned or derivation-owned decisions and neither a " +
          "UST / conventional-Gilt convention profile nor a reviewed " +
          "regular-schedule generator is approved yet.",
        evidence: EVIDENCE_BOND_REFERENCE,
        next:
          "Open Advanced → Bond reference and set them. Each entry is recorded " +
          "as a trader override with provenance, beside the Bloomberg text that " +
          "is evidence for it.",
      },
    },
    {
      id: "advanced-timing",
      label: "Timing & settlement override",
      advanced: true,
      resolved: (draft) =>
        present(draft.reporting_date) &&
        present(draft.forward_settlement_date) &&
        present(draft.option_settlement_date),
      locator: "#reporting-date-input",
      revealAdvanced: true,
      unresolved: {
        title: "Settlement and reporting dates have no approved calendar source",
        missing:
          "The reporting date, the forward settlement date and the option " +
          "settlement date.",
        why:
          "Shiori owns no market calendar and rolls no date, and no Bloomberg " +
          "field carries an OTC option's own cash settlement date.",
        evidence: EVIDENCE_TIMING,
        next:
          "Open Advanced → Timing & settlement and set the three dates. Each is " +
          "recorded as a trader override with provenance.",
      },
    },
  ];

  const ORDINARY_WORKFLOW_GROUP_IDS = WORKFLOW_GROUPS.filter(
    (group) => !group.advanced
  ).map((group) => group.id);

  // --- State ---------------------------------------------------------------

  // The resolved Bloomberg bond identity from the instrument-first lookup --
  // null until a lookup succeeds, and reset to null by Clear, a newer
  // successful lookup, or any failed Bloomberg call.
  let resolvedBloombergBond = null;

  // The in-memory pricing draft: a full standalone-option-case-shaped object,
  // created fresh by buildInitialDraftFromBloomberg on every successful lookup
  // -- never derived from, or merged with, a prior draft or any bundled
  // fixture. Only values supplied by Bloomberg or fixed mechanically by the
  // existing route/contract are seeded; every genuine trader decision starts
  // null.
  let currentDraft = null;

  // Provenance for every value the trader had to supply because Shiori could
  // not source it. Keyed by draft path so re-entering a value replaces its
  // stamp rather than accumulating duplicates. Cleared with the draft.
  let overrideProvenance = new Map();

  // True once a Bloomberg *refresh* for the currently loaded bond has failed.
  // The bond stays loaded (so the trader can retry the refresh) and the whole
  // trader-authored ticket stays intact, but everything Shiori sourced is
  // disowned: the displayed quote/instrument state, the priced result, and the
  // right to price at all. Cleared only by a successful refresh, a successful
  // lookup, or Clear.
  let sourcedQuoteInvalidated = false;

  // The currently displayed, exportable run.
  let currentDisplay = null;
  let displayGeneration = 0;

  // Whether the real typed builder has accepted the current draft. The Price
  // button is enabled only when this is true -- a non-empty form is never
  // sufficient on its own (Issue #143 requirement 5).
  let builderValidation = { state: "unknown", error: null };

  function setCurrentDisplay(display) {
    displayGeneration++;
    currentDisplay = display;
    setExportEnabled(display !== null);
  }

  function setExportEnabled(enabled) {
    els.downloadJsonBtn.classList.toggle("is-disabled", !enabled);
    els.downloadMarkdownBtn.classList.toggle("is-disabled", !enabled);
  }

  // --- Advanced section (collapsed by default) ------------------------------

  function setAdvancedCollapsed(collapsed) {
    els.advancedBody.hidden = collapsed;
    els.advancedHead.setAttribute("aria-expanded", String(!collapsed));
    els.advancedIndicator.textContent = collapsed ? "Expand" : "Collapse";
  }

  els.advancedHead.addEventListener("click", () => {
    setAdvancedCollapsed(!els.advancedBody.hidden);
  });

  // --- Small formatters -----------------------------------------------------

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

  function textOrNull(raw) {
    const trimmed = (raw || "").trim();
    return trimmed === "" ? null : trimmed;
  }

  // ex_dividend_days is `int` on its dataclass and explicitly rejects a
  // float/bool. Only a plain integer literal counts; "1.5" is not truncated.
  function integerOrNull(raw) {
    const trimmed = (raw || "").trim();
    if (!/^-?\d+$/.test(trimmed)) return null;
    return Number.parseInt(trimmed, 10);
  }

  function selectValueOrNull(select) {
    return select.value === "" ? null : select.value;
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

  function setCouponPercentOrNotAvailable(el, value) {
    const text = fmtCouponPercent(value);
    el.textContent = text;
    el.classList.toggle("pending-value", text === "Not available");
  }

  function describeProvenance(context) {
    const sourceSystem = context && context.source_system;
    return sourceSystem ? String(sourceSystem) : "Source not declared";
  }

  // Mirrors parse_bond_identifier in
  // src/shiori_pricing_lab/data/bloomberg_bond_quote.py -- client-side only for
  // immediate feedback and to build the symbology-qualified identifier the
  // existing quote-refresh path needs; the server independently re-parses and
  // is the actual authority. Never guesses a format, never appends a yellow-key
  // suffix.
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

  // --- Datetime instants ----------------------------------------------------
  //
  // Transparency only. This preview never replaces or mutates what is sent:
  // every timestamp goes to the server exactly as composed. Only
  // `as_of_timestamp` is respelled in UTC at the contract boundary (its own
  // no-look-ahead check is documented as needing the UTC calendar date);
  // `pricing_timestamp` and `expiry_timestamp` keep their offset, because the
  // timing contract compares the *local* calendar date they represent against
  // the explicit date fields beside them.

  function utcInstantPreview(raw) {
    const text = (raw || "").trim();
    if (text === "") return { text: "—", instant: null, invalid: false };
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
    // Intentionally the date written in the timestamp, not the UTC date
    // JavaScript's Date object would produce.
    return match[1];
  }

  function normalizeAsOfFromAcquisition(raw) {
    const text = (raw || "").trim();
    const match = ACQUISITION_TIMESTAMP_PATTERN.exec(text);
    if (!match) return textOrNull(text);
    // Match the approved Python boundary exactly: already-UTC spellings are
    // preserved, a genuinely non-zero offset is respelled as the same instant.
    if (match[4] === "Z" || /^[+-]00:00$/.test(match[4])) return text;
    const preview = utcInstantPreview(text);
    return preview.instant === null ? text : preview.instant;
  }

  // --- The one Expiry interaction ------------------------------------------
  //
  // #145 asked for expiry date and expiry timestamp to become one interaction
  // while preserving the offset-aware contract. A `datetime-local` value plus an
  // explicitly typed UTC offset compose both: the date half becomes
  // `bond_option.expiry_date`, and the whole thing becomes `expiry_timestamp`
  // with the trader's own offset kept verbatim. No time of day, timezone,
  // market close or business-day roll is ever inferred -- an incomplete or
  // malformed entry simply yields nulls and the group stays unresolved.

  const EXPIRY_OFFSET_PATTERN = /^(Z|[+-]\d{2}:\d{2})$/;
  const EXPIRY_LOCAL_PATTERN = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(:\d{2})?$/;

  function composeExpiry() {
    const localMatch = EXPIRY_LOCAL_PATTERN.exec((els.expiryDatetime.value || "").trim());
    const offset = (els.expiryOffset.value || "").trim();
    if (!localMatch) {
      return { date: null, timestamp: null, note: null };
    }
    const date = localMatch[1];
    const time = localMatch[2];
    const seconds = localMatch[3] || ":00";
    if (!EXPIRY_OFFSET_PATTERN.test(offset)) {
      return {
        date,
        timestamp: null,
        note:
          offset === ""
            ? "Enter the UTC offset this expiry time is quoted in (for example +08:00, or Z for UTC)."
            : `“${offset}” is not a UTC offset. Use +HH:MM, -HH:MM, or Z.`,
      };
    }
    return {
      date,
      timestamp: `${date}T${time}${seconds}${offset}`,
      note: null,
    };
  }

  function renderExpiryPreview() {
    const expiry = composeExpiry();
    if (expiry.timestamp === null) {
      els.expiryDerivedPreview.textContent =
        expiry.note ||
        "Enter the expiry date, local time and its UTC offset.";
      els.expiryDerivedPreview.classList.toggle("is-invalid", expiry.note !== null);
      return;
    }
    const preview = utcInstantPreview(expiry.timestamp);
    els.expiryDerivedPreview.classList.remove("is-invalid");
    els.expiryDerivedPreview.textContent =
      `Expiry date ${expiry.date} · timestamp ${expiry.timestamp}` +
      (preview.instant ? ` · same instant in UTC: ${preview.instant}` : "");
  }

  // --- Option Discount Curve editor ----------------------------------------
  //
  // Builds the existing BLICurvePoint contract only: OPTION_DISCOUNT_CURVE
  // purpose, CONTINUOUS_ZERO_RATE basis, existing nD/nM/nY tenor grammar. No
  // curve is constructed, bootstrapped, converted, or extrapolated here, and no
  // OVME MMkt/repo rate is relabelled as a continuous zero rate.

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

  // Only fully-valid rows become curve points. A half-typed row is simply not
  // yet a node -- never guessed at, and never silently dropped without the
  // coverage line below saying so.
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
  // (target - valuation).days / 365.0, with a same-day target needing no node
  // at all (its discount factor is 1.0 by definition).
  function curveCoordinateForDate(valuationDate, targetDate) {
    const days = isoDateDaysBetween(valuationDate, targetDate);
    if (days === null) return null;
    return days / 365.0;
  }

  function formatYears(value) {
    return value.toFixed(4);
  }

  // The exact blocking coverage state. Pricing is gated on this because the
  // reviewed interpolator rejects an out-of-range target outright rather than
  // flat-extrapolating -- so an uncovered date is a hard stop, never a soft
  // warning that still lets a fabricated discount factor through.
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
      els.discountingReadout.textContent = "No Option Discount Curve nodes entered";
      return;
    }
    const coverage = computeCurveCoverage(currentDraft);
    els.curveCoverage.textContent = coverage.message;
    els.curveCoverage.classList.toggle("is-blocking", coverage.state === "blocking");
    els.curveCoverage.classList.toggle("is-covered", coverage.state === "covered");
    els.discountingReadout.textContent = coverage.message;
  }

  // --- Override provenance --------------------------------------------------
  //
  // Every value Shiori could not source carries a stamp saying what it is, what
  // it is worth, why it had to be entered, and which Bloomberg acquisition
  // event the run is anchored to. No clock is read here: the run's own
  // acquisition timestamp is the anchor, so a stamp never invents a new
  // timestamp semantic of its own.

  const OVERRIDE_FIELDS = [
    {
      path: "forward_clean_price_input.forward_clean_price_per_100",
      label: "Forward Clean Price (per 100)",
      reason: "Bloomberg OPT_UNDL_FORWARD_PX returned BAD_FLD for cash bonds on this route.",
      read: (draft) => draft.forward_clean_price_input.forward_clean_price_per_100,
    },
    {
      path: "volatility_input.volatility",
      label: "Price Vol (σ)",
      reason: "Bloomberg PRICE_VOL and EQUIVALENT_PRICE_VOL both returned BAD_FLD.",
      read: (draft) => draft.volatility_input.volatility,
    },
    {
      path: "volatility_input.volatility_basis",
      label: "Volatility Basis",
      reason: "Chosen by the trader; no sourced basis exists to confirm it against.",
      read: (draft) => draft.volatility_input.volatility_basis,
    },
    {
      path: "curve_points",
      label: "Option Discount Curve nodes",
      reason: "No approved Bloomberg sourcing or curve-construction methodology exists.",
      read: (draft) =>
        (draft.curve_points || []).length === 0
          ? null
          : draft.curve_points.map((point) => `${point.tenor}=${point.rate}`).join(", "),
    },
    {
      path: "bond_reference_data_universe.0.day_count",
      label: "Day Count",
      reason: "No approved UST / Gilt profile; DAY_CNT_DES is display-only evidence.",
      read: (draft) => referenceRecord(draft).day_count,
    },
    {
      path: "bond_reference_data_universe.0.bond_type",
      label: "Bond Type",
      reason: "No approved UST / Gilt profile; MTY_TYP is display-only evidence.",
      read: (draft) => referenceRecord(draft).bond_type,
    },
    {
      path: "bond_reference_data_universe.0.ex_dividend_days",
      label: "Ex-Dividend Days",
      reason: "Ex-dividend candidates returned BAD_FLD; no profile rule is approved.",
      read: (draft) => referenceRecord(draft).ex_dividend_days,
    },
    {
      path: "bond_reference_data_universe.0.last_coupon_date",
      label: "Last Coupon Date",
      reason:
        "No reviewed regular-schedule generator is approved and PREV_CPN_DT's role is unconfirmed.",
      read: (draft) => referenceRecord(draft).last_coupon_date,
    },
    {
      path: "bond_reference_data_universe.0.status",
      label: "Bond Status",
      reason: "Security-status candidates returned BAD_FLD; no status policy is approved.",
      read: (draft) => referenceRecord(draft).status,
    },
    {
      path: "reporting_date",
      label: "Reporting Date",
      reason: "Fixing Reporting Date to Valuation Date is an unapproved route decision.",
      read: (draft) => draft.reporting_date,
    },
    {
      path: "forward_settlement_date",
      label: "Forward Settlement Date",
      reason: "Shiori owns no market calendar; SETTLE_DT describes a different role.",
      read: (draft) => draft.forward_settlement_date,
    },
    {
      path: "option_settlement_date",
      label: "Option Settlement Date",
      reason: "No Bloomberg field carries an OTC option's own cash settlement date.",
      read: (draft) => draft.option_settlement_date,
    },
  ];

  function refreshOverrideProvenance() {
    overrideProvenance = new Map();
    if (!currentDraft) return;
    const anchor = currentDraft.pricing_timestamp || null;
    OVERRIDE_FIELDS.forEach((field) => {
      const value = field.read(currentDraft);
      if (!present(value)) return;
      overrideProvenance.set(field.path, {
        field: field.label,
        path: field.path,
        value: String(value),
        source_system: MANUAL_SOURCE_SYSTEM,
        basis: "TRADER_OVERRIDE",
        reason_not_sourced: field.reason,
        run_acquired_at: anchor,
      });
    });
  }

  function overrideProvenanceRecords() {
    return Array.from(overrideProvenance.values());
  }

  function renderOverrideProvenance() {
    const records = overrideProvenanceRecords();
    els.provenanceLog.innerHTML = "";
    els.provenanceEmpty.hidden = records.length > 0;
    records.forEach((record) => {
      const item = document.createElement("li");
      item.className = "provenance-entry";
      const head = document.createElement("div");
      head.className = "provenance-entry-head";
      head.textContent = `${record.field} = ${record.value}`;
      const meta = document.createElement("div");
      meta.className = "provenance-entry-meta";
      meta.textContent =
        `${record.source_system} · ${record.basis} · anchored to Bloomberg acquisition ` +
        `${record.run_acquired_at || "not available"} — ${record.reason_not_sourced}`;
      item.appendChild(head);
      item.appendChild(meta);
      els.provenanceLog.appendChild(item);
    });

    const stamp = (path) => {
      const record = overrideProvenance.get(path);
      return record
        ? `Provenance: ${record.source_system} · ${record.basis} · anchored to ` +
            `Bloomberg acquisition ${record.run_acquired_at || "not available"}`
        : "Provenance: not entered yet — nothing is recorded until you enter a value.";
    };
    els.forwardProvenance.textContent = stamp(
      "forward_clean_price_input.forward_clean_price_per_100"
    );
    els.volProvenance.textContent = stamp("volatility_input.volatility");
    els.discountingProvenance.textContent = stamp("curve_points");
  }

  // --- Draft <-> form -------------------------------------------------------

  const MANUAL_TEXT_INPUTS = () => [
    els.strikePrice,
    els.notional,
    els.volatility,
    els.forwardPrice,
    els.expiryDatetime,
    els.expiryOffset,
    els.reportingDate,
    els.forwardSettlementDate,
    els.optionSettlementDate,
    els.exDividendDays,
    els.lastCouponDate,
  ];

  const DERIVED_READONLY_INPUTS = () => [
    els.valuationDate,
    els.asOfTimestamp,
    els.pricingTimestamp,
    els.expiryTimestamp,
  ];

  const MANUAL_SELECTS = () => [els.dayCount, els.bondTypeSelect, els.bondStatus];

  function setToggle(toggleEl, value) {
    toggleEl.querySelectorAll(".opt").forEach((opt) => {
      opt.classList.toggle("on", opt.dataset.value === value);
    });
  }

  // No option ever starts pre-selected (Call/Put and Buy/Sell included,
  // alongside the Quote Side toggles) -- this returns null rather than throwing
  // until the trader has explicitly clicked one.
  function getOptionalToggleValue(toggleEl) {
    const selected = toggleEl.querySelector(".opt.on");
    return selected ? selected.dataset.value : null;
  }

  // Every trader-editable control on the page, cleared as one set. A fresh
  // Bloomberg lookup, a failed Bloomberg call, and Clear all call this: a
  // manual value entered for one bond (its dates, its bond-reference terms, its
  // curve, its forward and vol) must never survive into a different bond's
  // draft.
  function clearTraderForm() {
    setToggle(els.optionTypeToggle, null);
    setToggle(els.positionToggle, null);
    MANUAL_TEXT_INPUTS().forEach((input) => {
      input.value = "";
    });
    DERIVED_READONLY_INPUTS().forEach((input) => {
      input.value = "";
    });
    MANUAL_SELECTS().forEach((select) => {
      select.value = "";
    });
    // Volatility basis is the one select with a real default: PRICE_VOL is the
    // direct, no-conversion basis the guard accepts.
    els.volatilityBasis.value = "PRICE_VOL";
    // Two blank rows: the minimum the curve contract needs, offered ready to
    // fill. They are structure only -- a blank row is never a node.
    clearCurveRows();
    addCurveRow("", "");
    addCurveRow("", "");
    renderExpiryPreview();
    renderTimestampUtcPreviews();
    renderCurveCoverage();
  }

  function setDerivedFormFromDraft() {
    els.valuationDate.value = currentDraft ? currentDraft.valuation_date || "" : "";
    els.asOfTimestamp.value = currentDraft ? currentDraft.as_of_timestamp || "" : "";
    els.pricingTimestamp.value = currentDraft ? currentDraft.pricing_timestamp || "" : "";
    els.expiryTimestamp.value = currentDraft ? currentDraft.expiry_timestamp || "" : "";
    renderTimestampUtcPreviews();
  }

  function setTradeFormFromDraft(draft) {
    setToggle(els.optionTypeToggle, draft.bond_option.option_type);
    setToggle(els.positionToggle, draft.bond_option.position);
    els.strikePrice.value = draft.bond_option.strike_price ?? "";
    els.notional.value = draft.bond_option.notional ?? "";
    els.volatility.value = draft.volatility_input.volatility ?? "";
    els.forwardPrice.value =
      draft.forward_clean_price_input.forward_clean_price_per_100 ?? "";
  }

  // The six overlay-shaped fields, read directly off the current draft rather
  // than re-reading the DOM -- the single source of truth for what gets sent to
  // the existing overlay-shaped Bloomberg refresh-and-price route.
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

  // Reads every trader-entered value off the form into the current draft.
  // Performs no financial computation, no unit conversion, and no enum
  // inference: each value goes to the field the trader entered it into, and the
  // existing typed constructors remain the only validator.
  function applyManualInputsToDraft() {
    if (!currentDraft) return;

    // Any edit invalidates an in-flight Price or Refresh: their responses
    // describe the case that was sent, which is no longer the ticket on
    // screen. Adopting one would silently discard this edit, or show a result
    // computed from inputs the trader has already changed.
    invalidatePendingPriceRequest();
    invalidatePendingBloombergRequest();

    const bondOption = currentDraft.bond_option;
    bondOption.option_type = getOptionalToggleValue(els.optionTypeToggle);
    bondOption.position = getOptionalToggleValue(els.positionToggle);
    bondOption.strike_price = numberOrNull(els.strikePrice.value);
    bondOption.notional = numberOrNull(els.notional.value);

    // One expiry interaction produces both contract fields; neither is ever
    // guessed from the other.
    const expiry = composeExpiry();
    bondOption.expiry_date = expiry.date;
    currentDraft.expiry_timestamp = expiry.timestamp;
    els.expiryTimestamp.value = expiry.timestamp || "";

    // The five profile/derivation-owned bond-reference fields. The two enum
    // selects are the trader's own choice -- bond_master_raw's DAY_CNT_DES /
    // MTY_TYP are displayed as evidence and are never read into the draft.
    const referenceData = currentDraft.bond_reference_data_universe[0];
    referenceData.day_count = selectValueOrNull(els.dayCount);
    referenceData.bond_type = selectValueOrNull(els.bondTypeSelect);
    referenceData.ex_dividend_days = integerOrNull(els.exDividendDays.value);
    referenceData.last_coupon_date = textOrNull(els.lastCouponDate.value);
    referenceData.status = selectValueOrNull(els.bondStatus);

    // The acquisition-derived values are deliberately not read back from the
    // DOM: their source is the recorded Bloomberg event, and editing any
    // unrelated trader control must never overwrite that provenance.
    currentDraft.reporting_date = textOrNull(els.reportingDate.value);
    currentDraft.forward_settlement_date = textOrNull(els.forwardSettlementDate.value);
    currentDraft.option_settlement_date = textOrNull(els.optionSettlementDate.value);

    currentDraft.volatility_input.volatility = numberOrNull(els.volatility.value);
    currentDraft.volatility_input.volatility_basis = selectValueOrNull(els.volatilityBasis);
    currentDraft.forward_clean_price_input.forward_clean_price_per_100 = numberOrNull(
      els.forwardPrice.value
    );

    currentDraft.curve_points = readCurveNodesFromRows();

    renderExpiryPreview();
    renderTimestampUtcPreviews();
    refreshOverrideProvenance();
    renderOverrideProvenance();
    invalidateBuilderValidation();
    syncDraftGating();
  }

  function shioriIdentifierSuffix(bond) {
    return `${bond.isin}-${(bond.bond_master_acquired_at || "").replace(/[^0-9A-Za-z]/g, "")}`;
  }

  // Builds a brand-new draft containing only the fields Bloomberg itself
  // returned for this bond -- every other required field starts null. Never
  // reads from, or is seeded by, any prior draft or any bundled fixture.
  function buildInitialDraftFromBloomberg(bond) {
    // Populated from bond.bond_master only -- never bond.bond_master_raw, which
    // is display-only and must never enter the typed schema. Every destination
    // key is always present, null unless that field's Bloomberg mnemonic is
    // confirmed (see
    // shiori_pricing_lab.data.bloomberg_bond_quote._BOND_MASTER_FIELD_MAP).
    // ex_dividend_days, status, day_count, bond_type and last_coupon_date have
    // no approved profile or mapping and stay null here regardless.
    const bondMaster = bond.bond_master || {};
    const identifierSuffix = shioriIdentifierSuffix(bond);
    const pricingTimestamp = textOrNull(bond.quote_acquired_at);
    return {
      bond_option: {
        // payoff_basis / exercise_style / settlement_type are the only values
        // the reviewed pricing guard accepts for this route, so they are set
        // rather than asked for. product_id is a Shiori identifier, not market
        // data.
        product_id: `SHIORI-WORKBENCH-${identifierSuffix}`,
        underlying_isin: bond.isin,
        currency: bond.currency,
        payoff_basis: "PRICE",
        option_type: null,
        exercise_style: "EUROPEAN",
        settlement_type: "CASH",
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
          day_count: null,
          callable_flag: bondMaster.callable_flag ?? null,
          sinkable_flag: bondMaster.sinkable_flag ?? null,
          bond_type: null,
          ex_dividend_days: null,
          first_coupon_date: bondMaster.first_coupon_date ?? null,
          last_coupon_date: null,
          status: null,
        },
      ],
      // The acquisition event is already the source for these three values. Its
      // timestamp is preserved exactly for pricing, its represented local date
      // supplies valuation_date, and only as_of_timestamp is respelled under
      // the approved UTC normalization rule. No settlement or expiry date is
      // inferred.
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
      // construction; MANUAL_VERIFIED needs an audit policy that does not exist
      // yet.
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
        // The contract requires the forward's side to equal the spot side, so
        // it is mirrored mechanically rather than asked for twice.
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
        // The standalone pricing path never reads credit_spread, so
        // NOT_REQUIRED is the truthful state. The contract demands
        // credit_spread/credit_spread_basis stay null for this treatment and
        // requires the audit text below.
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

  // --- Rendering ------------------------------------------------------------

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

  function hideContext() {
    els.provenancePill.hidden = true;
    els.quoteSideBadge.hidden = true;
    els.sidebarLiveRow.hidden = true;
    els.sidebarAsofRow.hidden = true;
  }

  // Client-side mirror of extract_standalone_option_case_context
  // (standalone_option_workbench_context.py), field-for-field -- used only for
  // the Bloomberg refresh-and-price path, whose response carries a display dict
  // but no separate context section, unlike POST /api/case. Every value is a
  // direct read off the already-complete draft -- no computation, no inference.
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

  const RESOLVED_BOND_FIELDS = () => [
    els.resolvedBondName,
    els.resolvedBondIsin,
    els.resolvedBondCusip,
    els.resolvedBondCurrency,
    els.resolvedBondCleanPrice,
    els.resolvedBondAccrued,
    els.resolvedBondCoupon,
    els.resolvedBondMaturity,
    els.resolvedBondAcquiredAt,
    els.resolvedBondSource,
  ];

  function renderResolvedBondPanel() {
    // `sourcedQuoteInvalidated` blanks the panel even though the bond object is
    // still held: after a failed refresh that object exists only as the retry
    // target, and none of its now-disowned quote or instrument values may be
    // displayed as if they were current.
    if (!resolvedBloombergBond || sourcedQuoteInvalidated) {
      // Blanked, not merely hidden. A hidden element still holds its text, and
      // a disowned bond's identity must not be one CSS change away from being
      // shown again -- or be readable as if it were still the current bond.
      els.resolvedBondPanel.hidden = true;
      RESOLVED_BOND_FIELDS().forEach((el) => {
        el.textContent = "—";
        el.classList.remove("pending-value");
      });
      return;
    }
    els.resolvedBondPanel.hidden = false;
    els.resolvedBondName.textContent = resolvedBloombergBond.name;
    els.resolvedBondIsin.textContent = resolvedBloombergBond.isin;
    els.resolvedBondCusip.textContent = resolvedBloombergBond.cusip;
    els.resolvedBondCurrency.textContent = resolvedBloombergBond.currency;
    els.resolvedBondCleanPrice.textContent = fmt(resolvedBloombergBond.clean_price_per_100);
    els.resolvedBondAccrued.textContent = fmt(resolvedBloombergBond.accrued_interest_per_100);
    setCouponPercentOrNotAvailable(
      els.resolvedBondCoupon,
      resolvedBloombergBond.bond_master.coupon
    );
    setTextOrNotAvailable(
      els.resolvedBondMaturity,
      resolvedBloombergBond.bond_master.maturity_date
    );
    els.resolvedBondAcquiredAt.textContent = resolvedBloombergBond.quote_acquired_at;
    els.resolvedBondSource.textContent = resolvedBloombergBond.source_system;
  }

  // Renders the Bloomberg Bond Master -- every field not confirmed via
  // tools/bloomberg_dapi_probe.py (see
  // shiori_pricing_lab.data.bloomberg_bond_quote._BOND_MASTER_FIELD_MAP) shows
  // "Not available", never a fabricated value. bond.bond_master_raw carries the
  // Bloomberg mnemonics that are confirmed to return a value but are
  // display-only -- shown in their own "Bloomberg ..." labeled rows, never
  // written into the typed schema fields above.
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
    setTextOrNotAvailable(els.detailsCallable, bondMaster.callable_flag);
    setTextOrNotAvailable(els.detailsSinkable, bondMaster.sinkable_flag);
    setTextOrNotAvailable(els.detailsBondType, bondMaster.bond_type);
    setTextOrNotAvailable(els.detailsBloombergDayCount, bondMasterRaw.day_count);
    setTextOrNotAvailable(els.detailsBloombergMaturityType, bondMasterRaw.maturity_type);
    setTextOrNotAvailable(els.detailsBloombergCalcType, bondMasterRaw.calc_type);
    els.detailsSource.textContent = bond.source_system;
    els.detailsAcquiredAt.textContent = bond.bond_master_acquired_at;
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
      els.detailsCallable,
      els.detailsSinkable,
      els.detailsBondType,
      els.detailsBloombergDayCount,
      els.detailsBloombergMaturityType,
      els.detailsBloombergCalcType,
    ].forEach((el) => setTextOrNotAvailable(el, null));
  }

  // Auto-populated provenance and the Bloomberg raw descriptions, shown so the
  // trader can see exactly what Shiori set and what Bloomberg actually
  // returned. The raw descriptions are evidence beside the enum selects and are
  // never written into the typed draft.
  function renderRouteMetadata() {
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
    els.curveCurrencyLabel.textContent = currentDraft ? currentDraft.bond_option.currency : "—";
    // The Advanced hints are Bloomberg-derived instrument evidence, so they are
    // read only while the sourced state is live. While `sourcedQuoteInvalidated`
    // the retained bond exists purely as a refresh retry target and must not be
    // read here -- otherwise a disowned bond's day-count/maturity-type strings
    // would stay on screen, and its last-coupon hint could steer the trader's
    // preserved override.
    const sourcedBond = sourcedQuoteInvalidated ? null : resolvedBloombergBond;
    const raw = (sourcedBond && sourcedBond.bond_master_raw) || {};
    const master = (sourcedBond && sourcedBond.bond_master) || {};
    setTextOrNotAvailable(els.hintDayCount, raw.day_count);
    setTextOrNotAvailable(els.hintBondType, raw.maturity_type);
    setTextOrNotAvailable(els.hintLastCouponDate, master.last_coupon_date);
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

  // The single, unified failure path: used for a FAILED PricingResult, an HTTP
  // 400/non-2xx bridge response, a network-level fetch rejection, and a
  // non-JSON response body alike. Never leaves a prior successful result or its
  // "loaded" status on screen; never touches currentDraft, so the trader can
  // fix the form and retry.
  function renderFailure(message) {
    clearResultFields();
    els.errorBanner.textContent = message;
    els.errorBanner.hidden = false;
    els.statusIndicator.classList.add("failed");
    els.statusText.textContent = "Pricing failed";
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

  function renderDisplay(display) {
    if (display.status === "FAILED") {
      const messages = (display.errors || [])
        .map((e) => `${e.code}: ${e.message}`)
        .join(" | ");
      renderFailure(messages || "Pricing failed.");
      return;
    }
    renderSuccess(display);
  }

  // --- Market review rows ---------------------------------------------------

  const REVIEW_SOURCING_NOTES = {
    forward:
      "Not sourced. Bloomberg OPT_UNDL_FORWARD_PX is not applicable to a cash bond " +
      "on this DAPI route (BAD_FLD for both the UST and the Gilt test securities, " +
      "with the confirmed OP046 / OP188 overrides applied). Shiori does not " +
      "reconstruct a forward from spot, repo, FTP, MMkt or a par rate.",
    vol:
      "Not sourced. Bloomberg PRICE_VOL and EQUIVALENT_PRICE_VOL both returned " +
      "BAD_FLD. YIELD_VOL is not a substitute and no yield-to-price conversion is " +
      "approved, so neither is offered here.",
    discounting:
      "Not sourced. No approved Bloomberg sourcing or curve-construction " +
      "methodology exists for the Option Discount Curve. MMkt, repo, FTP, par and " +
      "swap rates must not be entered as continuous zero rates.",
  };

  function renderMarketReview(unresolvedIds) {
    const rows = [
      ["forward-review", els.forwardReviewStatus, els.forwardSourcingNote, REVIEW_SOURCING_NOTES.forward],
      ["vol-review", els.volReviewStatus, els.volSourcingNote, REVIEW_SOURCING_NOTES.vol],
      [
        "discounting-review",
        els.discountingReviewStatus,
        els.discountingSourcingNote,
        REVIEW_SOURCING_NOTES.discounting,
      ],
    ];
    let outstanding = 0;
    rows.forEach(([id, statusEl, noteEl, note]) => {
      const unresolved = unresolvedIds.includes(id);
      if (unresolved) outstanding += 1;
      statusEl.textContent = unresolved ? "Trader override required" : "Reviewed";
      statusEl.classList.toggle("is-outstanding", unresolved);
      statusEl.classList.toggle("is-reviewed", !unresolved);
      noteEl.textContent = note;
    });
    els.marketReviewSummary.textContent =
      outstanding === 0 ? "All reviewed" : `${outstanding} awaiting review`;
  }

  // --- The one unresolved dependency ---------------------------------------
  //
  // Requirement 4: when a genuinely required input is missing, the trader sees
  // one clear unresolved dependency -- what is missing, why, what Bloomberg
  // actually answered, and the next step -- not a scattering of unexplained
  // blank fields. Remaining outstanding groups are named compactly beneath it
  // so nothing is hidden, but only one is explained at a time.

  let unresolvedFocusGroup = null;

  function unresolvedGroups(draft) {
    if (draft === null) {
      return [WORKFLOW_GROUPS[0]];
    }
    return WORKFLOW_GROUPS.filter((group) => !group.resolved(draft));
  }

  function renderUnresolvedDependency(groups) {
    if (groups.length === 0) {
      els.unresolvedPanel.hidden = true;
      unresolvedFocusGroup = null;
      return;
    }
    const primary = groups[0];
    // A group's explanation may be a fixed object or a function of current
    // state (the instrument group reads differently before a first lookup than
    // after a Bloomberg failure has invalidated an existing run).
    const detail =
      typeof primary.unresolved === "function" ? primary.unresolved() : primary.unresolved;
    unresolvedFocusGroup = primary;
    els.unresolvedPanel.hidden = false;
    els.unresolvedTitle.textContent = detail.title;
    els.unresolvedMissing.textContent = detail.missing;
    els.unresolvedWhy.textContent = detail.why;
    els.unresolvedEvidence.textContent = detail.evidence;
    els.unresolvedNext.textContent = detail.next;

    const rest = groups.slice(1).map((group) => group.label);
    els.unresolvedAlso.textContent =
      rest.length === 0
        ? "Nothing else is outstanding."
        : `Also outstanding, one at a time: ${rest.join(" · ")}`;
  }

  function revealUnresolvedFocus() {
    const group = unresolvedFocusGroup;
    if (!group) return;
    if (group.revealAdvanced) setAdvancedCollapsed(false);
    const target = document.querySelector(group.locator);
    if (!target) return;
    if (!target.matches("input, select, button, textarea, [tabindex]")) {
      target.setAttribute("tabindex", "-1");
    }
    target.focus({ preventScroll: true });
    target.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  // --- Price gate: the real typed builder decides ---------------------------
  //
  // Requirement 5: a non-empty form is never sufficient. Once every workflow
  // group is structurally resolved, the draft is sent to POST /api/case/validate,
  // which runs the existing build_request_from_standalone_option_case -- the
  // same typed builder, resolver and eligibility gates the real pricing call
  // uses, and no pricing at all. Price stays disabled until that returns ready.

  let validationGeneration = 0;
  let validationTimer = null;
  // Set only when the local bridge could not be reached or did not answer with
  // JSON. That is emphatically *not* the builder rejecting the draft, so it is
  // tracked separately and never rendered as one.
  let validationTransportError = null;

  const _VALIDATION_DEBOUNCE_MS = 60;
  const _VALIDATION_RETRY_MS = 1500;

  function invalidateBuilderValidation() {
    builderValidation = { state: "unknown", error: null };
    validationTransportError = null;
    validationGeneration++;
    if (validationTimer !== null) {
      clearTimeout(validationTimer);
      validationTimer = null;
    }
  }

  function scheduleBuilderValidation(delayMs) {
    if (validationTimer !== null) clearTimeout(validationTimer);
    validationTimer = setTimeout(runBuilderValidation, delayMs || _VALIDATION_DEBOUNCE_MS);
  }

  async function runBuilderValidation() {
    validationTimer = null;
    if (!currentDraft) return;
    const generation = ++validationGeneration;
    const draftAtRequestTime = currentDraft;
    let response;
    let payload;
    try {
      response = await fetch("/api/case/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draftAtRequestTime),
      });
      payload = await response.json();
    } catch (err) {
      if (generation !== validationGeneration) return;
      // A transport failure means Shiori does not *know* whether the builder
      // accepts this draft -- it does not mean the builder said no. Reporting
      // it as a rejection would be a false statement about the contract, and
      // latching it would strand a complete ticket with Price and Refresh both
      // disabled and nothing the trader could do but retype it. So the state
      // stays `unknown` and the check retries itself.
      builderValidation = { state: "unknown", error: null };
      validationTransportError = err.message;
      scheduleBuilderValidation(_VALIDATION_RETRY_MS);
      syncDraftGating();
      return;
    }
    if (generation !== validationGeneration) return;
    validationTransportError = null;
    builderValidation = payload && payload.ready
      ? { state: "ready", error: null }
      : { state: "failed", error: (payload && payload.error) || "The typed builder rejected this draft." };
    syncDraftGating();
  }

  // --- Gating ---------------------------------------------------------------

  function syncDraftGating() {
    const hasDraft = currentDraft !== null;
    const groups = unresolvedGroups(currentDraft);
    const unresolvedIds = groups.map((group) => group.id);
    const hasResult = currentDisplay !== null;

    els.workspaceSection.hidden = !hasDraft;
    els.instrumentHeaderSection.hidden = !hasResult;

    els.instrumentGroupStatus.textContent = !hasDraft
      ? "Enter an ISIN or CUSIP"
      : sourcedQuoteInvalidated
        ? "Sourced quote invalidated — refresh to re-source"
        : "Loaded from Bloomberg";

    // Rendered here rather than only on a form edit, so a freshly-loaded draft
    // shows its real (blocking) coverage state immediately instead of an
    // uninformative em dash.
    renderCurveCoverage();
    renderMarketReview(unresolvedIds);
    renderUnresolvedDependency(groups);

    const advancedOutstanding = groups.filter((group) => group.advanced).length;
    els.advancedSummary.textContent =
      !hasDraft || advancedOutstanding === 0
        ? "Overrides, derived values and provenance"
        : `${advancedOutstanding} override group(s) outstanding`;

    // Price and Refresh are gated separately, because a failed refresh has to
    // leave the trader a way back.
    //
    // Both need a complete draft the real typed builder accepts. Only Price
    // additionally needs live sourced state: after a failed refresh the
    // instrument group is reopened, so Price is off, while Refresh stays
    // available to re-source the very quote that failed. Builder validation
    // depends on the draft alone, so it is still scheduled while the sourced
    // quote is invalidated -- otherwise Refresh could never re-enable.
    const draftGroupsUnresolved = groups.filter((group) => group.id !== "instrument");
    const draftComplete = hasDraft && draftGroupsUnresolved.length === 0;
    if (draftComplete && builderValidation.state === "unknown" && validationTimer === null) {
      scheduleBuilderValidation();
    }
    const builderReady = draftComplete && builderValidation.state === "ready";
    const canRefresh = builderReady && resolvedBloombergBond !== null;
    const canPrice = canRefresh && !sourcedQuoteInvalidated;
    els.priceBtn.classList.toggle("is-disabled", !canPrice);
    els.bloombergRefreshBtn.classList.toggle("is-disabled", !canRefresh);

    if (draftComplete && validationTransportError !== null) {
      // Honest about which of the two it is: Shiori cannot reach its own local
      // validator, so it does not know whether the draft is acceptable.
      els.unresolvedPanel.hidden = false;
      unresolvedFocusGroup = null;
      els.unresolvedTitle.textContent = "Shiori cannot reach its own validator";
      els.unresolvedMissing.textContent =
        "Confirmation that the reviewed typed builder accepts this draft.";
      els.unresolvedWhy.textContent =
        "The local bridge did not answer the validation request: " +
        validationTransportError;
      els.unresolvedEvidence.textContent =
        "This is a transport failure, not a rejection — the builder has said " +
        "nothing about this draft either way, so Shiori will not claim it is " +
        "either valid or invalid.";
      els.unresolvedNext.textContent =
        "Your inputs are untouched and the check retries automatically. If it " +
        "keeps failing, the local Shiori workbench process is probably not " +
        "running.";
      els.unresolvedAlso.textContent = "Nothing else is outstanding.";
    } else if (draftComplete && builderValidation.state === "failed") {
      els.unresolvedPanel.hidden = false;
      unresolvedFocusGroup = null;
      els.unresolvedTitle.textContent = "The typed builder rejected this draft";
      els.unresolvedMissing.textContent =
        "Every input is present, but the reviewed standalone builder does not accept " +
        "them together.";
      els.unresolvedWhy.textContent = builderValidation.error || "";
      els.unresolvedEvidence.textContent =
        "This message comes from the existing typed contracts, resolver and " +
        "standalone eligibility gates — the same code the real pricing call runs. " +
        "Shiori shows it verbatim rather than reinterpreting it.";
      els.unresolvedNext.textContent =
        "Correct the value the message names. Price stays disabled until the real " +
        "builder accepts the draft.";
      els.unresolvedAlso.textContent = "Nothing else is outstanding.";
    }
  }

  // --- Request generation / abort tracking ---------------------------------

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

  // --- Whole-run state reset ------------------------------------------------
  //
  // Requirement: Refresh Bloomberg, Clear and a second lookup must each fully
  // discard the previous bond's trader inputs, overrides, curve rows, status,
  // errors, provenance and pricing results. One function owns that, so no
  // caller can reset "most" of it.

  function resetRunState() {
    invalidatePendingPriceRequest();
    invalidatePendingBloombergRequest();
    invalidateBuilderValidation();

    resolvedBloombergBond = null;
    currentDraft = null;
    overrideProvenance = new Map();
    sourcedQuoteInvalidated = false;
    setCurrentDisplay(null);

    renderResolvedBondPanel();
    clearBondMaster();
    hideContext();
    clearResultFields();
    clearTraderForm();
    renderRouteMetadata();
    renderOverrideProvenance();
    setAdvancedCollapsed(true);

    els.errorBanner.hidden = true;
    els.errorBanner.textContent = "";
    els.statusIndicator.classList.remove("failed");
  }

  // --- The two Bloomberg failure policies ----------------------------------
  //
  // These are deliberately *not* one shared reset. A refresh failure and a
  // lookup failure mean different things, and collapsing them either destroys
  // work needlessly or leaks one bond's ticket onto another.

  // (1) Refresh failure, same already-loaded bond.
  //
  // Disowned: the displayed Bloomberg quote and instrument state, the priced
  // context/result, the exportable run, the builder validation, and the right
  // to price -- `sourcedQuoteInvalidated` reopens the instrument workflow
  // group, so Price stays disabled until a refresh actually succeeds.
  //
  // Kept: Call/Put, Buy/Sell, strike, notional, expiry, the manual forward and
  // volatility, the curve rows and every Advanced override -- all of it belongs
  // to this same ticket on this same bond. `resolvedBloombergBond` is also kept
  // (never displayed while invalidated) purely so Refresh can be retried
  // against the same symbology-qualified identifier. A transient DAPI hiccup
  // must not make a trader retype a completed ticket.
  function renderRefreshFailure(message) {
    invalidatePendingPriceRequest();
    invalidateBuilderValidation();

    sourcedQuoteInvalidated = true;
    setCurrentDisplay(null);

    renderResolvedBondPanel();
    clearBondMaster();
    // Re-rendered *after* the flag is set, so the Advanced Bloomberg hints go
    // back to "Not available" alongside the Bond Master panel rather than
    // lingering as evidence from a bond this run has disowned.
    renderRouteMetadata();
    hideContext();
    clearResultFields();
    refreshOverrideProvenance();
    renderOverrideProvenance();

    els.errorBanner.textContent = message;
    els.errorBanner.hidden = false;
    els.statusIndicator.classList.add("failed");
    els.statusText.textContent = "Bloomberg refresh failed";
    syncDraftGating();
  }

  // (2) Lookup failure -- the trader was resolving an instrument, so there is
  // no ticket this failure can safely belong to.
  //
  // The whole run goes, exactly like Clear: a previous bond's quote, instrument
  // data, result and provenance must not survive, and its trader draft must
  // never be carried onto a different bond. This is the same second-lookup
  // isolation a *successful* lookup enforces.
  function renderLookupFailure(message) {
    resetRunState();
    els.errorBanner.textContent = message;
    els.errorBanner.hidden = false;
    els.statusIndicator.classList.add("failed");
    els.statusText.textContent = "Bloomberg lookup failed";
    syncDraftGating();
  }

  // A malformed identifier or unselected quote side is caught here, before any
  // request is made, so no Bloomberg answer is in question and nothing on
  // screen has gone stale. It reports the problem without discarding a run the
  // trader may have already completed -- unlike the two failure policies above.
  function renderLookupInputError(message) {
    els.errorBanner.textContent = message;
    els.errorBanner.hidden = false;
    els.statusIndicator.classList.add("failed");
    els.statusText.textContent = "Check the lookup inputs";
  }

  // --- Actions --------------------------------------------------------------

  async function loadBloombergBond() {
    const rawIdentifier = els.bondIdentifierInput.value;
    const quoteSide = getOptionalToggleValue(els.bondQuoteSideToggle);
    const parsed = parseBondIdentifier(rawIdentifier);
    if (!parsed || !quoteSide) {
      renderLookupInputError(
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
      // Either a genuine network/JSON failure, or this request was superseded.
      if (isStaleBondLookupRequest(generation)) return;
      renderLookupFailure("Bloomberg bond lookup failed: " + err.message);
      return;
    }
    if (isStaleBondLookupRequest(generation)) return;
    if (!response.ok) {
      renderLookupFailure(payload.error || "Bloomberg bond lookup failed.");
      return;
    }

    // A fresh lookup always starts from a completely reset run: it belongs to
    // another bond, and never inherits an input, override, curve row,
    // provenance stamp or result from the previous one.
    resetRunState();

    resolvedBloombergBond = {
      qualifiedIdentifier: parsed.qualified,
      isin: payload.isin,
      cusip: payload.cusip,
      name: payload.name,
      currency: payload.currency,
      quote_side: payload.quote_side,
      clean_price_per_100: payload.clean_price_per_100,
      accrued_interest_per_100: payload.accrued_interest_per_100,
      // Two acquisition times with genuinely different lifetimes. At lookup
      // they are the same event, but a refresh re-acquires only the quote --
      // the Bond Master fields and raw descriptions keep the timestamp of the
      // lookup that actually fetched them.
      quote_acquired_at: payload.acquired_at,
      bond_master_acquired_at: payload.acquired_at,
      source_system: payload.source_system,
      // Every field here is either a confirmed Bloomberg mnemonic's real value
      // or null pending real-DAPI confirmation -- never guessed.
      // `bond_master_raw` carries the confirmed-but-display-only mnemonics,
      // which must never be read into currentDraft's typed schema.
      bond_master: payload.bond_master || {},
      bond_master_raw: payload.bond_master_raw || {},
    };

    currentDraft = buildInitialDraftFromBloomberg(resolvedBloombergBond);
    setDerivedFormFromDraft();
    renderRouteMetadata();
    renderOverrideProvenance();

    els.statusText.textContent = "Bloomberg bond loaded";
    renderResolvedBondPanel();
    renderBondMaster(resolvedBloombergBond);
    syncDraftGating();
  }

  // Prices the current draft through the existing reviewed builder -- reuses
  // POST /api/case unchanged rather than adding any new pricing route or logic:
  // a full case dict in, {case, overlay, context, display} out.
  async function priceCurrentDraft() {
    if (!currentDraft || !resolvedBloombergBond) return;
    if (sourcedQuoteInvalidated) return;
    if (unresolvedGroups(currentDraft).length > 0) return;
    if (builderValidation.state !== "ready") return;

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
    setTradeFormFromDraft(currentDraft);
    refreshOverrideProvenance();
    renderOverrideProvenance();
    setCurrentDisplay(withOverrideProvenance(payload.display));
    renderDisplay(payload.display);
    syncDraftGating();
  }

  // Bloomberg quote refresh: prices the current draft with one fresh Bloomberg
  // bond quote in place of its own -- exactly like Price, except the bridge
  // substitutes one live quote before pricing. Reuses the existing POST
  // /api/case/bloomberg route unchanged.
  async function refreshBloombergAndPrice() {
    if (!currentDraft || !resolvedBloombergBond) return;
    // Deliberately ignores the instrument group: re-sourcing an invalidated
    // quote is exactly what this action is for.
    const outstanding = unresolvedGroups(currentDraft).filter(
      (group) => group.id !== "instrument"
    );
    if (outstanding.length > 0) return;
    if (builderValidation.state !== "ready") return;

    const quoteSide = getOptionalToggleValue(els.bondQuoteSideToggle);
    if (!quoteSide) {
      renderLookupInputError("Select a Quote Side before refreshing.");
      return;
    }

    // Build the exact case being sent, then adopt that same object on success.
    //
    // The route is about to source a spot quote on `quoteSide` and substitute
    // it into this case before pricing, and the reviewed request contract
    // requires `forward_clean_price_input.quote_side` to equal
    // `bond_quote.quote_side` (bli_standalone_option_request.py). So the side
    // has to be mirrored *before* the request -- mirroring it afterwards can
    // never run, because the builder rejects the mismatch first and the whole
    // refresh 400s. That is why this function constructs the request case
    // up front instead of mutating `currentDraft` piecemeal after the fact.
    // A deep copy, not a spread: a shallow copy still aliases every nested
    // object, so an edit made while the request is in flight would reach
    // through into the case already serialized and sent.
    const requestCase = JSON.parse(JSON.stringify(currentDraft));
    requestCase.forward_clean_price_input.quote_side = quoteSide;
    const overlay = extractOverlayFromDraft(requestCase);
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
          case: requestCase,
          overlay,
          bloomberg_security: resolvedBloombergBond.qualifiedIdentifier,
          quote_side: quoteSide,
        }),
        signal: controller.signal,
      });
      payload = await response.json();
    } catch (err) {
      if (isStaleBloombergRequest(generation)) return;
      renderRefreshFailure("Bloomberg refresh failed: " + err.message);
      return;
    }
    if (isStaleBloombergRequest(generation)) return;
    if (!response.ok) {
      renderRefreshFailure(payload.error || "Bloomberg refresh failed.");
      return;
    }

    // Adopt the case the server actually priced -- never one assembled here.
    //
    // The route returns `{case, display}`, where `case` is the very envelope
    // it priced: the sent case with the live quote and the acquisition
    // timestamp (T2) already substituted by the one function that performs
    // those substitutions. The browser adopts it verbatim.
    //
    // This is deliberately the whole of the state transition. Reconstructing
    // the priced case client-side is what produced four consecutive rounds of
    // review findings on this function -- an incomplete adoption, a stale
    // quote, a restamped provenance timestamp, an aliased shallow copy. None
    // of those are possible when the browser does not build the object at all.
    const pricedCase = payload.case;
    const display = payload.display;
    currentDraft = pricedCase;

    // Only the *quote* was re-acquired. The Bond Master fields and the raw
    // display-only descriptions still come from the lookup that fetched them,
    // so `bond_master_acquired_at` is deliberately left where it was: claiming
    // that evidence was acquired at T2 would be a false provenance statement
    // about data this refresh never touched.
    const liveQuote = display.live_bloomberg_quote || null;
    if (liveQuote) {
      resolvedBloombergBond.quote_acquired_at =
        liveQuote.acquired_at || resolvedBloombergBond.quote_acquired_at;
      if (liveQuote.currency) resolvedBloombergBond.currency = liveQuote.currency;
      if (liveQuote.quote_side) resolvedBloombergBond.quote_side = liveQuote.quote_side;
      resolvedBloombergBond.clean_price_per_100 = liveQuote.clean_price_per_100;
      resolvedBloombergBond.accrued_interest_per_100 = liveQuote.accrued_interest_per_100;
    }

    // The refresh succeeded, so the sourced state is trustworthy again.
    sourcedQuoteInvalidated = false;
    setDerivedFormFromDraft();
    setTradeFormFromDraft(currentDraft);
    renderResolvedBondPanel();
    renderBondMaster(resolvedBloombergBond);
    renderRouteMetadata();
    refreshOverrideProvenance();
    renderOverrideProvenance();

    renderContext(extractContextFromDraft(currentDraft));
    setCurrentDisplay(withOverrideProvenance(display));
    renderDisplay(display);
    syncDraftGating();
  }

  // The exported run carries the same override provenance the Advanced section
  // shows, so an exported artifact can always be traced back to what Shiori
  // sourced and what the trader had to supply. The pricing display dict itself
  // is never otherwise touched.
  function withOverrideProvenance(display) {
    const records = overrideProvenanceRecords();
    if (records.length === 0) return display;
    return { ...display, trader_override_provenance: records };
  }

  function clearDraft() {
    invalidatePendingBondLookupRequest();
    resetRunState();
    els.statusText.textContent = "No bond loaded";
    syncDraftGating();
  }

  // Download the current run as JSON/Markdown, reusing only the existing pure
  // export helpers server-side. Sends exactly the display dict active at click
  // time; a Price/Refresh/Clear/lookup action can change currentDisplay while
  // this request is in flight, so the response is checked against
  // displayGeneration before it is ever turned into a download.
  async function downloadCurrentRun(format) {
    if (!currentDisplay) return; // mirrors the is-disabled state

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
    if (generation !== displayGeneration) return; // the displayed run changed
    if (!response.ok) return;

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

  // --- Wiring ---------------------------------------------------------------

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
  els.editCurveBtn.addEventListener("click", () => {
    setAdvancedCollapsed(false);
    const target = document.querySelector("#curve-rows .curve-tenor-input");
    if (target) {
      target.focus({ preventScroll: true });
      target.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  });
  els.unresolvedGotoBtn.addEventListener("click", revealUnresolvedFocus);

  els.priceBtn.addEventListener("click", priceCurrentDraft);
  els.clearBtn.addEventListener("click", clearDraft);
  els.downloadJsonBtn.addEventListener("click", () => downloadCurrentRun("json"));
  els.downloadMarkdownBtn.addEventListener("click", () => downloadCurrentRun("markdown"));
  els.bloombergRefreshBtn.addEventListener("click", refreshBloombergAndPrice);
  els.loadBloombergBondBtn.addEventListener("click", loadBloombergBond);

  // No bootstrap: the page starts with nothing loaded at all. Advanced starts
  // collapsed, and syncDraftGating establishes the correct all-hidden/
  // all-disabled initial state without any network call.
  setAdvancedCollapsed(true);
  clearTraderForm();
  syncDraftGating();

  // Test-only, read-only accessors: they change no stored value, API response,
  // conversion, or pricing behavior.
  window.__shioriTestGetCurrentDraft = () => currentDraft;
  window.__shioriTestUnresolvedGroupIds = () =>
    unresolvedGroups(currentDraft).map((group) => group.id);
  window.__shioriTestOrdinaryWorkflowGroupIds = () => ORDINARY_WORKFLOW_GROUP_IDS.slice();
  window.__shioriTestOverrideProvenance = () => overrideProvenanceRecords();
  window.__shioriTestBuilderValidationState = () => builderValidation.state;
  window.__shioriTestSourcedQuoteInvalidated = () => sourcedQuoteInvalidated;
})();
