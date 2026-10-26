from __future__ import annotations

import datetime as dt
import decimal
import json

import pytest

from app import preview
from app.errors import AppError


def code_of(fn) -> str:
    with pytest.raises(AppError) as err:
        fn()
    return err.value.code


# --------------------------------------------------------------------------
# json_safe
# --------------------------------------------------------------------------
def test_json_safe_converts_awkward_types():
    class HyperTimestamp:  # shaped like tableauhyperapi.Timestamp
        def to_datetime(self):
            return dt.datetime(2024, 1, 2, 3, 4, 5)

    class Unconvertible:
        def to_date(self):
            raise OverflowError("year out of range")

        def __str__(self):
            return "9999-99-99"

    values = [
        None,
        True,
        7,
        1.5,
        float("nan"),
        float("inf"),
        decimal.Decimal("12345678901234567890.123456789"),
        dt.date(2024, 1, 2),
        dt.datetime(2024, 1, 2, 3, 4, 5),
        dt.time(3, 4),
        b"\x00\x01",
        HyperTimestamp(),
        Unconvertible(),
        "text ü",
    ]
    out = [preview.json_safe(v) for v in values]
    assert out == [
        None,
        True,
        7,
        1.5,
        "nan",
        "inf",
        "12345678901234567890.123456789",  # Decimal keeps every digit
        "2024-01-02",
        "2024-01-02T03:04:05",
        "03:04:00",
        "<2 bytes>",
        "2024-01-02T03:04:05",
        "9999-99-99",
        "text ü",
    ]
    json.dumps(out, allow_nan=False)  # strictly valid JSON (no NaN/Infinity tokens)


def test_describe_file():
    assert preview.describe_file("csv") == (True, None)
    ok, reason = preview.describe_file("tde")
    assert ok is False and ".hyper" in reason
    assert preview.describe_file("excel", "Data/book.xlsx") == (True, None)
    ok, reason = preview.describe_file("excel", "Data/old.xls")
    assert ok is False and ".xlsx" in reason
    assert preview.describe_file("json")[0] is False


def test_hyper_unavailable_is_reported_not_crashed(monkeypatch, tmp_path):
    monkeypatch.setattr(preview, "hyper_available", lambda: False)
    ok, reason = preview.describe_file("hyper")
    assert ok is False and "pip install tableauhyperapi" in reason
    f = tmp_path / "x.hyper"
    f.write_bytes(b"x")
    with pytest.raises(AppError) as err:
        preview.list_tables(f, "hyper")
    assert (err.value.code, err.value.status_code) == ("PREVIEW_UNAVAILABLE", 501)


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------
def write(tmp_path, name, data: bytes):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_csv_tables_and_paging(tmp_path):
    p = write(tmp_path, "a.csv", b"id,name\n" + b"".join(f"{i},n{i}\n".encode() for i in range(25)))
    (table,) = preview.list_tables(p, "csv")
    assert table["name"] == "a.csv" and table["row_count"] == 25
    assert [c["name"] for c in table["columns"]] == ["id", "name"]

    first = preview.read_rows(p, "csv", None, 0, 10)
    assert first["rows"][0] == ["0", "n0"] and len(first["rows"]) == 10
    last = preview.read_rows(p, "csv", None, 20, 10)
    assert len(last["rows"]) == 5 and last["rows"][-1] == ["24", "n24"]
    assert (last["offset"], last["limit"]) == (20, 10)
    assert preview.read_rows(p, "csv", None, 500, 10)["rows"] == []


def test_csv_handles_bom_cp1252_and_other_delimiters(tmp_path):
    bom = write(tmp_path, "bom.csv", b"\xef\xbb\xbfregion;sales\nWest;1\n")
    out = preview.read_rows(bom, "csv", None, 0, 5)
    assert [c["name"] for c in out["columns"]] == ["region", "sales"]  # BOM stripped, ';' sniffed
    assert out["rows"] == [["West", "1"]]

    legacy = write(tmp_path, "legacy.csv", "city,note\nZürich,café\n".encode("cp1252"))
    assert preview.read_rows(legacy, "csv", None, 0, 5)["rows"] == [["Zürich", "café"]]

    tabs = write(tmp_path, "t.tsv", b"a\tb\n1\t2\n")
    assert preview.read_rows(tabs, "csv", None, 0, 5)["rows"] == [["1", "2"]]


def test_csv_ragged_rows_are_padded_and_truncated(tmp_path):
    p = write(tmp_path, "r.csv", b"a,b,c\n1,2\n1,2,3,4\n")
    assert preview.read_rows(p, "csv", None, 0, 5)["rows"] == [["1", "2", None], ["1", "2", "3"]]


def test_csv_blank_header_names_and_empty_files(tmp_path):
    p = write(tmp_path, "h.csv", b"a,,c\n1,2,3\n")
    assert [c["name"] for c in preview.list_tables(p, "csv")[0]["columns"]] == ["a", "column_2", "c"]
    empty = write(tmp_path, "e.csv", b"")
    assert preview.list_tables(empty, "csv")[0]["row_count"] == 0
    assert preview.read_rows(empty, "csv", None, 0, 5)["rows"] == []


def test_unsupported_kinds_are_refused(tmp_path):
    f = write(tmp_path, "a.tde", b"x")
    assert code_of(lambda: preview.list_tables(f, "tde")) == "PREVIEW_UNAVAILABLE"
    assert code_of(lambda: preview.read_rows(f, "tde", None, 0, 5)) == "PREVIEW_UNAVAILABLE"


# --------------------------------------------------------------------------
# Hyper (real extract, real hyperd)
# --------------------------------------------------------------------------
def test_hyper_tables(hyper_file):
    (table,) = preview.list_tables(hyper_file, "hyper")
    assert table["name"] == "Extract.Extract"
    assert table["row_count"] == 250
    assert [c["name"] for c in table["columns"]] == ["Region", "Sales", "Order Date", "Qty", "Price", "Shipped At"]
    assert [c["type"] for c in table["columns"]][:2] == ["TEXT", "DOUBLE"]


def test_hyper_rows_paging_and_value_types(hyper_file):
    page1 = preview.read_rows(hyper_file, "hyper", "Extract.Extract", 0, 100)
    page2 = preview.read_rows(hyper_file, "hyper", "Extract.Extract", 100, 100)
    page3 = preview.read_rows(hyper_file, "hyper", "Extract.Extract", 200, 100)
    assert [len(p["rows"]) for p in (page1, page2, page3)] == [100, 100, 50]

    all_rows = page1["rows"] + page2["rows"] + page3["rows"]
    qty = sorted(r[3] for r in all_rows)
    assert qty == list(range(250))  # every row exactly once, none duplicated across pages

    row = next(r for r in all_rows if r[3] == 7)
    assert row[0] == "North"  # 7 % 4 == 3 -> "North"
    assert row[1] == 10.5
    assert row[2] == "2024-01-08"  # DATE -> ISO string
    assert row[4] == "8.25"  # NUMERIC -> string, no precision loss
    assert row[5] == "2024-03-08T13:45:30"  # TIMESTAMP -> ISO string

    assert next(r for r in all_rows if r[3] == 3)[0] is None  # SQL NULL -> JSON null
    assert any(r[0] == "Zürich" for r in all_rows)  # unicode survives
    json.dumps(all_rows, allow_nan=False)  # everything is JSON-serialisable


def test_hyper_table_name_is_validated_against_the_catalog_never_interpolated(hyper_file):
    assert code_of(lambda: preview.read_rows(hyper_file, "hyper", "Extract.Nope", 0, 5)) == "UNKNOWN_TABLE"
    evil = 'Extract.Extract"; DROP TABLE "Extract"."Extract"; --'
    assert code_of(lambda: preview.read_rows(hyper_file, "hyper", evil, 0, 5)) == "UNKNOWN_TABLE"
    assert code_of(lambda: preview.read_rows(hyper_file, "hyper", None, 0, 5)) == "TABLE_REQUIRED"
    # ...and the data is still there
    assert preview.list_tables(hyper_file, "hyper")[0]["row_count"] == 250


def test_corrupt_hyper_file_is_a_clean_error(tmp_path, need_hyper):
    bad = tmp_path / "bad.hyper"
    bad.write_bytes(b"this is not a hyper database" * 100)
    assert code_of(lambda: preview.list_tables(bad, "hyper")) == "HYPER_READ_ERROR"


# --------------------------------------------------------------------------
# Excel
# --------------------------------------------------------------------------
def make_xlsx(path, sheets):
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    wb.save(path)
    return path


def test_excel_tables_one_per_sheet(tmp_path):
    p = make_xlsx(tmp_path / "book.xlsx", {
        "Orders": [["Region", "Sales"], ["East", 10.5], ["West", 20]],
        "Empty": [],
    })
    tables = {t["name"]: t for t in preview.list_tables(p, "excel")}
    assert tables["Orders"]["row_count"] == 2
    assert [c["name"] for c in tables["Orders"]["columns"]] == ["Region", "Sales"]
    assert tables["Empty"]["row_count"] == 0 and tables["Empty"]["columns"] == []


def test_excel_rows_paging_types_and_padding(tmp_path):
    p = make_xlsx(tmp_path / "book.xlsx", {
        "Orders": [["Day", "Qty", None]] + [[dt.datetime(2024, 1, i + 1), i, None if i % 2 else "x"] for i in range(5)],
    })
    page = preview.read_rows(p, "excel", "Orders", offset=1, limit=2)
    assert [c["name"] for c in page["columns"]] == ["Day", "Qty", "column_3"]
    assert page["rows"] == [["2024-01-02T00:00:00", 1, None], ["2024-01-03T00:00:00", 2, "x"]]
    assert page["offset"] == 1 and page["limit"] == 2
    json.dumps(page, allow_nan=False)
    assert preview.read_rows(p, "excel", "Orders", offset=99, limit=5)["rows"] == []


def test_excel_unknown_sheet_is_a_clean_error(tmp_path):
    p = make_xlsx(tmp_path / "book.xlsx", {"A": [["x"], [1]]})
    assert code_of(lambda: preview.read_rows(p, "excel", "Nope", 0, 5)) == "UNKNOWN_TABLE"


def test_excel_formulas_show_cached_values_not_formula_text(tmp_path):
    # openpyxl writes no cached value, so a formula cell reads as None rather than as "=1+1" text
    p = make_xlsx(tmp_path / "book.xlsx", {"A": [["x"], ["=1+1"]]})
    assert preview.read_rows(p, "excel", "A", 0, 5)["rows"] == [[None]]


def test_legacy_xls_and_corrupt_files_are_refused_cleanly(tmp_path):
    old = write(tmp_path, "old.xls", b"x")
    assert code_of(lambda: preview.list_tables(old, "excel")) == "PREVIEW_UNAVAILABLE"
    bad = write(tmp_path, "bad.xlsx", b"this is not a zip")
    assert code_of(lambda: preview.list_tables(bad, "excel")) == "EXCEL_READ_ERROR"


def test_excel_zip_bomb_is_refused(tmp_path):
    import zipfile

    p = tmp_path / "bomb.xlsx"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("xl/worksheets/sheet1.xml", b"0" * (50 * 1024 * 1024))  # ratio far above the cap
    assert code_of(lambda: preview.list_tables(p, "excel")) == "ARCHIVE_UNSAFE"
