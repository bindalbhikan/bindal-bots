from datetime import date

from bindal.mis import pipeline as p

NAMES = {"1": "BINDAL EXPORTS LIMITED - Flipkart", "2": "BINDAL EXPORTS LIMITED - Myntra DressBerry CC",
         "3": "BINDAL EXPORTS LIMITED - Nykaa Fashion"}


def _line(day, channel, qty, value, status="Delivered"):
    return {"day": day, "channel": channel, "qty": qty, "value": value, "status": status}


def test_months():
    now, prev = p.months(date(2026, 10, 2))
    assert (now[0], now[-1]) == (date(2026, 9, 1), date(2026, 9, 30))
    assert (prev[0], prev[-1]) == (date(2026, 8, 1), date(2026, 8, 31))


def test_mis_with_and_without_rates():
    now, prev = p.months(date(2026, 10, 2))
    lines = [_line(date(2026, 9, 5), "1", 10, 5000), _line(date(2026, 9, 6), "1", 2, 1000, "Cancelled"),
             _line(date(2026, 8, 5), "1", 8, 4000), _line(date(2026, 9, 7), "2", 4, 3000),
             _line(date(2026, 9, 8), "3", 1, 900), _line(date(2026, 7, 1), "3", 50, 99999)]
    rates = {"Flipkart": {"commission": 20, "shipping": 50}, "Myntra": {"commission": 25, "shipping": 0}}
    rows = {r["channel"]: r for r in p.mis(lines, NAMES, now, prev, rates)}
    f = rows["Flipkart"]
    assert (f["units"], f["units_prev"], f["value"], f["cancel_pct"], f["net_sale"]) == (12, 8, 6000, 17, 5000)
    assert (f["commission"], f["shipping"], f["net"]) == (1000, 500, 3500)       # 10 kept units x 50
    assert rows["Myntra DBCC"]["commission"] == 750                              # "Myntra" covers every account
    assert "net" not in rows["Nykaa"]
    text = p.message(list(rows.values()), now, prev, rates)
    assert "Monthly MIS · September 2026" in text and "No commission / shipping rate for: Nykaa" in text
    assert "After commission and shipping" in text
