import time

import pytest

from ultron.agents import chat, watch
from ultron.core import config, llm, memory
from ultron.host import polling
from ultron.tools import memory as memory_tools


def mem():
    return memory.get()


def test_remember_recall_forget():
    a = mem().remember("The owner wants the sale report as units, not rupees", "preference", ["sale"])
    mem().remember("Myntra SPF waits out a portal cooldown of up to 150 minutes; that is harmless",
                   "known_issue", ["myntra-spf.yml"])
    mem().remember("PPMP is the second Myntra account", "fact")
    found = mem().search("myntra spf cooldown")
    assert found[0]["kind"] == "known_issue"
    assert mem().search("anything", tags=["myntra-spf.yml"])[0]["kind"] == "known_issue"
    assert mem().forget(a["id"]) and not mem().forget(a["id"])
    assert len(mem().read()["notes"]) == 2


def test_same_note_twice_is_refreshed_not_repeated():
    a = mem().remember("Bluedart picks up late on Mondays", "fact")
    b = mem().remember("Bluedart picks up late on Mondays.", "fact", ["courier"])
    notes = mem().read()["notes"]
    assert a["id"] == b["id"] and len(notes) == 1 and notes[0]["uses"] == 1 and notes[0]["tags"] == ["courier"]


@pytest.mark.parametrize("text", ["password: hunter22", "the OTP is 482913", "token=abcd1234efgh",
                                  "call the buyer on 9876543210", "mail raj@example.com",
                                  "key " + "a1" * 20])
def test_secrets_and_personal_data_are_refused(text):
    with pytest.raises(ValueError, match="not stored"):
        mem().remember(text, "fact")
    assert "Error: not stored" in memory_tools.remember(text=text)
    assert mem().read()["notes"] == []


def test_ordinary_notes_are_fine():
    mem().remember("Order FN2511505146 was the test order for the AJIO TAT fix", "fact")
    mem().remember("The Myntra OTP bot retries at 11 AM when the 10 AM run gets no OTP", "fact")
    mem().remember("Token of thanks: the packer team gets a bonus in Diwali week", "decision")
    assert len(mem().read()["notes"]) == 3


def test_preferences_always_in_context():
    mem().remember("Answer in short bullet points", "preference")
    mem().remember("Snapdeal sales are not reported", "fact")
    ctx = mem().context("how many orders on flipkart yesterday")
    assert "short bullet points" in ctx and "Snapdeal" not in ctx
    assert "Snapdeal" in mem().context("snapdeal sales")


def test_full_memory_drops_incidents_first(tmp_path):
    small = memory.Memory(memory.FileStore(tmp_path / "m2.json"), config.MemorySettings(max_notes=3))
    small.remember("keep this preference", "preference")
    small.remember("run 1 failed: login dropped", "incident")
    small.remember("run 2 failed: timeout at portal", "incident")
    small.remember("an important business fact", "fact")
    kinds = sorted(n["kind"] for n in small.read()["notes"])
    assert kinds == ["fact", "incident", "preference"]
    assert "run 2" in [n for n in small.read()["notes"] if n["kind"] == "incident"][0]["text"]


def test_conversation_history(tmp_path):
    m = memory.Memory(memory.FileStore(tmp_path / "m3.json"), config.MemorySettings(history_turns=2, history_days=1))
    for i in range(3):
        m.add_turn("42", f"q{i}", f"a{i}")
    m.add_turn("42", "my password is x1y2z3w4", "ok")
    h = m.history("42")
    assert [x["content"] for x in h] == ["q2", "a2", "(a message with a password, key, phone number or email: not kept)", "ok"]
    assert m.history("7") == []
    data = m.read()
    data["chats"]["42"][0]["at"] = time.time() - 5 * 86400        # an old message drops out
    m.store.write(data, "")
    assert len(m.history("42")) == 2
    m.clear_chat("42")
    assert m.history("42") == []


def test_worker_store_redoes_a_stale_write(monkeypatch):
    kv = {"version": 0, "data": None}
    stale = {"left": 1}

    class R:
        def __init__(self, code, body=None):
            self.status_code, self._body = code, body

        def json(self):
            return self._body

        def raise_for_status(self):
            assert self.status_code < 400

    def get(url, **kw):
        return R(404) if kv["data"] is None else R(200, {"version": kv["version"], "data": kv["data"]})

    def put(url, json, **kw):
        if stale["left"]:                       # another run wrote first
            stale["left"] -= 1
            kv["version"] += 1
            kv["data"] = {"schema": 1, "notes": [{"id": "x", "kind": "fact", "text": "other run", "tags": []}], "chats": {}}
            return R(409)
        assert json["version"] == kv["version"]
        kv["version"], kv["data"] = kv["version"] + 1, json["data"]
        return R(200, {"version": kv["version"]})

    monkeypatch.setattr(memory.requests, "get", get)
    monkeypatch.setattr(memory.requests, "put", put)
    m = memory.Memory(memory.WorkerStore("https://w", "t"), config.MemorySettings())
    m.remember("my note", "fact")
    assert [n["text"] for n in kv["data"]["notes"]] == ["other run", "my note"] and kv["version"] == 2


def test_auto_backend(monkeypatch):
    memory.reset()
    monkeypatch.delenv("AGENT_WORKER_URL", raising=False)
    assert isinstance(memory.get().store, memory.FileStore)
    memory.reset()
    monkeypatch.setenv("AGENT_WORKER_URL", "https://w")
    monkeypatch.setenv("AGENT_WORKER_TOKEN", "t")
    assert isinstance(memory.get().store, memory.WorkerStore)


def test_chat_remembers_and_follows_up(monkeypatch):
    seen = []
    replies = iter([
        {"choices": [{"message": {"content": "", "tool_calls": [{"id": "1", "function": {"name": "remember",
            "arguments": '{"text": "The owner calls Myntra PPMP the second account", "kind": "fact"}'}}]}}]},
        {"choices": [{"message": {"content": "Noted."}}]},
        {"choices": [{"message": {"content": "PPMP is your second Myntra account."}}]},
    ])
    monkeypatch.setattr(llm, "chat", lambda messages, tools=(), model="", max_tokens=4000: (seen.append(messages), next(replies))[1])
    assert "Noted." in chat.answer("PPMP is our second account, remember that", "42")
    assert "PPMP is your second" in chat.answer("which Myntra account is PPMP?", "42")
    last = seen[-1]
    assert "second account" in last[0]["content"]                        # the note is in the system prompt
    assert [m["role"] for m in last[1:]] == ["user", "assistant", "user"]  # the earlier turn came along
    assert last[1]["content"] == "PPMP is our second account, remember that"
    assert {t.__name__ for t in chat.tools()} >= {"recall", "remember", "forget"}


def test_chat_answers_when_memory_is_out_of_reach(monkeypatch):
    class Broken(memory.Memory):
        def read(self):
            raise ConnectionError("worker down")
    monkeypatch.setattr(memory, "_memory", Broken(None, config.MemorySettings()))
    monkeypatch.setattr(llm, "chat", lambda *a, **k: {"choices": [{"message": {"content": "Fine."}}]})
    assert "Fine." in chat.answer("hello", "42")


def test_watch_notes_incidents_and_reads_them_back():
    info = {"id": 9, "workflow_file": "sale.yml"}
    watch.note_incident(info, "The run failed.\nCause: OMSGuru login dropped.\nNext: rerun.")
    watch.note_incident({**info, "id": 10}, "The run failed.\nCause: Drive upload timed out.\nNext: rerun.")
    notes = mem().read()["notes"]
    assert len(notes) == 2 and all(n["kind"] == "incident" and n["tags"] == ["sale.yml"] for n in notes)
    mem().remember("A sale.yml login drop at 3:30 PM is OMSGuru maintenance, harmless", "known_issue", ["sale.yml"])
    past = watch.past("sale.yml", "ERROR login dropped")
    assert "maintenance" in past and "run 9" in past


def test_memory_commands():
    n = mem().remember("Reports go out by 10 AM", "preference")
    assert n["id"] in polling.memory_command("/memory", "", "42")
    assert "Reports go out" in polling.memory_command("/memory", "reports", "42")
    assert "Forgot" in polling.memory_command("/forget", n["id"], "42")
    mem().add_turn("42", "q", "a")
    polling.memory_command("/new", "", "42")
    assert mem().history("42") == []
