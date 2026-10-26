"""Tests for the Tableau Server/Cloud source.

Three layers:
* a fake ``tableauserverclient`` module (fast, exercises our logic and error mapping),
* a *contract* test against the real installed library (catches API drift),
* tests/test_server_rest_fake.py drives the real library against a fake REST server.
"""

from __future__ import annotations

import inspect
import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from app.config import Settings
from app.errors import AppError
from app.sources import ServerSource

from .builders import SAMPLE_TWB, make_twbx

PAT_SECRET = "super-secret-token-value"


class FakeTSC:
    """Just enough of the tableauserverclient API surface that ServerSource uses."""

    class ServerResponseError(Exception):
        def __init__(self, code, summary="", detail=""):
            super().__init__(f"{code}: {summary}")
            self.code, self.summary, self.detail = code, summary, detail

    class FailedSignInError(Exception):  # real TSC: NOT a ServerResponseError subclass
        def __init__(self, code, summary="", detail=""):
            super().__init__(f"{code}: {summary}")
            self.code, self.summary, self.detail = code, summary, detail

    class InternalServerError(Exception):
        def __init__(self, code=500):
            super().__init__(f"HTTP {code}")
            self.code = code

    class NonXMLResponseError(Exception):
        pass

    class Filter:
        def __init__(self, field, operator, value):
            self.field, self.operator, self.value = field, operator, value

    class RequestOptions:
        class Field:
            Name = "name"

        class Operator:
            Has = "has"

        class _Filters:
            def __init__(self):
                self.items = []

            def add(self, f):
                self.items.append(f)

        def __init__(self, pagenumber=1, pagesize=None):
            self.pagesize = pagesize
            self.filter = self._Filters()

    class PersonalAccessTokenAuth:
        def __init__(self, token_name, personal_access_token, site_id=None):
            self.token_name, self.secret, self.site_id = token_name, personal_access_token, site_id

    class WorkbookItem:
        def __init__(self, id, name, project_name="Default", updated_at=None):
            self.id, self.name, self.project_name, self.updated_at = id, name, project_name, updated_at

    class Pager:
        def __init__(self, endpoint, request_opts=None):
            FakeTSC.last_request_opts = request_opts
            self._items = list(endpoint.items)

        def __iter__(self):
            return iter(self._items)

    # --- behaviour switches (reset per test) ---
    signin_error = None
    download_error = None
    download_bytes = b""
    download_name = "Sales.twbx"
    download_to = None  # make download() return this path instead of writing into filepath
    items: list = []
    last_request_opts = None
    last_server = None
    last_auth = None

    @classmethod
    def reset(cls):
        cls.signin_error = cls.download_error = cls.last_request_opts = cls.last_server = cls.last_auth = None
        cls.download_to = None
        cls.download_bytes = b""
        cls.download_name = "Sales.twbx"
        cls.items = []

    class _Workbooks:
        def __init__(self, outer):
            self.outer = outer

        @property
        def items(self):
            return FakeTSC.items

        def download(self, workbook_id, filepath=None, include_extract=True):
            if FakeTSC.download_error:
                raise FakeTSC.download_error
            if FakeTSC.download_to is not None:
                return str(FakeTSC.download_to)
            target = Path(filepath) / FakeTSC.download_name
            target.write_bytes(FakeTSC.download_bytes)
            return str(target)

    class _Auth:
        @contextmanager
        def sign_in(self, auth):
            FakeTSC.last_auth = auth
            if FakeTSC.signin_error:
                raise FakeTSC.signin_error
            yield

    class Server:
        def __init__(self, url, use_server_version=False):
            self.url, self.use_server_version = url, use_server_version
            self.auth = FakeTSC._Auth()
            self.workbooks = FakeTSC._Workbooks(self)
            FakeTSC.last_server = self


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeTSC.reset()
    yield
    FakeTSC.reset()


def configured(**overrides) -> Settings:
    base = dict(
        server_url="https://tableau.example.com",
        server_site="mysite",
        server_pat_name="my-token",
        server_pat_secret=PAT_SECRET,
    )
    base.update(overrides)
    return replace(Settings(), **base)


def make_source(**overrides) -> ServerSource:
    return ServerSource(configured(**overrides), tsc_module=FakeTSC)


def code_of(fn) -> str:
    with pytest.raises(AppError) as err:
        fn()
    return err.value.code


# --------------------------------------------------------------------------
# status / configuration
# --------------------------------------------------------------------------
def test_unconfigured_source_is_unavailable_and_says_how_to_fix_it():
    src = ServerSource(Settings(), tsc_module=FakeTSC)
    status = src.status()
    assert status["available"] is False
    assert "TABLEAU_SERVER_URL" in status["reason"] and "TABLEAU_PAT_SECRET" in status["reason"]
    assert code_of(lambda: src.list_workbooks()) == "SOURCE_UNAVAILABLE"
    assert code_of(lambda: src.fetch("0123456789abcdef", Path("."))) == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("missing", ["server_url", "server_pat_name", "server_pat_secret"])
def test_any_missing_credential_means_unavailable(missing):
    assert make_source(**{missing: ""}).status()["available"] is False


def test_configured_status_never_leaks_the_secret():
    status = make_source().status()
    assert status["available"] is True and status["verified"] is True
    assert PAT_SECRET not in json.dumps(status)


def test_missing_tsc_package_is_reported(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "tableauserverclient":
            raise ImportError("nope")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    status = ServerSource(configured()).status()
    assert status["available"] is False and "tableauserverclient" in status["reason"]


# --------------------------------------------------------------------------
# listing
# --------------------------------------------------------------------------
def test_list_workbooks_maps_items_and_signs_in_with_the_token():
    import datetime as dt

    FakeTSC.items = [
        FakeTSC.WorkbookItem("11111111-aaaa", "Sales", "Finance", dt.datetime(2024, 5, 1, 12, 0)),
        FakeTSC.WorkbookItem("22222222-bbbb", "Ops"),
    ]
    refs = make_source().list_workbooks()
    assert [(r.id, r.name, r.project) for r in refs] == [
        ("11111111-aaaa", "Sales", "Finance"),
        ("22222222-bbbb", "Ops", "Default"),
    ]
    assert refs[0].updated_at == "2024-05-01T12:00:00"
    assert FakeTSC.last_server.url == "https://tableau.example.com"
    assert FakeTSC.last_server.use_server_version is True
    assert (FakeTSC.last_auth.token_name, FakeTSC.last_auth.secret, FakeTSC.last_auth.site_id) == (
        "my-token",
        PAT_SECRET,
        "mysite",
    )
    assert FakeTSC.last_request_opts.filter.items == []


def test_list_workbooks_uses_a_server_side_name_filter_and_sanitises_it():
    make_source().list_workbooks("sales, q3: report")
    (flt,) = FakeTSC.last_request_opts.filter.items
    assert (flt.field, flt.operator) == ("name", "has")
    assert "," not in flt.value and ":" not in flt.value  # those are filter syntax characters
    make_source().list_workbooks(" , : ")
    assert FakeTSC.last_request_opts.filter.items == []


def test_list_workbooks_caps_the_result():
    FakeTSC.items = [FakeTSC.WorkbookItem(f"{i:08d}-x", f"wb{i}") for i in range(50)]
    assert len(make_source().list_workbooks(limit=10)) == 10


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------
def test_fetch_downloads_into_the_destination_directory(tmp_path):
    FakeTSC.download_bytes = make_twbx(tmp_path / "src.twbx").read_bytes()
    out = make_source().fetch("11111111-aaaa-bbbb", tmp_path / "dl")
    assert out.name == "Sales.twbx" and out.parent == (tmp_path / "dl").resolve()
    assert out.read_bytes() == FakeTSC.download_bytes


@pytest.mark.parametrize("bad", ["", "short", "../../etc/passwd", "id with spaces!!", "x" * 100, "abc/def/ghi/jkl"])
def test_fetch_rejects_malformed_ids(tmp_path, bad):
    assert code_of(lambda: make_source().fetch(bad, tmp_path)) == "INVALID_INPUT"


def test_fetch_rejects_a_download_outside_the_destination(tmp_path):
    elsewhere = tmp_path / "elsewhere.twb"
    elsewhere.write_bytes(SAMPLE_TWB)
    FakeTSC.download_to = elsewhere  # a library bug / hostile server must not make us read outside dest_dir
    assert code_of(lambda: make_source().fetch("11111111-aaaa-bbbb", tmp_path / "dl")) == "SERVER_ERROR"


def test_fetch_deletes_and_rejects_oversized_downloads(tmp_path):
    FakeTSC.download_bytes = SAMPLE_TWB
    src = make_source(max_file_mb=1)
    FakeTSC.download_bytes = b"<workbook>" + b"x" * 2_000_000
    assert code_of(lambda: src.fetch("11111111-aaaa-bbbb", tmp_path / "dl")) == "TOO_LARGE"
    assert list((tmp_path / "dl").glob("*")) == []


def test_fetch_rejects_non_workbook_downloads(tmp_path):
    FakeTSC.download_bytes = b"<html>proxy login page</html>"
    assert code_of(lambda: make_source().fetch("11111111-aaaa-bbbb", tmp_path / "dl")) == "SERVER_ERROR"
    assert list((tmp_path / "dl").glob("*")) == []


# --------------------------------------------------------------------------
# error mapping (never leaks the token)
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "code, expected_code, expected_status",
    [
        ("401001", "SERVER_AUTH_FAILED", 401),
        ("403004", "DOWNLOAD_FORBIDDEN", 403),
        ("404006", "NOT_FOUND", 404),
        ("500000", "SERVER_ERROR", 502),
    ],
)
def test_server_errors_are_mapped(tmp_path, code, expected_code, expected_status):
    FakeTSC.signin_error = FakeTSC.ServerResponseError(code, "Some summary", f"detail mentioning {PAT_SECRET}")
    with pytest.raises(AppError) as err:
        make_source().list_workbooks()
    assert (err.value.code, err.value.status_code) == (expected_code, expected_status)
    assert PAT_SECRET not in err.value.message  # detail text is never surfaced


def test_failed_sign_in_error_is_mapped_even_though_it_is_not_a_server_response_error():
    FakeTSC.signin_error = FakeTSC.FailedSignInError("401001", "Signin Error", f"detail {PAT_SECRET}")
    with pytest.raises(AppError) as err:
        make_source().list_workbooks()
    assert (err.value.code, err.value.status_code) == ("SERVER_AUTH_FAILED", 401)
    assert PAT_SECRET not in err.value.message


def test_5xx_and_wrong_url_errors_are_mapped():
    FakeTSC.signin_error = FakeTSC.InternalServerError(503)
    with pytest.raises(AppError) as err:
        make_source().list_workbooks()
    assert err.value.code == "SERVER_ERROR" and "503" in err.value.message

    FakeTSC.signin_error = FakeTSC.NonXMLResponseError("<html>")
    with pytest.raises(AppError) as err:
        make_source().list_workbooks()
    assert err.value.code == "SERVER_BAD_RESPONSE" and "TABLEAU_SERVER_URL" in err.value.message


def test_download_errors_are_mapped_too(tmp_path):
    FakeTSC.download_error = FakeTSC.ServerResponseError("403004", "Forbidden", "")
    assert code_of(lambda: make_source().fetch("11111111-aaaa-bbbb", tmp_path)) == "DOWNLOAD_FORBIDDEN"


def test_network_failures_are_mapped():
    import requests

    FakeTSC.signin_error = requests.exceptions.ConnectionError("dns failure")
    assert code_of(lambda: make_source().list_workbooks()) == "SERVER_UNREACHABLE"


def test_unknown_exceptions_are_not_swallowed():
    FakeTSC.signin_error = RuntimeError("bug")
    with pytest.raises(RuntimeError):
        make_source().list_workbooks()


# --------------------------------------------------------------------------
# contract with the REAL tableauserverclient (no network)
# --------------------------------------------------------------------------
def test_contract_with_the_real_tableauserverclient():
    TSC = pytest.importorskip("tableauserverclient")

    # constructor arguments we rely on
    server = TSC.Server("https://tableau.example.invalid", use_server_version=False)
    auth = TSC.PersonalAccessTokenAuth("name", "secret", site_id="site")
    assert auth.site_id == "site"

    # listing: RequestOptions + Filter + Pager
    opts = TSC.RequestOptions(pagesize=100)
    opts.filter.add(TSC.Filter(TSC.RequestOptions.Field.Name, TSC.RequestOptions.Operator.Has, "x"))
    assert TSC.Pager

    # download signature
    params = inspect.signature(server.workbooks.download).parameters
    assert "filepath" in params

    # errors expose what the error mapper reads
    err = TSC.ServerResponseError("401001", "Signin Error", "detail")
    assert (err.code, err.summary) == ("401001", "Signin Error")

    # the workbook item attributes the lister reads
    item = TSC.WorkbookItem(project_id="p", name="n")
    for attr in ("id", "name", "project_name", "updated_at"):
        assert hasattr(item, attr)
    assert hasattr(server.auth, "sign_in")


# --------------------------------------------------------------------------
# one PAT = one session: overlapping requests must not sign each other out
# --------------------------------------------------------------------------
def test_sessions_never_overlap_across_threads(monkeypatch):
    import threading
    import time

    state = {"active": 0, "max": 0}
    guard = threading.Lock()

    class SlowAuth:
        @contextmanager
        def sign_in(self, auth):
            with guard:
                state["active"] += 1
                state["max"] = max(state["max"], state["active"])
            try:
                time.sleep(0.05)
                yield
            finally:
                with guard:
                    state["active"] -= 1

    monkeypatch.setattr(FakeTSC.Server, "__init__", _server_init_with(SlowAuth))
    src = make_source()
    errors = []

    def work():
        try:
            src.list_workbooks()
        except Exception as exc:  # pragma: no cover - failure is reported below
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert state["max"] == 1


def test_lock_is_released_after_a_failed_sign_in():
    src = make_source()
    FakeTSC.signin_error = FakeTSC.FailedSignInError("401001", "Login error")
    assert code_of(lambda: src.list_workbooks()) == "SERVER_AUTH_FAILED"
    FakeTSC.signin_error = None
    assert src.list_workbooks() == []  # would hang forever if the lock leaked


def _server_init_with(auth_cls):
    def init(self, url, use_server_version=False):
        self.url, self.use_server_version = url, use_server_version
        self.auth = auth_cls()
        self.workbooks = FakeTSC._Workbooks(self)
        FakeTSC.last_server = self

    return init
