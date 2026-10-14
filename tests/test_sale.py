from datetime import date

from openpyxl import Workbook

from bindal.sale import sjit
from bindal.sale.workbook import SaleWorkbook

BLOCKS = ["AZ/FX/FBA", "SJIT"]


def month_sheet(wb, title, year, month, dated_days, rows=31):
    ws = wb.create_sheet(title)
    ws["A1"] = date(year, month, 1)
    col = 2
    for block in BLOCKS:
        ws.cell(2, col, block)
        for i, metric in enumerate(("GROSS", "RETURN", "CANCEL", "NET")):
            ws.cell(3, col + i, metric)
        col += 4
    for i in range(rows):
        ws.cell(4 + i, 1, date(year, month, i + 1) if i < dated_days else None)
    ws.cell(4 + rows, 1, "TOTAL")
    return ws


def test_march_sheet_from_february_has_31_days(tmp_path):
    wb = Workbook()
    wb.remove(wb.active)
    month_sheet(wb, "JAN-26", 2026, 1, 31)
    month_sheet(wb, "FEB-26", 2026, 2, 28)   # 31 rows, last 3 without a date
    path = tmp_path / "sale.xlsx"
    wb.save(path)
    book = SaleWorkbook(path)
    assert book.write(date(2026, 3, 31), "SJIT", "GROSS", 5)
    assert book.read(date(2026, 3, 31), "SJIT", "GROSS") == 5


def test_sjit_parse_any_header_case():
    csv_text = ("Order Line Id,Created On,PO_Type,Order Status,Cancelled On\n"
                "1,2026-10-01 10:00:00,SJIT,C,\n"
                "2,2026-10-01 11:00:00,PPMP,C,\n"
                "3,2026-10-01 12:00:00,FBM,F,2026-10-02 09:00:00\n")
    gross, cancel, covered = sjit.parse([("r.csv", csv_text.encode())])
    assert gross[date(2026, 10, 1)] == 2
    assert cancel[date(2026, 10, 2)] == 1
    assert date(2026, 10, 1) in covered
