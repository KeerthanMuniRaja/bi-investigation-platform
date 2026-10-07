"""
Turning whatever a source produced (a parsed sheet, a connector's
QueryResult, rows posted to the API) into a Dataset, plus the declarative
transforms that reshape it before comparison.
"""
from __future__ import annotations

from typing import Any

from app.connectors.base import QueryResult
from app.engine.values import infer_dtype, normalize_key, to_jsonable
from app.models.dataset import Dataset, DatasetColumn, DatasetSpec, Transform


def build_dataset(
    dataset_id: str,
    name: str,
    spec: DatasetSpec,
    columns: list[str],
    rows: list[dict[str, Any]],
    platform: str | None = None,
    header_row: int | None = None,
    warnings: list[str] | None = None,
) -> Dataset:
    warnings = list(warnings or [])
    columns, rows = apply_transforms(columns, rows, spec.transforms, warnings)
    typed = [DatasetColumn(name=c, dtype=infer_dtype([r.get(c) for r in rows[:1000]])) for c in columns]
    return Dataset(
        dataset_id=dataset_id,
        name=name,
        source=spec,
        platform=platform,
        columns=typed,
        rows=rows,
        row_count=len(rows),
        header_row=header_row,
        warnings=warnings,
    )


def rows_from_query_result(result: QueryResult) -> tuple[list[str], list[dict[str, Any]]]:
    columns: list[str] = []
    for raw in result.columns:
        # 'Sales[Amount]' and 'Budget[Amount]' must not collapse into one key
        name = clean_column_name(raw)
        if name in columns:
            name = str(raw)
        base, n = name, 1
        while name in columns:
            n += 1
            name = f"{base} ({n})"
        columns.append(name)
    rows = [{col: to_jsonable(v) for col, v in zip(columns, row)} for row in result.rows]
    return columns, rows


def clean_column_name(name: str) -> str:
    """Execute Queries returns 'Table[Column]' / '[Measure]'; show 'Column' / 'Measure'."""
    name = str(name)
    if name.endswith("]") and "[" in name:
        return name[name.rindex("[") + 1:-1]
    return name


def apply_transforms(
    columns: list[str], rows: list[dict[str, Any]], transforms: list[Transform], warnings: list[str]
) -> tuple[list[str], list[dict[str, Any]]]:
    for t in transforms:
        missing = [c for c in _referenced(t) if c not in columns]
        if missing:
            raise ValueError(f"Transform '{t.op}' references unknown column(s): {', '.join(missing)}")
        if t.op == "rename":
            renamed = [t.mapping.get(c, c) for c in columns]
            clashes = sorted({c for c in renamed if renamed.count(c) > 1})
            if clashes:
                raise ValueError(f"Rename would create duplicate column(s): {', '.join(clashes)}")
            columns = renamed
            rows = [{t.mapping.get(k, k): v for k, v in r.items()} for r in rows]
        elif t.op == "drop":
            columns = [c for c in columns if c not in t.columns]
            rows = [{k: v for k, v in r.items() if k not in t.columns} for r in rows]
        elif t.op == "select":
            columns = [c for c in t.columns]
            rows = [{c: r.get(c) for c in columns} for r in rows]
        elif t.op == "filter":
            wanted = {normalize_key(v) for v in t.values}
            before = len(rows)
            rows = [r for r in rows if (normalize_key(r.get(t.column)) in wanted) != t.exclude]
            warnings.append(f"Filter on {t.column}: kept {len(rows)} of {before} rows.")
        elif t.op == "unpivot":
            id_cols = t.columns
            value_cols = [c for c in columns if c not in id_cols]
            long_rows = []
            for r in rows:
                for c in value_cols:
                    item = {k: r.get(k) for k in id_cols}
                    item[t.var_name] = c
                    item[t.value_name] = r.get(c)
                    long_rows.append(item)
            columns, rows = id_cols + [t.var_name, t.value_name], long_rows
    return columns, rows


def _referenced(t: Transform) -> list[str]:
    if t.op == "rename":
        return list(t.mapping)
    if t.op in ("drop", "select", "unpivot"):
        return t.columns
    if t.op == "filter":
        return [t.column] if t.column else []
    return []
