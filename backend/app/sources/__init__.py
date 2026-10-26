from __future__ import annotations

import httpx

from ..config import Settings
from .base import WorkbookRef, WorkbookSource
from .public import PublicSource
from .server import ServerSource
from .upload import UploadSource, save_upload

__all__ = [
    "PublicSource",
    "ServerSource",
    "UploadSource",
    "WorkbookRef",
    "WorkbookSource",
    "build_sources",
    "save_upload",
]


def build_sources(
    settings: Settings,
    *,
    public_transport: httpx.BaseTransport | None = None,
    tsc_module=None,
) -> dict[str, WorkbookSource]:
    """Create every source. The keyword arguments exist so tests can inject fakes."""
    return {
        "public": PublicSource(settings, transport=public_transport),
        "server": ServerSource(settings, tsc_module=tsc_module),
        "upload": UploadSource(),
    }
