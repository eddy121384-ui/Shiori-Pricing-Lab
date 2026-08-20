// Capture view (Issue #181) -- Bloomberg VCUB ATM Swaptions visual capture.
//
// A third self-contained IIFE, sharing no state with the Pricing or Markets
// modules and touching nothing either of them owns. It reads one
// operator-chosen image file locally, posts its bytes to this workbench's own
// loopback bridge (POST /api/vcub/atm/parse), and renders the reconstructed
// grid the server sends back, verbatim.
//
// This file computes nothing. It does not parse the image, place a cell,
// re-order an axis, fill a hole, round a number, or decide whether a capture
// may be confirmed -- every one of those is the server's answer, rendered as
// received. `can_confirm` in particular is read, never derived here: the
// Confirm button's enabled state mirrors the server's judgement, and the
// server refuses a blocked confirmation again on its own side regardless of
// what this page does.
//
// Nothing here prices anything, touches the trader's ticket, or feeds a
// captured number into any market-data input. Confirmation ends at "the
// trader agrees this transcription matches the screen".
(function () {
  "use strict";

  const navCapture = document.getElementById("nav-capture");
  const viewCapture = document.getElementById("view-capture");
  if (!navCapture || !viewCapture) return; // Capture view not present on this page

  const els = {
    fileInput: document.getElementById("capture-file-input"),
    fileRow: document.getElementById("capture-file-row"),
    fileName: document.getElementById("capture-file-name"),
    parseBtn: document.getElementById("capture-parse-btn"),
    chooseBtn: document.querySelector('label[for="capture-file-input"]'),
    loading: document.getElementById("capture-loading"),
    errorSection: document.getElementById("capture-error"),
    errorTitle: document.getElementById("capture-error-title"),
    errorDetail: document.getElementById("capture-error-detail"),
    reviewCard: document.getElementById("capture-review-card"),
    statusPill: document.getElementById("capture-status-pill"),
    blockers: document.getElementById("capture-blockers"),
    blockerList: document.getElementById("capture-blocker-list"),
    blockersTitle: document.getElementById("capture-blockers-title"),
    warnings: document.getElementById("capture-warnings"),
    warningList: document.getElementById("capture-warning-list"),
    warningsTitle: document.getElementById("capture-warnings-title"),
    metaGrid: document.getElementById("capture-meta-grid"),
    reviewedBy: document.getElementById("capture-reviewed-by"),
    confirmBtn: document.getElementById("capture-confirm-btn"),
    rejectBtn: document.getElementById("capture-reject-btn"),
    reviewStatus: document.getElementById("capture-review-status"),
    storage: document.getElementById("capture-storage"),
    storageTitle: document.getElementById("capture-storage-title"),
    storageDetail: document.getElementById("capture-storage-detail"),
    compareCard: document.getElementById("capture-compare-card"),
    shotImage: document.getElementById("capture-shot-image"),
    gridHead: document.getElementById("capture-grid-head"),
    gridBody: document.getElementById("capture-grid-body"),
    provenance: document.getElementById("capture-provenance"),
  };

  const NBSP_DASH = "—";

  // The Confirm button wears two labels. It is the same POST either way; what
  // changes is what the server does with it, so the button says which.
  const CONFIRM_LABEL = "Confirm";
  const RETRY_SAVE_LABEL = "Retry save";

  // Deliberately explicit, never a generic string-humanizer: these are the
  // only metadata keys the server contract sends, and their labels are a
  // fixed, reviewed mapping.
  const METADATA_LABELS = [
    ["currency", "Currency"],
    ["curve_config", "Curve / Config"],
    ["side", "Side"],
    ["quote_date", "Date"],
    ["tab", "Tab"],
    ["vol_type", "Vol Type"],
    ["source", "Source"],
  ];

  const STATUS_PILLS = {
    PENDING_REVIEW: { text: "Pending review", className: "is-ready" },
    CONFIRMED: { text: "Confirmed", className: "is-confirmed" },
    REJECTED: { text: "Rejected", className: "is-rejected" },
  };

  // Issue #183 -- what the canonical store did with this confirmed surface.
  // A fixed, reviewed mapping like METADATA_LABELS above, and the only place
  // that decides how durability is worded. `saved` drives the status pill:
  // only a surface that actually reached the store may read "Confirmed &
  // saved", so a durability failure can never be shown as one.
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

  // What a confirmed capture reports when the server said nothing about
  // storage at all -- an older workbench process serving this page. Treated
  // as "not saved" rather than assumed successful: the whole point of the
  // block is that the trader can tell session memory from durable data.
  const STORAGE_UNREPORTED = {
    saved: false,
    className: "is-failed",
    title: "Confirmed, but this workbench did not report a save",
    detail:
      "This page expects a storage result from every confirmation and did not get one, so treat this surface as session-only. Restart the workbench from start_shiori.bat so the page and the server are the same revision.",
  };

  let selectedFile = null;
  let selectedObjectUrl = null;
  let captureId = null;
  let busy = false;
  // The capture currently on screen, so the review actions can be restored to
  // match it after a request finishes -- including a failed one.
  let renderedCapture = null;
  // And what the store did with it, because that decides whether Confirm is
  // still live: a confirmed capture that did not reach the store needs its
  // button back so the trader can retry the save.
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
      reader.onerror = () => reject(new Error("the file could not be read from disk"));
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
  // half-rendered grid is exactly the kind of thing a trader could sign off
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
    for (const key of ["provenance", "metadata", "blocking_errors", "warnings", "review_status"]) {
      if (!(key in capture)) throw new Error(`malformed response: missing "capture.${key}"`);
    }
    if (!Array.isArray(capture.blocking_errors) || !Array.isArray(capture.warnings)) {
      throw new Error("malformed response: issue lists must be arrays");
    }
    if (typeof capture.can_confirm !== "boolean") {
      throw new Error('malformed response: "capture.can_confirm" must be a boolean');
    }
    // Optional -- /parse does not persist anything and sends none -- but a
    // malformed one is refused rather than rendered as an unknown state.
    if (payload.storage !== undefined && payload.storage !== null) {
      if (typeof payload.storage !== "object" || Array.isArray(payload.storage)) {
        throw new Error('malformed response: "storage" must be a JSON object');
      }
      if (typeof payload.storage.status !== "string" || !payload.storage.status) {
        throw new Error('malformed response: "storage.status" must be a non-empty string');
      }
    }
    const grid = capture.grid;
    if (grid !== null) {
      if (!Array.isArray(grid.expiry_labels) || !Array.isArray(grid.tenor_labels)) {
        throw new Error("malformed response: grid axes must be arrays");
      }
      if (!Array.isArray(grid.values) || grid.values.length !== grid.expiry_labels.length) {
        throw new Error("malformed response: the grid has one row per expiry label");
      }
      grid.values.forEach((row, index) => {
        if (!Array.isArray(row) || row.length !== grid.tenor_labels.length) {
          throw new Error(`malformed response: grid row ${index} does not match the tenor axis`);
        }
      });
    }
    return payload;
  }

  function renderIssues(listEl, issues) {
    listEl.textContent = "";
    issues.forEach((issue) => {
      const item = document.createElement("li");
      if (issue.expiry && issue.tenor) {
        const cell = document.createElement("span");
        cell.className = "cell";
        cell.textContent = `${issue.expiry} × ${issue.tenor}: `;
        item.appendChild(cell);
      }
      item.appendChild(document.createTextNode(issue.message));
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

  // Two decimals is only VCUB's usual look, never a rounding this page is
  // allowed to impose: a value that does not survive `toFixed(2)` intact is
  // shown in full, so what the trader compares against the screenshot is
  // exactly what the parser read.
  //
  // Signed zero is handled explicitly because JavaScript hides it twice over:
  // `(-0).toFixed(2)` is "0.00", and `Number("0.00") === -0` is true, so a
  // naive round-trip check calls the rendering lossless while showing the
  // trader a different number from the one the server holds. The bridge now
  // treats -0.0 and 0.0 as distinct captures, so this page must not collapse
  // them back together (Codex review, PR #182).
  function formatCapturedValue(value) {
    const sign = value < 0 || Object.is(value, -0) ? "-" : "";
    const magnitude = Math.abs(value);
    const twoDecimals = sign + magnitude.toFixed(2);
    return Object.is(Number(twoDecimals), value) ? twoDecimals : sign + String(magnitude);
  }

  // Expiry down the rows, Swap Tenor across the columns -- VCUB's own data
  // topology, drawn with the workbench's existing table component.
  function renderGrid(grid) {
    els.gridHead.textContent = "";
    els.gridBody.textContent = "";
    if (!grid) return;

    const corner = document.createElement("th");
    corner.className = "expiry-corner";
    corner.textContent = "Expiry";
    els.gridHead.appendChild(corner);
    grid.tenor_labels.forEach((tenor) => {
      const th = document.createElement("th");
      th.className = "num";
      th.textContent = tenor;
      els.gridHead.appendChild(th);
    });

    grid.expiry_labels.forEach((expiry, rowIndex) => {
      const tr = document.createElement("tr");
      const label = document.createElement("td");
      label.className = "expiry-label";
      label.textContent = expiry;
      tr.appendChild(label);
      grid.tenor_labels.forEach((tenor, columnIndex) => {
        const td = document.createElement("td");
        td.className = "num";
        const value = grid.values[rowIndex][columnIndex];
        if (typeof value === "number" && Number.isFinite(value)) {
          td.textContent = formatCapturedValue(value);
        } else {
          td.classList.add("unresolved");
          td.textContent = "unresolved";
        }
        td.title = `${expiry} × ${tenor}`;
        tr.appendChild(td);
      });
      els.gridBody.appendChild(tr);
    });
  }

  function renderProvenance(provenance, readerNotes) {
    const lines = [
      `source: ${provenance.source_reference}`,
      `sha256: ${provenance.source_image_sha256}`,
      `bytes: ${provenance.source_image_bytes}`,
      `captured_at: ${provenance.captured_at}`,
      `parser: ${provenance.parser_name} ${provenance.parser_version}`,
    ];
    (readerNotes || []).forEach((note) => lines.push(`reader: ${note}`));
    els.provenance.textContent = "";
    lines.forEach((line) => {
      const div = document.createElement("div");
      div.textContent = line;
      els.provenance.appendChild(div);
    });
  }

  // Which storage state this response describes, or null when durability is
  // not part of the answer at all -- a parse, or a rejection, neither of
  // which offers anything to the canonical store.
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
    els.warningsTitle.textContent = `${capture.warnings.length} unresolved — check these against the screenshot`;
    renderIssues(els.warningList, capture.warnings);

    renderMetadata(capture.metadata);
    renderGrid(capture.grid);
    renderProvenance(capture.provenance, readerNotes);
    renderStorage(storageState, payload.storage);

    renderedCapture = capture;
    renderedStorageState = storageState;
    applyReviewActionState();

    if (capture.review_status === "CONFIRMED") {
      // Fails closed: only a state that actually reports a save may claim
      // durability. Anything else -- including a confirmation that somehow
      // arrives with no storage state at all -- reads as session-only.
      const durability = storageState !== null && storageState.saved
        ? "It survives a workbench restart."
        : "It exists only in this session's memory and will be lost on restart.";
      els.reviewStatus.textContent = `Confirmed by ${capture.reviewed_by} at ${capture.reviewed_at}. ${durability} These values are stored as reviewed market data only — nothing is priced from them.`;
    } else if (capture.review_status === "REJECTED") {
      els.reviewStatus.textContent = `Rejected by ${capture.reviewed_by} at ${capture.reviewed_at}. No captured value was accepted.`;
    } else if (blocked) {
      els.reviewStatus.textContent = "The parser could not place every value safely, so this capture cannot be confirmed. Reject it and recapture the screen.";
    } else {
      els.reviewStatus.textContent = "Compare every cell against the screenshot before confirming.";
    }

    els.reviewCard.hidden = false;
    els.compareCard.hidden = false;
  }

  function setBusy(value) {
    busy = value;
    els.loading.hidden = !value;
    setDisabled(els.parseBtn, value || !selectedFile);
    // The file picker is frozen for the whole in-flight interval. Left live,
    // choosing another screenshot mid-request swapped `selectedFile` and the
    // preview under the response still on its way, so the first file's grid
    // could render beside the second file's image and Confirm would apply to
    // a capture the trader was no longer looking at (Codex review, PR #182).
    els.fileInput.disabled = value;
    setDisabled(els.chooseBtn, value);
    applyReviewActionState();
  }

  // Confirm/Reject availability is derived in one place, from the capture on
  // screen and whether a request is in flight, and re-applied whenever either
  // changes. Disabling them on the way into a request and restoring them only
  // on the success path -- the previous shape -- left both buttons dead after
  // a failed confirm or reject, so the capture stayed pending with no way to
  // retry short of reparsing (Codex review, PR #182).
  function applyReviewActionState() {
    if (renderedCapture === null) {
      setDisabled(els.confirmBtn, true);
      setDisabled(els.rejectBtn, true);
      els.confirmBtn.textContent = CONFIRM_LABEL;
      return;
    }
    const decided = renderedCapture.review_status !== "PENDING_REVIEW";
    // A confirmed capture the store did not take is the one decided state
    // where Confirm stays live. `can_confirm` is false by then -- the review
    // decision is terminal and the server says so -- and reading it alone
    // left the button inert with `pointer-events: none`, so the server-side
    // retry was unreachable from this page and a failed write still stranded
    // the trader (Codex review round 2, PR #184). The retry re-attempts the
    // save under the same reviewer; it never re-decides anything.
    const canRetrySave =
      renderedCapture.review_status === "CONFIRMED" &&
      renderedStorageState !== null &&
      !renderedStorageState.saved;
    setDisabled(els.confirmBtn, busy || (!renderedCapture.can_confirm && !canRetrySave));
    setDisabled(els.rejectBtn, busy || decided);
    els.confirmBtn.textContent = canRetrySave ? RETRY_SAVE_LABEL : CONFIRM_LABEL;
    // Left disabled on a retry on purpose: the server only repeats a
    // confirmation for the trader who made it, and the field still holds
    // their name, so freezing it is what keeps the retry addressed to them.
    els.reviewedBy.disabled = decided;
  }

  els.fileInput.addEventListener("change", () => {
    const file = els.fileInput.files && els.fileInput.files[0];
    selectedFile = file || null;
    clearError();
    if (selectedObjectUrl) {
      URL.revokeObjectURL(selectedObjectUrl);
      selectedObjectUrl = null;
    }
    els.fileRow.hidden = !selectedFile;
    els.fileName.textContent = selectedFile ? selectedFile.name : NBSP_DASH;
    setDisabled(els.parseBtn, !selectedFile);
    // A new file invalidates whatever was under review: never leave one
    // screenshot's grid on screen beside another screenshot.
    captureId = null;
    renderedCapture = null;
    renderedStorageState = null;
    els.reviewCard.hidden = true;
    els.compareCard.hidden = true;
    els.storage.hidden = true;
    if (selectedFile) {
      selectedObjectUrl = URL.createObjectURL(selectedFile);
      els.shotImage.src = selectedObjectUrl;
    }
  });

  els.parseBtn.addEventListener("click", async () => {
    if (!selectedFile || busy) return;
    clearError();
    // Pinned for this request: every field below reads the snapshot, never
    // the live `selectedFile`, so one request can only ever pair one file's
    // bytes with that same file's name -- belt and braces alongside the
    // frozen picker above.
    const requestedFile = selectedFile;
    setBusy(true);
    try {
      const bytes = await readFileAsBytes(requestedFile);
      const payload = validateCapturePayload(
        await postJson("/api/vcub/atm/parse", {
          source_reference: requestedFile.name,
          image_base64: base64FromBytes(bytes),
        })
      );
      setBusy(false);
      if (selectedFile !== requestedFile) return; // the trader moved on; this answer is stale
      renderCapture(payload, payload.reader_notes);
    } catch (err) {
      renderedCapture = null;
      renderedStorageState = null;
      setBusy(false);
      els.reviewCard.hidden = true;
      els.compareCard.hidden = true;
      showError("Unable to read this screenshot", err.message);
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

  els.confirmBtn.addEventListener("click", () => review("/api/vcub/atm/confirm", "confirm"));
  els.rejectBtn.addEventListener("click", () => review("/api/vcub/atm/reject", "reject"));

  setDisabled(els.parseBtn, true);
})();
