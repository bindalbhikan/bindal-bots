import pandas as pd

from bindal.agent import tools, watch


def test_verdict_shapes():
    assert watch.verdict("STATUS: OK") == ("OK", "")
    status, msg = watch.verdict("STATUS: ALERT\nRun fail hua, kaaran: login")
    assert status == "ALERT" and msg == "Run fail hua, kaaran: login"
    assert watch.verdict("I could not decide")[0] == "ALERT"     # unknown shape is never silent
    assert watch.verdict("")[0] == "ALERT"


def test_cost():
    assert watch.cost({"input": 1_000_000, "cache_write": 0, "cache_read": 0, "output": 0}) == 4.0
    assert watch.cost({"input": 0, "cache_write": 0, "cache_read": 1_000_000, "output": 50_000}) == 1.2


def _run_dir(tmp_path, monkeypatch):
    out = tmp_path / "output"
    (out / "ajio_tat").mkdir(parents=True)
    (out / "run.log").write_text("\n".join(
        ["10:00 INFO start"] + [f"10:0{i} INFO step {i}" for i in range(5)]
        + ["10:09 ERROR bindal.x: login failed", "10:10 INFO end"]), encoding="utf-8")
    pd.DataFrame({"Order Time": pd.to_datetime(["2026-06-01", "2026-06-20", "2026-07-02"]),
                  "TAT Result": ["CORRECT", "NOT CORRECT", None]}).to_excel(out / "ajio_tat" / "m.xlsx", index=False)
    monkeypatch.setattr(tools.github, "output", lambda run_id: out)
    return out


def test_run_log_grep_and_tail(tmp_path, monkeypatch):
    _run_dir(tmp_path, monkeypatch)
    hit = tools.run_log(run_id=1, grep="error")
    assert "login failed" in hit and "step 4" in hit and "end" in hit and "step 1" not in hit
    assert tools.run_log(run_id=1, tail=1).endswith("10:10 INFO end")


def test_files_and_sheet_summary(tmp_path, monkeypatch):
    _run_dir(tmp_path, monkeypatch)
    assert "ajio_tat/m.xlsx" in tools.run_files(run_id=1)
    s = tools.sheet_summary(run_id=1, path="ajio_tat/m.xlsx", group_by="TAT Result", month_of="Order Time")
    assert "3 rows" in s and "TAT Result=2" in s and "2026-06" in s and "(blank)" in s
    assert "no file" in tools.sheet_summary(run_id=1, path="../../secret.txt")
