"""The agent's model: any chat-completions endpoint (messages, tools, tool_calls).

By default the seller's Cloudflare worker (ultron/worker): POST <worker>/api/ai with the worker
token, served by Workers AI on the account's free daily allocation. AGENT_LLM_URL + AGENT_LLM_KEY
point it at any other OpenAI-compatible endpoint instead (full URL of /chat/completions).

`tool` turns a typed function with an "Args:" docstring into a tool; `agent_loop` runs the
model and the tools until it answers.
"""
from __future__ import annotations

import inspect
import json
import logging
import re
from typing import Callable, get_type_hints

import requests

from . import config
from .config import env

log = logging.getLogger("ultron")

JSON_TYPES = {int: "integer", str: "string", float: "number", bool: "boolean"}


class LLMError(RuntimeError):
    """The model could not be reached: free allocation used up, model down, worker not set up."""


def tool(fn: Callable) -> Callable:
    """Attach a function-calling schema built from the signature and the "Args:" docstring."""
    doc = inspect.getdoc(fn) or ""
    summary, _, args_doc = doc.partition("Args:")
    descr = dict(re.findall(r"^\s*(\w+):\s*(.+(?:\n\s{8,}.+)*)", args_doc, re.M))
    props, required, hints = {}, [], get_type_hints(fn)
    for name, p in inspect.signature(fn).parameters.items():
        props[name] = {"type": JSON_TYPES.get(hints.get(name), "string"),
                       "description": " ".join((descr.get(name) or name).split())}
        if p.default is inspect.Parameter.empty:
            required.append(name)
    fn.schema = {"type": "function", "function": {
        "name": fn.__name__, "description": " ".join(summary.split()),
        "parameters": {"type": "object", "properties": props, "required": required}}}
    return fn


def endpoint() -> tuple[str, str]:
    if env("AGENT_LLM_URL"):
        return env("AGENT_LLM_URL"), env("AGENT_LLM_KEY")
    url, token = config.get().worker()
    return (f"{url}/api/ai" if url else ""), token


def chat(messages: list[dict], tools: list[Callable] = (), model: str = "", max_tokens: int = 4000) -> dict:
    url, token = endpoint()
    if not url or not token:
        cfg = config.get()
        raise LLMError(f"model endpoint not set ({cfg.worker_url_env} / {cfg.worker_token_env}, or AGENT_LLM_URL)")
    body = {"model": model or config.get().model_name(), "messages": messages, "max_tokens": max_tokens}
    if tools:
        body["tools"] = [t.schema for t in tools]
    try:
        r = requests.post(url, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=180)
    except requests.RequestException as exc:
        raise LLMError(f"model not reached: {exc}") from exc
    try:
        data = r.json()
    except ValueError:
        raise LLMError(f"model: HTTP {r.status_code} {r.text[:200]}") from None
    if r.status_code != 200 or "error" in data or not data.get("choices"):
        raise LLMError(f"model: {str(data.get('error') or data)[:300]}")
    return data


def _run_tool(fns: dict[str, Callable], call: dict) -> str:
    fn = fns.get(call["function"]["name"])
    if fn is None:
        return f"Error: no tool {call['function']['name']!r}"
    try:
        args = call["function"].get("arguments") or "{}"
        args = json.loads(args) if isinstance(args, str) else args
        if isinstance(args, str):                 # some models send the JSON twice encoded
            args = json.loads(args)
        return str(fn(**args))
    except Exception as exc:  # noqa: BLE001 - the model reads the error and can try again
        return f"Error: {type(exc).__name__}: {str(exc)[:300]}"


def agent_loop(system: str, task: str, tools: list[Callable], max_steps: int = 8,
               model: str = "", history: list[dict] = ()) -> tuple[str, dict]:
    """(final answer text, usage) after letting the model call the tools up to max_steps times.
    `history`: earlier user / assistant messages of the conversation, oldest first."""
    model = model or config.get().model_name()
    fns = {t.__name__: t for t in tools}
    messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": task}]
    usage = {"input": 0, "output": 0, "steps": 0, "model": model}
    for step in range(max_steps + 1):
        last = step == max_steps
        if last:   # out of steps: answer now from what was read
            messages.append({"role": "user", "content": "No more tool calls: give your answer now."})
        data = chat(messages, [] if last else tools, model)
        usage["steps"] += 1
        u = data.get("usage") or {}
        usage["input"] += u.get("prompt_tokens") or 0
        usage["output"] += u.get("completion_tokens") or 0
        msg = data["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if not calls:
            return (msg.get("content") or "").strip(), usage
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for call in calls:
            log.info("agent tool %s %s", call["function"]["name"], str(call["function"].get("arguments"))[:200])
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": _run_tool(fns, call)})
    return "", usage


def cost(usage: dict) -> float:
    """What the call costs in US$ at the model's list price ([model.prices] in agent.toml);
    on the Workers AI free allocation it is within the day's free amount."""
    pin, pout = config.get().prices.get(usage.get("model"), (0.35, 0.75))
    return round((usage["input"] * pin + usage["output"] * pout) / 1e6, 4)
