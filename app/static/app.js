let state = {
  status: "idle",
  activeStep: "idle",
  logs: "",
  files: [],
  reports: [],
  activeReportTab: null,
};

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
  profiling: "Profiling",
  quality: "Quality Check",
  schema: "Schema Design",
  transformations: "SQL Compile",
  analytics: "Business KPIs",
  summarizing: "Executive Summary",
};

const statusBadge = document.getElementById("statusBadge");
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
const fileList = document.getElementById("fileList");
const btnLaunch = document.getElementById("btnLaunch");
const btnViewReports = document.getElementById("btnViewReports");
const btnReset = document.getElementById("btnReset");

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
const btnApprove = document.getElementById("btnApprove");
const btnReject = document.getElementById("btnReject");

document.addEventListener("DOMContentLoaded", () => {
  initDragAndDrop();
  initEventListeners();
  loadFiles();
  checkReportsAvailability();
  startStatusPolling(); // Check status right away to resume if already running
});

function initEventListeners() {
  btnLaunch.addEventListener("click", launchCrew);
  btnViewReports.addEventListener("click", showReportsViewDirectly);
  btnReset.addEventListener("click", resetWarehouse);

  dropzone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", handleFileSelect);

  btnApprove.addEventListener("click", () => submitApproval(true));
  btnReject.addEventListener("click", () => submitApproval(false));
}

function initDragAndDrop() {
  ["dragenter", "dragover"].forEach((eventName) => {
    dropzone.addEventListener(
      eventName,
      (e) => {
        e.preventDefault();
        dropzone.classList.add("highlight");
      },
      false,
    );
  });

  ["dragleave", "drop"].forEach((eventName) => {
    dropzone.addEventListener(
      eventName,
      (e) => {
        e.preventDefault();
        dropzone.classList.remove("highlight");
      },
      false,
    );
  });

  dropzone.addEventListener("drop", (e) => {
    const dt = e.dataTransfer;
    const files = dt.files;
    uploadFiles(files);
  });
}

function handleFileSelect(e) {
  const files = e.target.files;
  uploadFiles(files);
}

async function uploadFiles(files) {
  if (files.length === 0) return;
  const formData = new FormData();
  for (let i = 0; i < files.length; i++) {
    formData.append("files", files[i]);
  }

  try {
    const res = await fetch("/api/upload", {
      method: "POST",
      body: formData,
    });
    if (res.ok) {
      loadFiles();
    } else {
      const err = await res.json();
      alert(`Upload failed: ${err.detail}`);
    }
  } catch (e) {
    console.error("Error uploading files", e);
  }
}

async function loadFiles() {
  try {
    const res = await fetch("/api/files");
    if (res.ok) {
      state.files = await res.json();
      renderFileList();
      btnLaunch.disabled =
        state.files.length === 0 ||
        state.status === "running" ||
        state.status === "waiting_approval";
    }
  } catch (e) {
    console.error("Error loading files", e);
  }
}

function renderFileList() {
  if (state.files.length === 0) {
    fileList.innerHTML =
      '<span class="placeholder-text">No active datasets</span>';
    return;
  }

  fileList.innerHTML = state.files
    .map((f) => {
      const sizeStr =
        f.size > 1048576
          ? `${(f.size / 1048576).toFixed(1)} MB`
          : `${Math.max(Math.floor(f.size / 1024), 1)} KB`;
      const isBusy =
        state.status === "running" || state.status === "waiting_approval";
      return `
            <div class="file-item">
                <div class="file-info">
                    <span class="file-name">${f.name}</span>
                    <span class="file-size">${sizeStr}</span>
                </div>
                <button class="btn-delete" onclick="deleteFile('${f.name}')" ${isBusy ? "disabled" : ""}>✕</button>
            </div>
        `;
    })
    .join("");
}

async function deleteFile(filename) {
  try {
    const res = await fetch(`/api/files/${filename}`, {
      method: "DELETE",
    });
    if (res.ok) {
      loadFiles();
    }
  } catch (e) {
    console.error("Error deleting file", e);
  }
}

async function resetWarehouse() {
  if (
    !confirm(
      "Are you sure you want to reset the local DuckDB database? All schema tables will be wiped.",
    )
  )
    return;
  try {
    const res = await fetch("/api/reset", { method: "POST" });
    if (res.ok) {
      alert("Database warehouse reset successfully.");
    }
  } catch (e) {
    console.error("Error resetting database", e);
  }
}

async function launchCrew() {
  try {
    const res = await fetch("/api/run", { method: "POST" });
    if (res.ok) {
      startStatusPolling();
    }
  } catch (e) {
    console.error("Error starting pipeline", e);
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

      if (state.status === "completed") {
        loadReports();
      }
      checkReportsAvailability();
    }

    const isBusy =
      state.status === "running" || state.status === "waiting_approval";
    btnLaunch.disabled = isBusy || state.files.length === 0;
    btnReset.disabled = isBusy;
    loadFiles(); // reload list to disable delete buttons
  } catch (e) {
    console.error("Error polling status", e);
  }
}

function updateUI() {
  updateStatusBadge();

  if (state.status === "running" || state.status === "waiting_approval") {
    switchView(viewRunning);
    renderStepper();
    renderLogs();
  } else if (state.status === "completed") {
    switchView(viewReports);
  } else if (state.status === "failed") {
    switchView(viewRunning);
    renderStepper();
    renderLogs();
    logConsole.innerHTML += `\n\n[FATAL ERROR] Pipeline terminated with execution errors. Check logs above.`;
  } else {
    switchView(viewIdle);
  }
}

function switchView(targetView) {
  [viewIdle, viewRunning, viewReports].forEach((view) => {
    if (view === targetView) {
      view.classList.add("active");
    } else {
      view.classList.remove("active");
    }
  });
}

function updateStatusBadge() {
  statusBadge.className = `status-badge ${state.status}`;
  const textEl = statusBadge.querySelector(".badge-text");

  if (state.status === "idle") textEl.textContent = "Engine Idle";
  else if (state.status === "running") textEl.textContent = "Running Crew";
  else if (state.status === "waiting_approval")
    textEl.textContent = "Awaiting Approval";
  else if (state.status === "completed") textEl.textContent = "Success";
  else if (state.status === "failed") textEl.textContent = "Failed";
}

function renderStepper() {
  let activeIdx = STEP_ORDER.indexOf(state.activeStep);
  if (state.status === "completed") activeIdx = STEP_ORDER.length;

  let html = "";
  for (let i = 0; i < STEP_ORDER.length; i++) {
    const step = STEP_ORDER[i];
    const label = STEP_LABELS[step];
    const stepNum = i + 1;

    let cClass = "pending";
    let icon = stepNum;
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
            </div>
        `;
    if (i < STEP_ORDER.length - 1) {
      const lineClass = i < activeIdx ? "done" : "pending";
      html += `<div class="step-line ${lineClass}"></div>`;
    }
  }
  stepper.innerHTML = html;
}

function renderLogs() {
  const tailLines = state.logs.split("\n").slice(-250).join("\n");
  logConsole.innerHTML =
    ansiToHtml(tailLines) || "Initializing background agents environment...";
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
    } else if (state.reports.length > 0) {
      const firstAvail = state.reports.find((r) => r.available);
      if (firstAvail) selectReport(firstAvail.filename);
    }
  } catch (e) {
    console.error("Error loading reports list", e);
  }
}

async function renderKPIs() {
  let qualityScore = 0;
  let revenue = "—";
  let orders = "—";
  let aov = "—";
  let promptTokens = "0";
  let completionTokens = "0";
  let totalTokens = "—";
  let apiRequests = "—";

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
      const revMatch =
        text.match(/Total\s+Revenue[:\s]+\$?([\d,]+\.?\d*)/i) ||
        text.match(/Revenue[:\s]+\$?([\d,]+\.?\d*)/i);
      const ordMatch = text.match(/(?:Unique\s+)?Orders[:\s]+([\d,]+)/i);
      const aovMatch = text.match(
        /(?:Average\s+Order\s+Value|AOV)[:\s]+\$?([\d,]+\.?\d*)/i,
      );

      if (revMatch)
        revenue = `$${parseFloat(revMatch[1].replace(/,/g, "")).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      if (ordMatch)
        orders = parseInt(ordMatch[1].replace(/,/g, "")).toLocaleString();
      if (aovMatch)
        aov = `$${parseFloat(aovMatch[1].replace(/,/g, "")).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }

    // Token details from json
    const tokRes = await fetch("/api/reports/token_usage_report.json");
    if (tokRes.ok) {
      const data = JSON.parse((await tokRes.json()).content);
      let p = 0,
        c = 0,
        t = 0,
        r = 0;
      Object.values(data).forEach((v) => {
        p += v.prompt_tokens;
        c += v.completion_tokens;
        t += v.total_tokens;
        r += v.successful_requests;
      });
      promptTokens = p.toLocaleString();
      completionTokens = c.toLocaleString();
      totalTokens = t.toLocaleString();
      apiRequests = r.toLocaleString();
    }
  } catch (err) {
    console.error("Error reading KPI values from files", err);
  }

  const scoreColor =
    qualityScore >= 80
      ? "var(--accent-green)"
      : qualityScore >= 60
        ? "var(--accent-gold)"
        : "var(--accent-red)";

  kpiGrid.innerHTML = `
        <div class="kpi-card">
            <div class="kpi-card-header">Data Quality Audit</div>
            <div class="kpi-card-value" style="color: ${scoreColor}">${qualityScore}<span style="font-size:14px;color:var(--text-secondary);font-weight:400"> / 100</span></div>
            <div class="kpi-progress-track">
                <div class="kpi-progress-fill" style="width: ${qualityScore}%; background-color: ${scoreColor}"></div>
            </div>
        </div>
        <div class="kpi-card">
            <div class="kpi-card-header">E-Commerce Metrics</div>
            <div class="kpi-card-value" style="color: var(--accent-green)">${revenue}</div>
            <div class="kpi-card-sub">Total Unified Revenue</div>
            <div class="kpi-metrics-row">
                <div class="kpi-mini-metric">
                    <span class="kpi-mini-lbl">Orders</span>
                    <span class="kpi-mini-val">${orders}</span>
                </div>
                <div class="kpi-mini-metric">
                    <span class="kpi-mini-lbl">AOV</span>
                    <span class="kpi-mini-val">${aov}</span>
                </div>
            </div>
        </div>
        <div class="kpi-card">
            <div class="kpi-card-header">CrewAI Telemetry</div>
            <div class="kpi-card-value" style="color: var(--accent-cyan)">${totalTokens}</div>
            <div class="kpi-card-sub">Total Tokens Used · ${apiRequests} requests</div>
            <div class="kpi-metrics-row">
                <div class="kpi-mini-metric">
                    <span class="kpi-mini-lbl">Prompt</span>
                    <span class="kpi-mini-val">${promptTokens}</span>
                </div>
                <div class="kpi-mini-metric">
                    <span class="kpi-mini-lbl">Completion</span>
                    <span class="kpi-mini-val">${completionTokens}</span>
                </div>
            </div>
        </div>
    `;
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
      return `
            <div class="report-tab ${isActive}" onclick="selectReport('${r.filename}')">
                ${r.label}
            </div>
        `;
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
    console.error(`Error fetching content of report ${filename}`, e);
  }
}

function renderReportContent(filename, content) {
  const ext = filename.split(".").pop().toLowerCase();

  if (ext === "sql") {
    markdownViewer.innerHTML = `<pre><code class="language-sql">${escapeHtml(content)}</code></pre>`;
  } else if (filename.endsWith(".json")) {
    try {
      const parsed = JSON.parse(content);
      markdownViewer.innerHTML = `<pre><code class="language-json">${escapeHtml(JSON.stringify(parsed, null, 2))}</code></pre>`;
    } catch (e) {
      markdownViewer.innerHTML = `<pre><code>${escapeHtml(content)}</code></pre>`;
    }
  } else if (ext === "log" || filename === "execution.log") {
    markdownViewer.innerHTML = `<pre><code class="language-bash">${ansiToHtml(content)}</code></pre>`;
  } else {
    markdownViewer.innerHTML = parseMarkdown(content);
  }
}

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

  escaped = escaped.replace(/\u001b\[[0-9;?]*[A-LN-Zac-t]/g, "");

  const parts = escaped.split(/\u001b\[/);
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

    const code = m[1];
    const content = m[2];

    if (code === "0" || code === "") {
      while (openSpansCount > 0) {
        result += "</span>";
        openSpansCount--;
      }
    } else {
      const styles = [];
      const tokens = code.split(";");

      for (let t of tokens) {
        if (t === "1") {
          styles.push("font-weight: bold");
        } else if (t === "3") {
          styles.push("font-style: italic");
        } else if (t === "4") {
          styles.push("text-decoration: underline");
        } else if (t === "30") {
          styles.push("color: #1e293b");
        } else if (t === "31") {
          styles.push("color: var(--accent-red, #ef4444)");
        } else if (t === "32") {
          styles.push("color: var(--accent-green, #10b981)");
        } else if (t === "33") {
          styles.push("color: var(--accent-gold, #fbbf24)");
        } else if (t === "34") {
          styles.push("color: var(--accent-blue, #3b82f6)");
        } else if (t === "35") {
          styles.push("color: var(--accent-magenta, #d946ef)");
        } else if (t === "36") {
          styles.push("color: var(--accent-cyan, #06b6d4)");
        } else if (t === "37") {
          styles.push("color: var(--text-primary, #f3f4f6)");
        } else if (t === "90") {
          styles.push("color: var(--text-secondary, #8b949e)");
        } else if (t === "91") {
          styles.push("color: #f87171");
        } else if (t === "92") {
          styles.push("color: #34d399");
        } else if (t === "93") {
          styles.push("color: #fbbf24");
        } else if (t === "94") {
          styles.push("color: #60a5fa");
        } else if (t === "95") {
          styles.push("color: #f472b6");
        } else if (t === "96") {
          styles.push("color: #22d3ee");
        } else if (t === "97") {
          styles.push("color: #ffffff");
        }
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

function parseMarkdown(md) {
  let html = md;

  html = html.replace(/^# (.*?)$/gm, "<h1>$1</h1>");
  html = html.replace(/^## (.*?)$/gm, "<h2>$1</h2>");
  html = html.replace(/^### (.*?)$/gm, "<h3>$1</h3>");

  html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");

  const lines = html.split("\n");
  let inTable = false;
  let tableHtml = "";

  for (let i = 0; i < lines.length; i++) {
    let line = lines[i].trim();
    if (line.startsWith("|") && line.endsWith("|")) {
      if (!inTable) {
        inTable = true;
        tableHtml = "<table>";
      }

      // Skip markdown alignment lines (e.g. | :--- | :--- |)
      if (line.includes(":---") || line.includes("---:")) {
        continue;
      }

      const cells = line
        .split("|")
        .slice(1, -1)
        .map((c) => c.trim());
      const tag = tableHtml.includes("</th>") ? "td" : "th";
      tableHtml +=
        "<tr>" + cells.map((c) => `<${tag}>${c}</${tag}>`).join("") + "</tr>";
    } else {
      if (inTable) {
        inTable = false;
        tableHtml += "</table>";
        // Replace the preceding markdown lines with tableHtml
        let count = 0;
        let j = i - 1;
        while (
          j >= 0 &&
          lines[j].trim().startsWith("|") &&
          lines[j].trim().endsWith("|")
        ) {
          lines[j] = "";
          j--;
        }
        lines[j + 1] = tableHtml;
      }
    }
  }
  html = lines.join("\n");

  html = html.replace(/^\*\s+(.*?)$/gm, "<ul><li>$1</li></ul>");
  html = html.replace(/<\/ul>\s*<ul>/g, ""); // consolidate lists

  html = html.replace(/^>\s+(.*?)$/gm, "<blockquote>$1</blockquote>");

  html = html.replace(
    /```(.*?)\n([\s\S]*?)```/g,
    '<pre><code class="language-$1">$2</code></pre>',
  );

  html = html.replace(/`(.*?)`/g, "<code>$1</code>");

  html = html.replace(/\n\n/g, "<br><br>");

  return html;
}

async function checkReportsAvailability() {
  try {
    const res = await fetch('/api/reports');
    if (res.ok) {
      const reports = await res.json();
      const anyAvailable = reports.some(r => r.available);
      if (anyAvailable) {
        btnViewReports.style.display = 'block';
      } else {
        btnViewReports.style.display = 'none';
      }
    }
  } catch (e) {
    console.error("Error checking reports availability", e);
  }
}

function showReportsViewDirectly() {
  state.status = 'completed';
  updateUI();
  loadReports();
}
