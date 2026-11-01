"""Resumable, verified download of provider files into an immutable archive.

Original compressed bytes are kept, with a SHA-256 and fetch time recorded per file.
A 404 or empty body is recorded as "empty", meaning the provider has no data there.
A 403 stops the whole run: access controls are not worked around.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator

from quantresearch.core.config import Paths, paths
from quantresearch.warehouse import dukascopy as dk

USER_AGENT = "QuantResearch-historical-archive/1.0"
FIRST_INTRADAY = date(2003, 5, 5)


class AccessDenied(RuntimeError):
    pass


@dataclass(frozen=True)
class Job:
    key: str
    kind: str
    url: str
    path: Path


def connect(p: Paths) -> sqlite3.Connection:
    p.warehouse.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p.manifest, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        """CREATE TABLE IF NOT EXISTS files(
             key TEXT PRIMARY KEY, kind TEXT NOT NULL, url TEXT NOT NULL, path TEXT,
             status TEXT NOT NULL, bytes INTEGER, records INTEGER, sha256 TEXT,
             attempts INTEGER NOT NULL, error TEXT, fetched_at TEXT NOT NULL)"""
    )
    return con


def days(start: date, end: date, newest_first: bool = False) -> Iterator[date]:
    span = range((end - start).days + 1)
    for offset in (reversed(span) if newest_first else span):
        yield start + timedelta(days=offset)


def m1_jobs(p: Paths, start: date, end: date, sides=("bid", "ask")) -> Iterator[Job]:
    for day in days(start, end):
        for side in sides:
            yield Job(f"m1/{side}/{day}", f"m1_{side}", dk.m1_url(day, side),
                      p.bi5 / "m1" / side / str(day.year) / f"{day}.bi5")


def tick_jobs(p: Paths, start: date, end: date, newest_first: bool = False) -> Iterator[Job]:
    for day in days(start, end, newest_first):
        if day.weekday() == 5:
            continue  # No Saturday session in this feed; the M1 audit confirms it.
        for hour in range(24):
            yield Job(f"tick/{day}/{hour:02d}", "tick", dk.tick_url(day, hour),
                      p.bi5 / "tick" / str(day.year) / f"{day:%m}" / f"{day}_{hour:02d}.bi5")


def fetch_one(job: Job, tmp: Path, retries: int, timeout: int, opener=urllib.request.urlopen) -> dict:
    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(job.url, headers={"User-Agent": USER_AGENT})
            with opener(request, timeout=timeout) as response:
                raw = response.read()
            records = dk.validate_payload(raw, "m1" if job.kind.startswith("m1") else "tick")
            if not raw:
                return dict(status="empty", attempts=attempt, bytes=0, records=0)
            job.path.parent.mkdir(parents=True, exist_ok=True)
            temp = tmp / f"{hashlib.sha1(job.key.encode()).hexdigest()}.part"
            temp.write_bytes(raw)
            os.replace(temp, job.path)
            return dict(status="ok", attempts=attempt, bytes=len(raw), records=records,
                        sha256=hashlib.sha256(raw).hexdigest())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return dict(status="empty", attempts=attempt, bytes=0, records=0)
            if exc.code == 403:
                raise AccessDenied(f"HTTP 403 for {job.url}") from exc
            last_error = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError, dk.CorruptFile) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            time.sleep(min(60.0, 1.5 * 2 ** (attempt - 1)))
    return dict(status="failed", attempts=retries, error=last_error)


def run(jobs: Iterable[Job], workers: int = 4, retries: int = 8, timeout: int = 45,
        min_free_gb: float = 5.0, retry_failed: bool = True, p: Paths | None = None,
        progress_every: int = 500, fetcher=fetch_one) -> dict:
    p = p or paths()
    p.tmp.mkdir(parents=True, exist_ok=True)
    con = connect(p)
    done = dict(con.execute("SELECT key, status FROM files"))
    counts = {"ok": 0, "empty": 0, "failed": 0, "skipped": 0}
    stop_reason = None

    def pending() -> Iterator[Job]:
        for job in jobs:
            state = done.get(job.key)
            if state in ("ok", "empty") or (state == "failed" and not retry_failed):
                counts["skipped"] += 1
                continue
            yield job

    started = time.time()
    iterator = pending()
    with ThreadPoolExecutor(workers) as pool:
        active = {}
        finished = 0
        while True:
            while stop_reason is None and len(active) < workers * 2:
                job = next(iterator, None)
                if job is None:
                    break
                active[pool.submit(fetcher, job, p.tmp, retries, timeout)] = job
            if not active:
                break
            completed, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in completed:
                job = active.pop(future)
                try:
                    result = future.result()
                except AccessDenied as exc:
                    stop_reason = str(exc)
                    continue
                counts[result["status"]] += 1
                con.execute(
                    "INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (job.key, job.kind, job.url,
                     job.path.relative_to(p.root).as_posix() if result["status"] == "ok" else None,
                     result["status"], result.get("bytes"), result.get("records"), result.get("sha256"),
                     result["attempts"], result.get("error"), datetime.now(timezone.utc).isoformat()),
                )
                con.commit()  # Never hold the write lock between files.
                finished += 1
                if finished % progress_every == 0:
                    rate = finished / max(time.time() - started, 1)
                    print(f"{datetime.now():%H:%M:%S} {counts} {rate:.1f}/s last={job.key}", flush=True)
                    if shutil.disk_usage(p.root).free / 1e9 < min_free_gb:
                        stop_reason = f"Free disk below {min_free_gb} GB"
    con.commit()
    con.close()
    counts["stop_reason"] = stop_reason
    return counts


def yesterday() -> date:
    return datetime.now(timezone.utc).date() - timedelta(days=1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch XAUUSD provider files into the archive.")
    ap.add_argument("kind", choices=["m1", "tick"])
    ap.add_argument("--start", type=date.fromisoformat, default=FIRST_INTRADAY)
    ap.add_argument("--end", type=date.fromisoformat, default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--newest-first", action="store_true")
    ap.add_argument("--min-free-gb", type=float, default=5.0)
    args = ap.parse_args(argv)
    p = paths()
    end = args.end or yesterday()
    jobs = m1_jobs(p, args.start, end) if args.kind == "m1" else tick_jobs(p, args.start, end, args.newest_first)
    result = run(jobs, workers=args.workers, min_free_gb=args.min_free_gb, p=p)
    print(result)
    return 1 if result["stop_reason"] or result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
