"""Built-in tools "memory": the model searches, adds to and drops from its long-term notes
(core/memory.py). The chat agent gets all three when the memory is on; the watcher only reads
(recall), its incidents are noted by its own code.
"""
from __future__ import annotations

import time

from ..core import memory
from ..core.llm import tool


def _line(n: dict) -> str:
    tags = f" [{', '.join(n['tags'])}]" if n.get("tags") else ""
    return f"{n['id']} ({n['kind']}{tags}, {time.strftime('%d %b %Y', time.localtime(n.get('updated', 0)))}): {n['text']}"


@tool
def recall(query: str) -> str:
    """Search your long-term notes (the owner's preferences, facts about the business, known
    errors, decisions, past incidents) for something not already in front of you.

    Args:
        query: words to look for, e.g. "Myntra SPF cooldown" or "how the owner wants the sale report".
    """
    found = memory.get().search(query, k=10)
    return "\n".join(_line(n) for n in found) or f"Nothing in memory about {query!r}."


@tool
def remember(text: str, kind: str = "fact", tags: str = "") -> str:
    """Keep a note for the future: something lasting the owner told you or you found out, not a
    one-off answer. Kinds: preference (how the owner wants things), fact (about the business,
    channels, accounts, codes), known_issue (an error that is harmless or has a known fix),
    decision (what the owner decided). Never passwords, OTPs, tokens, or customers' details.

    Args:
        text: the note in one or two plain sentences, standing on its own.
        kind: preference, fact, known_issue or decision.
        tags: optional comma-separated words to find it by, e.g. "myntra-spf.yml, cooldown".
    """
    if kind == "incident":
        return "Error: incidents are noted by the watcher itself; use known_issue or fact"
    try:
        note = memory.get().remember(text, kind, [t for t in tags.split(",")], source="chat")
    except ValueError as exc:
        return f"Error: {exc}"
    return f"Remembered as {note['id']}."


@tool
def forget(note_id: str) -> str:
    """Drop one note, when the owner says it is wrong or no longer true.

    Args:
        note_id: the note's id as shown in brackets, e.g. "a1b2c3".
    """
    return f"Forgot {note_id}." if memory.get().forget(note_id) else f"No note {note_id!r}."


TOOLS = [recall, remember, forget]
READ = [recall]
