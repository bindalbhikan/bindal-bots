#!/usr/bin/env python
"""Download and decrypt a GitHub Actions run's output (log + output/ folder).

    python scripts/github/run_output.py <run id>        (the run id is in the run's URL)

The runs pack their output encrypted with the ARTIFACT_KEY secret (scripts/github/ci_run.sh), because
the repository's artifacts are public. The same key must be in the local .env as ARTIFACT_KEY.
The files land in output/runs/<run id>/; the end of the log is printed.
"""
import io
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = "bindalbhikan/bindal-bots"
sys.path.insert(0, str(ROOT))
from bindal.core.secure import decrypt  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not argv[0].isdigit():
        print(__doc__)
        return 2
    run_id = argv[0]
    from dotenv import dotenv_values
    key = os.environ.get("ARTIFACT_KEY") or dotenv_values(ROOT / ".env").get("ARTIFACT_KEY")
    if not key:
        print("ARTIFACT_KEY missing in .env")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run(["gh", "run", "download", run_id, "--repo", REPO, "--dir", tmp],
                           capture_output=True, text=True)
        if r.returncode:
            print(f"Download failed: {(r.stderr or r.stdout).strip()}")
            return 1
        found = list(Path(tmp).rglob("output.tar.gz.enc"))
        if not found:
            print("This run has no encrypted output (older runs kept plain files)")
            return 1
        data = decrypt(found[0].read_bytes(), key)
    dest = ROOT / "output" / "runs" / run_id
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        tar.extractall(dest, filter="data")
    log = dest / "output" / "run.log"
    print(f"Saved to {dest}")
    if log.is_file():
        print("--- end of run.log ---")
        print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
