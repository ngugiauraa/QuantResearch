"""Current bars: the warehouse for history, then the broker's own M1 bars from where the warehouse ends.

The warehouse is only as fresh as its last build. When MT5 is attached, `LiveBars.load` appends the broker's M1
bars after the warehouse's last M1 bar and builds higher timeframes from them with the warehouse's own
aggregation, so every timeframe still agrees. Nothing here is written to disk; `warehouse.mt5_sync` does that.

Broker rows carry FLAG_BROKER. Their ask is the broker's bid plus the broker's reported spread for that bar,
because MT5 bars are bid-only. Research and replays read the warehouse directly and never see these rows.
"""
from __future__ import annotations

import logging
import threading
import time

import duckdb
import pandas as pd

from quantresearch.core import timeframes as tfs
from quantresearch.core.config import Paths
from quantresearch.warehouse import store
from quantresearch.warehouse.build import BAR_COLUMNS, FLAG_BROKER, aggregate

log = logging.getLogger(__name__)
M1 = pd.Timedelta(minutes=1)


def broker_rows_to_bars(rows: list[dict]) -> pd.DataFrame:
    """MT5 M1 rows (from Mt5Broker.bars_m1) in the warehouse's M1 column layout."""
    if not rows:
        return pd.DataFrame(columns=BAR_COLUMNS)
    raw = pd.DataFrame(rows)
    spread = raw["spread"].astype(float)
    frame = pd.DataFrame({
        "ts": pd.to_datetime(raw["ts"], utc=True),
        "bid_open": raw["open"], "bid_high": raw["high"], "bid_low": raw["low"], "bid_close": raw["close"],
        "ask_open": raw["open"] + spread, "ask_high": raw["high"] + spread, "ask_low": raw["low"] + spread, "ask_close": raw["close"] + spread,
        # MT5 only has tick counts. Split evenly so total volume equals the tick volume.
        "bid_volume": raw["tick_volume"] / 2, "ask_volume": raw["tick_volume"] / 2,
        "m1_count": 1, "spread_mean": spread, "spread_max": spread, "flags": FLAG_BROKER,
    })
    frame["m1_count"] = frame["m1_count"].astype("int64")
    frame["flags"] = frame["flags"].astype("int64")
    return frame[BAR_COLUMNS].sort_values("ts", ignore_index=True)


def warehouse_end(p: Paths) -> pd.Timestamp | None:
    """Open time of the last M1 bar in the warehouse, from the newest partition only."""
    base = p.bars / "m1"
    files = sorted(base.glob("year=*/month=*/bars.parquet"), key=lambda f: (f.parent.parent.name, f.parent.name))
    if not files:
        return None
    con = duckdb.connect(":memory:")
    try:
        con.execute("SET TimeZone='UTC'")
        value = con.execute(f"SELECT max(ts) FROM read_parquet('{store._sql_path(files[-1])}')").fetchone()[0]
    finally:
        con.close()
    return None if value is None else store._ts(value)


class LiveBars:
    """Warehouse bars extended with the broker's bars. Without MT5 it is exactly `store.load_bars`."""

    def __init__(self, broker, p: Paths, ttl: float = 5.0):
        self.broker = broker
        self.p = p
        self.ttl = ttl
        self._lock = threading.Lock()
        self._tail: tuple[float, pd.Timestamp, pd.DataFrame] | None = None
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return hasattr(self.broker, "bars_m1")

    def tail(self, since: pd.Timestamp) -> pd.DataFrame:
        """Broker M1 bars opening at or after `since` that have closed. Cached for `ttl` seconds."""
        with self._lock:
            now = time.monotonic()
            if self._tail and self._tail[1] == since and now - self._tail[0] < self.ttl:
                return self._tail[2]
            clock = pd.Timestamp.now(tz="UTC")
            try:
                rows = self.broker.bars_m1(since.to_pydatetime(), clock.floor("min").to_pydatetime())
                self.last_error = None
            except Exception as exc:  # A broker hiccup falls back to the warehouse alone, and is reported.
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("MT5 bar feed unavailable: %s", self.last_error)
                rows = []
            frame = broker_rows_to_bars(rows)
            self._tail = (now, since, frame)
            return frame

    def status(self) -> dict:
        end = warehouse_end(self.p)
        out = {"enabled": self.enabled, "warehouse_end": end, "error": self.last_error, "broker_end": None}
        if self.enabled and end is not None:
            tail = self.tail(end + M1)
            out["broker_end"] = tail["ts"].iloc[-1] if len(tail) else None
        return out

    def load(self, tf: str, start=None, end=None, as_of=None) -> pd.DataFrame:
        frame = store.load_bars(tf, start, end, as_of, p=self.p)
        if not self.enabled or tf == "s1":
            return frame
        last = warehouse_end(self.p)
        if last is None:
            return frame
        cutoff = last + M1
        start_ts, end_ts, as_of_ts = store._ts(start), store._ts(end), store._ts(as_of)
        if (end_ts is not None and end_ts <= cutoff) or (as_of_ts is not None and as_of_ts <= cutoff):
            return frame
        tail = self.tail(cutoff)
        if tail.empty:
            return frame
        if tf == "m1":
            extra = tail
            keep = frame
        else:
            # Rebuild the warehouse's last, possibly partial, bar together with the broker's bars.
            first = tfs.bucket(pd.Series([cutoff]), tf).iloc[0]
            seed = store.load_bars("m1", first, cutoff, p=self.p)[BAR_COLUMNS] if first < cutoff else pd.DataFrame(columns=BAR_COLUMNS)
            extra = aggregate(pd.concat([seed, tail], ignore_index=True), tf)
            keep = frame[frame["ts"] < first]
        extra = store._finish(extra.copy(), tf, as_of_ts)
        if start_ts is not None:
            extra = extra[extra["ts"] >= start_ts]
        if end_ts is not None:
            extra = extra[extra["ts"] < end_ts]
        if extra.empty:
            return frame
        return pd.concat([keep, extra[frame.columns]], ignore_index=True).sort_values("ts", ignore_index=True)
