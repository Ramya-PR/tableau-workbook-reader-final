"""Tests for the migration-effort rules."""

from __future__ import annotations

import pytest

from app.migration import assess, assess_formula, clean_formula
from app.parser import parse_workbook

from .builders import SAMPLE_TWB


def ids(formula):
    return [f["id"] for f in assess_formula(formula)]


@pytest.mark.parametrize(
    "formula, expected",
    [
        ("{FIXED [Region] : SUM([Sales])}", "lod"),
        ("{ INCLUDE [Customer] : AVG([Sales]) }", "lod"),
        ("{exclude [Region] : SUM([Sales])}", "lod"),
        ("{SUM([Sales])}", "lod"),
        ("WINDOW_SUM(SUM([Sales]))", "table_calc"),
        ("RUNNING_SUM(SUM([Sales]))", "table_calc"),
        ("LOOKUP(SUM([Sales]), -1)", "table_calc"),
        ("INDEX() < 5", "table_calc"),
        ("SCRIPT_REAL('x', SUM([Sales]))", "external_script"),
        ("RAWSQL_INT('select 1')", "rawsql"),
        ("USERNAME() = [Owner]", "row_level_security"),
        ("ISMEMBEROF('Admins')", "row_level_security"),
        ("MAKEPOINT([Lat],[Lon])", "spatial"),
        ("ATTR([Region])", "attr"),
        ("REGEXP_EXTRACT([Name], '(\\d+)')", "regex"),
        ("DATETRUNC('month', [Order Date])", "date_functions"),
        ("SUM([Sales])", "aggregation"),
    ],
)
def test_each_rule_fires(formula, expected):
    assert expected in ids(formula)


@pytest.mark.parametrize(
    "formula",
    [
        "[Sales] * 0.2",
        "IF [Region] = 'East' THEN 1 ELSE 0 END",
        "'{FIXED [x] : SUM([y])}'",  # inside a string literal
        "// {FIXED [x] : SUM([y])}\n[Sales]",  # inside a comment
        "/* WINDOW_SUM(x) */ [Sales]",
        "[WINDOW_SUM] + 1",  # inside a field name
        "[Total Sales] + [Index]",  # field names that look like functions
        "",
        None,
    ],
)
def test_no_false_positives(formula):
    flags = ids(formula)
    assert not set(flags) & {"lod", "table_calc", "external_script", "rawsql", "row_level_security"}


def test_url_in_string_is_not_a_comment():
    # "//" inside a string must not hide the code after it
    assert "table_calc" in ids("IF CONTAINS([u], 'http://x') THEN WINDOW_SUM(SUM([s])) END")


def test_flags_sorted_most_severe_first():
    levels = [f["level"] for f in assess_formula("{FIXED [a] : SUM([b])} + DATETRUNC('day', [d])")]
    assert levels == sorted(levels, key={"high": 0, "medium": 1, "low": 2}.get)


def test_clean_formula_removes_strings_and_fields():
    assert "WINDOW" not in clean_formula("'WINDOW_SUM' + [WINDOW_X] // WINDOW_Y")


def test_assess_sample_workbook():
    meta = parse_workbook(SAMPLE_TWB, [])
    meta.pop("warnings")
    result = assess(meta)

    by_name = {(i["scope"], i["name"]): i for i in result["items"]}
    assert by_name[("field", "Top N")]["flags"] == ["parameter"]
    assert by_name[("field", "Profit")]["level"] == "low"
    assert by_name[("datasource", "Orders DB")]["flags"] == ["custom_sql"]
    assert by_name[("datasource", "Orders DB")]["level"] == "high"
    assert by_name[("datasource", "Shared Customers")]["flags"] == ["published_datasource"]
    # items are sorted high first
    levels = [i["level"] for i in result["items"]]
    assert levels == sorted(levels, key={"high": 0, "medium": 1, "low": 2}.get)
    assert sum(result["counts"].values()) == len(result["items"])
    # fields are annotated in place; plain source columns carry no level
    region = next(f for f in meta["fields"] if f["name"] == "Region")
    assert region["migration_level"] is None and region["migration_flags"] == []


def test_empty_workbook_scores_low():
    result = assess({"fields": [], "datasources": []})
    assert result == {"overall": "low", "counts": {"low": 0, "medium": 0, "high": 0}, "items": []}


def test_many_high_items_raise_overall():
    fields = [{"kind": "calculated", "name": f"c{i}", "datasource": "d", "formula_resolved": "{FIXED [a] : SUM([b])}"} for i in range(5)]
    assert assess({"fields": fields, "datasources": []})["overall"] == "high"


def test_overall_rule_is_simple_and_documented():
    def field(i, formula):
        return {"kind": "calculated", "name": f"c{i}", "datasource": "d", "formula_resolved": formula}

    lod = "{FIXED [a] : SUM([b])}"
    assert assess({"fields": [field(0, "[x] * 2")], "datasources": []})["overall"] == "low"
    assert assess({"fields": [field(0, lod)], "datasources": []})["overall"] == "medium"
    assert assess({"fields": [field(i, lod) for i in range(3)], "datasources": []})["overall"] == "high"
    params = [{"kind": "parameter", "name": f"p{i}", "datasource": "Parameters"} for i in range(5)]
    assert assess({"fields": params, "datasources": []})["overall"] == "medium"
    assert "score" not in assess({"fields": [], "datasources": []})
