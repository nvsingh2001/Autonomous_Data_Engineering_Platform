"""Redis-backed run state shared by the web process and the Celery workers.

Replaces the in-memory RunManager singleton: run status/active-step/error live
in a `adep:run:{id}` hash, the captured log stream in a `adep:run:{id}:log`
list, and the human-approval hand-off in a `adep:approval:{id}` list the worker
BLPOPs. `get_state()` keeps the exact response shape the SPA already polls.
"""

import json
import re
import uuid

import redis

import config

_CURRENT_KEY = "adep:run:current"
_RUN_TTL_SECONDS = 7 * 24 * 3600  # finished-run state kept a week for debugging
_LOG_MAX_LINES = 4000
_ACTIVITY_LIMIT = 40

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_FLOW_PREFIX = "[Flow]"

# Ordered: first marker found in a log chunk wins (mirrors the old RunManager).
_STEP_MARKERS = [
    ("Compiling final executive summaries...", "summarizing"),
    ("Compiling business insights...", "analytics"),
    ("Planning transformations...", "transformations"),
    ("Designing schema...", "schema"),
    ("Assessing data quality...", "quality"),
    ("Starting data profiling...", "profiling"),
]

_client: redis.Redis | None = None


def get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            config.REDIS_URL,
            decode_responses=True,
            socket_timeout=10,
            socket_connect_timeout=5,
        )
    return _client


def use_client(client) -> None:
    """Test seam: inject a fakeredis (or other) client."""
    global _client
    _client = client


def _run_key(run_id: str) -> str:
    return f"adep:run:{run_id}"


def _log_key(run_id: str) -> str:
    return f"adep:run:{run_id}:log"


def _approval_key(run_id: str) -> str:
    return f"adep:approval:{run_id}"


def current_run_id() -> str | None:
    return get_client().get(_CURRENT_KEY) or None


def get_status(run_id: str | None = None) -> str:
    run_id = run_id or current_run_id()
    if not run_id:
        return "idle"
    return get_client().hget(_run_key(run_id), "status") or "idle"


def is_busy() -> bool:
    return get_status() in ("running", "waiting_approval")


def start_run(instructions: str, intent: dict) -> str:
    """Create the run hash, point `current` at it, and clear leftovers."""
    r = get_client()
    run_id = uuid.uuid4().hex
    r.delete(_log_key(run_id), _approval_key(run_id))
    r.hset(
        _run_key(run_id),
        mapping={
            "status": "running",
            "active_step": "profiling",
            "error": "",
            "instructions": instructions,
            "intent": json.dumps(intent or {}),
            "warehouse_db_path": "",
            "entity_map": "{}",
            "chat_thread_id": "",
        },
    )
    r.set(_CURRENT_KEY, run_id)
    return run_id


def set_task_id(run_id: str, task_id: str) -> None:
    get_client().hset(_run_key(run_id), "task_id", task_id)


def complete(run_id: str, warehouse_db_path: str, entity_map: dict) -> None:
    r = get_client()
    r.hset(
        _run_key(run_id),
        mapping={
            "status": "completed",
            "active_step": "finished",
            "warehouse_db_path": warehouse_db_path,
            "entity_map": json.dumps(entity_map or {}),
        },
    )
    _expire(run_id)


def fail(run_id: str, error: str) -> None:
    r = get_client()
    r.hset(
        _run_key(run_id),
        mapping={"status": "failed", "active_step": "failed", "error": error},
    )
    _expire(run_id)


def _expire(run_id: str) -> None:
    r = get_client()
    r.expire(_run_key(run_id), _RUN_TTL_SECONDS)
    r.expire(_log_key(run_id), _RUN_TTL_SECONDS)
    r.expire(_approval_key(run_id), _RUN_TTL_SECONDS)


def append_log(run_id: str, chunk: str) -> None:
    """One captured stdout/stderr write: store it and advance the active step
    when a stage marker appears in it."""
    r = get_client()
    r.rpush(_log_key(run_id), chunk)
    r.ltrim(_log_key(run_id), -_LOG_MAX_LINES, -1)
    for marker, step_name in _STEP_MARKERS:
        if marker in chunk:
            r.hset(_run_key(run_id), "active_step", step_name)
            break


def _activity(chunks: list[str], limit: int = _ACTIVITY_LIMIT) -> list[str]:
    """The pipeline's own narration only — the `[Flow] …` lines — with ANSI and
    the prefix stripped. Deliberately excludes raw agent output (prompts,
    reasoning, tool I/O), so the UI can show what's happening without exposing
    how the system works."""
    out: list[str] = []
    for line in "".join(chunks).splitlines():
        if _FLOW_PREFIX not in line:
            continue
        clean = _ANSI_RE.sub("", line)
        msg = clean[clean.find(_FLOW_PREFIX) + len(_FLOW_PREFIX) :].strip()
        if msg:
            out.append(msg)
    return out[-limit:]


_IDLE_STATE = {
    "status": "idle",
    "error": None,
    "active_step": "idle",
    "activity": [],
    "approval_data": None,
}


def get_state() -> dict:
    """Same response shape as the old RunManager.get_state() — the SPA polls it."""
    run_id = current_run_id()
    if not run_id:
        return dict(_IDLE_STATE)
    r = get_client()
    run = r.hgetall(_run_key(run_id))
    if not run:
        return dict(_IDLE_STATE)
    approval_data = None
    if run.get("status") == "waiting_approval":
        approval_data = {
            "score": float(run.get("approval_score", 0)),
            "summary": run.get("approval_summary", ""),
        }
    return {
        "status": run.get("status", "idle"),
        "error": run.get("error") or None,
        "active_step": run.get("active_step", "idle"),
        "activity": _activity(r.lrange(_log_key(run_id), 0, -1)),
        "approval_data": approval_data,
    }


def request_approval(run_id: str, score: float, summary: str) -> bool:
    """Worker side of the human gate: publish the request, block on the decision
    list (bounded), reject on expiry. Cross-process replacement for the old
    threading.Event wait."""
    r = get_client()
    r.delete(_approval_key(run_id))
    r.hset(
        _run_key(run_id),
        mapping={
            "status": "waiting_approval",
            "approval_score": score,
            "approval_summary": summary,
        },
    )
    timeout = max(1, int(config.APPROVAL_TIMEOUT_SECONDS))
    popped = r.blpop(_approval_key(run_id), timeout=timeout)
    if popped is None:
        append_log(
            run_id,
            f"[Flow] No approval decision within {timeout}s — rejecting and "
            "aborting the run.\n",
        )
    r.hset(_run_key(run_id), "status", "running")
    r.hdel(_run_key(run_id), "approval_score", "approval_summary")
    return popped is not None and popped[1] == "1"


def submit_decision(approved: bool) -> bool:
    """Web side of the human gate. Returns False when no approval is pending."""
    r = get_client()
    run_id = current_run_id()
    if not run_id or get_status(run_id) != "waiting_approval":
        return False
    r.rpush(_approval_key(run_id), "1" if approved else "0")
    return True


def get_run_info(run_id: str | None = None) -> tuple[str, dict]:
    """(warehouse_db_path, entity_map) of the given/current run — what the chat
    worker needs to answer questions against the finished warehouse."""
    run_id = run_id or current_run_id()
    if not run_id:
        return "", {}
    run = get_client().hgetall(_run_key(run_id))
    try:
        entity_map = json.loads(run.get("entity_map") or "{}")
    except json.JSONDecodeError:
        entity_map = {}
    return run.get("warehouse_db_path", ""), entity_map


def ensure_chat_thread(run_id: str | None = None) -> str:
    """The Q&A LangSmith thread for the current warehouse build — all queries
    against one build share it; a new run starts with an empty one."""
    run_id = run_id or current_run_id()
    if not run_id:
        return ""
    r = get_client()
    thread_id = r.hget(_run_key(run_id), "chat_thread_id")
    if not thread_id:
        from pipeline.core import new_thread_id

        thread_id = new_thread_id("warehouse-chat")
        r.hset(_run_key(run_id), "chat_thread_id", thread_id)
    return thread_id
