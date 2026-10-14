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


def test_cocoblu_has_its_own_block_and_snapdeal_is_ignored():
    from bindal.sale.oms_sales import IGNORE, OMS_BLOCKS, block_for
    assert block_for("BINDAL EXPORTS LIMITED - COCOBLU") == "COCOBLU"
    assert block_for("BINDAL EXPORTS LIMITED - Amazon") == "AZ/FX/FBA"
    assert block_for("BINDAL EXPORTS LIMITED - Snapdeal") == IGNORE
    assert "COCOBLU" in OMS_BLOCKS and IGNORE not in OMS_BLOCKS


def test_move_blocks_adds_cocoblu_after_amazon_and_drops_snapdeal(tmp_path):
    wb = Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("OCT-26")
    ws["A1"] = date(2026, 10, 1)
    blocks = ["AZ/FX/FBA", "MEESHO", "SNAPDEAL", "TOTAL"]
    for i, block in enumerate(blocks):
        ws.cell(2, 2 + 4 * i, block)
        for k, m in enumerate(("GROSS", "RETURN", "Cancel", "NET")):
            ws.cell(3, 2 + 4 * i + k, m)
    ws.cell(3, 18, "Target")
    for d in range(31):
        ws.cell(4 + d, 1, date(2026, 10, d + 1))
        ws.cell(4 + d, 2, 5)          # AZ gross
        ws.cell(4 + d, 6, 7)          # MEESHO gross
        ws.cell(4 + d, 7, 1)          # MEESHO return (by hand)
    ws.cell(35, 1, "TOTAL")
    path = tmp_path / "sale.xlsx"
    wb.save(path)
    book = SaleWorkbook(path)
    book.move_blocks(book.wb["OCT-26"], "COCOBLU", "AZ/FX/FBA", "SNAPDEAL")
    layout, _, _ = book.layout(book.wb["OCT-26"])
    assert list(layout) == ["AZ/FX/FBA", "COCOBLU", "MEESHO", "TOTAL"]
    assert layout["COCOBLU"]["GROSS"] == 6 and layout["MEESHO"]["GROSS"] == 10
    assert book.read(date(2026, 10, 3), "MEESHO", "GROSS") == 7
    assert book.read(date(2026, 10, 3), "MEESHO", "RETURN") == 1
    assert book.wb["OCT-26"].cell(3, 18).value == "Target"
    assert book.has_block(date(2026, 10, 3), "COCOBLU")
