from datetime import date

import pytest

from bindal.return_analysis import pipeline, sources


def test_report_month_defaults_to_last_month():
    assert pipeline.report_month("", date(2026, 10, 15)) == date(2026, 9, 1)
    assert pipeline.report_month("", date(2027, 1, 15)) == date(2026, 12, 1)
    assert pipeline.report_month("2026-08", date(2026, 10, 15)) == date(2026, 8, 1)


def test_windows_on_the_15th():
    sale, ret, parts = sources.windows(date(2026, 9, 1), date(2026, 10, 15))
    assert sale == (date(2026, 9, 1), date(2026, 9, 30))
    assert ret == (date(2026, 9, 1), date(2026, 10, 14))
    assert parts == [(date(2026, 9, 1), date(2026, 9, 30)), (date(2026, 10, 1), date(2026, 10, 14))]


def test_windows_early_in_the_month_and_december():
    sale, ret, parts = sources.windows(date(2026, 9, 1), date(2026, 10, 6))
    assert ret[1] == date(2026, 10, 5) and len(parts) == 2
    sale, ret, parts = sources.windows(date(2026, 12, 1), date(2027, 1, 15))
    assert sale[1] == date(2026, 12, 31) and ret[1] == date(2027, 1, 14)
    with pytest.raises(ValueError):
        sources.windows(date(2026, 10, 1), date(2026, 10, 18))   # the month is not over


def test_summary_text_fits_a_phone():
    results = [{"portal": "MYNTRA MAIN", "month": "SEP-26", "sold": 7220, "returned": 2465, "rate": 34.14},
               {"portal": "ALL PORTAL", "month": "SEP-26", "sold": 15381, "returned": 5134, "rate": 33.38}]
    text = pipeline.summary_text(results, (date(2026, 9, 1), date(2026, 9, 30)),
                                 (date(2026, 9, 1), date(2026, 10, 14)), ["FLIPKART left out: x"])
    assert "Return Analysis · SEP-26" in text and "Myn MAIN" in text and "FLIPKART left out" in text
    table = text.split("<pre>")[1].split("</pre>")[0]
    assert max(len(line) for line in table.splitlines()) <= 25


def test_months_to_fill_stop_where_the_returns_window_is_open():
    from bindal.return_analysis import yearly
    assert [m.month for m in yearly.months_to_fill(2026, date(2026, 10, 18))] == list(range(1, 10))
    assert [m.month for m in yearly.months_to_fill(2026, date(2026, 10, 14))] == list(range(1, 9))
    assert len(yearly.months_to_fill(2026, date(2027, 1, 20))) == 12


def test_monthly_data_round_trip(tmp_path):
    import pandas as pd
    from bindal.return_analysis.engine import DATA_COLUMNS, read_data
    df = pd.DataFrame([["09-2026", "AJIO", "Leia", "1", "S1", "MY-1", 2, "DRESS", "Return", "Size Too Big", 499.5, None, None]],
                      columns=DATA_COLUMNS)
    df.to_csv(tmp_path / "AJIO SEP-26.csv", index=False)
    back = read_data(tmp_path / "AJIO SEP-26.csv")
    assert back.at[0, "QTY"] == 2 and back.at[0, "SALE PRICE"] == 499.5
    assert pd.isna(back.at[0, "STATE"]) and pd.isna(back.at[0, "COST"])


def test_yearly_text_fits_a_phone():
    results = [{"portal": "ALL PORTAL", "month": "2026", "sold": 120000, "returned": 40000, "rate": 33.3,
                "months": ["01-2026", "09-2026"]}]
    text = pipeline.yearly_text(results, 2026, [])
    assert "Jan - Sep 2026" in text
    table = text.split("<pre>")[1].split("</pre>")[0]
    assert max(len(line) for line in table.splitlines()) <= 25
