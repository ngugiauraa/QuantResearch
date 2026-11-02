"""The discovery feature store: memory-mapped arrays that every worker shares without copying.

It is built ONLY from data closed before the holdout start. External events published after it are dropped too.
So no discovery code can read the holdout, whatever it does: the data is not in the store. Opening a store
whose last bar closes after the holdout start raises, except through `holdout.py`'s single-use path.

Layout, per dataset and timeframe:
  meta.json       columns and their families, availability, quality and definitions; checksums; period
  features.npy    float32 [n_features, n_bars]
  prices.npy      float64 [len(PRICE_COLUMNS), n_bars]
  times.npy       int64 [3, n_bars]: open time, close time, trading-day id (ns)
  swap.npy        float64 [2, n_bars]: cumulative assumed financing per ounce, long and short
"""
from __future__ import annotations

import gc
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from quantresearch.core import timeframes as tfs
from quantresearch.core.config import Paths, paths
from quantresearch.discovery import features as feat
from quantresearch.discovery.config import HOLDOUT_START, store_dir

SWAP_MARKUP = 0.025
SWAP_NOTE = (f"Assumed financing, not broker data: a long pays fed funds + {SWAP_MARKUP:.1%} a year on the gold value; a short pays "
             f"max(0, {SWAP_MARKUP:.1%} - fed funds) and never earns a credit. Charged per calendar night, so weekends count.")


class HoldoutBreach(RuntimeError):
    """A discovery store contains data at or after the holdout start."""


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def swap_arrays(prices: pd.DataFrame, fed_funds: np.ndarray) -> np.ndarray:
    ff = pd.Series(fed_funds).ffill().fillna(0.0).to_numpy() / 100.0
    close = prices["close"].to_numpy(float)
    long_rate = -close * (ff + SWAP_MARKUP) / 360.0
    short_rate = -close * np.maximum(0.0, SWAP_MARKUP - ff) / 360.0
    dates = pd.DatetimeIndex(prices["ts"]).as_unit("ns").asi8 // 86_400_000_000_000
    nights = np.r_[0, np.diff(dates)].clip(min=0)
    return np.vstack([np.cumsum(long_rate * nights), np.cumsum(short_rate * nights)])


def _write_tf(folder: Path, tf: str, prices: pd.DataFrame, cat: feat.Catalog, holdout_start: pd.Timestamp, dataset: str,
              source: dict) -> dict:
    tmp = folder.with_name(folder.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    names = sorted(cat.columns)
    n = len(prices)
    mat = np.lib.format.open_memmap(tmp / "features.npy", mode="w+", dtype="float32", shape=(len(names), n))
    for i, name in enumerate(names):
        mat[i] = cat.columns[name]
    mat.flush()
    del mat
    np.save(tmp / "prices.npy", np.vstack([prices[c].to_numpy(float) for c in feat.PRICE_COLUMNS]))
    day = tfs.bucket(prices["close_ts"] - pd.Timedelta(nanoseconds=1), "d1ny")
    times = np.vstack([pd.DatetimeIndex(prices["ts"]).as_unit("ns").asi8, pd.DatetimeIndex(prices["close_ts"]).as_unit("ns").asi8,
                       pd.DatetimeIndex(day).as_unit("ns").asi8])
    np.save(tmp / "times.npy", times)
    ff = cat.columns.get("fed_funds")
    np.save(tmp / "swap.npy", swap_arrays(prices, ff if ff is not None else np.zeros(n)))
    meta = {
        "dataset": dataset, "timeframe": tf, "bars": n, "first_open": str(prices["ts"].iloc[0]), "last_close": str(prices["close_ts"].iloc[-1]),
        "holdout_start": str(holdout_start), "built_at": datetime.now(timezone.utc).isoformat(), "features": names,
        "catalog": {k: cat.meta[k] for k in names}, "price_columns": list(feat.PRICE_COLUMNS), "swap_note": SWAP_NOTE, "source": source,
        "excluded_series": list(feat.FORBIDDEN_SERIES),
    }
    meta["sha256"] = {f: _sha(tmp / f) for f in ("features.npy", "prices.npy", "times.npy", "swap.npy")}
    (tmp / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    if folder.exists():
        shutil.rmtree(folder)
    os.replace(tmp, folder)
    return meta


def _research_bars(tf: str, holdout_start: pd.Timestamp, p: Paths) -> pd.DataFrame:
    from quantresearch.warehouse.store import load_bars
    bars = load_bars(tf, None, holdout_start, p=p)
    return bars[bars["close_ts"] <= holdout_start].reset_index(drop=True)


def _cross(holdout_start: pd.Timestamp, p: Paths) -> dict[str, pd.DataFrame]:
    from quantresearch.external import store as ext
    out = {}
    for sym in feat.CROSS:
        try:
            frame = ext.load_cross(sym, "h1", p)
        except FileNotFoundError:
            continue
        out[sym] = frame[frame["close_ts"] <= holdout_start].reset_index(drop=True)
    return out


def _events(holdout_start: pd.Timestamp, p: Paths) -> pd.DataFrame:
    from quantresearch.external import store as ext
    events = ext.read_events(p)
    if events.empty:
        return events
    events = events[~events["series"].isin(feat.FORBIDDEN_SERIES)]
    # FOMC rows are a published calendar. Everything else must have been published before the holdout.
    keep = (events["published_ts"] < holdout_start) | ((events["series"] == "FOMC_DECISION") & (events["event_ts"] < holdout_start + pd.Timedelta(days=60)))
    return events[keep].reset_index(drop=True)


def build(dataset: str = "real", timeframes=("m15", "h1", "h4", "d1ny"), p: Paths | None = None, seed: int = 7,
          holdout_start: pd.Timestamp = HOLDOUT_START, out_root: Path | None = None, bars_override: dict | None = None,
          log=print) -> dict:
    """Build the store for 'real' warehouse data or a sign-randomised 'null' market built from real M15 bars."""
    p = p or paths()
    root = out_root or store_dir(p, dataset if dataset == "real" else f"{dataset}_{seed}")
    root.mkdir(parents=True, exist_ok=True)
    cross = _cross(holdout_start, p) if bars_override is None else {}
    events = _events(holdout_start, p) if bars_override is None else pd.DataFrame()
    cache: dict[str, pd.DataFrame] = {}

    def bars_for(tf: str) -> pd.DataFrame:
        if tf in cache:
            return cache[tf]
        if bars_override is not None:
            frame = bars_override[tf]
        elif dataset == "real":
            frame = _research_bars(tf, holdout_start, p)
        else:
            if "m15_null" not in cache:
                cache["m15_null"] = feat.null_bars(_research_bars("m15", holdout_start, p), seed)
            base = cache["m15_null"]
            frame = feat.finish_bars(base if tf == "m15" else feat.aggregate(base, tf), tf)
            frame = frame[frame["close_ts"] <= holdout_start].reset_index(drop=True)
        cache[tf] = frame
        return frame

    out = {}
    for tf in timeframes:
        log(f"[store] {dataset} {tf}: building")
        bars = bars_for(tf)
        if len(bars) and bars["close_ts"].iloc[-1] > holdout_start:
            raise HoldoutBreach(f"{tf} bars reach {bars['close_ts'].iloc[-1]}")
        higher = {h: bars_for(h) for h in feat.HIGHER.get(tf, ()) if h in ("h1", "h4", "d1ny")}
        prices, cat = feat.build_catalog(tf, bars, higher, cross, events)
        source = {"kind": dataset, "seed": seed if dataset != "real" else None, "cross": sorted(cross), "event_series": sorted(events["series"].unique()) if len(events) else []}
        meta = _write_tf(root / tf, tf, prices, cat, holdout_start, dataset, source)
        out[tf] = {"bars": meta["bars"], "features": len(meta["features"]), "last_close": meta["last_close"]}
        log(f"[store] {dataset} {tf}: {meta['bars']} bars, {len(meta['features'])} features")
        del prices, cat
        gc.collect()
    return out


class FeatureStore:
    """Read-only, memory-mapped view of one timeframe."""

    def __init__(self, folder: Path, allow_holdout: bool = False, verify: bool = False):
        self.folder = Path(folder)
        self.meta = json.loads((self.folder / "meta.json").read_text(encoding="utf-8"))
        self.tf = self.meta["timeframe"]
        self.dataset = self.meta["dataset"]
        last = pd.Timestamp(self.meta["last_close"])
        if not allow_holdout and last > pd.Timestamp(self.meta["holdout_start"]):
            raise HoldoutBreach(f"Store {folder} reaches {last}, past the holdout start")
        if verify:
            for name, digest in self.meta["sha256"].items():
                if _sha(self.folder / name) != digest:
                    raise ValueError(f"Checksum mismatch in {self.folder / name}")
        self.names: list[str] = self.meta["features"]
        self.index = {n: i for i, n in enumerate(self.names)}
        self._features = np.load(self.folder / "features.npy", mmap_mode="r")
        prices = np.load(self.folder / "prices.npy", mmap_mode="r")
        self.prices = {c: prices[i] for i, c in enumerate(self.meta["price_columns"])}
        times = np.load(self.folder / "times.npy", mmap_mode="r")
        self.open_ns, self.close_ns, self.day_ns = times[0], times[1], times[2]
        swap = np.load(self.folder / "swap.npy", mmap_mode="r")
        self.swap_long, self.swap_short = swap[0], swap[1]
        self.n = int(self.meta["bars"])

    def feature(self, name: str) -> np.ndarray:
        if name not in self.index:
            raise KeyError(f"Unknown feature {name} for {self.tf}")
        return self._features[self.index[name]]

    def family(self, name: str) -> str:
        return self.meta["catalog"][name]["family"]

    def bounds(self, start: pd.Timestamp, end: pd.Timestamp) -> tuple[int, int]:
        """Rows whose bar opens in [start, end) and closes by end."""
        s = int(np.searchsorted(self.open_ns, pd.Timestamp(start).as_unit("ns").value, side="left"))
        e = int(np.searchsorted(self.close_ns, pd.Timestamp(end).as_unit("ns").value, side="right"))
        return s, max(s, e)


_OPEN: dict[str, FeatureStore] = {}


def open_store(root: Path, tf: str) -> FeatureStore:
    key = str(Path(root) / tf)
    if key not in _OPEN:
        _OPEN[key] = FeatureStore(Path(root) / tf)
    return _OPEN[key]


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Build the discovery feature store (research period only).")
    ap.add_argument("--dataset", default="real", choices=["real", "null"])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--tf", nargs="*", default=["m15", "h1", "h4", "d1ny"])
    args = ap.parse_args()
    print(json.dumps(build(args.dataset, tuple(args.tf), seed=args.seed), indent=1))


if __name__ == "__main__":
    main()
