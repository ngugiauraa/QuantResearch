"""Read access to the warehouse. All history reaches the rest of the platform through here.

`load_bars(as_of=T)` returns only bars that have CLOSED by T, so a caller cannot see an
unfinished bar. Ticks and S1 bars are decoded from original files on demand.
"""
from __future__ import annotations

import functools
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from quantresearch.core import timeframes as tfs
from quantresearch.core.config import Paths, paths
from quantresearch.warehouse import dukascopy as dk


class WarehouseNotBuilt(FileNotFoundError):
    """No bars exist for a timeframe. A FileNotFoundError, so existing handlers keep working, with the fix in the message."""


def not_built_message(tf: str, base: Path, p: Paths) -> str:
    have_m1 = any((p.bars / "m1").glob("year=*/month=*/bars.parquet")) if (p.bars / "m1").exists() else False
    head = f"No {tf} bars have been built under {base}.\n"
    if tf != "m1" and have_m1:
        steps = ("The M1 bars exist, so build the higher timeframes from them (from the repository root, with PYTHONPATH=src):\n"
                 "  python -m quantresearch.warehouse.build --start 2003-05")
    else:
        steps = ("There is no warehouse data yet. Build it with these commands, from the repository root with PYTHONPATH=src:\n"
                 "  1. python -m quantresearch.warehouse.fetch_months m1 --workers 2   (downloads about 280 monthly files)\n"
                 "  2. python -m quantresearch.warehouse.build --start 2003-05          (builds every timeframe from M1)\n"
                 "  3. python -m quantresearch.warehouse.audit                          (completeness check)\n"
                 "  4. python -m quantresearch.external.store                           (macro and cross-market data some strategies use)\n"
                 "The download takes a long time and depends on your connection, but it is resumable: rerun the same command to continue.\n"
                 "The optional tick download (fetch_months tick) is separate and is not needed for bars; see README section 9.")
    return head + steps


def friendly_warehouse_errors(func):
    """For command-line entry points: print the fix and exit 1 instead of a traceback when no bars are built."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except WarehouseNotBuilt as exc:
            sys.stderr.write(f"{exc}\n")
            raise SystemExit(1) from None
    return wrapper


def _ts(value) -> pd.Timestamp | None:
    if value is None:
        return None
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _sql_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace("'", "''")


def load_bars(tf: str, start=None, end=None, as_of=None, p: Paths | None = None) -> pd.DataFrame:
    """Bars with start <= open time < end, restricted to those closed by `as_of`.

    Adds close_ts plus mid prices. Ask columns stay null where the ask is unobserved;
    they are never estimated here.
    """
    tfs.validate(tf)
    p = p or paths()
    if tf == "s1":
        return load_s1(start, end, as_of, p)
    start, end, as_of = _ts(start), _ts(end), _ts(as_of)
    base = p.bars / tf
    if not any(base.glob("year=*/month=*/bars.parquet")) if base.exists() else True:
        raise WarehouseNotBuilt(not_built_message(tf, base, p))
    pattern = _sql_path(base) + "/year=*/month=*/bars.parquet"
    where, params = [], []
    if start is not None:
        where.append("ts >= ?")
        params.append(start.to_pydatetime())
    if end is not None:
        where.append("ts < ?")
        params.append(end.to_pydatetime())
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    con = duckdb.connect(":memory:")
    try:
        con.execute("SET TimeZone='UTC'")
        frame = con.execute(
            f"SELECT * EXCLUDE (year, month) FROM read_parquet('{pattern}', hive_partitioning=true) {clause} ORDER BY ts",
            params,
        ).df()
    finally:
        con.close()
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    return _finish(frame, tf, as_of)


def _finish(frame: pd.DataFrame, tf: str, as_of: pd.Timestamp | None) -> pd.DataFrame:
    frame["close_ts"] = tfs.close_times(frame["ts"], tf)
    if as_of is not None:
        frame = frame[frame["close_ts"] <= as_of].reset_index(drop=True)
    for field in ("open", "high", "low", "close"):
        frame[field] = (frame[f"bid_{field}"] + frame[f"ask_{field}"]) / 2
        # Where the ask is unobserved, the plain price columns fall back to bid.
        frame[field] = frame[field].fillna(frame[f"bid_{field}"])
    frame["volume"] = frame["bid_volume"].fillna(0) + frame["ask_volume"].fillna(0)
    return frame


def tick_path(p: Paths, day: date, hour: int) -> Path:
    return p.bi5 / "tick" / str(day.year) / f"{day:%m}" / f"{day}_{hour:02d}.bi5"


def load_ticks(start, end, as_of=None, p: Paths | None = None) -> pd.DataFrame:
    """Original quotes with start <= ts < end and ts <= as_of, in provider order."""
    p = p or paths()
    start, end, as_of = _ts(start), _ts(end), _ts(as_of)
    frames = []
    months_done = set()
    hour = start.floor("h")
    while hour < end:
        monthly = p.warehouse / "ticks" / f"year={hour.year}" / f"month={hour.month:02d}" / "ticks.parquet"
        if monthly.exists():
            if monthly not in months_done:
                frames.append(pd.read_parquet(monthly))
                months_done.add(monthly)
        else:
            path = tick_path(p, hour.date(), hour.hour)
            if path.exists():
                frames.append(dk.decode_ticks(path.read_bytes(), hour.date(), hour.hour))
        hour += pd.Timedelta(hours=1)
    if not frames:
        return pd.DataFrame(columns=["ts", "ask", "bid", "ask_volume", "bid_volume"])
    out = pd.concat(frames, ignore_index=True)
    mask = (out["ts"] >= start) & (out["ts"] < end)
    if as_of is not None:
        mask &= out["ts"] <= as_of
    return out[mask].reset_index(drop=True)


def ticks_to_bars(ticks: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Build bid and ask candles from quotes. Spread comes from observed quotes."""
    if ticks.empty:
        return pd.DataFrame(columns=["ts"])
    key = tfs.bucket(ticks["ts"], tf)
    spread = ticks["ask"] - ticks["bid"]
    out = ticks.assign(_spread=spread).groupby(key, sort=True).agg(
        bid_open=("bid", "first"), bid_high=("bid", "max"), bid_low=("bid", "min"), bid_close=("bid", "last"),
        ask_open=("ask", "first"), ask_high=("ask", "max"), ask_low=("ask", "min"), ask_close=("ask", "last"),
        bid_volume=("bid_volume", "sum"), ask_volume=("ask_volume", "sum"),
        m1_count=("bid", "size"), spread_mean=("_spread", "mean"), spread_max=("_spread", "max"),
    )
    out["flags"] = 0
    out.index.name = "ts"
    return out.reset_index()


def load_s1(start, end, as_of=None, p: Paths | None = None) -> pd.DataFrame:
    if start is None or end is None:
        raise ValueError("S1 is built on demand; give both start and end")
    bars = ticks_to_bars(load_ticks(start, end, None, p), "s1")
    if bars.empty:
        return bars
    return _finish(bars, "s1", _ts(as_of))


def coverage(p: Paths | None = None) -> pd.DataFrame:
    p = p or paths()
    rows = []
    for tf in tfs.ALL:
        if tf == "s1":
            continue
        base = p.bars / tf
        if not base.exists():
            continue
        pattern = _sql_path(base) + "/year=*/month=*/bars.parquet"
        con = duckdb.connect(":memory:")
        con.execute("SET TimeZone='UTC'")
        n, a, b, asked = con.execute(
            f"SELECT count(*), min(ts), max(ts), count(ask_close) FROM read_parquet('{pattern}', hive_partitioning=false)"
        ).fetchone()
        con.close()
        rows.append(dict(timeframe=tf, rows=n, first=a, last=b, rows_with_ask=asked))
    return pd.DataFrame(rows)


def synthetic_bars(n: int, tf: str = "m1", start="2024-01-02 00:00", seed: int = 7, spread: float = 0.3,
                   vol: float = 0.0004, drift: float = 0.0, price: float = 2000.0) -> pd.DataFrame:
    """Reproducible random bars for software tests only. Not market data."""
    rng = np.random.default_rng(seed)
    step = pd.Timedelta(seconds=tfs.FIXED_SECONDS[tf])
    ts = pd.date_range(pd.Timestamp(start, tz="UTC"), periods=n, freq=step)
    close = price * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    open_ = np.r_[price, close[:-1]]
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    frame = pd.DataFrame({
        "ts": ts, "bid_open": open_, "bid_high": high, "bid_low": low, "bid_close": close,
        "ask_open": open_ + spread, "ask_high": high + spread, "ask_low": low + spread, "ask_close": close + spread,
        "bid_volume": rng.integers(100, 1000, n).astype(float), "ask_volume": rng.integers(100, 1000, n).astype(float),
        "m1_count": 1, "spread_mean": spread, "spread_max": spread, "flags": 0,
    })
    return _finish(frame, tf, None)


def one_day_later(day: date) -> date:
    return day + timedelta(days=1)
