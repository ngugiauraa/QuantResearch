"""Store and read external data. Refreshing one source never disturbs another."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from quantresearch.core.config import Paths, paths
from quantresearch.external import sources as src
from quantresearch.features import pit


def _events_path(p: Paths) -> str:
    return str(p.external / "events")


def write_events(name: str, frame: pd.DataFrame, p: Paths | None = None) -> None:
    p = p or paths()
    folder = p.external / "events"
    folder.mkdir(parents=True, exist_ok=True)
    temp = folder / f"{name}.parquet.tmp"
    frame.to_parquet(temp, index=False)
    os.replace(temp, folder / f"{name}.parquet")


def read_events(p: Paths | None = None) -> pd.DataFrame:
    p = p or paths()
    files = sorted((p.external / "events").glob("*.parquet")) if (p.external / "events").exists() else []
    if not files:
        return pd.DataFrame(columns=pit.EVENT_COLUMNS + ["pub_basis"])
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def refresh_all(p: Paths | None = None, cross: bool = True) -> dict:
    """Fetch everything. Each failure is reported and does not stop the others."""
    p = p or paths()
    status: dict[str, str] = {}
    for series in src.FRED_SERIES:
        try:
            write_events(f"fred_{series}", src.fetch_fred(series), p)
            status[series] = "ok"
        except Exception as exc:  # A dead provider must not stop the rest.
            status[series] = f"failed: {exc}"
    for name, job in (("fomc", src.fetch_fomc), ("cot", src.fetch_cot)):
        try:
            frame = job()
            write_events(name, frame, p)
            status[name] = f"ok ({len(frame)} rows)"
        except Exception as exc:
            status[name] = f"failed: {exc}"
    if cross:
        for symbol in src.CROSS_SYMBOLS:
            try:
                status[symbol] = str(src.fetch_cross(symbol, start=src.CROSS_START.get(symbol, "2003-05-01"), p=p).name)
            except Exception as exc:
                status[symbol] = f"failed: {exc}"
    report = {"refreshed_at": datetime.now(timezone.utc).isoformat(), "status": status}
    p.external.mkdir(parents=True, exist_ok=True)
    (p.external / "status.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


def load_cross(symbol: str, timeframe: str = "h1", p: Paths | None = None) -> pd.DataFrame:
    p = p or paths()
    frame = pd.read_parquet(p.external / "cross" / f"{symbol}_{timeframe}.parquet")
    seconds = {"h1": 3600, "m1": 60, "d1": 86400}[timeframe]
    frame["close_ts"] = frame["ts"] + pd.Timedelta(seconds=seconds)
    return frame


def attach_cross(base: pd.DataFrame, symbols: list[str], p: Paths | None = None, timeframe: str = "h1") -> pd.DataFrame:
    """Add <symbol>_close: the latest cross-market close already finished at each base bar's close."""
    out = base.copy()
    for symbol in symbols:
        try:
            cross = load_cross(symbol, timeframe, p)
        except FileNotFoundError:
            out[f"{symbol}_close"] = np.nan
            continue
        aligned = pit.align_higher_tf(base, cross, ["close"], f"{symbol}_")
        out[f"{symbol}_close"] = aligned[f"{symbol}_close"].to_numpy()
    return out


MACRO_FEATURES = {"DFII10": "real_yield", "DGS10": "yield_10y", "DGS2": "yield_2y", "DTWEXBGS": "dollar_broad", "VIXCLS": "vix"}


def macro_features(base: pd.DataFrame, events: pd.DataFrame | None = None, p: Paths | None = None) -> pd.DataFrame:
    """Levels and 5-observation changes, each using only what was published by the bar's close."""
    events = read_events(p) if events is None else events
    columns: dict[str, pd.Series] = {}
    for series, name in MACRO_FEATURES.items():
        level = pit.align_events(base["close_ts"], events, series)
        columns[name] = level
        rows = events[events["series"] == series].sort_values("event_ts").copy()
        rows["value"] = rows["value"].diff(5)
        columns[f"{name}_chg5"] = pit.align_events(base["close_ts"], rows.dropna(subset=["value"]), series)
    fomc = events[events["series"] == "FOMC_DECISION"]["event_ts"]
    columns["hours_to_fomc"] = event_proximity(base["close_ts"], fomc)
    return pd.DataFrame(columns, index=base.index).astype("float64")


def event_proximity(times: pd.Series, schedule: pd.Series) -> pd.Series:
    """Hours to the next scheduled event. A published calendar is knowable in advance."""
    if schedule is None or len(schedule) == 0:
        return pd.Series(np.nan, index=times.index)
    # Both sides are converted to nanoseconds: pandas columns can differ in time unit.
    grid = np.sort(pd.DatetimeIndex(schedule).as_unit("ns").asi8)
    now = pd.DatetimeIndex(times).as_unit("ns").asi8
    pos = np.searchsorted(grid, now, side="left")
    out = np.full(len(now), np.nan)
    has_next = pos < len(grid)
    out[has_next] = (grid[pos[has_next]] - now[has_next]) / 3.6e12
    return pd.Series(out, index=times.index)


def main() -> None:
    ap = argparse.ArgumentParser(description="Refresh external data.")
    ap.add_argument("--no-cross", action="store_true")
    args = ap.parse_args()
    print(json.dumps(refresh_all(cross=not args.no_cross), indent=1))


if __name__ == "__main__":
    main()
