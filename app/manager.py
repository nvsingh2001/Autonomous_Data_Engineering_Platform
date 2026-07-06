import os
import re
import sys
import threading
from tools import HumanLoopService, WebApprovalStrategy
from pipeline.core import new_thread_id

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_FLOW_PREFIX = "[Flow]"


REPORTS_DIR = "reports"


class RunManager:
    _STEP_MARKERS = [
        ("Compiling final executive summaries...", "summarizing"),
        ("Compiling business insights...", "analytics"),
        ("Planning transformations...", "transformations"),
        ("Designing schema...", "schema"),
        ("Assessing data quality...", "quality"),
        ("Starting data profiling...", "profiling"),
    ]

    def __init__(self):
        self.status = "idle"  # idle, running, waiting_approval, completed, failed
        self.error = None
        self.active_step = "idle"
        self.approval_data = None
        self.approval_decision = None
        self.approval_event = threading.Event()
        self.log_buffer = []
        self._lock = threading.Lock()
        self._chat_lock = threading.Lock()
        self.instructions = ""
        self.warehouse_db_path = ""
        self.entity_map: dict = {}
        self.chat_jobs: dict = {}
        self.intent_history: list[dict] = []
        self.business_intent: dict = {}
        self.intent_thread_id = ""
        self.chat_thread_id = ""

    def start(self):
        with self._lock:
            self.status = "running"
            self.error = None
            self.active_step = "profiling"
            self.approval_data = None
            self.approval_decision = None
            self.log_buffer = []
            self.approval_event.clear()
            self.instructions = ""
            self.warehouse_db_path = ""
            self.entity_map = {}
            self.chat_thread_id = ""

            log_path = os.path.join(REPORTS_DIR, "execution.log")
            try:
                if os.path.exists(log_path):
                    os.remove(log_path)
            except Exception:
                pass

    def begin_intent_thread(self) -> str:
        """A fresh intake conversation starts a fresh LangSmith thread."""
        self.intent_thread_id = new_thread_id("intent")
        return self.intent_thread_id

    def ensure_intent_thread(self) -> str:
        """The current intent conversation's thread, created if the conversation
        was started without /api/intent/start."""
        if not self.intent_thread_id:
            self.intent_thread_id = new_thread_id("intent")
        return self.intent_thread_id

    def ensure_chat_thread(self) -> str:
        """The Q&A thread for the current warehouse build — all queries against one
        build share it; start() clears it so the next build gets a new one."""
        if not self.chat_thread_id:
            self.chat_thread_id = new_thread_id("warehouse-chat")
        return self.chat_thread_id

    def complete(self):
        with self._lock:
            self.status = "completed"
            self.active_step = "finished"

    def fail(self, error: str):
        with self._lock:
            self.status = "failed"
            self.active_step = "failed"
            self.error = error

    def write_log(self, s: str):
        with self._lock:
            self.log_buffer.append(s)
            for marker, step_name in self._STEP_MARKERS:
                if marker in s:
                    self.active_step = step_name

            try:
                log_path = os.path.join(REPORTS_DIR, "execution.log")
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(s)
            except Exception:
                pass

    def request_approval(self, score: float, summary: str) -> bool:
        with self._lock:
            self.status = "waiting_approval"
            self.approval_data = {"score": score, "summary": summary}
            self.approval_decision = None
        self.approval_event.clear()
        self.approval_event.wait()
        with self._lock:
            decision = bool(self.approval_decision)
            self.status = "running"
            self.approval_data = None
        return decision

    def submit_decision(self, approved: bool):
        with self._lock:
            self.approval_decision = approved
        self.approval_event.set()

    def _activity(self, limit: int = 40) -> list:
        """The pipeline's own narration only — the `[Flow] …` lines — with ANSI and the
        prefix stripped. Deliberately excludes raw agent output (prompts, reasoning, tool
        I/O), so the UI can show what's happening without exposing how the system works."""
        out: list[str] = []
        for line in "".join(self.log_buffer).splitlines():
            if _FLOW_PREFIX not in line:
                continue
            clean = _ANSI_RE.sub("", line)
            msg = clean[clean.find(_FLOW_PREFIX) + len(_FLOW_PREFIX) :].strip()
            if msg:
                out.append(msg)
        return out[-limit:]

    def get_state(self) -> dict:
        with self._lock:
            return {
                "status": self.status,
                "error": self.error,
                "active_step": self.active_step,
                "activity": self._activity(),
                "approval_data": self.approval_data,
            }


mgr = RunManager()
HumanLoopService.set_strategy(
    WebApprovalStrategy(lambda score, summary: mgr.request_approval(score, summary))
)


class IOStreamRedirector:
    def __init__(self, manager: RunManager):
        self.manager = manager
        self.old_stdout = sys.stdout
        self.old_stderr = sys.stderr

    def __enter__(self):
        sys.stdout = self
        sys.stderr = self
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self.old_stdout
        sys.stderr = self.old_stderr

    def write(self, s):
        self.manager.write_log(s)
        self.old_stdout.write(s)

    def flush(self):
        self.old_stdout.flush()
