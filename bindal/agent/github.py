"""The bots' GitHub Actions runs, read-only: run lists, one run, its decrypted output.

The token is GITHUB_TOKEN (in Actions) or GH_TOKEN, else the local git credential for github.com.
"""
from __future__ import annotations

import io
import logging
import subprocess
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path

import requests

from ..core.config import OUTPUT, env
from ..core.secure import decrypt

log = logging.getLogger("bindal.agent")

REPO = "bindalbhikan/bindal-bots"
API = f"https://api.github.com/repos/{REPO}"
_token: str | None = None


def token() -> str:
    global _token
    if _token is None:
        _token = env("GITHUB_TOKEN") or env("GH_TOKEN")
        if not _token:
            try:
                out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                                     capture_output=True, text=True, timeout=20).stdout
                _token = next((ln[9:] for ln in out.splitlines() if ln.startswith("password=")), "")
            except Exception:  # noqa: BLE001 - no git: unauthenticated calls still read a public repo
                _token = ""
    return _token


def get(path: str, **params) -> requests.Response:
    headers = {"Accept": "application/vnd.github+json"}
    if token():
        headers["Authorization"] = f"Bearer {token()}"
    r = requests.get(path if path.startswith("http") else f"{API}{path}", headers=headers,
                     params=params or None, timeout=60)
    r.raise_for_status()
    return r


def _minutes(run: dict) -> float | None:
    try:
        a = datetime.fromisoformat(run["run_started_at"].replace("Z", "+00:00"))
        b = datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00"))
        return round((b - a).total_seconds() / 60, 1)
    except (KeyError, TypeError, ValueError):
        return None


def brief(run: dict) -> dict:
    return {"id": run["id"], "workflow": run.get("name"), "status": run.get("status"),
            "conclusion": run.get("conclusion"), "event": run.get("event"),
            "started": run.get("run_started_at"), "minutes": _minutes(run),
            "commit": (run.get("head_sha") or "")[:7], "url": run.get("html_url")}


def workflows() -> dict[str, str]:
    """Workflow file -> name."""
    return {Path(w["path"]).name: w["name"] for w in get("/actions/workflows", per_page=100).json()["workflows"]}


def runs(workflow_file: str, count: int = 10) -> list[dict]:
    data = get(f"/actions/workflows/{workflow_file}/runs", per_page=min(count, 100)).json()
    return [brief(r) for r in data.get("workflow_runs", [])]


def run(run_id: int) -> dict:
    r = get(f"/actions/runs/{run_id}").json()
    return {**brief(r), "workflow_file": Path(r.get("path") or "").name}


def output(run_id: int) -> Path | None:
    """The run's output/ folder (run.log + files), decrypted into output/agent/runs/<id>/;
    None when the run kept no encrypted output."""
    dest = OUTPUT / "agent" / "runs" / str(run_id)
    if (dest / "output").is_dir():
        return dest / "output"
    arts = get(f"/actions/runs/{run_id}/artifacts").json().get("artifacts", [])
    if not arts:
        return None
    key = env("ARTIFACT_KEY")
    if not key:
        raise RuntimeError("ARTIFACT_KEY not set: the run's output cannot be read")
    blob = get(arts[0]["archive_download_url"]).content
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = next((n for n in z.namelist() if n.endswith("output.tar.gz.enc")), None)
        if not name:
            return None
        data = decrypt(z.read(name), key)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        tar.extractall(dest, filter="data")
    return dest / "output" if (dest / "output").is_dir() else dest
