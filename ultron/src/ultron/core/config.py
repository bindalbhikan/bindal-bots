"""One seller's setup: agent.toml (ultron/examples/agent.example.toml shows every key).

Found at AGENT_CONFIG, else agent.toml in the current folder or the nearest folder above it. The
folder holding it is the project root: output/, .env and the bots' docs are read relative to it.
Settings that are secrets (tokens, keys) never go in agent.toml: they stay in the environment
(GitHub secrets, or a local .env), under the names agent.toml gives.
"""
from __future__ import annotations

import importlib
import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

DEFAULT_PRICES = {"@cf/openai/gpt-oss-120b": [0.35, 0.75]}


def env(name: str, default: str = "") -> str:
    """The setting, or `default` when it is missing or blank (GitHub passes an unset repository
    variable as an empty string, which must not wipe out the default)."""
    return (os.getenv(name) or "").strip() or default


@dataclass(frozen=True)
class Action:
    """Something the agent may propose and the owner approves, with these inputs only: a GitHub
    workflow, or (ultron serve on the seller's own machine) a command run there, each input
    added as --<input> <value>."""
    workflow: str
    inputs: tuple[str, ...]
    about: str
    command: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemorySettings:
    """[memory] in agent.toml (core/memory.py)."""
    backend: str = "auto"          # auto (the worker when it is set up, else a file) | worker | file | off
    history_turns: int = 10        # earlier messages of the same chat the agent sees
    history_days: int = 3          # ... no older than this
    max_notes: int = 400           # long-term notes kept; incidents go first when it is full
    recall: int = 8                # notes put in front of the model for one message
    note_chars: int = 500          # longest note


@dataclass(frozen=True)
class Config:
    root: Path
    company: str
    about: str
    owner_note: str
    language: str
    repo: str
    ref: str
    docs: Path
    model: str
    prices: dict[str, list[float]]
    worker_url_env: str
    worker_token_env: str
    watch_skip: frozenset[str]
    watch_failure_only: frozenset[str]
    watch_steps: int
    watch_tools: tuple[str, ...]
    chat_steps: int
    chat_tools: tuple[str, ...]
    actions: dict[str, Action] = field(default_factory=dict)
    channel_prefixes: tuple[str, ...] = ()
    channel_names: dict[str, str] = field(default_factory=dict)
    memory: MemorySettings = field(default_factory=lambda: MemorySettings())

    @property
    def output(self) -> Path:
        return self.root / "output"

    def worker(self) -> tuple[str, str]:
        """(url, token) of the Cloudflare worker that serves the model and the approvals."""
        return env(self.worker_url_env).rstrip("/"), env(self.worker_token_env)

    def model_name(self) -> str:
        return env("AGENT_MODEL", self.model)


def find(start: Path | None = None) -> Path:
    if env("AGENT_CONFIG"):
        return Path(env("AGENT_CONFIG")).resolve()
    here = (start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / "agent.toml").is_file():
            return folder / "agent.toml"
    raise FileNotFoundError("agent.toml not found (set AGENT_CONFIG, or run from the project folder)")


def parse(path: Path) -> Config:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    company, gh, model = raw.get("company", {}), raw.get("github", {}), raw.get("model", {})
    worker, watch, chat = raw.get("worker", {}), raw.get("watch", {}), raw.get("chat", {})
    channels, mem = raw.get("channels", {}), raw.get("memory", {})
    if mem.get("backend", "auto") not in ("auto", "worker", "file", "off"):
        raise ValueError(f"{path}: [memory] backend must be auto, worker, file or off")
    if not company.get("name"):
        raise ValueError(f"{path}: [company] name is required")
    actions = {}
    for key, a in raw.get("actions", {}).items():
        if not a.get("workflow") and not a.get("command"):
            raise ValueError(f"{path}: action {key!r} has no workflow or command")
        actions[key] = Action(a.get("workflow", ""), tuple(a.get("inputs", [])), a.get("about", ""),
                              tuple(a.get("command", [])))
    return Config(
        root=path.parent,
        company=company["name"],
        about=" ".join(company.get("about", "").split()),
        owner_note=" ".join(company.get("owner_note", "").split()),
        language=company.get("language", "plain English"),
        repo=gh.get("repo") or env("GITHUB_REPOSITORY"),
        ref=gh.get("ref", "main"),
        docs=path.parent / gh.get("docs", "docs/bots"),
        model=model.get("name", "@cf/openai/gpt-oss-120b"),
        prices={**DEFAULT_PRICES, **model.get("prices", {})},
        worker_url_env=worker.get("url_env", "AGENT_WORKER_URL"),
        worker_token_env=worker.get("token_env", "AGENT_WORKER_TOKEN"),
        watch_skip=frozenset(watch.get("skip", ["agent-watch.yml", "agent-chat.yml"])),
        watch_failure_only=frozenset(watch.get("failure_only", [])),
        watch_steps=int(watch.get("max_steps", 6)),
        watch_tools=tuple(watch.get("tools", [])),
        chat_steps=int(chat.get("max_steps", 8)),
        chat_tools=tuple(chat.get("tools", [])),
        actions=actions,
        channel_prefixes=tuple(channels.get("strip_prefix", [])),
        channel_names=dict(channels.get("names", {})),
        memory=MemorySettings(**{k: v for k, v in mem.items() if k in MemorySettings.__dataclass_fields__}),
    )


@lru_cache(maxsize=1)
def get() -> Config:
    return parse(find())


def channel_line(name: str) -> str:
    """An OMSGuru channel name as the seller calls it: [channels] strip_prefix taken off, then
    [channels] names applied (e.g. "Myntra DressBerry CC" -> "Myntra DBCC"); others unchanged."""
    cfg = get()
    name = str(name)
    for prefix in cfg.channel_prefixes:
        name = name.replace(prefix, "")
    name = name.strip()
    return cfg.channel_names.get(name, name)


# tool sets every seller gets: name in agent.toml -> module
BUILTIN_TOOLS = {"runs": "ultron.tools.runs", "omsguru": "ultron.tools.omsguru", "memory": "ultron.tools.memory"}


def load_tools(names: tuple[str, ...]) -> list[Callable]:
    """The tools listed in agent.toml: a built-in set (BUILTIN_TOOLS) or the dotted name of a
    module of the seller's own. Each module exports TOOLS, a list of functions made with
    ultron.core.llm.tool. They must only read; anything to be done goes through propose_action and
    the owner's approval."""
    out = []
    for name in names:
        out += list(getattr(importlib.import_module(BUILTIN_TOOLS.get(name, name)), "TOOLS"))
    return out
