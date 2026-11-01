from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

ALIASES = {
    "timestamp": ("timestamp", "datetime", "date", "time", "gmt time"),
    "open": ("open", "o", "bid_open", "ask_open"),
    "high": ("high", "h", "bid_high", "ask_high"),
    "low": ("low", "l", "bid_low", "ask_low"),
    "close": ("close", "c", "bid_close", "ask_close"),
    "volume": ("volume", "vol", "tickvol", "tick_volume"),
}

def canonicalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    rename = {}
    for canonical, candidates in ALIASES.items():
        for candidate in candidates:
            if candidate in df.columns:
                rename[candidate] = canonical
                break
    return df.rename(columns=rename)

def validate_csv(path: Path) -> dict:
    result = {"file": str(path), "errors": [], "warnings": [], "rows": 0}
    try:
        df = canonicalize(pd.read_csv(path))
    except Exception as exc:
        result["errors"].append(f"CSV read failed: {exc}")
        return result
    result["rows"] = len(df)
    required = ["timestamp", "open", "high", "low", "close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        result["errors"].append(f"Missing required columns: {missing}")
        return result
    ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    bad_ts = int(ts.isna().sum())
    if bad_ts:
        result["errors"].append(f"{bad_ts} invalid timestamps")
    valid_ts = ts.dropna()
    if valid_ts.duplicated().any():
        result["warnings"].append(f"{int(valid_ts.duplicated().sum())} duplicate timestamps")
    if not valid_ts.is_monotonic_increasing:
        result["warnings"].append("Timestamps are not sorted ascending")
    prices = df[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    invalid_prices = int(prices.isna().any(axis=1).sum())
    if invalid_prices:
        result["errors"].append(f"{invalid_prices} rows contain missing/non-numeric OHLC")
    bad_range = (prices["high"] < prices[["open", "close", "low"]].max(axis=1)) | (prices["low"] > prices[["open", "close", "high"]].min(axis=1))
    if bad_range.fillna(False).any():
        result["errors"].append(f"{int(bad_range.sum())} rows violate OHLC high/low bounds")
    if (prices <= 0).any(axis=None):
        result["warnings"].append("Non-positive price values detected")
    result["first_timestamp"] = valid_ts.min().isoformat() if len(valid_ts) else None
    result["last_timestamp"] = valid_ts.max().isoformat() if len(valid_ts) else None
    return result

def main():
    parser = argparse.ArgumentParser(description="Validate downloaded OHLC CSV files.")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/xauusd"))
    parser.add_argument("--output", type=Path, default=Path("data/processed/quality_report.json"))
    args = parser.parse_args()
    paths = args.paths or sorted(args.raw_dir.rglob("*.csv"))
    results = [validate_csv(p) for p in paths if p.is_file()]
    report = {"files_checked": len(results), "files_with_errors": sum(bool(x["errors"]) for x in results), "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for item in results:
        status = "FAIL" if item["errors"] else ("WARN" if item["warnings"] else "OK")
        print(f"{status:4} {item['file']} rows={item['rows']} errors={len(item['errors'])} warnings={len(item['warnings'])}")
    print(f"\nReport: {args.output}")

if __name__ == "__main__":
    main()
