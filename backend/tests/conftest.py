from __future__ import annotations

import pytest

from app.config import Settings

from .builders import hyper_works, make_hyper, make_twbx

_HYPER_OK = None


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="session")
def hyper_ok() -> bool:
    global _HYPER_OK
    if _HYPER_OK is None:
        _HYPER_OK = hyper_works()
    return _HYPER_OK


@pytest.fixture
def need_hyper(hyper_ok):
    if not hyper_ok:
        pytest.skip("tableauhyperapi is not installed or hyperd cannot start on this machine")


@pytest.fixture
def hyper_file(tmp_path, need_hyper):
    return make_hyper(tmp_path / "sales.hyper")


@pytest.fixture
def sample_twbx(tmp_path):
    return make_twbx(
        tmp_path / "sample.twbx",
        files={
            "Data/Extracts/sales.hyper": b"not-a-real-hyper",
            "Data/orders.csv": b"id,name\n1,a\n2,b\n",
            "Image/logo.png": b"\x89PNG",
            "TwbxExternalCache/TwbxResultsCacheV3/1/.bin": b"x",
            "Data/old.tde": b"tde",
        },
    )
