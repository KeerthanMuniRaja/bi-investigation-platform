"""
Generates the sample files in samples/ used by the demo, the tests and the
web app's "try it" flow. Each one reproduces a real-world reconciliation
headache on purpose:

  finance_stock_ledger_mar2026.xlsx
      Finance's stock ledger vs the Power BI "Stock Value by Product"
      visual (March 2026). Title rows above the header, a Total row, a
      number stored as text, one product split across two warehouses, one
      product adjusted, one product missing (late load), one extra product.

  powerbi_export_stock_value.csv
      What Power BI's "Export data" gives you for that visual — use it
      with the "Exported files" connection when there's no API access.

  tableau_sales_by_region.csv / finance_sales_by_region.xlsx
      A Tableau view export (SUM(Sales) naming) vs a finance workbook that
      reports in £ thousands — the platform should spot the unit mismatch.

Run:  python scripts/make_sample_files.py
"""
from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

OUT = Path(__file__).resolve().parent.parent / "samples"


def stock_ledger(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Stock Ledger"
    ws["A1"] = "Subsea Stock Ledger — Finance"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = "Period: March 2026 (closing)"
    ws.append([])
    ws.append(["Product Name", "Warehouse", "Year", "Period", "Qty on Hand", "Stock Value (GBP)"])
    for c in ws[4]:
        c.font = Font(bold=True)
    rows = [
        (" hydraulic pump", "Aberdeen", 2026, "Mar", 78000, 982800),
        ("Subsea Cable", "Aberdeen", 2026, "Mar", 21000, "£924,000"),      # number stored as text
        ("Valve Assembly", "Aberdeen", 2026, "Mar", 50000, 410000),         # split across two warehouses
        ("Valve Assembly", "Great Yarmouth", 2026, "Mar", 50000, 410000),
        ("ROV Thruster", "Aberdeen", 2026, "Mar", 4100, 621950),            # adjusted after the BI refresh
        ("Winch Motor", "Great Yarmouth", 2026, "Mar", 300, 15000),         # not in the Power BI model
        # Sensor Kit (294,128 in Power BI) is missing — its batch hadn't loaded into the ledger yet
    ]
    for r in rows:
        ws.append(list(r))
    ws.append(["Total", None, None, None, None, 3362750])
    ws.append([])
    ws.append(["Source: ERP stock valuation extract, run 01-Apr-2026 06:00. Values at weighted average cost."])
    for row in ws.iter_rows(min_row=5, min_col=6, max_col=6):
        for cell in row:
            cell.number_format = "£#,##0"
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["F"].width = 20
    wb.save(path)


def powerbi_export(path: Path) -> None:
    rows = [
        ("Hydraulic Pump", 2026, "March", 78000, 982800),
        ("Subsea Cable", 2026, "March", 21000, 924000),
        ("Valve Assembly", 2026, "March", 100000, 820000),
        ("ROV Thruster", 2026, "March", 4100, 623200),
        ("Sensor Kit", 2026, "March", 9488, 294128),
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Product", "Year", "Month", "Sum of Quantity", "Stock Value"])
        w.writerows(rows)


SALES = {
    ("North", "2026-01"): 412_500.0, ("North", "2026-02"): 398_200.0, ("North", "2026-03"): 455_900.0,
    ("South", "2026-01"): 610_000.0, ("South", "2026-02"): 587_400.0, ("South", "2026-03"): 640_100.0,
    ("East", "2026-01"): 222_300.0, ("East", "2026-02"): 231_800.0, ("East", "2026-03"): 245_600.0,
    ("West", "2026-01"): 305_700.0, ("West", "2026-02"): 318_900.0, ("West", "2026-03"): 299_400.0,
}


def tableau_sales(path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Region", "Month of Order Date", "SUM(Sales)"])
        for (region, month), value in SALES.items():
            w.writerow([region, month, f"{value:,.2f}"])


def finance_sales(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sales by Region"
    ws.append(["Region", "Period", "Sales (£k)"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for (region, month), value in SALES.items():
        ws.append([region, month, round(value / 1000, 1)])
    wb.save(path)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    stock_ledger(OUT / "finance_stock_ledger_mar2026.xlsx")
    powerbi_export(OUT / "powerbi_export_stock_value.csv")
    tableau_sales(OUT / "tableau_sales_by_region.csv")
    finance_sales(OUT / "finance_sales_by_region.xlsx")
    for p in sorted(OUT.iterdir()):
        print(f"wrote {p}")


if __name__ == "__main__":
    main()
