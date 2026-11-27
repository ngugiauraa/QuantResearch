"""Build the versioned bar warehouse from the immutable provider archive.

M1 is decoded from original files, with bid and ask side by side. Higher timeframes
are aggregated from M1, so every timeframe agrees exactly. Invalid rows go to quarantine
with a reason and are never altered. Each output records the checksums of its inputs.
Files are written atomically, so an interrupted build cannot leave a half-written month.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from quantresearch.core import timeframes as tfs
from quantresearch.core.config import Paths, paths
from quantresearch.warehouse import dukascopy as dk

BAR_COLUMNS = [
    "ts", "bid_open", "bid_high", "bid_low", "bid_close",
    "ask_open", "ask_high", "ask_low", "ask_close",
    "bid_volume", "ask_volume", "m1_count", "spread_mean", "spread_max", "flags",
]
# Flags describe rows that were kept; nothing is changed because of them.
FLAG_NO_ASK, FLAG_NO_BID, FLAG_CROSSED, FLAG_SPIKE = 1, 2, 4, 8
# The row came from the broker (MT5), not the provider archive: bid-only bars, ask = bid + the broker's bar spread.
FLAG_BROKER = 16
SPIKE_RETURN = 0.015


def _check_ohlc(frame: pd.DataFrame) -> pd.Series:
    """Reason per row, empty when valid."""
    o, h, l, c = frame["open"], frame["high"], frame["low"], frame["close"]
    reason = pd.Series("", index=frame.index, dtype=object)
    reason[(o <= 0) | (h <= 0) | (l <= 0) | (c <= 0)] += "NONPOSITIVE;"
    reason[h < np.maximum.reduce([o, c, l])] += "HIGH_BELOW_OHLC;"
    reason[l > np.minimum.reduce([o, c, h])] += "LOW_ABOVE_OHLC;"
    return reason


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_csv_month(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["ts"] = pd.to_datetime(frame.pop("timestamp"), utc=True)
    return frame[["ts", "open", "high", "low", "close", "volume"]]


def load_month_side(year: int, month: int, side: str, p: Paths) -> tuple[pd.DataFrame | None, dict, str]:
    """One month of one side from the best source available, and which source that was.

    Priority: verified monthly file, then provider day files, then the first-generation archive.
    Day files fill days the monthly file lacks. The first-generation archive lacks month ends;
    the audit reports that rather than this code hiding it.
    """
    inputs: dict[str, str] = {}
    frames: list[pd.DataFrame] = []
    source = "none"
    monthly = p.warehouse / "csv" / "m1" / side / str(year) / f"xauusd_{side}_m1_{year}_{month:02d}.csv.gz"
    if monthly.exists():
        frames.append(_read_csv_month(monthly))
        inputs[monthly.relative_to(p.root).as_posix()] = _sha(monthly)
        source = "monthly"
    first = date(year, month, 1)
    last = date(year + (month == 12), 1 if month == 12 else month + 1, 1) - timedelta(days=1)
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        path = p.bi5 / "m1" / side / str(day.year) / f"{day}.bi5"
        if path.exists():
            frames.append(dk.decode_candles(path.read_bytes(), day))
            inputs[path.relative_to(p.root).as_posix()] = _sha(path)
            source = "monthly+day" if source == "monthly" else "day"
    if not frames:
        legacy = p.root / "raw" / "xauusd" / "m1" / str(year) / f"xauusd_{side}_m1_{year}_{month:02d}.csv"
        if legacy.exists():
            frames.append(_read_csv_month(legacy))
            inputs[legacy.relative_to(p.root).as_posix()] = _sha(legacy)
            source = "legacy"
    if not frames:
        return None, inputs, source
    merged = pd.concat(frames, ignore_index=True).drop_duplicates("ts", keep="first").sort_values("ts", ignore_index=True)
    return merged, inputs, source


def build_month(year: int, month: int, p: Paths | None = None) -> dict:
    p = p or paths()
    bids, asks, rejected, inputs, sources = [], [], [], {}, {}
    for side, store in (("bid", bids), ("ask", asks)):
        frame, used, source = load_month_side(year, month, side, p)
        inputs.update(used)
        sources[side] = source
        if frame is None or frame.empty:
            continue
        reason = _check_ohlc(frame)
        bad = reason != ""
        if bad.any():
            rejected.append(frame[bad].assign(side=side, reason=reason[bad]))
        store.append(frame[~bad])
    if not bids and not asks:
        return {"year": year, "month": month, "rows": 0, "rejected": 0}
    bid = pd.concat(bids) if bids else pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    ask = pd.concat(asks) if asks else pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    bars = bid.rename(columns={c: f"bid_{c}" for c in bid.columns if c != "ts"}).merge(
        ask.rename(columns={c: f"ask_{c}" for c in ask.columns if c != "ts"}), on="ts", how="outer"
    ).sort_values("ts", ignore_index=True)
    bars["m1_count"] = 1
    spread = bars["ask_close"] - bars["bid_close"]
    bars["spread_mean"] = spread
    bars["spread_max"] = spread
    flags = np.zeros(len(bars), dtype="int64")
    flags[bars["ask_close"].isna().to_numpy()] |= FLAG_NO_ASK
    flags[bars["bid_close"].isna().to_numpy()] |= FLAG_NO_BID
    flags[(spread < 0).to_numpy()] |= FLAG_CROSSED
    # A spike reverses at once: a large move followed by an opposite large move.
    ret = bars["bid_close"].pct_change(fill_method=None)
    spike = (ret.abs() > SPIKE_RETURN) & (ret.shift(-1).abs() > SPIKE_RETURN) & (np.sign(ret) != np.sign(ret.shift(-1)))
    flags[spike.fillna(False).to_numpy()] |= FLAG_SPIKE
    bars["flags"] = flags
    _write_tf(bars[BAR_COLUMNS], "m1", year, month, p)
    if rejected:
        out = p.quarantine / f"{year}-{month:02d}.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        pd.concat(rejected).to_csv(out, index=False)
    meta = {
        "year": year, "month": month, "rows": int(len(bars)), "sources": sources,
        "rejected": int(sum(len(r) for r in rejected)),
        "flags": {name: int((flags & bit > 0).sum()) for name, bit in
                  (("no_ask", FLAG_NO_ASK), ("no_bid", FLAG_NO_BID), ("crossed", FLAG_CROSSED), ("spike", FLAG_SPIKE))},
        "inputs_sha256": hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest(),
        "input_files": len(inputs),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    target = p.bars / "m1" / f"year={year}" / f"month={month:02d}" / "meta.json"
    target.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def _write_tf(frame: pd.DataFrame, tf: str, year: int, month: int, p: Paths) -> Path:
    out_dir = p.bars / tf / f"year={year}" / f"month={month:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "bars.parquet"
    temp = out_dir / "bars.parquet.tmp"
    frame.to_parquet(temp, index=False, compression="zstd")
    os.replace(temp, target)
    return target


def aggregate(m1: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Aggregate M1 to a higher timeframe. Bars are labelled by their open time."""
    tfs.validate(tf)
    key = tfs.bucket(m1["ts"], tf)
    grouped = m1.groupby(key, sort=True)
    out = grouped.agg(
        bid_open=("bid_open", "first"), bid_high=("bid_high", "max"),
        bid_low=("bid_low", "min"), bid_close=("bid_close", "last"),
        ask_open=("ask_open", "first"), ask_high=("ask_high", "max"),
        ask_low=("ask_low", "min"), ask_close=("ask_close", "last"),
        bid_volume=("bid_volume", "sum"), ask_volume=("ask_volume", "sum"),
        m1_count=("m1_count", "sum"), spread_max=("spread_max", "max"),
        flags=("flags", lambda x: int(np.bitwise_or.reduce(x.to_numpy()))),
    )
    weighted = (m1["spread_mean"] * m1["m1_count"]).groupby(key, sort=True).sum()
    out["spread_mean"] = weighted / out["m1_count"]
    out.index.name = "ts"
    return out.reset_index()[BAR_COLUMNS]


def read_tf(tf: str, p: Paths | None = None) -> pd.DataFrame:
    p = p or paths()
    files = sorted((p.bars / tf).glob("year=*/month=*/bars.parquet"))
    if not files:
        return pd.DataFrame(columns=BAR_COLUMNS)
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def build_higher(p: Paths | None = None, frames=tfs.BUILT_FROM_M1) -> dict:
    """Rebuild every higher timeframe from the current M1 store.

    Partitions are by the bar's open time. A bar that opens in one month and spans into
    the next stays whole, which matters for H4 and the New York-day bars.
    """
    p = p or paths()
    m1 = read_tf("m1", p)
    counts = {}
    for tf in frames:
        bars = aggregate(m1, tf)
        counts[tf] = int(len(bars))
        for (y, m), part in bars.groupby([bars["ts"].dt.year, bars["ts"].dt.month]):
            _write_tf(part, tf, int(y), int(m), p)
    return counts


def build_all(start: str = "2003-05", end: str | None = None, p: Paths | None = None) -> dict:
    p = p or paths()
    end = end or datetime.now(timezone.utc).strftime("%Y-%m")
    months = pd.period_range(start, end, freq="M")
    summary = {"months": 0, "rows": 0, "rejected": 0, "flags": {}}
    for period in months:
        meta = build_month(period.year, period.month, p)
        if meta["rows"]:
            summary["months"] += 1
            summary["rows"] += meta["rows"]
            summary["rejected"] += meta["rejected"]
            for k, v in meta.get("flags", {}).items():
                summary["flags"][k] = summary["flags"].get(k, 0) + v
    summary["higher"] = build_higher(p)
    p.reports.mkdir(parents=True, exist_ok=True)
    (p.reports / "build_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the bar warehouse from provider files.")
    ap.add_argument("--start", default="2003-05")
    ap.add_argument("--end")
    args = ap.parse_args()
    print(json.dumps(build_all(args.start, args.end), indent=1))


if __name__ == "__main__":
    main()
