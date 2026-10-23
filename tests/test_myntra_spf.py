from bindal.myntra_spf.pipeline import final_status


def test_final_status_matches_the_master_formula():
    assert final_status({"SPF PAID": 120}) == "SPF Paid"
    assert final_status({"OMSGURU STATUS": "Return Received ERP"}) == "Return Received"
    assert final_status({"MANUAL RETURN STATUS": "Pickup Not Done"}) == "Return Received"
    assert final_status({"ORDER SETTLEMENT": 450.5}) == "Payment Received"
    assert final_status({"ORDER SETTLEMENT": 450.5, "RETURN DEBIT": -450.5}) == ""
    assert final_status({"OMSGURU STATUS": "Return Init"}) == ""      # blank: looked up in OMSGuru


def test_formula_linking_another_workbook_reads_as_empty():
    from bindal.myntra_spf.engine import clean
    assert clean("=VLOOKUP(G5,[1]Orders!$E:$F,2,)") is None
    assert clean("Return Received ERP") == "Return Received ERP"


def test_young_in_transit_rows_are_parked_and_read_back(tmp_path, monkeypatch):
    import datetime as dt
    import importlib
    monkeypatch.setenv("MYNTRA_SPF_DIR", str(tmp_path))
    from bindal.myntra_spf import engine
    engine = importlib.reload(engine)
    today = dt.datetime.combine(dt.date.today(), dt.time())
    young = {"order_line_id": "111", "return_id": "R1", "type": "Return", "status": "Ret Created",
             "return_tracking_number": "AWB1", "return_created_date": today - dt.timedelta(days=59)}
    old = dict(young, order_line_id="222", return_id="R2",
               return_created_date=today - dt.timedelta(days=60))
    data = {"MAIN": [young, old], "DBCC": [], "FMCC": []}
    engine.drop_young_in_transit(data, engine.argparse.Namespace(in_transit="n", dry_run=False))
    assert [r["order_line_id"] for r in data["MAIN"]] == ["222"]
    engine.write_master(data)
    back = engine.read_old_master()
    assert {k[0] for k in back["MAIN"]} == {"111", "222"}


def test_claims_follow_the_policy_timelines():
    import datetime as dt
    from bindal.myntra_spf.tickets import DELAYED, RNR, claim_of
    today = dt.date(2026, 10, 23)
    ago = lambda d: dt.datetime.combine(today - dt.timedelta(days=d), dt.time())  # noqa: E731
    delivered = {"order_line_id": 1, "model": "PPMP", "status": "Ret Delivered"}
    assert claim_of(dict(delivered, deliver_to_seller_date=ago(3)), today, set()) is None
    assert claim_of(dict(delivered, deliver_to_seller_date=ago(4)), today, set()) == RNR
    assert claim_of(dict(delivered, deliver_to_seller_date=ago(7)), today, set()) == RNR
    assert claim_of(dict(delivered, deliver_to_seller_date=ago(8)), today, set()) is None
    transit = {"order_line_id": 2, "model": "PPMP", "status": "Ret Created"}
    assert claim_of(dict(transit, return_created_date=ago(60)), today, set()) is None
    assert claim_of(dict(transit, return_created_date=ago(61)), today, set()) == DELAYED
    assert claim_of(dict(transit, return_created_date=ago(120)), today, set()) == DELAYED
    assert claim_of(dict(transit, return_created_date=ago(121)), today, set()) is None
    due = dict(transit, return_created_date=ago(70))
    assert claim_of(due, today, {"2"}) is None                              # ticket on the portal
    assert claim_of(dict(due, **{"TICKET ID": 9924390}), today, set()) is None
    assert claim_of(dict(due, model="SJIT"), today, set()) is None
    assert claim_of(dict(due, **{"OMSGURU STATUS": "Return Received ERP"}), today, set()) is None
    assert claim_of(dict(due, **{"RETURN POD": "YES"}), today, set()) is None


def test_claims_are_raised_in_bulk_with_their_real_ticket_ids(tmp_path, monkeypatch):
    from openpyxl import load_workbook
    from bindal.myntra_spf import tickets
    monkeypatch.setenv("MYNTRA_SPF_RESOLVE_MINUTES", "0")

    class Portal:
        calls = []

        def spf_bulk_claim(self, ids, cat, desc, seller=None):
            self.calls.append((list(ids), cat, seller))
            return 777

        def spf_tickets(self, since=None, page_size=500):
            assert since is not None
            return [{"ticketId": 9900011, "orderLineId": 11, "issueCategory": tickets.DELAYED, "status": "OPEN",
                     "createdOn": "2026-10-23T06:26:57.000Z", "lastModifiedOn": "2026-10-23T06:26:57.000Z"}]

    portal, details = Portal(), []
    lines = tickets.raise_claims(portal, "DBCC", {tickets.DELAYED: ["11", "12"]}, tmp_path, True, seller=32207,
                                 details=details)
    assert portal.calls == [(["11", "12"], tickets.DELAYED, 32207)]
    assert lines == ["DBCC Delayed Returns: 2 raised (job 777)",
                     "DBCC: 1 claims raised but no ticket id yet (BOT JOB, next run)"]
    new = list(load_workbook(tmp_path / "DBCC" / tickets.NEW_FILE).active.values)
    assert new[0][:2] == ("ticketId", "orderLineId") and new[1][:2] == (9900011, 11)
    bot = list(load_workbook(tmp_path / "DBCC" / tickets.BOT_FILE).active.values)
    assert [r[:2] for r in bot[1:]] == [("BOT JOB 777", "12")]
    assert [(d["TICKET ID"], d["STATUS"], d["CREATED"]) for d in details] == [("9900011", "OPEN", "23-Oct-2026 11:56")]
    off = tickets.raise_claims(None, "MAIN", {tickets.RNR: ["5"]}, tmp_path, False)
    assert off == ["MAIN Return Not Received: 1 due (not raised: tickets off)"]


def test_spf_tickets_pages_of_500_and_a_failed_page_is_asked_again(monkeypatch):
    from bindal.portals import myntra as mp
    monkeypatch.setattr(mp.time, "sleep", lambda s: None)
    portal = mp.MyntraPortal.__new__(mp.MyntraPortal)
    calls = []

    def call(method, url, params):
        p = dict(params)
        calls.append(p["page"])
        if len(calls) == 2:
            raise TimeoutError("timed out after 60002 ms")
        rows = [{"orderLineId": f"{p['page']}-{i}"} for i in range(500 if p["page"] == 0 else 200)]
        return {"data": rows, "status": {"totalCount": 700}}

    portal._call = call
    rows = portal.spf_tickets()
    assert len(rows) == 700 and calls == [0, 1, 1]                 # page 1 failed once, asked again


def test_accepted_spf_ticket_is_a_final_status():
    from bindal.myntra_spf import engine
    from bindal.myntra_spf.pipeline import final_status
    for st in ("ACCEPT", "INVOICE_INITIATED", "PAYMENT_COMPLETED"):
        assert final_status({"STATUS": st, "TICKET ID": 9}) == "SPF Accepted"
        assert engine.is_settled({"STATUS": st, "TICKET ID": 9})
        assert final_status({"STATUS": st}) == "" and not engine.is_settled({"STATUS": st})   # no ticket id
    assert final_status({"STATUS": "PAYMENT_COMPLETED", "SPF PAID": 500}) == "SPF Paid"
    assert final_status({"STATUS": "REJECT"}) == "" and not engine.is_settled({"STATUS": "REJECT"})
    f = engine.build_formulas(5)["FINAL RETURN STATUS"]
    st = engine.C["STATUS"] + "5"
    tid = engine.C["TICKET ID"] + "5"
    assert f'IF(AND({tid}<>"",OR({st}="ACCEPT",{st}="INVOICE_INITIATED",{st}="PAYMENT_COMPLETED")),"SPF Accepted",' in f
    assert f.count("(") == f.count(")")
    assert ("SPF ACCEPTED", "count", None, [("FINAL RETURN STATUS", "SPF Accepted")]) in engine.SUMMARY_COLS


def test_ticket_details_rows_and_unchanged_tickets_are_not_read_again():
    from bindal.myntra_spf import tickets
    t = {"ticketId": 9508045, "orderLineId": 11309894985, "returnId": 100192640491, "orderId": 1,
         "issueCategory": tickets.RNR, "status": "REJECT", "createdOn": "2099-09-30T11:58:04.000Z",
         "lastModifiedOn": "2099-10-01T13:07:22.000Z"}
    claim = {"status": "AWAITING_SELLER_RESPONSE", "history": '{"OPEN": "09-30-2099 17:28:03", "REJECT": "10-01-2099 18:37:21"}',
             "validationReason": "Not_Eligible_ticket_raised_within_60_days_TAT", "fwTicketId": 55, "description": "Return Pending"}
    comments = [{"comment": ": <div>Dear Sir,<br>We are <b>checking</b>.</div>", "user": "Ranu Gour", "createdOn": "10-02-2099 11:56:47"},
                {"comment": "older", "user": "X", "createdOn": "10-01-2099 10:00:00"}]
    row = tickets.detail_row("MAIN", t, claim, comments)
    assert row["STATUS HISTORY"] == "OPEN 30-Sep 17:28 > REJECT 01-Oct 18:37"
    assert row["REASON"] == "Not_Eligible_ticket_raised_within_60_days_TAT" and row["NEEDS OUR REPLY"] == "YES"
    assert (row["REPLIES"], row["LAST REPLY BY"], row["LAST REPLY AT"]) == (2, "Ranu Gour", "02-Oct-2099 11:56")
    assert row["LAST REPLY"] == "Dear Sir, We are checking ." and row["CREATED"] == "30-Sep-2099 17:28"

    class Portal:
        reads = 0

        def spf_claim(self, tid):
            Portal.reads += 1
            return claim

        def spf_comments(self, fw):
            return comments

    old = {"9508045": {**row, "REASON": "cached"}}
    out = tickets.ticket_details(Portal(), "MAIN", [t], old)
    assert Portal.reads == 0 and out[0]["REASON"] == "cached"          # LAST UPDATED unchanged
    out = tickets.ticket_details(Portal(), "MAIN", [{**t, "lastModifiedOn": "2099-10-03T10:00:00.000Z"}], old)
    assert Portal.reads == 1 and out[0]["REASON"].startswith("Not_Eligible")
    assert tickets.ticket_details(Portal(), "MAIN", [{**t, "createdOn": "2020-01-01T00:00:00.000Z"}], {}) == []


def test_claim_tat_rows_count_within_and_outside_the_policy_window():
    import datetime as dt
    from bindal.myntra_spf import engine
    d = dt.datetime
    base = {"TICKET ID": 1, "ORDER AMOUNT": 100.0}
    data = {name: [] for name in engine.SHEETS}
    data["MAIN"] = [
        {**base, "_ticket_created": d(2026, 10, 5), "deliver_to_seller_date": d(2026, 10, 1), "STATUS": "ACCEPT"},
        {**base, "_ticket_created": d(2026, 10, 20), "deliver_to_seller_date": d(2026, 10, 1), "STATUS": "REJECT"},
        {**base, "_ticket_created": d(2026, 10, 23), "return_created_date": d(2026, 8, 1), "STATUS": "PAYMENT_COMPLETED",
         "SPF PAID": 90.0},                                                           # day 83: within
        {**base, "_ticket_created": d(2026, 9, 2), "return_created_date": d(2026, 8, 20), "STATUS": "OPEN"},  # day 13
        {"TICKET ID": None, "ORDER AMOUNT": 50.0},                                    # no claim
    ]
    rows = engine.claim_tat_rows(data)
    oct_, sep = rows[("MAIN", "2026-10")], rows[("MAIN", "2026-09")]
    assert (oct_["CLAIMS RAISED"], oct_["CLAIM AMT"], oct_["WITHIN TAT"], oct_["OUTSIDE TAT"]) == (3, 300.0, 2, 1)
    assert (oct_["ACCEPTED"], oct_["REJECTED"], oct_["PAID"], oct_["SPF PAID AMT"]) == (1, 1, 1, 90.0)
    assert (sep["OUTSIDE TAT"], sep["OPEN"], sep["OPEN AMT"]) == (1, 1, 100.0)
    assert ("SPF ACCEPTED AMT", "sum", "ORDER AMOUNT", [("FINAL RETURN STATUS", "SPF Accepted")]) in engine.SUMMARY_COLS


def test_portal_tickets_reads_recent_claims_again_and_keeps_cached_rows(tmp_path, monkeypatch):
    from bindal.myntra_spf import tickets
    monkeypatch.setattr(tickets, "ticket_details", lambda portal, account, rows, cached: [
        {"TICKET ID": str(t["ticketId"]), "REASON": "R"} for t in rows])
    old = {"ticketId": 1, "orderLineId": 11, "status": "REJECT", "createdOn": "2026-10-01T00:00:00.000Z"}
    new = {"ticketId": 2, "orderLineId": 12, "status": "OPEN", "createdOn": "2026-10-20T00:00:00.000Z"}

    class Portal:
        def spf_tickets(self, since=None):
            return [new, old] if since else [old, old]     # the full list missed #2 and doubled #1

    details = {}
    olis = tickets.portal_tickets(Portal(), "MAIN", tmp_path, {}, details)
    assert olis == {"11", "12"} and [d["TICKET ID"] for d in details["MAIN"]] == ["1", "2"]


def test_cached_ticket_rows_stay_when_the_list_misses_them():
    from bindal.myntra_spf import tickets

    class Portal:
        def spf_claim(self, tid):
            return {}

        def spf_comments(self, fw):
            return []

    cached = {"5": {"ACCOUNT": "MAIN", "TICKET ID": "5", "STATUS": "OPEN"},
              "6": {"ACCOUNT": "DBCC", "TICKET ID": "6"}}
    out = tickets.ticket_details(Portal(), "MAIN", [], cached)
    assert [d["TICKET ID"] for d in out] == ["5"]



def test_portal_ticket_list_beats_an_older_hand_export_of_the_same_ticket():
    import datetime as dt
    from bindal.myntra_spf import engine
    day = dt.datetime(2026, 6, 25)
    hand = {"11": (day, 6711398, "ACCEPT", "hand reason")}
    portal = {"11": (day, 6711398, "REJECT", None)}
    for first, second in (((hand, False), (portal, True)), ((portal, True), (hand, False))):
        tickets, seen = {}, set()
        engine.merge_tickets(tickets, seen, *first)
        engine.merge_tickets(tickets, seen, *second)
        assert tickets["11"][2] == "REJECT"
    newer = {"11": (dt.datetime(2026, 7, 1), 7000001, "OPEN", None)}
    tickets, seen = {}, set()
    engine.merge_tickets(tickets, seen, portal, True)
    engine.merge_tickets(tickets, seen, newer, False)           # a newer ticket on the line still wins
    assert tickets["11"][1] == 7000001
