from datetime import date, datetime

from openpyxl import Workbook


def test_ajio_claim_tat_sheet():
    from bindal.ajio_spf import master_engine as m
    assert m.claim_tat(date(2026, 10, 5), date(2026, 10, 1), None) == "WITHIN"
    assert m.claim_tat(date(2026, 10, 20), date(2026, 10, 1), None) == "OUTSIDE"
    assert m.claim_tat(date(2026, 10, 20), None, date(2026, 8, 1)) == "WITHIN"       # day 80
    assert m.claim_tat(date(2026, 10, 20), None, date(2026, 6, 1)) == "OUTSIDE"      # day 141
    assert m.claim_tat(None, None, date(2026, 6, 1)) == ""
    heads = ["TICKET ID", "TICKET DATE", "POB ID", "Return QTY", "FWD B2B Invoice Amt", "G4 PAYMENT",
             "IC REVERSE", "STATUS", "Delivery Challan Date", "Return Created Date"]
    index = {h: i for i, h in enumerate(heads)}
    records = [
        [791, datetime(2026, 10, 5, 11, 0), "DV1", 1, 400.0, 400.0, None, "RESOLVED", datetime(2026, 10, 1), datetime(2026, 9, 1)],
        [792, datetime(2026, 10, 20), "DV1", 2, 300.0, None, None, "REJECTED", datetime(2026, 10, 1), datetime(2026, 9, 1)],
        [793, None, "DV1", 1, 100.0, None, None, "IN_PROGRESS", None, datetime(2026, 7, 1)],
        [None, None, "DV1", 1, 999.0, None, None, None, None, None],
    ]
    wb = Workbook()
    m.write_claim_tat(wb, records, index)
    rows = list(wb[m.CLAIM_TAT_SHEET].values)
    head = rows[0]
    got = {(r[0], r[1]): dict(zip(head, r)) for r in rows[1:] if r[0]}
    oct_ = got[("DV1", "2026-10")]
    assert (oct_["CLAIMS RAISED"], oct_["CLAIM QTY"], oct_["CLAIM AMT"]) == (2, 3, 700)
    assert (oct_["WITHIN TAT"], oct_["OUTSIDE TAT QTY"], oct_["PAID"], oct_["G4 PAID AMT"], oct_["REJECTED"]) == (1, 2, 1, 400, 1)
    assert got[("DV1", "NO DATE")]["OPEN"] == 1 and got[("ALL", "GRAND TOTAL")]["CLAIMS RAISED"] == 3
