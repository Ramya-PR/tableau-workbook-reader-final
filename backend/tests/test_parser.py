from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.archive import WorkbookArchive
from app.config import Settings
from app.errors import AppError
from app.parser import parse_workbook

from .builders import SAMPLE_TWB, SECRET_PASSWORD, SECRET_USER

MEMBERS = ["Data/Extracts/sales.hyper", "Data/orders.csv"]


@pytest.fixture(scope="module")
def meta() -> dict:
    return parse_workbook(SAMPLE_TWB, MEMBERS)


def field(meta, name, datasource=None):
    matches = [
        f for f in meta["fields"] if f["name"] == name and (datasource is None or f["datasource"] == datasource)
    ]
    assert len(matches) == 1, f"expected exactly one field {name!r}, got {len(matches)}"
    return matches[0]


def ds(meta, caption):
    return next(d for d in meta["datasources"] if d["caption"] == caption)


# --------------------------------------------------------------------------
def test_workbook_info_and_summary(meta):
    assert meta["workbook"]["version"] == "18.1"
    assert meta["workbook"]["source_platform"] == "win"
    assert meta["summary"] == {
        "datasources": 3,  # Parameters is not counted as a datasource
        "worksheets": 2,
        "dashboards": 1,
        "fields": 13,
        "calculated_fields": 2,
        "parameters": 1,
        "actions": 1,
        "extract_datasources": 1,
        "live_datasources": 1,
        "published_datasources": 1,
    }


def test_datasource_modes(meta):
    assert ds(meta, "Parameters")["mode"] == "parameters"
    assert ds(meta, "Sample Sales")["mode"] == "extract"
    assert ds(meta, "Shared Customers")["mode"] == "published"
    assert ds(meta, "Orders DB")["mode"] == "live"


def test_extract_is_linked_to_archive_member_even_with_absolute_path(meta):
    sales = ds(meta, "Sample Sales")
    assert sales["has_extract"] is True
    assert sales["data_file"] == "Data/Extracts/sales.hyper"
    assert sales["extract"]["schema"] == "Extract"


def test_published_datasource_details(meta):
    pub = ds(meta, "Shared Customers")
    assert pub["published_to"] == "/t/mysite/datasources"
    assert pub["server"] == "tableau.example.com"


def test_custom_sql_and_connection_details(meta):
    live = ds(meta, "Orders DB")
    assert live["connection_classes"] == ["postgres"]
    assert live["server"] == "db.example.com"
    assert live["custom_sql"] == ["SELECT * FROM orders WHERE region = 'W'"]


def test_credentials_never_appear_in_output(meta):
    blob = json.dumps(meta)
    assert SECRET_USER not in blob
    assert SECRET_PASSWORD not in blob


def test_calculated_field_formula_references_are_resolved_to_captions(meta):
    profit = field(meta, "Profit")
    assert profit["kind"] == "calculated"
    assert profit["internal_name"] == "Calculation_1001"
    assert profit["formula"] == "[Sales] * 0.2"

    vs_target = field(meta, "Profit vs Target")
    assert vs_target["formula"] == "[Calculation_1001] - [Parameters].[Parameter 1]"
    assert vs_target["formula_resolved"] == "[Profit] - [Parameters].[Top N]"


def test_parameter_fields(meta):
    top_n = field(meta, "Top N")
    assert top_n["kind"] == "parameter"
    assert top_n["value"] == "10"
    assert top_n["formula"] is None


def test_groups_sets_bins_and_prefixed_column_elements_are_found(meta):
    assert field(meta, "Region Group")["kind"] == "group"  # <_.fcp...column> + categorical-bin
    assert field(meta, "Sales (bin)")["kind"] == "bin"
    assert field(meta, "West Set")["kind"] == "set"


def test_physical_fields_roles_and_hidden(meta):
    notes = field(meta, "Notes")
    assert notes["hidden"] is True
    assert notes["role"] == "dimension"
    assert notes["table"] == "Extract"  # merged from metadata-records

    qty = field(meta, "Qty")
    assert (qty["datatype"], qty["role"], qty["role_inferred"]) == ("integer", "measure", True)
    order_date = field(meta, "Order Date")
    assert (order_date["datatype"], order_date["role"]) == ("date", "dimension")


def test_fields_know_which_worksheets_use_them(meta):
    assert field(meta, "Sales")["worksheets"] == ["Sales by Region"]
    assert field(meta, "Region")["worksheets"] == ["Sales by Region", "Detail"]
    assert field(meta, "Notes")["worksheets"] == []


def test_worksheet_summary(meta):
    ws = next(w for w in meta["worksheets"] if w["name"] == "Sales by Region")
    assert ws["datasources"] == ["Sample Sales", "Parameters"]
    assert ws["marks"] == ["Bar"]
    assert ws["rows"] == "sum(Sales)"
    assert ws["cols"] == "Region"
    assert ws["filters"] == ["Region (categorical)"]
    assert ws["dashboards"] == ["Overview"]
    assert ws["calculated_fields_used"] == 1
    assert ws["parameters_used"] == 1

    detail = next(w for w in meta["worksheets"] if w["name"] == "Detail")
    assert detail["dashboards"] == []
    assert detail["marks"] == ["Text"]


def test_worksheet_field_usage_rows_include_derivations(meta):
    rows = [r for r in meta["worksheet_fields"] if r["worksheet"] == "Sales by Region"]
    sales = next(r for r in rows if r["field"] == "Sales")
    assert sales["derivations"] == ["Sum", "Avg"]
    assert sales["datasource"] == "Sample Sales"
    profit = next(r for r in rows if r["field"] == "Profit")
    assert profit["kind"] == "calculated"
    assert profit["formula"] == "[Sales] * 0.2"
    assert any(r["kind"] == "parameter" and r["field"] == "Top N" for r in rows)


def test_dashboards(meta):
    dash = meta["dashboards"][0]
    assert dash["name"] == "Overview"
    assert dash["worksheets"] == ["Sales by Region"]  # de-duplicated; filter zone shares the name
    assert dash["objects"] == {"filter": 1, "text": 1}  # layout-* zones excluded


def test_warnings_flag_published_and_live_sources(meta):
    text = " ".join(meta["warnings"])
    assert "Shared Customers" in text and "published" in text
    assert "Orders DB" in text and "live connection" in text


# --------------------------------------------------------------------------
# robustness / safety
# --------------------------------------------------------------------------
def test_empty_workbook_parses_to_empty_result():
    out = parse_workbook(b"<?xml version='1.0'?><workbook version='10.0'></workbook>")
    assert out["summary"]["datasources"] == 0
    assert out["fields"] == [] and out["worksheets"] == []
    assert any("No datasources" in w for w in out["warnings"])


def test_missing_attributes_do_not_crash():
    xml = b"""<workbook><datasources><datasource><column/><column name='[x]'/></datasource></datasources>
              <worksheets><worksheet><table><view><datasource-dependencies/></view></table></worksheet></worksheets>
              <dashboards><dashboard/></dashboards></workbook>"""
    out = parse_workbook(xml)
    assert out["summary"]["worksheets"] == 1


def test_rejects_entity_declarations_before_parsing():
    xxe = (
        b'<?xml version="1.0"?><!DOCTYPE w [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b"<workbook><datasources><datasource caption='&xxe;' name='a'/></datasources></workbook>"
    )
    with pytest.raises(AppError) as err:
        parse_workbook(xxe)
    assert err.value.code == "PARSE_ERROR"


def test_billion_laughs_is_rejected():
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        b"<workbook>&lol2;</workbook>"
    )
    with pytest.raises(AppError) as err:
        parse_workbook(bomb)
    assert err.value.code == "PARSE_ERROR"


def test_malformed_xml_is_a_clean_error():
    with pytest.raises(AppError) as err:
        parse_workbook(b"<workbook><datasources></workbook>")
    assert err.value.code == "PARSE_ERROR"


def test_tds_is_identified_as_a_datasource_not_a_workbook():
    with pytest.raises(AppError) as err:
        parse_workbook(b"<datasource name='x'/>")
    assert err.value.code == "NOT_A_WORKBOOK"
    assert ".tds" in err.value.message


def test_other_xml_is_not_a_workbook():
    with pytest.raises(AppError) as err:
        parse_workbook(b"<html/>")
    assert err.value.code == "NOT_A_WORKBOOK"


# --------------------------------------------------------------------------
# Optional: run the parser over real workbooks.
#   TWB_SAMPLES_DIR=/path/to/dir pytest tests/test_parser.py -k real
# e.g. the test assets in https://github.com/tableau/document-api-python
# --------------------------------------------------------------------------
_samples = os.environ.get("TWB_SAMPLES_DIR")


@pytest.mark.skipif(not _samples, reason="set TWB_SAMPLES_DIR to run against real workbooks")
def test_real_workbooks_parse_without_crashing():
    files = sorted(p for p in Path(_samples).rglob("*") if p.suffix.lower() in {".twb", ".twbx"})
    assert files, "no .twb/.twbx files found in TWB_SAMPLES_DIR"
    for path in files:
        try:
            archive = WorkbookArchive(path, Settings())
            parse_workbook(archive.twb_bytes(), [d.path for d in archive.data_files])
        except AppError as exc:
            # Clean, user-facing refusals are fine (e.g. a deliberately corrupt sample);
            # anything else would surface as a test error.
            assert exc.code in {"NO_WORKBOOK_XML", "BAD_ARCHIVE", "NOT_A_WORKBOOK", "PARSE_ERROR"}, (path, exc)
