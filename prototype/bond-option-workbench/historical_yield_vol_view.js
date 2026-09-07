// Markets -> Bond Yield History -> Historical Yield Vol (Issue #197): the
// Middle Office-style historical volatility of one bond's own Yield.
//
// What this file does: sends the query already entered in the Bond Yield
// History form -- identifier, Bloomberg Yield field, date range -- plus an
// explicit observation count to POST /api/bloomberg/historical-yield-vol, and
// prints the answer.
//
// What this file never does, and must never start doing:
//
//   * compute a volatility statistic. There is no subtraction, no standard
//     deviation, no annualization and no rounding anywhere below. The whole
//     calculation lives in the server's one canonical calculator
//     (data/historical_yield_volatility.py), and the two figures are printed
//     from the payload's own *_text strings -- Python's own repr of the two
//     floats -- so what a trader reads is digit-for-digit what was computed;
//   * guess a Yield field, a unit, a window length, or an expiry -> lookback
//     mapping. The count box starts at Middle Office's confirmed 180 and is
//     sent verbatim; the unit is typed from workstation evidence and is never
//     inferred, and the normalization it drives happens on the server;
//   * rescale anything. The headline figures are in the Yield field's own
//     unit and the normalized volatility source is in DECIMAL_ANNUAL; both
//     arrive already computed, each carrying its own unit label, and this
//     page never converts one into the other;
//   * substitute anything. A blocked window (no history, too little history)
//     is displayed as blocked. No benchmark, index, VCUB, VOLATILITY_90D or
//     flat synthetic vol is ever reached for, and a short window is never
//     dressed up as a full one;
//   * price, capture, confirm, store, or mutate anything. The one route it
//     calls reads Bloomberg and writes nothing, and the result it shows is a
//     Yield Vol that no pricing path in this build accepts;
//   * load anything over the network beyond that one same-origin route.
(function () {
  "use strict";

  const panel = document.getElementById("markets-panel-yield-history");
  if (!panel) return;

  const els = {
    // Shared with the Issue #196 query form directly above this card: one
    // bond, one field and one range per view, entered once.
    identifier: document.getElementById("byh-identifier"),
    yieldField: document.getElementById("byh-yield-field"),
    start: document.getElementById("byh-start"),
    end: document.getElementById("byh-end"),

    observationCount: document.getElementById("hyv-observation-count"),
    fieldUnit: document.getElementById("hyv-field-unit"),
    calculateBtn: document.getElementById("hyv-calculate-btn"),

    loading: document.getElementById("hyv-loading"),
    idle: document.getElementById("hyv-idle"),
    error: document.getElementById("hyv-error"),
    errorDetail: document.getElementById("hyv-error-detail"),
    result: document.getElementById("hyv-result"),

    annualized: document.getElementById("hyv-annualized"),
    annualizedUnit: document.getElementById("hyv-annualized-unit"),
    daily: document.getElementById("hyv-daily"),
    dailyUnit: document.getElementById("hyv-daily-unit"),
    status: document.getElementById("hyv-status"),

    blockers: document.getElementById("hyv-blockers"),
    blockerList: document.getElementById("hyv-blocker-list"),
    warnings: document.getElementById("hyv-warnings"),
    warningsTitle: document.getElementById("hyv-warnings-title"),
    warningList: document.getElementById("hyv-warning-list"),

    methodologyValue: document.getElementById("hyv-methodology-value"),
    security: document.getElementById("hyv-security"),
    field: document.getElementById("hyv-field"),
    unit: document.getElementById("hyv-unit"),
    source: document.getElementById("hyv-source"),
    requestedRange: document.getElementById("hyv-requested-range"),
    requestedCount: document.getElementById("hyv-requested-count"),
    actualCount: document.getElementById("hyv-actual-count"),
    changeCount: document.getElementById("hyv-change-count"),
    firstObservation: document.getElementById("hyv-first-observation"),
    lastObservation: document.getElementById("hyv-last-observation"),
    stdevConvention: document.getElementById("hyv-stdev-convention"),
    annualization: document.getElementById("hyv-annualization"),
    acquiredAt: document.getElementById("hyv-acquired-at"),
    calculatedAt: document.getElementById("hyv-calculated-at"),

    sourceBlock: document.getElementById("hyv-source-block"),
    sourceDetail: document.getElementById("hyv-source-detail"),
  };
  for (const key of Object.keys(els)) {
    if (!els[key]) return;
  }

  const ROUTE = "/api/bloomberg/historical-yield-vol";
  const EM_DASH = "—";
  const UNIT_UNCONFIRMED = "Unit not confirmed by this request";

  let payload = null;
  let inFlight = false;
  const requestedRoutes = [];

  function text(value) {
    return value === null || value === undefined || value === "" ? EM_DASH : String(value);
  }

  async function postJson(route, body) {
    requestedRoutes.push(route);
    const response = await fetch(route, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let decoded;
    try {
      decoded = await response.json();
    } catch (parseError) {
      throw new Error("malformed response: not valid JSON");
    }
    if (!response.ok) {
      throw new Error((decoded && decoded.error) || `server returned HTTP ${response.status}`);
    }
    return decoded;
  }

  function showOnly(section) {
    els.loading.hidden = section !== "loading";
    els.idle.hidden = section !== "idle";
    els.error.hidden = section !== "error";
    els.result.hidden = section !== "result";
  }

  function fail(message) {
    payload = null;
    showOnly("error");
    els.errorDetail.textContent = message;
  }

  // The count is read as an integer string and sent as a number. An entry that
  // is not a whole number is refused here rather than rounded into one -- the
  // observation contract is stated, never adjusted on the trader's behalf.
  function validateQuery() {
    const identifier = els.identifier.value.trim();
    const yieldField = els.yieldField.value.trim();
    const start = els.start.value.trim();
    const end = els.end.value.trim();
    const rawCount = els.observationCount.value.trim();
    const unit = els.fieldUnit.value.trim();

    if (!identifier) {
      return { error: "Enter a bond identifier (a 12-character ISIN or 9-character CUSIP) above." };
    }
    if (!yieldField) {
      return {
        error:
          "Enter the Bloomberg Yield field confirmed on the workstation above. This " +
          "calculation has no default field and will not guess one.",
      };
    }
    if (!start || !end) return { error: "Enter both a start date and an end date above." };
    if (start > end) return { error: "The start date must not be after the end date." };
    if (!/^\d+$/.test(rawCount)) {
      return { error: "Enter the number of Yield observations as a whole number (180 = Middle Office's 6M window)." };
    }
    const body = {
      bond_identifier: identifier,
      yield_field: yieldField,
      start_date: start,
      end_date: end,
      requested_observation_count: Number(rawCount),
    };
    if (unit) body.field_unit = unit;
    return { body };
  }

  async function calculate() {
    if (inFlight) return;
    const query = validateQuery();
    if (query.error) {
      fail(query.error);
      return;
    }

    inFlight = true;
    els.calculateBtn.classList.add("is-disabled");
    showOnly("loading");
    try {
      const loaded = await postJson(ROUTE, query.body);
      const problem = validatePayload(loaded);
      if (problem) {
        fail(problem);
        return;
      }
      payload = loaded;
      render();
      showOnly("result");
    } catch (error) {
      fail(error.message || String(error));
    } finally {
      inFlight = false;
      els.calculateBtn.classList.remove("is-disabled");
    }
  }

  // A half-understood answer is refused rather than displayed: a number on
  // screen under a Middle Office heading has to be one this server calculated.
  function validatePayload(candidate) {
    if (!candidate || typeof candidate !== "object") return "malformed response: not an object";
    for (const key of ["window_status", "standard_deviation_convention", "methodology"]) {
      if (typeof candidate[key] !== "string" || !candidate[key]) {
        return `malformed response: "${key}" is missing`;
      }
    }
    for (const key of ["requested_observation_count", "observation_count", "yield_change_count"]) {
      if (!Number.isInteger(candidate[key])) return `malformed response: "${key}" is not an integer`;
    }
    for (const key of ["blockers", "warnings"]) {
      if (!Array.isArray(candidate[key])) return `malformed response: "${key}" must be an array`;
    }
    for (const key of ["daily_yield_vol_text", "annualized_yield_vol_text"]) {
      if (candidate[key] !== null && typeof candidate[key] !== "string") {
        return `malformed response: "${key}" is neither a string nor null`;
      }
    }
    return null;
  }

  function render() {
    // The two figures are printed from the server's own repr strings. Nothing
    // is parsed into a JavaScript number and re-formatted, so no digit on
    // screen is one this page chose.
    els.annualized.textContent = text(payload.annualized_yield_vol_text);
    els.daily.textContent = text(payload.daily_yield_vol_text);
    const unitLabel = payload.field_unit ? String(payload.field_unit) : UNIT_UNCONFIRMED;
    els.annualizedUnit.textContent = unitLabel;
    els.dailyUnit.textContent = unitLabel;

    els.status.textContent = payload.window_status;
    els.status.className =
      payload.window_status === "FULL_WINDOW" ? "hyv-status-pill is-full" : "hyv-status-pill is-short";

    // Blocking and merely-qualified are rendered in separate boxes. Folding
    // them into one list is how a short window that DOES have a usable number
    // ends up read as a window that has none.
    const fill = (list, box, entries) => {
      list.textContent = "";
      for (const entry of entries) {
        const item = document.createElement("li");
        item.textContent = String(entry);
        list.appendChild(item);
      }
      box.hidden = entries.length === 0;
    };
    fill(els.blockerList, els.blockers, payload.blockers);
    fill(els.warningList, els.warnings, payload.warnings);
    // The warning heading must never claim usability the result does not
    // have. A window can be BOTH short and too short -- one warning, one
    // blocker, no number -- and a fixed "usable, but qualified" heading told
    // the trader the opposite (Codex review, PR #200). Usability is stated
    // only when nothing is blocking.
    els.warningsTitle.textContent = payload.blockers.length
      ? "Not a full-window result"
      : "Not a full-window result — usable, but qualified";

    els.methodologyValue.textContent = text(payload.methodology);
    els.security.textContent = text(payload.security);
    els.field.textContent = text(payload.yield_field);
    els.unit.textContent = payload.field_unit ? String(payload.field_unit) : UNIT_UNCONFIRMED;
    els.source.textContent = text(payload.source_system);
    els.requestedRange.textContent = `${text(payload.requested_start_date)} → ${text(
      payload.requested_end_date
    )}`;
    els.requestedCount.textContent = String(payload.requested_observation_count);
    els.actualCount.textContent = `${payload.observation_count} of ${payload.series_observation_count} returned`;
    els.changeCount.textContent = String(payload.yield_change_count);
    els.firstObservation.textContent = text(payload.first_observation_date);
    els.lastObservation.textContent = text(payload.last_observation_date);
    els.stdevConvention.textContent = text(payload.standard_deviation_convention);
    els.annualization.textContent = `× √${payload.annualization_trading_days}`;
    els.acquiredAt.textContent = text(payload.acquired_at);
    els.calculatedAt.textContent = text(payload.calculated_at);

    const source = payload.volatility_source;
    if (source) {
      // The published number is NOT the figure in the headline above: it is
      // that figure normalized to the unit BLIVolatilityInput states. Both
      // are shown, each labelled with its own unit and the factor between
      // them, so a normalized value and a raw one can never be swapped.
      const audit = source.override_or_fallback_audit
        ? ` — ${source.override_or_fallback_audit}`
        : "";
      els.sourceDetail.textContent =
        `${source.source_system} · ${source.volatility_basis} · ${source.status} · ` +
        `${text(source.volatility_text)} ${text(source.volatility_unit)} ` +
        `(${text(source.source_unit)} × ${text(source.normalization_factor)})${audit}`;
      els.sourceBlock.className = "hyv-source-block";
    } else {
      els.sourceDetail.textContent = text(payload.volatility_source_unavailable_reason);
      els.sourceBlock.className = "hyv-source-block is-unavailable";
    }
  }

  els.calculateBtn.addEventListener("click", () => calculate());
  for (const input of [els.observationCount, els.fieldUnit]) {
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") calculate();
    });
  }

  // Test-only, read-only accessors -- same __shioriTest* convention the other
  // Markets modules use. They drive exactly the code paths a trader's pointer
  // drives and compute nothing of their own.
  window.__shioriTestHistoricalYieldVolCalculate = () => calculate();
  window.__shioriTestHistoricalYieldVolPayload = () => payload;
  window.__shioriTestHistoricalYieldVolRequestedRoutes = () => requestedRoutes.slice();
})();
