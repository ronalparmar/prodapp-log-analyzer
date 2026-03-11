"""
Analytics service: derive stats from scan and exception events.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import ExceptionEvent, LogFile, ScanEvent


def device_summary(db: Session, device_id: str) -> dict[str, Any]:
    """Return a summary dict for a given device ID."""
    log_files = db.query(LogFile).filter(LogFile.device_id == device_id).all()
    if not log_files:
        return {}

    lf_ids = [lf.id for lf in log_files]
    scans = (
        db.query(ScanEvent)
        .filter(ScanEvent.log_file_id.in_(lf_ids))
        .all()
    )
    exceptions = (
        db.query(ExceptionEvent)
        .filter(ExceptionEvent.log_file_id.in_(lf_ids))
        .all()
    )

    scan_count = sum(1 for s in scans if s.entry_mode == "scan")
    manual_count = sum(1 for s in scans if s.entry_mode == "manual")

    item_freq: Counter = Counter(s.item_number for s in scans)

    versions = list({v for lf in log_files for v in [av.version for av in lf.app_versions]})
    emails = list({e.email for lf in log_files for e in lf.emails})

    return {
        "device_id": device_id,
        "log_file_count": len(log_files),
        "scan_count": scan_count,
        "manual_count": manual_count,
        "exception_count": len(exceptions),
        "top_items": item_freq.most_common(10),
        "app_versions": versions,
        "emails": emails,
        "environments": list({lf.environment for lf in log_files if lf.environment}),
    }


def search_summary(scan_events: list[ScanEvent], exception_events: list[ExceptionEvent]) -> dict[str, Any]:
    """Compute summary stats for an arbitrary set of events (search result)."""
    scan_count = sum(1 for s in scan_events if s.entry_mode == "scan")
    manual_count = sum(1 for s in scan_events if s.entry_mode == "manual")
    item_freq: Counter = Counter(s.item_number for s in scan_events)
    exc_types: Counter = Counter(e.exception_type for e in exception_events)

    return {
        "scan_count": scan_count,
        "manual_count": manual_count,
        "exception_count": len(exception_events),
        "top_items": item_freq.most_common(10),
        "top_exceptions": exc_types.most_common(5),
    }
