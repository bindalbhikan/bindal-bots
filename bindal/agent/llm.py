"""The agent's model: Cloudflare Workers AI on the account's free daily allocation, through the
Telegram worker's /api/ai route (DASHBOARD_URL + DASHBOARD_TOKEN, the secrets the bots already
have). Chat-completions shape: messages, tools, tool_calls.

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

from ..core.config import env

log = logging.getLogger("bindal.agent")

MODEL = "@cf/openai/gpt-oss-120b"
# US$ per million tokens (input, output); the free allocation is 10,000 neurons a day, about $0.11
PRICES = {"@cf/openai/gpt-oss-120b": (0.35, 0.75), "@cf/qwen/qwen3.8-27b": (0.45, 3.2),
          "@cf/nvidia/nemotron-3-120b-a12b": (0.5, 1.5), "@cf/openai/gpt-oss-20b": (0.2, 0.3)}
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


def chat(messages: list[dict], tools: list[Callable] = (), model: str = MODEL, max_tokens: int = 4000) -> dict:
    url, token = env("DASHBOARD_URL").rstrip("/"), env("DASHBOARD_TOKEN")
    if not url or not token:
        raise LLMError("DASHBOARD_URL / DASHBOARD_TOKEN not set")
    body = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if tools:
        body["tools"] = [t.schema for t in tools]
    try:
        r = requests.post(f"{url}/api/ai", json=body, headers={"Authorization": f"Bearer {token}"}, timeout=180)
    except requests.RequestException as exc:
        raise LLMError(f"Workers AI not reached: {exc}") from exc
    try:
        data = r.json()
    except ValueError:
        raise LLMError(f"Workers AI: HTTP {r.status_code} {r.text[:200]}") from None
    if r.status_code != 200 or "error" in data or not data.get("choices"):
        raise LLMError(f"Workers AI: {str(data.get('error') or data)[:300]}")
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
               model: str = MODEL) -> tuple[str, dict]:
    """(final answer text, usage) after letting the model call the tools up to max_steps times."""
    fns = {t.__name__: t for t in tools}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
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
    """What the call would cost in US$ beyond the free allocation (it is within it at our volumes)."""
    pin, pout = PRICES.get(usage.get("model"), (0.35, 0.75))
    return round((usage["input"] * pin + usage["output"] * pout) / 1e6, 4)
