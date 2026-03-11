"""
Parser for Posten Bring ShopApp Android log files.

Each line format:  YYYY-MM-DD HH:MM:SS.mmmm <message>
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator, Optional

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_TS_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\s+(?P<msg>.*)$"
)
# "Device set up ---- Device Id - TC5705956"  (any device-ID format after the dash)
_DEVICE_ID_RE = re.compile(
    r"Device set up\s*-+\s*Device Id\s*[-:]\s*(?P<deviceId>[A-Za-z0-9_\-]+)",
    re.IGNORECASE,
)
# Fallback for bare "Device Id - <id>" lines (no "Device set up" prefix)
_DEVICE_ID_FALLBACK_RE = re.compile(
    r"\bDevice Id\s*[-:]\s*(?P<deviceId>[A-Za-z0-9_\-]+)",
    re.IGNORECASE,
)
_APP_VERSION_RE = re.compile(r"App version\s*----\s*(?P<version>[\d.]+)", re.IGNORECASE)
_EMAIL_VALIDATED_RE = re.compile(
    r"Validated User & Authorised User\s*-\s*(?P<email>[^,\s]+@[^,\s]+)",
    re.IGNORECASE,
)
_EMAIL_PROFILE_RE = re.compile(
    r"Profile information received for user\s*:\s*(?P<email>[^\s,]+@[^\s,]+)",
    re.IGNORECASE,
)
_AUTO_LOGIN_RE = re.compile(
    r"Auto login & Authorised User\s*-\s*(?P<user>[^,\s]+)", re.IGNORECASE
)
_PKG_RE = re.compile(
    r"/data/user/0/(?P<pkg>com\.postenbring\.[^/]+)/", re.IGNORECASE
)
_SCAN_RE = re.compile(
    r"Scanned package number\s+(?P<item>\S+)\s+bar code format\s+(?P<fmt>\S+)\s+in\s+(?P<process>\S+)\s+process",
    re.IGNORECASE,
)
# "GoodsEvent inserted: 601cd1b99d58411eb52a13d4931d2462"
_GOODS_EVENT_RE = re.compile(
    r"GoodsEvent inserted\s*:\s*(?P<guid>[a-fA-F0-9]{8,64})",
    re.IGNORECASE,
)
# "Master data sync :: <info>"
_MASTER_SYNC_RE = re.compile(
    r"Master data sync\s*::\s*(?P<info>.+)",
    re.IGNORECASE,
)
# "Return state is <state>"
_RETURN_STATE_RE = re.compile(
    r"Return state is\s+(?P<state>.+)",
    re.IGNORECASE,
)
_EXCEPTION_RE = re.compile(r"Exception:", re.IGNORECASE)
_UNHANDLED_RE = re.compile(r"\bUnhandled\b", re.IGNORECASE)
_TASK_SCHEDULER_EX_RE = re.compile(r"TaskSchedulerOnUnobservedTaskException", re.IGNORECASE)

# Session start markers
_SESSION_MARKERS = ("CreateWindow:", "@LoginViewModel", "@ScanningViewModel")

# Lines to look ahead after a scan to find associated GoodsEvent/ReturnState
_SCAN_LOOKAHEAD = 15


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class LogLine:
    timestamp: Optional[datetime]
    raw_ts: str
    message: str
    line_number: int


@dataclass
class ScanEvent:
    timestamp: Optional[datetime]
    raw_ts: str
    item_number: str
    barcode_format: str
    entry_mode: str          # "scan" or "manual"
    process: str
    line_number: int
    event_id: Optional[str] = None       # GUID from "GoodsEvent inserted: <guid>"
    return_state: Optional[str] = None   # from "Return state is <state>"


@dataclass
class ExceptionEvent:
    timestamp: Optional[datetime]
    raw_ts: str
    exception_type: str
    message: str
    context_lines: list[str] = field(default_factory=list)
    line_number: int = 0


@dataclass
class MasterDataSyncEvent:
    timestamp: Optional[datetime]
    raw_ts: str
    info: str
    line_number: int


@dataclass
class ParseResult:
    device_id: Optional[str] = None
    app_versions: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    username: Optional[str] = None
    package_name: Optional[str] = None
    environment: Optional[str] = None
    scan_events: list[ScanEvent] = field(default_factory=list)
    exception_events: list[ExceptionEvent] = field(default_factory=list)
    master_sync_events: list[MasterDataSyncEvent] = field(default_factory=list)
    all_lines: list[LogLine] = field(default_factory=list)
    sessions: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_ts(raw_ts: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw_ts.strip(), fmt)
        except ValueError:
            continue
    return None


def _classify_entry_mode(barcode_format: str) -> str:
    return "manual" if barcode_format.strip().lower() == "manualinput" else "scan"


def _determine_environment(package_name: Optional[str]) -> Optional[str]:
    if package_name is None:
        return None
    return "QA" if package_name.lower().endswith("qa") else "PROD"


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_log_lines(
    lines: list[str],
    exception_context: int = 10,
    session_gap_minutes: int = 20,
) -> ParseResult:
    """Parse a list of raw log lines into a structured :class:`ParseResult`."""

    result = ParseResult()
    parsed_lines: list[LogLine] = []

    # First pass: parse timestamp + message from every line
    for idx, raw in enumerate(lines, start=1):
        raw = raw.rstrip("\n")
        m = _TS_RE.match(raw)
        if m:
            raw_ts = m.group("ts")
            msg = m.group("msg")
            ts = _parse_ts(raw_ts)
        else:
            raw_ts = ""
            msg = raw
            ts = None
        parsed_lines.append(LogLine(timestamp=ts, raw_ts=raw_ts, message=msg, line_number=idx))

    result.all_lines = parsed_lines

    # Second pass: extract metadata + events
    for idx, pl in enumerate(parsed_lines):
        msg = pl.message

        # deviceId — "Device set up ---- Device Id - TC5705956"
        m = _DEVICE_ID_RE.search(msg)
        if m and result.device_id is None:
            result.device_id = m.group("deviceId")
        elif result.device_id is None:
            m = _DEVICE_ID_FALLBACK_RE.search(msg)
            if m:
                result.device_id = m.group("deviceId")

        # appVersion
        m = _APP_VERSION_RE.search(msg)
        if m:
            v = m.group("version")
            if v not in result.app_versions:
                result.app_versions.append(v)

        # email (validated)
        m = _EMAIL_VALIDATED_RE.search(msg)
        if m:
            email = m.group("email")
            if email not in result.emails:
                result.emails.append(email)

        # email (profile)
        m = _EMAIL_PROFILE_RE.search(msg)
        if m:
            email = m.group("email")
            if email not in result.emails:
                result.emails.append(email)

        # auto-login username
        m = _AUTO_LOGIN_RE.search(msg)
        if m and result.username is None:
            result.username = m.group("user")

        # packageName
        m = _PKG_RE.search(msg)
        if m:
            pkg = m.group("pkg")
            if result.package_name is None:
                result.package_name = pkg
                result.environment = _determine_environment(pkg)

        # master data sync
        m = _MASTER_SYNC_RE.search(msg)
        if m:
            result.master_sync_events.append(
                MasterDataSyncEvent(
                    timestamp=pl.timestamp,
                    raw_ts=pl.raw_ts,
                    info=m.group("info").strip(),
                    line_number=pl.line_number,
                )
            )
            continue  # not also an exception

        # scan event
        m = _SCAN_RE.search(msg)
        if m:
            entry_mode = _classify_entry_mode(m.group("fmt"))
            result.scan_events.append(
                ScanEvent(
                    timestamp=pl.timestamp,
                    raw_ts=pl.raw_ts,
                    item_number=m.group("item"),
                    barcode_format=m.group("fmt"),
                    entry_mode=entry_mode,
                    process=m.group("process"),
                    line_number=pl.line_number,
                )
            )
            continue  # scan lines are not also exceptions

        # exception event
        is_exception = (
            _EXCEPTION_RE.search(msg)
            or _UNHANDLED_RE.search(msg)
            or _TASK_SCHEDULER_EX_RE.search(msg)
        )
        if is_exception:
            exc_type = msg.split(":")[0].strip() if ":" in msg else msg.strip()
            exc_msg = msg[len(exc_type) + 1:].strip() if ":" in msg else ""
            start = max(0, idx - exception_context)
            end = min(len(parsed_lines), idx + exception_context + 1)
            ctx = [pl2.raw_ts + " " + pl2.message for pl2 in parsed_lines[start:end]]
            result.exception_events.append(
                ExceptionEvent(
                    timestamp=pl.timestamp,
                    raw_ts=pl.raw_ts,
                    exception_type=exc_type,
                    message=exc_msg,
                    context_lines=ctx,
                    line_number=pl.line_number,
                )
            )

    # Post-processing: associate GoodsEvent GUID and return_state with each scan.
    # For each scan at line_number N (1-based), look at parsed_lines[N:N+LOOKAHEAD]
    # for the nearest GoodsEvent and Return‑state lines.
    for scan in result.scan_events:
        start_idx = scan.line_number  # lines AFTER the scan (line_number is 1-based → 0-based index = N-1, so index N is the next line)
        for pl in parsed_lines[start_idx : start_idx + _SCAN_LOOKAHEAD]:
            if scan.event_id is None:
                m = _GOODS_EVENT_RE.search(pl.message)
                if m:
                    scan.event_id = m.group("guid")
            if scan.return_state is None:
                m = _RETURN_STATE_RE.search(pl.message)
                if m:
                    scan.return_state = m.group("state").strip()
            if scan.event_id and scan.return_state:
                break

    # Session detection
    result.sessions = _detect_sessions(parsed_lines, session_gap_minutes)

    return result


def _detect_sessions(parsed_lines: list[LogLine], gap_minutes: int) -> list[dict]:
    """Split lines into sessions by explicit markers or time gaps."""
    sessions: list[dict] = []
    current: dict = {"start": None, "end": None, "lines": []}

    for pl in parsed_lines:
        is_marker = any(marker in pl.message for marker in _SESSION_MARKERS)
        time_gap = False
        if (
            current["end"] is not None
            and pl.timestamp is not None
            and current["end"] is not None
        ):
            delta = (pl.timestamp - current["end"]).total_seconds() / 60
            time_gap = delta > gap_minutes

        if is_marker or time_gap:
            if current["lines"]:
                sessions.append(current)
            current = {"start": pl.timestamp, "end": pl.timestamp, "lines": [pl]}
        else:
            current["lines"].append(pl)
            if pl.timestamp:
                if current["start"] is None:
                    current["start"] = pl.timestamp
                current["end"] = pl.timestamp

    if current["lines"]:
        sessions.append(current)

    return sessions

