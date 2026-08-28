// Markets -> Swaption Vol Surface (Issue #194): a read-only view of one
// previously confirmed canonical VCUB ATM snapshot.
//
// What this file does: asks the bridge which confirmed ATM surfaces the local
// canonical store holds, fetches the one the trader picks, and draws it twice
// -- as the stored Expiry x Swap Tenor matrix, and as an interactive 3D
// surface over exactly those stored nodes.
//
// What this file never does, and must never start doing:
//
//   * capture, OCR, confirm, reject, or otherwise mutate anything. The only
//     two routes it calls are POST /api/vol-surface/atm/list and
//     POST /api/vol-surface/atm/surface, both read-only;
//   * price, discount, or call the VCUB normal-vol resolver;
//   * compute a volatility. Every number shown -- in a table cell, in a
//     tooltip, on an axis tick -- is a value the store handed back, printed
//     to every digit it holds and never rounded (see storedValueText). A
//     cell the capture left unresolved renders as an em dash, never as zero
//     and never as a neighbour's value;
//   * load anything over the network beyond those two same-origin routes.
//     The 3D surface is drawn on a plain 2D canvas with the projection below
//     -- no charting library, no CDN -- so the Markets page renders the same
//     with corporate internet blocked as it does with it open.
//
// The mesh drawn between adjacent stored nodes is display geometry: a shaded
// quad exists only as pixels, is never read back, never returned to the
// server, and is never stored. A quad is drawn only where all four of its
// corners carry a stored vol, so the mesh never spans a gap it would have to
// invent a node to cross.
(function () {
  "use strict";

  const panelCurve = document.getElementById("markets-panel-curve");
  const panelVol = document.getElementById("markets-panel-vol-surface");
  const tabCurve = document.getElementById("markets-tab-curve");
  const tabVol = document.getElementById("markets-tab-vol-surface");
  // Not present on this page (or an older markup revision): do nothing at all
  // rather than half-wire a view.
  if (!panelCurve || !panelVol || !tabCurve || !tabVol) return;

  const els = {
    meta: document.getElementById("vol-surface-meta"),
    select: document.getElementById("vol-surface-select"),
    refreshBtn: document.getElementById("vol-surface-refresh-btn"),
    provenance: document.getElementById("vol-surface-provenance"),
    currency: document.getElementById("vol-surface-currency"),
    curveConfig: document.getElementById("vol-surface-curve-config"),
    side: document.getElementById("vol-surface-side"),
    businessDate: document.getElementById("vol-surface-business-date"),
    volType: document.getElementById("vol-surface-vol-type"),
    source: document.getElementById("vol-surface-source"),
    unit: document.getElementById("vol-surface-unit"),
    capturedAt: document.getElementById("vol-surface-captured-at"),
    confirmedBy: document.getElementById("vol-surface-confirmed-by"),
    pointCount: document.getElementById("vol-surface-point-count"),
    surfaceId: document.getElementById("vol-surface-id"),
    loading: document.getElementById("vol-surface-loading"),
    empty: document.getElementById("vol-surface-empty"),
    error: document.getElementById("vol-surface-error"),
    errorDetail: document.getElementById("vol-surface-error-detail"),
    chartCard: document.getElementById("vol-surface-chart-card"),
    canvas: document.getElementById("vol-surface-canvas"),
    tooltip: document.getElementById("vol-surface-tooltip"),
    axisUnit: document.getElementById("vol-surface-axis-unit"),
    tableCard: document.getElementById("vol-surface-table-card"),
    tableHead: document.getElementById("vol-surface-table-head"),
    tableBody: document.getElementById("vol-surface-table-body"),
  };

  const DASH = "—";
  const TIMES = "×";
  const LIST_ROUTE = "/api/vol-surface/atm/list";
  const SURFACE_ROUTE = "/api/vol-surface/atm/surface";
  const PLACEHOLDER_VALUE = "";
  const NOTHING_STORED_TEXT =
    "No confirmed ATM surface is stored yet. Confirm a VCUB ATM Swaptions capture in the " +
    "Capture view and it will appear here.";
  const CHOOSE_SNAPSHOT_TEXT =
    "Several confirmed ATM snapshots are stored. Choose one above -- Shiori does not pick a " +
    "snapshot for you.";

  let summaries = [];
  let selectedSurfaceId = null;
  let payload = null;
  let listLoaded = false;
  let inFlight = false;
  const requestedRoutes = [];

  // ---- Rendering the stored values -----------------------------------------

  // The stored double itself. `String()` on a JSON-decoded number is the
  // shortest decimal that round-trips it -- every digit the stored value
  // holds and no digit it does not -- so a cell shows the confirmed value
  // exactly, never a toFixed() rounding of it (Issue #194: "Preserve the
  // stored values"). It is then zero-padded to two decimals so a column of
  // vols lines up: padding appends zeros and can neither add precision nor
  // drop a digit, so 82.4 reads "82.40" while 73.125 keeps all three of its
  // own. A value in exponential form is left exactly as it came.
  function storedValueText(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) return null;
    const exact = String(value);
    if (!/^-?\d+(\.\d+)?$/.test(exact)) return exact;
    const dot = exact.indexOf(".");
    const decimals = dot === -1 ? 0 : exact.length - dot - 1;
    return decimals >= 2 ? exact : `${dot === -1 ? exact + "." : exact}${"0".repeat(2 - decimals)}`;
  }

  function volText(value) {
    const text = storedValueText(value);
    if (text === null) return DASH;
    const unit = payload && payload.volatility_unit;
    return unit ? `${text} ${unit}` : text;
  }

  // An identity field the capture left unresolved is shown as unresolved --
  // never blank, and never stood in for by a plausible-looking default.
  function setProvenanceField(el, value) {
    if (typeof value === "string" && value) {
      el.textContent = value;
      el.classList.remove("unresolved");
    } else {
      el.textContent = "unresolved";
      el.classList.add("unresolved");
    }
  }

  // ---- The market-view selector --------------------------------------------

  let activeMarketView = "curve";

  function selectMarketView(view) {
    activeMarketView = view === "vol-surface" ? "vol-surface" : "curve";
    const showVol = activeMarketView === "vol-surface";
    panelVol.hidden = !showVol;
    panelCurve.hidden = showVol;
    tabVol.classList.toggle("is-active", showVol);
    tabCurve.classList.toggle("is-active", !showVol);
    tabVol.setAttribute("aria-selected", String(showVol));
    tabCurve.setAttribute("aria-selected", String(!showVol));
    if (showVol && !listLoaded && !inFlight) loadSurfaceList();
  }

  tabCurve.addEventListener("click", () => selectMarketView("curve"));
  tabVol.addEventListener("click", () => selectMarketView("vol-surface"));

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

  function showOnly(section, emptyText) {
    els.loading.hidden = section !== "loading";
    els.empty.hidden = section !== "empty";
    if (section === "empty" && emptyText) els.empty.textContent = emptyText;
    els.error.hidden = section !== "error";
    const showSurface = section === "surface";
    els.chartCard.hidden = !showSurface;
    els.tableCard.hidden = !showSurface;
    els.provenance.hidden = !showSurface;
  }

  function fail(message) {
    payload = null;
    showOnly("error");
    els.errorDetail.textContent = message;
  }

  function snapshotLabel(summary) {
    // Every field that distinguishes one stored snapshot from another,
    // capture id included -- two captures of the same screen on the same
    // business date differ only there, and the trader must be able to tell
    // them apart from the option itself (Issue #194: no silent choice of an
    // arbitrary duplicate).
    return [
      summary.business_date || "date unresolved",
      summary.currency || "currency unresolved",
      summary.curve_config || "curve unresolved",
      summary.side || "side unresolved",
      summary.vol_type || "type unresolved",
      `${summary.point_count} pts`,
      `confirmed ${summary.confirmed_at} by ${summary.confirmed_by}`,
      `capture ${summary.capture_id}`,
    ].join(" · ");
  }

  function renderSnapshotOptions() {
    els.select.textContent = "";
    els.select.disabled = summaries.length === 0;
    if (summaries.length > 1) {
      const placeholder = document.createElement("option");
      placeholder.value = PLACEHOLDER_VALUE;
      placeholder.textContent = `Choose one of ${summaries.length} confirmed ATM snapshots…`;
      els.select.appendChild(placeholder);
    }
    for (const summary of summaries) {
      const option = document.createElement("option");
      option.value = summary.surface_id;
      option.textContent = snapshotLabel(summary);
      els.select.appendChild(option);
    }
    els.select.value = selectedSurfaceId || PLACEHOLDER_VALUE;
  }

  async function loadSurfaceList() {
    if (inFlight) return;
    inFlight = true;
    els.refreshBtn.classList.add("is-disabled");
    showOnly("loading");
    try {
      const listed = await postJson(LIST_ROUTE, {});
      if (!listed || !Array.isArray(listed.surfaces)) {
        throw new Error('malformed response: "surfaces" must be an array');
      }
      summaries = listed.surfaces;
      listLoaded = true;
      // One stored snapshot is not a choice, so it is shown. More than one is
      // a choice, and it is the trader's: nothing is selected until they make
      // it (Issue #194).
      const keepSelection =
        selectedSurfaceId && summaries.some((s) => s.surface_id === selectedSurfaceId);
      if (!keepSelection) {
        selectedSurfaceId = summaries.length === 1 ? summaries[0].surface_id : null;
      }
      renderSnapshotOptions();
      if (summaries.length === 0) {
        payload = null;
        els.meta.textContent = "No confirmed ATM surface stored";
        showOnly("empty", NOTHING_STORED_TEXT);
      } else if (selectedSurfaceId) {
        await loadSelectedSurface();
      } else {
        payload = null;
        els.meta.textContent = `${summaries.length} confirmed ATM snapshots stored`;
        showOnly("empty", CHOOSE_SNAPSHOT_TEXT);
      }
    } catch (error) {
      summaries = [];
      renderSnapshotOptions();
      fail(error.message);
    } finally {
      inFlight = false;
      els.refreshBtn.classList.remove("is-disabled");
    }
  }

  async function loadSelectedSurface() {
    if (!selectedSurfaceId) return;
    const requestedId = selectedSurfaceId;
    showOnly("loading");
    try {
      const fetched = await postJson(SURFACE_ROUTE, { surface_id: requestedId });
      validateSurfacePayload(fetched, requestedId);
      // A slower earlier request must never overwrite a later selection.
      if (selectedSurfaceId !== requestedId) return;
      payload = fetched;
      renderSurface();
      showOnly("surface");
    } catch (error) {
      if (selectedSurfaceId !== requestedId) return;
      fail(error.message);
    }
  }

  // Fails closed on anything the route's contract does not promise: a
  // half-rendered matrix would look like a surface someone confirmed.
  function validateSurfacePayload(candidate, requestedId) {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
      throw new Error("malformed response: expected a JSON object");
    }
    for (const key of ["surface_id", "identity", "provenance", "point_count", "grid"]) {
      if (!(key in candidate)) throw new Error(`malformed response: missing "${key}"`);
    }
    if (candidate.surface_id !== requestedId) {
      throw new Error(
        `malformed response: asked for surface ${requestedId} and got ${candidate.surface_id}`
      );
    }
    const grid = candidate.grid;
    if (!grid || typeof grid !== "object") {
      throw new Error('malformed response: "grid" must be an object');
    }
    const { expiries, underlying_tenors: tenors, rows } = grid;
    if (!Array.isArray(expiries) || expiries.length === 0) {
      throw new Error('malformed response: "grid.expiries" must be a non-empty array');
    }
    if (!Array.isArray(tenors) || tenors.length === 0) {
      throw new Error('malformed response: "grid.underlying_tenors" must be a non-empty array');
    }
    if (!Array.isArray(rows) || rows.length !== expiries.length) {
      throw new Error(
        `malformed response: "grid.rows" must hold one row per expiry (${expiries.length})`
      );
    }
    rows.forEach((row, index) => {
      if (!Array.isArray(row) || row.length !== tenors.length) {
        throw new Error(
          `malformed response: row ${expiries[index]} must hold one cell per swap tenor ` +
            `(${tenors.length})`
        );
      }
      row.forEach((cell, column) => {
        if (cell !== null && (typeof cell !== "number" || !Number.isFinite(cell))) {
          throw new Error(
            `malformed response: ${expiries[index]} ${TIMES} ${tenors[column]} is neither a ` +
              "number nor an unresolved cell"
          );
        }
      });
    });
    return candidate;
  }

  els.select.addEventListener("change", () => {
    const chosen = els.select.value;
    selectedSurfaceId = chosen === PLACEHOLDER_VALUE ? null : chosen;
    if (!selectedSurfaceId) {
      payload = null;
      showOnly("empty", CHOOSE_SNAPSHOT_TEXT);
      return;
    }
    loadSelectedSurface();
  });
  els.refreshBtn.addEventListener("click", () => loadSurfaceList());

  // ---- Rendering one surface -------------------------------------------------

  function renderSurface() {
    const identity = payload.identity || {};
    const provenance = payload.provenance || {};
    els.meta.textContent = [
      identity.currency || "currency unresolved",
      identity.curve_config || "curve unresolved",
      identity.vol_type || "type unresolved",
      identity.business_date || "date unresolved",
      `${payload.point_count} points`,
    ].join(" · ");

    setProvenanceField(els.currency, identity.currency);
    setProvenanceField(els.curveConfig, identity.curve_config);
    setProvenanceField(els.side, identity.side);
    setProvenanceField(els.businessDate, identity.business_date);
    setProvenanceField(els.volType, identity.vol_type);
    setProvenanceField(els.source, identity.source);
    setProvenanceField(els.unit, payload.volatility_unit);
    setProvenanceField(els.capturedAt, provenance.captured_at);
    setProvenanceField(els.confirmedBy, provenance.confirmed_by);
    els.pointCount.textContent = String(payload.point_count);
    els.pointCount.classList.remove("unresolved");
    setProvenanceField(els.surfaceId, payload.surface_id);
    els.axisUnit.textContent = payload.volatility_unit ? ` (${payload.volatility_unit})` : "";

    renderTable();
    buildSurfaceNodes();
    drawSurface();
  }

  function renderTable() {
    const { expiries, underlying_tenors: tenors, rows } = payload.grid;
    els.tableHead.textContent = "";
    const corner = document.createElement("th");
    corner.textContent = "Expiry \\ Tenor";
    els.tableHead.appendChild(corner);
    for (const tenor of tenors) {
      const th = document.createElement("th");
      th.className = "num";
      th.textContent = tenor;
      els.tableHead.appendChild(th);
    }

    els.tableBody.textContent = "";
    expiries.forEach((expiry, rowIndex) => {
      const tr = document.createElement("tr");
      const rowHeader = document.createElement("th");
      rowHeader.scope = "row";
      rowHeader.textContent = expiry;
      tr.appendChild(rowHeader);
      rows[rowIndex].forEach((value) => {
        const td = document.createElement("td");
        const text = storedValueText(value);
        if (text === null) {
          td.textContent = DASH;
          td.classList.add("unresolved");
        } else {
          td.textContent = text;
        }
        tr.appendChild(td);
      });
      els.tableBody.appendChild(tr);
    });
  }

  // ---- The 3D surface --------------------------------------------------------
  //
  // Orthographic projection of the stored nodes, painted back to front. The
  // model box is x in [-1, 1] (swap tenor), y in [-1, 1] (option expiry) and
  // z in [-Z_HALF, Z_HALF] (normal vol, linearly scaled between the lowest
  // and highest *stored* vols). The z scaling is a drawing decision only: the
  // number reported for any node is always the stored one.

  const Z_HALF = 0.62;
  // The widest the tenor/expiry plane can project to, at any yaw: |x cos a -
  // y sin a| <= sqrt(2) over the unit square. It does not depend on the yaw,
  // so a horizontal fit derived from it holds the surface at a steady size
  // while the trader spins it -- the gesture that would be worst to have the
  // picture breathe under.
  const MODEL_HALF_WIDTH = Math.SQRT2;
  // How far up the screen the box reaches at the current elevation. Unlike
  // the width this genuinely depends on the tilt, and using the tilt rather
  // than its worst case is what keeps the surface filling the frame instead
  // of sitting small in the middle of it.
  function modelHalfHeight() {
    return MODEL_HALF_WIDTH * Math.sin(camera.pitch) + Z_HALF * Math.cos(camera.pitch);
  }
  // Axis names sit just beyond the tick labels; the margins are what they sit
  // in, so they have to clear the fitted plot area.
  const AXIS_LABEL_OFFSET = 1.62;
  const TICK_LABEL_OFFSET = 1.16;
  const PLOT_MARGIN = { top: 30, right: 116, bottom: 62, left: 96 };
  const PICK_RADIUS = 14;
  const DEFAULT_CAMERA = { yaw: -0.62, pitch: 0.48, zoom: 1 };

  const camera = { ...DEFAULT_CAMERA };
  let modelNodes = [];
  let volMin = 0;
  let volMax = 0;
  let projectedNodes = [];
  let hoveredNode = null;

  function buildSurfaceNodes() {
    const { expiries, underlying_tenors: tenors, rows } = payload.grid;
    const resolved = [];
    modelNodes = [];
    rows.forEach((row, i) => {
      row.forEach((value, j) => {
        if (typeof value !== "number" || !Number.isFinite(value)) return;
        resolved.push(value);
        modelNodes.push({
          i,
          j,
          expiry: expiries[i],
          tenor: tenors[j],
          volatility: value,
          x: tenors.length === 1 ? 0 : (j / (tenors.length - 1)) * 2 - 1,
          y: expiries.length === 1 ? 0 : (i / (expiries.length - 1)) * 2 - 1,
        });
      });
    });
    volMin = resolved.length ? Math.min(...resolved) : 0;
    volMax = resolved.length ? Math.max(...resolved) : 0;
    for (const node of modelNodes) node.z = modelZ(node.volatility);
  }

  function modelZ(value) {
    if (volMax === volMin) return 0;
    return ((value - volMin) / (volMax - volMin)) * (2 * Z_HALF) - Z_HALF;
  }

  function rotated(x, y, z) {
    const cy = Math.cos(camera.yaw);
    const sy = Math.sin(camera.yaw);
    const xr = x * cy - y * sy;
    const yr = x * sy + y * cy;
    const ce = Math.cos(camera.pitch);
    const se = Math.sin(camera.pitch);
    // `up` is the screen-up component, `depth` the distance into the screen:
    // the camera sits at elevation `pitch` above the tenor/expiry plane.
    return { across: xr, up: yr * se + z * ce, depth: yr * ce - z * se };
  }

  function projector() {
    const width = els.canvas.width;
    const height = els.canvas.height;
    const plotWidth = width - PLOT_MARGIN.left - PLOT_MARGIN.right;
    const plotHeight = height - PLOT_MARGIN.top - PLOT_MARGIN.bottom;
    const scale =
      Math.min(plotWidth / (2 * MODEL_HALF_WIDTH), plotHeight / (2 * modelHalfHeight())) *
      0.98 *
      camera.zoom;
    const centreX = PLOT_MARGIN.left + plotWidth / 2;
    const centreY = PLOT_MARGIN.top + plotHeight / 2;
    return (x, y, z) => {
      const r = rotated(x, y, z);
      return { sx: centreX + r.across * scale, sy: centreY - r.up * scale, depth: r.depth };
    };
  }

  // Blue -> teal -> amber -> red by height, so relative highs and lows read
  // at a glance. Purely presentational.
  const RAMP = [
    [47, 111, 208],
    [63, 182, 200],
    [242, 193, 78],
    [213, 80, 74],
  ];

  function rampColor(t, alpha) {
    const clamped = Math.min(1, Math.max(0, t));
    const span = clamped * (RAMP.length - 1);
    const index = Math.min(RAMP.length - 2, Math.floor(span));
    const f = span - index;
    const lo = RAMP[index];
    const hi = RAMP[index + 1];
    const channel = (k) => Math.round(lo[k] + (hi[k] - lo[k]) * f);
    return `rgba(${channel(0)}, ${channel(1)}, ${channel(2)}, ${alpha})`;
  }

  function normalizedHeight(value) {
    return volMax === volMin ? 0.5 : (value - volMin) / (volMax - volMin);
  }

  function drawSurface() {
    const ctx = els.canvas.getContext("2d");
    if (!ctx || !payload) return;
    const { expiries, underlying_tenors: tenors } = payload.grid;
    const project = projector();

    ctx.clearRect(0, 0, els.canvas.width, els.canvas.height);

    projectedNodes = modelNodes.map((node) => {
      const p = project(node.x, node.y, node.z);
      return { node, sx: p.sx, sy: p.sy, depth: p.depth };
    });

    drawFloorGrid(ctx, project, expiries, tenors);

    // Quads, farthest first. A quad is drawn only where all four corners
    // carry a stored vol: the mesh never bridges a cell the capture could not
    // read, because doing so would require inventing the missing node.
    const quads = [];
    const lookup = new Map();
    for (const node of modelNodes) lookup.set(`${node.i}|${node.j}`, node);
    const cornerOf = (i, j) => lookup.get(`${i}|${j}`) || null;
    for (let i = 0; i + 1 < expiries.length; i += 1) {
      for (let j = 0; j + 1 < tenors.length; j += 1) {
        const corners = [
          cornerOf(i, j),
          cornerOf(i, j + 1),
          cornerOf(i + 1, j + 1),
          cornerOf(i + 1, j),
        ];
        if (corners.some((corner) => corner === null)) continue;
        const projectedCorners = corners.map((corner) => project(corner.x, corner.y, corner.z));
        const depth =
          projectedCorners.reduce((total, corner) => total + corner.depth, 0) /
          projectedCorners.length;
        const height =
          corners.reduce((total, corner) => total + normalizedHeight(corner.volatility), 0) /
          corners.length;
        quads.push({ projectedCorners, depth, height });
      }
    }
    // Quads and node dots painted together, farthest first, so a node on the
    // far side of a fold is hidden by the near side rather than showing
    // through it. Each dot is biased a hair towards the viewer so it still
    // wins against the four quads it is a corner of -- they share its depth,
    // and without the bias a node would vanish under its own mesh.
    const DOT_DEPTH_BIAS = 0.03;
    const painted = quads.map((quad) => ({ quad, depth: quad.depth }));
    for (const projectedNode of projectedNodes) {
      painted.push({ dot: projectedNode, depth: projectedNode.depth - DOT_DEPTH_BIAS });
    }
    painted.sort((a, b) => b.depth - a.depth);
    for (const item of painted) {
      if (item.quad) {
        ctx.beginPath();
        item.quad.projectedCorners.forEach((corner, index) => {
          if (index === 0) ctx.moveTo(corner.sx, corner.sy);
          else ctx.lineTo(corner.sx, corner.sy);
        });
        ctx.closePath();
        ctx.fillStyle = rampColor(item.quad.height, 0.9);
        ctx.fill();
        ctx.strokeStyle = "rgba(255, 255, 255, 0.45)";
        ctx.lineWidth = 0.6;
        ctx.stroke();
        continue;
      }
      const isHovered = hoveredNode && hoveredNode.node === item.dot.node;
      ctx.beginPath();
      ctx.arc(item.dot.sx, item.dot.sy, isHovered ? 5 : 1.9, 0, Math.PI * 2);
      ctx.fillStyle = isHovered ? "#1f2a44" : "rgba(31, 42, 68, 0.5)";
      ctx.fill();
      if (isHovered) {
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 1.6;
        ctx.stroke();
      }
    }

    // Last, so a fold of the surface can never hide the labels that say what
    // is being looked at.
    drawFloorLabels(ctx, project, expiries, tenors);
    drawVolAxis(ctx, project);
  }

  const floorX = (j, tenors) => (tenors.length === 1 ? 0 : (j / (tenors.length - 1)) * 2 - 1);
  const floorY = (i, expiries) =>
    expiries.length === 1 ? 0 : (i / (expiries.length - 1)) * 2 - 1;

  // The reference grid the surface floats above, at the bottom of the vol
  // axis. Drawn before the mesh, since it belongs behind it.
  function drawFloorGrid(ctx, project, expiries, tenors) {
    const z = -Z_HALF;
    ctx.strokeStyle = "#dfe4ec";
    ctx.lineWidth = 1;
    const xAt = (j) => floorX(j, tenors);
    const yAt = (i) => floorY(i, expiries);

    for (let i = 0; i < expiries.length; i += 1) {
      const a = project(-1, yAt(i), z);
      const b = project(1, yAt(i), z);
      ctx.beginPath();
      ctx.moveTo(a.sx, a.sy);
      ctx.lineTo(b.sx, b.sy);
      ctx.stroke();
    }
    for (let j = 0; j < tenors.length; j += 1) {
      const a = project(xAt(j), -1, z);
      const b = project(xAt(j), 1, z);
      ctx.beginPath();
      ctx.moveTo(a.sx, a.sy);
      ctx.lineTo(b.sx, b.sy);
      ctx.stroke();
    }
  }

  // The tenor and expiry tick labels. They go on whichever edge currently
  // faces the trader, and are thinned so they never overlap when an axis is
  // rotated nearly edge-on.
  function drawFloorLabels(ctx, project, expiries, tenors) {
    const z = -Z_HALF;
    const xAt = (j) => floorX(j, tenors);
    const yAt = (i) => floorY(i, expiries);
    ctx.font = "11px system-ui, -apple-system, Segoe UI, sans-serif";
    ctx.fillStyle = "#6b7385";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    const tenorEdge = project(0, -1, z).depth <= project(0, 1, z).depth ? -1 : 1;
    let lastLabel = null;
    for (let j = 0; j < tenors.length; j += 1) {
      const inward = project(xAt(j), tenorEdge * 0.9, z);
      const outward = project(xAt(j), tenorEdge * TICK_LABEL_OFFSET, z);
      if (lastLabel && Math.hypot(outward.sx - lastLabel.sx, outward.sy - lastLabel.sy) < 24) {
        continue;
      }
      lastLabel = outward;
      ctx.strokeStyle = "#c9d0dd";
      ctx.beginPath();
      ctx.moveTo(inward.sx, inward.sy);
      ctx.lineTo(outward.sx, outward.sy);
      ctx.stroke();
      ctx.fillText(tenors[j], outward.sx, outward.sy);
    }

    const expiryEdge = project(-1, 0, z).depth <= project(1, 0, z).depth ? -1 : 1;
    lastLabel = null;
    for (let i = 0; i < expiries.length; i += 1) {
      const inward = project(expiryEdge * 0.9, yAt(i), z);
      const outward = project(expiryEdge * TICK_LABEL_OFFSET, yAt(i), z);
      if (lastLabel && Math.hypot(outward.sx - lastLabel.sx, outward.sy - lastLabel.sy) < 20) {
        continue;
      }
      lastLabel = outward;
      ctx.strokeStyle = "#c9d0dd";
      ctx.beginPath();
      ctx.moveTo(inward.sx, inward.sy);
      ctx.lineTo(outward.sx, outward.sy);
      ctx.stroke();
      ctx.fillText(expiries[i], outward.sx, outward.sy);
    }

    ctx.font = "12px system-ui, -apple-system, Segoe UI, sans-serif";
    ctx.fillStyle = "#99a1b0";
    const tenorAxisLabel = project(0, tenorEdge * AXIS_LABEL_OFFSET, z);
    ctx.fillText("Swap Tenor", tenorAxisLabel.sx, tenorAxisLabel.sy);
    const expiryAxisLabel = project(expiryEdge * AXIS_LABEL_OFFSET, 0, z);
    ctx.fillText("Option Expiry", expiryAxisLabel.sx, expiryAxisLabel.sy);
  }

  // A label that has to stay readable wherever it lands -- the vol axis is
  // painted after the mesh, so its ticks can fall on a coloured quad. The
  // plate is a translucent wash of the canvas background behind the text,
  // nothing more.
  function plateText(ctx, text, x, y) {
    const metrics = ctx.measureText(text);
    const paddingX = 3;
    const height = 14;
    const align = ctx.textAlign;
    const left =
      align === "right" ? x - metrics.width : align === "center" ? x - metrics.width / 2 : x;
    const fill = ctx.fillStyle;
    ctx.fillStyle = "rgba(251, 252, 254, 0.88)";
    ctx.fillRect(left - paddingX, y - height / 2, metrics.width + paddingX * 2, height);
    ctx.fillStyle = fill;
    ctx.fillText(text, x, y);
  }

  // The vertical vol axis, at whichever floor corner is farthest away so it
  // stands behind the surface rather than cutting through it. Its ticks are
  // the stored vol range, rendered exactly.
  function drawVolAxis(ctx, project) {
    if (!modelNodes.length) return;
    const corners = [
      [-1, -1],
      [-1, 1],
      [1, -1],
      [1, 1],
    ];
    let corner = corners[0];
    let farthest = -Infinity;
    for (const candidate of corners) {
      const depth = project(candidate[0], candidate[1], 0).depth;
      if (depth > farthest) {
        farthest = depth;
        corner = candidate;
      }
    }
    const bottom = project(corner[0], corner[1], -Z_HALF);
    const top = project(corner[0], corner[1], Z_HALF);
    ctx.strokeStyle = "#c9d0dd";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(bottom.sx, bottom.sy);
    ctx.lineTo(top.sx, top.sy);
    ctx.stroke();

    ctx.font = "11px system-ui, -apple-system, Segoe UI, sans-serif";
    ctx.fillStyle = "#6b7385";
    ctx.textAlign = corner[0] < 0 ? "right" : "left";
    ctx.textBaseline = "middle";
    const ticks = volMax === volMin ? [volMin] : [volMin, (volMin + volMax) / 2, volMax];
    for (const tick of ticks) {
      const point = project(corner[0], corner[1], modelZ(tick));
      const offset = corner[0] < 0 ? -8 : 8;
      ctx.beginPath();
      ctx.moveTo(point.sx, point.sy);
      ctx.lineTo(point.sx + offset, point.sy);
      ctx.stroke();
      // The two ends are stored vols and are printed exactly; the midpoint is
      // an axis position, so it is labelled as the rounded scale mark it is.
      const isStored = tick === volMin || tick === volMax;
      plateText(
        ctx,
        isStored ? storedValueText(tick) : `~${tick.toFixed(1)}`,
        point.sx + offset * 1.6,
        point.sy
      );
    }
    const unit = payload && payload.volatility_unit;
    ctx.font = "12px system-ui, -apple-system, Segoe UI, sans-serif";
    ctx.fillStyle = "#99a1b0";
    ctx.textAlign = "center";
    plateText(ctx, unit ? `Normal Vol (${unit})` : "Normal Vol", top.sx, top.sy - 16);
  }

  // ---- Rotate, zoom, pick ----------------------------------------------------

  function canvasPointFromEvent(event) {
    const rect = els.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return { x: 0, y: 0 };
    return {
      x: ((event.clientX - rect.left) / rect.width) * els.canvas.width,
      y: ((event.clientY - rect.top) / rect.height) * els.canvas.height,
    };
  }

  function nodeAt(x, y) {
    let best = null;
    let bestDistance = PICK_RADIUS * PICK_RADIUS;
    for (const projectedNode of projectedNodes) {
      const dx = projectedNode.sx - x;
      const dy = projectedNode.sy - y;
      const distance = dx * dx + dy * dy;
      if (distance <= bestDistance) {
        bestDistance = distance;
        best = projectedNode;
      }
    }
    return best;
  }

  function tooltipRow(key, value) {
    const row = document.createElement("div");
    row.className = "tt-row";
    const k = document.createElement("span");
    k.className = "tt-k";
    k.textContent = key;
    const v = document.createElement("span");
    v.textContent = value;
    row.appendChild(k);
    row.appendChild(v);
    return row;
  }

  function showTooltipFor(projectedNode) {
    const node = projectedNode.node;
    els.tooltip.textContent = "";
    const headline = document.createElement("div");
    headline.className = "tt-headline";
    headline.textContent = `${node.expiry} ${TIMES} ${node.tenor} = ${volText(node.volatility)}`;
    els.tooltip.appendChild(headline);
    els.tooltip.appendChild(tooltipRow("Option Expiry:", node.expiry));
    els.tooltip.appendChild(tooltipRow("Swap Tenor:", node.tenor));
    els.tooltip.appendChild(tooltipRow("Normal Vol:", volText(node.volatility)));
    const leftPercent = (projectedNode.sx / els.canvas.width) * 100;
    const topPercent = (projectedNode.sy / els.canvas.height) * 100;
    els.tooltip.style.left = `${leftPercent}%`;
    els.tooltip.style.top = `${topPercent}%`;
    els.tooltip.style.transform =
      leftPercent > 70 ? "translate(-100%, -115%)" : "translate(-10%, -115%)";
    els.tooltip.hidden = false;
  }

  function hoverAt(x, y) {
    const found = nodeAt(x, y);
    const changed = found !== hoveredNode;
    hoveredNode = found;
    if (found) showTooltipFor(found);
    else els.tooltip.hidden = true;
    if (changed) drawSurface();
    return found;
  }

  let dragOrigin = null;

  els.canvas.addEventListener("mousedown", (event) => {
    dragOrigin = { x: event.clientX, y: event.clientY };
    els.canvas.classList.add("is-dragging");
    els.tooltip.hidden = true;
    hoveredNode = null;
  });

  window.addEventListener("mouseup", () => {
    dragOrigin = null;
    els.canvas.classList.remove("is-dragging");
  });

  els.canvas.addEventListener("mousemove", (event) => {
    if (!payload) return;
    if (dragOrigin) {
      rotateBy(event.clientX - dragOrigin.x, event.clientY - dragOrigin.y);
      dragOrigin = { x: event.clientX, y: event.clientY };
      return;
    }
    const point = canvasPointFromEvent(event);
    hoverAt(point.x, point.y);
  });

  els.canvas.addEventListener("mouseleave", () => {
    hoveredNode = null;
    els.tooltip.hidden = true;
    if (payload) drawSurface();
  });

  els.canvas.addEventListener(
    "wheel",
    (event) => {
      if (!payload) return;
      event.preventDefault();
      zoomBy(event.deltaY);
    },
    { passive: false }
  );

  // Back to the starting view, without a control of its own to explain.
  els.canvas.addEventListener("dblclick", () => {
    if (!payload) return;
    Object.assign(camera, DEFAULT_CAMERA);
    drawSurface();
  });

  function rotateBy(dx, dy) {
    camera.yaw += dx * 0.008;
    camera.pitch = Math.min(1.45, Math.max(0.06, camera.pitch + dy * 0.006));
    drawSurface();
  }

  function zoomBy(deltaY) {
    camera.zoom = Math.min(4, Math.max(0.4, camera.zoom * Math.exp(-deltaY * 0.0015)));
    drawSurface();
  }

  // Test-only, read-only accessors. They drive exactly the code paths a
  // trader's pointer drives -- they compute no value of their own, and
  // change no stored value, request, or rendering decision (mirrors the
  // __shioriTest* convention the Markets curve module already uses).
  window.__shioriTestMarketsSelectMarketView = (view) => selectMarketView(view);
  window.__shioriTestMarketsActiveMarketView = () => activeMarketView;
  window.__shioriTestVolSurfaceLoadList = () => loadSurfaceList();
  window.__shioriTestVolSurfaceSummaries = () => summaries;
  window.__shioriTestVolSurfacePayload = () => payload;
  window.__shioriTestVolSurfaceCamera = () => ({ ...camera });
  window.__shioriTestVolSurfaceRotateBy = (dx, dy) => rotateBy(dx, dy);
  window.__shioriTestVolSurfaceZoomBy = (deltaY) => zoomBy(deltaY);
  window.__shioriTestVolSurfaceProjectedNodes = () =>
    projectedNodes.map((projectedNode) => ({
      expiry: projectedNode.node.expiry,
      tenor: projectedNode.node.tenor,
      volatility: projectedNode.node.volatility,
      sx: projectedNode.sx,
      sy: projectedNode.sy,
    }));
  window.__shioriTestVolSurfaceHoverAt = (x, y) => {
    const found = hoverAt(x, y);
    return found
      ? { expiry: found.node.expiry, tenor: found.node.tenor, volatility: found.node.volatility }
      : null;
  };
  window.__shioriTestVolSurfaceRequestedRoutes = () => requestedRoutes.slice();
})();
