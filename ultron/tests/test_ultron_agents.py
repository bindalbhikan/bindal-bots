import pytest

from ultron.agents import chat, watch
from ultron.core import approvals
from ultron.tools import runs


def test_verdict_shapes():
    assert watch.verdict("STATUS: OK") == ("OK", "")
    assert watch.verdict("**STATUS: OK**")[0] == "OK"
    status, msg = watch.verdict("STATUS: ALERT\nThe run failed. Cause: login")
    assert status == "ALERT" and msg == "The run failed. Cause: login"
    assert watch.verdict("I could not decide")[0] == "ALERT"     # unknown shape is never silent
    assert watch.verdict("")[0] == "ALERT"


def test_unusual():
    earlier = [{"id": i, "conclusion": "success", "minutes": 4.0} for i in range(2, 8)]
    ok = {"id": 1, "conclusion": "success", "minutes": 5.0}
    assert watch.unusual(ok, earlier, "300 lines in the log, 0 shown\nno line matches") == []
    assert watch.unusual({**ok, "conclusion": "failure"}, earlier, "")[0] == "conclusion failure"
    assert "minutes" in watch.unusual({**ok, "minutes": 40.0}, earlier, "")[0]
    assert watch.unusual(ok, earlier, "300 lines in the log, 10 shown\nx ERROR y") == ["ERROR lines in its log"]


def test_prompts_name_the_seller(example_seller):
    assert example_seller.company in watch.system_prompt("sale.yml")
    rules = chat.rules(example_seller)
    assert example_seller.company in rules and "- sale:" in rules and "- invpush:" in rules


def _run_dir(tmp_path, monkeypatch):
    pd = pytest.importorskip("pandas")        # not in a minimal (Termux-like) install
    out = tmp_path / "output"
    (out / "tat").mkdir(parents=True)
    (out / "run.log").write_text("\n".join(
        ["10:00 INFO start"] + [f"10:0{i} INFO step {i}" for i in range(5)]
        + ["10:09 ERROR app.x: login failed", "10:10 INFO end"]), encoding="utf-8")
    pd.DataFrame({"Order Time": pd.to_datetime(["2026-06-01", "2026-06-20", "2026-07-02"]),
                  "TAT Result": ["CORRECT", "NOT CORRECT", None]}).to_excel(out / "tat" / "m.xlsx", index=False)
    monkeypatch.setattr(runs.github, "output", lambda run_id: out)
    return out


def test_run_log_grep_and_tail(tmp_path, monkeypatch):
    _run_dir(tmp_path, monkeypatch)
    hit = runs.run_log(run_id=1, grep="error")
    assert "login failed" in hit and "step 4" in hit and "end" in hit and "step 1" not in hit
    assert runs.run_log(run_id=1, tail=1).endswith("10:10 INFO end")


def test_files_and_sheet_summary(tmp_path, monkeypatch):
    _run_dir(tmp_path, monkeypatch)
    assert "tat/m.xlsx" in runs.run_files(run_id=1)
    s = runs.sheet_summary(run_id=1, path="tat/m.xlsx", group_by="TAT Result", month_of="Order Time")
    assert "3 rows" in s and "TAT Result=2" in s and "2026-06" in s and "(blank)" in s
    assert "no file" in runs.sheet_summary(run_id=1, path="../../secret.txt")


def test_propose_action_checks_before_sending(monkeypatch):
    sent = []
    monkeypatch.setattr(approvals.requests, "post", lambda *a, **k: sent.append(k) or type("R", (), {"status_code": 200, "text": ""})())
    assert chat.propose_action(action="rm -rf", summary="x").startswith("Error: unknown action")
    assert chat.propose_action(action="sale", summary="x", inputs="{bad").startswith("Error: inputs")
    assert "JSON object" in chat.propose_action(action="sale", summary="x", inputs="[1]")
    assert "takes only" in chat.propose_action(action="sale", summary="x", inputs='{"rm": "1"}')
    assert not sent
    assert "NOT run yet" in chat.propose_action(action="sale", summary="sale of 1 Oct", inputs='{"from": "2026-10-01"}')
    assert sent[0]["json"]["action"] == "sale" and sent[0]["json"]["inputs"] == {"from": "2026-10-01"}


def test_chat_tools_follow_the_config(example_seller):
    names = [t.__name__ for t in chat.tools()]
    assert "find_order" in names and names[-1] == "propose_action"
