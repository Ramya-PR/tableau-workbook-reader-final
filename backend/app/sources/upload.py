"""Upload source: the user supplies the file themselves.

It needs no account, works for any workbook, and is the fallback when a Tableau
Public author has disabled downloads.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import BinaryIO

from ..archive import copy_limited
from ..errors import AppError, invalid_input
from .base import WorkbookSource

_ALLOWED_EXT = {".twb", ".twbx"}


def save_upload(stream: BinaryIO, filename: str | None, dest_dir: Path, max_bytes: int) -> Path:
    name = os.path.basename((filename or "").replace("\\", "/"))
    ext = os.path.splitext(name)[1].lower()
    if ext not in _ALLOWED_EXT:
        raise AppError("UNSUPPORTED_FILE", "Please upload a .twb or .twbx file.", 415)
    safe = re.sub(r"[^A-Za-z0-9._ -]", "_", name)[:120] or f"upload{ext}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe
    copy_limited(stream, dest, max_bytes)
    return dest


class UploadSource(WorkbookSource):
    name = "upload"
    verified = True

    def status(self) -> dict:
        return {"available": True, "can_list": False, "verified": True, "reason": None}

    def fetch(self, reference: str, dest_dir: Path) -> Path:  # pragma: no cover - guarded in API
        raise invalid_input("Uploads are received by the upload endpoint, not fetched.")
