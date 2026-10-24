from datetime import datetime


def test_otp_message_has_every_account_trip_and_courier(monkeypatch):
    from bindal.myntra_otp import pipeline as p
    from bindal.portals import myntra as mp
    monkeypatch.setattr(mp, "save_to_worker", lambda *a: None)

    class Portal:
        def __init__(self, account):
            self.account = account

        def otc(self, warehouse, trip):
            if self.account == "FMCC":
                return [{"courier": "MYS", "otc": "111111", "message": "Success"},
                        {"courier": "MYE", "otc": None, "message": "No Active Tripsheets"}]
            return [{"courier": "MYS", "otc": f"{warehouse}"[:4] + ("01" if trip == "PICKUP" else "02"), "message": "Success"},
                    {"courier": "MYE", "otc": "222222", "message": "Success"}]

    def portal_of(account):
        if account == "DBCC":
            raise mp.SessionExpired("no DBCC session saved")
        return Portal(account)

    rows, couriers, notes = p.read_codes(portal_of)
    assert couriers == ["MYS", "MYE"]
    assert [(c, t) for c, t, _ in rows] == [("TLPL", "Pickup"), ("TLPL", "Return"), ("BOAT", "Pickup"), ("BOAT", "Return")]
    text = p.message(rows, couriers, notes, datetime(2026, 10, 24, 11, 0))
    table = text.split("<pre>")[1].split("</pre>")[0].splitlines()
    assert table[0].split() == ["Acct", "Trip", "MYS", "MYE"]
    assert table[1].split() == ["TLPL", "Pickup", "461401", "222222"]
    assert table[3].split() == ["BOAT", "Pickup", "111111", "-"]
    assert all(len(line) <= 28 for line in table)
    assert "MMRJ: not read" in text and "24 Oct 2026" in text
