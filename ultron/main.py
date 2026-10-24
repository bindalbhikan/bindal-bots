"""Run Ultron from this folder, installed or not:

    python main.py serve          the agent on this machine (Windows, Linux, macOS, Android/Termux)
    python main.py check          read agent.toml and print what was found
    python main.py --help         every command

agent.toml is looked for from the current folder up (or set AGENT_CONFIG).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ultron.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
