"""This seller's setup of Ultron (agent.toml, the worker) and the rule that Ultron itself
names no seller. Ultron's own tests are in its repo (github.com/bindalbhikan/Ultron)."""
import tomllib
from pathlib import Path

from ultron.core import config

ROOT = Path(__file__).resolve().parents[1]


def setup():
    config.get.cache_clear()
    return config.parse(ROOT / "agent.toml")


def test_agent_toml():
    cfg = setup()
    assert cfg.company and cfg.repo and cfg.actions and "omsguru" in cfg.chat_tools
    assert {t.__name__ for t in config.load_tools(cfg.chat_tools)} >= {"find_order", "run_log"}


def test_worker_actions_match_agent_toml():
    worker = tomllib.loads((ROOT / "worker" / "wrangler.toml").read_text(encoding="utf-8"))["vars"]["AGENT_ACTIONS"]
    mine = {k: {"workflow": a.workflow, "inputs": list(a.inputs)} for k, a in setup().actions.items()}
    assert worker == mine


def test_action_workflows_exist():
    missing = [a.workflow for a in setup().actions.values() if not (ROOT / ".github" / "workflows" / a.workflow).is_file()]
    assert not missing


def test_ultron_names_no_seller():
    """The installed Ultron package (github.com/bindalbhikan/Ultron) never names this seller."""
    import ultron
    package = Path(ultron.__file__).resolve().parent
    seller = setup().company.split()[0].lower()
    hits = [str(p.relative_to(package)) for p in package.rglob("*.py")
            if seller in p.read_text(encoding="utf-8").lower()]
    assert not hits, f"{seller!r} named in {hits}"
