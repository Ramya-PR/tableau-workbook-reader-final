"""Tableau Server / Tableau Cloud source, built on Tableau Server Client (TSC).

TSC is a *Python* library that wraps the Tableau REST API. It lists and
downloads workbooks; it does not parse them (that's parser.py).

**Status:** written against the TSC API (checked by introspection on
tableauserverclient 0.42) and unit-tested with a fake TSC module and a fake REST
server. The author confirmed by hand that listing and selecting workbooks works
against a real Tableau Cloud site; there is no automated test against a live site.
"""

from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path

from ..archive import is_workbook_file
from ..config import Settings
from ..errors import AppError, invalid_input, not_found, source_unavailable, too_large
from .base import WorkbookRef, WorkbookSource

_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_NOT_CONFIGURED = (
    "Set TABLEAU_SERVER_URL, TABLEAU_PAT_NAME and TABLEAU_PAT_SECRET (and TABLEAU_SITE for a non-default "
    "site) in backend/.env, then restart the backend."
)


def _iso(value) -> str | None:
    try:
        return value.isoformat() if value is not None else None
    except AttributeError:
        return str(value)


class ServerSource(WorkbookSource):
    name = "server"
    verified = True

    def __init__(self, settings: Settings, tsc_module=None):
        self._s = settings
        self._tsc = tsc_module  # injectable for tests
        # A personal access token allows one session at a time: signing in again with the same
        # token ends the previous session. Requests run in parallel threads, so sessions are
        # serialised here. Tradeoff: one slow download blocks other Server requests.
        self._session_lock = threading.Lock()

    # -- setup -------------------------------------------------------------
    def _tsc_mod(self):
        if self._tsc is None:
            try:
                import tableauserverclient as tsc
            except ImportError as exc:
                raise source_unavailable(
                    "The tableauserverclient package is not installed (pip install tableauserverclient)."
                ) from exc
            self._tsc = tsc
        return self._tsc

    def status(self) -> dict:
        base = {"can_list": True, "verified": True}
        if not self._s.server_configured:
            return {**base, "available": False, "reason": _NOT_CONFIGURED}
        try:
            self._tsc_mod()
        except AppError as exc:
            return {**base, "available": False, "reason": exc.message}
        return {
            **base,
            "available": True,
            "reason": None,
            "note": "Confirmed working in a manual test against Tableau Cloud. Not covered by an automated test against a live site.",
            "server": self._s.server_url,
            "site": self._s.server_site or "(default)",
        }

    def _is(self, exc: Exception, *class_names: str) -> bool:
        tsc = self._tsc_mod()
        return any(isinstance(exc, getattr(tsc, n)) for n in class_names if hasattr(tsc, n))

    def _map_error(self, exc: Exception) -> AppError | None:
        """Translate TSC / network exceptions into user-facing errors (None = not ours).

        TSC's hierarchy (verified against the installed library): ``FailedSignInError`` derives from
        ``XMLError`` and ``NotSignedInError`` -- *not* from ``ServerResponseError`` -- while 5xx
        responses raise ``InternalServerError`` and a non-Tableau URL raises ``NonXMLResponseError``.
        Only ``code``/``summary`` are surfaced; ``detail`` is never shown.
        """
        auth_failed = AppError(
            "SERVER_AUTH_FAILED",
            "Tableau rejected the sign-in. Check TABLEAU_PAT_NAME, TABLEAU_PAT_SECRET and TABLEAU_SITE, "
            "and that the token has not expired.",
            401,
        )
        if self._is(exc, "FailedSignInError", "NotSignedInError"):
            return auth_failed
        name = type(exc).__name__
        if name == "InternalServerError":
            return AppError(
                "SERVER_ERROR", f"Tableau Server reported an internal error (HTTP {getattr(exc, 'code', '?')}).", 502
            )
        if name in ("NonXMLResponseError", "RedirectError"):
            return AppError(
                "SERVER_BAD_RESPONSE",
                f"{self._s.server_url} did not answer like a Tableau Server/Cloud REST API. "
                "Check TABLEAU_SERVER_URL (it should be the base URL, e.g. https://tableau.example.com).",
                502,
            )
        if self._is(exc, "ServerResponseError"):
            code = str(getattr(exc, "code", "") or "")
            summary = str(getattr(exc, "summary", "") or "")
            if code.startswith("401"):
                return auth_failed
            if code.startswith("403"):
                return AppError(
                    "DOWNLOAD_FORBIDDEN",
                    "Your token's user doesn't have permission to access or download this workbook.",
                    403,
                )
            if code.startswith("404"):
                return not_found("That workbook was not found on the server.")
            return AppError("SERVER_ERROR", f"Tableau Server returned an error ({code}): {summary}", 502)
        try:
            import requests

            if isinstance(exc, requests.exceptions.RequestException):
                return AppError(
                    "SERVER_UNREACHABLE",
                    f"Could not reach {self._s.server_url} ({type(exc).__name__}). Check TABLEAU_SERVER_URL and your network/VPN.",
                    502,
                )
        except ImportError:  # pragma: no cover
            pass
        return None

    @contextmanager
    def _signed_in(self):
        with self._session_lock:
            with self._signed_in_locked() as server:
                yield server

    @contextmanager
    def _signed_in_locked(self):
        tsc = self._tsc_mod()
        try:
            server = tsc.Server(self._s.server_url, use_server_version=True)
            auth = tsc.PersonalAccessTokenAuth(
                self._s.server_pat_name, self._s.server_pat_secret, site_id=self._s.server_site
            )
            try:
                # In TSC the sign-in request happens when sign_in() is *called*.
                signin = server.auth.sign_in(auth)
            except AttributeError as exc:
                # A host that answers 200 with something that isn't Tableau XML (a typo'd URL, a
                # login portal) makes TSC fail with a bare AttributeError. Report it properly.
                raise AppError(
                    "SERVER_BAD_RESPONSE",
                    f"{self._s.server_url} did not answer like a Tableau Server/Cloud REST API. "
                    "Check TABLEAU_SERVER_URL (it should be the base URL, e.g. https://tableau.example.com).",
                    502,
                ) from exc
            with signin:
                yield server
        except AppError:
            raise
        except Exception as exc:
            mapped = self._map_error(exc)
            if mapped is None:
                raise
            raise mapped from exc

    def _require_configured(self) -> None:
        if not self._s.server_configured:
            raise source_unavailable(_NOT_CONFIGURED)

    # -- API ---------------------------------------------------------------
    def list_workbooks(self, query: str | None = None, limit: int = 500, **_) -> list[WorkbookRef]:
        self._require_configured()
        tsc = self._tsc_mod()
        refs: list[WorkbookRef] = []
        with self._signed_in() as server:
            opts = tsc.RequestOptions(pagesize=100)
            cleaned = re.sub(r"[,:]", " ", query or "").strip()  # ',' and ':' are filter syntax
            if cleaned:
                opts.filter.add(
                    tsc.Filter(tsc.RequestOptions.Field.Name, tsc.RequestOptions.Operator.Has, cleaned)
                )
            for wb in tsc.Pager(server.workbooks, opts):  # Pager walks every page
                refs.append(
                    WorkbookRef(
                        id=str(wb.id),
                        name=str(getattr(wb, "name", None) or wb.id),
                        project=getattr(wb, "project_name", None),
                        updated_at=_iso(getattr(wb, "updated_at", None)),
                    )
                )
                if len(refs) >= limit:
                    break
        return refs

    def fetch(self, reference: str, dest_dir: Path) -> Path:
        self._require_configured()
        if not _ID_RE.match(reference or ""):
            raise invalid_input("Pick a workbook from the list (expected a Tableau workbook id).")
        dest_dir.mkdir(parents=True, exist_ok=True)
        with self._signed_in() as server:
            downloaded = server.workbooks.download(reference, filepath=str(dest_dir))
        path = Path(os.fspath(downloaded)).resolve()
        if dest_dir.resolve() not in path.parents or not path.is_file():
            raise AppError("SERVER_ERROR", "Tableau returned an unexpected download location.", 502)
        if path.stat().st_size > self._s.max_file_bytes:
            path.unlink(missing_ok=True)
            raise too_large(f"The workbook is larger than the {self._s.max_file_mb} MB limit.")
        if not is_workbook_file(path):
            path.unlink(missing_ok=True)
            raise AppError("SERVER_ERROR", "The downloaded file is not a Tableau workbook.", 502)
        return path
