"""Build bindal-bots/.env from the four old per-bot .env files (values are never printed).

    python scripts/merge_env.py            writes .env (refuses to overwrite an existing one)
    python scripts/merge_env.py --force    overwrite

Names that clashed between bots get a bot prefix (MASTER_DRIVE_FILE_ID was used by both
AJIO bots with different files, REPORT_DAYS by sale and VMS, ...).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT.parent
SOURCES = {  # old folder -> {old name: new name}; names not listed keep their name
    "DAILY SALE REPORT": {"REPORT_DAYS": "SALE_REPORT_DAYS"},
    "VMS REPORT": {"REPORT_DAYS": "VMS_REPORT_DAYS"},
    "AJIO TAT CHECK": {"MASTER_DRIVE_FILE_ID": "TAT_DRIVE_FILE_ID", "GOOGLE_SHEET_ID": "TAT_GOOGLE_SHEET_ID",
                       "MASTER_XLSX_PATH": "TAT_XLSX_PATH", "LOOKBACK_DAYS": "TAT_LOOKBACK_DAYS"},
    "AJIO SPF BOT": {"MASTER_DRIVE_FILE_ID": "SPF_MASTER_DRIVE_FILE_ID",
                     "MANUAL_DRIVE_FILE_ID": "SPF_MANUAL_DRIVE_FILE_ID", "OMS_DRIVE_FILE_ID": "SPF_OMS_DRIVE_FILE_ID",
                     "MASTER_XLSX_PATH": "SPF_MASTER_XLSX_PATH", "MANUAL_XLSX_PATH": "SPF_MANUAL_XLSX_PATH",
                     "OMS_XLSX_PATH": "SPF_OMS_XLSX_PATH", "DRY_RUN": "SPF_DRY_RUN"},
}
DROP = {"GOOGLE_SERVICE_ACCOUNT_FILE", "MYNTRA_EMAIL", "MYNTRA_PASSWORD"}


def read_env(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def main(argv: list[str]) -> int:
    target = ROOT / ".env"
    if target.exists() and "--force" not in argv:
        print(f"{target} exists; use --force to overwrite")
        return 1
    merged: dict[str, str] = {}
    conflicts = []
    for folder, renames in SOURCES.items():
        path = OLD / folder / ".env"
        if not path.exists():
            print(f"skip {folder}: no .env")
            continue
        for k, v in read_env(path).items():
            k = renames.get(k, k)
            if k in DROP or not v:
                continue
            if k in merged and merged[k] != v:
                conflicts.append(f"{k} ({folder})")
                continue
            merged.setdefault(k, v)
    # keep the layout and comments of .env.example, filling in the values
    lines, used = [], set()
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if key and key in merged:
            lines.append(f"{key}={merged[key]}")
            used.add(key)
        else:
            lines.append(line)
    extra = [k for k in merged if k not in used]
    if extra:
        lines += ["", "# ---- From the old .env files, not in .env.example ----"] + [f"{k}={merged[k]}" for k in extra]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {target}: {len(merged)} values")
    if extra:
        print("not in .env.example (check):", ", ".join(extra))
    if conflicts:
        print("different values in two bots, first one kept (check):", ", ".join(conflicts))
    key = OLD / "AJIO TAT CHECK" / "service_account.json"
    if key.exists() and not (ROOT / "service_account.json").exists():
        (ROOT / "service_account.json").write_bytes(key.read_bytes())
        print("copied service_account.json from AJIO TAT CHECK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
