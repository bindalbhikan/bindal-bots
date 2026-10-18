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
