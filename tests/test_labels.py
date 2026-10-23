from datetime import date, datetime

from bindal.core import telegram
from bindal.labels import pipeline as p

NAMES = {"37863": "BINDAL EXPORTS LIMITED - Flipkart", "45111": "BINDAL EXPORTS LIMITED - MYNTRA"}


def _print(awb, when, by="RAHUL"):
    return {"Order ID": awb, "Created On": when, "Created By": by}


def _line(awb, status, day=date(2026, 10, 19), channel="37863"):
    return {"awb": awb, "status": status, "day": day, "ordered": datetime.combine(day, datetime.min.time()),
            "channel": channel}


def test_first_prints_keeps_the_earliest():
    rows = [_print("a1", datetime(2026, 10, 20, 11)), _print("A1", datetime(2026, 10, 19, 15), "SHEKHAR"),
            _print("", datetime(2026, 10, 19)), {"Order ID": "B2", "Created On": None}]
    got = p.first_prints(rows)
    assert list(got) == ["A1"] and got["A1"]["Created By"] == "SHEKHAR"


def test_check_finds_the_three_mismatches():
    since, rts_from = datetime(2026, 10, 19), date(2026, 10, 17)
    prints = p.first_prints([
        _print("C1", datetime(2026, 10, 20, 10)),                  # printed, order cancelled
        _print("N1", datetime(2026, 10, 20, 10), "SHEKHAR"),       # printed, still Packed
        _print("OK", datetime(2026, 10, 19, 12)),                  # printed, ready to ship: fine
        _print("OLD", datetime(2026, 10, 16, 12)),                 # printed before `since`: not checked
    ])
    lines = [_line("C1", "Cancel Init"), _line("N1", "Packed", channel="45111"), _line("OK", "Ready to ship"),
             _line("OLD", "Cancelled"), _line("R1", "Ready to ship"),            # RTS, no print
             _line("R0", "Ready to ship", day=date(2026, 10, 15)),               # too old to ask
             _line("", "Ready to ship")]                                          # no AWB
    res = p.check(prints, lines, since, rts_from)
    assert res["recent"] == 3 and res["matched"] == 3
    assert [l["awb"] for _, l in res["cancelled"]] == ["C1"]
    assert [l["awb"] for _, l in res["not_rts"]] == ["N1"]
    assert [l["awb"] for l in res["no_label"]] == ["R1"]
    assert res["packers"] == {"RAHUL": 2, "SHEKHAR": 1} and res["packer_cancel"] == {"RAHUL": 1}
    text = telegram.render(p.message(res, NAMES, datetime(2026, 10, 20, 18), since, rts_from))
    assert "Printed for cancelled orders: 1" in text and "• C1 · Flipkart · Cancel Init · RAHUL 20 Oct 10:00" in text
    assert "Printed, not ready to ship in OMSGuru: 1" in text and "Myntra MAIN" in text
    assert "Ready to ship in OMSGuru, no ERP label: 1" in text and "No mismatch" not in text


def test_message_all_clear():
    res = p.check({}, [], datetime(2026, 10, 19), date(2026, 10, 17))
    assert "No mismatch" in telegram.render(
        p.message(res, NAMES, datetime(2026, 10, 20, 18), datetime(2026, 10, 19), date(2026, 10, 17)))
