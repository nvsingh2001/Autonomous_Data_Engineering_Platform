import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import HumanLoopService, WebApprovalStrategy

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
        self.chat_jobs: dict = {}  # {job_id: {"status": "pending"|"done"|"error", "answer": str}}

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

            log_path = os.path.join(REPORTS_DIR, "execution.log")
            try:
                if os.path.exists(log_path):
                    os.remove(log_path)
            except Exception:
                pass

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

    def get_state(self) -> dict:
        with self._lock:
            return {
                "status": self.status,
                "error": self.error,
                "active_step": self.active_step,
                "logs": "".join(self.log_buffer),
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
