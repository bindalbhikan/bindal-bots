import sys
import time

import pytest

from ultron.core import approvals, config
from ultron.host import polling
from ultron.integrations import telegram


@pytest.fixture
def local(tmp_path, monkeypatch, example_seller):
    """The local approval backend, its store in a temporary folder, Telegram recorded."""
    cfg = config.Config(**{**example_seller.__dict__, "root": tmp_path, "actions": {
        **example_seller.actions,
        "hello": config.Action("", ("name",), "say hello", ("python", "-c", "import sys; print('hi', sys.argv[1:])")),
    }})
    monkeypatch.setattr(config, "get", lambda: cfg)
    monkeypatch.setattr(approvals, "_mode", "local")
    sent = []
    monkeypatch.setattr(telegram, "send_buttons", lambda chat, text, rows: sent.append((chat, text, rows)) or {"ok": 1})
    return cfg, sent


def test_local_approval_runs_once(local):
    cfg, sent = local
    assert approvals.propose("hello", {"name": "x"}, "say hi", "42") == approvals.NOT_RUN
    chat, text, rows = sent[0]
    aid = rows[0][0][1][3:]
    assert chat == "42" and "say hi" in text and rows[0][1][1] == f"rj:{aid}"
    assert approvals.decide(aid, True, "7").startswith("⌛")          # another chat cannot decide it
    approvals.propose("hello", {"name": "x"}, "say hi", "42")
    aid = sent[1][2][0][0][1][3:]
    result = approvals.decide(aid, True, "42")
    assert result.startswith("✅ Approved: hello started here")
    assert approvals.decide(aid, True, "42").startswith("⌛")         # a second tap does nothing
    log = next((cfg.output / "agent" / "actions").glob("hello-*.log"))
    for _ in range(50):
        if log.read_text():
            break
        time.sleep(0.1)
    assert "hi ['--name', 'x']" in log.read_text()


def test_rejected_and_expired(local, monkeypatch):
    _, sent = local
    approvals.propose("hello", {}, "s", "42")
    assert approvals.decide(sent[0][2][0][0][1][3:], False, "42") == "✖️ Rejected."
    approvals.propose("hello", {}, "s", "42")
    monkeypatch.setattr(approvals.time, "time", lambda: 10 ** 11)
    assert approvals.decide(sent[1][2][0][0][1][3:], True, "42").startswith("⌛")


def test_command_line_is_one_argument_per_value():
    a = config.Action("", ("from", "dry_run"), "", ("python", "run.py", "sale"))
    argv = approvals.command_line(a, {"from": "2026-10-01; rm -rf /", "dry_run": "1"})
    assert argv == [sys.executable, "run.py", "sale", "--from", "2026-10-01; rm -rf /", "--dry-run", "1"]


def test_workflow_action_dispatches(local, monkeypatch):
    seen = []
    monkeypatch.setattr(approvals.github, "dispatch", lambda wf, inputs, ref: seen.append((wf, inputs, ref)))
    assert "sale.yml started on GitHub" in approvals.start("sale", {"from": "2026-10-01"})
    assert seen == [("sale.yml", {"from": "2026-10-01"}, "main")]


def test_serve_answers_messages_and_taps(monkeypatch):
    out, calls = [], []
    monkeypatch.setattr(polling.chat, "answer", lambda text, chat: f"answer to {text} in {chat}")
    monkeypatch.setattr(telegram, "send", lambda text, chat=None: out.append((text, chat)))
    monkeypatch.setattr(telegram, "call", lambda method, **kw: calls.append((method, kw)) or {})
    polling.on_message({"chat": {"id": 42}, "text": "orders yesterday?"})
    polling.on_message({"chat": {"id": 42}, "text": "/start"})
    polling.on_message({"chat": {"id": 42}, "text": "/other"})
    assert out[0] == ("answer to orders yesterday? in 42", "42") and "Ultron" in out[1][0] and len(out) == 2
    monkeypatch.setattr(polling.approvals, "decide", lambda aid, ok, chat: f"decided {aid} {ok} {chat}")
    polling.on_tap({"id": "c", "data": "ap:abc", "message": {"chat": {"id": 42}, "message_id": 5, "text": "Approve?"},
                    "from": {"first_name": "A"}})
    edit = [kw for m, kw in calls if m == "editMessageText"][0]
    assert "decided abc True 42" in edit["text"] and edit["message_id"] == 5


def test_two_kinds_of_conflict(monkeypatch):
    class R:
        def __init__(self, text):
            self.status_code, self.text, self.ok = 409, text, False
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(telegram.requests, "get", lambda *a, **k: R('{"description":"Conflict: can\'t use getUpdates method while webhook is active"}'))
    with pytest.raises(telegram.WebhookSet):
        telegram.updates(0)
    monkeypatch.setattr(telegram.requests, "get", lambda *a, **k: R('{"description":"Conflict: terminated by other getUpdates request"}'))
    with pytest.raises(telegram.OtherInstance):
        telegram.updates(0)
