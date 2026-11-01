"""Derive higher-timeframe XAUUSD candles from normalized 1-minute data."""

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = ROOT / "data" / "processed"

# Target label -> pandas frequency -> expected 1-minute observations
TIMEFRAMES = {
    "5m": ("5min", 5),
    "15m": ("15min", 15),
    "30m": ("30min", 30),
    "1h": ("1h", 60),
    "4h": ("4h", 240),
    "1d": ("1D", 1440),
}


def load_normalized_csv(path: Path) -> pd.DataFrame:
    """Load and validate the minimum required normalized schema."""
    df = pd.read_csv(path)

    required = {
        "timestamp", "symbol", "open", "high",
        "low", "close", "volume",
    }
    missing = required - set(df.columns)

    if missing:
        raise ValueError(
            f"Missing required columns: {', '.join(sorted(missing))}"
        )

    df["timestamp"] = pd.to_datetime(
        df["timestamp"], utc=True, errors="coerce"
    )

    if df["timestamp"].isna().any():
        raise ValueError("Input contains invalid timestamps.")

    numeric_columns = ["open", "high", "low", "close", "volume"]
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    if df[numeric_columns].isna().any().any():
        raise ValueError("Input contains invalid numeric OHLCV values.")

    if (df["volume"] < 0).any():
        raise ValueError("Volume cannot be negative.")

    if df["timestamp"].duplicated().any() and df["symbol"].nunique() == 1:
        raise ValueError("Duplicate timestamps detected in single-symbol input.")

    df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    return df


def resample_symbol(
    df: pd.DataFrame,
    timeframe: str,
    frequency: str,
    expected_count: int,
) -> pd.DataFrame:
    """Resample one symbol and retain a completeness diagnostic."""
    frames = []

    for symbol, group in df.groupby("symbol", sort=True):
        group = group.set_index("timestamp").sort_index()

        # A minute candle contributes to the UTC interval containing it.
        bars = group.resample(
            frequency,
            origin="start_day",
            label="left",
            closed="left",
        ).agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            source_rows=("close", "count"),
        )

        # Drop empty intervals; do not manufacture market candles.
        bars = bars.dropna(subset=["open", "high", "low", "close"])
        bars["symbol"] = symbol
        bars["timeframe"] = timeframe
        bars["expected_source_rows"] = expected_count
        bars["source_rows_complete"] = (
            bars["source_rows"] == expected_count
        )

        bars = bars.reset_index()
        frames.append(bars)

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)

    columns = [
        "timestamp", "symbol", "timeframe",
        "open", "high", "low", "close", "volume",
        "source_rows", "expected_source_rows",
        "source_rows_complete",
    ]
    return result[columns].sort_values(
        ["symbol", "timestamp"]
    ).reset_index(drop=True)


def resample_file(
    input_path: Path,
    timeframes: list[str],
    output_dir: Path,
) -> list[Path]:
    """Create one output CSV for each requested timeframe."""
    df = load_normalized_csv(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []

    for timeframe in timeframes:
        if timeframe not in TIMEFRAMES:
            raise ValueError(
                f"Unsupported timeframe: {timeframe}. "
                f"Choose from {', '.join(TIMEFRAMES)}"
            )

        frequency, expected_count = TIMEFRAMES[timeframe]
        result = resample_symbol(
            df, timeframe, frequency, expected_count
        )

        if result.empty:
            print(f"No output for {timeframe}: no valid candles.")
            continue

        filename = (
            f"{input_path.stem.replace('_normalized', '')}_{timeframe}.csv"
        )
        output_path = output_dir / filename

        result["timestamp"] = result["timestamp"].map(
            lambda value: value.isoformat().replace("+00:00", "Z")
        )
        result.to_csv(output_path, index=False)
        outputs.append(output_path)

        incomplete = int((~result["source_rows_complete"]).sum())
        print(
            f"{timeframe}: {len(result):,} bars | "
            f"{incomplete:,} bars with fewer than "
            f"{expected_count} source rows | {output_path.name}"
        )

    return outputs


def main():
    parser = argparse.ArgumentParser(
        description="Resample normalized XAUUSD 1-minute data."
    )
    parser.add_argument(
        "--input",
        default="data/processed/xauusd_2025_03_normalized.csv",
    )
    parser.add_argument(
        "--timeframes",
        nargs="+",
        default=["5m", "15m", "30m", "1h", "4h", "1d"],
        choices=list(TIMEFRAMES),
    )
    parser.add_argument(
        "--output-dir",
        default="data/processed/timeframes",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = ROOT / input_path

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir

    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    resample_file(input_path, args.timeframes, output_dir)


if __name__ == "__main__":
    main()