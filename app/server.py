import os
import re as _re
import sys
import uuid
from typing import List
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Body
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.manager import mgr
from app.worker import execute_pipeline
from app.chat import run_chat_query
from app import intent_chat
from tools import DatabaseService
from schemas import (
    RunRequest,
    IntentMessageRequest,
    QueryRequest,
    ApprovalInput,
    BusinessIntent,
)

app = FastAPI(title="ADEP Crew Web Server", version="1.1.0")

DATA_DIR = "data"
REPORTS_DIR = "reports"
_EXTS = (".csv", ".xlsx", ".xls", ".json")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)


_ALLOWED_EXTS = {".csv", ".xlsx", ".xls", ".json"}
_MAX_FILE_BYTES = 200 * 1024 * 1024  # 200 MB

_MAX_INSTR_LEN = 1500
_BLOCKED_PATTERNS = [
    r"\b(DROP|CREATE|ALTER|TRUNCATE|INSERT|UPDATE|DELETE|EXEC|EXECUTE)\b",
    r"\{\{.*?\}\}",
    r"ignore\s+(previous|prior|all)\s+instructions",
    r"forget\s+everything",
    r"(act|pretend)\s+as",
    r"you\s+are\s+now",
    r"<\s*/?system\s*>",
]


def _validate_instructions(text: str) -> tuple[bool, str]:
    if not text or not text.strip():
        return True, ""
    if len(text) > _MAX_INSTR_LEN:
        return False, f"Instructions must be {_MAX_INSTR_LEN} characters or fewer."
    for pattern in _BLOCKED_PATTERNS:
        if _re.search(pattern, text, _re.IGNORECASE):
            return (
                False,
                "Instructions contain disallowed content. Describe business questions only — no SQL commands or system directives.",
            )
    return True, ""


@app.get("/api/status")
def get_status():
    return mgr.get_state()


@app.post("/api/run")
def run_pipeline(
    background_tasks: BackgroundTasks,
    body: RunRequest = Body(default=RunRequest()),
):
    if mgr.status in ("running", "waiting_approval"):
        raise HTTPException(status_code=400, detail="Pipeline is already running.")

    # Structured intent from the conversation takes precedence over free-text.
    if body.questions:
        intent = BusinessIntent(
            questions=[q.strip() for q in body.questions if q.strip()],
            domain=body.domain or "e-commerce",
            priority_metrics=[m.strip() for m in body.priority_metrics if m.strip()],
            decision_context=body.decision_context.strip(),
        )
        instructions = intent.to_instructions()
        intent_dict = intent.model_dump()
    else:
        instructions = body.instructions.strip()
        intent_dict = {}

    ok, reason = _validate_instructions(instructions)
    if not ok:
        raise HTTPException(status_code=422, detail=reason)

    old_reports = [
        "profiling_report.json",
        "quality_report.md",
        "schema_design.md",
        "transformations.sql",
        "kpi_report.md",
        "executive_summary.md",
        "validation_report.md",
        "token_usage_report.json",
        "token_usage_report.md",
        "verified_metrics.json",
        "execution.log",
    ]
    for r in old_reports:
        path = os.path.join(REPORTS_DIR, r)
        if os.path.exists(path):
            os.remove(path)

    DatabaseService.clear_source_cache()
    mgr.start()
    mgr.instructions = instructions
    mgr.business_intent = intent_dict
    background_tasks.add_task(execute_pipeline, mgr)
    return {"status": "started"}


@app.post("/api/intent/start")
def intent_start():
    """Reset the intake conversation and return the assistant's opening message."""
    if mgr.status in ("running", "waiting_approval"):
        raise HTTPException(status_code=400, detail="Pipeline is busy.")
    reply = intent_chat.opening_message(DATA_DIR)
    mgr.intent_history = [{"role": "assistant", "content": reply}]
    mgr.business_intent = {}
    return {"reply": reply}


@app.post("/api/intent/message")
def intent_message(body: IntentMessageRequest):
    """One conversational turn. Synchronous (single LLM call, no tool loop)."""
    msg = (body.message or "").strip()
    if not msg:
        raise HTTPException(status_code=422, detail="Message cannot be empty.")
    ok, reason = _validate_instructions(msg)
    if not ok:
        raise HTTPException(status_code=422, detail=reason)
    try:
        reply = intent_chat.chat_turn(mgr.intent_history, msg, DATA_DIR)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Interviewer error: {e}")
    mgr.intent_history.append({"role": "user", "content": msg})
    mgr.intent_history.append({"role": "assistant", "content": reply})
    return {"reply": reply}


@app.post("/api/intent/finalize")
def intent_finalize():
    """Extract the structured BusinessIntent from the conversation so far."""
    intent = intent_chat.finalize_intent(mgr.intent_history)
    mgr.business_intent = intent.model_dump()
    return mgr.business_intent


@app.post("/api/approve")
def submit_approval(decision: ApprovalInput):
    if mgr.status != "waiting_approval":
        raise HTTPException(
            status_code=400, detail="No approval is currently requested."
        )
    mgr.submit_decision(decision.approved)
    return {"status": "submitted"}


_MAX_QUESTION_LEN = 500


def _validate_question(text: str) -> tuple[bool, str]:
    if not text or not text.strip():
        return False, "Question cannot be empty."
    if len(text) > _MAX_QUESTION_LEN:
        return False, f"Question must be {_MAX_QUESTION_LEN} characters or fewer."
    for pattern in _BLOCKED_PATTERNS:
        if _re.search(pattern, text, _re.IGNORECASE):
            return False, "Question contains disallowed content. Ask business questions only."
    return True, ""


def _run_chat_job(job_id: str, question: str) -> None:
    try:
        answer = run_chat_query(question, mgr.warehouse_db_path, mgr.entity_map)
        mgr.chat_jobs[job_id] = {"status": "done", "answer": answer}
    except Exception as e:
        mgr.chat_jobs[job_id] = {"status": "error", "answer": str(e)}
    finally:
        mgr._chat_lock.release()


@app.post("/api/query")
def submit_query(body: QueryRequest, background_tasks: BackgroundTasks):
    if mgr.status != "completed":
        raise HTTPException(
            status_code=400,
            detail="No warehouse available. Run the pipeline first.",
        )
    ok, reason = _validate_question(body.question)
    if not ok:
        raise HTTPException(status_code=422, detail=reason)
    if not mgr._chat_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=429, detail="A query is already in progress. Please wait."
        )
    job_id = str(uuid.uuid4())
    mgr.chat_jobs[job_id] = {"status": "pending", "answer": ""}
    background_tasks.add_task(_run_chat_job, job_id, body.question.strip())
    return {"job_id": job_id}


@app.get("/api/query/{job_id}")
def poll_query(job_id: str):
    job = mgr.chat_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Query job not found.")
    return job


@app.get("/api/files")
def list_files():
    files = []
    if os.path.exists(DATA_DIR):
        for f in os.listdir(DATA_DIR):
            path = os.path.join(DATA_DIR, f)
            if f.endswith(_EXTS) and os.path.isfile(path):
                size = os.path.getsize(path)
                files.append({"name": f, "size": size})
    return sorted(files, key=lambda x: x["name"])


@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...)):
    if mgr.status in ("running", "waiting_approval"):
        raise HTTPException(
            status_code=400, detail="Cannot upload files while pipeline is busy."
        )
    errors = []
    saved = []
    for f in files:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in _ALLOWED_EXTS:
            errors.append(
                f'"{f.filename}": unsupported type ({ext or "none"}). '
                "Only CSV, Excel (.xlsx/.xls), and JSON are accepted."
            )
            continue
        data = await f.read()
        if len(data) > _MAX_FILE_BYTES:
            size_mb = len(data) / (1024 * 1024)
            errors.append(
                f'"{f.filename}": {size_mb:.1f} MB exceeds the 200 MB per-file limit.'
            )
            continue
        safe_name = f.filename or "upload"
        target_path = os.path.join(DATA_DIR, safe_name)
        with open(target_path, "wb") as out:
            out.write(data)
        saved.append(safe_name)

    if errors and not saved:
        raise HTTPException(status_code=422, detail=errors)
    if errors:
        return {"status": "partial", "saved": saved, "errors": errors}
    return {"status": "uploaded", "saved": saved}


@app.delete("/api/files/{filename}")
def delete_file(filename: str):
    if mgr.status in ("running", "waiting_approval"):
        raise HTTPException(
            status_code=400, detail="Cannot delete files while pipeline is busy."
        )
    path = os.path.join(DATA_DIR, filename)
    if os.path.exists(path):
        os.remove(path)
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="File not found.")


@app.post("/api/reset")
def reset_warehouse():
    if mgr.status in ("running", "waiting_approval"):
        raise HTTPException(
            status_code=400, detail="Cannot reset database while pipeline is busy."
        )
    db_path = "data/warehouse.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    return {"status": "reset"}


@app.get("/api/reports")
def get_reports_summary():
    REPORTS = [
        ("Executive Summary", "executive_summary.md"),
        ("Answerability", "intent_report.md"),
        ("KPIs & Insights", "kpi_report.md"),
        ("Execution Log", "execution.log"),
        ("SQL Script", "transformations.sql"),
        ("Star Schema", "schema_design.md"),
        ("Quality Report", "quality_report.md"),
        ("Validation Report", "validation_report.md"),
        ("Token Usage Profile", "token_usage_report.md"),
        ("Data Profile", "profiling_report.json"),
    ]
    summary = []
    for label, fname in REPORTS:
        path = os.path.join(REPORTS_DIR, fname)
        available = os.path.exists(path)
        summary.append({"label": label, "filename": fname, "available": available})
    return summary


@app.get("/api/reports/{filename}")
def get_report_content(filename: str):
    path = os.path.join(REPORTS_DIR, filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return {"content": f.read()}
    raise HTTPException(status_code=404, detail="Report not found.")


app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
def read_root():
    return FileResponse("app/static/index.html")
