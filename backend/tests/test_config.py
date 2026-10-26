from __future__ import annotations

import pytest

from app.config import MB, Settings, load_settings


def test_defaults_when_environment_is_empty():
    s = load_settings({})
    assert s == Settings()
    assert s.max_file_bytes == 500 * MB and s.session_ttl_seconds == 30 * 60
    assert s.server_configured is False
    assert s.public_allowed_hosts == ("public.tableau.com",)


def test_environment_overrides():
    s = load_settings(
        {
            "MAX_FILE_MB": "10",
            "MAX_PREVIEW_ROWS": "50",
            "HTTP_TIMEOUT_SECONDS": "2.5",
            "PUBLIC_ALLOWED_HOSTS": " Public.Tableau.com , cdn.example.net ",
            "CORS_ORIGINS": "http://localhost:3000",
            "TABLEAU_PUBLIC_PROFILE": " some.person ",
        }
    )
    assert (s.max_file_mb, s.max_preview_rows, s.http_timeout_seconds) == (10, 50, 2.5)
    assert s.public_allowed_hosts == ("public.tableau.com", "cdn.example.net")  # lower-cased and trimmed
    assert s.cors_origins == ("http://localhost:3000",)
    assert s.public_default_profile == "some.person"


def test_server_credentials_and_url_normalisation():
    env = {
        "TABLEAU_SERVER_URL": "https://tableau.example.com/ ",
        "TABLEAU_SITE": "mysite",
        "TABLEAU_PAT_NAME": "tok",
        "TABLEAU_PAT_SECRET": "sec",
    }
    s = load_settings(env)
    assert s.server_url == "https://tableau.example.com"  # trailing slash removed
    assert s.server_configured is True
    for missing in ("TABLEAU_SERVER_URL", "TABLEAU_PAT_NAME", "TABLEAU_PAT_SECRET"):
        assert load_settings({k: v for k, v in env.items() if k != missing}).server_configured is False
    # an empty site is valid: it means the Default site
    assert load_settings({**env, "TABLEAU_SITE": ""}).server_configured is True


@pytest.mark.parametrize(
    "env",
    [{"MAX_FILE_MB": "abc"}, {"MAX_FILE_MB": "0"}, {"SESSION_TTL_MINUTES": "-5"}, {"HTTP_TIMEOUT_SECONDS": "fast"}],
)
def test_invalid_numbers_fail_loudly_with_the_variable_name(env):
    with pytest.raises(ValueError) as err:
        load_settings(env)
    assert next(iter(env)) in str(err.value)


def test_url_templates_can_be_overridden():
    s = load_settings({"PUBLIC_DOWNLOAD_URL_TEMPLATE": "https://public.tableau.com/x/{name}.{ext}"})
    assert s.public_download_url_template == "https://public.tableau.com/x/{name}.{ext}"
    assert "{profile}" in s.public_list_url_template  # default kept
