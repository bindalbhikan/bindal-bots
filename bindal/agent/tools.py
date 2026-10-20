"""The agent's tools. Read-only: nothing here starts, changes or sends anything.

Each returns text for the model, kept short (MAX_CHARS): a log is searched or tailed, a sheet
summarised, never sent whole.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
from . import github
from .llm import tool

MAX_CHARS = 8000          # every step sends the whole conversation again: keep results small


def _cut(text: str) -> str:
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + f"\n... ({len(text) - MAX_CHARS} more characters cut)"


def _out(run_id: int) -> Path:
    path = github.output(run_id)
    if path is None:
        raise ValueError(f"run {run_id} kept no encrypted output (cancelled before upload, or an old run)")
    return path


@tool
def recent_runs(workflow_file: str, count: int = 10) -> str:
    """Recent runs of one bot workflow, newest first: id, conclusion, event, start, minutes, commit.
    Use it to compare a run with the ones before it (usual duration, how often it fails).

    Args:
        workflow_file: workflow file name, e.g. "ajio-tat.yml", "myntra-spf.yml", "inv-push.yml".
        count: how many runs (max 30).
    """
    return json.dumps(github.runs(workflow_file, min(max(count, 1), 30)), default=str)


@tool
def run_log(run_id: int, grep: str = "", tail: int = 120) -> str:
    """The bot's own log of a run (output/run.log, decrypted). With grep: only the lines matching
    that regular expression (case-insensitive), with a line before and after; else the last lines.

    Args:
        run_id: the GitHub Actions run id.
        grep: optional regex, e.g. "ERROR|WARNING|Traceback" or "Master after merge".
        tail: lines from the end when grep is empty (max 400).
    """
    log = _out(run_id) / "run.log"
    if not log.is_file():
        return "this run has no run.log"
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    if grep:
        rx = re.compile(grep, re.I)
        keep = sorted({j for i, ln in enumerate(lines) if rx.search(ln) for j in (i - 1, i, i + 1)
                       if 0 <= j < len(lines)})
        body = "\n".join(lines[j] for j in keep) or f"no line matches {grep!r}"
        return _cut(f"{len(lines)} lines in the log, {len(keep)} shown\n{body}")
    n = min(max(tail, 1), 400)
    return _cut(f"{len(lines)} lines in the log, last {min(n, len(lines))}:\n" + "\n".join(lines[-n:]))


@tool
def run_files(run_id: int) -> str:
    """The files the run wrote (its output/ folder): path and size. xlsx files can be read with
    sheet_summary.

    Args:
        run_id: the GitHub Actions run id.
    """
    root = _out(run_id)
    files = [f"{p.relative_to(root).as_posix()}  {p.stat().st_size:,} bytes"
             for p in sorted(root.rglob("*")) if p.is_file() and p.name != "desktop.ini"]
    return _cut("\n".join(files) or "no files")


@tool
def sheet_summary(run_id: int, path: str, sheet: str = "", group_by: str = "", month_of: str = "") -> str:
    """Summary of one sheet of an xlsx the run wrote: its sheets, row count, every column with how
    many values are filled, the first rows. Optional counts: group_by = a column to count values
    of (top 25); month_of = a date column to count rows per month, crossed with group_by if given.

    Args:
        run_id: the GitHub Actions run id.
        path: the file's path as run_files shows it.
        sheet: sheet name; empty = the first sheet.
        group_by: optional column to count, e.g. "TAT Result" or "Pickup Status".
        month_of: optional date column, e.g. "Order Time".
    """
    f = (_out(run_id) / path).resolve()
    if not f.is_file() or _out(run_id).resolve() not in f.parents:
        return f"no file {path!r} in run {run_id}"
    xl = pd.ExcelFile(f)
    name = sheet or xl.sheet_names[0]
    if name not in xl.sheet_names:
        return f"no sheet {name!r}; sheets: {xl.sheet_names}"
    df = xl.parse(name)
    parts = [f"sheets: {xl.sheet_names}", f"sheet {name!r}: {len(df)} rows, {len(df.columns)} columns",
             "filled per column: " + ", ".join(f"{c}={int(df[c].notna().sum())}" for c in df.columns),
             "first rows:\n" + df.head(5).to_string(max_colwidth=40)]
    if month_of and month_of in df:
        month = pd.to_datetime(df[month_of], errors="coerce").dt.to_period("M").astype(str)
        if group_by and group_by in df:
            parts.append(f"rows per month of {month_of} x {group_by}:\n"
                         + pd.crosstab(month, df[group_by].fillna("(blank)"), margins=True).to_string())
        else:
            parts.append(f"rows per month of {month_of}:\n" + month.value_counts().sort_index().to_string())
    elif group_by and group_by in df:
        parts.append(f"{group_by} counts:\n" + df[group_by].fillna("(blank)").value_counts().head(25).to_string())
    for col in (group_by, month_of):
        if col and col not in df:
            parts.append(f"(no column {col!r})")
    return _cut("\n\n".join(parts))


READ_ONLY = [recent_runs, run_log, run_files, sheet_summary]
