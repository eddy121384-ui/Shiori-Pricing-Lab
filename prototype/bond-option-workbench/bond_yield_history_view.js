// Markets -> Bond Yield History (Issue #196): a read-only view of one bond's
// own raw Bloomberg historical Yield series.
//
// What this file does: asks the bridge for the observations Bloomberg returned
// for one bond, one confirmed Yield field and one explicit date range, and
// shows exactly those observations twice -- as a chronological Date / Yield
// table, and as a Yield-history line over the same points.
//
// What this file never does, and must never start doing:
//
//   * guess, default, remember, or substitute a Bloomberg Yield field. The
//     mnemonic is typed by the trader from workstation evidence; with the box
//     empty this view sends no request at all;
//   * fill, interpolate, forward-fill, back-fill, smooth, or resample. A date
//     Bloomberg did not answer for is absent -- it gets no row and no dot --
//     and a returned row with no value is drawn as no dot and rendered as an
//     em dash, never zero and never a neighbour's number;
//   * compute a volatility statistic. There is no standard deviation, no
//     annualization, and no Historical Vol anywhere in this file. The optional
//     daily-change column is a display derivative only (see dailyChanges);
//   * price, capture, confirm, store, or mutate anything. The one route it
//     calls is POST /api/bloomberg/bond-yield-history, which reads Bloomberg
//     and writes nothing;
//   * load anything over the network beyond that one same-origin route. The
//     chart is inline SVG drawn here -- no charting library, no CDN -- so this
//     page renders the same with corporate internet blocked.
(function () {
  "use strict";

  const panel = document.getElementById("markets-panel-yield-history");
  // Not present on this page (or an older markup revision): do nothing at all
  // rather than half-wire a view.
  if (!panel) return;

  const els = {
    identifier: document.getElementById("byh-identifier"),
    yieldField: document.getElementById("byh-yield-field"),
    start: document.getElementById("byh-start"),
    end: document.getElementById("byh-end"),
    loadBtn: document.getElementById("byh-load-btn"),
    provenance: document.getElementById("byh-provenance"),
    requestedIdentifier: document.getElementById("byh-requested-identifier"),
    security: document.getElementById("byh-security"),
    fieldMnemonic: document.getElementById("byh-field-mnemonic"),
    fieldMeaning: document.getElementById("byh-field-meaning"),
    fieldUnit: document.getElementById("byh-field-unit"),
    source: document.getElementById("byh-source"),
    requestedRange: document.getElementById("byh-requested-range"),
    observationCount: document.getElementById("byh-observation-count"),
    firstObservation: document.getElementById("byh-first-observation"),
    lastObservation: document.getElementById("byh-last-observation"),
    acquiredAt: document.getElementById("byh-acquired-at"),
    loading: document.getElementById("byh-loading"),
    idle: document.getElementById("byh-idle"),
    empty: document.getElementById("byh-empty"),
    error: document.getElementById("byh-error"),
    errorDetail: document.getElementById("byh-error-detail"),
    chartCard: document.getElementById("byh-chart-card"),
    chartYLabel: document.getElementById("byh-chart-ylabel"),
    chartWrap: document.getElementById("byh-chart-svg-wrap"),
    tableCard: document.getElementById("byh-table-card"),
    tableBody: document.getElementById("byh-table-body"),
    showChange: document.getElementById("byh-show-change"),
    changeNote: document.getElementById("byh-change-note"),
  };
  for (const key of Object.keys(els)) {
    if (!els[key]) return;
  }

  const ROUTE = "/api/bloomberg/bond-yield-history";
  const SVG_NS = "http://www.w3.org/2000/svg";
  const EM_DASH = "—";

  const CHART_WIDTH = 880;
  const CHART_HEIGHT = 300;
  const CHART_MARGIN = { top: 12, right: 16, bottom: 30, left: 56 };
  const MIN_X_TICK_GAP = 70;

  let payload = null;
  // One load at a time: every entry point below returns early while a request
  // is in flight, so a slow answer can never land on top of a newer one.
  let inFlight = false;
  const requestedRoutes = [];

  // ---- Values are shown exactly as Bloomberg sent them -----------------------

  // The table prints the response's own raw value string, so every digit
  // Bloomberg returned survives -- a value is never re-formatted through a
  // float, rounded, or padded here.
  function observationText(observation) {
    if (observation.raw_value === null || observation.raw_value === undefined) return EM_DASH;
    return String(observation.raw_value);
  }

  function hasValue(observation) {
    return typeof observation.yield_value === "number" && Number.isFinite(observation.yield_value);
  }

  function provenanceText(value) {
    return value === null || value === undefined || value === "" ? EM_DASH : String(value);
  }

  // ---- Daily Yield Change: a display derivative, and nothing more ------------

  // Exact decimal subtraction of two Bloomberg value strings. Scaling both to
  // integers and subtracting in BigInt keeps the answer exactly what the two
  // printed decimals imply -- a binary float subtraction of, say, 4.12 and 4.0
  // would put digits on screen that neither observation contains.
  //
  // This is `Yield_t - Yield_{t-1}` against the PREVIOUS RETURNED OBSERVATION,
  // which is not necessarily the previous calendar day. It is never stored,
  // never sent anywhere, and is never an input to a statistic: no standard
  // deviation and no annualization is computed in this file (Issue #196).
  const DECIMAL_RE = /^[+-]?\d+(\.\d+)?$/;

  function exactDecimalDifference(currentRaw, previousRaw) {
    if (typeof currentRaw !== "string" || typeof previousRaw !== "string") return null;
    if (!DECIMAL_RE.test(currentRaw.trim()) || !DECIMAL_RE.test(previousRaw.trim())) return null;
    const parse = (text) => {
      const [whole, fraction = ""] = text.trim().split(".");
      return { digits: whole + fraction, scale: fraction.length };
    };
    const current = parse(currentRaw);
    const previous = parse(previousRaw);
    const scale = Math.max(current.scale, previous.scale);
    const lift = (part) => BigInt(part.digits) * BigInt(10) ** BigInt(scale - part.scale);
    const difference = lift(current) - lift(previous);
    if (scale === 0) return difference.toString();
    const negative = difference < BigInt(0);
    const digits = (negative ? -difference : difference).toString().padStart(scale + 1, "0");
    const whole = digits.slice(0, digits.length - scale);
    const fraction = digits.slice(digits.length - scale);
    return `${negative ? "-" : ""}${whole}.${fraction}`;
  }

  // One entry per observation, aligned with the table's rows. `null` where a
  // change cannot be stated: the first row, either side missing a value, or a
  // value whose format is not a plain decimal. A gap is never bridged and a
  // missing side is never treated as zero.
  function dailyChanges(observations) {
    return observations.map((observation, index) => {
      if (index === 0) return null;
      const previous = observations[index - 1];
      if (!hasValue(observation) || !hasValue(previous)) return null;
      return exactDecimalDifference(observation.raw_value, previous.raw_value);
    });
  }

  // ---- Loading ---------------------------------------------------------------

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
    els.empty.hidden = section !== "empty";
    els.error.hidden = section !== "error";
    const showSeries = section === "series";
    els.chartCard.hidden = !showSeries;
    els.tableCard.hidden = !showSeries;
    // Provenance stays on screen for an empty series too: "Bloomberg returned
    // nothing for this field over this range" is an answer worth auditing.
    els.provenance.hidden = !(showSeries || section === "empty");
  }

  function fail(message) {
    payload = null;
    showOnly("error");
    els.errorDetail.textContent = message;
  }

  function validateQuery() {
    const identifier = els.identifier.value.trim();
    const yieldField = els.yieldField.value.trim();
    const start = els.start.value.trim();
    const end = els.end.value.trim();
    if (!identifier) {
      return { error: "Enter a bond identifier (a 12-character ISIN or 9-character CUSIP)." };
    }
    if (!yieldField) {
      return {
        error:
          "Enter the Bloomberg Yield field confirmed on the workstation. This view has no " +
          "default field and will not guess one.",
      };
    }
    if (!start || !end) return { error: "Enter both a start date and an end date." };
    if (start > end) return { error: "The start date must not be after the end date." };
    return {
      body: {
        bond_identifier: identifier,
        yield_field: yieldField,
        start_date: start,
        end_date: end,
      },
    };
  }

  async function loadHistory() {
    if (inFlight) return;
    const query = validateQuery();
    if (query.error) {
      fail(query.error);
      return;
    }

    inFlight = true;
    els.loadBtn.classList.add("is-disabled");
    showOnly("loading");
    try {
      const loaded = await postJson(ROUTE, query.body);
      const problem = validatePayload(loaded);
      if (problem) {
        fail(problem);
        return;
      }
      payload = loaded;
      renderProvenance();
      if (!payload.observations.length) {
        showOnly("empty");
        return;
      }
      showOnly("series");
      renderTable();
      renderChart();
    } catch (error) {
      fail(error.message || String(error));
    } finally {
      inFlight = false;
      els.loadBtn.classList.remove("is-disabled");
    }
  }

  // A malformed answer is refused rather than drawn: a chart built from a
  // half-understood payload is worse than no chart.
  function validatePayload(candidate) {
    if (!candidate || typeof candidate !== "object") return "malformed response: not an object";
    if (!Array.isArray(candidate.observations)) {
      return 'malformed response: "observations" must be an array';
    }
    for (const observation of candidate.observations) {
      if (!observation || typeof observation !== "object") {
        return "malformed response: an observation is not an object";
      }
      if (typeof observation.date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(observation.date)) {
        return "malformed response: an observation has no YYYY-MM-DD date";
      }
      const value = observation.yield_value;
      if (value !== null && (typeof value !== "number" || !Number.isFinite(value))) {
        return `malformed response: observation ${observation.date} has a non-finite value`;
      }
    }
    return null;
  }

  function renderProvenance() {
    els.requestedIdentifier.textContent = provenanceText(payload.requested_identifier);
    els.security.textContent = provenanceText(payload.security);
    els.fieldMnemonic.textContent = provenanceText(payload.yield_field);
    els.fieldMeaning.textContent = payload.field_meaning
      ? String(payload.field_meaning)
      : "Not confirmed by this request";
    els.fieldUnit.textContent = payload.field_unit
      ? String(payload.field_unit)
      : "Not confirmed by this request";
    els.source.textContent = provenanceText(payload.source_system);
    els.requestedRange.textContent = `${provenanceText(payload.requested_start_date)} → ${provenanceText(
      payload.requested_end_date
    )}`;
    els.observationCount.textContent = String(payload.observations.length);
    els.firstObservation.textContent = provenanceText(payload.first_observation_date);
    els.lastObservation.textContent = provenanceText(payload.last_observation_date);
    els.acquiredAt.textContent = provenanceText(payload.acquired_at);
    // The chart's y axis never claims a unit Bloomberg has not confirmed.
    els.chartYLabel.textContent = payload.field_unit
      ? `${payload.yield_field} (${payload.field_unit})`
      : `${payload.yield_field} (unit not confirmed)`;
  }

  // ---- Table -----------------------------------------------------------------

  function renderTable() {
    els.tableBody.textContent = "";
    const observations = payload.observations;
    const changes = dailyChanges(observations);
    const showChange = els.showChange.checked;

    for (const [index, observation] of observations.entries()) {
      const row = document.createElement("tr");

      const dateCell = document.createElement("td");
      dateCell.textContent = observation.date;
      row.appendChild(dateCell);

      const valueCell = document.createElement("td");
      valueCell.className = hasValue(observation) ? "num" : "num unavailable";
      valueCell.textContent = observationText(observation);
      if (!hasValue(observation)) {
        valueCell.title = "Bloomberg returned this row with no value. Nothing has been filled in.";
      }
      row.appendChild(valueCell);

      const changeCell = document.createElement("td");
      changeCell.className = changes[index] === null ? "num unavailable byh-change-col" : "num byh-change-col";
      changeCell.textContent = changes[index] === null ? EM_DASH : changes[index];
      changeCell.hidden = !showChange;
      row.appendChild(changeCell);

      els.tableBody.appendChild(row);
    }

    for (const header of document.querySelectorAll("#byh-table thead .byh-change-col")) {
      header.hidden = !showChange;
    }
    els.changeNote.hidden = !showChange;
  }

  // ---- Chart: one Yield history line, inline SVG, no charting library --------

  function niceTicks(lowest, highest) {
    if (!(highest > lowest)) return [lowest];
    const step = Math.pow(10, Math.floor(Math.log10((highest - lowest) / 4)));
    const residual = (highest - lowest) / 4 / step;
    const nice = (residual > 5 ? 10 : residual > 2 ? 5 : residual > 1 ? 2 : 1) * step;
    const ticks = [];
    for (let value = Math.ceil(lowest / nice) * nice; value <= highest; value += nice) {
      ticks.push(Math.round(value * 1e6) / 1e6);
    }
    return ticks.length ? ticks : [lowest, highest];
  }

  function dayNumber(isoDate) {
    const [year, month, day] = isoDate.split("-").map(Number);
    return Date.UTC(year, month - 1, day) / 86400000;
  }

  function chartPoints() {
    // Exactly the observations the table shows -- the same array, in the same
    // order, with each point positioned by its own observation date.
    return payload.observations.map((observation) => ({
      date: observation.date,
      day: dayNumber(observation.date),
      value: hasValue(observation) ? observation.yield_value : null,
      rawValue: observation.raw_value,
    }));
  }

  function renderChart() {
    els.chartWrap.textContent = "";
    const points = chartPoints();
    const valued = points.filter((point) => point.value !== null);
    if (!valued.length) {
      const empty = document.createElement("div");
      empty.className = "byh-chart-empty";
      empty.textContent = "Every observation Bloomberg returned over this range carried no value.";
      els.chartWrap.appendChild(empty);
      return;
    }

    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `${payload.yield_field} history for ${payload.security}`);
    const plotWidth = CHART_WIDTH - CHART_MARGIN.left - CHART_MARGIN.right;
    const plotHeight = CHART_HEIGHT - CHART_MARGIN.top - CHART_MARGIN.bottom;

    const xLowest = Math.min(...points.map((point) => point.day));
    const xHighest = Math.max(...points.map((point) => point.day));
    const values = valued.map((point) => point.value);
    let yLowest = Math.min(...values);
    let yHighest = Math.max(...values);
    const span = yHighest - yLowest;
    const pad = span === 0 ? Math.max(Math.abs(yLowest) * 0.05, 0.5) : span * 0.12;
    yLowest -= pad;
    yHighest += pad;

    // Time-scaled: a calendar gap between two observations shows as a wider
    // horizontal gap, never as two adjacent evenly-spaced points.
    const xFor = (day) =>
      CHART_MARGIN.left +
      (xHighest === xLowest ? plotWidth / 2 : ((day - xLowest) / (xHighest - xLowest)) * plotWidth);
    const yFor = (value) =>
      CHART_MARGIN.top + plotHeight - ((value - yLowest) / (yHighest - yLowest)) * plotHeight;

    for (const tick of niceTicks(yLowest, yHighest)) {
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
      label.setAttribute("class", "byh-y-tick");
      label.setAttribute("x", String(CHART_MARGIN.left - 8));
      label.setAttribute("y", String(y + 4));
      label.setAttribute("text-anchor", "end");
      label.setAttribute("font-size", "11");
      label.setAttribute("fill", "#99a1b0");
      // A round number the scale invented to divide the axis by, not a Yield
      // Bloomberg returned -- marked so nothing on screen reads as an
      // observation unless it is one. The exact returned value of every point
      // is on the point itself, and in the table.
      label.textContent = `~${Math.round(tick * 1e6) / 1e6}`;
      svg.appendChild(label);
    }

    // Every x tick label is a real observation date, thinned so they never
    // overlap -- the axis never invents a date of its own.
    let lastLabelX = -Infinity;
    for (const point of points) {
      const x = xFor(point.day);
      if (x - lastLabelX < MIN_X_TICK_GAP) continue;
      lastLabelX = x;
      const label = document.createElementNS(SVG_NS, "text");
      label.setAttribute("class", "byh-x-tick");
      label.setAttribute("x", String(x));
      label.setAttribute("y", String(CHART_HEIGHT - CHART_MARGIN.bottom + 16));
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("font-size", "11");
      label.setAttribute("fill", "#99a1b0");
      label.textContent = point.date;
      svg.appendChild(label);
    }

    // One polyline per unbroken run of valued observations. A returned row with
    // no value ends the run it interrupts, so no segment spans a hole.
    //
    // A run is NOT broken merely because two observations sit on non-adjacent
    // calendar dates, and that is deliberate (Codex review, PR #198). With
    // ACTIVE_DAYS_ONLY, Bloomberg omits every weekend and holiday, so breaking
    // on non-consecutive dates would break at every weekend and render a year
    // of daily yields as fifty-odd disconnected five-point stubs. Worse, the
    // rule needed to tell "market was closed" from "this bond has no history
    // here" is the security's own trading calendar, which this view does not
    // have and must not invent -- guessing one is exactly what Issue #196
    // forbids.
    //
    // The honest line is drawn instead: the dots are the data, the segments
    // are a reading aid between them, the x axis is scaled by real dates so a
    // long empty stretch reads as a long empty stretch, and the table states
    // exactly which dates came back. What the line genuinely must not cross is
    // a date Bloomberg *did* answer for and supplied no value on -- an
    // unresolved observation, the direct analogue of the vol surface's
    // unresolved node -- and it does not.
    let run = [];
    const flushRun = () => {
      if (run.length > 1) {
        const polyline = document.createElementNS(SVG_NS, "polyline");
        polyline.setAttribute("class", "byh-line");
        polyline.setAttribute("points", run.join(" "));
        polyline.setAttribute("fill", "none");
        polyline.setAttribute("stroke", "var(--chart-line)");
        polyline.setAttribute("stroke-width", "2");
        svg.appendChild(polyline);
      }
      run = [];
    };
    for (const point of points) {
      if (point.value === null) {
        flushRun();
        continue;
      }
      run.push(`${xFor(point.day)},${yFor(point.value)}`);
    }
    flushRun();

    for (const point of valued) {
      const dot = document.createElementNS(SVG_NS, "circle");
      dot.setAttribute("class", "byh-dot");
      dot.setAttribute("cx", String(xFor(point.day)));
      dot.setAttribute("cy", String(yFor(point.value)));
      dot.setAttribute("r", "2.6");
      const title = document.createElementNS(SVG_NS, "title");
      title.textContent = `${point.date} = ${point.rawValue}`;
      dot.appendChild(title);
      svg.appendChild(dot);
    }

    els.chartWrap.appendChild(svg);
  }

  // ---- Wiring ----------------------------------------------------------------

  els.loadBtn.addEventListener("click", () => loadHistory());
  els.showChange.addEventListener("change", () => {
    if (payload && payload.observations.length) renderTable();
  });
  for (const input of [els.identifier, els.yieldField, els.start, els.end]) {
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadHistory();
    });
  }

  // Selecting this market view deliberately wires up to nothing: there is no
  // field to request until the trader supplies one, so opening the tab sends
  // no request and this file needs no hook into the selector at all.

  // Test-only, read-only accessors. They drive exactly the code paths a
  // trader's pointer drives -- they compute no value of their own and change
  // no request or rendering decision (mirrors the __shioriTest* convention the
  // other Markets modules already use).
  window.__shioriTestYieldHistoryLoad = () => loadHistory();
  window.__shioriTestYieldHistoryPayload = () => payload;
  window.__shioriTestYieldHistoryRequestedRoutes = () => requestedRoutes.slice();
  window.__shioriTestYieldHistoryChartPoints = () => (payload ? chartPoints() : null);
  window.__shioriTestYieldHistoryDailyChanges = () =>
    payload ? dailyChanges(payload.observations) : null;
})();
