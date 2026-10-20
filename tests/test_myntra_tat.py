from datetime import date, datetime

from bindal.myntra_tat import pipeline as p

CSV = b"""order line id,order tracking number,courier code,order status,created on,packed on,fmpu date,cancelled on
1,mysp1,FLIPKARTLOGISTICS,SH,2026-10-15 10:00:00.000,2026-10-15 15:00:00.000,2026-10-16 11:00:00.000,
2,MYSP2,FLIPKARTLOGISTICS,SH,2026-10-15 11:00:00.000,2026-10-15 15:00:00.000,2026-10-17 18:00:00.000,
3,MYSP3,SHADOWFAX,PK,2026-10-15 12:00:00.000,2026-10-17 20:00:00.000,,
4,MYSP4,SHADOWFAX,F,2026-10-15 12:00:00.000,,,2026-10-15 13:00:00.000
5,MYSP5,SHADOWFAX,SH,2026-10-10 12:00:00.000,2026-10-10 15:00:00.000,2026-10-11 11:00:00.000,
1,MYSP1,FLIPKARTLOGISTICS,SH,2026-10-15 10:00:00.000,2026-10-15 15:00:00.000,2026-10-16 11:00:00.000,
"""


def test_score_and_message():
    rep = p.report_frame([("r.csv", CSV)], "DBCC")
    assert len(rep) == 5                                           # order line 1 twice: once
    sla = {f"MYSP{i}": datetime(2026, 10, 16, 23, 59) for i in range(1, 6)}
    df = p.score(rep, sla, date(2026, 10, 12), date(2026, 10, 18), datetime(2026, 10, 20, 18))
    assert sorted(df["awb"]) == ["MYSP1", "MYSP2", "MYSP3"]       # cancelled and out-of-window left out
    got = df.set_index("awb")
    assert got.loc["MYSP1", "on_time"] and not got.loc["MYSP1", "late"]
    assert got.loc["MYSP2", "late"] and not got.loc["MYSP2", "ours"]      # picked late: courier
    assert got.loc["MYSP3", "late"] and got.loc["MYSP3", "ours"]          # packed after the SLA: ours
    text = p.message(df, date(2026, 10, 12), date(2026, 10, 18), 3, 4)
    table = text.split("<pre>")[1].split("</pre>")[0].splitlines()
    assert table[1].split() == ["DBCC", "FLIPKAR", "2", "1", "50%"] and table[-1].split() == ["Total", "3", "2", "33%"]
    assert "Late: <b>2</b> · our side (packed after the SLA) 1" in text
