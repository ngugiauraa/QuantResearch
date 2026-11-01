from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from quantresearch.data_quality import canonicalize

def consolidate(source: Path, output: Path, timeframe: str, price_side: str = "bid") -> Path:
    # Its layout differs from the validated store, and a query layer scanning that folder would count rows twice.
    if output.resolve() == (Path("data/processed/parquet")).resolve():
        raise ValueError("Refusing to write into the validated Parquet archive. Choose a separate --output.")
    files = sorted(source.rglob("*.csv"))
    if not files:
        raise FileNotFoundError(f"No CSV files found under {source}")
    frames = []
    for path in files:
        try:
            frame = canonicalize(pd.read_csv(path))
            required = {"timestamp", "open", "high", "low", "close"}
            if not required.issubset(frame.columns):
                print(f"SKIP schema mismatch: {path}")
                continue
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
            frame = frame.dropna(subset=["timestamp"])
            for col in ("open", "high", "low", "close", "volume"):
                if col in frame.columns:
                    frame[col] = pd.to_numeric(frame[col], errors="coerce")
            frame["source_file"] = path.name
            frames.append(frame)
        except Exception as exc:
            print(f"SKIP unreadable {path}: {exc}")
    if not frames:
        raise ValueError("No valid candle CSVs were found.")
    data = pd.concat(frames, ignore_index=True)
    before = len(data)
    data = data.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
    if len(data) != before:
        print(f"WARNING: dropped {before - len(data):,} rows with duplicate timestamps, keeping the last of each")
    data["instrument"] = "xauusd"
    data["timeframe"] = timeframe
    data["price_side"] = price_side
    destination = output / f"instrument=xauusd" / f"timeframe={timeframe}" / f"price_side={price_side}"
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / "part-000.parquet"
    data.to_parquet(target, index=False, compression="zstd")
    print(f"Wrote {len(data):,} rows to {target}")
    print(f"Coverage: {data['timestamp'].min()} -> {data['timestamp'].max()}")
    return target

def main():
    parser = argparse.ArgumentParser(description="Consolidate monthly XAUUSD CSVs into Parquet.")
    parser.add_argument("--source", type=Path, required=True, help="Directory containing one timeframe's monthly CSVs")
    parser.add_argument("--output", type=Path, default=Path("data/processed/consolidated"))
    parser.add_argument("--timeframe", required=True, choices=["tick", "s1", "m1", "m5", "m15", "m30", "h1", "h4", "d1", "mn1"])
    parser.add_argument("--price-side", default="bid", choices=["bid", "ask"])
    args = parser.parse_args()
    consolidate(args.source, args.output, args.timeframe, args.price_side)

if __name__ == "__main__":
    main()
