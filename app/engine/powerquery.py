"""
Power Query (M) understanding.

Every imported table in a Power BI model is produced by an M query. Reading
it tells us, without running anything:

  - where the data really comes from (SQL Server db/schema/table,
    Snowflake, an Excel file and sheet, SharePoint, a dataflow, data typed
    into Power BI itself, ...) — the last hop of lineage before the source
    system,
  - which other queries it is built from (merges, appends, references),
  - what was done to it on the way in (filtered rows, removed columns,
    merged, grouped, ...), in plain English.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

CONNECTORS = {
    "Sql.Database": "SQL Server", "Sql.Databases": "SQL Server",
    "AzureSQL.Database": "Azure SQL",
    "Snowflake.Databases": "Snowflake",
    "Databricks.Catalogs": "Databricks", "Databricks.Contents": "Databricks", "DatabricksMultiCloud.Catalogs": "Databricks",
    "GoogleBigQuery.Database": "BigQuery",
    "AmazonRedshift.Database": "Redshift",
    "Oracle.Database": "Oracle",
    "PostgreSQL.Database": "PostgreSQL",
    "MySQL.Database": "MySQL",
    "Teradata.Database": "Teradata",
    "SapHana.Database": "SAP HANA",
    "Odbc.DataSource": "ODBC", "Odbc.Query": "ODBC", "OleDb.DataSource": "OLE DB",
    "AnalysisServices.Database": "Analysis Services", "AnalysisServices.Databases": "Analysis Services",
    "Excel.Workbook": "Excel",
    "Csv.Document": "CSV / text file",
    "Json.Document": "JSON",
    "Xml.Tables": "XML",
    "Folder.Files": "Folder", "Folder.Contents": "Folder",
    "SharePoint.Files": "SharePoint", "SharePoint.Contents": "SharePoint", "SharePoint.Tables": "SharePoint list",
    "OData.Feed": "OData",
    "Web.Contents": "Web / API", "Web.Page": "Web page",
    "AzureStorage.Blobs": "Azure Blob Storage", "AzureStorage.DataLake": "Azure Data Lake",
    "Lakehouse.Contents": "Fabric Lakehouse", "Fabric.Warehouse": "Fabric Warehouse",
    "PowerPlatform.Dataflows": "Power BI dataflow", "PowerBI.Dataflows": "Power BI dataflow", "Dataflows.Contents": "Dataflow",
    "CommonDataService.Database": "Dataverse", "Dataverse.Contents": "Dataverse",
    "Salesforce.Data": "Salesforce", "Salesforce.Reports": "Salesforce",
    "DynamicsNav.Contents": "Dynamics NAV", "Dynamics365BusinessCentral.Contents": "Business Central",
}

STEP_TEXT = {
    "Table.SelectRows": "filtered rows",
    "Table.RemoveColumns": "removed columns",
    "Table.SelectColumns": "kept selected columns",
    "Table.RenameColumns": "renamed columns",
    "Table.TransformColumnTypes": "changed data types",
    "Table.PromoteHeaders": "used first row as headers",
    "Table.AddColumn": "added a calculated column",
    "Table.DuplicateColumn": "duplicated a column",
    "Table.NestedJoin": "merged with another query",
    "Table.Join": "joined with another query",
    "Table.ExpandTableColumn": "expanded merged columns",
    "Table.Combine": "appended queries",
    "Table.Group": "grouped and aggregated",
    "Table.Distinct": "removed duplicate rows",
    "Table.Sort": "sorted",
    "Table.ReplaceValue": "replaced values",
    "Table.FillDown": "filled down blanks",
    "Table.FillUp": "filled up blanks",
    "Table.UnpivotOtherColumns": "unpivoted columns", "Table.Unpivot": "unpivoted columns",
    "Table.Pivot": "pivoted columns",
    "Table.Skip": "skipped top rows",
    "Table.FirstN": "kept top rows",
    "Table.RemoveRowsWithErrors": "removed error rows",
    "Table.ReplaceErrorValues": "replaced errors",
    "Table.TransformColumns": "transformed column values",
    "Table.SplitColumn": "split a column",
    "Table.CombineColumns": "merged columns",
    "Table.Buffer": "buffered in memory",
    "Table.FromRows": "data typed in Power BI (Enter data)",
    "Value.NativeQuery": "ran a native SQL query",
}

_FUNC = re.compile(r"\b([A-Z][A-Za-z0-9]*\.[A-Z][A-Za-z0-9]*)\s*\(")
_STEP = re.compile(r'^\s*(#"(?:[^"]|"")+"|[A-Za-z_][\w.]*)\s*=\s*(.*)$', re.S)
_NAV = re.compile(r"\{\s*\[([^\[\]{}]*)\]\s*\}")
_NAV_PAIR = re.compile(r'([A-Za-z_]\w*)\s*=\s*("(?:[^"]|"")*"|#"(?:[^"]|"")*"|[A-Za-z_][\w.]*)')
# One pass so "//" inside a string (a URL) isn't a comment and a quote inside a comment isn't a string.
_LEX = re.compile(r'(?P<qid>#"(?:[^"]|"")*")|(?P<str>"(?:[^"]|"")*"?)|(?P<cmt>//[^\n]*|/\*.*?(?:\*/|\Z))', re.S)
_BINDING_TOKEN = re.compile(r'#"(?:[^"]|"")*"|"(?:[^"]|"")*"?|[A-Za-z_][\w.]*|[()\[\]{},]')
_FILE_PARSERS = ("Excel.Workbook", "Csv.Document", "Json.Document", "Xml.Tables")
_NAV_ITEM_KINDS = {"table", "view", "sheet", "definedname", "function"}
_SERVER_ONLY = {"Sql.Databases", "AnalysisServices.Databases"}  # connect to a server; the database is navigated to


@dataclass
class MSource:
    kind: str
    function: str
    server: Optional[str] = None
    database: Optional[str] = None
    schema: Optional[str] = None
    item: Optional[str] = None
    path: Optional[str] = None
    native_query: Optional[str] = None
    warehouse: Optional[str] = None  # Snowflake warehouse / Databricks HTTP path: compute, not a database

    def display(self) -> str:
        parts = [self.kind]
        location = "/".join(p for p in (self.server, self.database) if p) or _short_path(self.path)
        if location:
            parts.append(location)
        obj = ".".join(p for p in (self.schema, self.item) if p)
        if obj:
            parts.append(obj)
        if self.native_query:
            parts.append("via SQL query")
        return " · ".join(parts)


@dataclass
class MStep:
    name: str
    function: Optional[str]
    description: str


@dataclass
class MQueryInfo:
    sources: list[MSource] = field(default_factory=list)
    referenced_queries: list[str] = field(default_factory=list)
    steps: list[MStep] = field(default_factory=list)
    entered_data: bool = False

    def summary(self) -> str:
        if self.entered_data and not self.sources:
            origin = "data typed directly into Power BI"
        elif self.sources:
            origin = "; ".join(s.display() for s in self.sources)
        elif self.referenced_queries:
            origin = "built from " + ", ".join(self.referenced_queries)
        else:
            origin = "unknown source"
        actions = []
        for s in self.steps:
            if s.function in STEP_TEXT and STEP_TEXT[s.function] not in actions and s.function != "Table.FromRows":
                actions.append(STEP_TEXT[s.function])
        return origin + (f" — then {', '.join(actions[:8])}" if actions else "")


def _unquote_step(name: str) -> str:
    return name[2:-1].replace('""', '"') if name.startswith('#"') else name


def _blank(text: str, strings: bool) -> str:
    """Same-length copy with comments (and, if asked, plain string contents)
    blanked, so offsets still line up with the original text."""
    def sub(m: re.Match) -> str:
        g = m.group()
        if m.lastgroup == "cmt":
            return re.sub(r"[^\n]", " ", g)
        if m.lastgroup == "str" and strings and len(g) >= 2:
            return g[0] + " " * (len(g) - 2) + g[-1]
        return g
    return _LEX.sub(sub, text)


def _top_bindings(code: str) -> list[tuple[int, int]]:
    """Spans of the outermost let's bindings. Splits on depth-0 commas only,
    so multi-line steps, record literals and nested `each let .. in` bodies
    stay inside the step they belong to."""
    spans, depth, start = [], 0, None
    for m in _BINDING_TOKEN.finditer(code):
        tok = m.group()
        if start is None:
            if tok == "let":
                start = m.end()
            continue
        if len(tok) == 1 and tok in "([{":
            depth += 1
        elif len(tok) == 1 and tok in ")]}":
            depth -= 1
        elif tok == "let":
            depth += 1
        elif tok == "in":
            if depth == 0:
                spans.append((start, m.start()))
                return spans
            depth -= 1
        elif tok == "," and depth == 0:
            spans.append((start, m.start()))
            start = m.end()
    if start is not None:
        spans.append((start, len(code)))
    return spans


def parse_m(expression: str, other_queries: Optional[list[str]] = None) -> MQueryInfo:
    info = MQueryInfo()
    text = (expression or "").replace("\r\n", "\n")
    if not text.strip():
        return info
    code = _blank(text, strings=False)  # comments gone: a commented-out Source line is not a source
    bare = _blank(text, strings=True)   # ...and string contents gone: "Sales" in a filter is not a query reference

    # --- steps (top-level let bindings)
    step_names: set[str] = set()
    for a, b in _top_bindings(code):
        m = _STEP.match(code[a:b])
        if not m:
            continue
        name = _unquote_step(m.group(1))
        funcs = _FUNC.findall(bare[a + m.start(2):b])
        func = next((f for f in funcs if f in STEP_TEXT or f in CONNECTORS), funcs[0] if funcs else None)
        step_names.add(name)
        desc = STEP_TEXT.get(func or "", CONNECTORS.get(func or "", "") and f"connected to {CONNECTORS[func]}") or (func or "reference")
        info.steps.append(MStep(name=name, function=func, description=desc))

    # --- source connectors
    consumed: list[tuple[int, int]] = []
    positions: list[int] = []  # text offset of each source, to attach navigation steps to the right one
    for match in _FUNC.finditer(bare):
        func = match.group(1)
        if any(a <= match.start() < b for a, b in consumed):
            continue  # e.g. the Web.Contents inside Excel.Workbook(Web.Contents(...))
        if func == "Table.FromRows" and "Binary.FromText" in bare:
            info.entered_data = True
        if func not in CONNECTORS:
            continue
        args = _call_args(code, match.end())
        parts = _split_args(args)
        positional = [a for a in parts if not a.startswith("[")]
        options = " ".join(a for a in parts if a.startswith("["))
        src = MSource(kind=CONNECTORS[func], function=func)
        if func in _FILE_PARSERS:
            inner = re.search(r"\b(File|Web)\.Contents\s*\(", bare[match.end():match.end() + len(args)])
            if not inner:
                if "Binary.FromText" in args:
                    info.entered_data = True
                continue  # parses a binary produced elsewhere (another step, inline data) — not itself a source
            inner_parts = _split_args(_call_args(args, inner.end()))
            inner_pos = [a for a in inner_parts if not a.startswith("[")]
            src.path = _literal_or_param(inner_pos[0]) if inner_pos else None
            if inner.group(1) == "Web":
                src.path = _with_relative_path(src.path, inner_parts)
                web_kind = "on SharePoint/OneDrive" if src.path and "sharepoint" in src.path.lower() else "(web)"
                src.kind = f"{CONNECTORS[func]} {web_kind}"
            consumed.append((match.start(), match.end() + len(args)))
        elif func in ("Folder.Files", "Folder.Contents") or func.startswith(("SharePoint.", "Web.", "OData.", "AzureStorage.")):
            src.path = _literal_or_param(positional[0]) if positional else None
            if func == "Web.Contents":
                src.path = _with_relative_path(src.path, parts)
        else:
            src.server = _literal_or_param(positional[0]) if positional else None
            second = _literal_or_param(positional[1]) if len(positional) > 1 else None
            if func == "Odbc.Query":
                src.native_query = _clean_sql(second) if second else None
            elif func.startswith(("Snowflake.", "Databricks", "DatabricksMultiCloud.")):
                src.warehouse = second  # the database comes from navigation ({[Name=..,Kind="Database"]})
            elif func not in ("Odbc.DataSource", "OleDb.DataSource"):
                src.database = second
        q = re.search(r'\bQuery\s*=\s*"((?:[^"]|"")*)"', options, re.S)
        if q:
            src.native_query = _clean_sql(q.group(1).replace('""', '"'))
        info.sources.append(src)
        positions.append(match.start())

    def owner(offset: int) -> Optional[MSource]:
        """The nearest source defined before this point in the text."""
        before = [i for i, p in enumerate(positions) if p < offset]
        return info.sources[before[-1]] if before else None

    # --- navigation into the source: {[Schema="dbo",Item="Stock"]}, {[Name="X",Kind="Database"]}, ...
    item_set_by_item: set[int] = set()
    for nav in _NAV.finditer(bare):
        target = owner(nav.start())
        if target is None:
            continue
        pairs = {k: _literal_or_param(v) for k, v in _NAV_PAIR.findall(code[nav.start(1):nav.end(1)])}
        kind, name = (pairs.get("Kind") or "").lower(), pairs.get("Name")
        if name and (kind == "database" or (not kind and len(pairs) == 1 and target.function in _SERVER_ONLY
                                            and not target.database)):
            target.database = name  # Sql.Databases(server){[Name="db"]} picks the database
        elif kind == "schema" and name:
            target.schema = name
        else:
            if pairs.get("Schema"):
                target.schema = pairs["Schema"]
            if pairs.get("Item"):
                target.item = pairs["Item"]
                item_set_by_item.add(id(target))
            elif name and (kind in _NAV_ITEM_KINDS or not kind) and id(target) not in item_set_by_item:
                target.item = name
            elif pairs.get("entity"):  # dataflow entity
                target.item = pairs["entity"]

    for native in re.finditer(r"\bValue\.NativeQuery\s*\(", bare):
        parts = _split_args(_call_args(code, native.end()))
        target = owner(native.start())
        if target is not None and len(parts) > 1 and parts[1].startswith('"'):
            target.native_query = _clean_sql(_literal_or_param(parts[1]))
    for src in info.sources:
        if src.native_query and not src.item:
            sql = re.sub(r"\b(?:EXTRACT|TRIM|SUBSTRING|OVERLAY|POSITION)\s*\([^()]*\)", " ", src.native_query, flags=re.I)
            tables = re.findall(r'\b(?:from|join)\s+([\w\[\]".]+)', sql, re.I)  # not EXTRACT(YEAR FROM d)
            if tables:
                src.item = ", ".join(dict.fromkeys(t.strip('[]"') for t in tables))

    # --- references to other queries in the same model: on the comment- and
    # string-free text, and not [field] access, Date.Year(..) or a binding name
    for q in other_queries or []:
        if q in step_names:
            continue
        quoted = '#"' + re.escape(q.replace('"', '""')) + '"'
        body = f"(?:{quoted}|{re.escape(q)})" if re.fullmatch(r"[A-Za-z_][\w.]*", q) else quoted
        if re.search(rf'(?<![\w"#.\[]){body}(?![\w".\]])(?!\s*=(?!>))', bare):
            info.referenced_queries.append(q)
    return info


def _call_args(text: str, start: int) -> str:
    depth, i = 1, start
    in_str = False
    while i < len(text) and depth:
        ch = text[i]
        if ch == '"':
            in_str = not in_str
        elif not in_str:
            depth += ch == "("
            depth -= ch == ")"
        i += 1
    return text[start:i - 1] if depth == 0 else text[start:i]


def _short_path(path: Optional[str]) -> Optional[str]:
    """SharePoint/web URLs -> 'host › file name'; local paths unchanged."""
    if not path or "://" not in path:
        return path
    from urllib.parse import unquote, urlparse

    u = urlparse(path)
    tail = unquote(u.path.rstrip("/").split("/")[-1]) or u.netloc
    site = re.search(r"/sites/([^/]+)", u.path)
    return f"{u.netloc}{' › ' + unquote(site.group(1)) if site else ''} › {tail}"


def _split_args(args: str, sep: str = ",") -> list[str]:
    parts, depth, buf, in_str = [], 0, [], False
    for ch in args:
        if ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == sep and depth == 0:
                parts.append("".join(buf).strip())
                buf = []
                continue
        buf.append(ch)
    if "".join(buf).strip():
        parts.append("".join(buf).strip())
    return parts


def _literal_or_param(arg: str) -> Optional[str]:
    """A literal, a parameter, or a concatenation of both:
    FolderPath & "Budget.xlsx" -> '‹FolderPath›Budget.xlsx'."""
    pieces = []
    for piece in _split_args(arg.strip(), "&"):
        if len(piece) >= 2 and piece[0] == '"' and piece[-1] == '"':
            pieces.append(piece[1:-1].replace('""', '"'))
        elif piece == "null":
            continue
        else:
            name = piece[2:-1].replace('""', '"') if piece.startswith('#"') and piece.endswith('"') else piece
            pieces.append(f"‹{name}›")  # a Power Query parameter or variable
    return "".join(pieces) or None


def _with_relative_path(path: Optional[str], parts: list[str]) -> Optional[str]:
    """Web.Contents(base, [RelativePath="v2/orders"]) really reads base/v2/orders."""
    options = " ".join(p for p in parts if p.startswith("["))
    rel = re.search(r'\bRelativePath\s*=\s*((?:"(?:[^"]|"")*"|#"(?:[^"]|"")*"|[^,\]])+)', options)
    if not rel:
        return path
    tail = _literal_or_param(rel.group(1)) or ""
    return f"{(path or '').rstrip('/')}/{tail.lstrip('/')}" if path else tail


def _clean_sql(sql: Optional[str]) -> Optional[str]:
    if not sql:
        return sql
    for esc in ("#(lf)", "#(cr)", "#(tab)"):
        sql = sql.replace(esc, " ")
    return sql[:500]
