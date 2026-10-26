"""Tableau Public source.

Fetches a workbook over plain HTTPS from public.tableau.com. No account needed.

The download and profile-listing URL patterns are unofficial. The workbook URL
flow was confirmed by hand against a real public workbook (by the author, not
by an automated test), but Tableau can change them at any time. Both are configurable (PUBLIC_DOWNLOAD_URL_TEMPLATE,
PUBLIC_LIST_URL_TEMPLATE) so a change on Tableau's side needs no code change.

Safety: this endpoint takes a URL from a browser, so it is an SSRF risk. We

* only accept https URLs on an allow-listed host (``PUBLIC_ALLOWED_HOSTS``),
* extract just the workbook *name* from the URL and rebuild the download URL
  ourselves (the user's URL is never fetched directly),
* follow redirects manually, re-checking the allow-list on every hop,
* cap the download size and apply a timeout.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

import httpx

from ..archive import is_workbook_file
from ..config import Settings
from ..errors import AppError, invalid_input, too_large
from .base import WorkbookRef, WorkbookSource

_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,200}$")
_MAX_REDIRECTS = 5
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_LIST_PAGE_SIZE = 100
_UA = "tableau-workbook-parser/0.1 (+local tool)"


class PublicSource(WorkbookSource):
    name = "public"
    verified = True

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self._s = settings
        self._transport = transport

    # -- status ------------------------------------------------------------
    def status(self) -> dict:
        return {
            "available": True,
            "can_list": True,
            "verified": True,
            "reason": None,
            "note": "Confirmed working in a manual test with a public workbook. The URL patterns are not officially documented and may change; use Upload if a download fails.",
            "default_profile": self._s.public_default_profile or None,
        }

    # -- helpers -----------------------------------------------------------
    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self._s.http_timeout_seconds,
            transport=self._transport,
            headers={"User-Agent": _UA},
            follow_redirects=False,
        )

    def _check_url(self, url: str, redirected: bool = False) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        try:
            port = parsed.port
        except ValueError:  # e.g. "host:99999" or "host:abc"
            port = -1
        ok = (
            parsed.scheme == "https"
            and host in self._s.public_allowed_hosts
            and "@" not in parsed.netloc
            and port in (None, 443)
        )
        if ok:
            return
        if redirected:
            raise AppError(
                "REDIRECT_BLOCKED",
                f"Tableau Public redirected the request to {parsed.scheme}://{host or '?'}, which is not allowed "
                f"(only https on {', '.join(self._s.public_allowed_hosts)}). If that host is Tableau's download "
                "CDN, add it to PUBLIC_ALLOWED_HOSTS.",
                502,
            )
        raise invalid_input(
            "Only https URLs on " + ", ".join(self._s.public_allowed_hosts) + " are accepted."
        )

    def _format(self, template: str, **values) -> str:
        try:
            return template.format(**values)
        except (KeyError, IndexError) as exc:
            raise AppError("CONFIG_ERROR", f"Invalid URL template in configuration: {template!r}", 500) from exc

    # -- reference parsing -------------------------------------------------
    def parse_reference(self, reference: str) -> str:
        """Turn a view URL or a bare name into a workbook repo name."""
        ref = (reference or "").strip()
        if not ref:
            raise invalid_input("Enter a Tableau Public workbook URL (or workbook name).")

        if "://" in ref or ref.lower().startswith("public.tableau.com"):
            if "://" not in ref:
                ref = "https://" + ref
            parsed = urlparse(ref)
            host = (parsed.hostname or "").lower()
            if parsed.scheme != "https" or host not in self._s.public_allowed_hosts:
                raise invalid_input(
                    "Only https URLs on " + ", ".join(self._s.public_allowed_hosts) + " are accepted."
                )
            segs = [unquote(s) for s in parsed.path.split("/") if s]
            name = None
            if segs[:1] == ["views"] and len(segs) >= 2:
                name = segs[1]
            elif "viz" in segs:
                i = segs.index("viz")
                name = segs[i + 1] if len(segs) > i + 1 else None
            elif segs[:1] == ["workbooks"] and len(segs) >= 2:
                name = re.sub(r"\.twbx?$", "", segs[1], flags=re.IGNORECASE)
            elif segs[:1] == ["shared"]:
                raise invalid_input(
                    "Short /shared/ links can't be resolved. Open the viz and copy the full "
                    "public.tableau.com/views/... or /app/profile/.../viz/... URL instead."
                )
            if not name:
                raise invalid_input(
                    "Couldn't find a workbook name in that URL. Expected something like "
                    "https://public.tableau.com/views/WorkbookName/SheetName"
                )
        else:
            name = unquote(ref)

        if not _NAME_RE.match(name):
            raise invalid_input(f"{name!r} doesn't look like a Tableau Public workbook name.")
        return name

    # -- download ----------------------------------------------------------
    def _download(self, client: httpx.Client, url: str, dest: Path) -> int:
        """GET ``url`` into ``dest``. Returns the final HTTP status (file written only on 200)."""
        limit = self._s.max_file_bytes
        redirected = False
        for _ in range(_MAX_REDIRECTS + 1):
            self._check_url(url, redirected)
            with client.stream("GET", url) as resp:
                if resp.status_code in _REDIRECT_CODES:
                    location = resp.headers.get("location")
                    if not location:
                        raise AppError("DOWNLOAD_FAILED", "Tableau Public sent a redirect with no target.", 502)
                    url, redirected = urljoin(url, location), True
                    continue
                if resp.status_code != 200:
                    return resp.status_code
                length = resp.headers.get("content-length", "")
                if length.isdigit() and int(length) > limit:
                    raise too_large(f"The workbook is larger than the {self._s.max_file_mb} MB limit.")
                written = 0
                try:
                    with open(dest, "wb") as out:
                        for chunk in resp.iter_bytes():
                            written += len(chunk)
                            if written > limit:
                                raise too_large(f"The workbook is larger than the {self._s.max_file_mb} MB limit.")
                            out.write(chunk)
                except BaseException:
                    dest.unlink(missing_ok=True)
                    raise
                return 200
        raise AppError("DOWNLOAD_FAILED", "Too many redirects from Tableau Public.", 502)

    def fetch(self, reference: str, dest_dir: Path) -> Path:
        name = self.parse_reference(reference)
        dest_dir.mkdir(parents=True, exist_ok=True)
        with self._client() as client:
            for ext in ("twbx", "twb"):
                url = self._format(self._s.public_download_url_template, name=quote(name, safe=""), ext=ext)
                dest = dest_dir / f"{name}.{ext}"
                try:
                    status = self._download(client, url, dest)
                except httpx.TimeoutException as exc:
                    raise AppError("DOWNLOAD_FAILED", "Tableau Public timed out.", 504) from exc
                except httpx.HTTPError as exc:
                    raise AppError(
                        "DOWNLOAD_FAILED", f"Could not reach Tableau Public ({type(exc).__name__}).", 502
                    ) from exc

                if status == 200:
                    if is_workbook_file(dest):
                        return dest
                    dest.unlink(missing_ok=True)  # e.g. an HTML login/error page served with 200
                    continue
                if status in (401, 403):
                    raise AppError(
                        "DOWNLOAD_DISABLED",
                        f"Tableau Public refused the download for '{name}'. The author has probably not enabled "
                        "downloads for it. Download the workbook from its page (if allowed) and use the Upload tab.",
                        403,
                    )
                if status == 404:
                    continue
                raise AppError("DOWNLOAD_FAILED", f"Tableau Public returned HTTP {status}.", 502)

        raise AppError(
            "DOWNLOAD_UNAVAILABLE",
            f"Couldn't download '{name}' from Tableau Public. The name may be wrong, the author may not allow "
            "downloads, or Tableau may have changed its download URL (see PUBLIC_DOWNLOAD_URL_TEMPLATE in the "
            "README). Use the Upload tab with a file downloaded from the workbook's page.",
            404,
        )

    # -- listing -----------------------------------------------------------
    def _get_json(self, client: httpx.Client, url: str):
        redirected = False
        for _ in range(_MAX_REDIRECTS + 1):
            self._check_url(url, redirected)
            resp = client.get(url)
            if resp.status_code in _REDIRECT_CODES and resp.headers.get("location"):
                url, redirected = urljoin(url, resp.headers["location"]), True
                continue
            return resp
        raise AppError("LIST_FAILED", "Too many redirects from Tableau Public.", 502)

    @staticmethod
    def _items(data) -> list:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("contents", "workbooks", "items", "results"):
                if isinstance(data.get(key), list):
                    return data[key]
        raise ValueError("unrecognised response shape")

    @staticmethod
    def _iso(value) -> str | None:
        if isinstance(value, (int, float)) and value > 0:
            seconds = value / 1000 if value > 1e11 else value
            try:
                return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).date().isoformat()
            except (OverflowError, OSError, ValueError):
                return None
        return str(value) if isinstance(value, str) and value else None

    def _list_with(self, client, template: str, profile: str, limit: int) -> list[WorkbookRef]:
        """Page through one listing endpoint. Raises ValueError/httpx.HTTPError if it doesn't work."""
        refs: list[WorkbookRef] = []
        index = 0
        while len(refs) < limit:
            url = self._format(template, profile=quote(profile, safe=""), count=_LIST_PAGE_SIZE, index=index)
            resp = self._get_json(client, url)
            if resp.status_code != 200:
                raise ValueError(f"HTTP {resp.status_code}")
            items = self._items(resp.json())
            for item in items:
                if not isinstance(item, dict):
                    continue
                wid = item.get("workbookRepoUrl") or item.get("workbookRepoURL") or item.get("repoUrl")
                if not wid or not _NAME_RE.match(str(wid)):
                    continue
                refs.append(
                    WorkbookRef(
                        id=str(wid),
                        name=str(item.get("title") or item.get("name") or wid),
                        updated_at=self._iso(
                            item.get("lastPublishDate") or item.get("lastUpdateDate") or item.get("publishDate")
                        ),
                    )
                )
            if len(items) < _LIST_PAGE_SIZE:
                break
            index += len(items)
        return refs

    def list_workbooks(self, query: str | None = None, profile: str | None = None, limit: int = 300, **_):
        profile = (profile or self._s.public_default_profile or "").strip()
        if not profile:
            raise invalid_input("Enter a Tableau Public profile name to list its workbooks.")
        if not _NAME_RE.match(profile):
            raise invalid_input("That doesn't look like a valid Tableau Public profile name.")

        templates = list(
            dict.fromkeys([self._s.public_list_url_template, *self._s.public_list_fallback_templates])
        )
        refs: list[WorkbookRef] = []
        failures: list[str] = []
        try:
            with self._client() as client:
                for template in templates:
                    try:
                        refs = self._list_with(client, template, profile, limit)
                        break
                    except (httpx.HTTPError, ValueError) as exc:
                        failures.append(f"{urlparse(template).path}: {exc}")
                else:
                    raise ValueError("; ".join(failures))
        except AppError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise AppError(
                "LIST_FAILED",
                f"Couldn't list workbooks for profile '{profile}' ({exc}). The profile-listing endpoints are "
                "unofficial and may have changed. Paste a workbook view URL instead, or use the Upload tab.",
                502,
            ) from exc

        if query:
            q = query.lower()
            refs = [r for r in refs if q in r.name.lower() or q in r.id.lower()]
        return refs[:limit]
