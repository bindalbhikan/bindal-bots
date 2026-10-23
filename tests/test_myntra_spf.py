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


def test_claims_are_raised_in_bulk_and_shown_as_bot_tickets(tmp_path):
    from openpyxl import load_workbook
    from bindal.myntra_spf import tickets

    class Portal:
        calls = []

        def spf_bulk_claim(self, ids, cat, desc, seller=None):
            self.calls.append((list(ids), cat, seller))
            return 777

    portal = Portal()
    lines = tickets.raise_claims(portal, "DBCC", {tickets.DELAYED: ["11", "12"]}, tmp_path, True, seller=32207)
    assert portal.calls == [(["11", "12"], tickets.DELAYED, 32207)]
    assert lines == ["DBCC Delayed Returns: 2 raised (job 777)"]
    rows = list(load_workbook(tmp_path / "DBCC" / tickets.BOT_FILE).active.values)
    assert rows[0][:2] == ("ticketId", "orderLineId") and rows[1][:2] == ("BOT JOB 777", "11")
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
        assert final_status({"STATUS": st}) == "SPF Accepted"
        assert engine.is_settled({"STATUS": st})
    assert final_status({"STATUS": "PAYMENT_COMPLETED", "SPF PAID": 500}) == "SPF Paid"
    assert final_status({"STATUS": "REJECT"}) == "" and not engine.is_settled({"STATUS": "REJECT"})
    f = engine.build_formulas(5)["FINAL RETURN STATUS"]
    st = engine.C["STATUS"] + "5"
    assert f'IF(OR({st}="ACCEPT",{st}="INVOICE_INITIATED",{st}="PAYMENT_COMPLETED"),"SPF Accepted",' in f
    assert f.count("(") == f.count(")")
    assert ("SPF ACCEPTED", "count", None, [("FINAL RETURN STATUS", "SPF Accepted")]) in engine.SUMMARY_COLS
