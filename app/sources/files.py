"""
Reading tabular data out of files: Excel workbooks (every tab), CSV/TSV,
and the CSV/XLSX exports every BI tool can produce (Power BI "Export data",
Tableau crosstab / view data, Qlik, Looker, ...).

Real-world finance workbooks are not clean tables. They have title rows
above the header, merged cells in dimension columns, "Total" rows, and
footnotes. This module finds the actual table instead of assuming row 1 is
the header, and reports every row it dropped so nothing disappears
silently (Section 35, "No False Completeness").
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.engine.values import is_blank, parse_number, to_jsonable

EXCEL_EXTENSIONS = {".xlsx", ".xlsm", ".xltx", ".xltm"}
TEXT_EXTENSIONS = {".csv", ".tsv", ".txt"}
SUPPORTED_EXTENSIONS = EXCEL_EXTENSIONS | TEXT_EXTENSIONS
CSV_SHEET_NAME = "Sheet1"

# "Total", "Grand Total", "Subtotal", "Total: North", "Total - North", "Total for
# North", "North Total", "Bikes Subtotal", "Result" — but not names like
# "Total SA" or "Total Gym Ltd".
_TOTAL_ROW = re.compile(
    r"^\s*(?:grand\s+|sub[\s-]?)?totals?(?:\s*[:\-–].*|\s+(?:for|of)\s.*)?\s*$"
    r"|^.+\s(?:grand\s+|sub[\s-]?)?totals?\s*$"
    r"|^\s*(?:overall\s+)?result\s*$",
    re.I,
)
_YEARLIKE = re.compile(r"^(19|20)\d{2}$")
_MAX_SCAN_ROWS = 30


class FileReadError(ValueError):
    pass


@dataclass
class SheetInfo:
    name: str
    rows: int
    columns: int


@dataclass
class ParsedTable:
    sheet: str
    header_row: int
    columns: list[str]
    rows: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ sheets
def list_sheets(path: Path) -> list[SheetInfo]:
    ext = Path(path).suffix.lower()
    _check_extension(ext)
    if ext in TEXT_EXTENSIONS:
        grid = read_grid(path)
        return [SheetInfo(name=CSV_SHEET_NAME, rows=len(grid), columns=max((len(r) for r in grid), default=0))]
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        return [SheetInfo(name=ws.title, rows=ws.max_row or 0, columns=ws.max_column or 0) for ws in wb.worksheets]
    finally:
        wb.close()


def read_grid(path: Path, sheet: Optional[str] = None, max_rows: Optional[int] = None) -> list[list[Any]]:
    """Raw cell values for one sheet, merged cells filled with their
    top-left value (crosstab exports merge dimension labels vertically)."""
    path = Path(path)
    ext = path.suffix.lower()
    _check_extension(ext)
    if ext in TEXT_EXTENSIONS:
        return _read_text_grid(path.read_bytes(), ext, max_rows)
    return _read_excel_grid(path, sheet, max_rows)


def parse_csv_text(text: str) -> list[list[Any]]:
    """For CSV that arrives over HTTP (e.g. Tableau's view data endpoint)."""
    return _read_text_grid(text.encode("utf-8"), ".csv", None)


def _check_extension(ext: str) -> None:
    if ext == ".xls":
        raise FileReadError("Legacy .xls files are not supported — open the file in Excel and save it as .xlsx.")
    if ext not in SUPPORTED_EXTENSIONS:
        raise FileReadError(f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")


def _text_encodings(raw: bytes) -> tuple[str, ...]:
    """Decide UTF-16 from the bytes, not from whether a decode 'worked': a
    Windows-1252 CSV with an even byte count decodes as (garbage) UTF-16."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return ("utf-16",)
    if b"\x00" in raw[:4096]:
        return ("utf-16-le",) if raw[1:2] == b"\x00" else ("utf-16-be",)
    return ("utf-8-sig", "cp1252", "latin-1")


def _read_text_grid(raw: bytes, ext: str, max_rows: Optional[int]) -> list[list[Any]]:
    text = None
    for encoding in _text_encodings(raw):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise FileReadError("Could not decode the file as text.")
    sample = text[:20000]
    if ext == ".tsv":
        delimiter = "\t"
    else:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    grid: list[list[Any]] = []
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        grid.append([cell if cell.strip() != "" else None for cell in row])
        if max_rows and len(grid) >= max_rows:
            break
    return grid


def _read_excel_grid(path: Path, sheet: Optional[str], max_rows: Optional[int]) -> list[list[Any]]:
    from openpyxl import load_workbook

    small = path.stat().st_size < 25 * 1024 * 1024
    # read_only mode can't see merged ranges, so only use it for big files
    wb = load_workbook(path, read_only=not small, data_only=True)
    try:
        if sheet is None:
            ws = wb.worksheets[0]
        elif sheet in wb.sheetnames:
            ws = wb[sheet]
        else:
            raise FileReadError(f"Sheet '{sheet}' not found. Available: {', '.join(wb.sheetnames)}")
        grid = []
        for row in ws.iter_rows(values_only=True):
            grid.append(list(row))
            if max_rows and len(grid) >= max_rows:
                break
        if small:
            # Fill vertically merged *labels* down (crosstab dimension
            # columns). Horizontal merges (titles, two-level headers) stay in
            # their first cell, and merged numbers are never copied — a
            # budget merged over three rows is one budget, not three.
            for merged in ws.merged_cells.ranges:
                top_left = ws.cell(merged.min_row, merged.min_col).value
                if merged.max_row == merged.min_row or not isinstance(top_left, str) or parse_number(top_left) is not None:
                    continue
                for r in range(merged.min_row + 1, merged.max_row + 1):
                    if r - 1 < len(grid) and merged.min_col - 1 < len(grid[r - 1]):
                        grid[r - 1][merged.min_col - 1] = top_left
        return grid
    finally:
        wb.close()


def formula_cells_without_values(path: Path, sheet: Optional[str]) -> int:
    """Formulas saved by tools other than Excel have no cached value, so
    they read as blank. Worth warning about instead of reporting zeros."""
    path = Path(path)
    if path.suffix.lower() not in EXCEL_EXTENSIONS or path.stat().st_size > 5 * 1024 * 1024:
        return 0
    from openpyxl import load_workbook

    wb_f = load_workbook(path, data_only=False, read_only=True)
    wb_v = load_workbook(path, data_only=True, read_only=True)
    try:
        name = sheet or wb_f.sheetnames[0]
        if name not in wb_f.sheetnames:
            return 0
        count = 0
        for row_f, row_v in zip(wb_f[name].iter_rows(values_only=True), wb_v[name].iter_rows(values_only=True)):
            for f, v in zip(row_f, row_v):
                if isinstance(f, str) and f.startswith("=") and v is None:
                    count += 1
        return count
    finally:
        wb_f.close()
        wb_v.close()


# ------------------------------------------------------------------ tables
def detect_header_row(grid: list[list[Any]]) -> int:
    """Pick the row that looks most like a header: mostly text, about as
    wide as the table, with data underneath it. Title rows ("Stock Report
    — March 2026") are a single cell and lose; data rows are mostly
    numbers and lose."""
    rows = grid[:_MAX_SCAN_ROWS]
    width = max((_filled(r) for r in rows), default=0)
    if width == 0:
        return 0
    best_index, best_score = 0, float("-inf")
    for i, row in enumerate(rows):
        filled = _filled(row)
        if filled == 0:
            continue
        cells = [c for c in row if not is_blank(c)]
        text_cells = [c.strip().casefold() for c in cells if isinstance(c, str) and parse_number(c) is None]
        texty = len(set(text_cells))  # a label repeated across a merge counts once
        numeric = filled - len(text_cells)
        below = [r for r in grid[i + 1:i + 6] if _filled(r) > 0]
        below_fill = sum(_filled(r) for r in below) / len(below) if below else 0
        score = texty * 2.0 - numeric * 2.0 + filled
        score += filled if below and below_fill >= 0.5 * filled else -filled
        if filled == 1 and width > 2:
            score -= 5
        if filled < 0.5 * width:
            score -= (width - filled)
        if score > best_score:
            best_index, best_score = i, score
    return best_index


def parse_table(
    grid: list[list[Any]],
    header_row: Optional[int] = None,
    sheet: str = CSV_SHEET_NAME,
    drop_total_rows: bool = True,
) -> ParsedTable:
    warnings: list[str] = []
    if not grid or all(_filled(r) == 0 for r in grid):
        return ParsedTable(sheet=sheet, header_row=0, columns=[], rows=[], warnings=["Sheet is empty."])
    if header_row is None:
        header_row = detect_header_row(grid)
    if header_row >= len(grid):
        raise FileReadError(f"Header row {header_row + 1} is beyond the end of the sheet ({len(grid)} rows).")

    header = list(grid[header_row])
    body_start = header_row + 1
    sub = grid[body_start] if body_start < len(grid) else []
    two_level = _is_sub_header(header, sub)
    if two_level:
        # "Sales" merged over two columns with "2024 | 2025" underneath ->
        # "Sales 2024", "Sales 2025" (the label carries right across the merge)
        carried = None
        for j in range(max(len(header), len(sub))):
            top = header[j] if j < len(header) else None
            low = sub[j] if j < len(sub) else None
            carried = top if not is_blank(top) else (carried if not is_blank(low) else None)
            parts = [str(to_jsonable(x)) for x in dict.fromkeys(x for x in (carried, low) if not is_blank(x))]
            value = " ".join(parts) if parts else None
            if j < len(header):
                header[j] = value
            else:
                header.append(value)
        body_start += 1
        warnings.append(f"Two header rows combined (rows {header_row + 1}–{header_row + 2}).")
    body = grid[body_start:]
    width = max([len(header)] + [len(r) for r in body[:2000]])
    used = [
        j for j in range(width)
        if (j < len(header) and not is_blank(header[j])) or any(j < len(r) and not is_blank(r[j]) for r in body)
    ]

    columns: list[str] = []
    taken: set[str] = set()
    for j in used:
        raw = header[j] if j < len(header) else None
        base = " ".join(str(to_jsonable(raw)).split()) if not is_blank(raw) else f"Column {j + 1}"
        name, n = base, 1
        while name in taken:  # "A", "A", "A (2)" must not collide
            n += 1
            name = f"{base} ({n})"
        taken.add(name)
        columns.append(name)

    rows: list[dict[str, Any]] = []
    dropped_totals: list[int] = []
    total_labels: list[str] = []
    dropped_notes: list[int] = []
    for offset, raw_row in enumerate(body):
        values = [raw_row[j] if j < len(raw_row) else None for j in used]
        filled = [v for v in values if not is_blank(v)]
        if not filled:
            continue
        sheet_row = body_start + offset + 1  # 1-based, as Excel shows it
        label = next((v for v in values if isinstance(v, str) and parse_number(v) is None and _TOTAL_ROW.match(v)), None)
        if drop_total_rows and label is not None:
            dropped_totals.append(sheet_row)
            if len(total_labels) < 5 and label.strip() not in total_labels:
                total_labels.append(label.strip())
            continue
        if len(filled) == 1 and len(used) >= 3 and isinstance(filled[0], str) and parse_number(filled[0]) is None \
                and is_blank(values[-1]) and len(filled[0]) > 25:
            dropped_notes.append(sheet_row)
            continue
        rows.append({col: to_jsonable(v) for col, v in zip(columns, values)})

    if header_row > 0:
        warnings.insert(0, f"Header detected on row {header_row + 1}; rows above it were treated as titles.")
    if dropped_totals:
        warnings.append(f"Dropped {len(dropped_totals)} total/subtotal row(s) (sheet rows {_fmt_rows(dropped_totals)}: "
                        f"{', '.join(repr(t) for t in total_labels)}) so they aren't double counted.")
    if dropped_notes:
        warnings.append(f"Dropped {len(dropped_notes)} note/footnote row(s) (sheet rows {_fmt_rows(dropped_notes)}).")
    return ParsedTable(sheet=sheet, header_row=header_row, columns=columns, rows=rows, warnings=warnings)


def read_table(path: Path, sheet: Optional[str] = None, header_row: Optional[int] = None) -> ParsedTable:
    path = Path(path)
    grid = read_grid(path, sheet)
    sheet_name = sheet or (CSV_SHEET_NAME if path.suffix.lower() in TEXT_EXTENSIONS else list_sheets(path)[0].name)
    table = parse_table(grid, header_row=header_row, sheet=sheet_name)
    missing = formula_cells_without_values(path, sheet)
    if missing:
        table.warnings.append(
            f"{missing} formula cell(s) have no saved result and read as blank — open the workbook in Excel, "
            "let it recalculate, save, and upload again."
        )
    return table


def _is_sub_header(header: list[Any], sub: list[Any]) -> bool:
    """A second header row under a merged group label: only labels or years,
    at least two of them, and sitting under a blank (merged-away) header cell."""
    filled = [(j, v) for j, v in enumerate(sub) if not is_blank(v)]
    if len(filled) < 2:
        return False
    labels = all((isinstance(v, str) and parse_number(v) is None) or _YEARLIKE.match(str(to_jsonable(v))) for _, v in filled)
    under_gap = any(j >= len(header) or is_blank(header[j]) for j, _ in filled)
    # the key column has no sub-label (or repeats its label: merged vertically over both rows)
    starts_blank = bool(sub) and (is_blank(sub[0]) or (bool(header) and sub[0] == header[0]))
    return bool(labels and under_gap and starts_blank)


def _filled(row: list[Any]) -> int:
    return sum(1 for c in row if not is_blank(c))


def _fmt_rows(rows: list[int]) -> str:
    shown = ", ".join(str(r) for r in rows[:8])
    return shown + (f" … +{len(rows) - 8} more" if len(rows) > 8 else "")
