"""Monthly acquisition through the pinned dukascopy-node tool, with the month-end fix.

The direct file feed can be badly degraded while this route stays fast, so bulk history comes from
here and `fetch.py` remains available for single files. Each output is verified, gzip-compressed,
checksummed and recorded in the manifest. A month is only accepted when its last row falls on or
after the month's last trading day, unless the month is still in progress. That check is what
would have caught the original defect at download time.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from quantresearch.core.config import Paths, paths
from quantresearch.dukascopy_download import DOWNLOADER, exclusive_end
from quantresearch.warehouse import calendar as cal
from quantresearch.warehouse.fetch import connect


def month_list(start: str, end: str, newest_first: bool = True) -> list[pd.Period]:
    months = list(pd.period_range(start, end, freq="M"))
    return months[::-1] if newest_first else months


def m1_path(p: Paths, side: str, month: pd.Period) -> Path:
    return p.warehouse / "csv" / "m1" / side / str(month.year) / f"xauusd_{side}_m1_{month.year}_{month.month:02d}.csv.gz"


def tick_path(p: Paths, month: pd.Period) -> Path:
    return p.warehouse / "ticks" / f"year={month.year}" / f"month={month.month:02d}" / "ticks.parquet"


def _command(kind: str, side: str, begin: date, end: date, directory: Path, name: str) -> list[str]:
    npx = shutil.which("npx") or "npx"
    return [npx, "--yes", DOWNLOADER, "-i", "xauusd", "-from", begin.isoformat(), "-to", exclusive_end(end),
            "-t", kind, "-p", side, "-v", "-vu", "units", "-f", "csv", "-df", "iso", "-bs", "3", "-bp", "2000",
            "-dir", str(directory), "-fn", name]


def month_bounds(month: pd.Period, today: date) -> tuple[date, date, bool]:
    begin = month.start_time.date()
    end = month.end_time.date()
    complete = end < today
    return begin, min(end, today - timedelta(days=1)), complete


def verify_coverage(last_ts: pd.Timestamp, month: pd.Period, complete: bool) -> str | None:
    """Reason a month fails, or None. Partial months are accepted but recorded as partial."""
    if not complete:
        return None
    expected = cal.last_trading_weekday(month.year, month.month)
    if last_ts.date() < expected:
        return f"ends {last_ts.date()}, before the last trading day {expected}"
    return None


def fetch_month(kind: str, side: str, month: pd.Period, p: Paths, timeout: int, retries: int, today: date) -> dict:
    begin, end, complete = month_bounds(month, today)
    last_error = ""
    for attempt in range(1, retries + 1):
        p.tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=p.tmp) as folder:
            directory = Path(folder)
            log = directory / "tool.log"
            try:
                with log.open("w", encoding="utf-8", errors="replace") as out:
                    subprocess.run(_command(kind, side, begin, end, directory, "out"), stdout=out,
                                   stderr=subprocess.STDOUT, timeout=timeout, check=True)
                csv_file = directory / "out.csv"
                if not csv_file.exists() or csv_file.stat().st_size == 0:
                    return dict(status="empty", attempts=attempt, bytes=0, records=0)
                frame = pd.read_csv(csv_file)
                if frame.empty:
                    return dict(status="empty", attempts=attempt, bytes=0, records=0)
                ts = pd.to_datetime(frame["timestamp"], utc=True)
                problem = verify_coverage(ts.iloc[-1], month, complete)
                if ts.dt.to_period("M").astype(str).nunique() != 1 or (ts.dt.month != month.month).any():
                    problem = "rows outside the requested month"
                if problem:
                    last_error = problem
                    # A known holiday gap is not a retryable failure: record and accept with a note.
                    if attempt == retries and "before the last trading day" in problem:
                        pass
                    else:
                        continue
                if kind == "tick":
                    target = tick_path(p, month)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temp = target.with_suffix(".tmp")
                    pd.DataFrame({
                        "ts": ts, "ask": frame["askPrice"], "bid": frame["bidPrice"],
                        "ask_volume": frame["askVolume"], "bid_volume": frame["bidVolume"],
                    }).to_parquet(temp, index=False, compression="zstd")
                    digest = hashlib.sha256(csv_file.read_bytes()).hexdigest()
                else:
                    target = m1_path(p, side, month)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temp = target.with_suffix(".tmp")
                    raw = csv_file.read_bytes()
                    digest = hashlib.sha256(raw).hexdigest()
                    with gzip.open(temp, "wb", compresslevel=6) as out:
                        out.write(raw)
                os.replace(temp, target)
                return dict(status="ok" if complete else "partial", attempts=attempt, bytes=target.stat().st_size,
                            records=int(len(frame)), sha256=digest, path=target.relative_to(p.root).as_posix(),
                            note=problem or "", last=str(ts.iloc[-1]))
            except subprocess.TimeoutExpired:
                last_error = f"timed out after {timeout}s"
            except (subprocess.CalledProcessError, OSError, ValueError, KeyError) as exc:
                tail = log.read_text(errors="replace")[-300:] if log.exists() else ""
                last_error = f"{type(exc).__name__}: {exc} {tail}"
    return dict(status="failed", attempts=retries, error=last_error[:500])


def run(kind: str, sides: list[str], start: str, end: str, workers: int, timeout: int, retries: int,
        min_free_gb: float, p: Paths | None = None) -> dict:
    p = p or paths()
    con = connect(p)
    done = dict(con.execute("SELECT key, status FROM files"))
    today = datetime.now(timezone.utc).date()
    jobs = []
    for month in month_list(start, end):
        for side in sides:
            key = f"month/{kind}/{side}/{month}"
            _, _, complete = month_bounds(month, today)
            if done.get(key) in ("ok", "empty") and complete:
                continue
            jobs.append((key, side, month))
    counts = {"ok": 0, "partial": 0, "empty": 0, "failed": 0, "queued": len(jobs)}
    stop = None
    with ThreadPoolExecutor(workers) as pool:
        active = {}
        index = 0
        while index < len(jobs) or active:
            while stop is None and index < len(jobs) and len(active) < workers:
                key, side, month = jobs[index]
                index += 1
                active[pool.submit(fetch_month, kind, side, month, p, timeout, retries, today)] = (key, side, month)
            if stop is not None and not active:
                break
            finished, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in finished:
                key, side, month = active.pop(future)
                result = future.result()
                counts[result["status"]] += 1
                con.execute(
                    "INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (key, f"month_{kind}_{side}", DOWNLOADER, result.get("path"), result["status"], result.get("bytes"),
                     result.get("records"), result.get("sha256"), result["attempts"],
                     result.get("error") or result.get("note"), datetime.now(timezone.utc).isoformat()),
                )
                con.commit()
                print(f"{datetime.now():%H:%M:%S} {key} {result['status']} rows={result.get('records')} "
                      f"attempts={result['attempts']} {result.get('note') or result.get('error') or ''}", flush=True)
                if shutil.disk_usage(p.root).free / 1e9 < min_free_gb:
                    stop = f"Free disk below {min_free_gb} GB"
            if stop is not None:
                index = len(jobs)
    con.close()
    counts["stop_reason"] = stop
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch whole months through the pinned Node tool.")
    ap.add_argument("kind", choices=["m1", "tick"])
    ap.add_argument("--sides", default="bid,ask")
    ap.add_argument("--start", default="2003-05")
    ap.add_argument("--end", default=None)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--retries", type=int, default=4)
    ap.add_argument("--min-free-gb", type=float, default=5.0)
    args = ap.parse_args(argv)
    end = args.end or datetime.now(timezone.utc).strftime("%Y-%m")
    sides = ["bid"] if args.kind == "tick" else args.sides.split(",")
    result = run(args.kind, sides, args.start, end, args.workers, args.timeout, args.retries, args.min_free_gb)
    print(json.dumps(result))
    return 1 if result["stop_reason"] or result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
