from __future__ import annotations

import os
import stat

import pytest

from app.errors import AppError
from app.store import SessionStore


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    s = SessionStore(ttl_seconds=60, max_sessions=3, root=tmp_path, clock=clock)
    yield s
    s.close()


def test_create_get_delete(store):
    s = store.create("upload", "a.twbx")
    assert s.dir.is_dir() and store.get(s.id) is s
    (s.dir / "data.bin").write_bytes(b"x")
    assert store.delete(s.id) is True
    assert not s.dir.exists()  # files are removed with the session
    assert store.delete(s.id) is False  # idempotent


def test_session_directory_is_private(store):
    s = store.create("upload", "a.twbx")
    if os.name == "posix":
        assert stat.S_IMODE(s.dir.stat().st_mode) == 0o700


def test_unknown_session_is_a_clear_404(store):
    with pytest.raises(AppError) as err:
        store.get("nope")
    assert (err.value.code, err.value.status_code) == ("SESSION_EXPIRED", 404)


def test_sessions_expire_after_the_ttl_and_their_files_are_removed(store, clock):
    s = store.create("upload", "a.twbx")
    clock.now += 59
    assert store.get(s.id) is s
    clock.now += 2
    with pytest.raises(AppError):
        store.get(s.id)
    assert not s.dir.exists()


def test_expiry_also_happens_on_create(store, clock):
    old = store.create("upload", "old.twbx")
    clock.now += 120
    store.create("upload", "new.twbx")
    assert not old.dir.exists() and len(store) == 1


def test_oldest_session_is_evicted_at_capacity(store, clock):
    made = []
    for i in range(4):
        made.append(store.create("upload", f"{i}.twbx"))
        clock.now += 1
    assert len(store) == 3
    assert not made[0].dir.exists() and all(m.dir.exists() for m in made[1:])


def test_close_removes_everything(tmp_path, clock):
    s = SessionStore(60, 5, root=tmp_path, clock=clock)
    dirs = [s.create("upload", "x").dir for _ in range(3)]
    s.close()
    assert not any(d.exists() for d in dirs) and len(s) == 0
