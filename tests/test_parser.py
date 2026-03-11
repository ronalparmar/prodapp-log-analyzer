"""
Unit tests for the ShopApp log parser.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the backend package is importable
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.parsing.shopapp_parser import (
    ParseResult,
    parse_log_file,
    parse_log_lines,
)

SAMPLE_PATH = _REPO_ROOT / "samples" / "shopappNorwayLog_7.txt"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lines(*msgs: str) -> list[str]:
    """Create fake log lines with sequential timestamps."""
    base = "2026-02-18 09:00:{:02d}.0000 "
    return [(base.format(i) + m + "\n") for i, m in enumerate(msgs)]


# ---------------------------------------------------------------------------
# deviceId extraction
# ---------------------------------------------------------------------------

class TestDeviceId:
    def test_extracts_device_id(self):
        lines = _lines("Device set up ---- Device Id - TC5705956")
        result = parse_log_lines(lines)
        assert result.device_id == "TC5705956"

    def test_no_device_id(self):
        lines = _lines("App version ---- 1.0.0")
        result = parse_log_lines(lines)
        assert result.device_id is None

    def test_device_id_only_first_occurrence(self):
        lines = _lines(
            "Device set up ---- Device Id - TC1111111",
            "Device set up ---- Device Id - TC9999999",
        )
        result = parse_log_lines(lines)
        # Only the first occurrence is stored
        assert result.device_id == "TC1111111"


# ---------------------------------------------------------------------------
# appVersion extraction
# ---------------------------------------------------------------------------

class TestAppVersion:
    def test_extracts_version(self):
        lines = _lines("App version ---- 550.25.310.1")
        result = parse_log_lines(lines)
        assert "550.25.310.1" in result.app_versions

    def test_multiple_versions(self):
        lines = _lines(
            "App version ---- 550.25.310.1",
            "App version ---- 550.26.1.1",
        )
        result = parse_log_lines(lines)
        assert "550.25.310.1" in result.app_versions
        assert "550.26.1.1" in result.app_versions


# ---------------------------------------------------------------------------
# email extraction
# ---------------------------------------------------------------------------

class TestEmail:
    def test_validated_user_email(self):
        lines = _lines(
            "Validated User & Authorised User - ronal.parmar@tcs.com, App Version : 550.25.310.1"
        )
        result = parse_log_lines(lines)
        assert "ronal.parmar@tcs.com" in result.emails

    def test_profile_information_email(self):
        lines = _lines("Profile information received for user : user@example.com")
        result = parse_log_lines(lines)
        assert "user@example.com" in result.emails

    def test_deduplicates_emails(self):
        lines = _lines(
            "Validated User & Authorised User - ronal.parmar@tcs.com, App Version : 1.0",
            "Profile information received for user : ronal.parmar@tcs.com",
        )
        result = parse_log_lines(lines)
        assert result.emails.count("ronal.parmar@tcs.com") == 1

    def test_no_email(self):
        lines = _lines("Some unrelated log line")
        result = parse_log_lines(lines)
        assert result.emails == []


# ---------------------------------------------------------------------------
# packageName and environment
# ---------------------------------------------------------------------------

class TestPackageName:
    def test_extracts_package_name_qa(self):
        lines = _lines(
            "Database path set to: /data/user/0/com.postenbring.shopapp.norwayqa/cache/ShopAppNorway.db"
        )
        result = parse_log_lines(lines)
        assert result.package_name == "com.postenbring.shopapp.norwayqa"
        assert result.environment == "QA"

    def test_extracts_package_name_prod(self):
        lines = _lines(
            "Database path set to: /data/user/0/com.postenbring.shopapp.norway/cache/ShopAppNorway.db"
        )
        result = parse_log_lines(lines)
        assert result.package_name == "com.postenbring.shopapp.norway"
        assert result.environment == "PROD"

    def test_no_package(self):
        lines = _lines("Some message without a package")
        result = parse_log_lines(lines)
        assert result.package_name is None
        assert result.environment is None


# ---------------------------------------------------------------------------
# Scan events
# ---------------------------------------------------------------------------

class TestScanEvents:
    def test_scan_event_code128(self):
        lines = _lines(
            "Scanned package number 00370438104439901228 bar code format CODE128 in DeliveryToCustomer process"
        )
        result = parse_log_lines(lines)
        assert len(result.scan_events) == 1
        se = result.scan_events[0]
        assert se.item_number == "00370438104439901228"
        assert se.barcode_format == "CODE128"
        assert se.entry_mode == "scan"
        assert se.process == "DeliveryToCustomer"

    def test_manual_input_event(self):
        lines = _lines(
            "Scanned package number KS000172751NO bar code format ManualInput in DeliveryToCustomer process"
        )
        result = parse_log_lines(lines)
        assert len(result.scan_events) == 1
        se = result.scan_events[0]
        assert se.item_number == "KS000172751NO"
        assert se.barcode_format == "ManualInput"
        assert se.entry_mode == "manual"

    def test_multiple_scans(self):
        lines = _lines(
            "Scanned package number AAA bar code format CODE128 in PickUp process",
            "Scanned package number BBB bar code format ManualInput in ReturnToSender process",
        )
        result = parse_log_lines(lines)
        assert len(result.scan_events) == 2
        assert result.scan_events[0].entry_mode == "scan"
        assert result.scan_events[1].entry_mode == "manual"

    def test_no_scan_events(self):
        lines = _lines("Some message without scan info")
        result = parse_log_lines(lines)
        assert result.scan_events == []


# ---------------------------------------------------------------------------
# Exception detection
# ---------------------------------------------------------------------------

class TestExceptionDetection:
    def test_task_scheduler_exception(self):
        lines = _lines(
            "TaskSchedulerOnUnobservedTaskException System.Exception: Connection failed"
        )
        result = parse_log_lines(lines)
        assert len(result.exception_events) > 0

    def test_exception_colon_marker(self):
        lines = _lines(
            "NullReferenceException: Object reference not set to an instance of an object."
        )
        result = parse_log_lines(lines)
        assert len(result.exception_events) > 0
        exc = result.exception_events[0]
        assert "NullReferenceException" in exc.exception_type

    def test_unhandled_marker(self):
        lines = _lines(
            "Unhandled exception in background thread: System.InvalidOperationException"
        )
        result = parse_log_lines(lines)
        assert len(result.exception_events) > 0

    def test_no_exception(self):
        lines = _lines("Scanned package number XYZ bar code format CODE128 in PickUp process")
        result = parse_log_lines(lines)
        assert result.exception_events == []

    def test_exception_context_lines(self):
        lines = _lines(
            "Line before context",
            "NullReferenceException: Object reference not set",
            "  at SomeMethod()",
        )
        result = parse_log_lines(lines, exception_context=5)
        assert len(result.exception_events) > 0
        # context should include surrounding lines
        ctx = result.exception_events[0].context_lines
        assert len(ctx) >= 1


# ---------------------------------------------------------------------------
# Sample log file integration test
# ---------------------------------------------------------------------------

class TestSampleFile:
    def test_sample_file_device_id(self):
        result = parse_log_file(str(SAMPLE_PATH))
        assert result.device_id == "TC5705956"

    def test_sample_file_email(self):
        result = parse_log_file(str(SAMPLE_PATH))
        assert "ronal.parmar@tcs.com" in result.emails

    def test_sample_file_package_name(self):
        result = parse_log_file(str(SAMPLE_PATH))
        assert result.package_name == "com.postenbring.shopapp.norwayqa"
        assert result.environment == "QA"

    def test_sample_file_scan_events(self):
        result = parse_log_file(str(SAMPLE_PATH))
        items = [se.item_number for se in result.scan_events]
        assert "00370438104439901228" in items

    def test_sample_file_has_manual_scans(self):
        result = parse_log_file(str(SAMPLE_PATH))
        manual = [se for se in result.scan_events if se.entry_mode == "manual"]
        assert len(manual) > 0

    def test_sample_file_has_exceptions(self):
        result = parse_log_file(str(SAMPLE_PATH))
        assert len(result.exception_events) > 0

    def test_sample_file_app_versions(self):
        result = parse_log_file(str(SAMPLE_PATH))
        assert "550.25.310.1" in result.app_versions
        assert "550.26.1.1" in result.app_versions
