"""
FastAPI application: upload, parse, search and download Android ShopApp log files.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import uuid
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from .db.models import (
    AppVersion,
    ExceptionEvent,
    LogFile,
    MasterDataSyncEvent,
    ScanEvent,
    Upload,
    UserEmail,
    get_engine,
    get_session_factory,
    init_db,
)
from .parsing.shopapp_parser import parse_log_lines
from .services.analytics import device_summary, search_summary
from .storage.filesystem import (
    UPLOAD_DIR,
    ensure_upload_dir,
    extract_zip,
    save_upload,
)
from .routes import soti_router
from .auth import (
    SESSION_SECRET,
    SSO_ENABLED,
    _UNPROTECTED,
    get_current_user,
    handle_callback,
    handle_login,
    handle_logout,
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="ProdApp Log Analyzer", version="2.0.0")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# SOTI MobiControl routes
app.include_router(soti_router)

# DB setup
_engine = get_engine()
init_db(_engine)
_SessionFactory = get_session_factory(_engine)


def get_db():
    db = _SessionFactory()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# SSO middleware  (SessionMiddleware must be added LAST → becomes outermost)
# ---------------------------------------------------------------------------

@app.middleware("http")
async def sso_guard(request: Request, call_next):
    """Redirect unauthenticated users to the Microsoft login page."""
    if SSO_ENABLED:
        path = request.url.path
        if not any(path.startswith(p) for p in _UNPROTECTED):
            user = request.session.get("user")
            if not user:
                return RedirectResponse(f"/auth/login?next={path}")
    return await call_next(request)


# SessionMiddleware must be added after @app.middleware so it becomes the
# outermost layer and populates request.session before sso_guard runs.
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=False)


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.get("/auth/login")
async def auth_login(request: Request):
    return await handle_login(request)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    return await handle_callback(request)


@app.get("/auth/logout")
async def auth_logout(request: Request):
    return await handle_logout(request)


# ---------------------------------------------------------------------------
# Helper: persist parsed results to DB
# ---------------------------------------------------------------------------

def _persist_parse_result(db: Session, log_file_rec: LogFile, parse_result) -> None:
    log_file_rec.device_id   = parse_result.device_id
    log_file_rec.package_name = parse_result.package_name
    log_file_rec.environment  = parse_result.environment
    log_file_rec.username     = parse_result.username

    for v in parse_result.app_versions:
        db.add(AppVersion(log_file_id=log_file_rec.id, version=v))

    for e in parse_result.emails:
        db.add(UserEmail(log_file_id=log_file_rec.id, email=e))

    for se in parse_result.scan_events:
        db.add(
            ScanEvent(
                log_file_id=log_file_rec.id,
                timestamp=se.timestamp,
                raw_ts=se.raw_ts,
                item_number=se.item_number,
                barcode_format=se.barcode_format,
                entry_mode=se.entry_mode,
                process=se.process,
                line_number=se.line_number,
                event_id=se.event_id,
                return_state=se.return_state,
            )
        )

    for ex in parse_result.exception_events:
        db.add(
            ExceptionEvent(
                log_file_id=log_file_rec.id,
                timestamp=ex.timestamp,
                raw_ts=ex.raw_ts,
                exception_type=ex.exception_type,
                message=ex.message,
                context_text="\n".join(ex.context_lines),
                line_number=ex.line_number,
            )
        )

    for ms in parse_result.master_sync_events:
        db.add(
            MasterDataSyncEvent(
                log_file_id=log_file_rec.id,
                timestamp=ms.timestamp,
                raw_ts=ms.raw_ts,
                info=ms.info,
                line_number=ms.line_number,
            )
        )

    db.commit()


def _ingest_txt_file(
    db: Session,
    upload_rec: Upload,
    txt_path: Path,
    filename: str,
) -> LogFile:
    """Parse a .txt file and store results in the DB."""
    with open(txt_path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    parse_result = parse_log_lines(lines)

    log_file_rec = LogFile(
        upload_id=upload_rec.id,
        filename=filename,
        stored_path=str(txt_path),
    )
    db.add(log_file_rec)
    db.flush()  # get ID

    _persist_parse_result(db, log_file_rec, parse_result)
    return log_file_rec


# ---------------------------------------------------------------------------
# Routes: Web UI
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = get_current_user(request)
    return templates.TemplateResponse("index.html", {"request": request, "user": user})


@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    user = get_current_user(request)
    return templates.TemplateResponse("upload.html", {"request": request, "user": user})


@app.post("/upload", response_class=HTMLResponse)
async def do_upload(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    ensure_upload_dir(UPLOAD_DIR)
    content = await file.read()
    original_name = file.filename or "upload.txt"
    ext = Path(original_name).suffix.lower()

    # ── Duplicate check ──────────────────────────────────────────────────
    file_hash = hashlib.sha256(content).hexdigest()
    existing = (
        db.query(Upload)
        .filter(Upload.file_hash == file_hash, Upload.is_deleted == False)
        .first()
    )
    if existing:
        return templates.TemplateResponse(
            "upload.html",
            {
                "request": request,
                "user": user,
                "error": (
                    f"This file was already uploaded as '{existing.filename}' "
                    f"(Upload #{existing.id}, {existing.uploaded_at.strftime('%d %b %Y %H:%M') if existing.uploaded_at else 'unknown date'}). "
                    f"Use the Reparse option if you want to re-process it."
                ),
                "duplicate_upload_id": existing.id,
            },
            status_code=200,
        )

    # Generate unique storage name
    unique_prefix = uuid.uuid4().hex[:8]
    stored_name = f"{unique_prefix}_{original_name}"
    stored_path = save_upload(stored_name, content)

    upload_rec = Upload(
        filename=original_name,
        stored_path=str(stored_path),
        file_size=len(content),
        file_hash=file_hash,
    )
    db.add(upload_rec)
    db.flush()

    log_files_ingested: list[LogFile] = []

    if ext == ".zip":
        extract_dir = Path(UPLOAD_DIR) / f"{unique_prefix}_extracted"
        txt_paths = extract_zip(stored_path, extract_dir)
        for txt_path in txt_paths:
            lf = _ingest_txt_file(db, upload_rec, txt_path, txt_path.name)
            log_files_ingested.append(lf)
    elif ext == ".txt":
        lf = _ingest_txt_file(db, upload_rec, stored_path, original_name)
        log_files_ingested.append(lf)
    else:
        db.rollback()
        try:
            stored_path.unlink(missing_ok=True)
        except Exception:
            pass
        return templates.TemplateResponse(
            "upload.html",
            {"request": request, "user": user, "error": "Only .zip or .txt files are supported."},
            status_code=400,
        )

    db.commit()

    return templates.TemplateResponse(
        "upload_success.html",
        {
            "request": request,
            "user": user,
            "upload": upload_rec,
            "log_files": log_files_ingested,
        },
    )


@app.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request,
    q: Optional[str] = None,
    search_type: Optional[str] = "item",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    environment: Optional[str] = None,
    process: Optional[str] = None,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    scan_events: list[ScanEvent] = []
    exception_events: list[ExceptionEvent] = []
    summary = {}
    error = None

    if q:
        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d") if date_from else None
            dt_to = datetime.strptime(date_to, "%Y-%m-%d") if date_to else None
        except ValueError:
            dt_from = dt_to = None
            error = "Invalid date format. Use YYYY-MM-DD."

        if search_type == "item":
            q_scan = db.query(ScanEvent).filter(ScanEvent.item_number.contains(q))
            if dt_from:
                q_scan = q_scan.filter(ScanEvent.timestamp >= dt_from)
            if dt_to:
                q_scan = q_scan.filter(ScanEvent.timestamp <= dt_to)
            if process:
                q_scan = q_scan.filter(ScanEvent.process.ilike(f"%{process}%"))
            if environment:
                q_scan = q_scan.join(LogFile).filter(LogFile.environment == environment.upper())
            scan_events = q_scan.order_by(ScanEvent.timestamp).all()

        elif search_type == "device":
            log_files = (
                db.query(LogFile)
                .filter(LogFile.device_id.ilike(f"%{q}%"))
                .all()
            )
            if environment:
                log_files = [lf for lf in log_files if lf.environment == environment.upper()]
            lf_ids = [lf.id for lf in log_files]
            if lf_ids:
                q_scan = db.query(ScanEvent).filter(ScanEvent.log_file_id.in_(lf_ids))
                if dt_from:
                    q_scan = q_scan.filter(ScanEvent.timestamp >= dt_from)
                if dt_to:
                    q_scan = q_scan.filter(ScanEvent.timestamp <= dt_to)
                if process:
                    q_scan = q_scan.filter(ScanEvent.process.ilike(f"%{process}%"))
                scan_events = q_scan.order_by(ScanEvent.timestamp).all()
                q_exc = db.query(ExceptionEvent).filter(ExceptionEvent.log_file_id.in_(lf_ids))
                if dt_from:
                    q_exc = q_exc.filter(ExceptionEvent.timestamp >= dt_from)
                if dt_to:
                    q_exc = q_exc.filter(ExceptionEvent.timestamp <= dt_to)
                exception_events = q_exc.order_by(ExceptionEvent.timestamp).all()

        elif search_type == "exception":
            q_exc = db.query(ExceptionEvent).filter(
                ExceptionEvent.exception_type.ilike(f"%{q}%")
                | ExceptionEvent.message.ilike(f"%{q}%")
            )
            if dt_from:
                q_exc = q_exc.filter(ExceptionEvent.timestamp >= dt_from)
            if dt_to:
                q_exc = q_exc.filter(ExceptionEvent.timestamp <= dt_to)
            if environment:
                q_exc = q_exc.join(LogFile).filter(LogFile.environment == environment.upper())
            exception_events = q_exc.order_by(ExceptionEvent.timestamp).all()

        summary = search_summary(scan_events, exception_events)

    return templates.TemplateResponse(
        "search.html",
        {
            "request": request,
            "user": user,
            "q": q or "",
            "search_type": search_type,
            "date_from": date_from or "",
            "date_to": date_to or "",
            "environment": environment or "",
            "process": process or "",
            "scan_events": scan_events,
            "exception_events": exception_events,
            "summary": summary,
            "error": error,
        },
    )


@app.get("/uploads", response_class=HTMLResponse)
async def list_uploads_page(
    request: Request,
    package: Optional[str] = None,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    q = db.query(Upload).filter(Upload.is_deleted == False)
    uploads = q.order_by(Upload.uploaded_at.desc()).all()

    # Gather distinct packages for filter dropdown
    all_pkgs = sorted({
        lf.package_name
        for u in uploads
        for lf in u.log_files
        if lf.package_name
    })

    # Client-side package filtering (also supported server-side)
    if package:
        uploads = [
            u for u in uploads
            if any(lf.package_name == package for lf in u.log_files)
        ]

    return templates.TemplateResponse(
        "uploads.html",
        {"request": request, "user": user, "uploads": uploads, "all_pkgs": all_pkgs, "pkg_filter": package or ""},
    )


@app.get("/uploads/{upload_id}", response_class=HTMLResponse)
async def upload_detail(upload_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    upload = db.query(Upload).filter(Upload.id == upload_id, Upload.is_deleted == False).first()
    if not upload:
        return HTMLResponse("Upload not found.", status_code=404)
    log_files = db.query(LogFile).filter(LogFile.upload_id == upload_id).all()
    return templates.TemplateResponse(
        "upload_success.html",
        {"request": request, "user": user, "upload": upload, "log_files": log_files},
    )


@app.get("/uploads/{upload_id}/download")
async def download_upload(upload_id: int, db: Session = Depends(get_db)):
    upload = db.query(Upload).filter(Upload.id == upload_id, Upload.is_deleted == False).first()
    if upload is None or not Path(upload.stored_path).exists():
        return HTMLResponse("File not found", status_code=404)
    return FileResponse(
        upload.stored_path,
        filename=upload.filename,
        media_type="application/octet-stream",
    )


@app.post("/uploads/{upload_id}/delete")
async def delete_upload(upload_id: int, db: Session = Depends(get_db)):
    """Hard-delete an upload: removes DB record (cascades) and stored files."""
    upload = db.query(Upload).filter(Upload.id == upload_id).first()
    if not upload:
        return HTMLResponse("Not found", status_code=404)

    # Remove stored file(s) from disk
    try:
        stored = Path(upload.stored_path)
        if stored.exists():
            stored.unlink()
        # Remove extracted ZIP directory if present
        ext_dir = stored.parent / (stored.stem.split("_")[0] + "_extracted")
        if ext_dir.exists():
            shutil.rmtree(ext_dir)
    except Exception:
        pass  # log but don't block the delete

    db.delete(upload)
    db.commit()
    return RedirectResponse("/uploads", status_code=303)


@app.post("/uploads/{upload_id}/reparse")
async def reparse_upload(upload_id: int, db: Session = Depends(get_db)):
    """Delete all parsed data for an upload and re-run the parser from stored files."""
    upload = db.query(Upload).filter(Upload.id == upload_id, Upload.is_deleted == False).first()
    if not upload:
        return HTMLResponse("Upload not found.", status_code=404)

    # Delete existing log files (cascades to scans / exceptions / master_sync)
    for lf in list(upload.log_files):
        db.delete(lf)
    db.flush()

    ext = Path(upload.filename).suffix.lower()
    log_files_ingested: list[LogFile] = []

    if ext == ".zip":
        stored = Path(upload.stored_path)
        unique_prefix = stored.stem.split("_")[0]
        extract_dir = stored.parent / f"{unique_prefix}_extracted"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        txt_paths = extract_zip(stored, extract_dir)
        for txt_path in txt_paths:
            lf = _ingest_txt_file(db, upload, txt_path, txt_path.name)
            log_files_ingested.append(lf)
    elif ext == ".txt":
        lf = _ingest_txt_file(db, upload, Path(upload.stored_path), upload.filename)
        log_files_ingested.append(lf)

    db.commit()
    return RedirectResponse(f"/uploads/{upload_id}", status_code=303)


@app.get("/logfiles/{log_file_id}/download")
async def download_log_file(log_file_id: int, db: Session = Depends(get_db)):
    lf = db.query(LogFile).filter(LogFile.id == log_file_id).first()
    if lf is None or not Path(lf.stored_path).exists():
        return HTMLResponse("File not found", status_code=404)
    return FileResponse(
        lf.stored_path,
        filename=lf.filename,
        media_type="text/plain",
    )


@app.get("/devices/{device_id}", response_class=HTMLResponse)
async def device_detail(device_id: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    summary = device_summary(db, device_id)
    log_files = db.query(LogFile).filter(LogFile.device_id == device_id).all()
    lf_ids = [lf.id for lf in log_files]
    master_syncs = (
        db.query(MasterDataSyncEvent)
        .filter(MasterDataSyncEvent.log_file_id.in_(lf_ids))
        .order_by(MasterDataSyncEvent.timestamp)
        .all()
        if lf_ids else []
    )
    return templates.TemplateResponse(
        "device.html",
        {
            "request": request,
            "user": user,
            "device_id": device_id,
            "summary": summary,
            "log_files": log_files,
            "master_syncs": master_syncs,
        },
    )


# ---------------------------------------------------------------------------
# REST API endpoints (for CLI and programmatic use)
# ---------------------------------------------------------------------------

@app.get("/api/v1/uploads")
async def api_list_uploads(db: Session = Depends(get_db)):
    uploads = db.query(Upload).filter(Upload.is_deleted == False).order_by(Upload.uploaded_at.desc()).all()
    return [
        {
            "id": u.id,
            "filename": u.filename,
            "uploaded_at": u.uploaded_at.isoformat() if u.uploaded_at else None,
            "file_size": u.file_size,
            "file_hash": u.file_hash,
        }
        for u in uploads
    ]


@app.post("/api/v1/uploads")
async def api_upload(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    ensure_upload_dir(UPLOAD_DIR)
    content = await file.read()
    original_name = file.filename or "upload.txt"
    ext = Path(original_name).suffix.lower()

    file_hash = hashlib.sha256(content).hexdigest()
    existing = db.query(Upload).filter(Upload.file_hash == file_hash, Upload.is_deleted == False).first()
    if existing:
        return JSONResponse({"duplicate": True, "existing_upload_id": existing.id}, status_code=200)

    unique_prefix = uuid.uuid4().hex[:8]
    stored_name = f"{unique_prefix}_{original_name}"
    stored_path = save_upload(stored_name, content)

    upload_rec = Upload(
        filename=original_name,
        stored_path=str(stored_path),
        file_size=len(content),
        file_hash=file_hash,
    )
    db.add(upload_rec)
    db.flush()

    log_file_ids: list[int] = []

    if ext == ".zip":
        extract_dir = Path(UPLOAD_DIR) / f"{unique_prefix}_extracted"
        txt_paths = extract_zip(stored_path, extract_dir)
        for txt_path in txt_paths:
            lf = _ingest_txt_file(db, upload_rec, txt_path, txt_path.name)
            log_file_ids.append(lf.id)
    elif ext == ".txt":
        lf = _ingest_txt_file(db, upload_rec, stored_path, original_name)
        log_file_ids.append(lf.id)
    else:
        db.rollback()
        return JSONResponse({"error": "Only .zip or .txt files are supported."}, status_code=400)

    db.commit()
    return {"upload_id": upload_rec.id, "log_file_ids": log_file_ids}


@app.get("/api/v1/search")
async def api_search(
    q: str,
    search_type: str = "item",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    environment: Optional[str] = None,
    process: Optional[str] = None,
    db: Session = Depends(get_db),
):
    try:
        dt_from = datetime.strptime(date_from, "%Y-%m-%d") if date_from else None
        dt_to = datetime.strptime(date_to, "%Y-%m-%d") if date_to else None
    except ValueError:
        dt_from = dt_to = None

    scan_events: list[ScanEvent] = []
    exception_events: list[ExceptionEvent] = []

    if search_type == "item":
        q_scan = db.query(ScanEvent).filter(ScanEvent.item_number.contains(q))
        if dt_from:
            q_scan = q_scan.filter(ScanEvent.timestamp >= dt_from)
        if dt_to:
            q_scan = q_scan.filter(ScanEvent.timestamp <= dt_to)
        scan_events = q_scan.order_by(ScanEvent.timestamp).all()

    elif search_type == "device":
        log_files = db.query(LogFile).filter(LogFile.device_id.ilike(f"%{q}%")).all()
        lf_ids = [lf.id for lf in log_files]
        if lf_ids:
            scan_events = db.query(ScanEvent).filter(ScanEvent.log_file_id.in_(lf_ids)).all()
            exception_events = db.query(ExceptionEvent).filter(ExceptionEvent.log_file_id.in_(lf_ids)).all()

    elif search_type == "exception":
        exception_events = (
            db.query(ExceptionEvent)
            .filter(ExceptionEvent.exception_type.ilike(f"%{q}%") | ExceptionEvent.message.ilike(f"%{q}%"))
            .all()
        )

    summary = search_summary(scan_events, exception_events)

    return {
        "query": q,
        "search_type": search_type,
        "summary": summary,
        "scan_events": [
            {
                "id": s.id,
                "timestamp": s.raw_ts,
                "item_number": s.item_number,
                "barcode_format": s.barcode_format,
                "entry_mode": s.entry_mode,
                "process": s.process,
                "event_id": s.event_id,
                "return_state": s.return_state,
                "log_file_id": s.log_file_id,
            }
            for s in scan_events
        ],
        "exception_events": [
            {
                "id": e.id,
                "timestamp": e.raw_ts,
                "exception_type": e.exception_type,
                "message": e.message,
                "log_file_id": e.log_file_id,
            }
            for e in exception_events
        ],
    }


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

