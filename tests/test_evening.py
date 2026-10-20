from datetime import date, datetime

from bindal.evening import pipeline as p


def _line(day, hour, channel, qty, status="New"):
    return {"day": day, "ordered": datetime.combine(day, datetime.min.time()).replace(hour=hour),
            "channel": channel, "qty": qty, "status": status}


def test_sale_table_compares_up_to_the_same_time():
    today, before = date(2026, 10, 20), date(2026, 10, 13)
    names = {"1": "BINDAL EXPORTS LIMITED - Flipkart", "2": "BINDAL EXPORTS LIMITED - MYNTRA"}
    lines = [_line(today, 9, "1", 5), _line(today, 10, "2", 3, "Cancelled"),
             _line(before, 9, "1", 10), _line(before, 20, "1", 50),      # after 4 PM last week: not counted
             _line(date(2026, 10, 19), 9, "1", 99)]                      # another day: not counted
    table, total, cancel = p.sale_table(lines, names, today, before, datetime(2026, 10, 20, 16, 0))
    rows = table.splitlines()
    assert rows[1].split() == ["Flipkart", "5", "10", "-50%"]
    assert rows[2].split() == ["Myntra", "MAIN", "3", "0", "new"]
    assert rows[-1].split() == ["Total", "8", "10", "-20%"] and total == 8 and cancel == 3


def test_local_day_is_ist():
    assert p._local_day("2026-10-19T20:00:00Z") == date(2026, 10, 20)     # 1:30 AM IST
    assert p._local_day(None) is None
