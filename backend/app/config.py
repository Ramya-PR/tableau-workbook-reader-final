"""Runtime configuration, read from environment variables (and a .env file).

Secrets (the Tableau personal access token) are only ever read here, on the
backend. They are never sent to the browser or written to logs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

MB = 1024 * 1024


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


def _csv(env: Mapping[str, str], name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    # --- limits -----------------------------------------------------------
    max_file_mb: int = 500  # download / upload size cap
    max_uncompressed_mb: int = 2048  # total size of everything inside a .twbx
    max_archive_members: int = 5000
    max_compression_ratio: int = 200  # per-member zip-bomb guard
    max_twb_mb: int = 100  # cap for the workbook XML itself
    http_timeout_seconds: float = 60.0

    # --- sessions ---------------------------------------------------------
    session_ttl_minutes: int = 30
    max_sessions: int = 20

    # --- data preview -----------------------------------------------------
    default_preview_rows: int = 100
    max_preview_rows: int = 1000

    # --- Tableau Public ---------------------------------------------------
    public_allowed_hosts: tuple[str, ...] = ("public.tableau.com",)
    # These two URL patterns are UNVERIFIED (see README). They are configurable
    # so a change on Tableau's side does not require a code change.
    public_list_url_template: str = (
        "https://public.tableau.com/profile/api/{profile}/workbooks?count={count}&index={index}"
    )
    # Tried in order after public_list_url_template, only if that one fails (e.g. HTTP 404).
    public_list_fallback_templates: tuple[str, ...] = (
        "https://public.tableau.com/public/apis/workbooks"
        "?profileName={profile}&startIndex={index}&count={count}&visibility=NON_HIDDEN",
    )
    public_download_url_template: str = "https://public.tableau.com/workbooks/{name}.{ext}"
    public_default_profile: str = ""

    # --- Tableau Server / Cloud ------------------------------------------
    server_url: str = ""
    server_site: str = ""  # the site's "content URL"; empty string = Default site
    server_pat_name: str = ""
    server_pat_secret: str = ""

    # --- web --------------------------------------------------------------
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * MB

    @property
    def max_uncompressed_bytes(self) -> int:
        return self.max_uncompressed_mb * MB

    @property
    def max_twb_bytes(self) -> int:
        return self.max_twb_mb * MB

    @property
    def session_ttl_seconds(self) -> int:
        return self.session_ttl_minutes * 60

    @property
    def server_configured(self) -> bool:
        return bool(self.server_url and self.server_pat_name and self.server_pat_secret)


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from ``environ`` (defaults to os.environ plus a .env file)."""
    if environ is None:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:  # python-dotenv is optional at runtime
            pass
        env: Mapping[str, str] = os.environ
    else:
        env = environ

    defaults = Settings()
    return Settings(
        max_file_mb=_int(env, "MAX_FILE_MB", defaults.max_file_mb),
        max_uncompressed_mb=_int(env, "MAX_UNCOMPRESSED_MB", defaults.max_uncompressed_mb),
        max_archive_members=_int(env, "MAX_ARCHIVE_MEMBERS", defaults.max_archive_members),
        max_compression_ratio=_int(env, "MAX_COMPRESSION_RATIO", defaults.max_compression_ratio),
        max_twb_mb=_int(env, "MAX_TWB_MB", defaults.max_twb_mb),
        http_timeout_seconds=_float(env, "HTTP_TIMEOUT_SECONDS", defaults.http_timeout_seconds),
        session_ttl_minutes=_int(env, "SESSION_TTL_MINUTES", defaults.session_ttl_minutes),
        max_sessions=_int(env, "MAX_SESSIONS", defaults.max_sessions),
        default_preview_rows=_int(env, "DEFAULT_PREVIEW_ROWS", defaults.default_preview_rows),
        max_preview_rows=_int(env, "MAX_PREVIEW_ROWS", defaults.max_preview_rows),
        public_allowed_hosts=_csv(env, "PUBLIC_ALLOWED_HOSTS", defaults.public_allowed_hosts),
        public_list_url_template=env.get("PUBLIC_LIST_URL_TEMPLATE", "").strip()
        or defaults.public_list_url_template,
        public_download_url_template=env.get("PUBLIC_DOWNLOAD_URL_TEMPLATE", "").strip()
        or defaults.public_download_url_template,
        public_default_profile=env.get("TABLEAU_PUBLIC_PROFILE", "").strip(),
        server_url=env.get("TABLEAU_SERVER_URL", "").strip().rstrip("/"),
        server_site=env.get("TABLEAU_SITE", "").strip(),
        server_pat_name=env.get("TABLEAU_PAT_NAME", "").strip(),
        server_pat_secret=env.get("TABLEAU_PAT_SECRET", "").strip(),
        cors_origins=_csv(env, "CORS_ORIGINS", defaults.cors_origins),
    )
