from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from app.config import Settings
from app.errors import AppError
from app.sources import PublicSource

from .builders import SAMPLE_TWB, make_twbx


def twbx_bytes(tmp_path) -> bytes:
    return make_twbx(tmp_path / "served.twbx").read_bytes()


def make_source(handler, **overrides):
    settings = replace(Settings(), **overrides)
    return PublicSource(settings, transport=httpx.MockTransport(handler)), settings


def code_of(fn) -> str:
    with pytest.raises(AppError) as err:
        fn()
    return err.value.code


# --------------------------------------------------------------------------
# reference parsing
# --------------------------------------------------------------------------
@pytest.fixture
def src():
    return PublicSource(Settings())


@pytest.mark.parametrize(
    "reference, expected",
    [
        ("https://public.tableau.com/views/SalesDash/Overview?:language=en-US&:display_count=n", "SalesDash"),
        ("https://public.tableau.com/views/Sales_Dash.v2/Sheet%201", "Sales_Dash.v2"),
        ("https://public.tableau.com/app/profile/some.person/viz/SalesDash/Overview", "SalesDash"),
        ("public.tableau.com/views/SalesDash/Overview", "SalesDash"),
        ("https://public.tableau.com/workbooks/SalesDash.twbx", "SalesDash"),
        ("  SalesDash  ", "SalesDash"),
    ],
)
def test_parse_reference_accepts_common_forms(src, reference, expected):
    assert src.parse_reference(reference) == expected


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "   ",
        "https://evil.example.com/views/SalesDash/Overview",
        "http://public.tableau.com/views/SalesDash/Overview",  # not https
        "https://public.tableau.com@evil.example.com/views/SalesDash/Overview",  # userinfo trick
        "https://public.tableau.com.evil.example.com/views/SalesDash/Overview",  # look-alike host
        "https://public.tableau.com/shared/ABC123",  # short link, can't be resolved offline
        "https://public.tableau.com/",  # no workbook name
        "../../etc/passwd",
        "a/b",
        "name with spaces",
        "file:///etc/passwd",
        "https://public.tableau.com/views/..%2F..%2Fsecret/Sheet",
    ],
)
def test_parse_reference_rejects_bad_input(src, reference):
    assert code_of(lambda: src.parse_reference(reference)) == "INVALID_INPUT"


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------
def test_fetch_downloads_twbx_from_the_rebuilt_url(tmp_path):
    seen = []
    body = twbx_bytes(tmp_path)

    def handler(request: httpx.Request):
        seen.append(str(request.url))
        return httpx.Response(200, content=body)

    source, _ = make_source(handler)
    path = source.fetch("https://public.tableau.com/views/SalesDash/Overview?evil=1", tmp_path / "dl")
    assert path.name == "SalesDash.twbx" and path.read_bytes() == body
    # only the *name* is taken from the user's URL; the request URL is rebuilt
    assert seen == ["https://public.tableau.com/workbooks/SalesDash.twbx"]


def test_fetch_falls_back_to_twb_when_twbx_is_404(tmp_path):
    def handler(request: httpx.Request):
        if request.url.path.endswith(".twbx"):
            return httpx.Response(404)
        return httpx.Response(200, content=SAMPLE_TWB)

    source, _ = make_source(handler)
    assert source.fetch("SalesDash", tmp_path).name == "SalesDash.twb"


def test_fetch_404_everywhere_suggests_upload(tmp_path):
    source, _ = make_source(lambda r: httpx.Response(404))
    with pytest.raises(AppError) as err:
        source.fetch("SalesDash", tmp_path)
    assert err.value.code == "DOWNLOAD_UNAVAILABLE" and err.value.status_code == 404
    assert "Upload" in err.value.message


@pytest.mark.parametrize("status", [401, 403])
def test_fetch_forbidden_means_downloads_disabled(tmp_path, status):
    source, _ = make_source(lambda r: httpx.Response(status))
    assert code_of(lambda: source.fetch("SalesDash", tmp_path)) == "DOWNLOAD_DISABLED"


def test_fetch_server_error(tmp_path):
    source, _ = make_source(lambda r: httpx.Response(500))
    assert code_of(lambda: source.fetch("SalesDash", tmp_path)) == "DOWNLOAD_FAILED"


def test_fetch_ignores_html_pages_served_with_200(tmp_path):
    source, _ = make_source(lambda r: httpx.Response(200, content=b"<html><body>Sign in</body></html>"))
    assert code_of(lambda: source.fetch("SalesDash", tmp_path)) == "DOWNLOAD_UNAVAILABLE"
    assert list(tmp_path.glob("*")) == []  # nothing left behind


def test_fetch_follows_redirects_within_allowed_hosts(tmp_path):
    body = twbx_bytes(tmp_path)

    def handler(request: httpx.Request):
        if request.url.path == "/workbooks/SalesDash.twbx":
            return httpx.Response(302, headers={"location": "/files/abc/SalesDash.twbx"})
        assert request.url.path == "/files/abc/SalesDash.twbx"
        return httpx.Response(200, content=body)

    source, _ = make_source(handler)
    assert source.fetch("SalesDash", tmp_path / "dl").read_bytes() == body


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example.com/x.twbx",
        "http://public.tableau.com/x.twbx",  # downgrade to http
        "https://169.254.169.254/latest/meta-data/",  # cloud metadata
        "https://public.tableau.com:99999/x",  # invalid port must not crash
        "https://user:pw@public.tableau.com/x",
        "file:///etc/passwd",
    ],
)
def test_redirects_to_disallowed_targets_are_blocked(tmp_path, location):
    calls = []

    def handler(request: httpx.Request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": location})

    source, _ = make_source(handler)
    assert code_of(lambda: source.fetch("SalesDash", tmp_path)) == "REDIRECT_BLOCKED"
    assert calls == ["https://public.tableau.com/workbooks/SalesDash.twbx"]  # the bad target is never requested


def test_extra_allowed_hosts_can_be_configured(tmp_path):
    body = twbx_bytes(tmp_path)

    def handler(request: httpx.Request):
        if request.url.host == "public.tableau.com":
            return httpx.Response(302, headers={"location": "https://cdn.example.net/a.twbx"})
        return httpx.Response(200, content=body)

    source, _ = make_source(handler, public_allowed_hosts=("public.tableau.com", "cdn.example.net"))
    assert source.fetch("SalesDash", tmp_path / "dl").read_bytes() == body


def test_redirect_loop_gives_up(tmp_path):
    source, _ = make_source(lambda r: httpx.Response(302, headers={"location": "/workbooks/SalesDash.twbx"}))
    assert code_of(lambda: source.fetch("SalesDash", tmp_path)) == "DOWNLOAD_FAILED"


def test_size_limit_by_content_length(tmp_path):
    source, _ = make_source(lambda r: httpx.Response(200, content=b"x" * 2_000_000), max_file_mb=1)
    assert code_of(lambda: source.fetch("SalesDash", tmp_path)) == "TOO_LARGE"


def test_size_limit_while_streaming_without_content_length_leaves_no_partial_file(tmp_path):
    def chunks():
        for _ in range(30):
            yield b"x" * 100_000  # chunked: no Content-Length header

    source, _ = make_source(lambda r: httpx.Response(200, content=chunks()), max_file_mb=1)
    assert code_of(lambda: source.fetch("SalesDash", tmp_path)) == "TOO_LARGE"
    assert list(tmp_path.glob("*")) == []


def test_network_errors_are_reported_cleanly(tmp_path):
    def boom(request):
        raise httpx.ConnectError("no route")

    source, _ = make_source(boom)
    assert code_of(lambda: source.fetch("SalesDash", tmp_path)) == "DOWNLOAD_FAILED"

    def slow(request):
        raise httpx.ReadTimeout("slow")

    source, _ = make_source(slow)
    with pytest.raises(AppError) as err:
        source.fetch("SalesDash", tmp_path)
    assert err.value.status_code == 504


def test_download_url_template_is_configurable(tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=SAMPLE_TWB)

    source, _ = make_source(handler, public_download_url_template="https://public.tableau.com/dl/{name}/get.{ext}")
    source.fetch("SalesDash", tmp_path)
    assert seen == ["https://public.tableau.com/dl/SalesDash/get.twbx"]


def test_bad_url_template_is_a_config_error(tmp_path):
    source, _ = make_source(lambda r: httpx.Response(200), public_download_url_template="https://public.tableau.com/{oops}")
    assert code_of(lambda: source.fetch("SalesDash", tmp_path)) == "CONFIG_ERROR"


# --------------------------------------------------------------------------
# listing
# --------------------------------------------------------------------------
def listing(n, start=0):
    return [{"workbookRepoUrl": f"Book{i}", "title": f"Book number {i}", "lastPublishDate": 1_700_000_000_000} for i in range(start, start + n)]


def test_list_workbooks_parses_response_and_filters(tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"contents": listing(3)})

    source, _ = make_source(handler)
    refs = source.list_workbooks(profile="some.person")
    assert [r.id for r in refs] == ["Book0", "Book1", "Book2"]
    assert refs[0].name == "Book number 0" and refs[0].updated_at == "2023-11-14"
    assert seen == ["https://public.tableau.com/profile/api/some.person/workbooks?count=100&index=0"]
    assert [r.id for r in source.list_workbooks("number 2", profile="some.person")] == ["Book2"]


def test_list_workbooks_paginates_until_a_short_page():
    pages = {0: listing(100), 100: listing(100, 100), 200: listing(5, 200)}

    def handler(request):
        index = int(request.url.params["index"])
        return httpx.Response(200, json={"contents": pages[index]})

    source, _ = make_source(handler)
    assert len(source.list_workbooks(profile="p")) == 205


def test_list_workbooks_respects_limit():
    source, _ = make_source(lambda r: httpx.Response(200, json={"contents": listing(100)}))
    assert len(source.list_workbooks(profile="p", limit=30)) == 30


def test_list_workbooks_accepts_alternative_shapes_and_skips_junk():
    body = [{"workbookRepoUrl": "Good", "title": "Good one"}, {"title": "no id"}, "garbage", {"workbookRepoUrl": "../bad"}]
    source, _ = make_source(lambda r: httpx.Response(200, json=body))
    assert [r.id for r in source.list_workbooks(profile="p")] == ["Good"]


def test_list_workbooks_needs_a_valid_profile():
    source, _ = make_source(lambda r: httpx.Response(200, json=[]))
    assert code_of(lambda: source.list_workbooks()) == "INVALID_INPUT"
    assert code_of(lambda: source.list_workbooks(profile="../x")) == "INVALID_INPUT"
    assert [r for r in make_source(lambda r: httpx.Response(200, json=[]), public_default_profile="dflt")[0].list_workbooks()] == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(404),
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, json={"unexpected": "shape"}),
    ],
)
def test_list_workbooks_failures_explain_the_fallback(response):
    source, _ = make_source(lambda r: response)
    with pytest.raises(AppError) as err:
        source.list_workbooks(profile="p")
    assert err.value.code == "LIST_FAILED"
    assert "Upload" in err.value.message


def test_list_does_not_follow_redirects_off_the_allowlist():
    source, _ = make_source(lambda r: httpx.Response(302, headers={"location": "https://evil.example.com/"}))
    assert code_of(lambda: source.list_workbooks(profile="p")) == "REDIRECT_BLOCKED"


# --------------------------------------------------------------------------
# listing falls back to the second endpoint when the first one is gone (HTTP 404)
# --------------------------------------------------------------------------
def test_listing_falls_back_when_the_first_endpoint_returns_404():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.startswith("/profile/api/"):
            return httpx.Response(404)
        assert request.url.params["profileName"] == "some.person"
        return httpx.Response(200, json={"contents": [{"workbookRepoUrl": "Book1", "title": "Book One"}]})

    source, _ = make_source(handler)
    assert [r.id for r in source.list_workbooks(profile="some.person")] == ["Book1"]
    assert seen[0].startswith("/profile/api/") and seen[1] == "/public/apis/workbooks"


def test_listing_reports_every_endpoint_that_failed():
    source, _ = make_source(lambda r: httpx.Response(404))
    with pytest.raises(AppError) as err:
        source.list_workbooks(profile="some.person")
    assert err.value.code == "LIST_FAILED"
    assert "/profile/api/" in err.value.message and "/public/apis/workbooks" in err.value.message


def test_listing_does_not_fall_back_when_the_first_endpoint_works():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=[{"workbookRepoUrl": "A", "title": "A"}])

    source, _ = make_source(handler)
    assert len(source.list_workbooks(profile="p")) == 1
    assert len(calls) == 1
