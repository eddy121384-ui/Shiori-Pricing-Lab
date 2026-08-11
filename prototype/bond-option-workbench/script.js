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
//   - The eight non-market Advanced technical fields (day count, bond type,
//     ex-dividend days, last coupon date, status, reporting date, and the two
//     settlement dates) are, since Issue #157, pre-filled for a supported UST
//     fixed-coupon bullet by the server-side resolver, each stamped with the
//     tier it came from -- BLOOMBERG_AUTO, SHIORI_DERIVED, UST_PROFILE_DEFAULT
//     or TRADER_OVERRIDE. Since Issue #161 they resolve *per field*: one
//     field the resolver cannot fill comes back BLOCKED for the trader and
//     never blanks the other seven. They stay in Advanced and stay editable
//     -- except for a bond whose product/schedule the pricing path cannot
//     carry at all, where they are filled with nothing, disabled, and no
//     "Go to this input" is offered, because no edit there could make the
//     ticket price. Shiori still sets none of them from a Bloomberg
//     description string.
//   - ADVANCED_OVERRIDE_REQUIRED / UNRESOLVED market inputs: forward clean
//     price, direct price vol, and the Option Discount Curve. These are the
//     three main-screen review groups, each stating the real Bloomberg
//     evidence for why it cannot be sourced.
//
// **Trader-desk input formats (Issue #161).** Strike and Forward Clean Price
// accept Treasury 32nds (98-16, 98-16+, 98-164, 99-032) as well as decimals,
// and Notional accepts K/M shorthand -- each normalized here by one
// deterministic parser into the canonical number the existing typed contract
// already expects, with the normalized value previewed beside the field and a
// malformed entry rejected with an explicit message rather than silently
// becoming null or 0. The 32nds string and the "1.5M" string never reach the
// draft, the builder, the engine, or either export. The Expiry UTC offset is
// pre-filled to +08:00 on a new ticket and is the trader's to change; nothing
// re-writes it for the life of that ticket.
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
    strikeNormalized: document.getElementById("strike-normalized-preview"),
    notional: document.getElementById("notional-input"),
    notionalNormalized: document.getElementById("notional-normalized-preview"),
    expiryDatetime: document.getElementById("expiry-datetime-input"),
    expiryOffset: document.getElementById("expiry-offset-input"),
    expiryDerivedPreview: document.getElementById("expiry-derived-preview"),

    // Market review groups
    marketReviewSummary: document.getElementById("market-review-summary"),
    forwardPrice: document.getElementById("forward-price-input"),
    forwardNormalized: document.getElementById("forward-normalized-preview"),
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
    conventionProfileSelect: document.getElementById("convention-profile-select"),
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
    advancedProfileStatus: document.getElementById("advanced-profile-status"),
    provDayCount: document.getElementById("prov-day-count"),
    provBondType: document.getElementById("prov-bond-type"),
    provExDividendDays: document.getElementById("prov-ex-dividend-days"),
    provLastCouponDate: document.getElementById("prov-last-coupon-date"),
    provBondStatus: document.getElementById("prov-bond-status"),
    provReportingDate: document.getElementById("prov-reporting-date"),
    provForwardSettlementDate: document.getElementById("prov-forward-settlement-date"),
    provOptionSettlementDate: document.getElementById("prov-option-settlement-date"),
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
    "Issue #157 approves one narrow UST fixed-coupon bullet profile to own " +
    "these fields; outside it Shiori still fills nothing, and inside it the " +
    "value comes from the profile or a reviewed derivation -- never from " +
    "mapping one of these strings into a typed enum.";
  const EVIDENCE_TIMING =
    "Issue #149: no Bloomberg reference field describes an OTC option's own cash " +
    "settlement date. SETTLE_DT and DAYS_TO_SETTLE describe the cash bond's " +
    "standard settlement -- a different role that must not be conflated. Issue " +
    "#157 approves, for supported USTs only, Reporting Date = Valuation Date and " +
    "settlement one U.S. government-bond business day after expiry on the " +
    "existing QuantLib U.S. government-bond calendar. Shiori still writes no " +
    "holiday table of its own and rolls nothing outside that profile.";
  // "At least one", not "the fields above": the loader permits independent
  // partial misses, so an unknown CPN_FREQ leaves coupon_frequency null while
  // coupon, the dates and the flags all came back populated. Calling those
  // empty would misreport fields Bloomberg actually returned.
  const EVIDENCE_BOND_MASTER =
    "Bond Master values are read verbatim from the DAPI mnemonics confirmed in " +
    "PR #141. At least one of them came back empty for this security -- each " +
    "field shown as Not available is one this security's response did not return.";
  const EVIDENCE_SOURCED_STATE_INVALIDATED =
    "The Bloomberg request for this run failed, so the previously sourced quote " +
    "and instrument data can no longer be shown or priced against -- Shiori will " +
    "not fall back to them. Your own trade inputs are untouched.";
  // Deliberately its own text rather than the invalidated-state evidence above,
  // which asserts a Bloomberg failure that did not happen on this path.
  //
  // It states the mismatch and nothing about request history, because no claim
  // about request history is true in every case this panel appears: usually no
  // request was made at all, but changing the side while a refresh or lookup is
  // outstanding aborts one that had already been sent. Saying "no request was
  // made or failed" would simply swap one false history for another, so it says
  // neither -- the mismatch itself is what the trader has to act on, and it is
  // true either way.
  const EVIDENCE_QUOTE_SIDE_MISMATCH =
    "A quote is only valid for the side it was sourced on. The quote backing " +
    "this run was sourced on one side and Quote Side now reads another, so " +
    "Shiori neither prices the unselected side nor relabels this quote as the " +
    "selected one. The sourced quote itself is unchanged.";

  // --- UST Advanced-field profile (Issue #157) -----------------------------
  //
  // The eight non-market Advanced technical fields are no longer typed one by
  // one. After a Bloomberg Load -- and again whenever the expiry changes --
  // the page asks POST /api/bond/advanced-profile, whose whole answer is computed
  // server-side by the reviewed deterministic resolver
  // (pricing/bli_ust_fixed_coupon_profile.py). This file decides *nothing*
  // about conventions: it never contains a day-count, bond-type or status
  // literal, never derives a coupon date, and owns no calendar. It writes the
  // returned values into the same controls the trader uses, records which of
  // the four provenance tiers each one came from, and stops re-deriving any
  // field the trader has taken over.

  // The export's own source_system label for a profile-supplied value.
  //
  // Issue #161 follow-up item 6 (compatibility correction): this is never
  // computed here. Each registered profile states its own explicit
  // `source_system` server-side (`BLIConventionProfile.source_system`) --
  // UST keeps its pre-existing, already-shipped
  // `SHIORI_UST_FIXED_COUPON_PROFILE` unchanged, while US_CORPORATE and
  // GERMAN_GOVT get their own distinct labels. `/api/bond/advanced-profile`'s
  // response now carries that value verbatim as `source_system`, and this
  // file reads it from the last resolved answer rather than deriving a
  // string from the selected profile's name -- deriving it here would be
  // exactly the second, independently-typed copy of server truth this file
  // avoids everywhere else (see `renderConventionProfilePicker`).
  function conventionProfileSourceSystem() {
    return advancedProfile && typeof advancedProfile.source_system === "string"
      ? advancedProfile.source_system
      : MANUAL_SOURCE_SYSTEM;
  }
  const TRADER_OVERRIDE_BASIS = "TRADER_OVERRIDE";
  // Mirrors the resolver's own PROVENANCE_BLOCKED label. Not a value tier:
  // it names a field the resolver refused to fill and handed to the trader.
  const PROVENANCE_BLOCKED = "BLOCKED";

  // The currently selected Convention Profile -- browser-owned state, sent
  // as an explicit input on every /api/bond/advanced-profile request (Issue
  // #157 P1-1 correction). The server never infers, defaults, or fabricates
  // it; it only validates the selection this file sends and echoes it back.
  //
  // Issue #161 makes it a real, visible selection rather than a constant, and
  // the follow-up correction makes it the *trader's* selection alone: it
  // stays `null` until the trader picks, and while it is `null` no profile
  // request is sent at all -- an Advanced field filled from an unselected
  // profile would be a value nobody chose. Shiori narrows the choices (see
  // `refreshConventionProfileCandidates`) but never makes one. What the page
  // asks for is a *profile*, never the eight technical fields.
  let selectedConventionProfile = null;
  // The last answer from /api/bond/convention-profile/candidates. Loading a
  // different bond resets it, the selection and the override flag together
  // with the rest of the run state.
  let conventionProfileCandidates = null;
  let conventionProfileOverridden = false;
  let conventionProfileGeneration = 0;
  let conventionProfileTransportError = null;

  // What each market-independent provenance tier means, in the trader's own
  // terms. The tier string itself always comes from the server.
  const PROVENANCE_DESCRIPTIONS = {
    BLOOMBERG_AUTO:
      "a typed value Bloomberg itself returned for this bond, mapped by a confirmed " +
      "mnemonic — never read off a description string",
    SHIORI_DERIVED:
      "derived mechanically by Shiori from this run's own loaded bond terms, valuation " +
      "date and expiry, using existing reviewed logic",
    TRADER_OVERRIDE:
      "you set this value yourself; Shiori will not re-derive or reset it for this bond",
    BLOCKED:
      "Shiori has no safe value or method for this one field, so it is yours to set — " +
      "every other field on this bond keeps the value Shiori resolved for it",
  };

  // The remaining tier is the selected profile's own, and there is now more
  // than one profile, so its label and its description are both derived from
  // the tier string the server sent rather than written out per market
  // (Issue #161 follow-up item 6). A German government bond's values must
  // never be described, or exported, as the UST profile's.
  const PROFILE_DEFAULT_TIER_SUFFIX = "_PROFILE_DEFAULT";

  function profileNameFromTier(tier) {
    return typeof tier === "string" && tier.endsWith(PROFILE_DEFAULT_TIER_SUFFIX)
      ? tier.slice(0, -PROFILE_DEFAULT_TIER_SUFFIX.length)
      : null;
  }

  function provenanceDescription(tier) {
    const profileName = profileNameFromTier(tier);
    if (profileName !== null) {
      return `the ${profileName} convention profile's own approved value for this field`;
    }
    return PROVENANCE_DESCRIPTIONS[tier] || "";
  }

  // path -> the control that holds it and the readout that explains it. The
  // paths are spelled exactly as the resolver's own
  // ``UST_ADVANCED_FIELD_PATHS``, and the browser regression tests assert each
  // one actually lands in its control -- a drift between the two sides is a
  // failing test, not a field that quietly stays blank.
  const PROFILE_FIELDS = [
    {
      path: "bond_reference_data_universe.0.day_count",
      control: () => els.dayCount,
      provenanceEl: () => els.provDayCount,
    },
    {
      path: "bond_reference_data_universe.0.bond_type",
      control: () => els.bondTypeSelect,
      provenanceEl: () => els.provBondType,
    },
    {
      path: "bond_reference_data_universe.0.ex_dividend_days",
      control: () => els.exDividendDays,
      provenanceEl: () => els.provExDividendDays,
    },
    {
      path: "bond_reference_data_universe.0.last_coupon_date",
      control: () => els.lastCouponDate,
      provenanceEl: () => els.provLastCouponDate,
    },
    {
      path: "bond_reference_data_universe.0.status",
      control: () => els.bondStatus,
      provenanceEl: () => els.provBondStatus,
    },
    {
      path: "reporting_date",
      control: () => els.reportingDate,
      provenanceEl: () => els.provReportingDate,
    },
    {
      path: "forward_settlement_date",
      control: () => els.forwardSettlementDate,
      provenanceEl: () => els.provForwardSettlementDate,
    },
    {
      path: "option_settlement_date",
      control: () => els.optionSettlementDate,
      provenanceEl: () => els.provOptionSettlementDate,
    },
  ];

  const PROFILE_FIELD_BY_PATH = new Map(PROFILE_FIELDS.map((field) => [field.path, field]));

  // The two fields the resolver recomputes on every expiry change (mirrors
  // the server's own EXPIRY_DEPENDENT_FIELD_PATHS). Used to withdraw a stale
  // settlement date the instant expiry (or any other profile-key input)
  // changes, rather than leaving it in the draft until the next profile
  // response happens to overwrite it (Issue #157 P1-2 correction).
  const EXPIRY_DEPENDENT_PROFILE_FIELDS = PROFILE_FIELDS.filter(
    (field) => field.path === "forward_settlement_date" || field.path === "option_settlement_date"
  );

  // Which of the eight paths each Advanced workflow group owns, so a group
  // reports the blocked fields it is actually about (Issue #161).
  const BOND_REFERENCE_PROFILE_PATHS = PROFILE_FIELDS.map((field) => field.path).filter((path) =>
    path.startsWith("bond_reference_data_universe.")
  );
  const TIMING_PROFILE_PATHS = PROFILE_FIELDS.map((field) => field.path).filter(
    (path) => !path.startsWith("bond_reference_data_universe.")
  );

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

  // Mirrors reference_data/_validation.py's `_parse_iso_date` exactly (Issue
  // #161 P3 correction): the strict `YYYY-MM-DD` shape, then a real calendar
  // round-trip so "2024-02-30" is rejected too, not just a malformed shape
  // like "20241231". Bloomberg's own ISSUE_DT / MATURITY / FIRST_CPN_DT
  // mnemonics pass through with zero format validation
  // (data/bloomberg_bond_quote.py's `_passthrough_bloomberg_string`), so a
  // non-ISO string reaching the draft is a real, reachable Bloomberg
  // response shape, not a synthetic edge case -- and `present()` alone
  // (non-blank-string only) cannot tell it apart from a genuinely usable
  // date. This is the one place this file re-implements the resolver's own
  // date-validity algorithm rather than reusing its answer directly,
  // because the checks below must be correct immediately after Bloomberg
  // Load, before the async POST /api/bond/advanced-profile round-trip completes --
  // never guessed, never coerced, same grammar and same calendar rule the
  // resolver itself uses.
  const ISO_DATE_SHAPE = /^\d{4}-\d{2}-\d{2}$/;

  function isValidIsoDate(value) {
    if (typeof value !== "string" || !ISO_DATE_SHAPE.test(value)) return false;
    const [year, month, day] = value.split("-").map(Number);
    if (month < 1 || month > 12) return false;
    const parsed = new Date(Date.UTC(year, month - 1, day));
    return (
      parsed.getUTCFullYear() === year &&
      parsed.getUTCMonth() === month - 1 &&
      parsed.getUTCDate() === day
    );
  }

  // --- The two kinds of "not filled", kept apart (Issue #161) --------------
  //
  // A product/schedule refusal and a field-level blocker look identical in a
  // form -- an empty control -- and are opposite things to a trader. The
  // first can never be repaired here, so offering an input for it is a fake
  // exit; the second is exactly what the Advanced override is for. Every
  // message and every control state below branches on which one it is, and
  // nothing infers one from the other.

  // True once the resolver has actually refused the product/schedule. Not
  // true while no answer has arrived (null) -- Shiori not knowing yet is not
  // the same as Shiori saying no.
  // True when the current pricing path cannot carry this bond at all, so the
  // Advanced controls are disabled rather than left looking like a way out.
  //
  // Two distinct situations, both genuinely unrepairable here (Issue #161):
  // the selected profile refused the bond's product shape or coupon
  // schedule, and -- new with the profile registry -- *no* registered
  // convention profile states conventions for this bond's own confirmed
  // currency and coupon frequency at all. The second is not "pick a
  // profile": there is nothing to pick, so offering the eight fields would
  // be exactly the fake exit this issue exists to remove.
  function noProfileCoversThisBond() {
    return (
      conventionProfileCandidates !== null &&
      (conventionProfileCandidates.candidates || []).length === 0
    );
  }

  function productUnsupported() {
    return (advancedProfile !== null && advancedProfile.supported === false) ||
      noProfileCoversThisBond();
  }

  function productRejectionReasons() {
    if (advancedProfile && advancedProfile.rejection_reasons) {
      return advancedProfile.rejection_reasons;
    }
    if (noProfileCoversThisBond()) return conventionProfileCandidates.reasons || [];
    return [];
  }

  // path -> the resolver's own reason it refused that one field. Empty
  // whenever the whole product was refused: no field-level claim is made
  // about a bond that never got as far as field resolution.
  function blockedFieldReasons() {
    const blocked = new Map();
    ((advancedProfile && advancedProfile.unresolved_fields) || []).forEach((item) => {
      blocked.set(item.path, item.reason);
    });
    return blocked;
  }

  // The blocked-field reasons for one Advanced group's own paths, so a group
  // explains the fields it actually owns rather than every blocked field.
  function blockedReasonsForPaths(paths) {
    const blocked = blockedFieldReasons();
    return paths.filter((path) => blocked.has(path)).map((path) => `${path}: ${blocked.get(path)}`);
  }

  // Which of last_coupon_date/status cannot resolve because a Bloomberg Bond
  // Master date (issue_date, maturity_date, first_coupon_date) is itself
  // missing *or not a valid, real calendar YYYY-MM-DD date* (Issue #161 P3
  // correction). None of those three has an Advanced override anywhere on
  // this page -- the resolver already leaves the affected field unresolved
  // rather than BLOCKED for exactly this reason (see
  // bli_ust_fixed_coupon_profile.py's module docstring), so this reads the
  // same underlying fact straight from the draft's own Bond Master record,
  // independent of the resolver's per-run answer, to render the group
  // honestly and to keep its Go-to route closed off (a real Advanced entry
  // here can never make this ticket price; the fix is a fresh, successful
  // Bloomberg Load, which the "Bloomberg Bond Master" group already reports
  // and blocks on).
  //
  // P3 correction: the first revision used `present()` here -- a bare
  // non-blank-string check -- which a malformed-but-non-empty Bloomberg date
  // (e.g. "20241231", no dashes) slips straight past. That silently
  // re-opened the exact fake exit this function exists to close: the
  // control stayed enabled, read "yours to enter", and Price never
  // explained the real, unfixable cause until the typed builder finally
  // rejected the draft after the trader had already "completed" the
  // ticket. `isValidIsoDate` is the same strict grammar-plus-calendar check
  // the resolver itself applies, so a malformed date is treated exactly
  // like a missing one here too.
  function bondMasterGapPaths() {
    if (!currentDraft) return [];
    const record = referenceRecord(currentDraft);
    const gaps = [];
    if (
      !(
        isValidIsoDate(record.issue_date) &&
        isValidIsoDate(record.maturity_date) &&
        isValidIsoDate(record.first_coupon_date)
      )
    ) {
      gaps.push("bond_reference_data_universe.0.last_coupon_date");
    }
    if (!isValidIsoDate(record.maturity_date)) {
      gaps.push("bond_reference_data_universe.0.status");
    }
    return gaps;
  }

  // True when the Quote Side on screen is not the side the draft's quote was
  // actually sourced on.
  //
  // Quote Side and the sourced draft are independent state, so they can
  // diverge with no request in flight at all -- select a different side on a
  // completed ticket and nothing about the draft changes. Invalidating pending
  // requests cannot reach that case, because the Price request is started
  // *after* the change. This is computed rather than latched: selecting the
  // original side back restores the run exactly as it was, with no Bloomberg
  // call and nothing discarded.
  function sourcedQuoteSideMismatch() {
    if (currentDraft === null || resolvedBloombergBond === null) return false;
    if (sourcedQuoteInvalidated) return false;
    const selected = getOptionalToggleValue(els.bondQuoteSideToggle);
    if (!selected) return false;
    return selected !== currentDraft.bond_quote.quote_side;
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
        draft !== null &&
        resolvedBloombergBond !== null &&
        !sourcedQuoteInvalidated &&
        !sourcedQuoteSideMismatch(),
      locator: "#bond-identifier-input",
      unresolved: () =>
        sourcedQuoteSideMismatch()
          ? {
              title: "Quote Side does not match the sourced quote",
              missing: `A Bloomberg quote sourced on ${getOptionalToggleValue(
                els.bondQuoteSideToggle
              )}.`,
              why:
                "This run's quote was sourced on " +
                `${currentDraft.bond_quote.quote_side}, and Quote Side now reads ` +
                `${getOptionalToggleValue(els.bondQuoteSideToggle)}. Shiori will not ` +
                "price a side the trader did not select, and will not relabel a " +
                "quote it did not source on that side.",
              evidence: EVIDENCE_QUOTE_SIDE_MISMATCH,
              // States what is retained, not what did or did not happen: a side
              // change on a settled ticket discards nothing, but the same click
              // aborts a refresh or lookup that was already in flight. The
              // retained state below is true either way.
              next:
                "Press Refresh Bloomberg & Price to re-source this bond on the " +
                "selected side, or select the original side back. Your trade " +
                "inputs, overrides and curve nodes are untouched, and so is the " +
                "quote already sourced for this run.",
            }
          : sourcedQuoteInvalidated
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
              // This branch renders both before the first lookup and after a
              // failed one has reset the run, so it cannot say whether a request
              // has been made. A failed lookup reports its own outcome in the
              // error banner; this states only what is missing and why.
              evidence:
                "Every instrument fact on this route comes from one Bloomberg " +
                "lookup, so until one has resolved successfully there is nothing " +
                "for a run to be anchored to.",
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
      // A malformed entry and a blank one are the same structural state, but
      // not the same thing to say: the trader who typed "98-33" needs to be
      // told what is wrong with it, not that nothing was entered.
      unresolved: () =>
        inputNormalization.strike.error !== null
          ? {
              title: "Strike cannot be read",
              missing: "The strike clean price, per 100.",
              why: inputNormalization.strike.error,
              evidence: "Not applicable — this is a trader input, not a sourced value.",
              next:
                "Correct the strike in the Trade group. Shiori normalizes a Treasury " +
                "quote to decimal per 100 for you, but it will not guess at a quote it " +
                "cannot read.",
            }
          : {
              title: "Strike has not been entered",
              missing: "The strike clean price, per 100.",
              why: "This is a genuine trade decision on a PRICE payoff basis.",
              evidence: "Not applicable — this is a trader input, not a sourced value.",
              next:
                "Enter the strike in the Trade group, as a decimal or a Treasury quote " +
                "(98-16, 98-16+, 98-164, 99-032). Shiori normalizes it to decimal per 100.",
            },
    },
    {
      id: "notional",
      label: "Notional",
      advanced: false,
      resolved: (draft) => present(draft.bond_option.notional),
      locator: "#notional-input",
      unresolved: () =>
        inputNormalization.notional.error !== null
          ? {
              title: "Notional cannot be read",
              missing: "The trade notional.",
              why: inputNormalization.notional.error,
              evidence: "Not applicable — this is a trader input, not a sourced value.",
              next:
                "Correct the notional in the Trade group. Shiori reads K and M " +
                "shorthand, but it will not part-read an amount it cannot parse in full.",
            }
          : {
              title: "Notional has not been entered",
              missing: "The trade notional.",
              why: "This is a genuine trade decision and scales the total premium.",
              evidence: "Not applicable — this is a trader input, not a sourced value.",
              next: "Enter the notional in the Trade group — 1,000,000, 1M or 1.5M all work.",
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
          "Set the expiry date and time. The UTC offset starts at +08:00 " +
          "(Asia/Taipei) on a new ticket and is yours to change (for example Z or " +
          "-05:00); Shiori keeps whichever offset you leave there, exactly, and " +
          "never guesses the date or the time of day.",
      },
    },
    {
      id: "forward-review",
      label: "Forward",
      advanced: false,
      resolved: (draft) =>
        present(draft.forward_clean_price_input.forward_clean_price_per_100),
      locator: "#forward-price-input",
      unresolved: () =>
        inputNormalization.forward.error !== null
          ? {
              title: "Forward clean price cannot be read",
              missing: "The forward clean price, per 100, at forward settlement.",
              why: inputNormalization.forward.error,
              evidence: EVIDENCE_FORWARD,
              next:
                "Correct the forward clean price. It accepts the same Treasury quote " +
                "formats as the strike and normalizes to decimal per 100; Shiori will " +
                "not guess at a quote it cannot read.",
            }
          : {
              title: "Forward clean price cannot be sourced from Bloomberg",
              missing: "The forward clean price, per 100, at forward settlement.",
              why:
                "OPT_UNDL_FORWARD_PX is the only candidate route and it is not " +
                "applicable to a cash bond on the current DAPI route.",
              evidence: EVIDENCE_FORWARD,
              next:
                "Enter the forward clean price you are pricing against, as a decimal or " +
                "a Treasury quote (98-16+, 99-032). It is recorded as a trader override " +
                "with provenance. Shiori will not reconstruct a forward from spot price, " +
                "repo, FTP, MMkt or any par rate.",
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
      // Issue #161 P3 correction: the three schedule dates use
      // `isValidIsoDate`, not `present`. A malformed-but-non-blank Bloomberg
      // date (Bloomberg's own ISSUE_DT/MATURITY/FIRST_CPN_DT mnemonics carry
      // no format guarantee -- see `isValidIsoDate`'s own comment) used to
      // read as "present" and let this group report resolved, so the
      // primary unresolved panel showed the wrong group entirely (the
      // Advanced "Bond reference" one, whose fields looked editable and
      // fixable) instead of this one's honest "run Bloomberg Load again".
      // coupon / coupon_frequency / callable_flag / sinkable_flag are not
      // date-shaped and keep the plain presence check.
      resolved: (draft) => {
        const record = referenceRecord(draft);
        return (
          present(record.coupon) &&
          present(record.coupon_frequency) &&
          isValidIsoDate(record.maturity_date) &&
          isValidIsoDate(record.issue_date) &&
          isValidIsoDate(record.first_coupon_date) &&
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
      unresolved: () => {
        const blocked = blockedReasonsForPaths(BOND_REFERENCE_PROFILE_PATHS);
        const gaps = bondMasterGapPaths();
        if (blocked.length === 0 && gaps.length > 0) {
          // Issue #161 P2 correction: every remaining field here traces to a
          // Bloomberg Bond Master date this security's response did not
          // return, and none of those dates has an Advanced override
          // anywhere on this page. Filling last_coupon_date or status by
          // hand cannot make this ticket price -- the fix is a fresh,
          // successful Bloomberg Load. goToDisabled closes off the one
          // route this group would otherwise still offer.
          return {
            title: "Bond reference fields are not filled for this bond",
            missing:
              "Day count, bond type, ex-dividend days, last coupon date and bond " +
              "status.",
            why:
              "Shiori filled every one of these it could. The rest depend on a " +
              "Bloomberg Bond Master date this security's response did not return " +
              "(" +
              gaps.join(", ") +
              "), and no Advanced control anywhere on this page can supply that " +
              "date -- typing a value into these controls cannot repair it.",
            evidence: EVIDENCE_BOND_REFERENCE,
            next:
              "See Bloomberg Bond Master below and run Bloomberg Load again for " +
              "this security. No Advanced field here is a route past it.",
            goToDisabled: true,
          };
        }
        return {
          title: "Bond reference fields are not filled for this bond",
          missing:
            "Day count, bond type, ex-dividend days, last coupon date and bond " +
            "status.",
          why:
            blocked.length > 0
              ? "Shiori filled every one of these it could and stopped at the rest, " +
                "which are yours to set: " +
                blocked.join(" · ")
              : "These are profile-owned or derivation-owned decisions, and the only " +
                "approved profile is the narrow UST fixed-coupon bullet one.",
          evidence: EVIDENCE_BOND_REFERENCE,
          next:
            "Open Advanced → Bond reference and set the outstanding fields yourself. " +
            "Each entry is recorded as a trader override with provenance, beside the " +
            "Bloomberg text that is evidence for it, and the fields Shiori did fill " +
            "keep their own values.",
        };
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
      unresolved: () => {
        const blocked = blockedReasonsForPaths(TIMING_PROFILE_PATHS);
        return {
          title: "Settlement and reporting dates are not filled for this bond",
          missing:
            "The reporting date, the forward settlement date and the option " +
            "settlement date.",
          why:
            blocked.length > 0
              ? "Shiori filled every one of these it could and stopped at the rest, " +
                "which are yours to set: " +
                blocked.join(" · ")
              : "No Bloomberg field carries an OTC option's own cash settlement date, " +
                "and the only approved calendar rule is the narrow UST fixed-coupon " +
                "bullet profile's one-business-day roll on the U.S. government-bond " +
                "calendar.",
          evidence: EVIDENCE_TIMING,
          next:
            "Enter the expiry, or open Advanced → Timing & settlement and set the " +
            "outstanding dates yourself. Each is recorded as a trader override with " +
            "provenance.",
        };
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

  // The last UST profile answer for the currently loaded bond -- null until one
  // arrives, and cleared with the draft. Holds the server's own
  // {supported, rejection_reasons, fields, pending_field_paths,
  // unresolved_fields}.
  let advancedProfile = null;

  // The last parse of each trader-format numeric input (Issue #161). Kept so a
  // malformed entry is explained beside its own field and in the unresolved
  // panel, instead of reading as an ordinary "not entered yet" blank.
  let inputNormalization = {
    strike: inputBlank(),
    notional: inputBlank(),
    forward: inputBlank(),
  };

  // path -> provenance tier, for the eight Advanced technical fields only.
  // Cleared with the draft, so one bond's tiers can never describe another's.
  let fieldProvenance = new Map();

  // The subset of those paths the trader has taken over. Once a path is here,
  // no profile answer -- not a re-derivation on an expiry change, not a later
  // response, not a re-render -- writes to it again for this bond.
  let traderOverriddenPaths = new Set();

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

  // --- Trader-desk input normalization (Issue #161) ------------------------
  //
  // One deterministic parser per format, each returning the same shape:
  // `{ value, preview, error }`. `value` is always the *canonical* number the
  // existing typed contract already expects -- decimal per 100 for a price, a
  // plain number for a notional -- so nothing downstream changes: the draft,
  // the JSON sent to the builder, the pricing engine and both exports never
  // see a "98-16+" or a "1.5M" string. This is a format layer, not a pricing
  // one: no rounding, no rescaling, no re-signing, no market assumption.
  //
  // A malformed entry never becomes null-in-silence, 0, or the previous
  // value: it returns an `error` the caller renders beside the field, and the
  // draft field stays unset, which keeps the existing typed-builder Price
  // gate closed exactly as a blank field does.

  // Handle, then two 32nds digits, then an optional sub-32nd. The tail is
  // captured loosely on purpose so a wrong sub-32nd can be named in the error
  // rather than collapsing into one generic "not a quote" message.
  const TREASURY_32NDS_SHAPE = /^(\d+)-(\d{2})(.*)$/;
  const PLAIN_DECIMAL_SHAPE = /^\d+(?:\.\d+)?$/;
  const TREASURY_QUOTE_FORMS =
    "Enter a decimal (98.515625) or a Treasury quote: 98-16, 98-16+, 98-164, 99-032.";

  // A "+" is a half 32nd; a trailing digit is that many eighths of a 32nd.
  const TREASURY_PLUS_EIGHTHS = 4;
  const TREASURY_EIGHTHS_PER_32ND = 8;
  const TREASURY_32NDS_PER_POINT = 32;

  function inputProblem(error) {
    return { value: null, preview: null, error: error };
  }

  function inputBlank() {
    return { value: null, preview: null, error: null };
  }

  // Formats a normalized number for the on-screen preview only. Never used to
  // build a request body, and never rounded: `maximumFractionDigits` is set
  // wide enough that a preview can only ever show the value in full.
  function previewNumber(value) {
    return value.toLocaleString("en-US", { maximumFractionDigits: 20 });
  }

  // Bloomberg / trading-desk Treasury price -> decimal per 100.
  //
  //   98-16   -> 98 + 16/32                 = 98.5
  //   98-16+  -> 98 + (16 + 4/8)/32         = 98.515625
  //   98-164  -> 98 + (16 + 4/8)/32         = 98.515625
  //   99-032  -> 99 + (3  + 2/8)/32         = 99.1015625
  //   99-037  -> 99 + (3  + 7/8)/32         = 99.12109375
  //
  // Every one of these is an exact binary fraction, so the normalized decimal
  // is exact rather than a rounded approximation of the trader's quote.
  function parseTreasuryQuote(raw) {
    const trimmed = (raw || "").trim();
    if (trimmed === "") return inputBlank();

    if (PLAIN_DECIMAL_SHAPE.test(trimmed)) {
      const decimal = Number(trimmed);
      if (!Number.isFinite(decimal)) {
        return inputProblem(`“${trimmed}” is not a finite number. ${TREASURY_QUOTE_FORMS}`);
      }
      return {
        value: decimal,
        preview: `Normalized: ${previewNumber(decimal)} per 100`,
        error: null,
      };
    }

    const match = TREASURY_32NDS_SHAPE.exec(trimmed);
    if (!match) {
      return inputProblem(`“${trimmed}” is not a price Shiori can read. ${TREASURY_QUOTE_FORMS}`);
    }
    const handle = Number.parseInt(match[1], 10);
    const thirtySeconds = Number.parseInt(match[2], 10);
    const tail = match[3];
    if (thirtySeconds > 31) {
      return inputProblem(
        `“${trimmed}” has ${match[2]} thirty-seconds; the 32nds part must be 00–31.`
      );
    }
    let eighths = 0;
    if (tail === "+") {
      eighths = TREASURY_PLUS_EIGHTHS;
    } else if (/^\d$/.test(tail)) {
      eighths = Number.parseInt(tail, 10);
      if (eighths > 7) {
        return inputProblem(
          `“${trimmed}” ends in ${tail}; the sub-32nd digit must be 0–7 (or “+”, a half 32nd).`
        );
      }
    } else if (tail !== "") {
      return inputProblem(
        `“${trimmed}” has trailing text Shiori cannot read (“${tail}”). ${TREASURY_QUOTE_FORMS}`
      );
    }
    const value =
      handle +
      (thirtySeconds + eighths / TREASURY_EIGHTHS_PER_32ND) / TREASURY_32NDS_PER_POINT;
    return {
      value: value,
      preview: `${trimmed} · normalized ${previewNumber(value)} per 100`,
      error: null,
    };
  }

  // Trader-desk notional shorthand -> a plain positive number.
  //
  //   1K / 1k -> 1000     250K -> 250000     1M / 1m -> 1000000
  //   1.5M    -> 1500000  1000000 -> 1000000 1,000,000 -> 1000000
  //
  // Deliberately no "B" or any other suffix: Issue #161 asked for K and M
  // only, and an unrequested suffix would be grammar Shiori invented.
  const NOTIONAL_SHAPE = /^(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?([KkMm])?$/;
  const NOTIONAL_FORMS = "Enter a number like 1000000, 1,000,000, 250K, 1M or 1.5M.";
  const NOTIONAL_SUFFIX_EXPONENT = { K: 3, M: 6 };

  function parseNotional(raw) {
    const trimmed = (raw || "").trim();
    if (trimmed === "") return inputBlank();

    const match = NOTIONAL_SHAPE.exec(trimmed);
    if (!match) {
      return inputProblem(`“${trimmed}” is not a notional Shiori can read. ${NOTIONAL_FORMS}`);
    }
    const wholeDigits = match[1].replace(/,/g, "");
    const fractionDigits = match[2] || "";
    const suffix = match[3] ? match[3].toUpperCase() : null;
    const exponent = (suffix ? NOTIONAL_SUFFIX_EXPONENT[suffix] : 0) - fractionDigits.length;

    // Scaled-integer arithmetic rather than `Number(trimmed) * 1e6`: 1.1M is
    // exactly 1,100,000 here, where the float multiplication would show
    // 1100000.0000000001 in the preview and send it to the builder.
    const mantissa = Number(wholeDigits + fractionDigits);
    if (!Number.isSafeInteger(mantissa)) {
      return inputProblem(
        `“${trimmed}” has more significant digits than Shiori can represent exactly.`
      );
    }
    const value =
      exponent >= 0 ? mantissa * Math.pow(10, exponent) : mantissa / Math.pow(10, -exponent);
    if (!Number.isFinite(value) || value <= 0) {
      return inputProblem(`“${trimmed}” normalizes to ${value}; the notional must be above zero.`);
    }
    return {
      value: value,
      preview: `Normalized notional: ${previewNumber(value)}`,
      error: null,
    };
  }

  // The single point where the three trader-format controls are read. Both
  // callers -- the form -> draft pass and the write-back after a priced run --
  // go through here, so there is exactly one call site per parser and no way
  // for two readers to disagree about a format.
  function readTraderFormatInputs() {
    return {
      strike: parseTreasuryQuote(els.strikePrice.value),
      notional: parseNotional(els.notional.value),
      forward: parseTreasuryQuote(els.forwardPrice.value),
    };
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

  // Issue #161: the offset a *new ticket* starts with -- Asia/Taipei, where
  // this desk works. It is a pre-filled starting value for one control, not
  // a convention and not an assumption about the trade: the trader can type
  // any other explicit offset over it, and once they do, nothing in this
  // file writes the control again for the life of the ticket (only a whole-
  // run reset -- Clear, a second Load, a lookup failure -- restores it, and
  // that is a *new* ticket by definition).
  //
  // Only the offset is pre-filled. The expiry date and time of day are still
  // genuine trade decisions and are never guessed, and `expiry_timestamp`
  // still carries the explicit offset the trader can see, never a timezone
  // label and never the machine's local zone.
  const DEFAULT_EXPIRY_UTC_OFFSET = "+08:00";

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

  // Issue #161: every trader-format field shows either what it normalized to
  // or exactly what is wrong with it -- never a silent blank. The hint is the
  // resting state before anything is typed, so the accepted formats are
  // discoverable without a manual.
  const STRIKE_FORMAT_HINT =
    "Accepts a decimal or a Treasury quote: 98.515625, 98-16, 98-16+, 98-164, 99-032.";
  const NOTIONAL_FORMAT_HINT = "Accepts 1000000, 1,000,000, 250K, 1M or 1.5M.";

  function renderInputNormalization() {
    [
      [els.strikeNormalized, inputNormalization.strike, STRIKE_FORMAT_HINT],
      [els.notionalNormalized, inputNormalization.notional, NOTIONAL_FORMAT_HINT],
      [els.forwardNormalized, inputNormalization.forward, STRIKE_FORMAT_HINT],
    ].forEach(([target, parsed, hint]) => {
      const failed = parsed.error !== null;
      target.classList.toggle("is-invalid", failed);
      target.textContent = failed ? parsed.error : parsed.preview || hint;
    });
  }

  function renderExpiryPreview() {
    const expiry = composeExpiry();
    if (expiry.timestamp === null) {
      els.expiryDerivedPreview.textContent =
        expiry.note ||
        `Enter the expiry date and local time. The UTC offset starts at ` +
          `${DEFAULT_EXPIRY_UTC_OFFSET} (Asia/Taipei) on a new ticket — change it if ` +
          "this expiry is quoted in another zone, and Shiori keeps whichever you set.";
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

  // --- Advanced-field profile: fetch, apply, provenance ---------------------

  let advancedProfileGeneration = 0;
  let lastAdvancedProfileKey = null;
  let advancedProfileTransportError = null;

  // The complete set of inputs the server's answer depends on. A profile
  // response is adopted only while this key still reads the same, which is
  // what makes a late answer for a previous bond -- or for a previous expiry,
  // or for a profile the trader has since changed away from -- impossible to
  // apply. A null selected profile makes the key null, so nothing is
  // requested and nothing is filled until a profile is actually selected.
  function advancedProfileKey() {
    if (!currentDraft || !resolvedBloombergBond || !selectedConventionProfile) return null;
    return JSON.stringify({
      convention_profile: selectedConventionProfile,
      isin: resolvedBloombergBond.isin,
      currency: currentDraft.bond_option.currency,
      valuation_date: currentDraft.valuation_date,
      expiry_date: currentDraft.bond_option.expiry_date,
    });
  }

  function conventionProfileCandidateNames() {
    return (conventionProfileCandidates && conventionProfileCandidates.candidates) || [];
  }

  // Renders the picker from the server's own candidate list, so the options a
  // trader can choose can never drift from the profiles the resolver will
  // actually accept for *this* bond. The empty option is always present: the
  // selection starts empty and stays empty until the trader makes it, because
  // Shiori does not choose a profile (see `refreshConventionProfileCandidates`).
  function renderConventionProfilePicker() {
    const select = els.conventionProfileSelect;
    const available = conventionProfileCandidateNames();
    const wanted = ["", ...available];
    const present = Array.from(select.options).map((option) => option.value);
    if (present.join(",") !== wanted.join(",")) {
      select.innerHTML = "";
      wanted.forEach((value) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value === "" ? "Select a convention profile…" : value;
        select.appendChild(option);
      });
    }
    select.value = selectedConventionProfile || "";
    select.disabled = available.length === 0;
  }

  // Asks the server which profiles this bond's own confirmed terms leave
  // open, for the bond this request was made for -- a late answer for a
  // previous bond can never populate the next one's picker.
  //
  // **Shiori never selects a profile here.** An earlier revision adopted the
  // server's suggestion whenever the confirmed currency and coupon frequency
  // narrowed the registry to one name; Eddy withdrew that, because those two
  // facts describe a bond's cash flows rather than its issuer class, and the
  // Bloomberg field that would classify it (SECURITY_TYP) is still
  // unconfirmed. The selection stays empty until the trader makes it -- and
  // what the page then asks for is a *profile*, never the eight Advanced
  // technical fields.
  async function refreshConventionProfileCandidates() {
    if (!currentDraft || !resolvedBloombergBond) return;
    const generation = ++conventionProfileGeneration;
    const requestBody = {
      currency: currentDraft.bond_option.currency,
      bond_master: resolvedBloombergBond.bond_master || {},
    };

    let payload;
    try {
      const response = await fetch("/api/bond/convention-profile/candidates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      });
      payload = await response.json();
      if (!response.ok) {
        throw new Error(payload && payload.error ? payload.error : `HTTP ${response.status}`);
      }
    } catch (error) {
      if (generation !== conventionProfileGeneration) return;
      conventionProfileCandidates = null;
      conventionProfileTransportError = String((error && error.message) || error);
      renderConventionProfilePicker();
      renderAdvancedProfileStatus();
      return;
    }
    if (generation !== conventionProfileGeneration) return;

    conventionProfileTransportError = null;
    conventionProfileCandidates = payload;
    renderConventionProfilePicker();
    renderAdvancedProfileStatus();
    syncDraftGating();
  }

  function markTraderOverride(path) {
    if (!currentDraft) return;
    traderOverriddenPaths.add(path);
    fieldProvenance.set(path, TRADER_OVERRIDE_BASIS);
    renderFieldProvenance();
  }

  // Writes the server's values into the trader's own controls. Deliberately
  // the only place anything writes to these eight controls outside a full
  // reset, and it never touches a path the trader has taken over.
  function applyAdvancedProfileFields(profile) {
    if (!currentDraft) return;
    const supplied = new Map(
      (profile.fields || []).map((field) => [field.path, field])
    );
    let changed = false;
    PROFILE_FIELDS.forEach((field) => {
      if (traderOverriddenPaths.has(field.path)) return;
      const control = field.control();
      const suppliedField = supplied.get(field.path);
      // Not supplied and not overridden: either this bond has no profile at
      // all, or the value depends on an expiry that is not entered (or no
      // longer is). Blanking it is the honest state -- a settlement date
      // derived from an expiry the trader has since changed away from is not
      // a value Shiori still stands behind.
      const value = suppliedField
        ? suppliedField.value === null || suppliedField.value === undefined
          ? ""
          : String(suppliedField.value)
        : "";
      if (control.value !== value) {
        control.value = value;
        changed = true;
      }
      if (suppliedField) {
        fieldProvenance.set(field.path, suppliedField.provenance);
      } else {
        fieldProvenance.delete(field.path);
      }
    });
    // Only when a control actually moved: re-reading the whole form invalidates
    // any in-flight Price or Refresh, so an answer that changes nothing must
    // not cancel one.
    if (changed) applyManualInputsToDraft();
  }

  function renderFieldProvenance() {
    const blocked = blockedFieldReasons();
    const gaps = new Set(bondMasterGapPaths());
    // A product/schedule refusal is not repairable here, so these controls
    // are disabled rather than left looking like a way out (Issue #161).
    // Every other state -- including a field-level BLOCKED -- keeps them
    // editable, because there the trader's entry is a real route past it.
    const disabled = productUnsupported();
    PROFILE_FIELDS.forEach((field) => {
      const target = field.provenanceEl();
      const tier = fieldProvenance.get(field.path) || null;
      const isOverride = tier === TRADER_OVERRIDE_BASIS;
      // Issue #161 P2 correction: last_coupon_date/status cannot be repaired
      // by typing into them while the Bloomberg Bond Master date they
      // depend on is itself missing -- no Advanced control anywhere on this
      // page supplies issue_date/maturity_date/first_coupon_date. A control
      // the trader already overrode before this run reached that state
      // stays editable (an existing override is never silently taken
      // away); an untouched one is disabled rather than left looking like a
      // real route.
      const noRepairRoute = !isOverride && gaps.has(field.path);
      field.control().disabled = disabled || noRepairRoute;
      const blockedReason = tier === null ? blocked.get(field.path) : undefined;
      target.classList.toggle("is-auto", tier !== null && !isOverride);
      target.classList.toggle("is-override", isOverride);
      target.classList.toggle(
        "is-blocked",
        blockedReason !== undefined || disabled || noRepairRoute
      );
      if (disabled) {
        target.textContent =
          "Source: none — this bond is not supported on the current pricing path, so " +
          "Shiori fills nothing here and editing this field cannot make the ticket price.";
        return;
      }
      if (noRepairRoute) {
        target.textContent =
          "Source: none — this depends on a Bloomberg Bond Master date this security's " +
          "response did not return, and no Advanced control anywhere on this page " +
          "supplies that date. Run Bloomberg Load again for this security; typing a " +
          "value here cannot make this ticket price.";
        return;
      }
      if (blockedReason !== undefined) {
        target.textContent =
          `Source: ${PROVENANCE_BLOCKED} — ${blockedReason}. ` +
          provenanceDescription(PROVENANCE_BLOCKED);
        return;
      }
      if (tier === null) {
        target.textContent = currentDraft
          ? "Source: not set — Shiori has filled nothing here, so this value is yours to enter."
          : "—";
        return;
      }
      target.textContent = `Source: ${tier} — ${provenanceDescription(tier)}`;
    });
  }

  function renderAdvancedProfileStatus() {
    const target = els.advancedProfileStatus;
    target.classList.remove("is-supported", "is-unsupported");
    if (!currentDraft) {
      target.textContent = "—";
      return;
    }
    if (conventionProfileTransportError !== null) {
      target.textContent =
        "Shiori could not reach its own convention-profile registry, so it has offered " +
        "no profile to select and pre-filled nothing here: " +
        conventionProfileTransportError;
      target.classList.add("is-unsupported");
      return;
    }
    // No registered profile covers this bond at all. That is a refusal, not
    // a question: there is no profile to pick, so this reads like any other
    // unsupported product rather than inviting a selection that cannot help.
    if (noProfileCoversThisBond()) {
      target.textContent =
        "No convention profile Shiori has covers this bond, so it has pre-filled none of " +
        "these fields and has disabled them. No Advanced edit and no profile selection " +
        "can make this ticket price (this says nothing about who issued the bond — only " +
        "that its own confirmed terms are outside what Shiori's profiles state " +
        "conventions for): " + productRejectionReasons().join(" · ");
      target.classList.add("is-unsupported");
      return;
    }
    // No profile selected, but one or more genuinely fit: the one thing to
    // ask for is a profile. Issue #161 is explicit that this must never fall
    // back to asking the trader to hand-type the eight technical fields.
    if (!selectedConventionProfile) {
      const reasons =
        (conventionProfileCandidates && conventionProfileCandidates.reasons) || [];
      target.textContent =
        "No convention profile is selected for this bond, so Shiori has pre-filled nothing " +
        "here. Shiori will not choose one for you — it has no confirmed Bloomberg " +
        "classification to choose from. Select the Convention Profile that applies and it " +
        "will fill every Advanced field it can" +
        (reasons.length > 0 ? ": " + reasons.join(" · ") : ".");
      target.classList.add("is-unsupported");
      return;
    }
    if (advancedProfileTransportError !== null) {
      target.textContent =
        `Shiori could not reach its own ${selectedConventionProfile} profile resolver, ` +
        "so nothing here has been pre-filled: " + advancedProfileTransportError;
      target.classList.add("is-unsupported");
      return;
    }
    if (advancedProfile === null) {
      target.textContent =
        `Checking this bond's shape against the selected ${selectedConventionProfile} ` +
        "convention profile…";
      return;
    }
    // Echo the server's own confirmation of which profile it actually
    // resolved against -- never assume it matches what was sent.
    const profileName = advancedProfile.convention_profile || selectedConventionProfile;
    if (!advancedProfile.supported) {
      target.textContent =
        "This bond's product or coupon schedule is not supported on the current pricing " +
        `path, and does not fit the selected ${profileName} convention profile, so Shiori ` +
        "pre-fills none of these fields and has disabled them. No Advanced edit can make " +
        "this ticket price (this says nothing about who issued the bond — only that its " +
        "own terms are outside what this path carries): " +
        productRejectionReasons().join(" · ");
      target.classList.add("is-unsupported");
      return;
    }
    const pending = advancedProfile.pending_field_paths || [];
    const unresolved = advancedProfile.unresolved_fields || [];
    let text =
      `Convention Profile: ${profileName} (you selected it; Shiori does not choose a ` +
      "profile). This bond's shape fits it, so Shiori has filled every field it could and " +
      "shown where each value came from.";
    if (pending.length > 0) {
      text += ` Waiting on the expiry before deriving: ${pending.join(", ")}.`;
    }
    if (unresolved.length > 0) {
      text +=
        " These are yours to set — every other field keeps the value Shiori resolved: " +
        unresolved.map((item) => `${item.path} (${item.reason})`).join(" · ");
    }
    target.textContent = text;
    target.classList.add("is-supported");
  }

  // One request per genuine change of the inputs the answer depends on -- not
  // one per keystroke. The server route is a pure function of its body, so
  // re-asking with an unchanged key could only return the same answer.
  async function refreshAdvancedProfile() {
    const key = advancedProfileKey();
    if (key === null || key === lastAdvancedProfileKey) return;
    lastAdvancedProfileKey = key;
    const generation = ++advancedProfileGeneration;
    const requestBody = {
      convention_profile: selectedConventionProfile,
      isin: resolvedBloombergBond.isin,
      currency: currentDraft.bond_option.currency,
      bond_master: resolvedBloombergBond.bond_master || {},
      bond_master_raw: resolvedBloombergBond.bond_master_raw || {},
      valuation_date: currentDraft.valuation_date,
      expiry_date: currentDraft.bond_option.expiry_date,
    };

    let response;
    let payload;
    try {
      response = await fetch("/api/bond/advanced-profile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      });
      payload = await response.json();
    } catch (err) {
      if (generation !== advancedProfileGeneration) return;
      // Shiori does not know whether a profile applies, which is not the same
      // as knowing it does not. Nothing is filled, the state says so, and the
      // key is released so the next edit retries. The expiry-dependent
      // settlement dates were already withdrawn synchronously in
      // applyManualInputsToDraft when this key changed -- this failure must
      // not restore them or leave a stale one behind (Issue #157 P1-2).
      lastAdvancedProfileKey = null;
      advancedProfile = null;
      advancedProfileTransportError = err.message;
      renderAdvancedProfileStatus();
      renderFieldProvenance();
      syncDraftGating();
      return;
    }
    // Two independent staleness checks. The generation catches a reset or a
    // newer request; the key catches a draft that has moved on to another bond
    // or another expiry while this answer was outstanding, which is exactly
    // the case where applying it would fill a field from data the trader has
    // left behind.
    if (generation !== advancedProfileGeneration) return;
    if (advancedProfileKey() !== key) return;
    if (!response.ok) {
      // Same guarantee as the transport-failure branch above: the withdrawn
      // settlement dates stay withdrawn, never restored from a failed
      // request (Issue #157 P1-2).
      lastAdvancedProfileKey = null;
      advancedProfile = null;
      advancedProfileTransportError = (payload && payload.error) || "profile request failed";
      renderAdvancedProfileStatus();
      renderFieldProvenance();
      syncDraftGating();
      return;
    }
    advancedProfileTransportError = null;
    advancedProfile = payload;
    applyAdvancedProfileFields(payload);
    renderAdvancedProfileStatus();
    renderFieldProvenance();
    syncDraftGating();
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
      reason:
        "No confirmed Bloomberg mnemonic maps to a typed day count; DAY_CNT_DES stays " +
        "display-only evidence.",
      read: (draft) => referenceRecord(draft).day_count,
    },
    {
      path: "bond_reference_data_universe.0.bond_type",
      label: "Bond Type",
      reason:
        "No confirmed Bloomberg mnemonic maps to a typed bond type; MTY_TYP stays " +
        "display-only evidence.",
      read: (draft) => referenceRecord(draft).bond_type,
    },
    {
      path: "bond_reference_data_universe.0.ex_dividend_days",
      label: "Ex-Dividend Days",
      reason: "Ex-dividend candidates returned BAD_FLD, so Bloomberg supplies no value here.",
      read: (draft) => referenceRecord(draft).ex_dividend_days,
    },
    {
      path: "bond_reference_data_universe.0.last_coupon_date",
      label: "Last Coupon Date",
      reason:
        "PREV_CPN_DT's role against this field is unconfirmed, so Bloomberg supplies no " +
        "value here.",
      read: (draft) => referenceRecord(draft).last_coupon_date,
    },
    {
      path: "bond_reference_data_universe.0.status",
      label: "Bond Status",
      reason:
        "Security-status candidates returned BAD_FLD, so Bloomberg supplies no value here.",
      read: (draft) => referenceRecord(draft).status,
    },
    {
      path: "reporting_date",
      label: "Reporting Date",
      reason: "No Bloomberg field carries this run's own reporting date.",
      read: (draft) => draft.reporting_date,
    },
    {
      path: "forward_settlement_date",
      label: "Forward Settlement Date",
      reason:
        "No Bloomberg field carries an OTC option's forward settlement date; SETTLE_DT " +
        "describes the cash bond's own settlement, a different role.",
      read: (draft) => draft.forward_settlement_date,
    },
    {
      path: "option_settlement_date",
      label: "Option Settlement Date",
      reason: "No Bloomberg field carries an OTC option's own cash settlement date.",
      read: (draft) => draft.option_settlement_date,
    },
  ];

  // Every recorded value states which of the four Issue #157 tiers it came
  // from -- not a blanket "trader override" claim. A profile-filled or
  // Shiori-derived field says so, and only a value the trader actually took
  // over is stamped TRADER_OVERRIDE against MANUAL_TRADER_ENTRY. Fields with
  // no profile tier at all (the forward, the vol and the curve) are trader
  // entries by construction and keep their existing stamp.
  function refreshOverrideProvenance() {
    overrideProvenance = new Map();
    if (!currentDraft) return;
    const anchor = currentDraft.pricing_timestamp || null;
    OVERRIDE_FIELDS.forEach((field) => {
      const value = field.read(currentDraft);
      if (!present(value)) return;
      const basis = PROFILE_FIELD_BY_PATH.has(field.path)
        ? fieldProvenance.get(field.path) || TRADER_OVERRIDE_BASIS
        : TRADER_OVERRIDE_BASIS;
      overrideProvenance.set(field.path, {
        field: field.label,
        path: field.path,
        value: String(value),
        source_system:
          basis === TRADER_OVERRIDE_BASIS
            ? MANUAL_SOURCE_SYSTEM
            : conventionProfileSourceSystem(),
        basis: basis,
        reason_not_sourced:
          basis === TRADER_OVERRIDE_BASIS
            ? field.reason
            : `${field.reason} ${provenanceDescription(basis)}`.trim(),
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
    // The one other pre-filled control (Issue #161). Set here, in the single
    // function that starts a fresh ticket, so "new ticket" and "offset back
    // to +08:00" are the same event by construction -- there is nowhere else
    // for a re-render or a validation pass to reach it from.
    els.expiryOffset.value = DEFAULT_EXPIRY_UTC_OFFSET;
    // Two blank rows: the minimum the curve contract needs, offered ready to
    // fill. They are structure only -- a blank row is never a node.
    clearCurveRows();
    addCurveRow("", "");
    addCurveRow("", "");
    inputNormalization = {
      strike: inputBlank(),
      notional: inputBlank(),
      forward: inputBlank(),
    };
    renderInputNormalization();
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

  // Writes a canonical number back into a trader-format control -- but leaves
  // the trader's own text alone when it already normalizes to exactly that
  // number (Issue #161). Without this, pressing Price would rewrite a strike
  // typed as "98-16+" into "98.515625": the same value, but not the trader's
  // own quote, and a control that visibly rewrites itself after every run.
  function setNormalizedInputValue(input, parsed, value) {
    if (parsed.error === null && parsed.value === value) return;
    input.value = value ?? "";
  }

  function setTradeFormFromDraft(draft) {
    setToggle(els.optionTypeToggle, draft.bond_option.option_type);
    setToggle(els.positionToggle, draft.bond_option.position);
    setNormalizedInputValue(
      els.strikePrice, inputNormalization.strike, draft.bond_option.strike_price
    );
    setNormalizedInputValue(els.notional, inputNormalization.notional, draft.bond_option.notional);
    els.volatility.value = draft.volatility_input.volatility ?? "";
    setNormalizedInputValue(
      els.forwardPrice,
      inputNormalization.forward,
      draft.forward_clean_price_input.forward_clean_price_per_100
    );
    // Re-read whatever the controls now hold, so the previews describe what is
    // on screen rather than what was on screen before this write.
    inputNormalization = readTraderFormatInputs();
    renderInputNormalization();
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

    // Issue #161: the three trader-format numeric fields are normalized once,
    // here, and only their canonical numbers reach the draft. A malformed
    // entry yields null *and* a visible error beside the field -- the same
    // structural state as blank, so the existing gate stays closed, but never
    // silently so.
    inputNormalization = readTraderFormatInputs();

    const bondOption = currentDraft.bond_option;
    bondOption.option_type = getOptionalToggleValue(els.optionTypeToggle);
    bondOption.position = getOptionalToggleValue(els.positionToggle);
    bondOption.strike_price = inputNormalization.strike.value;
    bondOption.notional = inputNormalization.notional.value;

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

    // Issue #157 P1-2 correction: the instant any input the UST profile
    // answer depends on changes (expiry above all, but also currency or
    // valuation date), the forward/option settlement dates that answer last
    // derived belong to the *previous* key and are no longer trustworthy --
    // withdraw them right here, synchronously, before they are ever read
    // back into the draft below. Without this, the stale dates would still
    // read as "present" for one render/validation cycle (the profile refresh
    // below is async), during which the typed-builder validation this
    // function schedules could see a structurally complete draft and enable
    // Price against a settlement date derived for an expiry that is no
    // longer on screen. A trader-overridden path is never touched here --
    // it survives untouched and is still validated by the real typed builder
    // like any other field.
    const profileKeyBeforeThisEdit = lastAdvancedProfileKey;
    if (advancedProfileKey() !== profileKeyBeforeThisEdit) {
      EXPIRY_DEPENDENT_PROFILE_FIELDS.forEach((field) => {
        if (traderOverriddenPaths.has(field.path)) return;
        field.control().value = "";
        fieldProvenance.delete(field.path);
      });
      // The last profile answer describes the key that is about to change --
      // it must not keep being shown (or trusted) as if it still applied to
      // the new one while the fresh request below is outstanding.
      advancedProfile = null;
      advancedProfileTransportError = null;
    }

    // The acquisition-derived values are deliberately not read back from the
    // DOM: their source is the recorded Bloomberg event, and editing any
    // unrelated trader control must never overwrite that provenance.
    currentDraft.reporting_date = textOrNull(els.reportingDate.value);
    currentDraft.forward_settlement_date = textOrNull(els.forwardSettlementDate.value);
    currentDraft.option_settlement_date = textOrNull(els.optionSettlementDate.value);

    currentDraft.volatility_input.volatility = numberOrNull(els.volatility.value);
    currentDraft.volatility_input.volatility_basis = selectValueOrNull(els.volatilityBasis);
    currentDraft.forward_clean_price_input.forward_clean_price_per_100 =
      inputNormalization.forward.value;

    currentDraft.curve_points = readCurveNodesFromRows();

    renderInputNormalization();
    renderExpiryPreview();
    renderTimestampUtcPreviews();
    refreshOverrideProvenance();
    renderOverrideProvenance();
    renderFieldProvenance();
    renderAdvancedProfileStatus();
    invalidateBuilderValidation();
    syncDraftGating();
    // Cheap unless the expiry (or the loaded bond) actually changed: the
    // profile request is keyed on its own inputs, so an unrelated edit --
    // strike, notional, a curve node -- asks nothing and re-derives nothing.
    refreshAdvancedProfile();
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

  // Issue #161: a bond the current pricing path cannot carry gets one honest
  // panel and no input route at all. Before this, the page kept showing
  // editable Advanced controls and a "Go to this input" button while
  // simultaneously saying those edits could never make the ticket price --
  // a fake exit. The rule now: no Go-to button, disabled profile controls
  // (see renderFieldProvenance), and a next step that is actually available.
  function renderProductUnsupportedDependency() {
    unresolvedFocusGroup = null;
    els.unresolvedPanel.hidden = false;
    els.unresolvedGotoBtn.hidden = true;
    els.unresolvedTitle.textContent =
      "This bond is not supported on the current pricing path";
    els.unresolvedMissing.textContent =
      "Nothing you can enter in Advanced — these are facts about the bond and its " +
      "schedule, not inputs Shiori is waiting for.";
    els.unresolvedWhy.textContent = productRejectionReasons().join(" · ");
    els.unresolvedEvidence.textContent =
      "The reviewed resolver refused this bond's own terms or its coupon schedule, " +
      "not one of its fields. The typed pricing adapter carries one regular " +
      "fixed-coupon grid, so an irregular or stubbed schedule cannot be repaired by " +
      "typing a last coupon date, and a callable, sinkable, matured or non-USD bond " +
      "is outside the selected profile entirely.";
    els.unresolvedNext.textContent =
      "Read the reasons above. If one of them is a Bond Master value Bloomberg simply " +
      "did not return, run Bloomberg Load again for this security; otherwise this bond " +
      "is outside what the current pricing path carries and a different bond is the " +
      "only way forward. The Advanced technical fields are disabled here precisely " +
      "because changing them could not make this ticket price — Shiori will not offer " +
      "an input that leads nowhere.";
    els.unresolvedAlso.textContent =
      "Everything else on this ticket is on hold until a supported bond is loaded.";
  }

  function renderUnresolvedDependency(groups) {
    if (productUnsupported()) {
      renderProductUnsupportedDependency();
      return;
    }
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
    // Every route offered from here is real, with one narrower exception: a
    // group whose own detail sets `goToDisabled` (Issue #161 P2 correction)
    // -- every field left in it traces to a Bloomberg Bond Master date with
    // no Advanced control anywhere, so Go-to would open Advanced onto a
    // control that cannot repair anything.
    els.unresolvedGotoBtn.hidden = Boolean(detail.goToDisabled);
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
    if (!currentDraft || !advancedProfile || advancedProfile.supported !== true) return;
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
        : sourcedQuoteSideMismatch()
          ? "Quote Side changed — refresh to re-source on that side"
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
    const profileReady =
      hasDraft &&
      resolvedBloombergBond !== null &&
      advancedProfileTransportError === null &&
      advancedProfile !== null &&
      advancedProfile.supported === true;
    if (
      draftComplete &&
      profileReady &&
      builderValidation.state === "unknown" &&
      validationTimer === null
    ) {
      scheduleBuilderValidation();
    }
    const builderReady = draftComplete && profileReady && builderValidation.state === "ready";
    // A pending lookup names a *different* bond than the one backing this
    // draft. Both actions begin by invalidating that lookup, so leaving them
    // live would let a click silently cancel the trader's Load and then price
    // or refresh the previous bond while the security input names the new one.
    // One invariant for both actions: neither is available while a run-state-
    // changing request is outstanding, nor while a lookup names another bond.
    const canRefresh =
      builderReady &&
      resolvedBloombergBond !== null &&
      !bondLookupInFlight &&
      inFlightRunRequest === null;
    // Price additionally requires that the sourced quote is live *and* on the
    // side currently selected -- a Price started after a side change cannot be
    // caught by request invalidation, so it has to be gated here.
    const canPrice =
      canRefresh && !sourcedQuoteInvalidated && !sourcedQuoteSideMismatch();
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
  // Whether a lookup is genuinely outstanding right now. The controller alone
  // cannot answer that -- it stays set after a lookup settles -- and the Quote
  // Side handler needs the difference: a side change during a pending lookup
  // has to void it and say so, while a side change with nothing in flight must
  // stay silent.
  let bondLookupInFlight = false;

  // --- The single run-request lifecycle ------------------------------------
  //
  // Owner decision: at most one run-state-changing request -- Price or
  // Bloomberg Refresh -- may be in flight at a time. `null` when idle,
  // otherwise "PRICE" or "REFRESH".
  //
  // This replaces an earlier supersession ordering, which let a refresh abort a
  // pending Price on the reasoning that a priced run still resulted, on fresher
  // data. That holds only when the refresh *succeeds*: when it fails, the
  // aborted Price is gone, no result exists, and the invalidated quote leaves
  // Price disabled -- the trader loses both outcomes and the way back is behind
  // the action that just failed. Neither action can cancel the other now, so
  // the question does not arise and no precedence rule is needed.
  //
  // A form edit, Clear and a new instrument lookup remain cancel-and-reset
  // boundaries: they release this state and *permanently* invalidate the
  // outstanding response through the generation counters, so a late answer can
  // never overwrite an edited draft.
  let inFlightRunRequest = null;

  function releaseRunRequest() {
    inFlightRunRequest = null;
  }

  function beginBondLookupRequest() {
    return ++bondLookupGeneration;
  }

  function isStaleBondLookupRequest(generation) {
    return generation !== bondLookupGeneration;
  }

  function invalidatePendingBondLookupRequest() {
    beginBondLookupRequest();
    bondLookupInFlight = false;
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
    if (inFlightRunRequest === "PRICE") releaseRunRequest();
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
    if (inFlightRunRequest === "REFRESH") releaseRunRequest();
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

    // The whole profile lifecycle goes with the draft. Bumping the generation
    // and releasing the key permanently voids any answer still outstanding, so
    // a late response for the previous bond can never fill the next one's
    // fields -- and no override, tier or rejection reason survives either.
    advancedProfileGeneration++;
    advancedProfile = null;
    fieldProvenance = new Map();
    traderOverriddenPaths = new Set();
    lastAdvancedProfileKey = null;
    advancedProfileTransportError = null;
    // The convention-profile selection belongs to the bond that was loaded,
    // not to the page: a Clear, or a Load of a different security, must not
    // leave the next bond silently carrying the previous one's profile --
    // including a profile the trader chose by hand. Bumping this generation
    // also voids any suggestion still outstanding.
    conventionProfileGeneration++;
    conventionProfileCandidates = null;
    conventionProfileOverridden = false;
    conventionProfileTransportError = null;
    selectedConventionProfile = null;
    renderConventionProfilePicker();

    renderResolvedBondPanel();
    clearBondMaster();
    hideContext();
    clearResultFields();
    clearTraderForm();
    renderRouteMetadata();
    renderOverrideProvenance();
    renderFieldProvenance();
    renderAdvancedProfileStatus();
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

  // Adopt-time sourcing-input check. A response describes the identifier and
  // side it was requested with; if either sourcing input has since changed,
  // acting on it -- adopting it *or* letting its failure reset the run -- would
  // speak for a bond or side the trader is no longer naming.
  //
  // Written as an invariant applied at every point a lookup answer is acted on,
  // rather than as another per-control listener: the controls that feed a
  // lookup can grow, and one rule covering "the inputs must still be what this
  // response was requested with" does not have to be extended each time one
  // does.
  function sourcingInputsStillMatch(requestedIdentifier, requestedSide) {
    const currentIdentifier = parseBondIdentifier(els.bondIdentifierInput.value);
    return (
      currentIdentifier !== null &&
      currentIdentifier.identifier === requestedIdentifier &&
      getOptionalToggleValue(els.bondQuoteSideToggle) === requestedSide
    );
  }

  function renderSupersededLookupInputs() {
    renderLookupInputError(
      "The lookup inputs changed while Bloomberg was still answering, so that " +
        "response was discarded. Press Load to source the bond named above."
    );
    // The lookup has settled, so whatever run is still on screen becomes
    // actionable again. The other settle paths re-render through their own
    // renderers; this one has to do it itself.
    syncDraftGating();
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
    bondLookupInFlight = true;
    // Both Price and Refresh are gated on this flag, so the buttons have to be
    // re-rendered as soon as it is raised rather than at the next form edit.
    syncDraftGating();
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
      // A superseded request was already marked settled by whichever action
      // invalidated it, so only the genuine failure clears the flag here.
      if (isStaleBondLookupRequest(generation)) return;
      bondLookupInFlight = false;
      // The guard belongs here too, not only on the parsed-response path. A
      // non-JSON error body or a rejected fetch lands in this catch *before*
      // any adoption check runs, and an identifier edit advances no generation,
      // so this superseded answer would otherwise reach `renderLookupFailure`
      // and reset a run that belongs to a bond the trader is still naming.
      if (!sourcingInputsStillMatch(parsed.identifier, quoteSide)) {
        renderSupersededLookupInputs();
        return;
      }
      renderLookupFailure("Bloomberg bond lookup failed: " + err.message);
      return;
    }
    if (isStaleBondLookupRequest(generation)) return;
    bondLookupInFlight = false;

    // Checked before the `response.ok` branch on purpose: `renderLookupFailure`
    // resets the whole run, so a failed answer for a superseded identifier
    // would otherwise discard a prior completed ticket on that bond's behalf.
    if (!sourcingInputsStillMatch(parsed.identifier, quoteSide)) {
      renderSupersededLookupInputs();
      return;
    }

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
    renderFieldProvenance();
    renderAdvancedProfileStatus();

    els.statusText.textContent = "Bloomberg bond loaded";
    renderResolvedBondPanel();
    renderBondMaster(resolvedBloombergBond);
    syncDraftGating();
    // A fresh bond, a fresh profile: resetRunState above already discarded the
    // previous one's tiers, overrides and profile selection, so nothing here
    // is inherited. This only populates the picker -- the trader's own
    // selection is what triggers field resolution.
    refreshConventionProfileCandidates();
  }

  // Prices the current draft through the existing reviewed builder -- reuses
  // POST /api/case unchanged rather than adding any new pricing route or logic:
  // a full case dict in, {case, overlay, context, display} out.
  async function priceCurrentDraft() {
    if (!currentDraft || !resolvedBloombergBond) return;
    if (sourcedQuoteInvalidated) return;
    // One run-state-changing request at a time, and never while a lookup names
    // another bond. The gating above already disables the button; this is the
    // same rule at the action, so it cannot be reached another way.
    if (inFlightRunRequest !== null) return;
    if (bondLookupInFlight) return;
    // Never price a draft whose quote was sourced on a side other than the one
    // on screen. The gating above already disables the button; this is the
    // same rule at the action itself, so it cannot be reached another way.
    if (sourcedQuoteSideMismatch()) return;
    if (unresolvedGroups(currentDraft).length > 0) return;
    if (builderValidation.state !== "ready") return;

    const generation = beginRequest();
    inFlightRunRequest = "PRICE";
    syncDraftGating();

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
      // A stale request was already released by whichever boundary invalidated
      // it, so only a live one releases here.
      if (isStaleRequest(generation)) return;
      releaseRunRequest();
      setCurrentDisplay(null);
      renderFailure("Pricing request failed: " + err.message);
      syncDraftGating();
      return;
    }
    if (isStaleRequest(generation)) return;
    releaseRunRequest();
    if (!response.ok) {
      setCurrentDisplay(null);
      renderFailure(payload.error || "Pricing request failed.");
      syncDraftGating();
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
    if (inFlightRunRequest !== null) return;
    if (bondLookupInFlight) return;
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
    inFlightRunRequest = "REFRESH";
    syncDraftGating();

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
      releaseRunRequest();
      renderRefreshFailure("Bloomberg refresh failed: " + err.message);
      return;
    }
    if (isStaleBloombergRequest(generation)) return;
    releaseRunRequest();
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
    renderFieldProvenance();
    renderAdvancedProfileStatus();
    // Only re-asks if the re-acquisition actually moved one of the profile's
    // own inputs; the trader's overrides survive this untouched either way.
    refreshAdvancedProfile();

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
    if (!opt) return;
    setToggle(els.bondQuoteSideToggle, opt.dataset.value);

    // Quote Side is a sourcing input, not a draft field, so nothing here writes
    // it into `currentDraft` -- Load and Refresh read the toggle when they
    // build their request, and the sourced side always comes back from the
    // response. That is not a reason to leave it outside invalidation, though:
    // invalidation is about a response describing a case the trader has moved
    // away from, and all three requests this toggle feeds can be in flight.
    //
    // A pending lookup has to be handled here rather than in
    // applyManualInputsToDraft(): there is no draft yet while the first lookup
    // is outstanding, so that function returns at its own guard, and it does
    // not invalidate lookups in any case. Voiding it silently would leave the
    // trader watching a Load that never resolves, and re-issuing it
    // automatically would be an implicit second Bloomberg call this workflow
    // does not make -- so it is voided and explained.
    if (bondLookupInFlight) {
      invalidatePendingBondLookupRequest();
      renderLookupInputError(
        "Quote Side changed while the Bloomberg lookup was still running, so that " +
          "lookup was discarded. Press Load to source this bond on the new side."
      );
    }

    // A Price or Refresh already in flight was sent on the previous side, and
    // adopting its response would leave the draft's bond_quote and
    // forward_clean_price_input sides disagreeing with the side the toggle now
    // shows, until some later refresh happened to correct it.
    applyManualInputsToDraft();
  });
  // Registered before the generic handlers below, so by the time the draft is
  // re-read the path is already marked as the trader's. Both event types are
  // listened for because these eight controls are a mix of selects, date
  // inputs and a text input.
  PROFILE_FIELDS.forEach((field) => {
    const control = field.control();
    const mark = () => markTraderOverride(field.path);
    control.addEventListener("input", mark);
    control.addEventListener("change", mark);
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
  // Always visible, whether or not Advanced is expanded, and whether or not a
  // bond is loaded yet. Its options come from the server's own candidate list
  // rather than a second, independently-typed copy of the profile registry,
  // and its value is the one piece of browser state this file sends as
  // convention_profile on every profile request.
  renderConventionProfilePicker();
  els.conventionProfileSelect.addEventListener("change", () => {
    const chosen = els.conventionProfileSelect.value || null;
    if (chosen === selectedConventionProfile) return;
    // Records that the selection is the trader's own. Nothing in this file
    // ever sets `selectedConventionProfile` outside this handler and the
    // per-bond reset, so a profile can only ever be a deliberate choice.
    conventionProfileOverridden = true;
    selectedConventionProfile = chosen;
    // The previous profile's answer describes a profile that is no longer
    // selected, so it must stop being shown or trusted while the fresh
    // request is outstanding -- the same rule the expiry change already
    // follows. Trader-overridden fields are deliberately left untouched.
    advancedProfileGeneration++;
    advancedProfile = null;
    advancedProfileTransportError = null;
    lastAdvancedProfileKey = null;
    applyAdvancedProfileFields({ fields: [] });
    renderConventionProfilePicker();
    renderFieldProvenance();
    renderAdvancedProfileStatus();
    syncDraftGating();
    refreshAdvancedProfile();
  });

  // Test-only, read-only accessors: they change no stored value, API response,
  // conversion, or pricing behavior.
  window.__shioriTestGetCurrentDraft = () => currentDraft;
  window.__shioriTestUnresolvedGroupIds = () =>
    unresolvedGroups(currentDraft).map((group) => group.id);
  window.__shioriTestOrdinaryWorkflowGroupIds = () => ORDINARY_WORKFLOW_GROUP_IDS.slice();
  window.__shioriTestOverrideProvenance = () => overrideProvenanceRecords();
  window.__shioriTestBuilderValidationState = () => builderValidation.state;
  window.__shioriTestSourcedQuoteInvalidated = () => sourcedQuoteInvalidated;
  window.__shioriTestFieldProvenance = () => Object.fromEntries(fieldProvenance);
  window.__shioriTestTraderOverriddenPaths = () => Array.from(traderOverriddenPaths);
  window.__shioriTestAdvancedProfile = () => advancedProfile;
  window.__shioriTestSelectedConventionProfile = () => selectedConventionProfile;
  window.__shioriTestConventionProfileCandidates = () => conventionProfileCandidates;
  window.__shioriTestConventionProfileOverridden = () => conventionProfileOverridden;
  // Read-only generation fence for browser tests (no production behavior
  // depends on this accessor existing). `conventionProfileGeneration` is
  // bumped exactly twice per successful Bloomberg Load -- once by
  // resetRunState, once by refreshConventionProfileCandidates -- and by
  // nothing else, so a test can wait for it to increase past a
  // pre-click value to know a fresh load's reset has genuinely happened,
  // rather than polling DOM text that can already equal the expected value
  // when the same identifier is loaded twice in a row (Issue #161 CI fix).
  window.__shioriTestConventionProfileGeneration = () => conventionProfileGeneration;
  // Issue #161. The two parsers are pure functions of their argument, so
  // exposing them tests the very code the page runs rather than a copy.
  window.__shioriTestParseTreasuryQuote = (raw) => parseTreasuryQuote(raw);
  window.__shioriTestParseNotional = (raw) => parseNotional(raw);
  window.__shioriTestInputNormalization = () => inputNormalization;
  window.__shioriTestBlockedFieldPaths = () => Array.from(blockedFieldReasons().keys());
  window.__shioriTestDefaultExpiryUtcOffset = () => DEFAULT_EXPIRY_UTC_OFFSET;
})();

// Markets view (Issue #167): read-only USD SOFR Option Discount Curve viewer.
//
// Deliberately a second, self-contained IIFE rather than woven into the
// Pricing IIFE above -- it shares no state with the trader-draft workflow and
// touches none of it. This file performs no pricing, discounting, bootstrap,
// interpolation, or par/zero conversion of any kind; it only calls
// POST /api/bloomberg/option-discount-curve (see
// standalone_option_workbench_server.fetch_usd_sofr_option_discount_curve)
// and renders the response verbatim -- one Zero Rate Curve chart, a summary
// strip, and the full node table. SWAP (Par Rate) is rendered exactly as the
// server sends it, including `null` (rendered as "—", never fabricated,
// never substituted with the zero rate).
(function () {
  "use strict";

  const navPricing = document.getElementById("nav-pricing");
  const navMarkets = document.getElementById("nav-markets");
  const viewPricing = document.getElementById("view-pricing");
  const viewMarkets = document.getElementById("view-markets");
  if (!navMarkets || !viewMarkets) return; // Markets view not present on this page

  const els = {
    footer: document.getElementById("app-footer"),
    refreshBtn: document.getElementById("markets-refresh-btn"),
    meta: document.getElementById("markets-curve-meta"),
    acquiredRow: document.getElementById("markets-acquired-at"),
    acquiredValue: document.getElementById("markets-acquired-at-value"),
    loading: document.getElementById("markets-loading"),
    errorSection: document.getElementById("markets-error"),
    errorDetail: document.getElementById("markets-error-detail"),
    staleBanner: document.getElementById("markets-stale-banner"),
    staleAcquired: document.getElementById("markets-stale-acquired-at"),
    staleDetail: document.getElementById("markets-stale-detail"),
    content: document.getElementById("markets-content"),
    chartWrap: document.getElementById("markets-chart-svg-wrap"),
    tableCard: document.getElementById("markets-table-card"),
    tableBody: document.getElementById("markets-table-body"),
    summaryNodes: document.getElementById("markets-summary-nodes"),
    summaryCoverage: document.getElementById("markets-summary-coverage"),
    summary1y: document.getElementById("markets-summary-1y"),
    summary10y: document.getElementById("markets-summary-10y"),
    summary30y: document.getElementById("markets-summary-30y"),
    summarySource: document.getElementById("markets-summary-source"),
  };

  // The chart's x-axis shows only these tenors' labels (Issue #167 §5): every
  // valid node is still plotted as a line/dot, this only thins the labels.
  const MAJOR_AXIS_TENORS = [
    "1W", "1M", "3M", "6M", "1Y", "2Y", "5Y", "10Y", "20Y", "30Y", "50Y",
  ];

  // Deliberately explicit, never a generic string-humanizer: these are the
  // only two enum values the server ever sends (BLOOMBERG_DAPI /
  // CONTINUOUS_ZERO_RATE), so the display label is a fixed, reviewed mapping,
  // not a guess about what an unrecognized value might mean. An unrecognized
  // value falls back to the raw string, verbatim, never invented.
  const SOURCE_SYSTEM_LABELS = { BLOOMBERG_DAPI: "Bloomberg DAPI" };
  const RATE_BASIS_LABELS = { CONTINUOUS_ZERO_RATE: "Continuous Zero Rate" };

  const NBSP_DASH = "—";

  let lastSuccessfulCurve = null;
  let isLoadingCurve = false;

  // Pricing-only bond/quote provenance context: the top-nav provenance pill
  // and quote-side badge, and the sidebar's Market Data live-source/as-of
  // rows. All four live outside #view-pricing (top nav / sidebar are shared
  // shell chrome), so hiding #view-pricing alone leaves them showing a
  // previously-loaded bond's provenance/quote-side/as-of timestamp next to
  // the curve -- unrelated evidence presented as if it were curve context
  // (Codex review, PR #170). Each element's own `hidden` state is saved
  // before being force-hidden and restored on return to Pricing, rather than
  // guessed or re-derived here -- this file has no visibility into why the
  // Pricing IIFE set a given state, only that it should come back unchanged.
  const pricingContextEls = [
    document.getElementById("provenance-pill"),
    document.getElementById("quote-side-badge"),
    document.getElementById("sidebar-live-row"),
    document.getElementById("sidebar-asof-row"),
  ].filter(Boolean);
  let savedPricingContextHidden = null;

  function switchToView(view) {
    const showMarkets = view === "markets";
    viewMarkets.hidden = !showMarkets;
    viewPricing.hidden = showMarkets;
    if (els.footer) els.footer.hidden = showMarkets;
    navMarkets.classList.toggle("active", showMarkets);
    navPricing.classList.toggle("active", !showMarkets);

    if (showMarkets) {
      savedPricingContextHidden = pricingContextEls.map((el) => el.hidden);
      pricingContextEls.forEach((el) => {
        el.hidden = true;
      });
    } else if (savedPricingContextHidden) {
      pricingContextEls.forEach((el, index) => {
        el.hidden = savedPricingContextHidden[index];
      });
      savedPricingContextHidden = null;
    }

    if (showMarkets && !lastSuccessfulCurve && !isLoadingCurve) {
      loadCurve();
    }
  }

  navMarkets.addEventListener("click", () => switchToView("markets"));
  navPricing.addEventListener("click", () => switchToView("pricing"));
  els.refreshBtn.addEventListener("click", () => loadCurve());

  function formatPercent(value, digits) {
    if (typeof value !== "number" || !Number.isFinite(value)) return NBSP_DASH;
    return `${value.toFixed(digits)}%`;
  }

  function formatNumber(value, digits) {
    if (typeof value !== "number" || !Number.isFinite(value)) return NBSP_DASH;
    return value.toFixed(digits);
  }

  function nodeByTenor(nodes, tenor) {
    return nodes.find((node) => node.tenor === tenor) || null;
  }

  // Fails closed on anything the server contract does not promise (Issue
  // #167 requirement 8: malformed response must be a clear error state, not
  // a half-rendered page). Every field this function checks is one the
  // Markets view actually reads below.
  function validateCurvePayload(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("malformed response: expected a JSON object");
    }
    for (const key of [
      "curve_id", "curve_name", "source_system", "rate_basis", "acquired_at", "coverage", "nodes",
    ]) {
      if (!(key in payload)) throw new Error(`malformed response: missing "${key}"`);
    }
    if (!Array.isArray(payload.nodes) || payload.nodes.length === 0) {
      throw new Error("malformed response: \"nodes\" must be a non-empty array");
    }
    payload.nodes.forEach((node, index) => {
      const label = node && typeof node === "object" ? node.tenor : index;
      if (!node || typeof node !== "object") {
        throw new Error(`malformed response: node ${index} is not an object`);
      }
      if (typeof node.tenor !== "string" || !node.tenor) {
        throw new Error(`malformed response: node ${index} has an invalid tenor`);
      }
      if (typeof node.maturity !== "string" || !node.maturity) {
        throw new Error(`malformed response: node ${label} is missing its maturity`);
      }
      if (typeof node.zero_rate_percent !== "number" || !Number.isFinite(node.zero_rate_percent)) {
        throw new Error(`malformed response: node ${label} has a non-numeric zero rate`);
      }
      if (typeof node.discount_factor !== "number" || !Number.isFinite(node.discount_factor)) {
        throw new Error(`malformed response: node ${label} has a non-numeric discount factor`);
      }
      if (node.par_rate_percent !== null && typeof node.par_rate_percent !== "number") {
        throw new Error(`malformed response: node ${label} has an invalid par rate`);
      }
    });
    return payload;
  }

  async function loadCurve() {
    if (isLoadingCurve) return;
    isLoadingCurve = true;
    els.refreshBtn.classList.add("is-disabled");
    els.errorSection.hidden = true;
    els.staleBanner.hidden = true;
    if (!lastSuccessfulCurve) {
      els.content.hidden = true;
      els.tableCard.hidden = true;
      els.loading.hidden = false;
    }

    try {
      const response = await fetch("/api/bloomberg/option-discount-curve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      let payload;
      try {
        payload = await response.json();
      } catch (parseError) {
        throw new Error("malformed response: not valid JSON");
      }
      if (!response.ok) {
        throw new Error((payload && payload.error) || `server returned HTTP ${response.status}`);
      }
      validateCurvePayload(payload);
      lastSuccessfulCurve = payload;
      renderCurve(payload);
      els.loading.hidden = true;
      els.errorSection.hidden = true;
      els.staleBanner.hidden = true;
      els.content.hidden = false;
      els.tableCard.hidden = false;
    } catch (error) {
      els.loading.hidden = true;
      if (lastSuccessfulCurve) {
        // A prior successful load stays on screen -- but a failed refresh
        // must never look like a successful one (Issue #167 requirement 8):
        // the acquired timestamp stays the old one and the failure is named.
        els.staleBanner.hidden = false;
        els.staleAcquired.textContent = lastSuccessfulCurve.acquired_at;
        els.staleDetail.textContent = error.message;
      } else {
        els.errorSection.hidden = false;
        els.errorDetail.textContent = error.message;
        els.content.hidden = true;
        els.tableCard.hidden = true;
      }
    } finally {
      isLoadingCurve = false;
      els.refreshBtn.classList.remove("is-disabled");
    }
  }

  function renderCurve(payload) {
    const curveNumberMatch = /\(([^)]+)\)\s*$/.exec(payload.curve_name || "");
    const curveNumberLabel = curveNumberMatch ? curveNumberMatch[1] : payload.curve_id;
    els.meta.textContent = [
      curveNumberLabel,
      SOURCE_SYSTEM_LABELS[payload.source_system] || payload.source_system,
      RATE_BASIS_LABELS[payload.rate_basis] || payload.rate_basis,
    ].join(" · ");

    els.acquiredRow.hidden = false;
    els.acquiredValue.textContent = payload.acquired_at;

    renderSummaryStrip(payload);
    renderChart(payload.nodes);
    renderTable(payload.nodes);
  }

  function renderSummaryStrip(payload) {
    const nodes = payload.nodes;
    els.summaryNodes.textContent = String(nodes.length);
    els.summaryCoverage.textContent =
      payload.coverage && payload.coverage.first_tenor && payload.coverage.last_tenor
        ? `${payload.coverage.first_tenor} – ${payload.coverage.last_tenor}`
        : NBSP_DASH;

    const oneYear = nodeByTenor(nodes, "1Y");
    const tenYear = nodeByTenor(nodes, "10Y");
    const thirtyYear = nodeByTenor(nodes, "30Y");
    els.summary1y.textContent = oneYear ? formatPercent(oneYear.zero_rate_percent, 4) : NBSP_DASH;
    els.summary10y.textContent = tenYear ? formatPercent(tenYear.zero_rate_percent, 4) : NBSP_DASH;
    els.summary30y.textContent = thirtyYear ? formatPercent(thirtyYear.zero_rate_percent, 4) : NBSP_DASH;

    const curveNumberMatch = /\(([^)]+)\)\s*$/.exec(payload.curve_name || "");
    els.summarySource.textContent = curveNumberMatch
      ? curveNumberMatch[1].replace("Curve ", "")
      : payload.source_system;
  }

  function renderTable(nodes) {
    els.tableBody.textContent = "";
    for (const node of nodes) {
      const row = document.createElement("tr");

      const tenorCell = document.createElement("td");
      tenorCell.textContent = node.tenor;
      row.appendChild(tenorCell);

      const maturityCell = document.createElement("td");
      maturityCell.textContent = node.maturity;
      row.appendChild(maturityCell);

      const parCell = document.createElement("td");
      parCell.className = "num";
      if (node.par_rate_percent === null) {
        parCell.textContent = NBSP_DASH;
        parCell.classList.add("unavailable");
      } else {
        parCell.textContent = formatPercent(node.par_rate_percent, 4);
      }
      row.appendChild(parCell);

      const zeroCell = document.createElement("td");
      zeroCell.className = "num";
      zeroCell.textContent = formatPercent(node.zero_rate_percent, 4);
      row.appendChild(zeroCell);

      const dfCell = document.createElement("td");
      dfCell.className = "num";
      dfCell.textContent = formatNumber(node.discount_factor, 6);
      row.appendChild(dfCell);

      els.tableBody.appendChild(row);
    }
  }

  // ---- Chart: one Zero Rate Curve line, inline SVG, no charting library ----

  const SVG_NS = "http://www.w3.org/2000/svg";
  const CHART_WIDTH = 880;
  const CHART_HEIGHT = 300;
  const CHART_MARGIN = { top: 12, right: 16, bottom: 30, left: 46 };

  function computeNiceYAxis(values) {
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min;
    const pad = span === 0 ? Math.max(0.25, Math.abs(min) * 0.1) : span * 0.15;
    const roughLo = min - pad;
    const roughHi = max + pad;
    const tickCount = 5;
    const rawStep = (roughHi - roughLo) / tickCount || 1;
    const magnitude = Math.pow(10, Math.floor(Math.log10(Math.abs(rawStep))));
    const residual = rawStep / magnitude;
    let niceResidual;
    if (residual > 5) niceResidual = 10;
    else if (residual > 2) niceResidual = 5;
    else if (residual > 1) niceResidual = 2;
    else niceResidual = 1;
    const step = niceResidual * magnitude;
    const lo = Math.floor(roughLo / step) * step;
    const hi = Math.ceil(roughHi / step) * step;
    const ticks = [];
    for (let value = lo; value <= hi + step / 2; value += step) {
      ticks.push(Math.round(value * 1e6) / 1e6);
    }
    return { lo, hi, ticks };
  }

  function renderChart(nodes) {
    els.chartWrap.textContent = "";

    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`);
    svg.setAttribute("width", String(CHART_WIDTH));
    svg.setAttribute("height", String(CHART_HEIGHT));
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Zero Rate Curve");

    const plotWidth = CHART_WIDTH - CHART_MARGIN.left - CHART_MARGIN.right;
    const plotHeight = CHART_HEIGHT - CHART_MARGIN.top - CHART_MARGIN.bottom;
    const yAxis = computeNiceYAxis(nodes.map((node) => node.zero_rate_percent));

    const xFor = (index) =>
      CHART_MARGIN.left + (nodes.length === 1 ? plotWidth / 2 : (index / (nodes.length - 1)) * plotWidth);
    const yFor = (value) =>
      CHART_MARGIN.top + plotHeight - ((value - yAxis.lo) / (yAxis.hi - yAxis.lo)) * plotHeight;

    // Horizontal gridlines + y-axis tick labels.
    for (const tick of yAxis.ticks) {
      const y = yFor(tick);
      const gridline = document.createElementNS(SVG_NS, "line");
      gridline.setAttribute("x1", String(CHART_MARGIN.left));
      gridline.setAttribute("x2", String(CHART_WIDTH - CHART_MARGIN.right));
      gridline.setAttribute("y1", String(y));
      gridline.setAttribute("y2", String(y));
      gridline.setAttribute("stroke", "#eef0f4");
      gridline.setAttribute("stroke-dasharray", "3 3");
      svg.appendChild(gridline);

      const label = document.createElementNS(SVG_NS, "text");
      label.setAttribute("x", String(CHART_MARGIN.left - 8));
      label.setAttribute("y", String(y + 4));
      label.setAttribute("text-anchor", "end");
      label.setAttribute("font-size", "11");
      label.setAttribute("fill", "#99a1b0");
      label.textContent = tick.toFixed(2);
      svg.appendChild(label);
    }

    // X-axis tick labels: only the major tenors (Issue #167 §5), never all
    // 32, even though every node still gets a plotted dot.
    nodes.forEach((node, index) => {
      if (!MAJOR_AXIS_TENORS.includes(node.tenor)) return;
      const label = document.createElementNS(SVG_NS, "text");
      label.setAttribute("x", String(xFor(index)));
      label.setAttribute("y", String(CHART_HEIGHT - CHART_MARGIN.bottom + 18));
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("font-size", "11");
      label.setAttribute("fill", "#99a1b0");
      label.textContent = node.tenor;
      svg.appendChild(label);
    });

    // The single Zero Rate Curve line (Issue #167 §5: exactly one line).
    const points = nodes.map((node, index) => `${xFor(index)},${yFor(node.zero_rate_percent)}`).join(" ");
    const polyline = document.createElementNS(SVG_NS, "polyline");
    polyline.setAttribute("points", points);
    polyline.setAttribute("fill", "none");
    polyline.setAttribute("stroke", "var(--chart-line)");
    polyline.setAttribute("stroke-width", "2");
    svg.appendChild(polyline);

    // Node dots, each focusable/hoverable for the tooltip (Issue #167 §5:
    // hover/focus must show Tenor / Maturity / Zero Rate / Discount Factor).
    const tooltip = document.createElement("div");
    tooltip.className = "markets-chart-tooltip";
    tooltip.hidden = true;

    nodes.forEach((node, index) => {
      const cx = xFor(index);
      const cy = yFor(node.zero_rate_percent);

      const group = document.createElementNS(SVG_NS, "g");
      group.setAttribute("class", "markets-chart-node");
      group.setAttribute("tabindex", "0");
      group.setAttribute(
        "aria-label",
        `Tenor ${node.tenor}, maturity ${node.maturity}, zero rate ${formatPercent(node.zero_rate_percent, 4)}, discount factor ${formatNumber(node.discount_factor, 6)}`
      );

      const hitArea = document.createElementNS(SVG_NS, "circle");
      hitArea.setAttribute("cx", String(cx));
      hitArea.setAttribute("cy", String(cy));
      hitArea.setAttribute("r", "9");
      hitArea.setAttribute("fill", "transparent");
      group.appendChild(hitArea);

      const dot = document.createElementNS(SVG_NS, "circle");
      dot.setAttribute("cx", String(cx));
      dot.setAttribute("cy", String(cy));
      dot.setAttribute("r", "3.5");
      group.appendChild(dot);

      const showTooltip = () => {
        group.classList.add("is-focused");
        tooltip.hidden = false;
        tooltip.innerHTML = "";
        [
          ["Tenor:", node.tenor],
          ["Maturity:", node.maturity],
          ["Zero Rate:", formatPercent(node.zero_rate_percent, 4)],
          ["Discount Factor:", formatNumber(node.discount_factor, 6)],
        ].forEach(([label, value]) => {
          const row = document.createElement("div");
          row.className = "tt-row";
          const k = document.createElement("span");
          k.className = "tt-k";
          k.textContent = label;
          const v = document.createElement("span");
          v.textContent = value;
          row.appendChild(k);
          row.appendChild(v);
          tooltip.appendChild(row);
        });
        const leftPercent = (cx / CHART_WIDTH) * 100;
        const topPercent = (cy / CHART_HEIGHT) * 100;
        tooltip.style.left = `${leftPercent}%`;
        tooltip.style.top = `${topPercent}%`;
        tooltip.style.transform =
          leftPercent > 70 ? "translate(-100%, -115%)" : "translate(-10%, -115%)";
      };
      const hideTooltip = () => {
        group.classList.remove("is-focused");
        tooltip.hidden = true;
      };

      group.addEventListener("mouseenter", showTooltip);
      group.addEventListener("mouseleave", hideTooltip);
      group.addEventListener("focus", showTooltip);
      group.addEventListener("blur", hideTooltip);

      svg.appendChild(group);
    });

    els.chartWrap.appendChild(svg);
    els.chartWrap.appendChild(tooltip);
  }

  // Test-only, read-only accessors -- change no stored value, API response,
  // or rendering decision (mirrors the Pricing IIFE's own __shioriTest*
  // convention above).
  window.__shioriTestMarketsSwitchToView = (view) => switchToView(view);
  window.__shioriTestMarketsCurrentCurve = () => lastSuccessfulCurve;
  window.__shioriTestMarketsLoadCurve = () => loadCurve();
})();
