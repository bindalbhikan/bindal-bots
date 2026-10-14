"""Command-line entry point for the AWB tracking tool."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .excel_io import read_jobs, write_json, write_report
from .registry import CourierRegistry
from .runner import Job, summarize, track_all

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.json"


def load_config(path: str | Path | None) -> dict:
    candidate = Path(path) if path else DEFAULT_CONFIG
    if candidate.exists():
        with candidate.open(encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="track",
        description="Bulk AWB tracking across Indian courier partners.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python track.py -i input.xlsx\n"
            "  python track.py -i input.xlsx -o out/report.xlsx -w 12 --json out/raw.json\n"
            "  python track.py --awb 4884666796456 --partner DELHIVERY\n"
            "  python track.py --awb SDLC1078244202 --awb VL0085177649772\n"
        ),
    )
    parser.add_argument("-i", "--input", help="input .xlsx/.csv with AWB and PARTNER columns")
    parser.add_argument("--sheet", help="worksheet name (default: first sheet)")
    parser.add_argument("-o", "--output", help="output .xlsx path (default: output/tracking_<ts>.xlsx)")
    parser.add_argument("--json", dest="json_path", help="also dump results as JSON")
    parser.add_argument("--raw", action="store_true", help="include raw API payloads in the JSON dump")
    parser.add_argument("--awb", action="append", default=[],
                        help="track a single AWB (repeatable); skips the input file")
    parser.add_argument("--partner", help="partner name for --awb (auto-detected if omitted)")
    parser.add_argument("-w", "--workers", type=int, default=8, help="concurrent workers (default: 8)")
    parser.add_argument("--limit", type=int, help="only process the first N rows")
    parser.add_argument("--only", help="comma-separated partners to include")
    parser.add_argument("--config", help="path to config.json")
    parser.add_argument("--no-progress", action="store_true", help="suppress the progress line")
    parser.add_argument("--list-partners", action="store_true", help="print supported partners and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    registry = CourierRegistry(config)

    if args.list_partners:
        print("Supported partners:")
        for name in registry.known_partners():
            cls = registry.resolve_name(name)
            aliases = ", ".join(cls.aliases) or "-"
            print(f"  {name:<12} aliases: {aliases}")
        return 0

    if args.awb:
        jobs = [Job(a, args.partner or "") for a in args.awb]
    elif args.input:
        jobs = read_jobs(args.input, args.sheet)
    else:
        default_input = Path("input.xlsx")
        if not default_input.exists():
            build_parser().print_help()
            return 2
        jobs = read_jobs(default_input)

    if args.only:
        wanted = {p.strip().upper() for p in args.only.split(",") if p.strip()}
        jobs = [j for j in jobs
                if (registry.resolve_name(j.partner, j.awb) or type("X", (), {"name": ""}))
                .name in wanted]
    if args.limit:
        jobs = jobs[:args.limit]

    if not jobs:
        print("No AWBs to track.", file=sys.stderr)
        return 1

    print(f"Tracking {len(jobs)} AWB(s) with {args.workers} worker(s)...", file=sys.stderr)
    results = track_all(jobs, registry, workers=args.workers,
                        progress=not args.no_progress)
    stats = summarize(results)

    output = Path(args.output) if args.output else Path("output") / (
        f"tracking_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
    report = write_report(results, output, stats)

    print(f"\nReport: {report.resolve()}", file=sys.stderr)
    if args.json_path:
        print(f"JSON:   {write_json(results, args.json_path, args.raw).resolve()}",
              file=sys.stderr)

    print("\nBy status:", file=sys.stderr)
    for status, count in stats["by_status"].items():
        print(f"  {status:<18} {count}", file=sys.stderr)
    if stats["by_party"]:
        print("\nCancelled / failed by:", file=sys.stderr)
        for party, count in stats["by_party"].items():
            print(f"  {party:<18} {count}", file=sys.stderr)
    print("\nBy partner:", file=sys.stderr)
    for partner, counts in sorted(stats["by_partner"].items()):
        print(f"  {partner:<14} total {counts['total']:<4} ok {counts['ok']:<4} "
              f"failed {counts['failed']}", file=sys.stderr)

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
