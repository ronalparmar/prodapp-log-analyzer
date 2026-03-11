"""
FastAPI application: upload, parse, search and download Android ShopApp log files.
"""

from __future__ import annotations

import io
import os
import uuid
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .db.models import (
    AppVersion,
    ExceptionEvent,
    LogFile,
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

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="ProdApp Log Analyzer", version="1.0.0")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

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
# Helper: persist parsed results to DB
# ---------------------------------------------------------------------------

def _persist_parse_result(db: Session, log_file_rec: LogFile, parse_result) -> None:
    log_file_rec.device_id = parse_result.device_id
    log_file_rec.package_name = parse_result.package_name
    log_file_rec.environment = parse_result.environment
    log_file_rec.username = parse_result.username

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
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    return templates.TemplateResponse("upload.html", {"request": request})


@app.post("/upload", response_class=HTMLResponse)
async def do_upload(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    ensure_upload_dir(UPLOAD_DIR)
    content = await file.read()
    original_name = file.filename or "upload.txt"
    ext = Path(original_name).suffix.lower()

    # Generate unique storage name
    unique_prefix = uuid.uuid4().hex[:8]
    stored_name = f"{unique_prefix}_{original_name}"
    stored_path = save_upload(stored_name, content)

    upload_rec = Upload(
        filename=original_name,
        stored_path=str(stored_path),
        file_size=len(content),
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
        return templates.TemplateResponse(
            "upload.html",
            {"request": request, "error": "Only .zip or .txt files are supported."},
            status_code=400,
        )

    db.commit()

    return templates.TemplateResponse(
        "upload_success.html",
        {
            "request": request,
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
async def list_uploads_page(request: Request, db: Session = Depends(get_db)):
    uploads = db.query(Upload).order_by(Upload.uploaded_at.desc()).all()
    return templates.TemplateResponse(
        "uploads.html", {"request": request, "uploads": uploads}
    )


@app.get("/uploads/{upload_id}/download")
async def download_upload(upload_id: int, db: Session = Depends(get_db)):
    upload = db.query(Upload).filter(Upload.id == upload_id).first()
    if upload is None or not Path(upload.stored_path).exists():
        return HTMLResponse("File not found", status_code=404)
    return FileResponse(
        upload.stored_path,
        filename=upload.filename,
        media_type="application/octet-stream",
    )


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
    summary = device_summary(db, device_id)
    log_files = db.query(LogFile).filter(LogFile.device_id == device_id).all()
    return templates.TemplateResponse(
        "device.html",
        {"request": request, "device_id": device_id, "summary": summary, "log_files": log_files},
    )


# ---------------------------------------------------------------------------
# REST API endpoints (for CLI and programmatic use)
# ---------------------------------------------------------------------------

@app.get("/api/v1/uploads")
async def api_list_uploads(db: Session = Depends(get_db)):
    uploads = db.query(Upload).order_by(Upload.uploaded_at.desc()).all()
    return [
        {
            "id": u.id,
            "filename": u.filename,
            "uploaded_at": u.uploaded_at.isoformat() if u.uploaded_at else None,
            "file_size": u.file_size,
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

    unique_prefix = uuid.uuid4().hex[:8]
    stored_name = f"{unique_prefix}_{original_name}"
    stored_path = save_upload(stored_name, content)

    upload_rec = Upload(
        filename=original_name,
        stored_path=str(stored_path),
        file_size=len(content),
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
        return {"error": "Only .zip or .txt files are supported."}, 400

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
