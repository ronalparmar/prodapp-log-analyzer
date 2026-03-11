#!/usr/bin/env python3
"""
ProdApp Log Analyzer CLI

Commands:
  ingest   - Ingest a local .txt or .zip log file
  search   - Search events by item/device/exception
  report   - Produce a JSON or CSV summary for a device
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

# Allow running from the cli/ folder or from the repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.db.models import (
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
from app.parsing.shopapp_parser import parse_log_file, parse_log_lines
from app.services.analytics import device_summary, search_summary
from app.storage.filesystem import UPLOAD_DIR, extract_zip, save_upload


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_db(db_url: str | None = None):
    engine = get_engine(db_url or os.environ.get("DATABASE_URL", f"sqlite:///{_REPO_ROOT}/backend/data/log_analyzer.db"))
    init_db(engine)
    factory = get_session_factory(engine)
    return factory()


def _persist(db, upload_rec, log_file_rec, parse_result):
    from app.db.models import AppVersion, ExceptionEvent, ScanEvent, UserEmail

    log_file_rec.device_id = parse_result.device_id
    log_file_rec.package_name = parse_result.package_name
    log_file_rec.environment = parse_result.environment
    log_file_rec.username = parse_result.username

    for v in parse_result.app_versions:
        db.add(AppVersion(log_file_id=log_file_rec.id, version=v))
    for e in parse_result.emails:
        db.add(UserEmail(log_file_id=log_file_rec.id, email=e))
    for se in parse_result.scan_events:
        db.add(ScanEvent(
            log_file_id=log_file_rec.id,
            timestamp=se.timestamp, raw_ts=se.raw_ts,
            item_number=se.item_number, barcode_format=se.barcode_format,
            entry_mode=se.entry_mode, process=se.process, line_number=se.line_number,
        ))
    for ex in parse_result.exception_events:
        db.add(ExceptionEvent(
            log_file_id=log_file_rec.id,
            timestamp=ex.timestamp, raw_ts=ex.raw_ts,
            exception_type=ex.exception_type, message=ex.message,
            context_text="\n".join(ex.context_lines), line_number=ex.line_number,
        ))
    db.commit()


def _ingest_file(db, txt_path: Path, upload_id: int, filename: str):
    parse_result = parse_log_file(str(txt_path))
    lf = LogFile(upload_id=upload_id, filename=filename, stored_path=str(txt_path))
    db.add(lf)
    db.flush()
    _persist(db, None, lf, parse_result)
    return lf, parse_result


# ---------------------------------------------------------------------------
# ingest command
# ---------------------------------------------------------------------------

def cmd_ingest(args):
    db = _get_db(args.db)
    path = Path(args.file)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    content = path.read_bytes()
    upload_dir = Path(UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_path = save_upload(path.name, content, str(upload_dir))

    upload_rec = Upload(filename=path.name, stored_path=str(stored_path), file_size=len(content))
    db.add(upload_rec)
    db.flush()

    ingested = []
    if path.suffix.lower() == ".zip":
        import uuid
        extract_dir = upload_dir / f"{uuid.uuid4().hex[:8]}_extracted"
        txt_paths = extract_zip(stored_path, extract_dir)
        for txt_path in txt_paths:
            lf, pr = _ingest_file(db, txt_path, upload_rec.id, txt_path.name)
            ingested.append((lf, pr))
    elif path.suffix.lower() == ".txt":
        lf, pr = _ingest_file(db, stored_path, upload_rec.id, path.name)
        ingested.append((lf, pr))
    else:
        print("Error: only .txt or .zip files are supported.", file=sys.stderr)
        sys.exit(1)

    db.commit()
    print(f"✅ Ingested upload #{upload_rec.id}: {path.name}")
    for lf, pr in ingested:
        print(f"  📄 Log file #{lf.id}: {lf.filename}")
        print(f"     Device:       {pr.device_id or '—'}")
        print(f"     Package:      {pr.package_name or '—'}")
        print(f"     Environment:  {pr.environment or '—'}")
        print(f"     Versions:     {', '.join(pr.app_versions) or '—'}")
        print(f"     Scan events:  {len(pr.scan_events)}")
        print(f"     Exceptions:   {len(pr.exception_events)}")


# ---------------------------------------------------------------------------
# search command
# ---------------------------------------------------------------------------

def cmd_search(args):
    db = _get_db(args.db)

    scan_events = []
    exception_events = []

    if args.item:
        scan_events = db.query(ScanEvent).filter(ScanEvent.item_number.contains(args.item)).all()
    elif args.device:
        log_files = db.query(LogFile).filter(LogFile.device_id.ilike(f"%{args.device}%")).all()
        lf_ids = [lf.id for lf in log_files]
        if lf_ids:
            scan_events = db.query(ScanEvent).filter(ScanEvent.log_file_id.in_(lf_ids)).all()
            exception_events = db.query(ExceptionEvent).filter(ExceptionEvent.log_file_id.in_(lf_ids)).all()
    elif args.exception:
        exception_events = db.query(ExceptionEvent).filter(
            ExceptionEvent.exception_type.ilike(f"%{args.exception}%")
            | ExceptionEvent.message.ilike(f"%{args.exception}%")
        ).all()
    else:
        print("Error: provide --item, --device, or --exception", file=sys.stderr)
        sys.exit(1)

    fmt = args.format or "table"

    if fmt == "json":
        result = {
            "scan_events": [
                {"timestamp": s.raw_ts, "item_number": s.item_number,
                 "entry_mode": s.entry_mode, "process": s.process,
                 "barcode_format": s.barcode_format}
                for s in scan_events
            ],
            "exception_events": [
                {"timestamp": e.raw_ts, "exception_type": e.exception_type, "message": e.message}
                for e in exception_events
            ],
        }
        print(json.dumps(result, indent=2, default=str))
    else:
        if scan_events:
            print(f"\n{'TIMESTAMP':<30} {'ITEM':<25} {'MODE':<8} {'PROCESS'}")
            print("-" * 80)
            for s in scan_events:
                print(f"{s.raw_ts:<30} {s.item_number:<25} {s.entry_mode:<8} {s.process}")
        if exception_events:
            print(f"\n{'TIMESTAMP':<30} {'TYPE':<40} {'MESSAGE'}")
            print("-" * 100)
            for e in exception_events:
                msg = (e.message or "")[:60]
                print(f"{e.raw_ts:<30} {(e.exception_type or ''):<40} {msg}")
        if not scan_events and not exception_events:
            print("No results found.")


# ---------------------------------------------------------------------------
# report command
# ---------------------------------------------------------------------------

def cmd_report(args):
    db = _get_db(args.db)

    if args.device:
        summary = device_summary(db, args.device)
        if not summary:
            print(f"No data for device {args.device}", file=sys.stderr)
            sys.exit(1)
    else:
        # All devices summary
        log_files = db.query(LogFile).all()
        devices = list({lf.device_id for lf in log_files if lf.device_id})
        summary = {"devices": [device_summary(db, d) for d in devices]}

    fmt = args.format or "json"

    if fmt == "csv":
        if args.device:
            rows = [
                {"device_id": summary["device_id"],
                 "scan_count": summary["scan_count"],
                 "manual_count": summary["manual_count"],
                 "exception_count": summary["exception_count"],
                 "log_file_count": summary["log_file_count"],
                 "environments": ",".join(summary.get("environments", [])),
                 "app_versions": ",".join(summary.get("app_versions", []))}
            ]
        else:
            rows = [
                {"device_id": s.get("device_id", ""),
                 "scan_count": s.get("scan_count", 0),
                 "manual_count": s.get("manual_count", 0),
                 "exception_count": s.get("exception_count", 0),
                 "log_file_count": s.get("log_file_count", 0),
                 "environments": ",".join(s.get("environments", [])),
                 "app_versions": ",".join(s.get("app_versions", []))}
                for s in summary.get("devices", [])
            ]
        if rows:
            writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    else:
        print(json.dumps(summary, indent=2, default=str))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="shopapp-logs",
        description="ProdApp Log Analyzer CLI",
    )
    parser.add_argument("--db", help="SQLAlchemy database URL (overrides DATABASE_URL env var)")
    sub = parser.add_subparsers(dest="command", required=True)

    # ingest
    p_ingest = sub.add_parser("ingest", help="Ingest a log file (.txt or .zip)")
    p_ingest.add_argument("file", help="Path to the log file")
    p_ingest.set_defaults(func=cmd_ingest)

    # search
    p_search = sub.add_parser("search", help="Search events")
    p_search.add_argument("--item", help="Search by item number")
    p_search.add_argument("--device", help="Search by device ID")
    p_search.add_argument("--exception", help="Search by exception type/message")
    p_search.add_argument("--format", choices=["table", "json"], default="table", help="Output format")
    p_search.set_defaults(func=cmd_search)

    # report
    p_report = sub.add_parser("report", help="Generate a summary report")
    p_report.add_argument("--device", help="Limit report to a specific device ID")
    p_report.add_argument("--format", choices=["json", "csv"], default="json", help="Output format")
    p_report.set_defaults(func=cmd_report)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
