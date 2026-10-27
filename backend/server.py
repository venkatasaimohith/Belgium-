#!/usr/bin/env python3
"""
Mailbox Detection Studio — Backend API
======================================
Serves:
  • REST API under /api/*
  • Frontend static files from ../frontend

Run:
  python server.py
  # or: uvicorn server:app --host 0.0.0.0 --port 8002
"""

from __future__ import annotations

import json
import io
import logging
import os
import shutil
import socket
import sqlite3
import sys
import threading
import zipfile
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

# Local imports
BACKEND_ROOT = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_ROOT.parent
FRONTEND_ROOT = REPO_ROOT / "frontend"
JOBS_ROOT = BACKEND_ROOT / "jobs"
DATA_ROOT = BACKEND_ROOT / "data"
ENV_FILE_PATH = REPO_ROOT / ".env"
LEGACY_ENV_FILE_PATH = BACKEND_ROOT / ".env"

sys.path.insert(0, str(BACKEND_ROOT))
from pipeline.runner import STAGES, init_job_stages, run_pipeline  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backend")

JOBS_ROOT.mkdir(parents=True, exist_ok=True)
DATA_ROOT.mkdir(parents=True, exist_ok=True)

# In-memory job index (also persisted as job.json per work dir)
JOBS_DB: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


# ─────────────────────────────────────────────
#  .env helpers
# ─────────────────────────────────────────────

def read_env_file_dict() -> Dict[str, str]:
    env_vars: Dict[str, str] = {}
    env_path = ENV_FILE_PATH if ENV_FILE_PATH.exists() else LEGACY_ENV_FILE_PATH
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()
                os.environ.setdefault(k.strip(), v.strip())
    return env_vars


def write_env_file_dict(new_values: Dict[str, str]) -> None:
    existing = read_env_file_dict()
    existing.update({k: str(v) for k, v in new_values.items() if v is not None})
    lines = [f"{k}={v}\n" for k, v in existing.items()]
    ENV_FILE_PATH.write_text("".join(lines), encoding="utf-8")
    for k, v in new_values.items():
        if v is not None:
            os.environ[k] = str(v)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_path(job_id: str) -> Path:
    return JOBS_ROOT / job_id / "job.json"


def persist_job(job: Dict[str, Any]) -> None:
    job_id = job["job_id"]
    path = _job_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _JOBS_LOCK:
        JOBS_DB[job_id] = job
        path.write_text(json.dumps(job, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def load_jobs_from_disk() -> None:
    if not JOBS_ROOT.exists():
        return
    for folder in JOBS_ROOT.iterdir():
        jp = folder / "job.json"
        if jp.exists():
            try:
                job = json.loads(jp.read_text(encoding="utf-8"))
                # A background thread cannot survive a server restart. Recover
                # persisted active states instead of leaving the UI stuck forever.
                if job.get("status") in ("PROCESSING", "QUEUED", "STOPPING"):
                    job["status"] = "FAILED"
                    job["error"] = "Pipeline interrupted by backend restart"
                    job["cancel_requested"] = False
                    job["updated_at"] = _now()
                    (folder / ".cancel_requested").unlink(missing_ok=True)
                    jp.write_text(
                        json.dumps(job, indent=2, ensure_ascii=False, default=str),
                        encoding="utf-8",
                    )
                JOBS_DB[job["job_id"]] = job
            except Exception as exc:
                logger.warning("Could not load %s: %s", jp, exc)


# ─────────────────────────────────────────────
#  App
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Backend root : %s", BACKEND_ROOT)
    logger.info("Frontend root: %s", FRONTEND_ROOT)
    logger.info("Jobs root    : %s", JOBS_ROOT)
    logger.info("Loaded jobs  : %d", len(JOBS_DB))
    yield


app = FastAPI(title="Mailbox Detection Studio API", version="3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def disable_frontend_cache(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path == "/index.html" or request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

read_env_file_dict()
load_jobs_from_disk()


# ── Settings ─────────────────────────────────

@app.get("/api/app-settings")
def get_app_settings():
    env_vars = read_env_file_dict()
    items = [
        {
            "key": "GOOGLE_MAPS_API_KEY",
            "label": "Google Maps API Key",
            "group": "Google",
            "hint": "Street View Metadata + Static Image API key.",
            "can_validate": True,
            "is_set": bool(env_vars.get("GOOGLE_MAPS_API_KEY")),
            "masked_value": (env_vars.get("GOOGLE_MAPS_API_KEY", "")[:6] + "...")
            if env_vars.get("GOOGLE_MAPS_API_KEY")
            else "",
        },
        {
            "key": "MAILBOX_MAX_SITES",
            "label": "Max Sites Per Run",
            "group": "Pipeline",
            "hint": "Optional debug limit. The default 0 processes all sites.",
            "can_validate": False,
            "is_set": bool(env_vars.get("MAILBOX_MAX_SITES")),
            "masked_value": env_vars.get("MAILBOX_MAX_SITES", "0"),
        },
        {
            "key": "OLLAMA_BASE_URL",
            "label": "Ollama Base URL",
            "group": "Ollama",
            "hint": "Optional local vision model endpoint.",
            "can_validate": True,
            "is_set": bool(env_vars.get("OLLAMA_BASE_URL")),
            "masked_value": env_vars.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        },
        {
            "key": "OLLAMA_MODEL",
            "label": "Ollama Model Name",
            "group": "Ollama",
            "hint": "e.g. qwen2-vl",
            "can_validate": True,
            "is_set": bool(env_vars.get("OLLAMA_MODEL")),
            "masked_value": env_vars.get("OLLAMA_MODEL", "qwen2-vl"),
        },
    ]
    return {"items": items, "env_file": str(ENV_FILE_PATH), "backend_available": True}


@app.put("/api/app-settings")
def update_app_settings(payload: Dict[str, Any]):
    values = payload.get("values", {})
    if values:
        write_env_file_dict(values)
    return get_app_settings()


@app.post("/api/app-settings/validate")
def validate_app_settings(payload: Dict[str, Any]):
    keys = payload.get("keys", [])
    values = payload.get("values", {})
    env_vars = read_env_file_dict()
    results = []
    valid_count = missing_count = 0
    for k in keys:
        val = values.get(k) or env_vars.get(k)
        if val:
            valid_count += 1
            results.append({"key": k, "status": "valid", "valid": True, "detail": "Configured"})
        else:
            missing_count += 1
            results.append({"key": k, "status": "missing", "valid": False, "detail": "Missing"})
    return {
        "results": results,
        "summary": {"valid": valid_count, "invalid": 0, "missing": missing_count, "unchecked": 0},
        "backend_available": True,
    }


# ── Agents / flow ────────────────────────────

@app.get("/api/belgium/agents")
def list_agents():
    return {
        "agents": [
            {
                "key": s["key"],
                "display_name": s["display_name"],
                "description": s["description"],
                "enabled_by_default": True,
                "default_order": i + 1,
            }
            for i, s in enumerate(STAGES)
        ]
    }


@app.get("/api/belgium/flows/default")
def get_default_flow():
    return {
        "flow": [
            {"key": s["key"], "enabled": True, "order": i + 1}
            for i, s in enumerate(STAGES)
        ]
    }


@app.get("/api/belgium/flows/templates")
def list_flow_templates():
    return {"templates": []}


# ── Jobs ──────────────────────────────────────

@app.get("/api/belgium/jobs")
def list_jobs():
    with _JOBS_LOCK:
        jobs = list(JOBS_DB.values())
    jobs.sort(key=lambda j: j.get("created_at") or "", reverse=True)
    return {"jobs": jobs}


@app.get("/api/belgium/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS_DB.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/belgium/jobs/{job_id}/flow")
def get_job_flow(job_id: str):
    job = JOBS_DB.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    saved = job.get("flow_config")
    if saved:
        return saved
    return {
        "template_id": None,
        "flow": [
            {"key": s["key"], "enabled": True, "order": i + 1}
            for i, s in enumerate(STAGES)
        ],
        "fallback": True,
    }


@app.put("/api/belgium/jobs/{job_id}/flow")
def update_job_flow(job_id: str, payload: Dict[str, Any]):
    job = JOBS_DB.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") in ("PROCESSING", "QUEUED", "STOPPING"):
        raise HTTPException(status_code=409, detail="Cannot change flow while pipeline is running")
    known_keys = {stage["key"] for stage in STAGES}
    normalized = []
    for index, item in enumerate(payload.get("flow") or []):
        key = item.get("key") or item.get("agent_key")
        if key not in known_keys:
            continue
        normalized.append(
            {
                "key": key,
                "enabled": item.get("enabled", True) is not False,
                "order": int(item.get("order") or index + 1),
                "locked": bool(item.get("locked", False)),
                "required": bool(item.get("required", False)),
            }
        )
    job["flow_config"] = {
        "template_id": payload.get("template_id"),
        "flow": normalized,
        "fallback": False,
    }
    job["updated_at"] = _now()
    persist_job(job)
    return {"status": "updated", **job["flow_config"]}


@app.put("/api/belgium/jobs/{job_id}/options")
def update_job_options(job_id: str, payload: Dict[str, Any]):
    job = JOBS_DB.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") in ("PROCESSING", "QUEUED", "STOPPING"):
        raise HTTPException(status_code=409, detail="Cannot change options while pipeline is running")
    allowed = {
        "views_inspected",
        "deduplicate_enabled",
        "model_mode",
        "imagery_preference",
        "streetview_year",
        "use_api_cache",
        "enhancement_mode",
        "enhancement_scale",
        "enhancement_device",
        "enhancement_workers",
        "rejection_vision",
        "realesrgan_weights",
    }
    updates = {key: value for key, value in payload.items() if key in allowed}
    if "streetview_year" in updates:
        raw_year = str(updates["streetview_year"] or "latest").strip().lower()
        if raw_year in ("latest", "auto"):
            updates["streetview_year"] = "latest"
        else:
            try:
                year = int(raw_year)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Street View year must be a year or 'latest'") from exc
            if year < 2007 or year > datetime.now(timezone.utc).year:
                raise HTTPException(status_code=422, detail="Street View year must be between 2007 and the current year")
            updates["streetview_year"] = str(year)
    job.update(updates)
    job.setdefault("options", {}).update(updates)
    job["updated_at"] = _now()
    persist_job(job)
    return {"status": "updated", "options": updates}


@app.get("/api/belgium/jobs/{job_id}/records")
def get_job_records(
    job_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=500),
):
    job = JOBS_DB.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _refresh_broken_preview(job)
    records = job.get("records") or []
    total = len(records)
    start = (page - 1) * limit
    end = start + limit
    return {
        "count": job.get("summary_json", {}).get("total_input_records", total),
        "records": records[start:end],
        "page": page,
        "limit": limit,
        "total_pages": max(1, (total + limit - 1) // limit if total else 1),
    }


@app.get("/api/belgium/jobs/{job_id}/progress")
def get_progress(job_id: str):
    job = JOBS_DB.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job_id,
        "status": job.get("status"),
        "progress_pct": job.get("progress_pct", 0),
        "current_agent": job.get("current_agent"),
        "stages": job.get("stages") or [],
    }


@app.get("/api/belgium/jobs/{job_id}/events")
def get_events(job_id: str):
    job = JOBS_DB.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    events = []
    for i, stage in enumerate(job.get("stages") or []):
        events.append(
            {
                "id": i + 1,
                "event_type": "agent",
                "level": "ERROR" if stage.get("status") == "FAILED" else "INFO",
                "message": f"{stage.get('display_name')}: {stage.get('status')}"
                + (f" — {stage.get('error_message')}" if stage.get("error_message") else ""),
                "timestamp": stage.get("completed_at") or stage.get("started_at") or job.get("updated_at"),
            }
        )
    return {"events": events}


def _parse_preview_records(file_path: Path, limit: Optional[int] = None) -> tuple[List[dict], int]:
    """Lightweight preview for the UI before the full pipeline runs."""
    records: List[dict] = []
    feature_count = 0
    suffix = file_path.suffix.lower()
    try:
        if suffix == ".gpkg":
            with closing(sqlite3.connect(str(file_path))) as conn:
                c = conn.cursor()
                c.execute(
                    "SELECT table_name FROM gpkg_contents "
                    "WHERE data_type='features' ORDER BY table_name"
                )
                tables = [r[0] for r in c.fetchall()]
                table_columns: Dict[str, List[str]] = {}
                for table in tables:
                    c.execute(f'PRAGMA table_info("{table}")')
                    table_columns[table] = [col[1] for col in c.fetchall()]

                def address_layer_score(table: str) -> int:
                    name = table.lower()
                    columns = {column.lower() for column in table_columns[table]}
                    score = 100 if any(token in name for token in ("address", "adres")) else 0
                    score += 30 if columns & {
                        "aansluitadres_straatnaam", "bm_street_name", "street", "straatnaam"
                    } else 0
                    score += 30 if columns & {
                        "aansluitadres_huisnummer", "bm_housenumber", "housenr", "house_number"
                    } else 0
                    score += 10 if columns & {
                        "aansluitadres_postcode", "postcode", "postal_code", "zip"
                    } else 0
                    if any(token in name for token in ("parcel", "building", "road", "frontage")):
                        score -= 100
                    return score

                if not tables:
                    return records, feature_count
                # GeoPackages commonly store parcels before their address layer.
                # Select by address semantics rather than SQLite table order.
                tname = max(tables, key=address_layer_score)
                logger.info("Previewing GeoPackage address layer '%s'", tname)
                c.execute(f'SELECT count(*) FROM "{tname}"')
                feature_count = int(c.fetchone()[0])
                if limit is None:
                    c.execute(f'SELECT * FROM "{tname}"')
                else:
                    c.execute(f'SELECT * FROM "{tname}" LIMIT ?', (limit,))
                rows = c.fetchall()
                cols = table_columns[tname]
                from pipeline.runner import member_to_landbase_row

                for i, r in enumerate(rows):
                    rdict = dict(zip(cols, r))
                    records.append(
                        member_to_landbase_row(
                            rdict,
                            row_index=i + 1,
                            pending=True,
                        )
                    )
        else:
            feature_count = 0
    except Exception as exc:
        logger.warning("Preview parse failed: %s", exc)
    return records, feature_count


def _refresh_broken_preview(job: Dict[str, Any]) -> None:
    """Repair older previews with the wrong layer or truncated pending rows."""
    records = job.get("records") or []
    expected = job.get("summary_json", {}).get("total_input_records", 0)
    incomplete = len(records) < expected and all(
        row.get("status") == "PENDING" for row in records
    )
    if not incomplete and any(
        row.get("bm_street_name") or row.get("bm_housenumber") or row.get("bm_address")
        for row in records
    ):
        return
    input_path = Path(str(job.get("input_path") or ""))
    if not input_path.is_file() or input_path.suffix.lower() != ".gpkg":
        return
    refreshed, feature_count = _parse_preview_records(input_path)
    if not refreshed or not any(row.get("bm_street_name") for row in refreshed):
        return
    job["records"] = refreshed
    job.setdefault("summary_json", {})["total_input_records"] = feature_count
    job["updated_at"] = _now()
    persist_job(job)


@app.post("/api/belgium/upload")
async def upload_job(
    project_name: str = Form(...),
    input_type: str = Form("GPKG"),
    model_mode: str = Form("rf_model"),
    gpkg_file: Optional[UploadFile] = File(None),
    kml_file: Optional[UploadFile] = File(None),
    kmz_file: Optional[UploadFile] = File(None),
):
    file_obj = gpkg_file or kml_file or kmz_file
    if not file_obj:
        raise HTTPException(status_code=400, detail="No file uploaded")

    job_id = f"job-belgium-{int(datetime.now().timestamp() * 1000)}"
    work_dir = JOBS_ROOT / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    filename = file_obj.filename or project_name
    input_path = work_dir / filename
    content = await file_obj.read()
    input_path.write_bytes(content)

    # KMZ → extract first KML
    if filename.lower().endswith(".kmz"):
        try:
            with zipfile.ZipFile(input_path, "r") as zf:
                kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
                if kml_names:
                    extracted = work_dir / Path(kml_names[0]).name
                    extracted.write_bytes(zf.read(kml_names[0]))
                    input_path = extracted
                    input_type = "KML"
        except Exception as exc:
            logger.warning("KMZ extract failed: %s", exc)

    preview, feature_count = _parse_preview_records(input_path)
    if feature_count == 0:
        feature_count = len(preview)

    job = {
        "id": job_id,
        "job_id": job_id,
        "project_name": project_name or filename,
        "input_type": input_type.upper(),
        "model_mode": model_mode,
        "streetview_year": "latest",
        "use_api_cache": True,
        "run_mode": "mailbox_pipeline",
        "projection_mode": "EPSG:31370",
        "status": "UPLOADED",
        "progress_pct": 0,
        "current_agent": None,
        "created_at": _now(),
        "updated_at": _now(),
        "input_path": str(input_path),
        "work_dir": str(work_dir),
        "files": [
            {
                "id": f"f-{job_id}",
                "file_type": input_type.lower(),
                "original_filename": filename,
                "size_bytes": len(content),
            }
        ],
        "outputs": [],
        "summary_json": {
            "total_input_records": feature_count,
            "unique_addresses": None,
        },
        "records": preview,
        "options": {
            "demo_boxes": False,
            "dry_run_images": not bool(os.environ.get("GOOGLE_MAPS_API_KEY")),
            "offline_register": False,
            "fetch_wfs": True,
            "model_mode": model_mode,
            "streetview_year": "latest",
            "use_api_cache": True,
        },
    }
    init_job_stages(job)
    persist_job(job)
    return {
        "job_id": job_id,
        "id": job_id,
        "status": "UPLOADED",
        "message": "File uploaded — ready for Agents 1–7 pipeline",
    }


def _run_job_bg(job_id: str) -> None:
    job = JOBS_DB.get(job_id)
    if not job:
        return
    run_pipeline(job, persist=persist_job)


@app.post("/api/belgium/jobs/{job_id}/process")
def process_job(job_id: str, background_tasks: BackgroundTasks):
    job = JOBS_DB.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") in ("PROCESSING", "QUEUED", "STOPPING"):
        return {
            "job_id": job_id,
            "status": job.get("status"),
            "message": "Already running" if job.get("status") != "STOPPING" else "Pipeline is stopping",
        }

    job["status"] = "PROCESSING"
    job["cancel_requested"] = False
    (Path(job["work_dir"]) / ".cancel_requested").unlink(missing_ok=True)
    job["progress_pct"] = 1
    job["current_agent"] = "agent1_dedup"
    job["updated_at"] = _now()
    # Refresh options from the env file each run.  The server may have been
    # started before a setting was changed, so os.environ can be stale.
    opts = job.setdefault("options", {})
    opts["dry_run_images"] = not bool(os.environ.get("GOOGLE_MAPS_API_KEY"))
    env_vars = read_env_file_dict()
    max_env = env_vars.get("MAILBOX_MAX_SITES", os.environ.get("MAILBOX_MAX_SITES", "0"))
    if max_env in ("0", "none", "None", ""):
        opts["max_sites"] = None
    else:
        try:
            opts["max_sites"] = int(max_env)
        except ValueError:
            opts["max_sites"] = None
    persist_job(job)

    background_tasks.add_task(_run_job_bg, job_id)
    return {
        "job_id": job_id,
        "status": "PROCESSING",
        "message": "Mailbox and floor-count pipeline started (Agents 1–7)",
        "stages": [s["key"] for s in STAGES],
    }


@app.post("/api/belgium/jobs/{job_id}/stop")
def stop_job(job_id: str):
    job = JOBS_DB.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") not in ("PROCESSING", "QUEUED", "STOPPING"):
        return {"job_id": job_id, "status": job.get("status"), "message": "Job is not running"}
    job["cancel_requested"] = True
    cancel_file = Path(job.get("work_dir") or _job_path(job_id).parent) / ".cancel_requested"
    cancel_file.parent.mkdir(parents=True, exist_ok=True)
    cancel_file.write_text(_now(), encoding="utf-8")
    job["status"] = "STOPPING"
    job["error"] = "Stop requested by user"
    job["updated_at"] = _now()
    persist_job(job)
    return {"job_id": job_id, "status": "STOPPING", "message": "Stopping active pipeline"}


@app.delete("/api/belgium/jobs/{job_id}")
def delete_job(job_id: str):
    with _JOBS_LOCK:
        JOBS_DB.pop(job_id, None)
    work = JOBS_ROOT / job_id
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    return {"status": "deleted", "job_id": job_id}


@app.post("/api/belgium/jobs/bulk-delete")
def bulk_delete(payload: Dict[str, Any]):
    ids = payload.get("job_ids") or []
    deleted = []
    for job_id in ids:
        delete_job(job_id)
        deleted.append(job_id)
    return {"status": "deleted", "deleted_job_ids": deleted, "errors": []}


@app.get("/api/belgium/jobs/{job_id}/export")
def export_job(job_id: str, format: str = Query("kml")):
    job = JOBS_DB.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    records = job.get("records") or []
    fmt = format.lower()

    if fmt == "kml":
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<kml xmlns="http://www.opengis.net/kml/2.2">',
            "  <Document>",
            f'    <name>{job.get("project_name", "Mailbox")}_Output</name>',
        ]
        for idx, r in enumerate(records):
            street = r.get("bm_street_name") or ""
            house = r.get("bm_address") or r.get("bm_housenumber") or ""
            city = r.get("m_municipality_name") or r.get("name") or ""
            addr = (
                str(r.get("address") or f"{street} {house}, {city}".strip(", ") or f"Address {idx+1}")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            lat = r.get("lat") or (50.8467 + idx * 0.0012)
            lng = r.get("lng") or (4.3524 + idx * 0.0015)
            lines.extend(
                [
                    "    <Placemark>",
                    f"      <name>{addr}</name>",
                    "      <ExtendedData>",
                    f'        <Data name="fid"><value>{r.get("fid", "")}</value></Data>',
                    f'        <Data name="site_id"><value>{r.get("site_id") or ""}</value></Data>',
                    f'        <Data name="Check"><value>{r.get("Check") or r.get("status") or ""}</value></Data>',
                    f'        <Data name="Building Type"><value>{r.get("Building Type") or ""}</value></Data>',
                    f'        <Data name="Remarks"><value>{str(r.get("Remarks") or "").replace("&", "&amp;")}</value></Data>',
                    f'        <Data name="final_count"><value>{r.get("final_count") if r.get("final_count") is not None else "N/A"}</value></Data>',
                    "      </ExtendedData>",
                    f"      <Point><coordinates>{lng},{lat},0</coordinates></Point>",
                    "    </Placemark>",
                ]
            )
        lines.extend(["  </Document>", "</kml>"])
        content = "\n".join(lines)
        return Response(
            content=content,
            media_type="application/vnd.google-earth.kml+xml",
            headers={"Content-Disposition": f'attachment; filename="mailbox_{job_id}.kml"'},
        )

    if fmt == "gpkg":
        # Prefer real agent2_result.gpkg if present
        work = Path(job.get("work_dir") or (JOBS_ROOT / job_id))
        real = work / "output" / "agent2_result.gpkg"
        if real.exists():
            return FileResponse(
                real,
                filename=f"mailbox_{job_id}.gpkg",
                media_type="application/geopackage+sqlite3",
            )
        # Minimal gpkg fallback
        out_path = work / f"mailbox_{job_id}.gpkg"
        conn = sqlite3.connect(str(out_path))
        c = conn.cursor()
        c.execute("PRAGMA application_id = 1196444487;")
        c.execute(
            "CREATE TABLE IF NOT EXISTS mailbox_results ("
            "id TEXT PRIMARY KEY, fid TEXT, bm_street_name TEXT, bm_housenumber TEXT, "
            "bm_box TEXT, bm_address TEXT, m_municipality_name TEXT, "
            "check_status TEXT, building_type TEXT, remarks TEXT, "
            "final_count INTEGER, site_id TEXT)"
        )
        c.execute("DELETE FROM mailbox_results;")
        for idx, r in enumerate(records):
            c.execute(
                "INSERT INTO mailbox_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    r.get("id", f"rec-{idx+1}"),
                    str(r.get("fid") or ""),
                    r.get("bm_street_name"),
                    r.get("bm_housenumber"),
                    r.get("bm_box"),
                    r.get("bm_address"),
                    r.get("m_municipality_name"),
                    r.get("Check"),
                    r.get("Building Type"),
                    r.get("Remarks"),
                    r.get("final_count"),
                    r.get("site_id"),
                ),
            )
        conn.commit()
        conn.close()
        return FileResponse(
            out_path,
            filename=f"mailbox_{job_id}.gpkg",
            media_type="application/geopackage+sqlite3",
        )

    raise HTTPException(status_code=400, detail="Supported formats: kml, gpkg")


@app.get("/api/belgium/jobs/{job_id}/streetview-images")
def download_streetview_images(
    job_id: str,
    year: Optional[str] = Query(None),
):
    """Download Agent 3 images as a ZIP, optionally filtered by metadata year."""
    job = JOBS_DB.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    selected = str(
        year
        or job.get("streetview_year")
        or (job.get("options") or {}).get("streetview_year")
        or "latest"
    ).strip().lower()
    if selected not in ("latest", "auto"):
        try:
            selected_year = int(selected)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Year must be a number or 'latest'") from exc
        if selected_year < 2007 or selected_year > datetime.now(timezone.utc).year:
            raise HTTPException(status_code=422, detail="Requested Street View year is out of range")
    else:
        selected_year = None

    work_dir = Path(job.get("work_dir") or (JOBS_ROOT / job_id)).resolve()
    manifest = work_dir / "output" / "agent3_images.json"
    if not manifest.exists():
        raise HTTPException(status_code=404, detail="Street View images are not available yet")

    try:
        sites = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Street View image manifest is invalid") from exc

    matches: List[tuple[Path, str]] = []
    for site in sites if isinstance(sites, list) else []:
        site_id = str(site.get("site_id") or "site")
        for image in site.get("images") or []:
            image_year = str(image.get("date") or "")[:4]
            if selected_year is not None and image_year != str(selected_year):
                continue
            image_path = Path(str(image.get("file_path") or ""))
            if not image_path.is_absolute():
                image_path = work_dir / image_path
            try:
                resolved = image_path.resolve()
                resolved.relative_to(work_dir)
            except (OSError, ValueError):
                continue
            if resolved.is_file():
                matches.append((resolved, f"{image_year or 'unknown'}/{site_id}/{resolved.name}"))

    if not matches:
        label = str(selected_year) if selected_year is not None else "the selected run"
        raise HTTPException(status_code=404, detail=f"No Street View images found for {label}")

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for image_path, archive_name in matches:
            bundle.write(image_path, archive_name)
    year_label = str(selected_year) if selected_year is not None else "latest"
    return Response(
        content=archive.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="streetview_{job_id}_{year_label}.zip"',
            "X-StreetView-Image-Count": str(len(matches)),
        },
    )


def _local_ip() -> str:
    """Best-effort LAN IP so other machines on the network can open the app."""
    # Explicit override for Docker / reverse-proxy / fixed host
    forced = os.environ.get("PUBLIC_HOST") or os.environ.get("APP_HOST")
    if forced:
        return forced.strip()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.5)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


def _access_urls(port: int) -> Dict[str, str]:
    lan = _local_ip()
    return {
        "local": f"http://127.0.0.1:{port}",
        "localhost": f"http://localhost:{port}",
        "network": f"http://{lan}:{port}",
        "lan_ip": lan,
        "port": str(port),
    }


@app.get("/api/health")
def health():
    port = int(os.environ.get("PORT", "8002"))
    urls = _access_urls(port)
    return {
        "status": "ok",
        "agents": [s["key"] for s in STAGES],
        "jobs": len(JOBS_DB),
        "frontend": FRONTEND_ROOT.exists(),
        "urls": urls,
        "message": (
            f"Open on this PC: {urls['localhost']}  |  "
            f"Open from other devices on the same network: {urls['network']}"
        ),
    }


@app.get("/api/access-info")
def access_info():
    """Dynamic URLs for the running server (local + LAN)."""
    port = int(os.environ.get("PORT", "8002"))
    return _access_urls(port)


# ── Serve frontend ────────────────────────────

if FRONTEND_ROOT.exists():
    assets_dir = FRONTEND_ROOT / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/")
    def index():
        index_path = FRONTEND_ROOT / "index.html"
        return HTMLResponse(index_path.read_text(encoding="utf-8"))

    @app.get("/index.html")
    def index_html():
        return HTMLResponse((FRONTEND_ROOT / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8002"))
    host = os.environ.get("BIND_HOST", "0.0.0.0")  # listen on all interfaces
    urls = _access_urls(port)

    print("")
    print("=" * 64)
    print("  Mailbox Detection Studio")
    print("=" * 64)
    print(f"  This PC     : {urls['localhost']}")
    print(f"  Network URL : {urls['network']}")
    print(f"  (same Wi‑Fi / LAN — open from other phones/PCs)")
    print(f"  Frontend    : {FRONTEND_ROOT}")
    print(f"  Agents      : {BACKEND_ROOT / 'agents'}")
    print(f"  Binding     : {host}:{port}")
    print("=" * 64)
    print("")

    # Persist for start_app.bat / other tools
    try:
        (BACKEND_ROOT / ".runtime_urls.json").write_text(
            json.dumps(urls, indent=2), encoding="utf-8"
        )
    except Exception:
        pass

    uvicorn.run(app, host=host, port=port)
