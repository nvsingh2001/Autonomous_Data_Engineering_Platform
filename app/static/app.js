let state = {
  status: "idle",
  activeStep: "idle",
  logs: "",
  files: [],
  reports: [],
  activeReportTab: null,
};

if (typeof marked !== "undefined") {
  marked.use({ gfm: true, breaks: false });
}

let pollInterval = null;

const STEP_ORDER = [
  "profiling",
  "quality",
  "schema",
  "transformations",
  "analytics",
  "summarizing",
];
const STEP_LABELS = {
  profiling: "Profiling Datasets",
  quality: "Quality Audit Gate",
  schema: "Warehouse Design",
  transformations: "Compile Schema",
  analytics: "Extract Business KPIs",
  summarizing: "Executive Recommendations",
};

const statusBadge = document.getElementById("statusBadge");
const fileInput = document.getElementById("fileInput");
const fileChips = document.getElementById("fileChips");
const chatInputBox = document.getElementById("chatInputBox");
const viewIdle = document.getElementById("viewIdle");
const viewRunning = document.getElementById("viewRunning");
const viewReports = document.getElementById("viewReports");
const stepper = document.getElementById("stepper");
const logConsole = document.getElementById("logConsole");
const terminalConsole = document.getElementById("terminalConsole");
const kpiGrid = document.getElementById("kpiGrid");
const reportTabs = document.getElementById("reportTabs");
const markdownViewer = document.getElementById("markdownViewer");
const approvalModal = document.getElementById("approvalModal");
const modalScore = document.getElementById("modalScore");
const modalProgressFill = document.getElementById("modalProgressFill");
const modalSummary = document.getElementById("modalSummary");
const btnLaunch = document.getElementById("btnLaunch");
const btnAttach = document.getElementById("btnAttach");
const btnReset = document.getElementById("btnReset");
const btnApprove = document.getElementById("btnApprove");
const btnReject = document.getElementById("btnReject");
const btnViewReports = document.getElementById("btnViewReports");
const btnNewRun = document.getElementById("btnNewRun");
const btnHome = document.getElementById("btnHome");
const btnBuild = document.getElementById("btnBuild");
const intentStream = document.getElementById("intentStream");
const intentGreeting = document.getElementById("intentGreeting");
const btnSkipChat = document.getElementById("btnSkipChat");

let chatMessages, chatQuestion, btnAsk;
let intentStarted = false;

document.addEventListener("DOMContentLoaded", () => {
  chatMessages = document.getElementById("chatMessages");
  chatQuestion = document.getElementById("chatQuestion");
  btnAsk = document.getElementById("btnAsk");

  initDragAndDrop();
  initEventListeners();
  initInstructionsCounter();
  initChatPanel();
  loadFiles();
  checkReportsAvailability();
  startStatusPolling();
});

function initInstructionsCounter() {
  const ta = document.getElementById("userInstructions");
  const counter = document.getElementById("instrCharCount");
  if (!ta || !counter) return;
  ta.addEventListener("input", () => {
    const len = ta.value.length;
    counter.textContent = len;
    counter.parentElement.style.color = len >= 1400 ? "var(--accent-red)" : "";
  });
}

function initEventListeners() {
  btnLaunch.addEventListener("click", onComposerSend);
  if (btnBuild) btnBuild.addEventListener("click", finalizeAndBuild);
  if (btnSkipChat) btnSkipChat.addEventListener("click", skipAndBuild);
  const ta = document.getElementById("userInstructions");
  if (ta)
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendIntentMessage();
      }
    });
  btnAttach.addEventListener("click", () => fileInput.click());
  btnReset.addEventListener("click", resetWarehouse);
  btnApprove.addEventListener("click", () => submitApproval(true));
  btnReject.addEventListener("click", () => submitApproval(false));
  fileInput.addEventListener("change", handleFileSelect);
  if (btnViewReports)
    btnViewReports.addEventListener("click", showReportsViewDirectly);
  if (btnNewRun) btnNewRun.addEventListener("click", startNewRun);
  if (btnHome) btnHome.addEventListener("click", goHome);
}

function initDragAndDrop() {
  const target = viewIdle;
  if (!target) return;

  ["dragenter", "dragover"].forEach((evt) => {
    target.addEventListener(
      evt,
      (e) => {
        e.preventDefault();
        if (chatInputBox) chatInputBox.classList.add("drag-over");
      },
      false,
    );
  });

  ["dragleave", "drop"].forEach((evt) => {
    target.addEventListener(
      evt,
      (e) => {
        e.preventDefault();
        if (chatInputBox) chatInputBox.classList.remove("drag-over");
      },
      false,
    );
  });

  target.addEventListener("drop", (e) => {
    const dt = e.dataTransfer;
    if (dt && dt.files.length > 0) uploadFiles(dt.files);
  });
}

function handleFileSelect(e) {
  uploadFiles(e.target.files);
  e.target.value = "";
}

const _ALLOWED_EXTS = new Set([".csv", ".xlsx", ".xls", ".json"]);
const _MAX_FILE_BYTES = 200 * 1024 * 1024;

function validateFiles(files) {
  const errors = [],
    valid = [];
  for (const f of files) {
    const dot = f.name.lastIndexOf(".");
    const ext = dot >= 0 ? f.name.slice(dot).toLowerCase() : "";
    if (!_ALLOWED_EXTS.has(ext)) {
      errors.push(
        `"${f.name}" — unsupported type (${ext || "no extension"}). Use CSV, XLSX, XLS, or JSON.`,
      );
    } else if (f.size > _MAX_FILE_BYTES) {
      errors.push(
        `"${f.name}" — ${(f.size / 1048576).toFixed(1)} MB exceeds the 200 MB limit.`,
      );
    } else {
      valid.push(f);
    }
  }
  return { valid, errors };
}

function showUploadError(errors) {
  let banner = document.getElementById("uploadErrorBanner");
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "uploadErrorBanner";
    banner.className = "upload-error-banner";
    const inputOuter = document.querySelector(".chat-input-outer");
    if (inputOuter) inputOuter.insertAdjacentElement("beforebegin", banner);
    else viewIdle.appendChild(banner);
  }
  banner.innerHTML =
    `<div class="upload-error-title">Upload rejected</div>` +
    errors.map((e) => `<div class="upload-error-item">✕ ${e}</div>`).join("") +
    `<button class="upload-error-close" onclick="this.parentElement.remove()">Dismiss</button>`;
  clearTimeout(banner._timer);
  banner._timer = setTimeout(() => banner.remove(), 8000);
}

function uploadFiles(files) {
  if (!files || files.length === 0) return;

  const { valid, errors: clientErrors } = validateFiles(Array.from(files));
  if (clientErrors.length > 0) showUploadError(clientErrors);
  if (valid.length === 0) return;

  const formData = new FormData();
  for (const f of valid) formData.append("files", f);

  // Show progress bar
  const progressContainer = document.getElementById("uploadProgressContainer");
  const progressText = document.getElementById("uploadProgressText");
  const progressPercent = document.getElementById("uploadProgressPercent");
  const progressFill = document.getElementById("uploadProgressFill");

  if (progressContainer) {
    progressContainer.style.display = "flex";
    if (progressText) {
      if (valid.length === 1) {
        progressText.textContent = `Uploading ${valid[0].name}...`;
      } else {
        progressText.textContent = `Uploading ${valid.length} files...`;
      }
    }
    if (progressPercent) progressPercent.textContent = "0%";
    if (progressFill) progressFill.style.width = "0%";
  }

  btnLaunch.disabled = true;
  btnAttach.disabled = true;

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload", true);

  xhr.upload.addEventListener("progress", (event) => {
    if (event.lengthComputable) {
      const percent = Math.round((event.loaded / event.total) * 100);
      if (progressPercent) progressPercent.textContent = `${percent}%`;
      if (progressFill) progressFill.style.width = `${percent}%`;
    }
  });

  xhr.onload = () => {
    if (progressContainer) progressContainer.style.display = "none";
    btnAttach.disabled = false;

    if (xhr.status >= 200 && xhr.status < 300) {
      try {
        const data = JSON.parse(xhr.responseText);
        if (data.errors && data.errors.length > 0) {
          showUploadError(data.errors);
        }
        loadFiles();
      } catch (err) {
        console.error("Error parsing upload response", err);
      }
    } else {
      try {
        const data = JSON.parse(xhr.responseText);
        if (xhr.status === 422) {
          showUploadError(
            Array.isArray(data.detail) ? data.detail : [data.detail],
          );
        } else {
          showUploadError([`Server returned status ${xhr.status}`]);
        }
      } catch (_) {
        showUploadError([`Server returned status ${xhr.status}`]);
      }
      loadFiles();
    }
  };

  xhr.onerror = () => {
    if (progressContainer) progressContainer.style.display = "none";
    btnAttach.disabled = false;
    showUploadError(["Network error during file upload."]);
    loadFiles();
  };

  xhr.send(formData);
}

async function loadFiles() {
  try {
    const res = await fetch("/api/files");
    if (res.ok) {
      state.files = await res.json();
      renderFileChips();
      const isBusy =
        state.status === "running" || state.status === "waiting_approval";
      btnLaunch.disabled = state.files.length === 0 || isBusy;
      // Auto-start is triggered from pollStatus (which knows the real run status),
      // so it never fires before we've confirmed the pipeline is idle.
      maybeAutoStartConversation();
    }
  } catch (e) {
    console.error("Error loading files", e);
  }
}

function renderFileChips() {
  if (!fileChips) return;
  const isBusy =
    state.status === "running" || state.status === "waiting_approval";

  if (state.files.length === 0) {
    fileChips.innerHTML = "";
    fileChips.style.display = "none";
    return;
  }

  fileChips.style.display = "flex";
  fileChips.innerHTML = state.files
    .map((f) => {
      const sizeStr =
        f.size > 1048576
          ? `${(f.size / 1048576).toFixed(1)} MB`
          : `${Math.max(Math.floor(f.size / 1024), 1)} KB`;
      return `
      <div class="file-chip">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
        </svg>
        <span class="chip-name" title="${f.name}">${f.name}</span>
        <span class="chip-size">${sizeStr}</span>
        <button class="chip-remove" onclick="deleteFile('${f.name}')" ${isBusy ? "disabled" : ""} title="Remove">✕</button>
      </div>`;
    })
    .join("");
}

async function deleteFile(filename) {
  try {
    const res = await fetch(`/api/files/${encodeURIComponent(filename)}`, {
      method: "DELETE",
    });
    if (res.ok) loadFiles();
  } catch (e) {
    console.error("Error deleting file", e);
  }
}

async function resetWarehouse() {
  if (
    !confirm(
      "Reset the local DuckDB warehouse? All schema tables will be wiped.",
    )
  )
    return;
  try {
    const res = await fetch("/api/reset", { method: "POST" });
    if (res.ok) alert("Warehouse reset successfully.");
  } catch (e) {
    console.error("Error resetting warehouse", e);
  }
}

function startNewRun() {
  // Clear client state so the idle view is shown fresh
  state.status = "idle";
  state.activeStep = "idle";
  state.logs = "";
  state.activeReportTab = null;
  const ta = document.getElementById("userInstructions");
  if (ta) ta.value = "";
  const counter = document.getElementById("instrCharCount");
  if (counter) counter.textContent = "0";
  resetChatPanel();
  updateUI();
  loadFiles();
}

function goHome() {
  state.status = "idle";
  updateUI();
}

window.sendPrompt = function (btn) {
  sendIntentMessage(btn.textContent.trim());
};

function renderIntentMessage(role, content) {
  if (!intentStream) return null;
  const msg = document.createElement("div");
  msg.className = "intent-msg intent-msg-" + role;
  if (role === "assistant") {
    msg.innerHTML =
      '<div class="intent-avatar">A</div>' +
      '<div class="intent-bubble">' +
      marked.parse(content || "") +
      "</div>";
  } else {
    const bubble = document.createElement("div");
    bubble.className = "intent-bubble";
    bubble.textContent = content;
    msg.appendChild(bubble);
  }
  intentStream.appendChild(msg);
  intentStream.scrollTop = intentStream.scrollHeight;
  return msg;
}

function showIntentThinking() {
  if (!intentStream) return null;
  const msg = document.createElement("div");
  msg.className = "intent-msg intent-msg-assistant";
  msg.innerHTML =
    '<div class="intent-avatar">A</div>' +
    '<div class="intent-bubble"><div class="intent-thinking"><span></span><span></span><span></span></div></div>';
  intentStream.appendChild(msg);
  intentStream.scrollTop = intentStream.scrollHeight;
  return msg;
}

function setComposerBusy(busy) {
  const ta = document.getElementById("userInstructions");
  if (ta) ta.disabled = busy;
  btnLaunch.disabled = busy;
}

function maybeAutoStartConversation() {
  if (state.status === "idle" && state.files.length > 0 && !intentStarted) {
    ensureConversationStarted();
  }
}

async function ensureConversationStarted() {
  if (intentStarted) return;
  intentStarted = true;
  if (intentGreeting) intentGreeting.style.display = "none";
  if (intentStream) intentStream.style.display = "flex";
  const thinking = showIntentThinking();
  try {
    const res = await fetch("/api/intent/start", { method: "POST" });
    if (!res.ok) {
      // Pipeline busy — don't fake a conversation; revert and let the status
      // poller switch to the running view.
      if (thinking) thinking.remove();
      intentStarted = false;
      if (intentStream) {
        intentStream.innerHTML = "";
        intentStream.style.display = "none";
      }
      if (intentGreeting) intentGreeting.style.display = "";
      return;
    }
    const data = await res.json();
    if (thinking) thinking.remove();
    renderIntentMessage(
      "assistant",
      data.reply || "Hi! What would you like to learn from this data?",
    );
  } catch (e) {
    if (thinking) thinking.remove();
    renderIntentMessage(
      "assistant",
      "Hi! What business questions are you hoping to answer?",
    );
  }
  if (btnBuild) btnBuild.style.display = "inline-flex";
}

async function sendIntentMessage(text) {
  const ta = document.getElementById("userInstructions");
  const msg = (text != null ? text : ta ? ta.value : "").trim();
  if (!msg) return;
  await ensureConversationStarted();
  if (ta) {
    ta.value = "";
    const c = document.getElementById("instrCharCount");
    if (c) c.textContent = "0";
  }
  renderIntentMessage("user", msg);
  setComposerBusy(true);
  const thinking = showIntentThinking();
  try {
    const res = await fetch("/api/intent/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg }),
    });
    const data = await res.json();
    if (thinking) thinking.remove();
    renderIntentMessage(
      "assistant",
      res.ok ? data.reply || "" : data.detail || "Something went wrong.",
    );
  } catch (e) {
    if (thinking) thinking.remove();
    renderIntentMessage("assistant", "Connection error. Please try again.");
  } finally {
    setComposerBusy(false);
    if (ta) ta.focus();
  }
}

function onComposerSend() {
  sendIntentMessage();
}

async function finalizeAndBuild() {
  if (btnBuild) {
    btnBuild.disabled = true;
    btnBuild.textContent = "Building…";
  }
  try {
    const fin = await fetch("/api/intent/finalize", { method: "POST" });
    if (!fin.ok) throw new Error("finalize HTTP " + fin.status);
    const intent = await fin.json();
    await postRun({
      questions: intent.questions || [],
      domain: intent.domain || "e-commerce",
      priority_metrics: intent.priority_metrics || [],
      metric_definitions: intent.metric_definitions || [],
      decision_context: intent.decision_context || "",
    });
  } catch (e) {
    console.error("Build failed", e);
    showIntentError("Couldn't finalize the conversation — please try again.");
  } finally {
    if (btnBuild) {
      btnBuild.disabled = false;
      btnBuild.textContent = "Build warehouse";
    }
  }
}

async function skipAndBuild(e) {
  if (e) e.preventDefault();
  await postRun({ instructions: "" });
}

function showIntentError(msg) {
  const instrError = document.getElementById("instrError");
  if (instrError) {
    instrError.textContent = msg;
    instrError.style.display = "block";
  } else {
    alert(msg);
  }
}

async function postRun(body) {
  const instrError = document.getElementById("instrError");
  if (instrError) instrError.style.display = "none";
  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.ok) {
      startStatusPolling();
      return;
    }
    // Surface every non-OK response instead of failing silently.
    let detail = "Could not start the pipeline (HTTP " + res.status + ").";
    try {
      const data = await res.json();
      if (data && data.detail) detail = data.detail;
    } catch (_) {}
    showIntentError(detail);
  } catch (e) {
    console.error("Error starting pipeline", e);
    showIntentError("Network error starting the pipeline.");
  }
}

function startStatusPolling() {
  if (pollInterval) clearInterval(pollInterval);
  pollStatus();
  pollInterval = setInterval(pollStatus, 1000);
}

async function pollStatus() {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) return;
    const data = await res.json();

    state.status = data.status;
    state.activeStep = data.active_step;
    state.logs = data.logs;

    updateUI();

    if (state.status === "waiting_approval" && data.approval_data) {
      showApprovalModal(data.approval_data);
    } else {
      hideApprovalModal();
    }

    if (
      state.status === "completed" ||
      state.status === "failed" ||
      state.status === "idle"
    ) {
      clearInterval(pollInterval);
      pollInterval = null;
      if (state.status === "completed") loadReports();
      checkReportsAvailability();
    }

    const isBusy =
      state.status === "running" || state.status === "waiting_approval";
    btnLaunch.disabled = isBusy || state.files.length === 0;
    // Build is only visible after the conversation starts (which proves data
    // exists on the server), so gate it on busy-state alone — never leave it a
    // dead disabled button that eats clicks with no feedback.
    if (btnBuild) btnBuild.disabled = isBusy;
    if (btnAttach) btnAttach.disabled = isBusy;
    renderFileChips();
    maybeAutoStartConversation();
  } catch (e) {
    console.error("Error polling status", e);
  }
}

function updateUI() {
  updateStatusBadge();
  updateTopBarButtons();

  if (state.status === "running" || state.status === "waiting_approval") {
    hideFailedBanner();
    switchView(viewRunning);
    renderStepper();
    renderLogs();
  } else if (state.status === "completed") {
    hideFailedBanner();
    switchView(viewReports);
  } else if (state.status === "failed") {
    switchView(viewIdle);
    showFailedBanner(state.error);
  } else {
    hideFailedBanner();
    switchView(viewIdle);
  }
}

function showFailedBanner(errorMsg) {
  let banner = document.getElementById("failedBanner");
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "failedBanner";
    banner.className = "failed-banner";
    // Insert at top of the idle view
    viewIdle.insertBefore(banner, viewIdle.firstChild);
  }
  banner.innerHTML = `
    <span class="failed-banner-icon">✕</span>
    <span class="failed-banner-text">Last run failed${errorMsg ? ": " + errorMsg : "."}</span>
    <button class="failed-banner-logs" onclick="showLastRunLogs()">View logs</button>
    <button class="failed-banner-close" onclick="this.parentElement.remove()">✕</button>`;
}

function hideFailedBanner() {
  const banner = document.getElementById("failedBanner");
  if (banner) banner.remove();
}

window.showLastRunLogs = function () {
  switchView(viewRunning);
  renderStepper();
  renderLogs();
  if (!logConsole.innerHTML.includes("[FATAL ERROR]")) {
    logConsole.innerHTML += `\n\n<span style="color: var(--accent-red); font-weight: bold;">[FATAL ERROR] Pipeline terminated. Check logs above.</span>`;
    terminalConsole.scrollTop = terminalConsole.scrollHeight;
  }
};

function updateTopBarButtons() {
  const isDone = state.status === "completed" || state.status === "failed";
  if (btnNewRun) btnNewRun.style.display = isDone ? "inline-flex" : "none";
  if (btnHome)
    btnHome.style.display = state.status !== "idle" ? "inline-flex" : "none";
}

function switchView(targetView) {
  [viewIdle, viewRunning, viewReports].forEach((view) => {
    view.classList.toggle("active", view === targetView);
  });
}

function updateStatusBadge() {
  statusBadge.className = `status-badge ${state.status}`;
  const textEl = statusBadge.querySelector(".badge-text");
  const labels = {
    idle: "Engine Idle",
    running: "Running Crew",
    waiting_approval: "Awaiting Approval",
    completed: "Success",
    failed: "Failed",
  };
  textEl.textContent = labels[state.status] || state.status;
}

function renderStepper() {
  let activeIdx = STEP_ORDER.indexOf(state.activeStep);
  if (state.status === "completed") activeIdx = STEP_ORDER.length;

  let html = "";
  for (let i = 0; i < STEP_ORDER.length; i++) {
    const step = STEP_ORDER[i];
    const label = STEP_LABELS[step];
    let cClass = "pending",
      icon = i + 1;
    if (i < activeIdx) {
      cClass = "done";
      icon = "✓";
    } else if (i === activeIdx) {
      cClass = "active";
    }

    html += `
      <div class="stepper-step">
        <div class="step-circle ${cClass}">${icon}</div>
        <div class="step-label ${cClass}">${label}</div>
      </div>`;
    if (i < STEP_ORDER.length - 1) {
      html += `<div class="step-line ${i < activeIdx ? "done" : "pending"}"></div>`;
    }
  }
  stepper.innerHTML = html;
}

function stripAnsi(text) {
  return text
    .replace(/\x1b\[[0-9;?]*[A-LN-Zac-t]/g, "")
    .replace(/\x1b[()][AB012]/g, "");
}

function renderLogs() {
  const tailLines = state.logs.split("\n").slice(-250).join("\n");
  logConsole.innerHTML = tailLines
    ? `<pre style="margin:0;white-space:pre-wrap;word-break:break-word;">${ansiToHtml(tailLines)}</pre>`
    : "Initializing background agents environment...";
  terminalConsole.scrollTop = terminalConsole.scrollHeight;
}

function showApprovalModal(data) {
  if (approvalModal.classList.contains("active")) return;
  modalScore.textContent = data.score;
  modalSummary.textContent = data.summary;
  const color = data.score >= 60 ? "var(--accent-gold)" : "var(--accent-red)";
  modalProgressFill.style.backgroundColor = color;
  modalProgressFill.style.width = `${data.score}%`;
  modalScore.style.color = color;
  approvalModal.classList.add("active");
}

function hideApprovalModal() {
  approvalModal.classList.remove("active");
}

async function submitApproval(approved) {
  try {
    const res = await fetch("/api/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved }),
    });
    if (res.ok) {
      hideApprovalModal();
      startStatusPolling();
    }
  } catch (e) {
    console.error("Error submitting approval", e);
  }
}

async function loadReports() {
  try {
    const res = await fetch("/api/reports");
    if (!res.ok) return;
    state.reports = await res.json();
    renderKPIs();
    renderReportsTabs();
    const execRep = state.reports.find(
      (r) => r.filename === "executive_summary.md",
    );
    if (execRep && execRep.available) {
      selectReport(execRep.filename);
    } else {
      const firstAvail = state.reports.find((r) => r.available);
      if (firstAvail) selectReport(firstAvail.filename);
    }
  } catch (e) {
    console.error("Error loading reports list", e);
  }
}

async function renderKPIs() {
  let qualityScore = 0,
    revenue = "—",
    orders = "—",
    aov = "—";
  let promptTokens = "0",
    completionTokens = "0",
    totalTokens = "—";
  let currencySymbol = "$";

  try {
    const qRes = await fetch("/api/reports/quality_report.md");
    if (qRes.ok) {
      const text = (await qRes.json()).content;
      const m = text.match(/Quality\s+Score:\s*(\d+)/i);
      if (m) qualityScore = parseInt(m[1]);
    }

    const kRes = await fetch("/api/reports/kpi_report.md");
    if (kRes.ok) {
      const text = (await kRes.json()).content;
      if (text.includes("₹")) {
        currencySymbol = "₹";
      } else if (text.includes("$")) {
        currencySymbol = "$";
      }

      const cleanText = text.replace(/\*/g, "");
      const revMatch =
        cleanText.match(
          /Total\s+Revenue\s*\|?\s*[:\-]?\s*([$₹]?)\s*([\d,]+\.?\d*)/i,
        ) ||
        cleanText.match(/Revenue\s*\|?\s*[:\-]?\s*([$₹]?)\s*([\d,]+\.?\d*)/i);
      const ordMatch = cleanText.match(
        /(?:Unique\s+)?Orders\s*\|?\s*[:\-]?\s*([\d,]+)/i,
      );
      const aovMatch = cleanText.match(
        /(?:Average\s+Order\s+Value|AOV)\s*\|?\s*[:\-]?\s*([$₹]?)\s*([\d,]+\.?\d*)/i,
      );

      if (revMatch) {
        if (revMatch[1]) currencySymbol = revMatch[1];
        revenue = `${currencySymbol}${parseFloat(revMatch[2].replace(/,/g, "")).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      }
      if (ordMatch)
        orders = parseInt(ordMatch[1].replace(/,/g, "")).toLocaleString();
      if (aovMatch) {
        if (aovMatch[1]) currencySymbol = aovMatch[1];
        aov = `${currencySymbol}${parseFloat(aovMatch[2].replace(/,/g, "")).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      }
    }

    const vRes = await fetch("/api/reports/verified_metrics.json");
    if (vRes.ok) {
      try {
        const metrics = JSON.parse((await vRes.json()).content);
        const canonTable =
          metrics.canonical_revenue_table || metrics.primary_fact_table;
        if (canonTable && metrics.fact_tables[canonTable]) {
          const ftData = metrics.fact_tables[canonTable];
          if (ftData.total_revenue !== undefined) {
            revenue = `${currencySymbol}${parseFloat(ftData.total_revenue).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
          }
          if (ftData.unique_orders !== undefined) {
            orders = parseInt(ftData.unique_orders).toLocaleString();
          } else if (ftData.row_count !== undefined) {
            orders = parseInt(ftData.row_count).toLocaleString();
          }
          if (ftData.aov !== undefined) {
            aov = `${currencySymbol}${parseFloat(ftData.aov).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
          } else if (ftData.total_revenue !== undefined && orders > 0) {
            aov = `${currencySymbol}${(ftData.total_revenue / ftData.unique_orders).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
          }
        }
      } catch (err) {
        console.error("Error parsing verified_metrics.json", err);
      }
    }

    const tokRes = await fetch("/api/reports/token_usage_report.json");
    if (tokRes.ok) {
      const data = JSON.parse((await tokRes.json()).content);
      let p = 0,
        c = 0,
        t = 0;
      Object.values(data).forEach((v) => {
        p += v.prompt_tokens;
        c += v.completion_tokens;
        t += v.total_tokens;
      });
      promptTokens = p.toLocaleString();
      completionTokens = c.toLocaleString();
      totalTokens = t.toLocaleString();
    }
  } catch (err) {
    console.error("Error reading KPI values", err);
  }

  const scoreColor =
    qualityScore >= 80
      ? "var(--accent-green)"
      : qualityScore >= 60
        ? "var(--accent-gold)"
        : "var(--accent-red)";
  kpiGrid.innerHTML = `
    <div class="kpi-card">
      <div class="kpi-card-header">Data Quality Score</div>
      <div class="kpi-card-value" style="color: ${scoreColor}">${qualityScore}<span style="font-size:14px;color:var(--text-secondary);font-weight:400"> / 100</span></div>
      <div class="kpi-card-sub">Data Quality Integrity Gate Check</div>
      <div class="kpi-progress-track"><div class="kpi-progress-fill" style="width: ${qualityScore}%; background-color: ${scoreColor}"></div></div>
    </div>
    <div class="kpi-card">
      <div class="kpi-card-header">Unified E-Commerce KPI</div>
      <div class="kpi-card-value" style="color: var(--accent-green)">${revenue}</div>
      <div class="kpi-card-sub">Total Analytics-Ready Revenue</div>
      <div class="kpi-metrics-row">
        <div class="kpi-mini-metric"><span class="kpi-mini-lbl">Orders</span><span class="kpi-mini-val">${orders}</span></div>
        <div class="kpi-mini-metric"><span class="kpi-mini-lbl">Average Order Value</span><span class="kpi-mini-val">${aov}</span></div>
      </div>
    </div>
    <div class="kpi-card">
      <div class="kpi-card-header">CrewAI Orchestration Telemetry</div>
      <div class="kpi-card-value" style="color: var(--accent-cyan)">${totalTokens}</div>
      <div class="kpi-card-sub">LLM Token Usage Across 6 Agents</div>
      <div class="kpi-metrics-row">
        <div class="kpi-mini-metric"><span class="kpi-mini-lbl">Prompt Tokens</span><span class="kpi-mini-val">${promptTokens}</span></div>
        <div class="kpi-mini-metric"><span class="kpi-mini-lbl">Completion Tokens</span><span class="kpi-mini-val">${completionTokens}</span></div>
      </div>
    </div>`;
}

function renderReportsTabs() {
  const list = state.reports.filter((r) => r.available);
  if (list.length === 0) {
    reportTabs.innerHTML =
      '<span class="placeholder-text">No reports generated yet.</span>';
    return;
  }
  reportTabs.innerHTML = list
    .map((r) => {
      const isActive = r.filename === state.activeReportTab ? "active" : "";
      return `<div class="report-tab ${isActive}" onclick="selectReport('${r.filename}')">${r.label}</div>`;
    })
    .join("");
}

async function selectReport(filename) {
  state.activeReportTab = filename;
  renderReportsTabs();
  try {
    const res = await fetch(`/api/reports/${filename}`);
    if (res.ok) {
      const data = await res.json();
      renderReportContent(filename, data.content);
    }
  } catch (e) {
    console.error(`Error fetching report ${filename}`, e);
  }
}

function jsonFallback(content) {
  try {
    const parsed = JSON.parse(content);
    return `<div class="code-container"><button class="btn-copy-code" onclick="copyReportText(this)">Copy JSON</button><pre><code class="language-json">${escapeHtml(JSON.stringify(parsed, null, 2))}</code></pre></div>`;
  } catch (_) {
    return `<div class="code-container"><pre><code>${escapeHtml(content)}</code></pre></div>`;
  }
}

function renderReportContent(filename, content) {
  const ext = filename.split(".").pop().toLowerCase();
  if (ext === "sql") {
    markdownViewer.innerHTML = `<div class="code-container"><button class="btn-copy-code" onclick="copyReportText(this)">Copy Code</button><pre><code class="language-sql">${escapeHtml(content)}</code></pre></div>`;
  } else if (filename === "profiling_report.json") {
    try {
      markdownViewer.innerHTML = renderProfilingJSON(JSON.parse(content));
    } catch (_) {
      markdownViewer.innerHTML = jsonFallback(content);
    }
  } else if (filename === "token_usage_report.json") {
    try {
      markdownViewer.innerHTML = renderTokenUsageJSON(JSON.parse(content));
    } catch (_) {
      markdownViewer.innerHTML = jsonFallback(content);
    }
  } else if (filename.endsWith(".json")) {
    markdownViewer.innerHTML = jsonFallback(content);
  } else if (ext === "log" || filename === "execution.log") {
    markdownViewer.innerHTML = `<div class="code-container"><button class="btn-copy-code" onclick="copyReportText(this)">Copy Log</button><pre><code class="language-bash">${ansiToHtml(content)}</code></pre></div>`;
  } else {
    markdownViewer.innerHTML = marked.parse(content);
  }
}

window.copyReportText = function (btnEl) {
  const codeEl =
    btnEl.nextElementSibling.querySelector("code") || btnEl.nextElementSibling;
  const text = codeEl.innerText || codeEl.textContent;
  navigator.clipboard
    .writeText(text)
    .then(() => {
      const orig = btnEl.textContent;
      btnEl.textContent = "Copied!";
      btnEl.classList.add("copied");
      setTimeout(() => {
        btnEl.textContent = orig;
        btnEl.classList.remove("copied");
      }, 2000);
    })
    .catch((err) => console.error("Could not copy text:", err));
};

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function ansiToHtml(text) {
  if (!text) return "";
  let escaped = escapeHtml(text);
  escaped = escaped.replace(/\[[0-9;?]*[A-LN-Zac-t]/g, "");
  const parts = escaped.split(/\[/);
  if (parts.length === 1) return escaped;

  let result = parts[0];
  let openSpansCount = 0;

  for (let i = 1; i < parts.length; i++) {
    const part = parts[i];
    const m = part.match(/^([0-9;]*)m([\s\S]*)$/);
    if (!m) {
      result += "[" + part;
      continue;
    }
    const code = m[1],
      content = m[2];

    if (code === "0" || code === "") {
      while (openSpansCount > 0) {
        result += "</span>";
        openSpansCount--;
      }
    } else {
      const styles = [];
      for (const t of code.split(";")) {
        if (t === "1") styles.push("font-weight: bold");
        else if (t === "3") styles.push("font-style: italic");
        else if (t === "4") styles.push("text-decoration: underline");
        else if (t === "31") styles.push("color: var(--accent-red, #f43f5e)");
        else if (t === "32") styles.push("color: var(--accent-green, #10b981)");
        else if (t === "33") styles.push("color: var(--accent-gold, #fbbf24)");
        else if (t === "34") styles.push("color: #6366f1");
        else if (t === "35") styles.push("color: #d946ef");
        else if (t === "36") styles.push("color: var(--accent-cyan, #06b6d4)");
        else if (t === "37") styles.push("color: var(--text-primary, #f8fafc)");
        else if (t === "90")
          styles.push("color: var(--text-secondary, #94a3b8)");
        else if (t === "91") styles.push("color: #fb7185");
        else if (t === "92") styles.push("color: #34d399");
        else if (t === "93") styles.push("color: #fbbf24");
        else if (t === "94") styles.push("color: #818cf8");
        else if (t === "95") styles.push("color: #f472b6");
        else if (t === "96") styles.push("color: #22d3ee");
        else if (t === "97") styles.push("color: #ffffff");
      }
      if (styles.length > 0) {
        if (openSpansCount > 0) {
          result += "</span>";
          openSpansCount--;
        }
        result += `<span style="${styles.join("; ")}">`;
        openSpansCount++;
      }
    }
    result += content;
  }
  while (openSpansCount > 0) {
    result += "</span>";
    openSpansCount--;
  }
  return result;
}

function renderProfilingJSON(data) {
  const ENTITY_COLORS = {
    orders: "entity-orders",
    order_items: "entity-order-items",
    products: "entity-products",
    customers: "entity-customers",
    sessions: "entity-sessions",
    pageviews: "entity-pageviews",
    refunds: "entity-refunds",
    financials: "entity-financials",
    inventory: "entity-inventory",
    campaigns: "entity-campaigns",
    unknown: "entity-unknown",
  };
  const TYPE_CLASS = {
    integer: "type-integer",
    float: "type-float",
    date: "type-date",
    string: "type-string",
    boolean: "type-boolean",
  };
  let html = '<div class="profiling-report">';
  for (const [, profile] of Object.entries(data)) {
    if (typeof profile !== "object" || !profile || !profile.file_name) continue;
    const entity = (profile._entity || "unknown").toLowerCase();
    const entityClass = ENTITY_COLORS[entity] || "entity-unknown";
    const confidence = Math.round((profile._entity_confidence || 0) * 100);
    const anomalies = Array.isArray(profile.anomalies) ? profile.anomalies : [];
    const colRows = (profile.columns || [])
      .map((col) => {
        const typeKey = (col.datatype || "string").toLowerCase();
        const typeClass = TYPE_CLASS[typeKey] || "type-string";
        const highNull = (col.null_percentage || 0) > 20;
        return `<tr><td><code>${escapeHtml(col.name || "")}</code></td><td><span class="type-badge ${typeClass}">${escapeHtml(col.datatype || "STRING")}</span></td><td>${(col.unique_count || 0).toLocaleString()}</td><td>${(col.null_count || 0).toLocaleString()}</td><td class="${highNull ? "high-null" : ""}">${(col.null_percentage || 0).toFixed(1)}%</td></tr>`;
      })
      .join("");
    html += `<div class="profile-card">
      <div class="profile-card-header">
        <span class="profile-filename">${escapeHtml(profile.file_name)}</span>
        <span class="entity-badge ${entityClass}">${escapeHtml(profile._entity || "unknown")}</span>
        <span class="confidence-badge">${confidence}% confidence</span>
      </div>
      ${profile._entity_grain ? `<p class="profile-grain">${escapeHtml(profile._entity_grain)}</p>` : ""}
      <div class="profile-meta">
        <span>${(profile.row_count || 0).toLocaleString()} rows</span>
        <span>${profile.column_count || 0} columns</span>
        <span>${profile.duplicate_rows || 0} duplicates</span>
        <span>${escapeHtml(profile.encoding || "utf-8")}</span>
      </div>
      ${anomalies.length ? `<div class="anomaly-alert">&#9888; ${anomalies.map((a) => escapeHtml(String(a))).join(" &middot; ")}</div>` : ""}
      <table class="profile-columns-table"><thead><tr><th>Column</th><th>Type</th><th>Unique</th><th>Nulls</th><th>Null %</th></tr></thead><tbody>${colRows}</tbody></table>
    </div>`;
  }
  html += "</div>";
  return html;
}

function renderTokenUsageJSON(data) {
  let totalPrompt = 0,
    totalCompletion = 0,
    totalTokens = 0,
    totalRequests = 0;
  let rows = "";
  for (const [role, usage] of Object.entries(data)) {
    if (typeof usage !== "object" || !usage) continue;
    const p = usage.prompt_tokens || 0;
    const c = usage.completion_tokens || 0;
    const t = usage.total_tokens || 0;
    const r = usage.successful_requests || 0;
    totalPrompt += p;
    totalCompletion += c;
    totalTokens += t;
    totalRequests += r;
    rows += `<tr><td>${escapeHtml(role)}</td><td>${p.toLocaleString()}</td><td>${c.toLocaleString()}</td><td><strong>${t.toLocaleString()}</strong></td><td>${r}</td></tr>`;
  }
  return `<div class="token-report"><table class="profile-columns-table token-table"><thead><tr><th>Agent Role</th><th>Prompt Tokens</th><th>Completion Tokens</th><th>Total Tokens</th><th>Requests</th></tr></thead><tbody>${rows}</tbody><tfoot><tr class="token-total-row"><td><strong>TOTAL</strong></td><td>${totalPrompt.toLocaleString()}</td><td>${totalCompletion.toLocaleString()}</td><td><strong>${totalTokens.toLocaleString()}</strong></td><td>${totalRequests}</td></tr></tfoot></table></div>`;
}

async function checkReportsAvailability() {
  try {
    const res = await fetch("/api/reports");
    if (res.ok) {
      const reports = await res.json();
      const anyAvailable = reports.some((r) => r.available);
      if (btnViewReports)
        btnViewReports.style.display = anyAvailable ? "inline-flex" : "none";
    }
  } catch (e) {
    console.error("Error checking reports availability", e);
  }
}

function showReportsViewDirectly() {
  state.status = "completed";
  updateUI();
  loadReports();
}

function initChatPanel() {
  if (btnAsk) btnAsk.addEventListener("click", submitChatQuestion);
  if (chatQuestion) {
    chatQuestion.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        submitChatQuestion();
      }
    });
  }
}

window.fillChatInput = function (btn) {
  if (!chatQuestion) return;
  chatQuestion.value = btn.textContent;
  chatQuestion.focus();
};

function resetChatPanel() {
  if (!chatMessages) return;
  chatMessages.innerHTML = `
    <div class="chat-welcome-msg" id="chatWelcome">
      <p>Pipeline complete. Ask a question about your data.</p>
      <div class="chat-suggestion-chips">
        <button class="chat-suggest" onclick="fillChatInput(this)">What are the top products by revenue?</button>
        <button class="chat-suggest" onclick="fillChatInput(this)">Show monthly sales trends</button>
        <button class="chat-suggest" onclick="fillChatInput(this)">Which customers have the highest order value?</button>
        <button class="chat-suggest" onclick="fillChatInput(this)">What is the overall conversion rate?</button>
      </div>
    </div>`;
  if (chatQuestion) chatQuestion.value = "";
}

async function submitChatQuestion() {
  if (!chatQuestion || !chatMessages || !btnAsk) return;
  const q = chatQuestion.value.trim();
  if (!q || btnAsk.disabled) return;

  chatQuestion.value = "";
  btnAsk.disabled = true;

  // Remove welcome message on first question
  const welcome = chatMessages.querySelector(".chat-welcome-msg");
  if (welcome) welcome.remove();

  // User bubble
  const userMsg = document.createElement("div");
  userMsg.className = "chat-msg-user";
  userMsg.textContent = q;
  chatMessages.appendChild(userMsg);

  // Thinking indicator
  const thinkingMsg = document.createElement("div");
  thinkingMsg.className = "chat-msg-thinking";
  thinkingMsg.innerHTML = `Querying warehouse <div class="thinking-dots"><span></span><span></span><span></span></div>`;
  chatMessages.appendChild(thinkingMsg);
  chatMessages.scrollTop = chatMessages.scrollHeight;

  try {
    // Submit the job (returns immediately with job_id)
    const submitRes = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }),
    });

    if (!submitRes.ok) {
      const data = await submitRes.json().catch(() => ({}));
      thinkingMsg.remove();
      const errMsg = document.createElement("div");
      errMsg.className = "chat-msg-ai chat-msg-error";
      errMsg.textContent =
        data.detail || "Query failed. Try rephrasing your question.";
      chatMessages.appendChild(errMsg);
      return;
    }

    const { job_id: jobId } = await submitRes.json();

    // Poll until done or error (max 3 minutes)
    const POLL_MS = 2000;
    const MAX_POLLS = 90;
    let polls = 0;

    await new Promise((resolve) => {
      const timer = setInterval(async () => {
        polls++;
        try {
          const pollRes = await fetch(`/api/query/${jobId}`);
          const job = await pollRes.json();

          if (job.status === "done" || job.status === "error") {
            clearInterval(timer);
            thinkingMsg.remove();
            const aiMsg = document.createElement("div");
            aiMsg.className = "chat-msg-ai";
            if (job.status === "done") {
              aiMsg.innerHTML = marked.parse(
                job.answer || "No answer returned.",
              );
            } else {
              aiMsg.classList.add("chat-msg-error");
              aiMsg.textContent =
                job.answer || "Query failed. Try rephrasing your question.";
            }
            chatMessages.appendChild(aiMsg);
            resolve();
          } else if (polls >= MAX_POLLS) {
            clearInterval(timer);
            thinkingMsg.remove();
            const errMsg = document.createElement("div");
            errMsg.className = "chat-msg-ai chat-msg-error";
            errMsg.textContent =
              "Query timed out. The warehouse may be busy — please try again.";
            chatMessages.appendChild(errMsg);
            resolve();
          }
        } catch (pollErr) {
          console.error("Poll error:", pollErr);
        }
      }, POLL_MS);
    });
  } catch (e) {
    thinkingMsg.remove();
    const errMsg = document.createElement("div");
    errMsg.className = "chat-msg-ai chat-msg-error";
    errMsg.textContent = "Network error. Please try again.";
    chatMessages.appendChild(errMsg);
    console.error("Chat query error:", e);
  } finally {
    btnAsk.disabled = false;
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }
}
