import io
import os
import re as _re
import secrets
import sys
import zipfile
from typing import List
from fastapi import FastAPI, Request, UploadFile, File, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

config.assert_valid_config()

from logging_setup import setup_logging

setup_logging()

from app.manager import mgr
from app import intent_chat
from app import run_store
from app.celery_app import celery as celery_app
from app.tasks import run_pipeline as run_pipeline_task, chat_query as chat_query_task
from celery.result import AsyncResult
from pipeline.core import setup_telemetry, set_thread
from schemas import (
    RunRequest,
    IntentMessageRequest,
    QueryRequest,
    ApprovalInput,
    BusinessIntent,
    KPIDefinition,
)

setup_telemetry()

app = FastAPI(title="ADEP Crew Web Server", version="1.1.1")

if config.CORS_ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["X-API-Key", "Content-Type"],
    )

# Chart/export images are loaded via <img>/anchor tags, which cannot send
# headers — those routes stay key-free and rely on their unguessable
# uuid4-hex filenames (regex-enforced below) as capability URLs. The readiness
# probe is public because platform healthcheckers cannot send API keys either.
_PUBLIC_API_PREFIXES = ("/api/charts/", "/api/exports/")
_PUBLIC_API_PATHS = ("/api/ready",)


def _needs_api_key(path: str) -> bool:
    if not config.WEB_API_KEY:
        return False
    if not path.startswith("/api/"):
        return False
    if path in _PUBLIC_API_PATHS:
        return False
    return not path.startswith(_PUBLIC_API_PREFIXES)


def _key_matches(request: Request) -> bool:
    expected = config.WEB_API_KEY or ""
    supplied = request.headers.get("X-API-Key", "")
    return bool(supplied) and secrets.compare_digest(supplied, expected)


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    if _needs_api_key(request.url.path) and not _key_matches(request):
        return JSONResponse(
            status_code=401, content={"detail": "Missing or invalid API key."}
        )
    return await call_next(request)


DATA_DIR = "data"
REPORTS_DIR = "reports"
CHARTS_DIR = os.path.join(REPORTS_DIR, "charts")
EXPORTS_DIR = os.path.join(REPORTS_DIR, "exports")
_EXTS = (".csv", ".xlsx", ".xls", ".json")
_CHART_FILENAME_RE = _re.compile(r"^[0-9a-f]{32}\.png$")
_EXPORT_FILENAME_RE = _re.compile(r"^[0-9a-f]{32}\.csv$")
# Report artifacts are flat files named by the pipeline (letters/digits/underscores,
# .md/.json/.sql). Anything else — separators, dotfiles, other extensions — is not
# a report and must not reach the filesystem.
_REPORT_FILENAME_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*\.(md|json|sql)$")
# Uploaded dataset names after sanitization: no separators, no leading dot.
_DATA_FILENAME_RE = _re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9 ._-]*\.(csv|xlsx|xls|json)$", _re.IGNORECASE
)


def _sanitize_upload_name(raw: str | None) -> str:
    """A bare, traversal-proof filename: last path segment only, safe charset,
    no leading dots. Multipart filenames are attacker-controlled."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _re.sub(r"[^A-Za-z0-9 ._-]", "_", name).lstrip(". ")
    return name or "upload"


def _purge_dir(directory: str) -> None:
    for f in os.listdir(directory):
        os.remove(os.path.join(directory, f))


os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(CHARTS_DIR, exist_ok=True)
os.makedirs(EXPORTS_DIR, exist_ok=True)


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
    return run_store.get_state()


@app.get("/api/ready")
def readiness():
    """Readiness, as opposed to /api/status liveness: the app can actually do
    work only if the job queue's Redis is reachable and storage is writable."""
    problems: list[str] = []
    try:
        run_store.get_client().ping()
    except Exception as e:
        problems.append(f"redis unreachable: {e}")
    for d in (DATA_DIR, REPORTS_DIR):
        if not os.access(d, os.W_OK):
            problems.append(f"directory not writable: {d}")
    if problems:
        return JSONResponse(status_code=503, content={"ready": False, "problems": problems})
    return {"ready": True}


@app.post("/api/run")
def run_pipeline(body: RunRequest = Body(default=RunRequest())):
    if run_store.is_busy():
        raise HTTPException(status_code=400, detail="Pipeline is already running.")

    # Structured intent from the conversation takes precedence over free-text.
    if body.questions:
        intent = BusinessIntent(
            questions=[q.strip() for q in body.questions if q.strip()],
            domain=body.domain or "e-commerce",
            priority_metrics=[m.strip() for m in body.priority_metrics if m.strip()],
            metric_definitions=[
                KPIDefinition(
                    name=str(d.get("name", "")).strip(),
                    definition=str(d.get("definition", "")).strip(),
                )
                for d in body.metric_definitions
                if isinstance(d, dict)
                and str(d.get("name", "")).strip()
                and str(d.get("definition", "")).strip()
            ],
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
        "verification_report.md",
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
    _purge_dir(CHARTS_DIR)
    _purge_dir(EXPORTS_DIR)

    run_id = run_store.start_run(instructions, intent_dict)
    try:
        async_result = run_pipeline_task.apply_async(
            args=[run_id, instructions, intent_dict]
        )
    except Exception as e:
        run_store.fail(run_id, f"Could not enqueue the run: {e}")
        raise HTTPException(
            status_code=503, detail=f"Job queue unavailable: {e}"
        )
    run_store.set_task_id(run_id, async_result.id)
    return {"status": "started", "run_id": run_id}


@app.post("/api/intent/start")
def intent_start():
    """Reset the intake conversation and return the assistant's opening message."""
    if run_store.is_busy():
        raise HTTPException(status_code=400, detail="Pipeline is busy.")
    set_thread(mgr.begin_intent_thread())
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
    set_thread(mgr.ensure_intent_thread())
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
    set_thread(mgr.ensure_intent_thread())
    intent = intent_chat.finalize_intent(mgr.intent_history)
    mgr.business_intent = intent.model_dump()
    return mgr.business_intent


@app.post("/api/approve")
def submit_approval(decision: ApprovalInput):
    if not run_store.submit_decision(decision.approved):
        raise HTTPException(
            status_code=400, detail="No approval is currently requested."
        )
    return {"status": "submitted"}


_MAX_QUESTION_LEN = 500


def _validate_question(text: str) -> tuple[bool, str]:
    if not text or not text.strip():
        return False, "Question cannot be empty."
    if len(text) > _MAX_QUESTION_LEN:
        return False, f"Question must be {_MAX_QUESTION_LEN} characters or fewer."
    for pattern in _BLOCKED_PATTERNS:
        if _re.search(pattern, text, _re.IGNORECASE):
            return (
                False,
                "Question contains disallowed content. Ask business questions only.",
            )
    return True, ""


@app.post("/api/query")
def submit_query(body: QueryRequest):
    if run_store.get_status() != "completed":
        raise HTTPException(
            status_code=400,
            detail="No warehouse available. Run the pipeline first.",
        )
    ok, reason = _validate_question(body.question)
    if not ok:
        raise HTTPException(status_code=422, detail=reason)
    db_path, entity_map = run_store.get_run_info()
    thread_id = run_store.ensure_chat_thread()
    try:
        async_result = chat_query_task.delay(
            body.question.strip(), db_path, entity_map, thread_id
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Job queue unavailable: {e}")
    return {"job_id": async_result.id}


@app.get("/api/query/{job_id}")
def poll_query(job_id: str):
    # Celery can't distinguish "unknown id" from "queued, not started" — both
    # report PENDING, so unknown ids poll as pending instead of 404ing.
    result = AsyncResult(job_id, app=celery_app)
    if result.successful():
        return {"status": "done", "answer": result.result}
    if result.failed():
        return {"status": "error", "answer": str(result.result)}
    return {"status": "pending", "answer": ""}


@app.get("/api/charts/{filename}")
def get_chart(filename: str):
    # filenames are uuid4().hex + ".png", generated only by render_chart — reject
    # anything else so this route can't be used to read arbitrary reports-dir files.
    if not _CHART_FILENAME_RE.match(filename):
        raise HTTPException(status_code=404, detail="Chart not found.")
    path = os.path.join(CHARTS_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Chart not found.")
    return FileResponse(path, media_type="image/png")


@app.get("/api/exports/{filename}")
def get_export(filename: str):
    # filenames are uuid4().hex + ".csv", generated only by export_csv — reject
    # anything else so this route can't be used to read arbitrary reports-dir files.
    if not _EXPORT_FILENAME_RE.match(filename):
        raise HTTPException(status_code=404, detail="Export not found.")
    path = os.path.join(EXPORTS_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Export not found.")
    return FileResponse(
        path,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
    if run_store.is_busy():
        raise HTTPException(
            status_code=400, detail="Cannot upload files while pipeline is busy."
        )
    errors = []
    saved = []
    for f in files:
        safe_name = _sanitize_upload_name(f.filename)
        ext = os.path.splitext(safe_name)[1].lower()
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
    if run_store.is_busy():
        raise HTTPException(
            status_code=400, detail="Cannot delete files while pipeline is busy."
        )
    if not _DATA_FILENAME_RE.match(filename):
        raise HTTPException(status_code=404, detail="File not found.")
    path = os.path.join(DATA_DIR, filename)
    if os.path.isfile(path):
        os.remove(path)
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="File not found.")


@app.post("/api/reset")
def reset_warehouse():
    if run_store.is_busy():
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
        ("Answer Verification", "verification_report.md"),
        ("SQL Script", "transformations.sql"),
        ("Star Schema", "schema_design.md"),
        ("Quality Report", "quality_report.md"),
        ("Validation Report", "validation_report.md"),
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
    if not _REPORT_FILENAME_RE.match(filename):
        raise HTTPException(status_code=404, detail="Report not found.")
    path = os.path.join(REPORTS_DIR, filename)
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return {"content": f.read()}
    raise HTTPException(status_code=404, detail="Report not found.")


@app.get("/api/reports/download/{filename}")
def download_report(filename: str):
    if not _REPORT_FILENAME_RE.match(filename):
        raise HTTPException(status_code=404, detail="Report not found.")
    path = os.path.join(REPORTS_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Report not found.")
    return FileResponse(
        path,
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/reports-download-all")
def download_all_reports():
    available = [
        fname
        for _, fname in [
            ("Executive Summary", "executive_summary.md"),
            ("Answerability", "intent_report.md"),
            ("KPIs & Insights", "kpi_report.md"),
            ("Answer Verification", "verification_report.md"),
            ("SQL Script", "transformations.sql"),
            ("Star Schema", "schema_design.md"),
            ("Quality Report", "quality_report.md"),
            ("Validation Report", "validation_report.md"),
            ("Token Usage Profile", "token_usage_report.md"),
            ("Data Profile", "profiling_report.json"),
        ]
        if os.path.exists(os.path.join(REPORTS_DIR, fname))
    ]
    if not available:
        raise HTTPException(status_code=404, detail="No reports available.")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in available:
            zf.write(os.path.join(REPORTS_DIR, fname), arcname=fname)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="adep_reports.zip"'},
    )


app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
def read_root():
    return FileResponse("app/static/index.html")
