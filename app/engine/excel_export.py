"""
Excel outputs (Section 27, Module 13). These are the artifacts a BI analyst
or auditor actually takes to a meeting — they have to stand on their own
as evidence (Section 28: "Excel Interaction With Lineage").

  export_reconciliation       two snapshots of one visual + the case
  export_data_reconciliation  row-level BI-vs-Excel (or any two datasets)
                              comparison with bridge, causes and diffs
"""
from __future__ import annotations

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.models.data_reconciliation import DataReconciliation
from app.models.investigation import Investigation
from app.models.reconciliation import Reconciliation
from app.models.snapshot import VisualSnapshot

BOLD = Font(bold=True)
TITLE = Font(bold=True, size=14)
HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
HEADER_FONT = Font(bold=True, color="FFFFFF")
STATUS_FILL = {
    "MATCH": PatternFill("solid", fgColor="E3F4E8"),
    "MISMATCH": PatternFill("solid", fgColor="FDE7D6"),
    "ONLY_LEFT": PatternFill("solid", fgColor="FBE3E4"),
    "ONLY_RIGHT": PatternFill("solid", fgColor="E4E9FB"),
}
NUMBER = "#,##0.00;[Red]-#,##0.00"
PCT = '0.00"%"'


def _save(wb: Workbook, output_path: str) -> None:
    """Every value written comes from user data (keys, file names, DAX), so
    before saving: drop control characters Excel rejects, and store text
    that starts with '=' as text — never as a live formula."""
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    clean = ILLEGAL_CHARACTERS_RE.sub("", cell.value)
                    if clean != cell.value or cell.data_type == "f":
                        cell.value = clean
                        cell.data_type = "s"
    wb.save(output_path)


def _scrub(model):
    """Copy of a pydantic model with control characters removed from every
    string: openpyxl refuses them the moment a cell is assigned."""
    def clean(v):
        if isinstance(v, str):
            return ILLEGAL_CHARACTERS_RE.sub("", v)
        if isinstance(v, dict):
            return {clean(k): clean(x) for k, x in v.items()}
        if isinstance(v, list):
            return [clean(x) for x in v]
        return v
    return None if model is None else type(model).model_validate(clean(model.model_dump()))


def _header(ws: Worksheet, row: int, values: list[str]) -> None:
    for col, value in enumerate(values, start=1):
        cell = ws.cell(row=row, column=col, value=value)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)


def _widths(ws: Worksheet, widths: list[int]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _investigation_sheets(wb: Workbook, investigation: Investigation) -> None:
    evidence_sheet = wb.create_sheet("Evidence")
    _header(evidence_sheet, 1, ["Evidence ID", "Kind", "Description", "Confidence", "Source Ref", "Captured At"])
    for item in investigation.evidence:
        evidence_sheet.append([item.evidence_id, item.kind, item.description, item.confidence.value, item.source_ref, item.captured_at])
    _widths(evidence_sheet, [18, 20, 90, 20, 18, 28])

    findings_sheet = wb.create_sheet("Findings")
    _header(findings_sheet, 1, ["Finding ID", "Summary", "Confidence"])
    for finding in investigation.findings:
        findings_sheet.append([finding.finding_id, finding.summary, finding.confidence.value])
    _widths(findings_sheet, [16, 110, 22])

    if investigation.hypotheses:
        hyp = wb.create_sheet("Hypotheses")
        _header(hyp, 1, ["Rank", "Hypothesis", "Score", "Supported", "Confidence", "Amount", "Supporting facts", "Suggested action"])
        for i, h in enumerate(investigation.hypotheses, start=1):
            hyp.append([i, h.title or h.description, h.score, "yes" if h.supported else ("no" if h.supported is False else "?"),
                        h.confidence.value, h.affected_amount, "\n".join(h.facts), h.suggested_action])
            hyp.cell(row=i + 1, column=6).number_format = NUMBER
        _widths(hyp, [6, 50, 8, 10, 20, 16, 70, 60])


def export_reconciliation(
    investigation: Investigation,
    reconciliation: Reconciliation,
    snapshot_a: VisualSnapshot,
    snapshot_b: VisualSnapshot,
    output_path: str,
) -> str:
    investigation, reconciliation = _scrub(investigation), _scrub(reconciliation)
    snapshot_a, snapshot_b = _scrub(snapshot_a), _scrub(snapshot_b)
    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    summary["A1"] = "Investigation"
    summary["A1"].font = BOLD
    summary["B1"] = investigation.question
    summary["A2"] = "Investigation ID"
    summary["B2"] = investigation.investigation_id
    summary["A3"] = "Confidence"
    summary["B3"] = investigation.confidence.value
    summary["A4"] = "Status"
    summary["B4"] = investigation.status.value

    _header(summary, 6, ["", "Snapshot A", "Snapshot B", "Difference", "Difference %"])
    v = reconciliation.variance
    summary.cell(row=7, column=1, value=snapshot_a.visual_title)
    for col, value in ((2, v.value_a), (3, v.value_b), (4, v.difference)):
        summary.cell(row=7, column=col, value=value).number_format = NUMBER
    summary.cell(row=7, column=5, value=(f"{v.difference_pct:.2f}%" if v.difference_pct is not None else "n/a"))

    summary.cell(row=9, column=1, value="Context A").font = BOLD
    summary.cell(row=9, column=2, value=snapshot_a.context_fingerprint() or "(none)")
    summary.cell(row=10, column=1, value="Context B").font = BOLD
    summary.cell(row=10, column=2, value=snapshot_b.context_fingerprint() or "(none)")
    summary.cell(row=11, column=1, value="Context differences").font = BOLD
    summary.cell(row=11, column=2, value="; ".join(reconciliation.context_diff) or "none")
    _widths(summary, [26, 26, 26, 26, 26])

    _investigation_sheets(wb, investigation)
    _save(wb, output_path)
    return output_path


def export_data_reconciliation(rec: DataReconciliation, output_path: str, investigation: Investigation | None = None) -> str:
    rec, investigation = _scrub(rec), _scrub(investigation)
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = rec.name
    ws["A1"].font = TITLE
    meta = [
        ("Reconciliation ID", rec.reconciliation_id),
        ("Run at (UTC)", rec.created_at),
        ("Left", rec.left_name),
        ("Right", rec.right_name),
        ("Result", "All rows match" if rec.status == "MATCHED" else "Differences found"),
        ("Confidence", rec.confidence.value),
        ("Match rate", f"{rec.summary.match_rate_pct}%"),
        ("Keys compared", rec.summary.total_keys),
        ("Matched", rec.summary.matched),
        ("Value differences", rec.summary.mismatched),
        (f"Only in {rec.left_name}", rec.summary.only_left),
        (f"Only in {rec.right_name}", rec.summary.only_right),
    ]
    for i, (k, v) in enumerate(meta, start=3):
        ws.cell(row=i, column=1, value=k).font = BOLD
        ws.cell(row=i, column=2, value=v)

    row = len(meta) + 5
    ws.cell(row=row - 1, column=1, value="Totals and variance bridge").font = BOLD
    _header(ws, row, ["Value", f"Total {rec.left_name}", f"Total {rec.right_name}", "Difference", "Difference %",
                      f"Missing from {rec.right_name}", f"Missing from {rec.left_name}", "Value differences"])
    for vs in rec.value_summaries:
        row += 1
        values = [vs.label, vs.total_left, vs.total_right, vs.difference, vs.difference_pct,
                  vs.bridge.missing_from_right, vs.bridge.missing_from_left, vs.bridge.value_differences]
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=row, column=col, value=value)
            if col in (2, 3, 4, 6, 7, 8):
                cell.number_format = NUMBER
            elif col == 5:
                cell.number_format = PCT

    if rec.warnings:
        row += 2
        ws.cell(row=row, column=1, value="Notes").font = BOLD
        for w in rec.warnings:
            row += 1
            ws.cell(row=row, column=1, value=w)
    _widths(ws, [34, 22, 22, 18, 14, 22, 22, 18])

    causes = wb.create_sheet("Likely causes")
    _header(causes, 1, ["Rank", "Likely cause", "Score", "Confidence", "Amount", "Why we think so", "Suggested action"])
    for i, h in enumerate(rec.hypotheses, start=1):
        causes.append([i, h.title, h.score, h.confidence.value, h.affected_amount, "\n".join(h.facts), h.suggested_action])
        causes.cell(row=i + 1, column=5).number_format = NUMBER
        for col in (2, 6, 7):
            causes.cell(row=i + 1, column=col).alignment = Alignment(wrap_text=True, vertical="top")
    _widths(causes, [6, 45, 8, 20, 16, 80, 60])

    key_labels = rec.key_labels() or ["Scope"]
    value_labels = rec.value_labels()

    def row_sheet(title: str, include_matches: bool) -> None:
        sheet = wb.create_sheet(title)
        headers = ["Status", *key_labels]
        for label in value_labels:
            headers += [f"{label} — {rec.left_name}", f"{label} — {rec.right_name}", f"{label} — Difference", f"{label} — Diff %"]
        headers += [f"Rows in {rec.left_name}", f"Rows in {rec.right_name}"]
        _header(sheet, 1, headers)
        r = 1
        for result_row in rec.rows:
            if not include_matches and result_row.status == "MATCH":
                continue
            r += 1
            values = [result_row.status.replace("_", " ").title(), *[result_row.keys.get(k, "") for k in key_labels]]
            for c in result_row.cells:
                values += [c.left, c.right, c.difference, c.difference_pct]
            values += [result_row.left_rows, result_row.right_rows]
            for col, value in enumerate(values, start=1):
                cell = sheet.cell(row=r, column=col, value=value)
                offset = col - 1 - len(key_labels) - 1
                if 0 <= offset < 4 * len(value_labels):
                    cell.number_format = PCT if offset % 4 == 3 else NUMBER
            sheet.cell(row=r, column=1).fill = STATUS_FILL[result_row.status]
        sheet.freeze_panes = sheet.cell(row=2, column=len(key_labels) + 2)
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(r, 1)}"
        _widths(sheet, [14, *[22] * len(key_labels), *[18] * (4 * len(value_labels)), 12, 12])

    row_sheet("Differences", include_matches=False)
    row_sheet("All rows", include_matches=True)

    if rec.drivers:
        drivers = wb.create_sheet("Drivers")
        _header(drivers, 1, ["Value", "Dimension", "Member", "Difference", "Share of variance %", "Rows"])
        for d in rec.drivers:
            for m in d.members:
                drivers.append([d.value_label, d.dimension, m.member, m.difference, m.share_pct, m.rows])
                drivers.cell(row=drivers.max_row, column=4).number_format = NUMBER
        _widths(drivers, [30, 24, 30, 18, 18, 8])

    mapping = wb.create_sheet("Mapping")
    _header(mapping, 1, ["Role", f"{rec.left_name} column", f"{rec.right_name} column", "Tolerance (abs)", "Tolerance (%)"])
    for k in rec.config.key_columns:
        mapping.append(["Key", k.left, k.right, None, None])
    for v in rec.config.value_columns:
        mapping.append(["Value", v.left, v.right, v.tolerance_abs, v.tolerance_pct])
    for side, filters in (("left", rec.config.left_filters), ("right", rec.config.right_filters)):
        for col, vals in filters.items():
            mapping.append([f"Filter ({side})", col, ", ".join(map(str, vals)), None, None])
    _widths(mapping, [16, 34, 34, 16, 16])

    if investigation is not None:
        _investigation_sheets(wb, investigation)
    _save(wb, output_path)
    return output_path
