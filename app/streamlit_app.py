import os
import sys
import re
import json
import threading
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()
os.environ["OTEL_SDK_DISABLED"] = "true"
os.environ["CREWAI_TELEMETRY_OPT_OUT"] = "true"

import streamlit as st
from tools import HumanLoopService, WebApprovalStrategy

DATA_DIR    = "data"
REPORTS_DIR = "reports"
_EXTS       = (".csv", ".xlsx", ".xls", ".json")

# ── Global CSS (enhancement only — all structure uses native st.* components) ──
_CSS = """
<style>
/* Page chrome */
[data-testid="stAppViewContainer"] > .main { background: #0d1117; }
[data-testid="stHeader"]           { background: #0d1117!important; border-bottom: 1px solid #21262d; }
[data-testid="stSidebar"]          { background: #010409!important; border-right: 1px solid #21262d; }
section[data-testid="stSidebar"] * { color: #e6edf3!important; }

/* Sidebar uploader */
[data-testid="stFileUploader"] {
    background: #161b22; border: 1px dashed #30363d;
    border-radius: 8px; padding: 4px;
}
[data-testid="stFileUploader"]:hover { border-color: #58a6ff; }

/* Primary button */
button[kind="primary"] {
    background: #1f6feb!important; border: none!important;
    color: #fff!important; font-weight: 600!important;
    border-radius: 6px!important;
}
button[kind="primary"]:hover { background: #388bfd!important; }

/* Tabs */
[data-testid="stTabs"] button[role="tab"] {
    color: #8b949e!important; font-size: 13px!important;
}
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    color: #58a6ff!important; border-bottom-color: #58a6ff!important;
}

/* Code blocks */
[data-testid="stCode"] pre, .stCode pre {
    font-family: 'JetBrains Mono', monospace!important;
    font-size: 12px!important; background: #010409!important;
}

/* Metric labels */
[data-testid="stMetric"] label { color: #8b949e!important; font-size:11px!important; text-transform: uppercase; letter-spacing: .5px; }
[data-testid="stMetricValue"]  { color: #f0f6fc!important; }

/* Scrollbar */
::-webkit-scrollbar       { width:5px; height:5px; }
::-webkit-scrollbar-track { background:#0d1117; }
::-webkit-scrollbar-thumb { background:#30363d; border-radius:3px; }

/* ── Custom components ───────────────────────────────────── */

.adep-stepper {
    display:flex; align-items:center; padding:24px 0 8px;
}
.adep-step {
    display:flex; flex-direction:column; align-items:center;
    flex:1; gap:7px; position:relative; z-index:1;
}
.adep-circle {
    width:34px; height:34px; border-radius:50%;
    display:flex; align-items:center; justify-content:center;
    font-size:12px; font-weight:700; border:2px solid;
    font-family:'JetBrains Mono',monospace;
}
.adep-circle.done    { background:#0d2a0d; border-color:#3fb950; color:#3fb950; }
.adep-circle.active  { background:#0d2044; border-color:#58a6ff; color:#58a6ff;
                        box-shadow:0 0 14px rgba(88,166,255,.35); }
.adep-circle.pending { background:#161b22; border-color:#30363d; color:#484f58; }
.adep-step-lbl { font-size:11px; font-weight:500; text-align:center; }
.adep-step-lbl.done    { color:#3fb950; }
.adep-step-lbl.active  { color:#58a6ff; }
.adep-step-lbl.pending { color:#484f58; }
.adep-line { flex:1; height:2px; margin-top:-23px; }
.adep-line.done    { background:#3fb950; }
.adep-line.pending { background:#21262d; }

@keyframes adep-pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.5;transform:scale(1.35)} }
.adep-circle.active { animation: adep-pulse 1.5s ease-in-out infinite; }

.adep-kpi-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin-bottom:16px; }
.adep-kpi {
    background:#161b22; border:1px solid #21262d; border-radius:10px; padding:20px 22px;
}
.adep-kpi-lbl  { font-size:11px; font-weight:600; color:#8b949e; text-transform:uppercase;
                  letter-spacing:.6px; margin-bottom:14px; }
.adep-kpi-val  { font-size:24px; font-weight:700; font-family:'JetBrains Mono',monospace; }
.adep-kpi-sub  { font-size:11px; color:#8b949e; margin-top:4px; }
.adep-kpi-row  { display:flex; gap:24px; margin-top:10px; }
.adep-kpi-mini .adep-kpi-val { font-size:18px; }
.adep-kpi-mini .adep-kpi-lbl { margin-bottom:4px; }

.adep-qual-bar { height:5px; background:#21262d; border-radius:3px; margin-top:10px; }
.adep-qual-fill { height:100%; border-radius:3px; transition:width .5s ease; }

</style>
"""


# ── State machine ──────────────────────────────────────────────────────────────

class LogBuffer:
    def __init__(self):
        self._lines: List[str] = []
        self._lock  = threading.Lock()

    def write(self, s: str) -> None:
        with self._lock:
            self._lines.append(s)
        if sys.__stdout__ is not None:
            sys.__stdout__.write(s)

    def flush(self) -> None:
        if sys.__stdout__ is not None:
            sys.__stdout__.flush()

    def get(self) -> str:
        with self._lock:
            return "".join(self._lines)

    def clear(self) -> None:
        with self._lock:
            self._lines = []


class RunManager:
    _STEP_MARKERS = [
        ("Compiling final executive summaries...", "summarizing"),
        ("Compiling business insights...",         "analytics"),
        ("Planning transformations...",            "transformations"),
        ("Designing schema...",                    "schema"),
        ("Assessing data quality...",              "quality"),
        ("Starting data profiling...",             "profiling"),
    ]

    def __init__(self):
        self._status: str = "idle"
        self._error:  Optional[str] = None
        self._approval_data:     Optional[dict] = None
        self._approval_decision: Optional[bool] = None
        self._approval_event = threading.Event()
        self._lock = threading.Lock()
        self.log   = LogBuffer()

    @property
    def status(self) -> str:
        return self._status

    def is_busy(self) -> bool:
        with self._lock:
            return self._status in ("running", "waiting_approval")

    def start(self) -> None:
        with self._lock:
            self._status = "running"
            self._error  = None
            self._approval_data     = None
            self._approval_decision = None
        self.log.clear()

    def complete(self) -> None:
        with self._lock:
            self._status = "completed"

    def fail(self, error: str) -> None:
        with self._lock:
            self._status = "failed"
            self._error  = error

    def request_approval(self, score: float, summary: str) -> bool:
        with self._lock:
            self._status = "waiting_approval"
            self._approval_data = {"score": score, "summary": summary}
        self._approval_event.clear()
        self._approval_event.wait()
        with self._lock:
            decision = bool(self._approval_decision)
            self._status        = "running"
            self._approval_data = None
            self._approval_decision = None
        return decision

    def submit_approval(self, approved: bool) -> None:
        with self._lock:
            self._approval_decision = approved
        self._approval_event.set()

    def _resolve_step(self, logs: str) -> str:
        for marker, step_name in self._STEP_MARKERS:
            if marker in logs:
                return step_name
        with self._lock:
            if self._status == "completed":
                return "finished"
            if self._status == "failed":
                return "failed"
        return "idle"

    def get_state(self) -> dict:
        logs = self.log.get()
        with self._lock:
            return {
                "status":        self._status,
                "error":         self._error,
                "active_step":   self._resolve_step(logs),
                "logs":          logs,
                "approval_data": self._approval_data,
            }


@st.cache_resource
def _get_run_manager() -> RunManager:
    mgr = RunManager()
    HumanLoopService.set_strategy(
        WebApprovalStrategy(lambda score, summary: mgr.request_approval(score, summary))
    )
    return mgr


def _run_pipeline(mgr: RunManager) -> None:
    from flow import DataEngineeringFlow  # deferred import — avoids CrewAI init on Streamlit startup
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = mgr.log
    sys.stderr = mgr.log
    try:
        DataEngineeringFlow().kickoff()
        mgr.complete()
    except SystemExit:
        mgr.fail("Pipeline halted — operator rejected the quality approval.")
    except Exception as e:
        mgr.fail(str(e))
        print(f"\n[Fatal] {e}\n")
    finally:
        sys.stdout = old_out
        sys.stderr = old_err


# ── Helpers ────────────────────────────────────────────────────────────────────

def _data_files() -> List[str]:
    if not os.path.exists(DATA_DIR):
        return []
    return sorted(
        f for f in os.listdir(DATA_DIR)
        if f.endswith(_EXTS) and os.path.isfile(os.path.join(DATA_DIR, f))
    )


def _read_report(name: str) -> str:
    path = os.path.join(REPORTS_DIR, name)
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _quality_score() -> int:
    m = re.search(r"Quality\s+Score:\s*(\d+)", _read_report("quality_report.md"), re.I)
    return int(m.group(1)) if m else 0


def _kpi_metrics():
    c = _read_report("kpi_report.md")
    rev = (re.search(r"Total\s+Revenue[:\s]+\$?([\d,]+\.?\d*)", c, re.I)
           or re.search(r"Revenue[:\s]+\$?([\d,]+\.?\d*)", c, re.I)
           or re.search(r"Gross\s+(?:Sales|Revenue|Amount)[:\s]+\$?([\d,]+\.?\d*)", c, re.I))
    ord_ = (re.search(r"(?:Unique\s+)?Orders[:\s]+([\d,]+)", c, re.I)
            or re.search(r"(?:Total\s+)?Transactions[:\s]+([\d,]+)", c, re.I))
    aov  = (re.search(r"(?:Average\s+Order\s+Value|AOV)[:\s]+\$?([\d,]+\.?\d*)", c, re.I)
            or re.search(r"Avg(?:erage)?\s+(?:Transaction|Order)\s+Value[:\s]+\$?([\d,]+\.?\d*)", c, re.I))
    m_  = lambda m, fmt: fmt.format(float(m.group(1).replace(",", ""))) if m else "—"
    return (m_(rev,  "${:,.2f}"),
            m_(ord_, "{:,.0f}").replace(".0", ""),
            m_(aov,  "${:,.2f}"))


def _token_metrics():
    try:
        data = json.loads(_read_report("token_usage_report.json"))
        p = sum(v["prompt_tokens"]       for v in data.values())
        c = sum(v["completion_tokens"]   for v in data.values())
        t = sum(v["total_tokens"]        for v in data.values())
        r = sum(v["successful_requests"] for v in data.values())
        return f"{t:,}", f"{r:,}", f"{p:,}", f"{c:,}"
    except Exception:
        return "—", "—", "—", "—"


# ── Custom HTML builders ───────────────────────────────────────────────────────

def _stepper_html(active_step: str, status: str) -> str:
    STEPS = [("profiling","1","Profiling"), ("quality","2","Quality"),
             ("schema","3","Schema"), ("transformations","4","SQL"),
             ("analytics","5","KPIs"), ("summarizing","6","Summary")]
    order = [s[0] for s in STEPS]
    try:
        ai = order.index(active_step)
    except ValueError:
        ai = len(order) if status in ("completed", "finished") else -1

    parts = []
    for i, (_, num, label) in enumerate(STEPS):
        s = "done" if i < ai else ("active" if i == ai else "pending")
        icon = "✓" if s == "done" else num
        parts.append(
            f'<div class="adep-step">'
            f'<div class="adep-circle {s}">{icon}</div>'
            f'<div class="adep-step-lbl {s}">{label}</div></div>'
        )
        if i < len(STEPS) - 1:
            lc = "done" if i < ai else "pending"
            parts.append(f'<div class="adep-line {lc}"></div>')
    return f'<div class="adep-stepper">{"".join(parts)}</div>'


def _kpi_cards_html(score, rev, orders, aov, tokens, reqs, prompt, compl) -> str:
    bar_col = "#3fb950" if score >= 80 else "#e3b341" if score >= 60 else "#f85149"
    return f"""
<div class="adep-kpi-grid">
  <div class="adep-kpi">
    <div class="adep-kpi-lbl">Data Quality</div>
    <div class="adep-kpi-val" style="color:{bar_col}">{score}<span style="font-size:14px;color:#8b949e;font-weight:400"> /100</span></div>
    <div class="adep-qual-bar"><div class="adep-qual-fill" style="width:{score}%;background:{bar_col}"></div></div>
  </div>
  <div class="adep-kpi">
    <div class="adep-kpi-lbl">Business KPIs</div>
    <div class="adep-kpi-val" style="color:#3fb950">{rev}</div>
    <div class="adep-kpi-sub">Total Revenue</div>
    <div class="adep-kpi-row">
      <div class="adep-kpi-mini">
        <div class="adep-kpi-lbl">Orders</div>
        <div class="adep-kpi-val">{orders}</div>
      </div>
      <div class="adep-kpi-mini">
        <div class="adep-kpi-lbl">AOV</div>
        <div class="adep-kpi-val">{aov}</div>
      </div>
    </div>
  </div>
  <div class="adep-kpi">
    <div class="adep-kpi-lbl">LLM Usage</div>
    <div class="adep-kpi-val" style="color:#58a6ff">{tokens}</div>
    <div class="adep-kpi-sub">Total tokens · {reqs} requests</div>
    <div class="adep-kpi-row">
      <div class="adep-kpi-mini">
        <div class="adep-kpi-lbl">Prompt</div>
        <div class="adep-kpi-val">{prompt}</div>
      </div>
      <div class="adep-kpi-mini">
        <div class="adep-kpi-lbl">Completion</div>
        <div class="adep-kpi-val">{compl}</div>
      </div>
    </div>
  </div>
</div>"""


# ── Dialog ─────────────────────────────────────────────────────────────────────

@st.dialog("Quality Gate — Human Approval Required", width="large")
def _approval_dialog(data: dict) -> None:
    mgr = _get_run_manager()
    score     = data["score"]
    bar_color = "#e3b341" if score >= 60 else "#f85149"
    st.markdown(f"""
<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;margin-bottom:16px">
  <div style="display:flex;align-items:center;gap:16px">
    <div style="font-size:40px;font-weight:700;color:{bar_color};font-family:'JetBrains Mono',monospace">{score}</div>
    <div>
      <div style="font-size:13px;color:#8b949e">Quality Score / 100</div>
      <div style="font-size:12px;color:#8b949e;margin-top:2px">Threshold: 80</div>
    </div>
  </div>
  <div style="height:5px;background:#21262d;border-radius:3px;margin-top:14px">
    <div style="height:100%;width:{score}%;background:{bar_color};border-radius:3px"></div>
  </div>
</div>""", unsafe_allow_html=True)
    st.markdown("**Identified issues:**")
    st.code(data["summary"], language="text")
    st.caption("Approve to continue with schema design and SQL, or reject to halt the pipeline.")
    c1, c2 = st.columns(2)
    if c1.button("✓ Approve & Proceed", type="primary", use_container_width=True):
        mgr.submit_approval(True)
        st.rerun()
    if c2.button("✕ Reject & Halt", use_container_width=True):
        mgr.submit_approval(False)
        st.rerun()


# ── Live panel ─────────────────────────────────────────────────────────────────

@st.fragment(run_every=1)
def _live_panel() -> None:
    mgr    = _get_run_manager()
    state  = mgr.get_state()
    status = state["status"]
    logs   = state["logs"] or ""

    if status in ("running", "waiting_approval"):
        label = "Awaiting your approval…" if status == "waiting_approval" else "Pipeline running…"
        with st.status(label, expanded=True, state="running"):
            st.markdown(_stepper_html(state["active_step"], status), unsafe_allow_html=True)
            if logs:
                # Show last 200 lines so the box doesn't grow unbounded
                tail = "\n".join(logs.splitlines()[-200:])
                st.code(tail, language="bash")
            else:
                st.caption("Starting up — importing CrewAI pipeline (this takes ~10 s)…")
        if status == "waiting_approval" and state["approval_data"]:
            _approval_dialog(state["approval_data"])

    elif status == "completed":
        st.success("Pipeline completed successfully.")
        st.markdown(_stepper_html("finished", "completed"), unsafe_allow_html=True)
        st.divider()
        score               = _quality_score()
        rev, orders, aov    = _kpi_metrics()
        tok, reqs, p, c     = _token_metrics()
        st.markdown(_kpi_cards_html(score, rev, orders, aov, tok, reqs, p, c), unsafe_allow_html=True)
        _render_reports()

    elif status == "failed":
        err = state["error"] or "An unexpected error occurred."
        st.error(f"**Pipeline failed:** {err}")
        with st.expander("Full log", expanded=True):
            st.code(logs or "(no output captured)", language="bash")

    else:  # idle
        st.info("Upload datasets in the sidebar, then click **Launch AI Crew** to start the pipeline.")


def _render_reports() -> None:
    REPORTS = [
        ("Executive Summary", "executive_summary.md",  "md"),
        ("KPIs & Insights",   "kpi_report.md",         "md"),
        ("SQL Script",        "transformations.sql",   "sql"),
        ("Star Schema",       "schema_design.md",      "md"),
        ("Quality Report",    "quality_report.md",     "md"),
        ("Data Profile",      "profiling_report.json", "json"),
    ]
    tabs = st.tabs([r[0] for r in REPORTS])
    for tab, (_, fname, ftype) in zip(tabs, REPORTS):
        with tab:
            content = _read_report(fname)
            if not content:
                st.caption(f"{fname} not yet generated.")
            elif ftype == "sql":
                st.code(content, language="sql")
            elif ftype == "json":
                try:
                    st.json(json.loads(content))
                except Exception:
                    st.code(content)
            else:
                st.markdown(content)


# ── Sidebar ────────────────────────────────────────────────────────────────────

def _sidebar() -> None:
    mgr   = _get_run_manager()
    busy  = mgr.is_busy()
    files = _data_files()

    with st.sidebar:
        st.markdown("## 🌊 ADEP Crew")
        st.caption("v1.1.0 · Polars Engine · CrewAI")
        st.divider()

        st.markdown("**Upload Datasets**")
        uploaded = st.file_uploader(
            "Drop CSV / Excel / JSON",
            type=["csv", "xlsx", "xls", "json"],
            accept_multiple_files=True,
            label_visibility="collapsed",
            disabled=busy,
        )
        if uploaded:
            os.makedirs(DATA_DIR, exist_ok=True)
            for f in uploaded:
                with open(os.path.join(DATA_DIR, f.name), "wb") as out:
                    out.write(f.getbuffer())
            st.rerun()

        st.markdown("**Active Datasets**")
        if files:
            for fname in files:
                size  = os.path.getsize(os.path.join(DATA_DIR, fname))
                label = (f"{size/1_048_576:.1f} MB" if size > 1_048_576
                         else f"{max(size//1024, 1)} KB")
                c1, c2 = st.columns([0.82, 0.18])
                c1.markdown(
                    f"<div style='font-size:12px;padding:5px 0'>"
                    f"<b>{fname}</b><br>"
                    f"<span style='color:#8b949e;font-size:11px'>{label}</span></div>",
                    unsafe_allow_html=True,
                )
                if c2.button("✕", key=f"del_{fname}", disabled=busy, help=f"Remove {fname}"):
                    os.remove(os.path.join(DATA_DIR, fname))
                    st.rerun()
        else:
            st.caption("No datasets found in `data/`.")

        st.divider()
        if st.button("🚀 Launch AI Crew", type="primary",
                     disabled=busy or not files, use_container_width=True):
            mgr.start()
            threading.Thread(target=_run_pipeline, args=(mgr,), daemon=True).start()
            st.toast("Pipeline started — importing agents…", icon="🚀")
            st.rerun()

        if busy:
            st.caption("Pipeline is running…")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(
        page_title="ADEP Crew",
        page_icon="🌊",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(_CSS, unsafe_allow_html=True)

    _sidebar()

    st.title("Pipeline Dashboard")
    st.caption("Autonomous Data Engineering Platform")

    _live_panel()


main()
