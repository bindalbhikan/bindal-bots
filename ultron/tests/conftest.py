from pathlib import Path

import pytest

from ultron.core import config, memory

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "agent.example.toml"
GET = config.get          # the cached loader itself, even while a test has patched config.get


@pytest.fixture(autouse=True)
def example_seller(monkeypatch, tmp_path):
    """Every test runs as the example seller (examples/agent.example.toml), whatever folder pytest
    starts in, with its own empty memory file; the cached config is dropped before and after so no
    other setup leaks in or out."""
    monkeypatch.setenv("AGENT_CONFIG", str(EXAMPLE))
    GET.cache_clear()
    cfg = GET()
    monkeypatch.setattr(memory, "_memory", memory.Memory(memory.FileStore(tmp_path / "memory.json"), cfg.memory))
    yield cfg
    GET.cache_clear()
    memory.reset()
