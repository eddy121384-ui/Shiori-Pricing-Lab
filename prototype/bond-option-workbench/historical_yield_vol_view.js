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
  // The server's `_DECIMAL_ANNUAL_NORMALIZATION_FACTORS`, which is Annex A
  // A.8.1's closed vocabulary. Held here only to check what the card claims
  // was applied -- no value on screen is normalized by this page.
  const DECIMAL_ANNUAL_FACTORS = { DECIMAL: 1, PERCENT: 0.01, BASIS_POINTS: 0.0001 };
  // Two observations make one change, and one change has no ddof=1 standard
  // deviation -- the server refuses anything smaller before it calculates.
  const MINIMUM_REQUESTED_OBSERVATIONS = 3;
  // Two changes is what a ddof=1 standard deviation needs.
  const MINIMUM_CHANGES_FOR_STDEV = 2;
  // `bloomberg_bond_yield_history.SOURCE_SYSTEM`: the one acquisition path
  // this statistic is built on.
  const BLOOMBERG_SOURCE_SYSTEM = "BLOOMBERG_DAPI";
  // The one annualization this calculation uses, printed as "x sqrt(252)".
  const ANNUALIZATION_TRADING_DAYS = 252;
  // The one methodology this route emits, under the one convention it uses.
  const CARD_METHODOLOGY_LABELS = {
    methodology: "HISTORICAL_YIELD_VOL_MO",
    standard_deviation_convention: "SAMPLE_STDEV_S_DDOF_1",
  };

  // The shape both the #196 loader and the calculator stamp:
  // `datetime.now().astimezone().isoformat(timespec="seconds")`. The offset is
  // required, not optional -- a local reading names no placeable moment, and
  // `acquired_at` exists precisely to tell two acquisitions apart.
  function isOffsetAwareTimestamp(value) {
    if (typeof value !== "string") return false;
    const parts =
      /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.exec(
        value
      );
    if (parts === null) return false;
    // Digits alone are not a calendar. `2026-02-31T14:05:00+00:00` and
    // `2026-01-01T24:00:00Z` both match the shape, and V8 rolls both over to
    // a *different* moment -- the second into the next day -- while the
    // server's `datetime.fromisoformat` refuses them outright (Codex review,
    // PR #200). Walk the components back out, the way `isIsoCalendarDate`
    // does, so a stamp can only mean the moment it spells.
    const [, year, month, day, hour, minute, second] = parts;
    const walked = new Date(
      Date.UTC(
        Number(year),
        Number(month) - 1,
        Number(day),
        Number(hour),
        Number(minute),
        Number(second)
      )
    );
    if (Number.isNaN(walked.getTime())) return false;
    const spelled = `${year}-${month}-${day}T${hour}:${minute}:${second}`;
    if (walked.toISOString().slice(0, 19) !== spelled) return false;
    // The offset itself still has to name a real one: "+25:00" spells a
    // shift no zone has, and only parsing the whole stamp catches it.
    return Number.isFinite(Date.parse(value));
  }

  // A strict ISO calendar date, the way the route serializes one. Parsed and
  // then compared back, so "2026-02-31" is refused rather than rolled over.
  function isIsoCalendarDate(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const parsed = new Date(`${value}T00:00:00Z`);
    return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
  }
  // The labels the canonical source always carries. Not a vocabulary the card
  // may extend: this route publishes one source and no other.
  const PUBLISHED_SOURCE_LABELS = {
    source_system: "HISTORICAL_YIELD_VOL_MO",
    volatility_basis: "YIELD_VOL",
    status: "ACTIVE",
    volatility_unit: "DECIMAL_ANNUAL",
  };

  // `decimal_annual_normalization_factor` trims and upper-cases before it
  // matches, and the server publishes `source_unit` as the trader typed it --
  // so "percent" is a legitimate HTTP 200 whose factor really is 0.01. A raw
  // lookup here refused that honest answer (Codex review, PR #200). This is
  // lexical tidying of someone's typing, never an interpretation of what a
  // unit means: an unknown spelling still fails closed.
  function canonicalUnit(value) {
    return typeof value === "string" ? value.trim().toUpperCase() : value;
  }
  const UNIT_UNCONFIRMED = "Unit not confirmed by this request";

  let payload = null;
  let inFlight = false;
  const requestedRoutes = [];

  function text(value) {
    return value === null || value === undefined || value === "" ? EM_DASH : String(value);
  }

  // A string that renders as nothing is not an answer. text() turns "" into
  // an em dash, and a whitespace-only string into whitespace, so a validator
  // that checks only `typeof value === "string"` lets a blank field reach the
  // card as a dash where a real sentence belongs (Codex review, PR #200).
  function isNonBlankString(value) {
    return typeof value === "string" && value.trim() !== "";
  }

  // Every figure this card prints comes from a string, and the string is what
  // reaches the screen -- so it has to be the number it claims to be. One
  // function for all three of them, because writing this rule once per figure
  // is exactly how the third one was left out: the top-level pair was fixed
  // and `volatility_source.volatility_text` kept rendering whatever it said
  // (Codex review, PR #200).
  //
  // Numeric equality rather than string equality: Python's repr and
  // JavaScript's String() spell the same float differently (1e-05 against
  // 0.00001), and it is the value that must match, not the spelling.
  function figureTextProblem(textKey, rendered, numberKey, value) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      return `malformed response: "${numberKey}" is not a finite number`;
    }
    if (!isNonBlankString(rendered)) {
      return `malformed response: "${textKey}" is not a non-blank string`;
    }
    if (Number(rendered) !== value) {
      return `malformed response: "${textKey}" does not read back as "${numberKey}"`;
    }
    // A standard deviation, and its annualization, are never negative. The
    // server's own result guard refuses this; the card rendered `-1` beside a
    // matching "-1" because agreement and finiteness were the only tests
    // (Codex review, PR #200).
    if (value < 0) {
      return `malformed response: "${numberKey}" is ${value}, and a volatility is never negative`;
    }
    return null;
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
    // A count past 2^53-1 does not survive Number(): 9007199254740993 arrives
    // as ...992, and the server would then calculate and audit a window the
    // trader never asked for while this card claims the count is sent
    // verbatim (Codex review, PR #200). Refused rather than silently rounded.
    if (!Number.isSafeInteger(Number(rawCount))) {
      return {
        error:
          "That observation count is too large to send exactly. Enter a whole number this " +
          "page can represent without rounding it.",
      };
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
    // Every string this page presents as audited provenance is checked, not
    // just the three that shape the layout (Codex review, PR #200). render()
    // coerces whatever it is given, so an object or a number arrived on
    // screen looking like a source system or an acquisition timestamp.
    // The methodology and the convention are not free text either: this route
    // emits one methodology under one convention, and the card prints both as
    // the method behind the figure. `POPULATION_STDEV_P` was rendered as the
    // methodology of a successful Middle Office result (Codex review, #200).
    for (const [key, fixed] of Object.entries(CARD_METHODOLOGY_LABELS)) {
      if (candidate[key] !== fixed) {
        return (
          `malformed response: "${key}" is ${JSON.stringify(candidate[key])}; ` +
          `this card shows only ${fixed}`
        );
      }
    }
    // The annualization is part of that same methodology and the card prints
    // it as "x sqrt(252)". Left out of the label check, `365` was displayed
    // as the convention behind the figures (Codex review, PR #200).
    if (candidate.annualization_trading_days !== ANNUALIZATION_TRADING_DAYS) {
      return (
        `malformed response: "annualization_trading_days" is ` +
        `${JSON.stringify(candidate.annualization_trading_days)}; this card shows only ` +
        `${ANNUALIZATION_TRADING_DAYS}`
      );
    }
    // The Yield field's own unit, printed beside both figures whether or not
    // anything was published. Only checked when a source existed, so on the
    // deliberate figure-without-source path an object reached the card as
    // "[object Object]" beside two real risk numbers (Codex review, #200).
    if (candidate.field_unit !== null && !isNonBlankString(candidate.field_unit)) {
      return 'malformed response: "field_unit" is neither a non-blank string nor null';
    }
    // The acquisition path this statistic is built on, checked exactly the way
    // the server guard checks it. `REUTERS` was rendered as provenance under a
    // heading that says Bloomberg (Codex review, PR #200). Exact and
    // case-sensitive: unlike the Yield unit this label never passes through a
    // trader's typing, so there is nothing to tidy.
    if (candidate.source_system !== BLOOMBERG_SOURCE_SYSTEM) {
      return (
        `malformed response: "source_system" is ${JSON.stringify(candidate.source_system)}; ` +
        `this card shows only ${BLOOMBERG_SOURCE_SYSTEM}`
      );
    }
    // Both timestamps are evidence of *when* -- when Bloomberg was read, and
    // when this number was calculated -- and "not-a-time" was displayed as
    // exactly that. An offset is what makes the moment placeable; a
    // well-formed local reading is refused for the same reason.
    for (const key of ["acquired_at", "calculated_at"]) {
      if (!isOffsetAwareTimestamp(candidate[key])) {
        return (
          `malformed response: "${key}" is ${JSON.stringify(candidate[key])}; it must be an ` +
          "ISO-8601 timestamp with a UTC offset"
        );
      }
    }
    for (const key of [
      "window_status",
      "security",
      "yield_field",
    ]) {
      // isNonBlankString, not truthiness: "   " is truthy and renders as
      // nothing, so the card showed visually blank audit provenance beside
      // the risk figure. The same rule was already applied to the nested
      // source's strings and to the unavailability reason, and this loop --
      // the original one -- was left on truthiness (Codex review, PR #200).
      if (!isNonBlankString(candidate[key])) {
        return `malformed response: "${key}" is missing`;
      }
    }
    for (const key of ["blockers", "warnings"]) {
      if (!Array.isArray(candidate[key])) return `malformed response: "${key}" must be an array`;
      // These entries ARE the refusal and the qualification a trader reads.
      // An object rendered as "[object Object]" and a blank string as an
      // empty bullet, in the one place the card explains itself (Codex
      // review, PR #200). Checked before any length is treated as meaningful.
      if (!candidate[key].every(isNonBlankString)) {
        return `malformed response: every "${key}" entry must be a non-blank string`;
      }
    }
    // The observation dates behind the figure, and the two endpoints the card
    // prints as "first/last observation used". Never checked against each
    // other or against the count, so a null endpoint drew a dash and an
    // object drew "[object Object]" beside the risk figure, and an endpoint
    // contradicting the list was false Bloomberg provenance (Codex review,
    // PR #200).
    if (!Array.isArray(candidate.observation_dates)) {
      return 'malformed response: "observation_dates" must be an array';
    }
    if (!candidate.observation_dates.every(isIsoCalendarDate)) {
      return 'malformed response: every "observation_dates" entry must be an ISO calendar date';
    }
    for (let index = 1; index < candidate.observation_dates.length; index += 1) {
      if (candidate.observation_dates[index - 1] >= candidate.observation_dates[index]) {
        return (
          `malformed response: "observation_dates" is not ascending at ` +
          `${candidate.observation_dates[index]}`
        );
      }
    }
    // The Bloomberg request range, printed verbatim as this calculation's
    // provenance. Never inspected at all before -- an object rendered as
    // "[object Object]" under "requested range" (Codex review, PR #200).
    for (const key of ["requested_start_date", "requested_end_date"]) {
      if (!isIsoCalendarDate(candidate[key])) {
        return `malformed response: "${key}" is not an ISO calendar date`;
      }
    }
    if (candidate.requested_start_date > candidate.requested_end_date) {
      return (
        `malformed response: the requested range ${candidate.requested_start_date}..` +
        `${candidate.requested_end_date} ends before it starts`
      );
    }
    for (const key of [
      "series_observation_count",
      "requested_observation_count",
      "observation_count",
      "yield_change_count",
    ]) {
      // isSafeInteger, not isInteger: 9007199254740993 on the wire is parsed
      // as ...992 and displayed as a different observation contract from the
      // one received. The query path has refused exactly this since the third
      // round; the response path had not (Codex review, PR #200).
      if (!Number.isSafeInteger(candidate[key])) {
        return `malformed response: "${key}" is not a whole number this page can represent`;
      }
      // Integrality is not the question a count answers. `-1 of -1
      // observations` satisfied every arithmetic rule below and was rendered
      // as this figure's provenance (Codex review, PR #200).
      if (candidate[key] < 0) return `malformed response: "${key}" is ${candidate[key]}`;
    }
    // The counts are this card's provenance for the figure above them, and
    // they are not three independent numbers: the window is the smaller of
    // what was asked for and what came back, and N observations make N-1
    // changes. Typed but unrelated, they read as "calculated from 180
    // observations (0 Yield Changes)" under a real number (Codex review,
    // PR #200).
    const expectedObservations = Math.min(
      candidate.series_observation_count,
      candidate.requested_observation_count,
    );
    if (candidate.observation_count !== expectedObservations) {
      return (
        `malformed response: "observation_count" is ${candidate.observation_count}, and a ` +
        `window of ${candidate.requested_observation_count} over ` +
        `${candidate.series_observation_count} returned observations is ${expectedObservations}`
      );
    }
    const expectedChanges = Math.max(candidate.observation_count - 1, 0);
    if (candidate.yield_change_count !== expectedChanges) {
      return (
        `malformed response: "yield_change_count" is ${candidate.yield_change_count}, and ` +
        `${candidate.observation_count} observations make ${expectedChanges}`
      );
    }
    if (candidate.requested_observation_count < MINIMUM_REQUESTED_OBSERVATIONS) {
      return (
        `malformed response: "requested_observation_count" is ` +
        `${candidate.requested_observation_count}; this calculation needs at least ` +
        `${MINIMUM_REQUESTED_OBSERVATIONS}`
      );
    }
    // Inside the range the card prints as the Bloomberg request. Ascending,
    // counted and endpoint-matched was not enough: a list that begins before
    // the requested start or ends after the requested end passed all three
    // and displayed a request range its own observations contradict (Codex
    // review, PR #200).
    for (const used of candidate.observation_dates) {
      if (used < candidate.requested_start_date || used > candidate.requested_end_date) {
        return (
          `malformed response: observation ${used} is outside the requested range ` +
          `${candidate.requested_start_date}..${candidate.requested_end_date}`
        );
      }
    }
    if (candidate.observation_dates.length !== candidate.observation_count) {
      return (
        `malformed response: "observation_count" is ${candidate.observation_count} and ` +
        `${candidate.observation_dates.length} observation date(s) are listed`
      );
    }
    const expectedFirst = candidate.observation_dates.length
      ? candidate.observation_dates[0]
      : null;
    const expectedLast = candidate.observation_dates.length
      ? candidate.observation_dates[candidate.observation_dates.length - 1]
      : null;
    if (candidate.first_observation_date !== expectedFirst) {
      return (
        `malformed response: "first_observation_date" is ` +
        `${JSON.stringify(candidate.first_observation_date)}, and the observations used ` +
        `start at ${JSON.stringify(expectedFirst)}`
      );
    }
    if (candidate.last_observation_date !== expectedLast) {
      return (
        `malformed response: "last_observation_date" is ` +
        `${JSON.stringify(candidate.last_observation_date)}, and the observations used ` +
        `end at ${JSON.stringify(expectedLast)}`
      );
    }
    // The status is not an independent label: it is what the counts say. A
    // short window reported as FULL_WINDOW got the green treatment and lost
    // its qualification, and the counts underneath it said otherwise the
    // whole time (Codex review, PR #200). The zero case is tested first, the
    // same order the calculator uses.
    const expectedStatus = candidate.observation_count === 0
      ? "NO_HISTORY"
      : candidate.observation_count === candidate.requested_observation_count
        ? "FULL_WINDOW"
        : "INSUFFICIENT_HISTORY";
    if (candidate.window_status !== expectedStatus) {
      return (
        `malformed response: "window_status" is "${candidate.window_status}" for ` +
        `${candidate.observation_count} of ${candidate.requested_observation_count} ` +
        `observations, which is ${expectedStatus}`
      );
    }
    // The warning is that qualification, so it belongs to exactly that status
    // -- the same rule the server's own result guard carries, and exactly one
    // of them: two warnings is not "a warning" either.
    const expectedWarnings = expectedStatus === "INSUFFICIENT_HISTORY" ? 1 : 0;
    if (candidate.warnings.length !== expectedWarnings) {
      return (
        `malformed response: ${expectedStatus} with ${candidate.warnings.length} warning(s); ` +
        `that status carries exactly ${expectedWarnings}`
      );
    }
    // The card does NOT check what the warning says, and that is a decision
    // rather than an oversight (Codex review, PR #200).
    //
    // Three content rules were written here and all three were wrong. An
    // unbounded substring found "90 of the requested 180" inside "190 of the
    // requested 180". A fixed count prefix plus a whole-token search accepted
    // "90 of the requested 1180 ... The previous target was 180." Each looked
    // like a check on whether the warning describes this window, and was
    // really a search for characters. The only version that is not is the
    // whole sentence -- a second copy of the server's prose in JavaScript,
    // which is the drift this file has spent thirteen review rounds paying
    // for, and which would break the card whenever the server rewords an
    // explanation that was never wrong.
    //
    // So the structural rules above stay -- exactly one warning for
    // INSUFFICIENT_HISTORY, none otherwise, every entry a non-blank string --
    // and the content gap is carried openly: `result_shape_problem` enforces
    // the exact sentence server-side, and a machine-readable warning code in
    // the payload is with Eddy as a contract change. A rule that looks like
    // validation and is not is worse than a stated gap.

    // The two headline figures are printed from these strings verbatim, so a
    // string that is not a number is a fabricated risk figure on screen under
    // a Middle Office heading -- `annualized_yield_vol_text: "not calculated"`
    // was displayed as the result (Codex review, PR #200). Each text field is
    // checked against the numeric field it is the repr of: present exactly
    // when that one is, and reading back as exactly that number. Numeric
    // equality rather than string equality on purpose -- Python's repr and
    // JavaScript's String() disagree on the same float (1e-05 vs 0.00001),
    // and it is the value that must match, not the spelling.
    for (const [textKey, numberKey] of [
      ["daily_yield_vol_text", "daily_yield_vol"],
      ["annualized_yield_vol_text", "annualized_yield_vol"],
    ]) {
      const value = candidate[numberKey];
      const rendered = candidate[textKey];
      if (value === null || value === undefined) {
        if (rendered !== null && rendered !== undefined) {
          return `malformed response: "${textKey}" carries text where "${numberKey}" is absent`;
        }
        continue;
      }
      const problem = figureTextProblem(textKey, rendered, numberKey, value);
      if (problem !== null) return problem;
    }
    // The nested source is inspected, not merely tested for truthiness
    // (Codex review, PR #200). `volatility_source: {}` is truthy, so the
    // renderer treated it as a published source and drew a normal block full
    // of dashes and `undefined` -- a half-understood risk result presented as
    // a whole one, which is the opposite of what this validator promises.
    const source = candidate.volatility_source;
    if (source !== null && source !== undefined) {
      if (typeof source !== "object" || Array.isArray(source)) {
        return 'malformed response: "volatility_source" is neither an object nor null';
      }
      // Publication either succeeded or it did not: the route fills exactly
      // one of these two from one try/except. A payload carrying both was
      // accepted here, and `render()` takes the source branch -- so the card
      // drew an ACTIVE normalized risk source while the same payload said
      // publication had failed, and said why where nobody could read it
      // (Codex review, PR #200).
      const refusal = candidate.volatility_source_unavailable_reason;
      if (refusal !== null && refusal !== undefined) {
        return (
          'malformed response: "volatility_source" is published and ' +
          `"volatility_source_unavailable_reason" is ${JSON.stringify(refusal)}; ` +
          "publication cannot both succeed and be refused"
        );
      }
      // Four of these are fixed by the contract, not free text: this route
      // can serialize only the canonical source, and `BLIVolatilityInput`
      // itself refuses a non-active one. `status: "STALE"` and
      // `volatility_basis: "PRICE_VOL"` were drawn in the normal block as
      // though published (Codex review, PR #200). Exact matches on purpose --
      // these are enum `.value` strings the server generates, never anything
      // a trader typed, so there is nothing here to canonicalize.
      for (const [key, fixed] of Object.entries(PUBLISHED_SOURCE_LABELS)) {
        if (source[key] !== fixed) {
          return (
            `malformed response: volatility_source."${key}" is ` +
            `${JSON.stringify(source[key])}; this card shows only ${fixed}`
          );
        }
      }
      for (const key of [
        // "volatility_text" is deliberately not here: it is checked below
        // against the number it is the text of, which subsumes non-blank.
        "source_unit",
        // Always populated by the publication helper, and the only place a
        // published number's calculation provenance appears on this card --
        // omitted, the renderer silently dropped it; as an object it appended
        // "[object Object]" (Codex review, PR #200).
        "override_or_fallback_audit",
      ]) {
        if (!isNonBlankString(source[key])) {
          return `malformed response: volatility_source."${key}" is missing`;
        }
      }
      const publishedProblem = figureTextProblem(
        'volatility_source."volatility_text"',
        source.volatility_text,
        'volatility_source."volatility"',
        source.volatility,
      );
      if (publishedProblem !== null) return publishedProblem;
      // Strictly positive, and only here. A zero headline is an honest flat
      // window -- identical Yield Changes really do give sigma 0 -- and the
      // publication helper refuses exactly that with a reason, so the shared
      // figure rule must keep allowing it. Requiring it everywhere would
      // refuse a legitimate answer, which is the mistake the `percent`
      // canonicalization finding already cost (Codex review, PR #200).
      if (source.volatility <= 0) {
        return (
          'malformed response: volatility_source."volatility" is ' +
          `${source.volatility}; a published volatility is never zero or negative`
        );
      }
      // The card prints "source unit x factor" as the exact normalization
      // applied to an ACTIVE risk source, so an arbitrary finite number there
      // is false provenance, not a cosmetic slip -- `PERCENT` beside `-1` was
      // displayed as fact (Codex review, PR #200). The vocabulary is closed
      // and fixed by Annex A A.8.1 (1 bp = 1e-4); this is a label checked
      // against a reviewed constant, not a calculation repeated in the
      // browser, and nothing here recomputes the published number.
      const sourceUnit = canonicalUnit(source.source_unit);
      const expectedFactor = DECIMAL_ANNUAL_FACTORS[sourceUnit];
      if (expectedFactor === undefined) {
        return (
          'malformed response: volatility_source."source_unit" is ' +
          `"${source.source_unit}", which is not one of ` +
          `${Object.keys(DECIMAL_ANNUAL_FACTORS).join(", ")}`
        );
      }
      if (source.normalization_factor !== expectedFactor) {
        return (
          'malformed response: volatility_source."normalization_factor" is ' +
          `${source.normalization_factor} for source unit "${source.source_unit}", ` +
          `which normalizes by ${expectedFactor}`
        );
      }
      // The server publishes `source_unit` as a copy of the same `field_unit`
      // the headline is labelled with, so the two disagreeing is impossible --
      // and the card would otherwise label the raw figure PERCENT while
      // claiming its normalized source came from DECIMAL, with an internally
      // valid factor under it (Codex review, PR #200).
      if (canonicalUnit(candidate.field_unit) !== sourceUnit) {
        return (
          `malformed response: the headline is labelled "${candidate.field_unit}" while its ` +
          `normalized source claims "${source.source_unit}"`
        );
      }
    } else if (!isNonBlankString(candidate.volatility_source_unavailable_reason)) {
      // No source and no reason is not an answer either: the card would show
      // an em dash where the refusal belongs. A blank or whitespace-only
      // reason renders as exactly that dash, so it is refused for the same
      // reason a missing one is (Codex review, PR #200).
      return 'malformed response: no "volatility_source" and no reason for its absence';
    }

    // Each figure agreeing with its own number is not enough: absence agrees
    // with absence, so a payload with no blockers, an ACTIVE normalized source
    // and BOTH headline pairs null passed every rule above and drew two dashes
    // beside a published risk figure (Codex review, PR #200). Availability is
    // a property of the whole answer, not of one field, and it is the same
    // invariant the calculator's `is_usable` carries: a fatal blocker means
    // there is no number, and no blocker means there is one.
    // Both figures, not just the annualized one: the daily sigma alone being
    // null passed every per-field rule and drew a real risk figure beside a
    // dash, a shape the calculator cannot produce (Codex review, PR #200).
    const present = (value) => value !== null && value !== undefined;
    if (present(candidate.annualized_yield_vol) !== present(candidate.daily_yield_vol)) {
      return 'malformed response: one Historical Yield Vol figure is present and the other is not';
    }
    const figuresPresent = present(candidate.annualized_yield_vol);
    // Availability follows the change count, which is what the convention
    // actually needs -- two observations and one change with populated
    // figures passed every other rule, and ddof=1 cannot produce a standard
    // deviation from one change (Codex review, PR #200).
    if (figuresPresent !== candidate.yield_change_count >= MINIMUM_CHANGES_FOR_STDEV) {
      return (
        `malformed response: ${candidate.yield_change_count} Yield Change(s) ` +
        (figuresPresent ? "cannot produce" : "produce") +
        ` the ${MINIMUM_CHANGES_FOR_STDEV}-change standard deviation this card shows`
      );
    }
    if (candidate.blockers.length === 0 && !figuresPresent) {
      return 'malformed response: no "blockers", yet no Historical Yield Vol to show';
    }
    if (candidate.blockers.length > 0 && figuresPresent) {
      return 'malformed response: a fatal blocker beside a Historical Yield Vol';
    }
    // The reverse does NOT hold and must not be asserted: a figure exists with
    // no published source whenever the Yield field's unit was never confirmed,
    // which is an honest answer this card is built to show.
    if (!figuresPresent && candidate.volatility_source) {
      return 'malformed response: a published volatility source with no figure behind it';
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
