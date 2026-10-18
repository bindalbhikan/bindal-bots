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
