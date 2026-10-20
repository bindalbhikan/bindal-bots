from collections import Counter
from datetime import date

from bindal.stock import pipeline as p


def test_style_size():
    assert p.style_size("COORD-AE-1-N10-L") == ("COORD-AE-1-N10", "L")
    assert p.style_size("DRESS-WW-B16-XXL") == ("DRESS-WW-B16", "XXL")
    assert p.style_size("KURTA-12-38") == ("KURTA-12", "38")
    assert p.style_size("COMBO-PACK") == ("COMBO-PACK", "")


def test_stock_and_sold():
    csv = b"DropshipWarehouseId,Item SkuCode,InventoryAction,QtyIncludesBlocked,Qty\n1,a-1-m,RESET,No,5\n1,A-1-L,RESET,No,0\n"
    assert p.stock_of([csv, csv]) == Counter({"A-1-M": 10, "A-1-L": 0})
    lines = [{"status": "Ready to ship", "skus": [("A-1-M", 2), ("A-1-L", 1)]},
             {"status": "Cancelled", "skus": [("A-1-M", 5)]}, {"status": "New", "skus": []}]
    assert p.sold_of(lines) == Counter({"A-1-M": 2, "A-1-L": 1})


def test_at_risk_and_message():
    sold = Counter({"A-1-M": 28, "A-1-L": 14, "A-1-S": 14, "B-2-XL": 3, "COMBO": 50})
    stock = Counter({"A-1-M": 3, "A-1-L": 0, "A-1-S": 30, "B-2-XL": 0})
    risk = p.at_risk(sold, stock, days=14, min_sold=7, cover_days=3)
    assert [r["sku"] for r in risk] == ["A-1-M", "A-1-L"]        # S has 30 days; B sold too little; combo unknown
    assert risk[0]["days_left"] == 1.5 and risk[1]["stock"] == 0
    text = p.message(risk, 14, 3, date(2026, 10, 20))
    assert "Out of stock: <b>1</b> SKUs (about 1 units a day not sold) · running out: <b>1</b>" in text
    assert "• <b>A-1</b> · sells 3.0/day · M 3 (1.5d), L 0 (OUT)" in text      # sizes in size order
