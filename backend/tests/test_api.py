"""End-to-end tests through the HTTP API (FastAPI TestClient)."""

from __future__ import annotations

import json
import os
import zipfile
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.config import Settings
from app.main import create_app
from app.sources import build_sources

from .builders import SAMPLE_TWB, SECRET_PASSWORD, SECRET_USER, make_twbx
from .test_sources_server import PAT_SECRET, FakeTSC


@pytest.fixture
def make_client():
    """Factory: ``make_client(public_handler=..., tsc=..., **settings_overrides)``."""
    opened: list[TestClient] = []

    def factory(public_handler=None, tsc=None, raise_server_exceptions=True, **overrides):
        settings = replace(Settings(), **overrides)
        sources = build_sources(
            settings,
            public_transport=httpx.MockTransport(public_handler) if public_handler else None,
            tsc_module=tsc,
        )
        app = create_app(settings, sources=sources, frontend_dist=overrides.pop("frontend_dist", None))
        client = TestClient(app, raise_server_exceptions=raise_server_exceptions)
        client.__enter__()  # runs lifespan; __exit__ later deletes every temp dir
        opened.append(client)
        return client

    yield factory
    for c in opened:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()


def upload(client, path: Path, name: str | None = None):
    with open(path, "rb") as fh:
        return client.post(
            "/api/workbooks/upload", files={"file": (name or path.name, fh, "application/octet-stream")}
        )


def error(resp) -> dict:
    assert resp.status_code >= 400, resp.text
    return resp.json()["detail"]


def sessions(client) -> int:
    return len(client.app.state.store)


@pytest.fixture
def csv_twbx(tmp_path):
    return make_twbx(tmp_path / "csv.twbx", files={"Data/orders.csv": b"id,name\n" + b"1,a\n2,b\n3,c\n"})


@pytest.fixture
def hyper_twbx(tmp_path, hyper_file):
    return make_twbx(tmp_path / "hyper.twbx", files={"Data/Extracts/sales.hyper": hyper_file.read_bytes()})


# --------------------------------------------------------------------------
# health / capabilities
# --------------------------------------------------------------------------
def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_capabilities_describe_every_source_and_never_leak_secrets(make_client):
    c = make_client(
        tsc=FakeTSC,
        server_url="https://tableau.example.com",
        server_pat_name="tok",
        server_pat_secret=PAT_SECRET,
    )
    body = c.get("/api/capabilities").json()
    assert PAT_SECRET not in json.dumps(body)
    assert body["sources"]["upload"]["verified"] is True
    assert body["sources"]["public"]["available"] is True and body["sources"]["public"]["verified"] is True
    assert body["sources"]["server"]["available"] is True and body["sources"]["server"]["verified"] is True
    assert body["preview"]["csv"] is True
    assert body["limits"]["max_preview_rows"] == 1000


def test_unconfigured_server_source_is_reported_unavailable(client):
    server = client.get("/api/capabilities").json()["sources"]["server"]
    assert server["available"] is False and "TABLEAU_SERVER_URL" in server["reason"]


# --------------------------------------------------------------------------
# upload -> parse -> preview -> delete
# --------------------------------------------------------------------------
def test_upload_twbx_returns_metadata_and_data_files(client, csv_twbx):
    resp = upload(client, csv_twbx)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["file_kind"] == "twbx" and body["filename"] == "csv.twbx" and body["source"] == "upload"
    assert body["metadata"]["summary"]["worksheets"] == 2
    assert body["metadata"]["summary"]["calculated_fields"] == 2
    assert [(d["path"], d["kind"], d["previewable"]) for d in body["data_files"]] == [
        ("Data/orders.csv", "csv", True)
    ]
    assert "warnings" not in body["metadata"]  # warnings are top-level only
    assert any("Shared Customers" in w for w in body["warnings"])
    # credentials in the workbook never reach the browser
    assert SECRET_USER not in resp.text and SECRET_PASSWORD not in resp.text
    # nor does a server-side path
    assert "twbparser-" not in resp.text


def test_csv_preview_flow_and_delete(client, csv_twbx):
    body = upload(client, csv_twbx).json()
    wid, path = body["workbook_id"], body["data_files"][0]["path"]

    tables = client.get(f"/api/workbooks/{wid}/preview/tables", params={"file": path}).json()
    assert tables["tables"][0]["row_count"] == 3

    rows = client.get(f"/api/workbooks/{wid}/preview/rows", params={"file": path, "limit": 2}).json()
    assert rows["rows"] == [["1", "a"], ["2", "b"]] and rows["limit"] == 2
    rows = client.get(f"/api/workbooks/{wid}/preview/rows", params={"file": path, "offset": 2}).json()
    assert rows["rows"] == [["3", "c"]]

    session_dir = client.app.state.store.get(wid).dir
    assert session_dir.exists()
    assert client.delete(f"/api/workbooks/{wid}").json() == {"deleted": True}
    assert not session_dir.exists()  # company data does not linger on disk
    err = error(client.get(f"/api/workbooks/{wid}/preview/rows", params={"file": path}))
    assert err["code"] == "SESSION_EXPIRED"
    assert client.delete(f"/api/workbooks/{wid}").json() == {"deleted": False}


def test_hyper_preview_flow(client, hyper_twbx):
    body = upload(client, hyper_twbx).json()
    wid = body["workbook_id"]
    (df,) = body["data_files"]
    assert (df["kind"], df["previewable"]) == ("hyper", True)
    # the extract was linked to the datasource that uses it
    sales = next(d for d in body["metadata"]["datasources"] if d["caption"] == "Sample Sales")
    assert sales["data_file"] == df["path"]

    tables = client.get(f"/api/workbooks/{wid}/preview/tables", params={"file": df["path"]}).json()["tables"]
    assert tables[0]["name"] == "Extract.Extract" and tables[0]["row_count"] == 250

    rows = client.get(
        f"/api/workbooks/{wid}/preview/rows",
        params={"file": df["path"], "table": "Extract.Extract", "offset": 240, "limit": 100},
    ).json()
    assert len(rows["rows"]) == 10 and [c["name"] for c in rows["columns"]][0] == "Region"


def test_preview_limit_is_capped_by_the_server(make_client, csv_twbx):
    c = make_client(max_preview_rows=2, default_preview_rows=1)
    body = upload(c, csv_twbx).json()
    wid, path = body["workbook_id"], body["data_files"][0]["path"]
    assert len(c.get(f"/api/workbooks/{wid}/preview/rows", params={"file": path}).json()["rows"]) == 1  # default
    big = c.get(f"/api/workbooks/{wid}/preview/rows", params={"file": path, "limit": 100000}).json()
    assert len(big["rows"]) == 2 and big["limit"] == 2  # capped


def test_bare_twb_upload(client, tmp_path):
    p = tmp_path / "plain.twb"
    p.write_bytes(SAMPLE_TWB)
    body = upload(client, p).json()
    assert body["file_kind"] == "twb" and body["data_files"] == []
    assert any(".twb file" in w for w in body["warnings"])


def test_twbx_without_data_files_explains_why(client, tmp_path):
    body = upload(client, make_twbx(tmp_path / "nodata.twbx")).json()
    assert body["data_files"] == []
    assert any("live connections" in w for w in body["warnings"])


def test_unpreviewable_data_files_are_listed_with_a_reason(client, tmp_path):
    p = make_twbx(tmp_path / "legacy.twbx", files={"Data/old.tde": b"tde", "Data/book.xls": b"xl"})
    body = upload(client, p).json()
    by_kind = {d["kind"]: d for d in body["data_files"]}
    assert by_kind["tde"]["previewable"] is False and ".hyper" in by_kind["tde"]["reason"]
    assert by_kind["excel"]["previewable"] is False and ".xlsx" in by_kind["excel"]["reason"]
    wid = body["workbook_id"]
    err = error(client.get(f"/api/workbooks/{wid}/preview/tables", params={"file": "Data/old.tde"}))
    assert err["code"] == "PREVIEW_UNAVAILABLE"


def test_preview_only_serves_files_that_are_in_the_workbook(client, csv_twbx):
    wid = upload(client, csv_twbx).json()["workbook_id"]
    for evil in ["../../etc/passwd", "/etc/passwd", "Data/other.csv", "Sales.twb", ""]:
        resp = client.get(f"/api/workbooks/{wid}/preview/rows", params={"file": evil})
        assert resp.status_code in (404, 422), (evil, resp.text)
        if resp.status_code == 404:
            assert resp.json()["detail"]["code"] == "UNKNOWN_DATA_FILE"


def test_invalid_paging_parameters_are_rejected(client, csv_twbx):
    body = upload(client, csv_twbx).json()
    wid, path = body["workbook_id"], body["data_files"][0]["path"]
    for params in ({"offset": -1}, {"limit": 0}, {"offset": "abc"}):
        resp = client.get(f"/api/workbooks/{wid}/preview/rows", params={"file": path, **params})
        assert resp.status_code == 422


# --------------------------------------------------------------------------
# upload failures: clean errors, nothing left behind
# --------------------------------------------------------------------------
def test_wrong_extension(client, tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("hello")
    assert error(upload(client, p))["code"] == "UNSUPPORTED_FILE"
    assert sessions(client) == 0


def test_not_a_workbook_inside(client, tmp_path):
    p = tmp_path / "fake.twb"
    p.write_bytes(b"<html><body>hi</body></html>")
    resp = upload(client, p)
    assert resp.status_code == 422 and error(resp)["code"] in ("NOT_A_WORKBOOK", "PARSE_ERROR")
    assert sessions(client) == 0


def test_tds_upload_is_explained(client, tmp_path):
    p = tmp_path / "source.twb"
    p.write_bytes(b"<datasource name='x'/>")
    detail = error(upload(client, p))
    assert detail["code"] == "NOT_A_WORKBOOK" and ".tds" in detail["message"]


def test_zip_slip_upload_is_refused(client, tmp_path):
    p = tmp_path / "evil.twbx"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("W.twb", SAMPLE_TWB)
        zf.writestr("../../escape.txt", b"boom")
    resp = upload(client, p)
    assert resp.status_code == 422 and error(resp)["code"] == "ARCHIVE_UNSAFE"
    assert sessions(client) == 0
    assert not (Path(tmp_path).parent / "escape.txt").exists()


def test_oversized_upload_is_rejected_and_cleaned_up(make_client, tmp_path):
    c = make_client(max_file_mb=1)
    p = tmp_path / "big.twb"
    p.write_bytes(b"<workbook>" + os.urandom(1_500_000))
    resp = upload(c, p)
    assert resp.status_code == 413 and error(resp)["code"] == "TOO_LARGE"
    assert sessions(c) == 0


def test_upload_filename_cannot_escape_the_session_directory(client, tmp_path):
    p = make_twbx(tmp_path / "x.twbx")
    body = upload(client, p, name="../../../../tmp/pwned.twbx").json()
    assert body["filename"] == "pwned.twbx"  # only the basename survives


# --------------------------------------------------------------------------
# Tableau Public
# --------------------------------------------------------------------------
def test_parse_from_tableau_public(make_client, tmp_path):
    served = make_twbx(tmp_path / "served.twbx", files={"Data/orders.csv": b"a\n1\n"}).read_bytes()
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=served)

    c = make_client(public_handler=handler)
    resp = c.post(
        "/api/workbooks/parse",
        json={"source": "public", "reference": "https://public.tableau.com/views/SalesDash/Overview"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "public" and body["filename"] == "SalesDash.twbx"
    assert body["metadata"]["summary"]["worksheets"] == 2 and body["data_files"][0]["kind"] == "csv"
    assert seen == ["https://public.tableau.com/workbooks/SalesDash.twbx"]


@pytest.mark.parametrize(
    "status, code, http",
    [(403, "DOWNLOAD_DISABLED", 403), (404, "DOWNLOAD_UNAVAILABLE", 404), (500, "DOWNLOAD_FAILED", 502)],
)
def test_public_download_failures_are_reported_and_cleaned_up(make_client, status, code, http):
    c = make_client(public_handler=lambda r: httpx.Response(status))
    resp = c.post("/api/workbooks/parse", json={"source": "public", "reference": "SalesDash"})
    assert resp.status_code == http and error(resp)["code"] == code
    assert sessions(c) == 0


def test_public_rejects_non_tableau_urls_without_any_request(make_client):
    called = []
    c = make_client(public_handler=lambda r: called.append(r) or httpx.Response(200))
    resp = c.post("/api/workbooks/parse", json={"source": "public", "reference": "https://evil.example.com/views/x/y"})
    assert resp.status_code == 400 and error(resp)["code"] == "INVALID_INPUT"
    assert called == [] and sessions(c) == 0


def test_list_public_workbooks(make_client):
    c = make_client(
        public_handler=lambda r: httpx.Response(
            200, json={"contents": [{"workbookRepoUrl": "SalesDash", "title": "Sales Dash"}]}
        )
    )
    body = c.get("/api/sources/public/workbooks", params={"profile": "some.person"}).json()
    assert body["workbooks"] == [{"id": "SalesDash", "name": "Sales Dash", "project": None, "updated_at": None}]
    assert c.get("/api/sources/public/workbooks", params={"profile": "some.person", "q": "zzz"}).json() == {
        "workbooks": []
    }


# --------------------------------------------------------------------------
# Tableau Server
# --------------------------------------------------------------------------
def test_server_source_requires_configuration(client):
    resp = client.post("/api/workbooks/parse", json={"source": "server", "reference": "11111111-aaaa-bbbb"})
    assert resp.status_code == 503 and error(resp)["code"] == "SOURCE_UNAVAILABLE"
    assert client.get("/api/sources/server/workbooks").status_code == 503


def test_server_list_and_parse(make_client, tmp_path):
    FakeTSC.reset()
    FakeTSC.items = [FakeTSC.WorkbookItem("11111111-aaaa-bbbb", "Sales", "Finance")]
    FakeTSC.download_bytes = make_twbx(tmp_path / "s.twbx").read_bytes()
    c = make_client(
        tsc=FakeTSC, server_url="https://tableau.example.com", server_pat_name="tok", server_pat_secret=PAT_SECRET
    )
    listed = c.get("/api/sources/server/workbooks").json()["workbooks"]
    assert listed[0]["id"] == "11111111-aaaa-bbbb" and listed[0]["project"] == "Finance"

    resp = c.post("/api/workbooks/parse", json={"source": "server", "reference": listed[0]["id"]})
    assert resp.status_code == 200 and resp.json()["source"] == "server"
    assert PAT_SECRET not in resp.text
    FakeTSC.reset()


def test_server_auth_failure_is_reported(make_client):
    FakeTSC.reset()
    FakeTSC.signin_error = FakeTSC.FailedSignInError("401001", "Signin Error")
    c = make_client(tsc=FakeTSC, server_url="https://t.example.com", server_pat_name="tok", server_pat_secret=PAT_SECRET)
    resp = c.get("/api/sources/server/workbooks")
    assert resp.status_code == 401 and error(resp)["code"] == "SERVER_AUTH_FAILED"
    assert PAT_SECRET not in resp.text
    FakeTSC.reset()


# --------------------------------------------------------------------------
# routing / validation
# --------------------------------------------------------------------------
def test_parse_rejects_upload_as_a_source_and_unknown_sources(client):
    resp = client.post("/api/workbooks/parse", json={"source": "upload", "reference": "x"})
    assert resp.status_code == 400 and error(resp)["code"] == "INVALID_INPUT"
    resp = client.post("/api/workbooks/parse", json={"source": "nope", "reference": "x"})
    assert resp.status_code == 400
    assert error(client.get("/api/sources/nope/workbooks"))["code"] == "UNKNOWN_SOURCE"
    assert error(client.get("/api/sources/upload/workbooks"))["code"] == "LIST_UNSUPPORTED"
    assert client.post("/api/workbooks/parse", json={"source": "public"}).status_code == 422  # missing field


def test_sessions_expire(make_client, csv_twbx):
    c = make_client(session_ttl_minutes=1)
    body = upload(c, csv_twbx).json()
    wid, path = body["workbook_id"], body["data_files"][0]["path"]
    store = c.app.state.store
    now = store._clock()
    store._clock = lambda: now + 3600  # an hour later
    assert error(c.get(f"/api/workbooks/{wid}/preview/rows", params={"file": path}))["code"] == "SESSION_EXPIRED"


def test_unexpected_errors_are_generic_and_clean_up(make_client, csv_twbx, monkeypatch):
    c = make_client(raise_server_exceptions=False)

    def boom(*a, **k):
        raise RuntimeError("secret internal detail at /srv/app/x.py")

    monkeypatch.setattr(app_main, "parse_workbook", boom)
    resp = upload(c, csv_twbx)
    assert resp.status_code == 500
    assert resp.json()["detail"] == {"code": "INTERNAL_ERROR", "message": "Something went wrong on the server."}
    assert "secret internal detail" not in resp.text
    assert sessions(c) == 0


def test_temp_directories_are_removed_on_shutdown(make_client, csv_twbx):
    c = make_client()
    wid = upload(c, csv_twbx).json()["workbook_id"]
    session_dir = c.app.state.store.get(wid).dir
    assert session_dir.exists()
    c.__exit__(None, None, None)  # triggers the lifespan shutdown
    assert not session_dir.exists()
    c.__enter__()  # let the fixture's final __exit__ stay balanced


def test_cors_allows_only_configured_origins(make_client):
    c = make_client(cors_origins=("http://localhost:5173",))
    ok = c.options(
        "/api/health", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"}
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = c.options("/api/health", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers


def test_built_frontend_is_served_when_present(make_client, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><title>app</title></html>")
    settings = Settings()
    app = create_app(settings, sources=build_sources(settings), frontend_dist=dist)
    with TestClient(app) as c:
        assert "<title>app</title>" in c.get("/").text
        assert c.get("/api/health").status_code == 200  # API routes win over the static mount


def test_upload_includes_migration_assessment(make_client, tmp_path):
    client = make_client()
    path = make_twbx(tmp_path / "Sales.twbx")
    with open(path, "rb") as fh:
        body = client.post("/api/workbooks/upload", files={"file": ("Sales.twbx", fh)}).json()
    mig = body["metadata"]["migration"]
    assert set(mig) == {"overall", "counts", "items"}
    names = {(i["scope"], i["name"]) for i in mig["items"]}
    assert ("datasource", "Orders DB") in names and ("field", "Top N") in names
    profit = next(f for f in body["metadata"]["fields"] if f["name"] == "Profit")
    assert profit["migration_level"] == "low"
    assert SECRET_PASSWORD not in json.dumps(body) and SECRET_USER not in json.dumps(body)


def test_excel_in_a_twbx_previews_end_to_end(client, tmp_path):
    from .test_preview import make_xlsx

    xlsx = make_xlsx(tmp_path / "book.xlsx", {"Sheet1": [["Region", "Sales"], ["East", 1], ["West", 2], ["North", 3]]})
    p = make_twbx(tmp_path / "xl.twbx", files={"Data/book.xlsx": xlsx.read_bytes()})
    body = upload(client, p).json()
    assert body["data_files"][0]["previewable"] is True
    wid = body["workbook_id"]
    tables = client.get(f"/api/workbooks/{wid}/preview/tables", params={"file": "Data/book.xlsx"}).json()["tables"]
    assert tables[0]["name"] == "Sheet1" and tables[0]["row_count"] == 3
    rows = client.get(
        f"/api/workbooks/{wid}/preview/rows", params={"file": "Data/book.xlsx", "table": "Sheet1", "offset": 1, "limit": 1}
    ).json()
    assert rows["rows"] == [["West", 2]]
