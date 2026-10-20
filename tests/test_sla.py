from datetime import datetime

from bindal.sla import pipeline as p

NAMES = {"45111": "BINDAL EXPORTS LIMITED - MYNTRA", "57285": "BINDAL EXPORTS LIMITED - Ajio Api"}


def _line(order, status, sla, channel="45111"):
    return {"order": order, "status": status, "sla": sla, "channel": channel}


def test_at_risk_keeps_open_orders_due_by_tonight():
    now = datetime(2026, 10, 20, 12, 0)
    lines = [_line("A1", "New", datetime(2026, 10, 20, 11, 59)),               # overdue
             _line("B2", "Packed", datetime(2026, 10, 20, 16, 0), "57285"),   # due later today
             _line("C3", "Ready to ship", datetime(2026, 10, 20, 9, 0)),      # done: out
             _line("D4", "Pending", datetime(2026, 10, 21, 12, 0)),           # tomorrow: out
             _line("E5", "Processing", datetime(2026, 10, 19, 23, 0)),        # overdue since yesterday
             _line("F6", "New", None)]                                        # no SLA: out
    assert [r["order"] for r in p.at_risk(lines, now)] == ["E5", "A1", "B2"]


def test_message_counts_and_lists():
    now = datetime(2026, 10, 20, 12, 0)
    risk = p.at_risk([_line("A1", "New", datetime(2026, 10, 20, 11, 59)),
                      _line("B2", "Packed", datetime(2026, 10, 20, 16, 0), "57285"),
                      _line("E5", "Processing", datetime(2026, 10, 19, 23, 0))], now)
    text = p.message(risk, NAMES, now)
    table = text.split("<pre>")[1].split("</pre>")[0].splitlines()
    assert table[1].split() == ["Myntra", "MAIN", "2", "0"] and table[-1].split() == ["Total", "2", "1"]
    assert "• A1 · New · <b>OVER</b>" in text and "• B2 · Packed · due 04:00 PM" in text
    assert text.index("<b>Myntra MAIN</b>") < text.index("<b>Ajio</b>")
