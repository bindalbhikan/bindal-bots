from datetime import date, datetime

from bindal.weekly import pipeline as p

NAMES = {"1": "BINDAL EXPORTS LIMITED - Flipkart", "2": "BINDAL EXPORTS LIMITED - MYNTRA"}


def _line(day, channel, qty, skus, status="Delivered", value=100.0):
    return {"day": day, "channel": channel, "qty": qty, "skus": skus, "status": status, "value": value}


def test_weeks():
    this, last = p.weeks(date(2026, 10, 19))
    assert this[0] == date(2026, 10, 18) and this[-1] == date(2026, 10, 12)
    assert last[0] == date(2026, 10, 11) and last[-1] == date(2026, 10, 5)


def test_channels_styles_and_packers():
    this, last = p.weeks(date(2026, 10, 19))
    now, before = date(2026, 10, 15), date(2026, 10, 8)
    lines = [_line(now, "1", 4, [("A-1-M", 4)], value=400), _line(now, "1", 1, [("A-1-L", 1)], "Cancelled"),
             _line(before, "1", 2, [("A-1-M", 2)], value=200), _line(before, "2", 12, [("B-2-S", 12)]),
             _line(now, "2", 3, [("B-2-S", 3)]), _line(date(2026, 10, 1), "2", 99, [("C-3-M", 99)])]
    ch = p.by_channel(lines, NAMES, this, last)
    assert ch[0] == ("Flipkart", 5, 2, 500.0, 200.0, 20) and ch[1][:3] == ("Myntra MAIN", 3, 12)
    top, fell = p.by_style(lines, this, last)
    assert top == [("A-1", 4, 2), ("B-2", 3, 12)]          # cancelled left out
    assert fell == [("B-2", 3, 12)]                         # 10+ last week and lower now
    erp = [{"Created On": datetime(2026, 10, 15, 10), "Created By": "RAHUL"},
           {"Created On": datetime(2026, 10, 7, 10), "Created By": "RAHUL"},
           {"Created On": datetime(2026, 10, 7, 11), "Created By": "SHEKHAR"}, {"Created On": None}]
    assert p.packers(erp, this, last) == [("RAHUL", 1, 1), ("SHEKHAR", 0, 1)]
    text = p.message(ch, top, fell, p.packers(erp, this, last), this, last)
    assert "Weekly review · 12 Oct - 18 Oct" in text and "• B-2 3 / 12" in text and "• RAHUL 1 / 1" in text


def test_rupees():
    assert p.rupees(3_009_000) == "30.09L" and p.rupees(43_000) == "43K"
