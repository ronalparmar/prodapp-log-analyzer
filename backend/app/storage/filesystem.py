"""
Local filesystem storage for uploaded log files.
"""

from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "./data/uploads")


def ensure_upload_dir(base_dir: str = UPLOAD_DIR) -> Path:
    p = Path(base_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_upload(filename: str, content: bytes, base_dir: str = UPLOAD_DIR) -> Path:
    """Persist raw uploaded bytes to disk, return the stored path."""
    dest = ensure_upload_dir(base_dir) / filename
    dest.write_bytes(content)
    return dest


def extract_zip(zip_path: str | Path, dest_dir: str | Path) -> list[Path]:
    """Extract a ZIP file, return list of extracted .txt file paths."""
    extracted: list[Path] = []
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            if member.endswith(".txt") and not member.startswith("__MACOSX"):
                zf.extract(member, dest_dir)
                extracted.append(dest_dir / member)
    return extracted


def list_uploads(base_dir: str = UPLOAD_DIR) -> list[Path]:
    """Return all stored upload files."""
    p = Path(base_dir)
    if not p.exists():
        return []
    return sorted(p.iterdir())
