import pandas as pd

from bindal.ajio_tat.sheets import summary_layout
from bindal.ajio_tat.tat_engine import apply_status, summarize, summary_rows


def order(courier_status):
    return pd.DataFrame([{
        "Invoice No": "INV1", "Order ID": "O1", "AWB": "SF1", "Courier": "SHADOWFAX",
        "Order Time": pd.Timestamp("2026-10-10 09:00"), "Dispatch Deadline": pd.Timestamp("2026-10-10 23:59:59"),
        "Processed Time": pd.Timestamp("2026-10-10 09:30"), "RTS Time": pd.Timestamp("2026-10-10 09:40"),
        "RTS Exact": True, "Pickup Attempt Time": pd.NaT, "Pickup Time": pd.NaT,
        "OMS Item Status": "Ready to ship", "Courier Status": courier_status,
        "Last Updated": pd.Timestamp("2026-10-11")}])


def test_failed_pickup_is_not_counted_as_picked():
    df = apply_status(order("PICKUP_FAILED"), pd.Timestamp("2026-10-11 12:00"))
    assert df.loc[0, "Pickup Status"] == "Pending Pickup"
    assert df.loc[0, "TAT Result"] == "NOT CORRECT"


def test_moving_parcel_without_scan_is_picked_time_unknown():
    df = apply_status(order("IN_TRANSIT"), pd.Timestamp("2026-10-11 12:00"))
    assert df.loc[0, "Pickup Status"] == "Picked (Time N/A)"


def test_summary_layout_follows_the_rows():
    df = apply_status(order("IN_TRANSIT"), pd.Timestamp("2026-10-11 12:00"))
    grid = summary_rows(summarize(df), pd.Timestamp("2026-10-11"), "note")
    at = summary_layout(grid)
    assert grid[at["counts_head"]][0] == "TAT Result"
    assert grid[at["pct"]][0] == "TAT %"
    assert grid[at["courier_head"]][0] == "Courier"


def test_summary_rows_without_tracking_time():
    df = apply_status(order("IN_TRANSIT"), pd.Timestamp("2026-10-11 12:00"))
    assert "not known" in summary_rows(summarize(df), pd.NaT, "note")[2][0]


def test_erp_labels_fill_only_missing_times():
    import pandas as pd
    from bindal.ajio_tat.tat_engine import apply_erp_labels, normalize_master
    m = normalize_master(pd.DataFrame({"Invoice No": ["I1", "I2", "I3"], "Order ID": ["1", "2", "3"],
                                       "AWB": ["a1", "A2", "A3"], "ERP Label Time": [None, None, "2026-10-01 09:00"],
                                       "ERP Label By": [None, None, "OLD"]}))
    out, n = apply_erp_labels(m, {"A1": ("2026-10-16 10:00:37", "RAHUL"), "A3": ("2026-10-17 10:00", "NEW")})
    assert n == 1
    assert out.loc[0, "ERP Label By"] == "RAHUL" and str(out.loc[0, "ERP Label Time"]).startswith("2026-10-16 10:00")
    assert pd.isna(out.loc[1, "ERP Label Time"]) and out.loc[2, "ERP Label By"] == "OLD"


def test_build_report_output_has_the_erp_columns_empty():
    import pandas as pd
    from bindal.ajio_tat.tat_engine import DATA_COLS, merge_master, normalize_master
    old = normalize_master(pd.DataFrame({"Invoice No": ["I1"], "Order ID": ["1"], "AWB": ["A1"]}))
    new = pd.DataFrame({"Invoice No": ["I2"], "Order ID": ["2"], "AWB": ["A2"]})   # no ERP columns
    merged, stats = merge_master(old, new)
    assert list(merged.columns) == DATA_COLS and stats["new"] == 1


def test_courier_scorecard_complete_days_and_streak(monkeypatch):
    from bindal.agent import llm
    from bindal.ajio_tat import pipeline as p
    monkeypatch.setattr(llm, "chat", lambda *a, **k: (_ for _ in ()).throw(llm.LLMError("offline")))
    now = pd.Timestamp("2026-10-20 17:30")
    rows = []
    for back in range(2, 9):                       # 18 Oct back to 12 Oct: complete days
        day = now.normalize() - pd.Timedelta(days=back)
        for i in range(10):                        # Bluedart 4 of 10 on time every day: a streak
            rows.append({"Order Time": day + pd.Timedelta(hours=9), "Courier": "BLUEDART",
                         "Dispatch Deadline": day + pd.Timedelta(hours=23, minutes=59),
                         "Pickup Status": "On Time" if i < 4 else "Late", "Process Status": "On Time",
                         "TAT Result": "CORRECT" if i < 4 else "NOT CORRECT"})
        rows.append({"Order Time": day + pd.Timedelta(hours=9), "Courier": "SHADOWFAX",
                     "Dispatch Deadline": day + pd.Timedelta(hours=23, minutes=59), "Pickup Status": "On Time",
                     "Process Status": "Late", "TAT Result": "NOT CORRECT"})
    yesterday = now.normalize() - pd.Timedelta(days=1)  # not complete yet: left out
    rows.append({"Order Time": yesterday + pd.Timedelta(hours=11), "Courier": "BLUEDART",
                 "Dispatch Deadline": now.normalize() + pd.Timedelta(hours=23, minutes=59),
                 "Pickup Status": "Pending Pickup", "Process Status": "On Time", "TAT Result": "PENDING"})
    df = pd.DataFrame(rows)
    text = p.courier_scorecard(df, now)
    assert "12 Oct - 18 Oct" in text
    table = text.split("<pre>")[1].split("</pre>")[0].splitlines()
    assert table[0].split()[-1] == "18Oct"
    assert table[1].split() == ["BluDart", "70", "42", "40%", "40%"]
    assert table[-1].split() == ["Total", "77", "42", "45%"]
    assert "BluDart: pickup on time under 70% for 7 days in a row" in text
    assert "NOT CORRECT on 18 Oct: <b>7</b> · our side (processing late) 1 · courier side (pickup late) 6" in text
    assert "🧠" not in text                          # model offline: the scorecard still goes out


def test_ajio_pickup_timestamp_replaces_the_courier_time(monkeypatch):
    from bindal.ajio_tat import pipeline as p
    monkeypatch.setenv("AJIO_USERNAME", "x")
    rep = pd.DataFrame({"Cust Order No": ["FN1", "FN2", "FN3"], "FWD Seller Order NO": ["O1", "O2", "O3"],
                        "FWD AWB": ["A1", "A2", "A3"], "Seller Invoice No": ["I1", "I2", "I3"],
                        "Pickup Timestamp": ["Thu Oct 22 11:57:00 IST 2026", "Thu Oct 22 12:03:00 IST 2026", None]})
    monkeypatch.setattr(p, "ajio_order_report", lambda a, b: rep.copy())
    df = pd.DataFrame({"Order ID": ["O1", "O2", "O3"], "AWB": ["A1", "A2", "A3"], "Invoice No": ["I1", "I2", "I3"],
                       "Cust Order No": [None, None, None],
                       "Order Time": pd.to_datetime(["2026-10-21 10:00"] * 3),
                       "Pickup Time": pd.to_datetime(["2026-10-22 18:00", None, "2026-10-22 15:00"])})
    out = p.ajio_report(df, [], since=pd.Timestamp("2026-10-15"))
    assert list(out["Pickup Time"]) == [pd.Timestamp("2026-10-22 11:57"), pd.Timestamp("2026-10-22 12:03"),
                                        pd.Timestamp("2026-10-22 15:00")]   # AJIO first, courier where AJIO has none
    assert list(out["Cust Order No"]) == ["FN1", "FN2", "FN3"]


def test_dispatch_deadline_is_the_courier_pickup_time_from_29_jul():
    from bindal.ajio_tat.tat_engine import dispatch_deadline
    ts = pd.Timestamp
    assert dispatch_deadline(ts("2026-07-28 09:00"), "XPRESSBEES") == ts("2026-07-28 23:59:59")
    assert dispatch_deadline(ts("2026-10-10 09:59"), "XPRESSBEES") == ts("2026-10-10 12:00")
    assert dispatch_deadline(ts("2026-10-10 10:00"), "SHADOWFAX") == ts("2026-10-11 12:30")
    assert dispatch_deadline(ts("2026-10-10 15:00"), "BLUEDART") == ts("2026-10-11 12:00")
    assert dispatch_deadline(ts("2026-10-10 15:00"), "EKART") == ts("2026-10-11 23:59:59")
    assert pd.isna(dispatch_deadline(pd.NaT, "BLUEDART"))


def test_pickup_tickets_one_per_overdue_order_never_twice(monkeypatch):
    from bindal.ajio_tat import pipeline as p
    from bindal.portals import ajio
    now = pd.Timestamp("2026-10-23 16:00")
    base = {"Process Status": "On Time", "Pickup Status": "Pending Pickup", "Courier": "BLUEDART",
            "Order Time": pd.Timestamp("2026-10-22 11:00"), "RTS Time": pd.Timestamp("2026-10-22 15:00")}
    df = pd.DataFrame([
        {**base, "Order ID": "1", "AWB": "A1", "Dispatch Deadline": pd.Timestamp("2026-10-22 12:00")},  # overdue
        {**base, "Order ID": "2", "AWB": "A2", "Dispatch Deadline": pd.Timestamp("2026-10-22 12:00")},  # has ticket
        {**base, "Order ID": "3", "AWB": "A3", "Dispatch Deadline": pd.Timestamp("2026-10-23 12:00")},  # in grace
        {**base, "Order ID": "4", "AWB": "A4", "Dispatch Deadline": pd.Timestamp("2026-10-23 12:00"),
         "Pickup Status": "Late"},                                                                  # picked
        {**base, "Order ID": "5", "AWB": "A5", "Dispatch Deadline": pd.Timestamp("2026-10-22 12:00")}])  # courier: picked
    sent = []
    monkeypatch.setattr(p, "courier_pickup_check", lambda pairs: {"A1": "not picked", "A5": "picked"})

    class Client:
        def __init__(self, *a): pass
        def login(self): pass
        def ticket_list(self, a, b):
            return [{"orderId": "2", "ticketId": "T2", "subCategory": ajio.PICKUP_NOT_DONE}]
        def create_ticket(self, payload):
            sent.append(payload)
            return "T1"

    monkeypatch.setenv("AJIO_USERNAME", "x@y.com")
    monkeypatch.setattr(ajio, "AjioClient", Client)
    monkeypatch.setattr(p.time, "sleep", lambda s: None)
    done = p.raise_pickup_tickets(df, now, dry=False)
    assert [(d["Order ID"], d["Result"]) for d in done] == [("1", "created"), ("2", "already has ticket"),
                                                            ("5", "courier: picked")]
    assert sent[0]["orderId"] == "1" and sent[0]["issueType"] == "ORDERS" and sent[0]["ticketType"] == "GENERAL"
    assert sent[0]["subIssueType"] == ajio.PICKUP_NOT_DONE and "A1" in sent[0]["issueTitle"]
    assert "🎫" in p.pickup_ticket_report(done)[0]


def test_ticket_ids_land_in_the_master_and_need_the_ajio_report(monkeypatch):
    from types import SimpleNamespace
    from bindal.ajio_tat import pipeline as p
    args = SimpleNamespace(tickets=True, tickets_dry=False)
    df = pd.DataFrame({"Order ID": ["1", "2"], "Pickup Ticket": [None, "OLD"]})
    monkeypatch.setattr(p, "raise_pickup_tickets", lambda *a, **k: [
        {"Order ID": "1", "AWB": "A1", "Courier": "BLUEDART", "Pickup Deadline": pd.NaT,
         "Ticket": "T1", "Result": "created"}])
    out, rep = p.pickup_tickets(df, args, [])
    assert out["Pickup Ticket"].tolist() == ["T1", "OLD"] and rep
    out, rep = p.pickup_tickets(df, args, ["AJIO order report not read: timeout"])
    assert pd.isna(out.loc[0, "Pickup Ticket"]) and out.loc[1, "Pickup Ticket"] == "OLD"
    assert "not raised" in rep[0]



def test_open_pickup_ticket_gets_one_follow_up_a_day(monkeypatch):
    from bindal.ajio_tat import pipeline as p
    from bindal.portals import ajio
    now = pd.Timestamp("2026-10-25 17:30")
    base = {"Process Status": "On Time", "Pickup Status": "Pending Pickup", "Courier": "BLUEDART",
            "Order Time": pd.Timestamp("2026-10-22 11:00"), "RTS Time": pd.Timestamp("2026-10-22 15:00"),
            "Dispatch Deadline": pd.Timestamp("2026-10-23 12:00")}
    df = pd.DataFrame([{**base, "Order ID": "1", "AWB": "A1"}, {**base, "Order ID": "2", "AWB": "A2"},
                       {**base, "Order ID": "3", "AWB": "A3"}])
    comments = []

    class Client:
        seller_id = "me"
        def __init__(self, *a): pass
        def login(self): pass
        def ticket_list(self, a, b):
            return [{"orderId": o, "ticketId": "T" + o, "subCategory": ajio.PICKUP_NOT_DONE} for o in ("1", "2", "3")]
        def ticket_detail(self, tid):
            if tid == "T2":      # we commented 3 hours ago: not yet
                return {"createdDate": "2026-10-23T15:36:43.67",
                        "comments": [{"authorType": "SELLER", "createdAt": "2026-10-25T14:30:00"}]}
            return {"createdDate": "2026-10-23T15:36:43.67", "comments": []}
        def comment(self, tid, text):
            comments.append((tid, text))

    monkeypatch.setenv("AJIO_USERNAME", "x@y.com")
    monkeypatch.setattr(ajio, "AjioClient", Client)
    monkeypatch.setattr(p.time, "sleep", lambda s: None)
    monkeypatch.setattr(p, "courier_pickup_check", lambda c: {"A1": "not picked", "A2": "not picked", "A3": "picked"})
    done = p.raise_pickup_tickets(df, now, dry=False)
    assert [(d["Order ID"], d["Result"]) for d in done] == [("1", "followed up"), ("2", "already has ticket"),
                                                            ("3", "already has ticket")]
    assert comments == [("T1", "Pickup is still pending for Order ID 1 (AWB A1). Kindly arrange the pickup today, "
                               "and no penalty should be applied to this order.")]
    assert any("Followed up 1" in str(line) for line in p.pickup_ticket_report(done))
