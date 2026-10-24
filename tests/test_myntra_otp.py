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
    lines = text.splitlines()
    assert "<pre>" not in text                                   # plain lines: pastes cleanly into WhatsApp
    i = lines.index("<b>TLPL</b> (Myntra Main)")
    assert lines[i + 1] == "Pickup: EK: 222222 | FL: 461401"
    assert lines[i + 2] == "Return: EK: 222222 | FL: 461402"
    j = lines.index("<b>BOAT</b> (Myntra FF X MUR CC)")
    assert lines[j + 1] == "Pickup: EK: no trip | FL: 111111"
    assert "MMRJ: not read" in text and "24 Oct 2026" in text


def test_sheet_rows_in_the_team_layout_without_repeats():
    from bindal.myntra_otp import pipeline as p
    rows = [("TLPL", "Return", {"MYS": "012345", "MYE": "856240"}), ("BOAT", "Pickup", {"MYS": "111111", "MYE": "-"})]
    existing = [["DATE", "PORTAL", "COUNT", "OTP", "STATUS", "Name", ""],
                ["24-10-2026", "Myntra_Main_TLPL", "17", "856240", "RETURN", "Bhikan", ""]]      # typed by hand
    new = p.sheet_rows(rows, datetime(2026, 10, 24, 11, 0), existing)
    assert new == [["24-10-2026", "Myntra_Main_TLPL", "", "012345", "RETURN", "Bhikan", "FL"],
                   ["24-10-2026", "Myntra_FF X MUR CC_BOAT", "", "111111", "PICKUP", "Bhikan", "FL"]]
    assert p.sheet_rows(rows, datetime(2026, 10, 24), existing + new) == []


def test_retry_sends_only_new_codes(monkeypatch):
    from types import SimpleNamespace
    from bindal.myntra_otp import pipeline as p
    sent = []
    monkeypatch.setattr(p, "read_codes", lambda: ([("TLPL", "Pickup", {"MYE": "1234"})], ["MYE"], []))
    monkeypatch.setattr(p.telegram, "send", lambda text: sent.append(text))
    args = SimpleNamespace(retry=True, no_sheet=False, no_telegram=False)
    monkeypatch.setattr(p, "write_sheet", lambda rows, now: (0, True))     # 10 AM already had it
    assert p.run(args) == 0 and sent == []
    monkeypatch.setattr(p, "write_sheet", lambda rows, now: (1, True))     # a code came after 10 AM
    p.run(args)
    assert len(sent) == 1 and "11 AM update" in sent[0] and "1234" in sent[0]
