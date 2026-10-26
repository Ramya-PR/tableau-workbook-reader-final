"""Short-lived on-disk sessions.

A parsed workbook's archive is kept in a private temp directory so the data
preview can extract a .hyper/.csv from it later. Sessions expire after a TTL
and are removed on shutdown or on an explicit DELETE, because the files may
contain company data.
"""

from __future__ import annotations

import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .archive import DataFile, WorkbookArchive, remove_tree
from .errors import not_found


@dataclass
class Session:
    id: str
    dir: Path
    source: str
    filename: str
    created: float
    archive_path: Path | None = None
    archive: WorkbookArchive | None = None
    data_files: list[DataFile] = field(default_factory=list)

    @property
    def data_dir(self) -> Path:
        return self.dir / "data"


class SessionStore:
    def __init__(
        self,
        ttl_seconds: int,
        max_sessions: int,
        root: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._ttl = ttl_seconds
        self._max = max_sessions
        self._root = root
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, source: str, filename: str) -> Session:
        with self._lock:
            self._expire_locked()
            while len(self._sessions) >= self._max:
                oldest = min(self._sessions.values(), key=lambda s: s.created)
                self._drop_locked(oldest.id)
            sid = uuid.uuid4().hex
            # mkdtemp creates the directory with mode 0700 (owner only).
            directory = Path(tempfile.mkdtemp(prefix="twbparser-", dir=self._root))
            session = Session(id=sid, dir=directory, source=source, filename=filename, created=self._clock())
            self._sessions[sid] = session
            return session

    def get(self, sid: str) -> Session:
        with self._lock:
            self._expire_locked()
            session = self._sessions.get(sid)
            if session is None:
                raise not_found(
                    "This workbook session has expired or does not exist. Load the workbook again.",
                    "SESSION_EXPIRED",
                )
            return session

    def delete(self, sid: str) -> bool:
        with self._lock:
            return self._drop_locked(sid)

    def close(self) -> None:
        with self._lock:
            for sid in list(self._sessions):
                self._drop_locked(sid)

    def __len__(self) -> int:
        with self._lock:
            self._expire_locked()
            return len(self._sessions)

    # -- internals (lock held) ---------------------------------------------
    def _expire_locked(self) -> None:
        now = self._clock()
        for sid, session in list(self._sessions.items()):
            if now - session.created > self._ttl:
                self._drop_locked(sid)

    def _drop_locked(self, sid: str) -> bool:
        session = self._sessions.pop(sid, None)
        if session is None:
            return False
        remove_tree(session.dir)
        return True
