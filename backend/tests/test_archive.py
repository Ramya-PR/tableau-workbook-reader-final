from __future__ import annotations

import os
import stat
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from app.archive import WorkbookArchive, copy_limited, is_workbook_file, sniff_kind
from app.config import Settings
from app.errors import AppError

from .builders import SAMPLE_TWB, make_twbx


def write_zip(path: Path, entries: list[tuple[str, bytes]], symlink: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.create_system = 3  # unix
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "/etc/passwd")
    return path


def code_of(fn) -> str:
    with pytest.raises(AppError) as err:
        fn()
    return err.value.code


# --------------------------------------------------------------------------
def test_twbx_is_opened_and_data_files_are_classified(sample_twbx, settings):
    arc = WorkbookArchive(sample_twbx, settings)
    assert arc.kind == "twbx"
    assert arc.twb_bytes() == SAMPLE_TWB
    listed = {d.path: d.kind for d in arc.data_files}
    # images and Tableau's result cache are not "data"
    assert listed == {
        "Data/Extracts/sales.hyper": "hyper",
        "Data/old.tde": "tde",
        "Data/orders.csv": "csv",
    }


def test_bare_twb_has_no_data_files(tmp_path, settings):
    p = tmp_path / "w.twb"
    p.write_bytes(SAMPLE_TWB)
    arc = WorkbookArchive(p, settings)
    assert arc.kind == "twb" and arc.data_files == []
    assert arc.twb_bytes() == SAMPLE_TWB


def test_sniffing_goes_by_content_not_extension(tmp_path):
    p = tmp_path / "actually_xml.twbx"
    p.write_bytes(b"\xef\xbb\xbf  <?xml version='1.0'?><workbook/>")
    assert sniff_kind(p) == "twb"
    q = tmp_path / "junk.twb"
    q.write_bytes(b"\x00\x01\x02 not a workbook")
    assert code_of(lambda: sniff_kind(q)) == "UNSUPPORTED_FILE"


def test_is_workbook_file_rejects_html_pages(tmp_path):
    html = tmp_path / "login.twbx"
    html.write_bytes(b"<!DOCTYPE html><html><body>Please sign in</body></html>")
    assert not is_workbook_file(html)
    xml = tmp_path / "ok.twb"
    xml.write_bytes(SAMPLE_TWB)
    assert is_workbook_file(xml)


def test_corrupt_zip_is_a_clean_error(tmp_path, settings):
    p = tmp_path / "bad.twbx"
    p.write_bytes(b"PK\x03\x04" + b"garbage" * 20)
    assert code_of(lambda: WorkbookArchive(p, settings)) == "BAD_ARCHIVE"


def test_zip_without_a_twb(tmp_path, settings):
    p = write_zip(tmp_path / "x.zip", [("readme.txt", b"hi")])
    assert code_of(lambda: WorkbookArchive(p, settings)) == "NO_WORKBOOK_XML"


def test_shallowest_twb_wins_and_macosx_junk_is_ignored(tmp_path, settings):
    p = write_zip(
        tmp_path / "x.twbx",
        [
            ("__MACOSX/._Real.twb", b"junk"),
            ("old/Backup.twb", b"<workbook caption='nested'/>"),
            ("Real.twb", b"<workbook caption='root'/>"),
        ],
    )
    assert b"root" in WorkbookArchive(p, settings).twb_bytes()


# -- zip-slip and friends --------------------------------------------------
@pytest.mark.parametrize(
    "evil",
    ["../evil.txt", "a/../../evil.txt", "/etc/cron.d/evil", "..\\evil.txt", "C:\\Windows\\evil.txt", "C:/evil.txt"],
)
def test_unsafe_member_names_are_refused(tmp_path, settings, evil):
    p = write_zip(tmp_path / "x.twbx", [("W.twb", SAMPLE_TWB), (evil, b"boom")])
    assert code_of(lambda: WorkbookArchive(p, settings)) == "ARCHIVE_UNSAFE"


def test_symlink_members_are_refused(tmp_path, settings):
    p = write_zip(tmp_path / "x.twbx", [("W.twb", SAMPLE_TWB)], symlink="Data/link.csv")
    assert code_of(lambda: WorkbookArchive(p, settings)) == "ARCHIVE_UNSAFE"


def test_zip_bomb_by_compression_ratio_is_refused(tmp_path, settings):
    p = write_zip(tmp_path / "x.twbx", [("W.twb", SAMPLE_TWB), ("Data/bomb.csv", b"\0" * (8 * 1024 * 1024))])
    assert code_of(lambda: WorkbookArchive(p, settings)) == "ARCHIVE_UNSAFE"


def test_total_uncompressed_size_limit(tmp_path):
    s = replace(Settings(), max_uncompressed_mb=1)
    p = write_zip(tmp_path / "x.twbx", [("W.twb", SAMPLE_TWB), ("Data/big.csv", os.urandom(2 * 1024 * 1024))])
    assert code_of(lambda: WorkbookArchive(p, s)) == "ARCHIVE_UNSAFE"


def test_member_count_limit(tmp_path):
    s = replace(Settings(), max_archive_members=3)
    p = write_zip(tmp_path / "x.twbx", [("W.twb", SAMPLE_TWB)] + [(f"Data/{i}.csv", b"a") for i in range(5)])
    assert code_of(lambda: WorkbookArchive(p, s)) == "ARCHIVE_UNSAFE"


def test_workbook_xml_size_limit(tmp_path):
    s = replace(Settings(), max_twb_mb=1)
    p = tmp_path / "huge.twb"
    p.write_bytes(b"<workbook>" + b"a" * (1_500_000) + b"</workbook>")
    assert code_of(lambda: WorkbookArchive(p, s).twb_bytes()) == "TOO_LARGE"


# -- extraction ------------------------------------------------------------
def test_extract_only_listed_members_with_sanitised_names(tmp_path, settings):
    p = make_twbx(tmp_path / "x.twbx", files={"Data/Extracts/my data (1) ✓.hyper": b"abc"})
    arc = WorkbookArchive(p, settings)
    dest_dir = tmp_path / "out"
    out = arc.extract("Data/Extracts/my data (1) ✓.hyper", dest_dir)
    assert out.read_bytes() == b"abc"
    assert out.parent == dest_dir
    assert "/" not in out.name and "✓" not in out.name
    assert arc.extract("Data/Extracts/my data (1) ✓.hyper", dest_dir) == out  # cached

    assert code_of(lambda: arc.extract("Sales.twb", dest_dir)) == "UNKNOWN_DATA_FILE"
    assert code_of(lambda: arc.extract("../../etc/passwd", dest_dir)) == "UNKNOWN_DATA_FILE"
    assert [f.name for f in dest_dir.iterdir()] == [out.name]  # nothing else was written


def test_extract_enforces_size_limit_and_leaves_no_partial_file(tmp_path):
    p = make_twbx(tmp_path / "x.twbx", files={"Data/a.csv": b"x" * 10})
    arc = WorkbookArchive(p, Settings())
    # The zip header is honest here, so shrink the cap after opening to exercise the
    # streaming guard that protects against archives whose headers lie about size.
    arc.settings = replace(Settings(), max_uncompressed_mb=0)
    dest = tmp_path / "out"
    assert code_of(lambda: arc.extract("Data/a.csv", dest)) == "TOO_LARGE"
    assert list(dest.glob("*")) == []


def test_extract_from_bare_twb_is_an_error(tmp_path, settings):
    p = tmp_path / "w.twb"
    p.write_bytes(SAMPLE_TWB)
    assert code_of(lambda: WorkbookArchive(p, settings).extract("x.csv", tmp_path)) == "NO_DATA_FILES"


def test_copy_limited_cleans_up(tmp_path):
    import io

    dest = tmp_path / "f.bin"
    assert copy_limited(io.BytesIO(b"abc"), dest, 10) == 3
    big = tmp_path / "g.bin"
    assert code_of(lambda: copy_limited(io.BytesIO(b"x" * 100), big, 10)) == "TOO_LARGE"
    assert not big.exists()
