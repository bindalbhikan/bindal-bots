"""Concurrent execution: group AWBs by courier, batch where supported."""
from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable, Sequence

from .models import NOT_FOUND, UNKNOWN, TrackResult
from .registry import CourierRegistry


class Job:
    __slots__ = ("awb", "partner")

    def __init__(self, awb: Any, partner: Any = "") -> None:
        self.awb = str(awb).strip()
        self.partner = str(partner or "").strip()


class ProgressPrinter:
    """Single-line progress written to stderr, safe across worker threads."""

    def __init__(self, total: int, enabled: bool = True) -> None:
        self.total = total
        self.enabled = enabled and sys.stderr.isatty()
        self.done = 0
        self.ok = 0
        self._lock = threading.Lock()

    def update(self, result: TrackResult) -> None:
        with self._lock:
            self.done += 1
            self.ok += 1 if result.ok else 0
            if self.enabled:
                sys.stderr.write(
                    f"\r  {self.done}/{self.total} done  |  {self.ok} ok  "
                    f"|  {result.partner} {result.awb} -> {result.status}      ")
                sys.stderr.flush()

    def finish(self) -> None:
        if self.enabled:
            sys.stderr.write("\r" + " " * 90 + "\r")
            sys.stderr.flush()


def track_all(
    jobs: Sequence[Job],
    registry: CourierRegistry,
    workers: int = 8,
    progress: bool = True,
    on_result: Callable[[TrackResult], None] | None = None,
) -> list[TrackResult]:
    """Track every job, preserving input order in the returned list."""
    results: list[TrackResult | None] = [None] * len(jobs)
    reporter = ProgressPrinter(len(jobs), progress)

    # Group by resolved courier so batch-capable APIs get used, and so an
    # unknown partner fails fast without a network call.
    grouped: dict[str, list[int]] = {}
    for index, job in enumerate(jobs):
        courier = registry.get(job.partner, job.awb)
        if courier is None:
            res = TrackResult(awb=job.awb, partner=job.partner or "?", status=UNKNOWN)
            res.error = (f"unknown partner {job.partner!r} "
                         f"(known: {', '.join(registry.known_partners())})")
            results[index] = res.finalize()
            reporter.update(res)
            if on_result:
                on_result(res)
            continue
        grouped.setdefault(courier.name, []).append(index)

    def run_unit(courier_name: str, indexes: list[int]) -> list[tuple[int, TrackResult]]:
        courier = registry.get(courier_name)
        awbs = [jobs[i].awb for i in indexes]
        if len(awbs) > 1 and courier.batch_size > 1:
            fetched = courier.fetch_many(awbs)
        else:
            fetched = [courier.fetch(a) for a in awbs]
        return list(zip(indexes, fetched))

    # Build work units: batchable couriers get chunks, others get one AWB each.
    units: list[tuple[str, list[int]]] = []
    for courier_name, indexes in grouped.items():
        courier = registry.get(courier_name)
        size = max(1, courier.batch_size)
        if size > 1:
            for start in range(0, len(indexes), size):
                units.append((courier_name, indexes[start:start + size]))
        else:
            units.extend((courier_name, [i]) for i in indexes)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(run_unit, name, idxs) for name, idxs in units]
        for future in as_completed(futures):
            for index, result in future.result():
                results[index] = result
                reporter.update(result)
                if on_result:
                    on_result(result)

    # Second chance: the courier named by the order system did not know the AWB,
    # but its shape fits another courier (e.g. a 13-digit XpressBees AWB filed as Delhivery).
    def missed(r: TrackResult | None) -> bool:
        return r is not None and not r.ok and r.status in (NOT_FOUND, UNKNOWN)

    retry = [(i, name) for i, r in enumerate(results) if missed(r)
             for name in registry.alternatives(jobs[i].awb, exclude=r.partner)[:1]]
    if retry:
        def second(item):
            i, name = item
            return i, registry.get(name).fetch(jobs[i].awb)
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for i, alt in pool.map(second, retry):
                if alt.ok:
                    results[i] = alt

    reporter.finish()
    return [r for r in results if r is not None]


def summarize(results: Iterable[TrackResult]) -> dict[str, Any]:
    """Counts by canonical status, by partner, and by responsible party."""
    by_status: dict[str, int] = {}
    by_partner: dict[str, dict[str, int]] = {}
    by_party: dict[str, int] = {}
    for res in results:
        by_status[res.status] = by_status.get(res.status, 0) + 1
        slot = by_partner.setdefault(res.partner, {"total": 0, "ok": 0, "failed": 0})
        slot["total"] += 1
        slot["ok" if res.ok else "failed"] += 1
        if res.cancelled_by:
            by_party[res.cancelled_by] = by_party.get(res.cancelled_by, 0) + 1
    return {"by_status": dict(sorted(by_status.items())),
            "by_partner": by_partner,
            "by_party": dict(sorted(by_party.items()))}
