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
