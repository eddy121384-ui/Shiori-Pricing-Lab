// OTM/SABR capture view (Issue #185) -- Bloomberg VCUB OTM Swaptions / SABR
// visual capture from several overlapping screenshots.
//
// A fourth self-contained IIFE, sharing no state with the Pricing, Markets, or
// ATM Capture modules and touching nothing any of them owns. It reads the
// operator's chosen image files locally, posts their bytes to this workbench's
// own loopback bridge in ONE request (POST /api/vcub/otm/parse), and renders
// the merged table the server sends back, verbatim.
//
// This file computes nothing. It does not parse an image, place a cell, merge
// two screenshots, order a row, resolve an overlap, fill a hole, round a
// number, or decide whether a capture may be confirmed -- every one of those
// is the server's answer, rendered as received. `can_confirm` in particular is
// read, never derived here: the Confirm button's enabled state mirrors the
// server's judgement, and the server refuses a blocked confirmation again on
// its own side regardless of what this page does.
//
// The one thing this page *does* own is the file list: which images go into a
// capture session, and removing one picked by mistake before Parse. Once Parse
// is pressed that list is a request body and nothing more.
//
// Nothing here prices anything, touches the trader's ticket, or feeds a
// captured number into any market-data input.
(function () {
  "use strict";

  const navOtm = document.getElementById("nav-capture-otm");
  const viewOtm = document.getElementById("view-capture-otm");
  if (!navOtm || !viewOtm) return; // OTM capture view not present on this page

  const els = {
    fileInput: document.getElementById("otm-file-input"),
    dropzone: document.getElementById("otm-dropzone"),
    fileList: document.getElementById("otm-file-list"),
    parseBtn: document.getElementById("otm-parse-btn"),
    chooseBtn: document.querySelector('label[for="otm-file-input"]'),
    loading: document.getElementById("otm-loading"),
    errorSection: document.getElementById("otm-error"),
    errorTitle: document.getElementById("otm-error-title"),
    errorDetail: document.getElementById("otm-error-detail"),
    reviewCard: document.getElementById("otm-review-card"),
    statusPill: document.getElementById("otm-status-pill"),
    blockers: document.getElementById("otm-blockers"),
    blockerList: document.getElementById("otm-blocker-list"),
    blockersTitle: document.getElementById("otm-blockers-title"),
    warnings: document.getElementById("otm-warnings"),
    warningList: document.getElementById("otm-warning-list"),
    warningsTitle: document.getElementById("otm-warnings-title"),
    metaGrid: document.getElementById("otm-meta-grid"),
    coverage: document.getElementById("otm-coverage"),
    completeness: document.getElementById("otm-completeness"),
    completenessTitle: document.getElementById("otm-completeness-title"),
    completenessDetail: document.getElementById("otm-completeness-detail"),
    reviewedBy: document.getElementById("otm-reviewed-by"),
    confirmBtn: document.getElementById("otm-confirm-btn"),
    rejectBtn: document.getElementById("otm-reject-btn"),
    reviewStatus: document.getElementById("otm-review-status"),
    storage: document.getElementById("otm-storage"),
    storageTitle: document.getElementById("otm-storage-title"),
    storageDetail: document.getElementById("otm-storage-detail"),
    compareCard: document.getElementById("otm-compare-card"),
    shotStrip: document.getElementById("otm-shot-strip"),
    gridHead: document.getElementById("otm-grid-head"),
    gridBody: document.getElementById("otm-grid-body"),
    provenance: document.getElementById("otm-provenance"),
  };

  // The Confirm button wears two labels. It is the same POST either way; what
  // changes is what the server does with it, so the button says which.
  const CONFIRM_LABEL = "Confirm";
  const RETRY_SAVE_LABEL = "Retry save";

  // Deliberately explicit, never a generic string-humanizer: these are the
  // only metadata keys the server contract sends for this screen, and their
  // labels are a fixed, reviewed mapping.
  const METADATA_LABELS = [
    ["currency", "Currency"],
    ["curve_config", "Curve / Config"],
    ["side", "Side"],
    ["quote_date", "Date"],
    ["tab", "Tab"],
    ["vol_type", "Type"],
    ["source", "Source"],
    ["display_mode", "Display"],
  ];

  const STATUS_PILLS = {
    PENDING_REVIEW: { text: "Pending review", className: "is-ready" },
    CONFIRMED: { text: "Confirmed", className: "is-confirmed" },
    REJECTED: { text: "Rejected", className: "is-rejected" },
  };

  // What the canonical store did with this confirmed surface (Issue #183's
  // contract, unchanged). `saved` drives the status pill: only a surface that
  // actually reached the store may read "Confirmed & saved".
  const STORAGE_STATES = {
    SAVED: {
      saved: true,
      className: "is-saved",
      title: "Confirmed & saved to the local vol-surface store",
    },
    ALREADY_SAVED: {
      saved: true,
      className: "is-known",
      title: "Confirmed & saved — this surface was already stored, unchanged",
    },
    FAILED: {
      saved: false,
      className: "is-failed",
      title: "Confirmed, but NOT saved — this surface exists only in this session",
    },
  };

  const STORAGE_UNREPORTED = {
    saved: false,
    className: "is-failed",
    title: "Confirmed, but this workbench did not report a save",
    detail:
      "This page expects a storage result from every confirmation and did not get one, so treat this surface as session-only. Restart the workbench from start_shiori.bat so the page and the server are the same revision.",
  };

  // The capture session the trader is assembling: the files themselves, in the
  // order they were picked, each with the object URL its thumbnail uses.
  let selected = [];
  let captureId = null;
  let busy = false;
  let renderedCapture = null;
  let renderedStorageState = null;

  function setDisabled(el, disabled) {
    el.classList.toggle("is-disabled", Boolean(disabled));
  }

  function showError(title, detail) {
    els.errorTitle.textContent = title;
    els.errorDetail.textContent = detail;
    els.errorSection.hidden = false;
  }

  function clearError() {
    els.errorSection.hidden = true;
  }

  // Base64 of the exact bytes read, built in fixed-size chunks so a large
  // screenshot cannot blow the argument limit of String.fromCharCode.
  function base64FromBytes(bytes) {
    let binary = "";
    const CHUNK = 0x8000;
    for (let offset = 0; offset < bytes.length; offset += CHUNK) {
      binary += String.fromCharCode.apply(null, bytes.subarray(offset, offset + CHUNK));
    }
    return btoa(binary);
  }

  function readFileAsBytes(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error(`${file.name} could not be read from disk`));
      reader.onload = () => resolve(new Uint8Array(reader.result));
      reader.readAsArrayBuffer(file);
    });
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
    } catch (err) {
      throw new Error(`the workbench bridge returned a non-JSON response (HTTP ${response.status})`);
    }
    if (!response.ok) {
      throw new Error((payload && payload.error) || `HTTP ${response.status}`);
    }
    return payload;
  }

  // Fails closed on anything the server contract does not promise: a
  // half-rendered table is exactly the kind of thing a trader could sign off
  // on by mistake.
  function validateCapturePayload(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("malformed response: expected a JSON object");
    }
    if (typeof payload.capture_id !== "string" || !payload.capture_id) {
      throw new Error('malformed response: missing "capture_id"');
    }
    const capture = payload.capture;
    if (!capture || typeof capture !== "object") {
      throw new Error('malformed response: missing "capture"');
    }
    for (const key of ["sources", "metadata", "coverage", "blocking_errors", "warnings", "review_status"]) {
      if (!(key in capture)) throw new Error(`malformed response: missing "capture.${key}"`);
    }
    for (const key of ["sources", "coverage", "blocking_errors", "warnings", "missing_rows", "unexpected_rows", "missing_strikes", "unexpected_strikes"]) {
      if (!Array.isArray(capture[key])) {
        throw new Error(`malformed response: "capture.${key}" must be an array`);
      }
    }
    if (typeof capture.can_confirm !== "boolean") {
      throw new Error('malformed response: "capture.can_confirm" must be a boolean');
    }
    if (payload.storage !== undefined && payload.storage !== null) {
      if (typeof payload.storage !== "object" || Array.isArray(payload.storage)) {
        throw new Error('malformed response: "storage" must be a JSON object');
      }
      if (typeof payload.storage.status !== "string" || !payload.storage.status) {
        throw new Error('malformed response: "storage.status" must be a non-empty string');
      }
    }
    const table = capture.table;
    if (table !== null && table !== undefined) {
      if (!Array.isArray(table.strikes) || !Array.isArray(table.rows)) {
        throw new Error("malformed response: the table's axes must be arrays");
      }
      table.rows.forEach((row, index) => {
        if (!Array.isArray(row.values) || row.values.length !== table.strikes.length) {
          throw new Error(`malformed response: table row ${index} does not match the strike axis`);
        }
      });
    }
    return payload;
  }

  // ---- the file list the trader assembles --------------------------------

  function fileKey(file) {
    return `${file.name}|${file.size}|${file.lastModified}`;
  }

  function addFiles(fileList) {
    if (busy) return;
    const known = new Set(selected.map((item) => fileKey(item.file)));
    Array.from(fileList || []).forEach((file) => {
      // The same file dropped twice in one gesture is one file. Two *different*
      // files with identical bytes are the server's business, not this page's:
      // it refuses them by hash, which is the honest test.
      if (known.has(fileKey(file))) return;
      known.add(fileKey(file));
      selected.push({ file, url: URL.createObjectURL(file) });
    });
    invalidateReview();
    renderFileList();
  }

  function removeFileAt(index) {
    if (busy) return;
    const [removed] = selected.splice(index, 1);
    if (removed) URL.revokeObjectURL(removed.url);
    invalidateReview();
    renderFileList();
  }

  // A change to the file list invalidates whatever was under review: never
  // leave one session's table on screen beside another session's files.
  function invalidateReview() {
    captureId = null;
    renderedCapture = null;
    renderedStorageState = null;
    els.reviewCard.hidden = true;
    els.compareCard.hidden = true;
    els.storage.hidden = true;
    els.completeness.hidden = true;
    clearError();
  }

  function renderFileList() {
    els.fileList.textContent = "";
    selected.forEach((item, index) => {
      const li = document.createElement("li");
      li.className = "otm-file-item";

      const position = document.createElement("span");
      position.className = "otm-file-index";
      position.textContent = `${index + 1}.`;

      const name = document.createElement("span");
      name.className = "otm-file-name";
      name.textContent = item.file.name;

      const size = document.createElement("span");
      size.className = "otm-file-size";
      size.textContent = `${item.file.size.toLocaleString()} bytes`;

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "otm-file-remove";
      remove.textContent = "Remove";
      remove.disabled = busy;
      remove.setAttribute("aria-label", `Remove ${item.file.name} from this capture`);
      remove.addEventListener("click", () => removeFileAt(index));

      li.appendChild(position);
      li.appendChild(name);
      li.appendChild(size);
      li.appendChild(remove);
      els.fileList.appendChild(li);
    });
    els.fileList.hidden = selected.length === 0;
    setDisabled(els.parseBtn, busy || selected.length === 0);
  }

  // ---- rendering the server's answer -------------------------------------

  function renderIssues(listEl, issues) {
    listEl.textContent = "";
    issues.forEach((issue) => {
      const item = document.createElement("li");
      if (issue.row) {
        const cell = document.createElement("span");
        cell.className = "cell";
        cell.textContent = issue.strike
          ? `${issue.row} × ${issue.strike}: `
          : `${issue.row}: `;
        item.appendChild(cell);
      }
      item.appendChild(document.createTextNode(issue.message));
      if (issue.source) {
        const source = document.createElement("span");
        source.className = "code";
        source.textContent = issue.source;
        item.appendChild(source);
      }
      const code = document.createElement("span");
      code.className = "code";
      code.textContent = issue.code;
      item.appendChild(code);
      listEl.appendChild(item);
    });
  }

  function renderMetadata(metadata) {
    els.metaGrid.textContent = "";
    const unresolved = new Set(metadata.unresolved_fields || []);
    METADATA_LABELS.forEach(([key, label]) => {
      const item = document.createElement("div");
      item.className = "capture-meta-item";
      const k = document.createElement("div");
      k.className = "capture-meta-k";
      k.textContent = label;
      const v = document.createElement("div");
      v.className = "capture-meta-v";
      if (unresolved.has(key) || metadata[key] === null || metadata[key] === undefined) {
        v.classList.add("is-unresolved");
        v.textContent = "Unresolved";
      } else {
        v.textContent = metadata[key];
      }
      item.appendChild(k);
      item.appendChild(v);
      els.metaGrid.appendChild(item);
    });
  }

  // Whether the merged capture is the whole screen. Every number here is the
  // server's: this page counts no rows and decides no completeness -- it
  // renders the answer, and the Confirm button's state comes from the same
  // response's `can_confirm`.
  function renderCompleteness(capture) {
    if (!capture.table) {
      els.completeness.hidden = true;
      return;
    }
    const missingRows = capture.missing_rows || [];
    const unexpectedRows = capture.unexpected_rows || [];
    const missingStrikes = capture.missing_strikes || [];
    const unexpectedStrikes = capture.unexpected_strikes || [];
    const complete =
      missingRows.length === 0 &&
      unexpectedRows.length === 0 &&
      missingStrikes.length === 0 &&
      unexpectedStrikes.length === 0;
    els.completeness.className = "otm-completeness " + (complete ? "is-complete" : "is-partial");

    const rows = capture.table.rows.length;
    const strikes = capture.table.strikes.length;
    els.completenessTitle.textContent = complete
      ? `Complete — all ${rows} Term × Tenor rows × ${strikes} strike columns captured`
      : `Incomplete — ${rows} of ${capture.expected_row_count} Term × Tenor rows, ` +
        `${strikes} of ${capture.expected_strike_count} strike columns`;

    els.completenessDetail.textContent = "";
    const line = (text) => {
      const div = document.createElement("div");
      div.textContent = text;
      els.completenessDetail.appendChild(div);
    };
    if (missingRows.length) line(`Missing rows (${missingRows.length}): ${missingRows.join(", ")}`);
    if (missingStrikes.length) {
      line(`Missing strike columns (${missingStrikes.length}): ${missingStrikes.join(", ")}`);
    }
    if (unexpectedRows.length) {
      line(`Rows not part of this screen (${unexpectedRows.length}): ${unexpectedRows.join(", ")}`);
    }
    if (unexpectedStrikes.length) {
      line(
        `Strike columns not part of this screen (${unexpectedStrikes.length}): ${unexpectedStrikes.join(", ")}`
      );
    }
    if (!complete) {
      line(
        "Capture the rest in the same sitting, overlapping what you already have, and parse the whole set again."
      );
    }
    els.completeness.hidden = false;
  }

  // What each screenshot contributed, and where two of them overlapped --
  // copied from the server's own coverage block, never counted here.
  function renderCoverage(coverage) {
    els.coverage.textContent = "";
    coverage.forEach((item) => {
      const row = document.createElement("div");
      row.className = "otm-coverage-row";

      const file = document.createElement("span");
      file.className = "otm-coverage-file";
      file.textContent = item.source_reference;
      row.appendChild(file);

      if (!item.row_count) {
        const empty = document.createElement("span");
        empty.className = "otm-coverage-empty";
        empty.textContent = "no rows were reconstructed from this screenshot";
        row.appendChild(empty);
      } else {
        const span = document.createElement("span");
        span.textContent = `${item.row_count} rows, ${item.first_row} → ${item.last_row}`;
        row.appendChild(span);
        const shared = document.createElement("span");
        shared.textContent = item.shared_row_count
          ? `${item.shared_row_count} shared with another screenshot`
          : "no rows shared with another screenshot";
        row.appendChild(shared);
      }
      els.coverage.appendChild(row);
    });
  }

  // Two decimals is only VCUB's usual look, never a rounding this page is
  // allowed to impose: a value that does not survive `toFixed(2)` intact is
  // shown in full, so what the trader compares against the screenshot is
  // exactly what the parser read. Signed zero is handled explicitly because
  // JavaScript hides it twice over (see vcub_capture.js).
  function formatCapturedValue(value) {
    const sign = value < 0 || Object.is(value, -0) ? "-" : "";
    const magnitude = Math.abs(value);
    const twoDecimals = sign + magnitude.toFixed(2);
    return Object.is(Number(twoDecimals), value) ? twoDecimals : sign + String(magnitude);
  }

  // Term x Tenor down the rows, strike offset across the columns -- the
  // screen's own data topology. The ATM column is marked because it is the one
  // column whose number is an absolute vol; the rest are spreads to it.
  function renderTable(table) {
    els.gridHead.textContent = "";
    els.gridBody.textContent = "";
    if (!table) return;

    const corner = document.createElement("th");
    corner.className = "otm-row-corner";
    corner.textContent = "Term × Tenor";
    els.gridHead.appendChild(corner);
    table.strikes.forEach((strike) => {
      const th = document.createElement("th");
      th.className = strike.offset_bp === null ? "num is-atm" : "num";
      th.textContent = strike.label;
      els.gridHead.appendChild(th);
    });

    table.rows.forEach((row) => {
      const tr = document.createElement("tr");
      const label = document.createElement("td");
      label.className = "otm-row-label";
      label.textContent = row.label;
      tr.appendChild(label);
      table.strikes.forEach((strike, columnIndex) => {
        const td = document.createElement("td");
        td.className = strike.offset_bp === null ? "num is-atm" : "num";
        const value = row.values[columnIndex];
        if (typeof value === "number" && Number.isFinite(value)) {
          td.textContent = formatCapturedValue(value);
        } else {
          td.classList.add("unresolved");
          td.textContent = "unresolved";
        }
        td.title = `${row.label} × ${strike.label}`;
        tr.appendChild(td);
      });
      els.gridBody.appendChild(tr);
    });
  }

  // The thumbnails are the local files this page still holds, matched to the
  // server's source list by position -- the same order the request was built
  // in. A source the page has no file for still gets its caption, so a
  // reloaded review never shows a screenshot next to the wrong name.
  function renderShots(sources) {
    els.shotStrip.textContent = "";
    sources.forEach((source, index) => {
      const item = document.createElement("div");
      item.className = "otm-shot-item";
      const caption = document.createElement("div");
      caption.className = "otm-shot-caption";
      caption.textContent = `${index + 1}. ${source.source_reference}`;
      item.appendChild(caption);
      const local = selected[index];
      if (local && local.file.name === source.source_reference) {
        const img = document.createElement("img");
        img.src = local.url;
        img.alt = `Screenshot ${index + 1} of this capture, ${source.source_reference}`;
        item.appendChild(img);
      }
      els.shotStrip.appendChild(item);
    });
  }

  function renderProvenance(sources, readerNotes) {
    const lines = [];
    sources.forEach((source, index) => {
      lines.push(`${index + 1}. ${source.source_reference}`);
      lines.push(`   sha256: ${source.source_image_sha256}`);
      lines.push(`   bytes: ${source.source_image_bytes}`);
    });
    if (sources.length) {
      lines.push(`captured_at: ${sources[0].captured_at}`);
      lines.push(`parser: ${sources[0].parser_name} ${sources[0].parser_version}`);
    }
    (readerNotes || []).forEach((note) => lines.push(`reader: ${note}`));
    els.provenance.textContent = "";
    lines.forEach((line) => {
      const div = document.createElement("div");
      div.textContent = line;
      els.provenance.appendChild(div);
    });
  }

  function storageStateFor(capture, storage) {
    if (capture.review_status !== "CONFIRMED") return null;
    if (!storage) return STORAGE_UNREPORTED;
    if (storage.status === "NOT_ATTEMPTED") return null;
    return STORAGE_STATES[storage.status] || {
      saved: false,
      className: "is-failed",
      title: `Confirmed, but the workbench reported an unknown storage status (${storage.status})`,
    };
  }

  // Every line here is copied from the server's answer; this page derives no
  // surface id, counts no point, and never words a save the server did not
  // report.
  function renderStorage(state, storage) {
    if (state === null) {
      els.storage.hidden = true;
      return;
    }
    els.storage.className = "capture-storage " + state.className;
    els.storageTitle.textContent = state.title;
    const lines = [];
    if (state.detail) lines.push(state.detail);
    if (storage) {
      if (storage.surface_id) lines.push(`surface_id: ${storage.surface_id}`);
      if (typeof storage.point_count === "number") lines.push(`points: ${storage.point_count}`);
      if (storage.database) lines.push(`database: ${storage.database}`);
      if (storage.error) lines.push(`reason: ${storage.error}`);
    }
    els.storageDetail.textContent = "";
    lines.forEach((line) => {
      const div = document.createElement("div");
      div.textContent = line;
      els.storageDetail.appendChild(div);
    });
    els.storage.hidden = false;
  }

  function renderCapture(payload, readerNotes) {
    const capture = payload.capture;
    captureId = payload.capture_id;

    const storageState = storageStateFor(capture, payload.storage);
    const pill = STATUS_PILLS[capture.review_status] || { text: capture.review_status, className: "" };
    const blocked = capture.blocking_errors.length > 0;
    let pillText = pill.text;
    let pillClass = pill.className;
    if (blocked && capture.review_status === "PENDING_REVIEW") {
      pillText = "Blocked";
      pillClass = "is-blocked";
    } else if (storageState !== null) {
      pillText = storageState.saved ? "Confirmed & saved" : "Confirmed — not saved";
      pillClass = storageState.saved ? "is-confirmed" : "is-blocked";
    }
    els.statusPill.className = "capture-status-pill " + pillClass;
    els.statusPill.textContent = pillText;

    els.blockers.hidden = !blocked;
    els.blockersTitle.textContent = `${capture.blocking_errors.length} blocking — confirmation is not available`;
    renderIssues(els.blockerList, capture.blocking_errors);

    els.warnings.hidden = capture.warnings.length === 0;
    els.warningsTitle.textContent = `${capture.warnings.length} to check against the screenshots`;
    renderIssues(els.warningList, capture.warnings);

    renderMetadata(capture.metadata);
    renderCompleteness(capture);
    renderCoverage(capture.coverage);
    renderTable(capture.table);
    renderShots(capture.sources);
    renderProvenance(capture.sources, readerNotes);
    renderStorage(storageState, payload.storage);

    renderedCapture = capture;
    renderedStorageState = storageState;
    applyReviewActionState();

    const rowCount = capture.table ? capture.table.rows.length : 0;
    const shotCount = capture.sources.length;
    if (capture.review_status === "CONFIRMED") {
      const durability = storageState !== null && storageState.saved
        ? "It survives a workbench restart."
        : "It exists only in this session's memory and will be lost on restart.";
      els.reviewStatus.textContent = `Confirmed by ${capture.reviewed_by} at ${capture.reviewed_at}. ${durability} These values are stored as reviewed market data only — nothing is priced from them.`;
    } else if (capture.review_status === "REJECTED") {
      els.reviewStatus.textContent = `Rejected by ${capture.reviewed_by} at ${capture.reviewed_at}. No captured value was accepted.`;
    } else if (blocked) {
      els.reviewStatus.textContent = "This capture cannot be confirmed: either the parser could not place every value safely, or the reconstructed surface is not the whole screen. Fix what is listed above and parse the whole set again, or reject it and recapture the screen.";
    } else {
      els.reviewStatus.textContent = `${rowCount} Term × Tenor rows reconstructed from ${shotCount} screenshot${shotCount === 1 ? "" : "s"}. Compare every cell against them before confirming — one Confirm stores one snapshot.`;
    }

    els.reviewCard.hidden = false;
    els.compareCard.hidden = false;
  }

  function setBusy(value) {
    busy = value;
    els.loading.hidden = !value;
    setDisabled(els.parseBtn, value || selected.length === 0);
    // The pickers are frozen for the whole in-flight interval, for the same
    // reason the ATM view freezes its one file input: choosing another set
    // mid-request would swap the thumbnails under a response still on its way.
    els.fileInput.disabled = value;
    setDisabled(els.chooseBtn, value);
    els.fileList.querySelectorAll(".otm-file-remove").forEach((button) => {
      button.disabled = value;
    });
    applyReviewActionState();
  }

  // Confirm/Reject availability is derived in one place, from the capture on
  // screen and whether a request is in flight, and re-applied whenever either
  // changes.
  function applyReviewActionState() {
    if (renderedCapture === null) {
      setDisabled(els.confirmBtn, true);
      setDisabled(els.rejectBtn, true);
      els.confirmBtn.textContent = CONFIRM_LABEL;
      return;
    }
    const decided = renderedCapture.review_status !== "PENDING_REVIEW";
    // A confirmed capture the store did not take is the one decided state
    // where Confirm stays live, so the trader can retry the save under the
    // same reviewer (PR #184's retry policy, unchanged).
    const canRetrySave =
      renderedCapture.review_status === "CONFIRMED" &&
      renderedStorageState !== null &&
      !renderedStorageState.saved;
    setDisabled(els.confirmBtn, busy || (!renderedCapture.can_confirm && !canRetrySave));
    setDisabled(els.rejectBtn, busy || decided);
    els.confirmBtn.textContent = canRetrySave ? RETRY_SAVE_LABEL : CONFIRM_LABEL;
    els.reviewedBy.disabled = decided;
  }

  // ---- events -------------------------------------------------------------

  els.fileInput.addEventListener("change", () => {
    addFiles(els.fileInput.files);
    // Cleared so picking the same file again after removing it still fires a
    // change event.
    els.fileInput.value = "";
  });

  ["dragenter", "dragover"].forEach((name) => {
    els.dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      if (!busy) els.dropzone.classList.add("is-dragover");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    els.dropzone.addEventListener(name, () => els.dropzone.classList.remove("is-dragover"));
  });
  els.dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    if (busy) return;
    addFiles(event.dataTransfer && event.dataTransfer.files);
  });
  // Without these the browser would navigate away from the workbench when a
  // file is dropped anywhere but the zone -- losing the trader's session.
  ["dragover", "drop"].forEach((name) => {
    viewOtm.addEventListener(name, (event) => {
      if (event.target === els.dropzone || els.dropzone.contains(event.target)) return;
      event.preventDefault();
    });
  });

  els.parseBtn.addEventListener("click", async () => {
    if (busy || selected.length === 0) return;
    clearError();
    // Pinned for this request: the payload is built from this snapshot, never
    // from the live list, so one request can only ever carry one session's
    // files with those same files' names.
    const requested = selected.slice();
    setBusy(true);
    try {
      const images = [];
      for (const item of requested) {
        const bytes = await readFileAsBytes(item.file);
        images.push({
          source_reference: item.file.name,
          image_base64: base64FromBytes(bytes),
        });
      }
      const payload = validateCapturePayload(
        await postJson("/api/vcub/otm/parse", { images })
      );
      setBusy(false);
      renderCapture(payload, payload.reader_notes);
    } catch (err) {
      renderedCapture = null;
      renderedStorageState = null;
      setBusy(false);
      els.reviewCard.hidden = true;
      els.compareCard.hidden = true;
      showError("Unable to read these screenshots", err.message);
    }
  });

  async function review(path, actionLabel) {
    if (!captureId || busy) return;
    const reviewedBy = els.reviewedBy.value.trim();
    if (!reviewedBy) {
      showError(`Cannot ${actionLabel} this capture`, "Enter who is reviewing it first.");
      return;
    }
    clearError();
    setBusy(true);
    try {
      const payload = validateCapturePayload(
        await postJson(path, { capture_id: captureId, reviewed_by: reviewedBy })
      );
      setBusy(false);
      renderCapture(payload, null);
    } catch (err) {
      setBusy(false);
      showError(`Unable to ${actionLabel} this capture`, err.message);
    }
  }

  els.confirmBtn.addEventListener("click", () => review("/api/vcub/otm/confirm", "confirm"));
  els.rejectBtn.addEventListener("click", () => review("/api/vcub/otm/reject", "reject"));

  setDisabled(els.parseBtn, true);
})();
