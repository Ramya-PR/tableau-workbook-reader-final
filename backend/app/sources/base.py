"""Common interface for every way of getting a workbook file.

Everything downstream of ``fetch()`` (archive handling, parsing, preview, API,
UI) is source-agnostic: a source's only job is to put a .twb/.twbx on disk.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path

from ..errors import AppError


@dataclass
class WorkbookRef:
    id: str  # whatever fetch() needs to get this workbook again
    name: str
    project: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class WorkbookSource(ABC):
    name: str
    #: True once the source has been exercised against the real service.
    #: Surfaced in /api/capabilities so the UI can flag experimental sources.
    verified: bool = False

    @abstractmethod
    def status(self) -> dict:
        """``{"available", "can_list", "verified", "reason", ...}`` for /api/capabilities."""

    def list_workbooks(self, query: str | None = None, **options) -> list[WorkbookRef]:
        raise AppError("LIST_UNSUPPORTED", f"The {self.name} source cannot list workbooks.", 400)

    @abstractmethod
    def fetch(self, reference: str, dest_dir: Path) -> Path:
        """Download/locate the workbook identified by ``reference`` into ``dest_dir``."""
