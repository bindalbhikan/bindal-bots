from collections import Counter

from bindal.deadstock import pipeline as p


def test_classify_dead_and_slow():
    stock = Counter({"A-1-M": 20, "A-1-L": 3, "B-2-S": 100, "C-3-M": 30})
    sold = Counter({"B-2-S": 30, "C-3-M": 90})          # 90 days: B lasts 300 days, C 30 days
    dead, slow = p.classify(stock, sold, days=90, min_stock=5, slow_days=180)
    assert [r["sku"] for r in dead] == ["A-1-M"]           # A-1-L has under 5 in stock
    assert [r["sku"] for r in slow] == ["B-2-S"] and slow[0]["days_left"] == 300
    part = p.section("Dead", dead, slow=False)
    assert part.title == "<b>Dead: 20 units in 1 SKUs, 1 styles</b>" and "• <b>A-1</b> 20 units · M 20" in part.lines[0]
    assert part.rows == [{"Style": "A-1", "SKU": "A-1-M", "Size": "M", "Stock": 20, "Sold": 0}]
