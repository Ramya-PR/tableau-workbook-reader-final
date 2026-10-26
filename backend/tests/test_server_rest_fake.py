"""Run the REAL tableauserverclient against an in-process fake of the Tableau REST API.

This exercises our ServerSource together with TSC's real behaviour (version
handshake, personal-access-token sign-in, paging via ``Pager``, workbook
download and filename handling). It is *not* a real Tableau Server: the fake is
our reconstruction of the REST protocol, so passing here shows the plumbing and
TSC usage are right, not that a given Tableau version behaves identically.
"""

from __future__ import annotations

import socket
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from app.config import Settings
from app.errors import AppError
from app.sources import ServerSource

from .builders import make_twbx

pytest.importorskip("tableauserverclient")

NS = "http://tableau.com/api"
SITE_ID = "site-luid-1234"
TOKEN = "auth-token-abc"
WORKBOOKS = [
    {"id": "11111111-aaaa-4bbb-8ccc-000000000001", "name": "Sales Overview", "project": "Finance"},
    {"id": "22222222-aaaa-4bbb-8ccc-000000000002", "name": "Ops Dashboard", "project": "Operations"},
    {"id": "33333333-aaaa-4bbb-8ccc-000000000003", "name": "Sales Forecast", "project": "Finance"},
]
PAGE_SIZE = 2  # the fake caps pages, so TSC's Pager must walk several of them


class FakeTableau:
    def __init__(self, workbook_file: bytes):
        self.workbook_file = workbook_file
        self.requests: list[tuple[str, str]] = []
        self.signin_status = 200
        self.download_status = 200
        self.pat_seen: tuple[str, str, str] | None = None


def make_handler(state: FakeTableau):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence
            pass

        def _send(self, status: int, body: str | bytes, ctype="application/xml", headers=None):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _error(self, status: int, code: str, summary: str):
            self._send(
                status,
                f'<tsResponse xmlns="{NS}"><error code="{code}"><summary>{summary}</summary>'
                f"<detail>detail text</detail></error></tsResponse>",
            )

        def do_GET(self):
            url = urlparse(self.path)
            state.requests.append(("GET", self.path))
            if url.path.lower().endswith("/serverinfo"):  # TSC requests ".../serverInfo"
                return self._send(
                    200,
                    f'<tsResponse xmlns="{NS}"><serverInfo><productVersion build="x">2024.1</productVersion>'
                    f"<restApiVersion>3.22</restApiVersion></serverInfo></tsResponse>",
                )
            if self.headers.get("X-Tableau-Auth") != TOKEN:
                return self._error(401, "401002", "Unauthorized Access")

            if url.path.endswith("/workbooks"):
                q = parse_qs(url.query)
                page = int(q.get("pageNumber", ["1"])[0])
                match = WORKBOOKS
                for f in q.get("filter", []):
                    field, op, value = f.split(":", 2)
                    assert (field, op) == ("name", "has")
                    match = [w for w in match if value.lower() in w["name"].lower()]
                chunk = match[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
                items = "".join(
                    f'<workbook id="{w["id"]}" name="{w["name"]}" contentUrl="{w["name"].replace(" ", "")}" '
                    f'size="12" createdAt="2024-01-01T00:00:00Z" updatedAt="2024-05-01T12:30:00Z">'
                    f'<project id="p-{w["project"]}" name="{w["project"]}"/><owner id="owner-1"/><tags/></workbook>'
                    for w in chunk
                )
                return self._send(
                    200,
                    f'<tsResponse xmlns="{NS}"><pagination pageNumber="{page}" pageSize="{PAGE_SIZE}" '
                    f'totalAvailable="{len(match)}"/><workbooks>{items}</workbooks></tsResponse>',
                )

            if url.path.endswith("/content") and "/workbooks/" in url.path:
                if state.download_status != 200:
                    return self._error(state.download_status, f"{state.download_status}004", "Forbidden")
                return self._send(
                    200,
                    state.workbook_file,
                    ctype="application/octet-stream",
                    headers={"Content-Disposition": 'name="tableau_workbook"; filename="Sales Overview.twbx"'},
                )
            return self._error(404, "404000", "Not found")

        def do_POST(self):
            state.requests.append(("POST", self.path))
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode()
            if self.path.endswith("/auth/signin"):
                if state.signin_status != 200:
                    return self._error(state.signin_status, f"{state.signin_status}001", "Signin Error")
                import re

                name = re.search(r'personalAccessTokenName="([^"]*)"', body)
                secret = re.search(r'personalAccessTokenSecret="([^"]*)"', body)
                site = re.search(r'<site contentUrl="([^"]*)"', body)
                state.pat_seen = (
                    name.group(1) if name else "",
                    secret.group(1) if secret else "",
                    site.group(1) if site else "",
                )
                return self._send(
                    200,
                    f'<tsResponse xmlns="{NS}"><credentials token="{TOKEN}"><site id="{SITE_ID}" '
                    f'contentUrl="mysite"/><user id="user-luid"/></credentials></tsResponse>',
                )
            if self.path.endswith("/auth/signout"):
                return self._send(204, b"")
            return self._error(404, "404000", "Not found")

    return Handler


@pytest.fixture
def fake(tmp_path):
    state = FakeTableau(make_twbx(tmp_path / "served.twbx").read_bytes())
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()
    server.server_close()


def source_for(state: FakeTableau, **overrides) -> ServerSource:
    s = replace(
        Settings(),
        server_url=state.url,
        server_site="mysite",
        server_pat_name="my-token",
        server_pat_secret="s3cret-value",
        **overrides,
    )
    return ServerSource(s)  # real tableauserverclient


def code_of(fn) -> str:
    with pytest.raises(AppError) as err:
        fn()
    return err.value.code


def test_list_walks_every_page_with_the_real_pager(fake):
    refs = source_for(fake).list_workbooks()
    assert [r.name for r in refs] == ["Sales Overview", "Ops Dashboard", "Sales Forecast"]
    assert refs[0].project == "Finance" and refs[0].id == WORKBOOKS[0]["id"]
    assert refs[0].updated_at and refs[0].updated_at.startswith("2024-05-01")
    list_calls = [p for m, p in fake.requests if m == "GET" and "/workbooks?" in p]
    assert len(list_calls) == 2  # 3 workbooks, 2 per page


def test_version_handshake_sign_in_and_sign_out(fake):
    source_for(fake).list_workbooks()
    assert fake.pat_seen == ("my-token", "s3cret-value", "mysite")
    # use_server_version=True: TSC signs in at its default API version, asks the server which REST API
    # version it speaks, and uses that version for everything afterwards (observed with TSC 0.42).
    assert ("POST", "/api/2.4/auth/signin") in fake.requests
    assert ("GET", "/api/2.4/serverInfo") in fake.requests
    assert any(m == "GET" and p.startswith("/api/3.22/sites/") for m, p in fake.requests)
    assert ("POST", "/api/3.22/auth/signout") in fake.requests  # we always sign out


def test_name_filter_is_sent_to_the_server(fake):
    refs = source_for(fake).list_workbooks("sales")
    assert [r.name for r in refs] == ["Sales Overview", "Sales Forecast"]
    assert any("filter=name%3Ahas%3Asales" in p or "filter=name:has:sales" in p for _, p in fake.requests)


def test_fetch_downloads_the_workbook_file(fake, tmp_path):
    out = source_for(fake).fetch(WORKBOOKS[0]["id"], tmp_path / "dl")
    assert out.name == "Sales Overview.twbx"  # filename comes from Content-Disposition
    assert out.read_bytes() == fake.workbook_file


def test_bad_credentials_are_reported_without_leaking_the_token(fake):
    fake.signin_status = 401
    with pytest.raises(AppError) as err:
        source_for(fake).list_workbooks()
    assert err.value.code == "SERVER_AUTH_FAILED"
    assert "s3cret-value" not in err.value.message


def test_forbidden_download(fake, tmp_path):
    fake.download_status = 403
    assert code_of(lambda: source_for(fake).fetch(WORKBOOKS[0]["id"], tmp_path / "dl")) == "DOWNLOAD_FORBIDDEN"


def test_server_errors(fake):
    fake.signin_status = 503
    assert code_of(lambda: source_for(fake).list_workbooks()) == "SERVER_ERROR"


def test_url_that_is_not_a_tableau_server(tmp_path):
    class Html(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = b"<html><body>Welcome to my homepage</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = do_GET

    server = ThreadingHTTPServer(("127.0.0.1", 0), Html)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        settings = replace(
            Settings(),
            server_url=f"http://127.0.0.1:{server.server_address[1]}",
            server_pat_name="n",
            server_pat_secret="s",
        )
        assert code_of(lambda: ServerSource(settings).list_workbooks()) == "SERVER_BAD_RESPONSE"
    finally:
        server.shutdown()
        server.server_close()


def test_unreachable_server(tmp_path):
    with socket.socket() as s:  # grab a port that nothing listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings = replace(
        Settings(),
        server_url=f"http://127.0.0.1:{port}",
        server_pat_name="n",
        server_pat_secret="s",
    )
    assert code_of(lambda: ServerSource(settings).list_workbooks()) == "SERVER_UNREACHABLE"
