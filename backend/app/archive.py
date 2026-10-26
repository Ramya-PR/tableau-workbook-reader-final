"""Safe access to .twb / .twbx files.

A .twbx is a zip. We never call ``extractall``: files are untrusted, so we

* validate every member name (no absolute paths, ``..``, drive letters, symlinks),
* cap member count, total uncompressed size and per-member compression ratio,
* read the workbook XML with a hard byte limit, and
* extract data files one at a time, on demand, into a directory we choose,
  using a sanitised file name (never the name stored in the archive).
"""

from __future__ import annotations

import posixpath
import re
import shutil
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Settings
from .errors import AppError, parse_error, too_large, unsafe_archive

ZIP_MAGIC = b"PK\x03\x04"
_CHUNK = 1024 * 256
_RATIO_CHECK_MIN_BYTES = 1024 * 1024  # tiny members can legitimately compress >200x

# File extensions we recognise as "data bundled with the workbook".
KIND_BY_EXT = {
    ".hyper": "hyper",
    ".tde": "tde",
    ".csv": "csv",
    ".tsv": "csv",
    ".txt": "csv",
    ".tab": "csv",
    ".xlsx": "excel",
    ".xlsm": "excel",
    ".xls": "excel",
    ".json": "json",
    ".mdb": "access",
    ".accdb": "access",
}
_IGNORED_PREFIXES = ("twbxexternalcache/", "image/", "images/", "shapes/", "__macosx/", "templates/")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]")


@dataclass
class DataFile:
    path: str  # member name inside the archive, exactly as stored
    kind: str  # hyper | tde | csv | excel | json | access
    size_bytes: int
    previewable: bool = False
    reason: str | None = None  # why it is not previewable

    def to_dict(self) -> dict:
        return asdict(self)


def sniff_kind(path: Path) -> str:
    """Return ``"twbx"`` for zips, ``"twb"`` for XML, else raise."""
    with open(path, "rb") as fh:
        head = fh.read(512)
    if head.startswith(ZIP_MAGIC):
        return "twbx"
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped.startswith(b"<"):
        return "twb"
    raise AppError(
        "UNSUPPORTED_FILE",
        "This file is neither a Tableau packaged workbook (.twbx, a zip) nor a workbook XML file (.twb).",
        415,
    )


def is_workbook_file(path: Path) -> bool:
    """Stricter than ``sniff_kind``: a zip, or XML that actually contains ``<workbook``.

    Used to reject HTML login/error pages that a server returned with HTTP 200.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(8192)
    except OSError:
        return False
    return head.startswith(ZIP_MAGIC) or b"<workbook" in head


def _normalise_member_name(name: str) -> str:
    return name.replace("\\", "/")


def _check_member_name(name: str) -> None:
    norm = _normalise_member_name(name)
    parts = norm.split("/")
    if (
        not norm
        or "\x00" in norm
        or norm.startswith("/")
        or re.match(r"^[A-Za-z]:", norm)
        or any(part == ".." for part in parts)
    ):
        raise unsafe_archive(f"Archive contains an unsafe path ({name!r}); refusing to open it.")


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return ((info.external_attr >> 16) & 0o170000) == 0o120000


def validate_zip(zf: zipfile.ZipFile, settings: Settings) -> list[zipfile.ZipInfo]:
    infos = zf.infolist()
    if len(infos) > settings.max_archive_members:
        raise unsafe_archive(
            f"Archive has {len(infos)} entries (limit {settings.max_archive_members}); refusing to open it."
        )
    total = 0
    for info in infos:
        _check_member_name(info.filename)
        if _is_symlink(info):
            raise unsafe_archive(f"Archive contains a symbolic link ({info.filename!r}); refusing to open it.")
        if info.is_dir():
            continue
        total += info.file_size
        if total > settings.max_uncompressed_bytes:
            raise unsafe_archive(
                f"Archive expands to more than {settings.max_uncompressed_mb} MB; refusing to open it."
            )
        if info.file_size > _RATIO_CHECK_MIN_BYTES:
            if info.compress_size == 0 or info.file_size / info.compress_size > settings.max_compression_ratio:
                raise unsafe_archive(
                    f"Entry {info.filename!r} has a suspicious compression ratio; refusing to open it."
                )
    return infos


def _safe_filename(member: str) -> str:
    base = posixpath.basename(_normalise_member_name(member)) or "file"
    return _SAFE_NAME.sub("_", base)[:120] or "file"


class WorkbookArchive:
    """Read-only view of a .twb or .twbx on disk."""

    def __init__(self, path: Path, settings: Settings):
        self.path = Path(path)
        self.settings = settings
        self.kind = sniff_kind(self.path)  # "twb" | "twbx"
        self._twb_member: zipfile.ZipInfo | None = None
        self._data_files: list[DataFile] | None = None
        if self.kind == "twbx":
            with self._open_zip() as zf:
                infos = validate_zip(zf, settings)
            self._twb_member = self._pick_twb(infos)
            self._data_files = self._list_data_files(infos)
        else:
            self._data_files = []

    # -- zip helpers -------------------------------------------------------
    def _open_zip(self) -> zipfile.ZipFile:
        try:
            return zipfile.ZipFile(self.path)
        except zipfile.BadZipFile as exc:
            raise parse_error("The .twbx file is corrupted (not a valid zip archive).", "BAD_ARCHIVE") from exc

    @staticmethod
    def _pick_twb(infos: list[zipfile.ZipInfo]) -> zipfile.ZipInfo:
        candidates = [
            i
            for i in infos
            if not i.is_dir()
            and i.filename.lower().endswith(".twb")
            and not _normalise_member_name(i.filename).lower().startswith("__macosx/")
        ]
        if not candidates:
            raise parse_error("No .twb workbook file was found inside this .twbx.", "NO_WORKBOOK_XML")
        # Tableau puts the workbook at the archive root; prefer the shallowest.
        return min(candidates, key=lambda i: (_normalise_member_name(i.filename).count("/"), i.filename))

    def _list_data_files(self, infos: list[zipfile.ZipInfo]) -> list[DataFile]:
        out: list[DataFile] = []
        for info in infos:
            if info.is_dir() or info is self._twb_member:
                continue
            norm = _normalise_member_name(info.filename).lower()
            if norm.startswith(_IGNORED_PREFIXES):
                continue
            kind = KIND_BY_EXT.get(posixpath.splitext(norm)[1])
            if kind:
                out.append(DataFile(path=info.filename, kind=kind, size_bytes=info.file_size))
        return sorted(out, key=lambda d: d.path.lower())

    # -- public API --------------------------------------------------------
    @property
    def data_files(self) -> list[DataFile]:
        return list(self._data_files or [])

    def twb_bytes(self) -> bytes:
        limit = self.settings.max_twb_bytes
        if self.kind == "twb":
            with open(self.path, "rb") as fh:
                data = fh.read(limit + 1)
        else:
            assert self._twb_member is not None
            with self._open_zip() as zf, zf.open(self._twb_member) as fh:
                data = fh.read(limit + 1)  # bounded: a lying zip header can't exhaust memory
        if len(data) > limit:
            raise too_large(f"The workbook XML is larger than {self.settings.max_twb_mb} MB.")
        return data

    def extract(self, member: str, dest_dir: Path) -> Path:
        """Extract a single *listed* data file into ``dest_dir`` and return its path."""
        if self.kind != "twbx":
            raise parse_error("A .twb file has no bundled data files.", "NO_DATA_FILES")
        listed = {d.path for d in self.data_files}
        if member not in listed:
            raise parse_error(f"{member!r} is not a data file in this workbook.", "UNKNOWN_DATA_FILE")

        dest_dir.mkdir(parents=True, exist_ok=True)
        index = sorted(listed).index(member)
        dest = dest_dir / f"{index:03d}_{_safe_filename(member)}"
        if dest.exists():
            return dest

        limit = self.settings.max_uncompressed_bytes
        written = 0
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with self._open_zip() as zf, zf.open(member) as src, open(tmp, "wb") as out:
                while chunk := src.read(_CHUNK):
                    written += len(chunk)
                    if written > limit:
                        raise too_large("Data file is too large to extract.")
                    out.write(chunk)
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
        return dest


def copy_limited(src, dest: Path, max_bytes: int) -> int:
    """Copy a binary stream to ``dest`` enforcing a size cap. Returns bytes written."""
    written = 0
    try:
        with open(dest, "wb") as out:
            while chunk := src.read(_CHUNK):
                written += len(chunk)
                if written > max_bytes:
                    raise too_large(f"File is larger than the {max_bytes // (1024 * 1024)} MB limit.")
                out.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return written


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
