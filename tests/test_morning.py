from datetime import date

from bindal.morning import pipeline as m


def test_channel_line_names():
    assert m.channel_line("BINDAL EXPORTS LIMITED - MYNTRA") == "Myntra MAIN"
    assert m.channel_line("BINDAL EXPORTS LIMITED - Myntra Fashfun Murcia CC") == "Myntra FMCC"
    assert m.channel_line("BINDAL EXPORTS LIMITED - Flipkart") == "Flipkart"


def test_change():
    assert m.change(130, 200) == "-35%" and m.change(5, 0) == "new" and m.change(0, 0) == "-"


def test_table_orders_lines_and_totals():
    d, b = date(2026, 10, 19), date(2026, 10, 12)
    gross = {"Flipkart": {d: 223, b: 557}, "Myntra MAIN": {d: 130, b: 199}, "Snapdeal": {d: 0, b: 0}}
    canc = {"Flipkart": {d: 21}, "Myntra MAIN": {d: 12}}
    t = m.table(gross, canc, d, b)
    lines = t.split("<pre>")[1].split("</pre>")[0].splitlines()
    assert lines[0].split() == ["Channel", "Kal", "Mon", "±"]
    assert lines[1].startswith("Myntra MAIN") and lines[2].startswith("Flipkart")   # ORDER, not alphabetical
    assert not any(l.startswith("Snapdeal") for l in lines)                          # 0 both days: left out
    assert lines[-1].split() == ["Total", "353", "756", "-53%"]
    assert "Cancel kal: <b>33</b> (9%)" in t
