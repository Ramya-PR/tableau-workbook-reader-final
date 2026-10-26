"""Migration-effort hints for a parsed workbook.

Rule-based and deliberately simple: each rule looks for a Tableau construct
that has no direct equivalent (or needs rewriting) in Power BI or Looker.
The result is a prompt for a human review, not an automatic conversion.
"""

from __future__ import annotations

import re
from typing import Any

LEVELS = ("low", "medium", "high")
_RANK = {lvl: i for i, lvl in enumerate(LEVELS)}

# (flag id, level, human reason, regex applied to the cleaned formula)
_FORMULA_RULES: list[tuple[str, str, str, re.Pattern[str]]] = [
    ("lod", "high", "Level of detail expression ({FIXED}, {INCLUDE}, {EXCLUDE} or {SUM(...)}). Needs a rewrite as DAX CALCULATE or a Looker derived table.",
     re.compile(r"\{[^{}]*\}")),
    ("table_calc", "high", "Table calculation. Depends on the layout of the view, so it has no one-to-one equivalent.",
     re.compile(r"\b(?:WINDOW_\w+|RUNNING_\w+|LOOKUP|INDEX|FIRST|LAST|SIZE|PREVIOUS_VALUE|RANK(?:_\w+)?|TOTAL)\s*\(", re.I)),
    ("external_script", "high", "Calls an external R or Python service (SCRIPT_*).",
     re.compile(r"\bSCRIPT_(?:REAL|INT|BOOL|STR)\s*\(", re.I)),
    ("rawsql", "high", "Passes raw SQL to the database (RAWSQL_*).",
     re.compile(r"\bRAWSQL\w*\s*\(", re.I)),
    ("row_level_security", "high", "User-based security (USERNAME, ISMEMBEROF and similar). Must be redesigned as row-level security in the target tool.",
     re.compile(r"\b(?:USERNAME|FULLNAME|USERDOMAIN|USER|ISMEMBEROF|USERATTRIBUTE\w*)\s*\(", re.I)),
    ("spatial", "medium", "Spatial function. Map support differs between tools.",
     re.compile(r"\b(?:MAKEPOINT|MAKELINE|BUFFER|DISTANCE|AREA|INTERSECTS|DIFFERENCE|SHAPETYPE)\s*\(", re.I)),
    ("attr", "medium", "Uses ATTR(), which has no equivalent. Usually replaced by MIN/MAX or a lookup.",
     re.compile(r"\bATTR\s*\(", re.I)),
    ("regex", "medium", "Regular expression function. Syntax and support vary by target tool.",
     re.compile(r"\bREGEXP_\w+\s*\(", re.I)),
    ("date_functions", "low", "Date function (DATETRUNC, DATEDIFF, DATEPART). Names and argument order differ in the target tool.",
     re.compile(r"\b(?:DATETRUNC|DATEDIFF|DATEPART|DATENAME|DATEADD|MAKEDATE|MAKEDATETIME)\s*\(", re.I)),
    ("aggregation", "low", "Aggregated calculation. Check that it behaves the same at the new grain.",
     re.compile(r"\b(?:SUM|AVG|MIN|MAX|COUNTD?|MEDIAN|STDEV\w*|VAR\w*|PERCENTILE)\s*\(", re.I)),
]

_STRING = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"//[^\n]*")
_FIELD_REF = re.compile(r"\[[^\]]*\]")


def clean_formula(formula: str) -> str:
    """Remove comments, string literals and [field names] so keywords inside them don't match."""
    text = _BLOCK_COMMENT.sub(" ", formula)
    text = _STRING.sub('""', text)
    text = _LINE_COMMENT.sub(" ", text)  # after strings, so "//" inside a URL string survives
    return _FIELD_REF.sub("[]", text)


def assess_formula(formula: str | None) -> list[dict[str, str]]:
    """Return the flags raised by one formula, most severe first."""
    if not formula or not formula.strip():
        return []
    text = clean_formula(formula)
    flags = []
    for flag_id, level, reason, pattern in _FORMULA_RULES:
        if pattern.search(text):
            flags.append({"id": flag_id, "level": level, "reason": reason})
    return sorted(flags, key=lambda f: -_RANK[f["level"]])


def _worst(flags: list[dict[str, str]]) -> str | None:
    return max((f["level"] for f in flags), key=_RANK.__getitem__, default=None)


def _datasource_flags(ds: dict[str, Any]) -> list[dict[str, str]]:
    flags = []
    if ds.get("custom_sql"):
        flags.append({"id": "custom_sql", "level": "high",
                      "reason": "Custom SQL. Must be recreated as a view, query or derived table in the target tool."})
    if ds.get("mode") == "published":
        flags.append({"id": "published_datasource", "level": "medium",
                      "reason": "Published datasource. Its full definition lives on the server and must be inventoried separately."})
    if ds.get("mode") == "extract":
        flags.append({"id": "extract", "level": "low",
                      "reason": "Uses a Tableau extract. Decide between import and live query in the target tool."})
    if ds.get("joins") or ds.get("unions") or ds.get("join_details"):
        flags.append({"id": "joins", "level": "low",
                      "reason": "Joins or unions are defined in Tableau's data model and must be rebuilt."})
    return flags


def assess(metadata: dict[str, Any]) -> dict[str, Any]:
    """Annotate fields in place with ``migration_level``/``migration_flags`` and return a summary."""
    items: list[dict[str, Any]] = []
    counts = {"low": 0, "medium": 0, "high": 0}

    for fld in metadata.get("fields", []):
        flags: list[dict[str, str]] = []
        if fld["kind"] == "calculated":
            flags = assess_formula(fld.get("formula_resolved") or fld.get("formula"))
            if not flags:
                flags = [{"id": "simple_calculation", "level": "low", "reason": "Calculated field. Rewrite in the target tool's formula language."}]
        elif fld["kind"] == "parameter":
            flags = [{"id": "parameter", "level": "medium", "reason": "Parameter. Becomes a what-if parameter (Power BI) or a filter or parameter (Looker)."}]
        elif fld["kind"] in ("set", "group", "bin"):
            flags = [{"id": fld["kind"], "level": "low", "reason": f"Tableau {fld['kind']}. Rebuild as a calculated column, grouping or bin in the target tool."}]
        level = _worst(flags)
        fld["migration_level"] = level
        fld["migration_flags"] = [f["id"] for f in flags]
        if level:
            counts[level] += 1
            items.append({"scope": "field", "datasource": fld["datasource"], "name": fld["name"], "level": level,
                          "flags": [f["id"] for f in flags], "reasons": [f["reason"] for f in flags]})

    for ds in metadata.get("datasources", []):
        flags = _datasource_flags(ds)
        level = _worst(flags)
        ds["migration_level"] = level
        ds["migration_flags"] = [f["id"] for f in flags]
        if level:
            counts[level] += 1
            items.append({"scope": "datasource", "datasource": ds.get("caption"), "name": ds.get("caption"), "level": level,
                          "flags": [f["id"] for f in flags], "reasons": [f["reason"] for f in flags]})

    # Quick table calculations live on the worksheet shelf, so no field formula shows them.
    seen: set[tuple[str, str]] = set()
    for use in metadata.get("worksheet_fields", []):
        calcs = use.get("quick_table_calcs") or []
        if not calcs or (use["worksheet"], use["field"]) in seen:
            continue
        seen.add((use["worksheet"], use["field"]))
        counts["high"] += 1
        items.append({"scope": "worksheet field", "datasource": use["datasource"], "name": f"{use['worksheet']}: {use['field']}",
                      "level": "high", "flags": ["quick_table_calc"],
                      "reasons": [f"Quick table calculation ({', '.join(calcs)}) set on the worksheet. "
                                  "It depends on the layout of the view, so it must be rebuilt as a measure."]})

    items.sort(key=lambda i: (-_RANK[i["level"]], i["scope"], i["name"] or ""))
    # Overall level, by a deliberately simple rule (no weights): any 3 high items, or a mix of
    # many medium ones, means a heavy migration; a single high item means a moderate one.
    if counts["high"] >= 3:
        overall = "high"
    elif counts["high"] >= 1 or counts["medium"] >= 5:
        overall = "medium"
    else:
        overall = "low"
    return {"overall": overall, "counts": counts, "items": items}
