"""Parse the XML inside a Tableau workbook (.twb) into plain JSON-able dicts.

Design notes
------------
* The XML is untrusted. We use a hardened lxml parser (no entity expansion, no
  network, no DTD loading) and additionally refuse anything containing an
  ``<!ENTITY`` declaration, which a genuine workbook never has.
* Tableau's XML varies a lot between versions. Everything is read defensively:
  a missing element or attribute never raises, it just yields ``None``/empty.
* Newer workbooks wrap some elements in names like
  ``_.fcp.ObjectModelTableType.true...column``. We therefore match elements by
  the part of the tag after the last ``...`` (see ``_local``).
* Calculated fields are stored under internal names (``[Calculation_123...]``)
  with the friendly name in ``caption``, and formulas reference the internal
  names. ``formula_resolved`` rewrites those references to captions.
* Connection credentials (username / password) are never read into the output.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from lxml import etree

from .errors import parse_error

# Connection classes that point at a file (which would be bundled in a .twbx).
FILE_CONNECTION_CLASSES = {
    "excel-direct",
    "excel",
    "textscan",
    "text",
    "csv",
    "ogrdirect",
    "hyper",
    "dataengine",
    "sqlproxy-file",
}
PARAMETERS_DATASOURCE = "Parameters"
MAX_CUSTOM_SQL_CHARS = 5000

_QUALIFIED_REF = re.compile(r"(?:\[([^\[\]]+)\]\.)?\[([^\[\]]+)\]")
_SHELF_REF = re.compile(r"\[([^\[\]]+)\]\.\[([^\[\]]+)\]")
_INSTANCE = re.compile(r"^(?P<prefix>[A-Za-z0-9_]+):(?P<name>.*):(?P<suffix>[A-Za-z0-9]+)$")


# --------------------------------------------------------------------------
# small XML helpers
# --------------------------------------------------------------------------
def _local(tag) -> str | None:
    """Element name without namespace or the ``prefix...`` wrapper (None for comments)."""
    if not isinstance(tag, str):
        return None
    return tag.rsplit("}", 1)[-1].rsplit("...", 1)[-1]


def _kids(el, name: str) -> list:
    if el is None:
        return []
    return [c for c in el if _local(c.tag) == name]


def _kid(el, name: str):
    kids = _kids(el, name)
    return kids[0] if kids else None


def _desc(el, name: str) -> list:
    if el is None:
        return []
    return [c for c in el.iter() if c is not el and _local(c.tag) == name]


def _strip(name: str | None) -> str:
    """``[Sales]`` -> ``Sales``."""
    if not name:
        return ""
    if name.startswith("[") and name.endswith("]"):
        return name[1:-1]
    return name


def _attr_by_local_name(el, local: str) -> str | None:
    for key, value in el.attrib.items():
        if key.rsplit("}", 1)[-1] == local:
            return value
    return None


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def _unique(items: Iterable[str]) -> list[str]:
    return list(OrderedDict.fromkeys(i for i in items if i))


# --------------------------------------------------------------------------
# data model (internal)
# --------------------------------------------------------------------------
@dataclass
class _Datasource:
    name: str
    caption: str
    is_parameters: bool
    el: etree._Element
    fields: "OrderedDict[str, dict]" = field(default_factory=OrderedDict)  # internal name -> row
    captions: dict[str, str] = field(default_factory=dict)  # internal name -> display name
    row: dict = field(default_factory=dict)


def _infer_role(datatype: str | None) -> str:
    return "measure" if (datatype or "").lower() in {"integer", "real"} else "dimension"


# --------------------------------------------------------------------------
# datasources
# --------------------------------------------------------------------------
def _conn_info(conn_el, caption: str | None = None) -> dict:
    # Deliberately NOT reading username/password attributes.
    return {
        "class": conn_el.get("class"),
        "caption": caption,
        "server": conn_el.get("server") or None,
        "port": conn_el.get("port") or None,
        "dbname": conn_el.get("dbname") or None,
        "filename": conn_el.get("filename") or None,
    }


def _normalise_path(value: str) -> str:
    return value.replace("\\", "/").strip()


def _resolve_member(candidates: Sequence[str | None], members: Sequence[str]) -> str | None:
    """Find which archive member a connection's file path refers to (best effort)."""
    if not members:
        return None
    norm_members = {m: _normalise_path(m).lower() for m in members}
    for cand in candidates:
        if not cand:
            continue
        target = _normalise_path(cand).lower()
        for member, nm in norm_members.items():
            if nm == target or nm.endswith("/" + target) or target.endswith("/" + nm):
                return member
        base = posixpath.basename(target)
        same_base = [m for m, nm in norm_members.items() if posixpath.basename(nm) == base]
        if base and len(same_base) == 1:
            return same_base[0]
    return None


def _field_row(ds: _Datasource, internal: str, **kw) -> dict:
    row = {
        "datasource": ds.caption,
        "datasource_name": ds.name,
        "name": kw.get("caption") or internal,
        "internal_name": internal,
        "kind": kw.get("kind", "physical"),
        "datatype": kw.get("datatype"),
        "role": kw.get("role"),
        "role_inferred": kw.get("role_inferred", False),
        "type": kw.get("type"),
        "hidden": kw.get("hidden", False),
        "table": kw.get("table"),
        "source_column": kw.get("source_column"),
        "formula": kw.get("formula"),
        "formula_resolved": None,
        "value": kw.get("value"),
        "worksheets": [],
    }
    return row


def _merge_missing(target: dict, other: dict) -> None:
    for key, value in other.items():
        if target.get(key) in (None, "", [], False) and value not in (None, "", []):
            target[key] = value


def _collect_fields(ds: _Datasource) -> None:
    el = ds.el

    # Physical columns known from the connection's metadata-records.
    conn = _kid(el, "connection")
    extract = _kid(el, "extract")
    meta_parent = _kid(conn, "metadata-records") if conn is not None else None
    if meta_parent is None and extract is not None:
        for c in _desc(extract, "connection"):
            meta_parent = _kid(c, "metadata-records")
            if meta_parent is not None:
                break
    meta_index: dict[str, dict] = {}
    for rec in _kids(meta_parent, "metadata-record"):
        if rec.get("class") != "column":
            continue
        local = _strip(rec.findtext("local-name"))
        if not local:
            continue
        meta_index[local] = {
            "datatype": rec.findtext("local-type"),
            "table": _strip(rec.findtext("parent-name")) or None,
            "source_column": rec.findtext("remote-name"),
        }

    def add(row: dict) -> None:
        existing = ds.fields.get(row["internal_name"])
        if existing is None:
            ds.fields[row["internal_name"]] = row
        else:
            _merge_missing(existing, row)

    # <column> elements: customised physical fields, calculations, parameters, bins.
    for col in _kids(el, "column"):
        raw_name = col.get("name")
        internal = _strip(raw_name)
        if not internal:
            continue
        calc = _kid(col, "calculation")
        calc_class = calc.get("class") if calc is not None else None
        formula = calc.get("formula") if calc is not None else None
        if ds.is_parameters or col.get("param-domain-type"):
            kind = "parameter"
        elif calc_class == "bin":
            kind = "bin"
        elif calc_class == "categorical-bin":
            kind = "group"  # Tableau stores manual groups on a field this way
        elif formula is not None:
            kind = "calculated"
        else:
            kind = "physical"
        meta = meta_index.get(internal, {})
        add(
            _field_row(
                ds,
                internal,
                caption=col.get("caption"),
                kind=kind,
                datatype=col.get("datatype") or meta.get("datatype"),
                role=col.get("role"),
                type=col.get("type"),
                hidden=_truthy(col.get("hidden")),
                table=meta.get("table"),
                source_column=meta.get("source_column"),
                formula=None if kind == "parameter" else formula,
                value=col.get("value") if kind == "parameter" else None,
            )
        )

    # Groups and sets are <group> elements.
    for grp in _kids(el, "group"):
        internal = _strip(grp.get("name"))
        if not internal:
            continue
        builder = _attr_by_local_name(grp, "ui-builder") or ""
        kind = "set" if builder == "filter-group" else "group"
        add(_field_row(ds, internal, caption=grp.get("caption"), kind=kind, role="dimension", datatype=None))

    # Remaining physical columns that Tableau never customised.
    for local, meta in meta_index.items():
        if local in ds.fields:
            continue
        add(
            _field_row(
                ds,
                local,
                kind="physical",
                datatype=meta["datatype"],
                role=_infer_role(meta["datatype"]),
                role_inferred=True,
                table=meta["table"],
                source_column=meta["source_column"],
            )
        )

    # Fill in missing roles on customised columns, then compute display names.
    for row in ds.fields.values():
        if not row["role"] and row["kind"] in ("physical", "calculated"):
            row["role"] = _infer_role(row["datatype"])
            row["role_inferred"] = True
        ds.captions[row["internal_name"]] = row["name"]


def _build_datasource(ds_el, members: Sequence[str]) -> _Datasource:
    name = ds_el.get("name") or ""
    caption = ds_el.get("caption") or name
    ds = _Datasource(
        name=name,
        caption=caption,
        is_parameters=(name == PARAMETERS_DATASOURCE),
        el=ds_el,
    )
    _collect_fields(ds)

    conn = _kid(ds_el, "connection")
    connections: list[dict] = []
    if conn is not None:
        for nc in _desc(conn, "named-connection"):
            inner = _kid(nc, "connection")
            if inner is not None:
                connections.append(_conn_info(inner, nc.get("caption")))
        if not connections and conn.get("class") and conn.get("class") != "federated":
            connections.append(_conn_info(conn))

    # Extract
    extract = _kid(ds_el, "extract")
    has_extract = extract is not None and (extract.get("enabled") or "true").lower() != "false"
    extract_conn = None
    if extract is not None:
        for c in _desc(extract, "connection"):
            extract_conn = {
                "class": c.get("class"),
                "dbname": c.get("dbname") or None,
                "schema": c.get("schema") or None,
                "tablename": c.get("tablename") or None,
            }
            break

    # Tables, custom SQL, joins, unions
    tables: list[str] = []
    custom_sql: list[str] = []
    joins = unions = 0
    if conn is not None:
        for rel in _desc(conn, "relation"):
            rtype = rel.get("type")
            if rtype == "text":
                text = (rel.text or "").strip()
                if text:
                    custom_sql.append(text[:MAX_CUSTOM_SQL_CHARS])
            elif rtype == "join":
                joins += 1
            elif rtype == "union":
                unions += 1
            elif rel.get("name") or rel.get("table"):
                tables.append(rel.get("name") or rel.get("table"))

    repo = _kid(ds_el, "repository-location")
    published_to = None
    if repo is not None:
        published_to = repo.get("path") or repo.get("id") or "published datasource"

    classes = {c["class"] for c in connections if c.get("class")}
    if ds.is_parameters:
        mode = "parameters"
    elif "sqlproxy" in classes or published_to:
        mode = "published"
    elif has_extract:
        mode = "extract"
    elif classes and classes <= FILE_CONNECTION_CLASSES:
        mode = "file"
    elif classes:
        mode = "live"
    else:
        mode = "unknown"

    candidates: list[str | None] = []
    if extract_conn:
        candidates.append(extract_conn["dbname"])
    for c in connections:
        candidates.append(c.get("filename"))
        if c.get("class") in FILE_CONNECTION_CLASSES:
            candidates.append(c.get("dbname"))
    data_file = _resolve_member(candidates, members)

    first = connections[0] if connections else {}
    calc_count = sum(1 for f in ds.fields.values() if f["kind"] == "calculated")
    ds.row = {
        "name": name,
        "caption": caption,
        "mode": mode,
        "connection_classes": sorted(classes),
        "server": first.get("server"),
        "dbname": first.get("dbname"),
        "filename": first.get("filename"),
        "connections": connections,
        "has_extract": has_extract,
        "extract": extract_conn,
        "data_file": data_file,
        "tables": _unique(tables),
        "custom_sql": custom_sql,
        "joins": joins,
        "unions": unions,
        "join_details": _join_details(ds_el, conn),
        "published_to": published_to,
        "field_count": len(ds.fields),
        "calculated_field_count": calc_count,
    }
    return ds


# --------------------------------------------------------------------------
# formula / shelf rendering
# --------------------------------------------------------------------------
def _resolve_formula(
    formula: str,
    context_ds: str,
    datasources: dict[str, _Datasource],
    param_captions: dict[str, str],
) -> str:
    def repl(m: re.Match) -> str:
        qualifier, fld = m.group(1), m.group(2)
        if qualifier is None:
            ds = datasources.get(context_ds)
            shown = (ds.captions.get(fld) if ds else None) or fld
            return f"[{shown}]"
        if qualifier == PARAMETERS_DATASOURCE:
            return f"[{PARAMETERS_DATASOURCE}].[{param_captions.get(fld, fld)}]"
        ds = datasources.get(qualifier)
        if ds is not None:
            return f"[{ds.caption}].[{ds.captions.get(fld) or fld}]"
        return m.group(0)

    return _QUALIFIED_REF.sub(repl, formula)


def _instance_label(inner: str, ds: _Datasource | None) -> str:
    """``sum:Sales:qk`` -> ``sum(Sales)``; ``none:Region:nk`` -> ``Region``."""
    captions = ds.captions if ds else {}
    m = _INSTANCE.match(inner)
    if not m:
        return captions.get(inner, inner)
    prefix, name = m.group("prefix"), m.group("name")
    shown = captions.get(name, name)
    return shown if prefix.lower() in ("none", "usr") else f"{prefix.lower()}({shown})"


def _readable_shelf(text: str | None, datasources: dict[str, _Datasource]) -> str | None:
    if not text:
        return None

    def repl(m: re.Match) -> str:
        return _instance_label(m.group(2), datasources.get(m.group(1)))

    return _SHELF_REF.sub(repl, text).strip() or None


# --------------------------------------------------------------------------
# joins and relationships
# --------------------------------------------------------------------------
MAX_JOIN_DETAILS = 50


def _relation_label(rel) -> str:
    return rel.get("name") or rel.get("table") or "?"


def _join_details(ds_el, conn) -> list[str]:
    """Readable descriptions of the datasource's joins, unions and (2020.2+) relationships."""
    out: list[str] = []
    if conn is not None:
        for rel in _desc(conn, "relation"):
            rtype = rel.get("type")
            if rtype == "join":
                sides = [_relation_label(r) for r in _kids(rel, "relation")]
                keys = []
                for clause in _kids(rel, "clause"):
                    for ex in _kids(clause, "expression"):
                        ops = [e.get("op") for e in _kids(ex, "expression")]
                        if len(ops) == 2:
                            keys.append(f"{ops[0]} {ex.get('op') or '='} {ops[1]}")
                kind = rel.get("join") or "join"
                out.append(f"{kind} join {' and '.join(sides)} on {' AND '.join(keys) if keys else '(no key found)'}")
            elif rtype == "union":
                out.append(f"union of {', '.join(_relation_label(r) for r in _kids(rel, 'relation')) or '?'}")

    # Relationships (the logical data model used since Tableau 2020.2)
    objects = {o.get("id"): (o.get("caption") or o.get("id")) for o in _desc(ds_el, "object") if o.get("id")}
    for rel in _desc(ds_el, "relationship"):
        ex = _kid(rel, "expression")
        ops = [e.get("op") for e in _kids(ex, "expression")] if ex is not None else []
        ends = [objects.get(e.get("object-id"), e.get("object-id")) for e in (
            _kid(rel, "first-end-point"), _kid(rel, "second-end-point")) if e is not None]
        if len(ops) == 2 and len(ends) == 2:
            out.append(f"relationship {ends[0]} to {ends[1]} on {ops[0]} {ex.get('op') or '='} {ops[1]}")
        elif ends:
            out.append(f"relationship {' to '.join(ends)}")
    return out[:MAX_JOIN_DETAILS]


# --------------------------------------------------------------------------
# worksheets and dashboards
# --------------------------------------------------------------------------
def _parse_worksheet(ws, datasources: dict[str, _Datasource], param_captions: dict[str, str]):
    name = ws.get("name") or ""
    table = _kid(ws, "table")
    view = _kid(table, "view")

    used: "OrderedDict[tuple[str, str], dict]" = OrderedDict()
    ds_names: list[str] = []

    for dep in _kids(view, "datasource-dependencies"):
        ds_name = dep.get("datasource") or ""
        ds = datasources.get(ds_name)
        ds_names.append(ds_name)
        derivations: dict[str, list[str]] = {}
        quick_calcs: dict[str, list[str]] = {}
        for ci in _kids(dep, "column-instance"):
            key = _strip(ci.get("column"))
            derivations.setdefault(key, []).append(ci.get("derivation") or "")
            for tc in _kids(ci, "table-calc"):  # quick table calculation set on the shelf, not in a formula
                quick_calcs.setdefault(key, []).append(tc.get("type") or "table calculation")

        dep_cols = {_strip(c.get("name")): c for c in _kids(dep, "column")}
        # a column can appear only as an instance (e.g. built-in fields)
        for internal in list(dep_cols) + [d for d in derivations if d not in dep_cols]:
            if not internal:
                continue
            base = ds.fields.get(internal) if ds else None
            dep_col = dep_cols.get(internal)
            calc = _kid(dep_col, "calculation") if dep_col is not None else None
            if base is not None:
                row = {
                    "kind": base["kind"],
                    "datatype": base["datatype"],
                    "role": base["role"],
                    "display": base["name"],
                    "formula": base["formula_resolved"],
                }
            else:
                kind = "parameter" if ds_name == PARAMETERS_DATASOURCE else (
                    "calculated" if calc is not None and calc.get("formula") is not None else "physical"
                )
                formula = calc.get("formula") if calc is not None else None
                row = {
                    "kind": kind,
                    "datatype": dep_col.get("datatype") if dep_col is not None else None,
                    "role": dep_col.get("role") if dep_col is not None else None,
                    "display": (dep_col.get("caption") if dep_col is not None else None) or internal,
                    "formula": (
                        _resolve_formula(formula, ds_name, datasources, param_captions) if formula else None
                    ),
                }
            row.update(
                {
                    "worksheet": name,
                    "datasource": ds.caption if ds else ds_name,
                    "field": row.pop("display"),
                    "internal_name": internal,
                    "derivations": _unique(derivations.get(internal, [])),
                    "quick_table_calcs": _unique(quick_calcs.get(internal, [])),
                }
            )
            used[(ds_name, internal)] = row
            if base is not None and name not in base["worksheets"]:
                base["worksheets"].append(name)

    view_ds_names = [d.get("name") or "" for d in _kids(_kid(view, "datasources"), "datasource")]
    all_ds = _unique(view_ds_names + ds_names)

    filters = []
    for flt in _kids(view, "filter"):
        label = _readable_shelf(flt.get("column"), datasources) or flt.get("column") or "?"
        filters.append(f"{label} ({flt.get('class') or 'filter'})")

    marks = _unique(m.get("class") or "" for m in _desc(_kid(table, "panes"), "mark"))

    usage_rows = list(used.values())
    summary = {
        "name": name,
        "datasources": [datasources[d].caption if d in datasources else d for d in all_ds],
        "field_count": len(usage_rows),
        "calculated_fields_used": sum(1 for r in usage_rows if r["kind"] == "calculated"),
        "parameters_used": sum(1 for r in usage_rows if r["kind"] == "parameter"),
        "filters": filters,
        "marks": marks,
        "rows": _readable_shelf(table.findtext("rows") if table is not None else None, datasources),
        "cols": _readable_shelf(table.findtext("cols") if table is not None else None, datasources),
        "dashboards": [],
    }
    return summary, usage_rows


def _parse_dashboard(dash, worksheet_names: set[str]) -> dict:
    zones = _desc(dash, "zone")
    sheets = _unique(z.get("name") or "" for z in zones if (z.get("name") or "") in worksheet_names)
    types = Counter(
        (z.get("type-v2") or z.get("type") or "")
        for z in zones
        if (z.get("type-v2") or z.get("type"))
        and not (z.get("type-v2") or z.get("type") or "").startswith("layout")
    )
    return {
        "name": dash.get("name") or "",
        "worksheets": sheets,
        "worksheet_count": len(sheets),
        "objects": dict(sorted(types.items())),
    }


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def _make_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
    )


def parse_workbook(xml_bytes: bytes, data_members: Sequence[str] = ()) -> dict:
    """Parse workbook XML. ``data_members`` are the archive's data file names (for linking)."""
    if b"<!ENTITY" in xml_bytes:
        raise parse_error("The workbook XML declares XML entities, which Tableau files never do; refusing to parse.")
    try:
        root = etree.fromstring(xml_bytes, _make_parser())
    except etree.XMLSyntaxError as exc:
        raise parse_error(f"The workbook XML could not be parsed: {exc}") from exc

    root_name = _local(root.tag)
    if root_name == "datasource":
        raise parse_error(
            "This looks like a Tableau data source (.tds), not a workbook (.twb).", "NOT_A_WORKBOOK"
        )
    if root_name != "workbook":
        raise parse_error("The file is XML but not a Tableau workbook.", "NOT_A_WORKBOOK")

    datasources: "OrderedDict[str, _Datasource]" = OrderedDict()
    for ds_el in _kids(_kid(root, "datasources"), "datasource"):
        ds = _build_datasource(ds_el, data_members)
        datasources[ds.name] = ds

    params = datasources.get(PARAMETERS_DATASOURCE)
    param_captions = dict(params.captions) if params else {}

    # Resolve formulas now that every datasource's captions are known.
    for ds in datasources.values():
        for fld in ds.fields.values():
            if fld["formula"]:
                fld["formula_resolved"] = _resolve_formula(fld["formula"], ds.name, datasources, param_captions)

    worksheets: list[dict] = []
    worksheet_fields: list[dict] = []
    for ws in _kids(_kid(root, "worksheets"), "worksheet"):
        summary, usage = _parse_worksheet(ws, datasources, param_captions)
        worksheets.append(summary)
        worksheet_fields.extend(usage)

    ws_names = {w["name"] for w in worksheets}
    dashboards = [_parse_dashboard(d, ws_names) for d in _kids(_kid(root, "dashboards"), "dashboard")]
    by_name = {w["name"]: w for w in worksheets}
    for dash in dashboards:
        for sheet in dash["worksheets"]:
            by_name[sheet]["dashboards"].append(dash["name"])

    all_fields = [f for ds in datasources.values() for f in ds.fields.values()]
    real_fields = [f for f in all_fields if f["kind"] != "parameter"]
    ds_rows = [ds.row for ds in datasources.values()]
    repo = _kid(root, "repository-location")

    warnings: list[str] = []
    for row in ds_rows:
        if row["mode"] == "published":
            warnings.append(
                f"Datasource '{row['caption']}' is published to Tableau Server/Cloud. Its full field list lives "
                "on the server; only fields referenced or customised in this workbook are shown here. "
                "Download the published datasource (.tdsx) to inventory the rest."
            )
        elif row["mode"] == "live":
            warnings.append(
                f"Datasource '{row['caption']}' uses a live connection "
                f"({', '.join(row['connection_classes'])}); the workbook holds no row data for it."
            )
    if not datasources:
        warnings.append("No datasources were found in this workbook.")

    return {
        "workbook": {
            "version": root.get("version"),
            "original_version": root.get("original-version"),
            "source_build": root.get("source-build"),
            "source_platform": root.get("source-platform"),
            "published_to": (repo.get("path") if repo is not None else None),
        },
        "summary": {
            "datasources": sum(1 for r in ds_rows if r["mode"] != "parameters"),
            "worksheets": len(worksheets),
            "dashboards": len(dashboards),
            "fields": len(real_fields),
            "calculated_fields": sum(1 for f in real_fields if f["kind"] == "calculated"),
            "parameters": sum(1 for f in all_fields if f["kind"] == "parameter"),
            "actions": len(_desc(_kid(root, "actions"), "action")),
            "extract_datasources": sum(1 for r in ds_rows if r["mode"] == "extract"),
            "live_datasources": sum(1 for r in ds_rows if r["mode"] == "live"),
            "published_datasources": sum(1 for r in ds_rows if r["mode"] == "published"),
        },
        "datasources": ds_rows,
        "fields": all_fields,
        "worksheets": worksheets,
        "worksheet_fields": worksheet_fields,
        "dashboards": dashboards,
        "warnings": warnings,
    }
