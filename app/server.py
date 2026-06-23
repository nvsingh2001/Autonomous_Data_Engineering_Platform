import os
import sys
import shutil
from typing import List
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.manager import mgr
from app.worker import execute_pipeline

app = FastAPI(title="ADEP Crew Web Server", version="1.1.0")

DATA_DIR = "data"
REPORTS_DIR = "reports"
_EXTS = (".csv", ".xlsx", ".xls", ".json")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)


class ApprovalInput(BaseModel):
    approved: bool


@app.get("/api/status")
def get_status():
    return mgr.get_state()


@app.post("/api/run")
def run_pipeline(background_tasks: BackgroundTasks):
    if mgr.status in ("running", "waiting_approval"):
        raise HTTPException(status_code=400, detail="Pipeline is already running.")

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
        "execution.log",
    ]
    for r in old_reports:
        path = os.path.join(REPORTS_DIR, r)
        if os.path.exists(path):
            os.remove(path)

    mgr.start()
    background_tasks.add_task(execute_pipeline, mgr)
    return {"status": "started"}


@app.post("/api/approve")
def submit_approval(decision: ApprovalInput):
    if mgr.status != "waiting_approval":
        raise HTTPException(
            status_code=400, detail="No approval is currently requested."
        )
    mgr.submit_decision(decision.approved)
    return {"status": "submitted"}


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
    for f in files:
        target_path = os.path.join(DATA_DIR, f.filename)
        with open(target_path, "wb") as out:
            shutil.copyfileobj(f.file, out)
    return {"status": "uploaded"}


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
