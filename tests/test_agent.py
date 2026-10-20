import pandas as pd

from bindal.agent import llm, tools, watch


def test_verdict_shapes():
    assert watch.verdict("STATUS: OK") == ("OK", "")
    assert watch.verdict("**STATUS: OK**")[0] == "OK"
    status, msg = watch.verdict("STATUS: ALERT\nThe run failed. Cause: login")
    assert status == "ALERT" and msg == "The run failed. Cause: login"
    assert watch.verdict("I could not decide")[0] == "ALERT"     # unknown shape is never silent
    assert watch.verdict("")[0] == "ALERT"


def test_tool_schema_from_docstring():
    s = tools.run_log.schema["function"]
    assert s["name"] == "run_log" and s["parameters"]["required"] == ["run_id"]
    props = s["parameters"]["properties"]
    assert props["run_id"]["type"] == "integer" and props["grep"]["type"] == "string"
    assert "regex" in props["grep"]["description"]


def test_tool_call_arguments(monkeypatch):
    seen = {}

    @llm.tool
    def echo(a: int, b: str = "x") -> str:
        """Echo.

        Args:
            a: a number.
            b: a word.
        """
        seen.update(a=a, b=b)
        return "done"

    fns = {"echo": echo}
    assert llm._run_tool(fns, {"function": {"name": "echo", "arguments": '{"a": 3}'}}) == "done" and seen["a"] == 3
    assert llm._run_tool(fns, {"function": {"name": "echo", "arguments": '"{\\"a\\": 4}"'}}) == "done" and seen["a"] == 4
    assert llm._run_tool(fns, {"function": {"name": "nope", "arguments": "{}"}}).startswith("Error")
    assert llm._run_tool(fns, {"function": {"name": "echo", "arguments": "{}"}}).startswith("Error: TypeError")


def test_agent_loop_runs_tools_then_answers(monkeypatch):
    replies = iter([
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "run_files", "arguments": '{"run_id": 1}'}}]}}],
         "usage": {"prompt_tokens": 100, "completion_tokens": 10}},
        {"choices": [{"message": {"content": "STATUS: OK"}}], "usage": {"prompt_tokens": 150, "completion_tokens": 5}},
    ])
    sent = []
    monkeypatch.setattr(llm, "chat", lambda messages, tools=(), model=llm.MODEL, max_tokens=4000: (sent.append(list(messages)), next(replies))[1])
    monkeypatch.setattr(tools, "_out", lambda run_id: __import__("pathlib").Path("."))
    text, usage = llm.agent_loop("sys", "task", tools.READ_ONLY)
    assert text == "STATUS: OK" and usage["steps"] == 2 and usage["input"] == 250
    assert sent[1][-1]["role"] == "tool" and sent[1][-1]["tool_call_id"] == "c1"


def test_unusual():
    earlier = [{"id": i, "conclusion": "success", "minutes": 4.0} for i in range(2, 8)]
    ok = {"id": 1, "conclusion": "success", "minutes": 5.0}
    assert watch.unusual(ok, earlier, "300 lines in the log, 0 shown\nno line matches") == []
    assert watch.unusual({**ok, "conclusion": "failure"}, earlier, "")[0] == "conclusion failure"
    assert "minutes" in watch.unusual({**ok, "minutes": 40.0}, earlier, "")[0]
    assert watch.unusual(ok, earlier, "300 lines in the log, 10 shown\nx ERROR y") == ["ERROR lines in its log"]


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


def test_chat_markdown_to_telegram():
    from bindal.agent import chat
    text = chat.to_telegram("## Sales\n- **Flipkart:** 223 <units>\n  * MAIN 130")
    assert text == "<b>Sales</b>\n• <b>Flipkart:</b> 223 &lt;units&gt;\n  • MAIN 130"


def test_propose_action_checks_before_sending(monkeypatch):
    from bindal.agent import chat
    sent = []
    monkeypatch.setattr(chat.requests, "post", lambda *a, **k: sent.append(k) or type("R", (), {"status_code": 200, "text": ""})())
    assert chat.propose_action(action="rm -rf", summary="x").startswith("Error: unknown action")
    assert chat.propose_action(action="ajiotat", summary="x", inputs="{bad").startswith("Error: inputs")
    assert not sent
    assert "NOT run yet" in chat.propose_action(action="ajiotat", summary="add 2 orders", inputs='{"orders": "FN1,FN2"}')
    assert sent[0]["json"]["action"] == "ajiotat" and sent[0]["json"]["inputs"] == {"orders": "FN1,FN2"}
