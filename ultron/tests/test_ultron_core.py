import pytest

from ultron.core import config, llm
from ultron.integrations import telegram
from ultron.tools import runs


def test_example_config(example_seller):
    cfg = example_seller
    assert cfg.company and cfg.repo and cfg.actions["sale"].inputs == ("from", "to")
    assert cfg.worker_url_env == "AGENT_WORKER_URL" and "agent-watch.yml" in cfg.watch_skip
    names = {t.__name__ for t in config.load_tools(cfg.chat_tools)}
    assert {"run_log", "find_order", "sale_by_channel", "due_today"} <= names


def test_config_needs_a_company(tmp_path):
    (tmp_path / "agent.toml").write_text("[github]\nrepo = 'a/b'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="name is required"):
        config.parse(tmp_path / "agent.toml")


def test_config_action_needs_a_workflow(tmp_path):
    (tmp_path / "agent.toml").write_text("[company]\nname = 'X'\n[actions]\nsale = { inputs = [] }\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no workflow or command"):
        config.parse(tmp_path / "agent.toml")


def test_config_found_from_a_subfolder(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_CONFIG")
    (tmp_path / "agent.toml").write_text("[company]\nname = 'X'\n", encoding="utf-8")
    (tmp_path / "a" / "b").mkdir(parents=True)
    assert config.find(tmp_path / "a" / "b") == tmp_path / "agent.toml"


def test_channel_line():
    assert config.channel_line("EXAMPLE FASHIONS PVT LTD - MYNTRA") == "Myntra"
    assert config.channel_line("EXAMPLE FASHIONS PVT LTD - Flipkart") == "Flipkart"


def test_tool_schema_from_docstring():
    s = runs.run_log.schema["function"]
    assert s["name"] == "run_log" and s["parameters"]["required"] == ["run_id"]
    props = s["parameters"]["properties"]
    assert props["run_id"]["type"] == "integer" and props["grep"]["type"] == "string"
    assert "regex" in props["grep"]["description"]


def test_tool_call_arguments():
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


def test_agent_loop_runs_tools_then_answers(monkeypatch, tmp_path):
    replies = iter([
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "run_files", "arguments": '{"run_id": 1}'}}]}}],
         "usage": {"prompt_tokens": 100, "completion_tokens": 10}},
        {"choices": [{"message": {"content": "STATUS: OK"}}], "usage": {"prompt_tokens": 150, "completion_tokens": 5}},
    ])
    sent = []
    monkeypatch.setattr(llm, "chat", lambda messages, tools=(), model="", max_tokens=4000: (sent.append(list(messages)), next(replies))[1])
    monkeypatch.setattr(runs, "_out", lambda run_id: tmp_path)
    text, usage = llm.agent_loop("sys", "task", runs.TOOLS)
    assert text == "STATUS: OK" and usage["steps"] == 2 and usage["input"] == 250
    assert usage["model"] == "@cf/openai/gpt-oss-120b" and llm.cost(usage) > 0
    assert sent[1][-1]["role"] == "tool" and sent[1][-1]["tool_call_id"] == "c1"


def test_model_endpoint(monkeypatch):
    monkeypatch.setenv("AGENT_WORKER_URL", "https://w.example/")
    monkeypatch.setenv("AGENT_WORKER_TOKEN", "t")
    assert llm.endpoint() == ("https://w.example/api/ai", "t")
    monkeypatch.setenv("AGENT_LLM_URL", "https://api.example/v1/chat/completions")
    monkeypatch.setenv("AGENT_LLM_KEY", "k")
    assert llm.endpoint() == ("https://api.example/v1/chat/completions", "k")


def test_chat_without_endpoint(monkeypatch):
    for name in ("AGENT_WORKER_URL", "AGENT_WORKER_TOKEN", "AGENT_LLM_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(llm.LLMError, match="AGENT_WORKER_URL"):
        llm.chat([{"role": "user", "content": "hi"}])


def test_markdown_to_telegram():
    text = telegram.md("## Sales\n- **Flipkart:** 223 <units>\n  * MAIN 130\n| a | b |\n|---|---|")
    assert text == "<b>Sales</b>\n• <b>Flipkart:</b> 223 &lt;units&gt;\n  • MAIN 130\na · b"


def test_long_message_cut_on_lines():
    parts = telegram.parts("\n".join(f"line {i:04d}" for i in range(1000)), limit=500)
    assert len(parts) > 1 and all(len(p) <= 500 for p in parts)
    assert "".join(parts).count("line") == 1000
