
from __future__ import annotations

import argparse
import json
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path(__file__).resolve().parents[2]
PARQUET_ROOT = ROOT / "data" / "processed" / "parquet"
FEATURE_ROOT = ROOT / "data" / "processed" / "features"

TIMEFRAMES = {
    "tick", "s1", "m1", "m5", "m15",
    "m30", "h1", "h4", "d1", "mn1",
}

SIDES = {"bid", "ask"}

CANDLE_COLUMNS = {
    "timestamp", "open", "high", "low", "close", "volume",
    "source_file", "source_row_number",
}

TICK_COLUMNS = {
    "timestamp", "bidPrice", "askPrice",
    "bidVolume", "askVolume",
    "source_file", "source_row_number",
}


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def discover_files(
    parquet_root: Path,
) -> dict[tuple[str, str], list[Path]]:
    """Discover registered-layout Parquet files by timeframe and side."""

    groups: dict[tuple[str, str], list[Path]] = defaultdict(list)

    for path in sorted(parquet_root.rglob("*.parquet")):
        parts = path.parts

        timeframe = next(
            (
                part.split("=", 1)[1]
                for part in parts
                if part.startswith("timeframe=")
            ),
            None,
        )

        side = next(
            (
                part.split("=", 1)[1]
                for part in parts
                if part.startswith("price_side=")
            ),
            None,
        )

        instrument = next(
            (
                part.split("=", 1)[1]
                for part in parts
                if part.startswith("instrument=")
            ),
            None,
        )

        if instrument != "xauusd":
            continue

        if timeframe not in TIMEFRAMES or side not in SIDES:
            continue

        groups[(timeframe, side)].append(path)

    return dict(groups)


def file_list_sql(paths: list[Path]) -> str:
    return "[" + ", ".join(
        sql_string(str(path).replace("\\", "/"))
        for path in paths
    ) + "]"


def candle_feature_sql(paths: list[Path]) -> str:
    """Build OHLC feature query with rolling windows across file boundaries."""

    source = file_list_sql(paths)

    return f"""
        WITH source AS (
            SELECT
                timestamp,
                open,
                high,
                low,
                close,
                volume,
                source_file,
                source_row_number,
                instrument,
                timeframe,
                price_side,
                CAST(year AS INTEGER) AS year,
                CAST(month AS INTEGER) AS month
            FROM read_parquet(
                {source},
                hive_partitioning = true,
                union_by_name = true
            )
        ),

        lagged AS (
            SELECT
                *,
                LAG(close, 1) OVER w AS previous_close,
                LAG(close, 5) OVER w AS close_5,
                LAG(close, 20) OVER w AS close_20
            FROM source
            WINDOW w AS (ORDER BY timestamp)
        ),

        returns AS (
            SELECT
                *,
                close / NULLIF(previous_close, 0) - 1.0
                    AS simple_return_1,

                LN(close / NULLIF(previous_close, 0))
                    AS log_return_1,

                close / NULLIF(close_5, 0) - 1.0
                    AS simple_return_5,

                LN(close / NULLIF(close_5, 0))
                    AS log_return_5,

                close / NULLIF(close_20, 0) - 1.0
                    AS momentum_20,

                CASE
                    WHEN previous_close IS NULL THEN high - low
                    ELSE GREATEST(
                        high - low,
                        ABS(high - previous_close),
                        ABS(low - previous_close)
                    )
                END AS true_range
            FROM lagged
        ),

        features AS (
            SELECT
                *,

                STDDEV_SAMP(log_return_1) OVER (
                    ORDER BY timestamp
                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                ) AS volatility_20,

                STDDEV_SAMP(log_return_1) OVER (
                    ORDER BY timestamp
                    ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                ) AS volatility_60,

                AVG(true_range) OVER (
                    ORDER BY timestamp
                    ROWS BETWEEN 13 PRECEDING AND CURRENT ROW
                ) AS atr_14,

                AVG(true_range) OVER (
                    ORDER BY timestamp
                    ROWS BETWEEN 49 PRECEDING AND CURRENT ROW
                ) AS atr_50,

                high - low AS candle_range,

                ABS(close - open) AS candle_body,

                GREATEST(
                    0.0,
                    high - GREATEST(open, close)
                ) AS upper_wick,

                GREATEST(
                    0.0,
                    LEAST(open, close) - low
                ) AS lower_wick,

                (close - open) / NULLIF(open, 0)
                    AS body_return,

                (high - low) / NULLIF(close, 0)
                    AS normalized_range,

                (high - low) / NULLIF(open, 0)
                    AS range_pct,

                (close - low) / NULLIF(high - low, 0)
                    AS close_location_value

            FROM returns
        )

        SELECT
            timestamp,
            open,
            high,
            low,
            close,
            volume,

            simple_return_1,
            log_return_1,
            simple_return_5,
            log_return_5,
            momentum_20,

            true_range,
            atr_14,
            atr_50,
            atr_14 / NULLIF(close, 0) AS normalized_atr_14,

            volatility_20,
            volatility_60,

            candle_range,
            candle_body,
            upper_wick,
            lower_wick,
            body_return,
            normalized_range,
            range_pct,
            close_location_value,

            source_file,
            source_row_number,
            instrument,
            timeframe,
            price_side,
            year,
            month

        FROM features
        ORDER BY timestamp
    """


def tick_feature_sql(paths: list[Path]) -> str:
    """Build quote and tick-return features."""

    source = file_list_sql(paths)

    return f"""
        WITH source AS (
            SELECT
                timestamp,
                bidPrice,
                askPrice,
                bidVolume,
                askVolume,
                source_file,
                source_row_number,
                instrument,
                timeframe,
                price_side,
                CAST(year AS INTEGER) AS year,
                CAST(month AS INTEGER) AS month
            FROM read_parquet(
                {source},
                hive_partitioning = true,
                union_by_name = true
            )
        ),

        prices AS (
            SELECT
                *,
                (bidPrice + askPrice) / 2.0 AS mid_price,
                askPrice - bidPrice AS spread,
                (
                    (askPrice - bidPrice)
                    / NULLIF((bidPrice + askPrice) / 2.0, 0)
                ) * 10000.0 AS spread_bps,

                LAG((bidPrice + askPrice) / 2.0, 1)
                    OVER w AS previous_mid,

                LAG((bidPrice + askPrice) / 2.0, 10)
                    OVER w AS mid_10,

                LAG((bidPrice + askPrice) / 2.0, 100)
                    OVER w AS mid_100

            FROM source
            WINDOW w AS (ORDER BY timestamp)
        ),

        returns AS (
            SELECT
                *,
                mid_price / NULLIF(previous_mid, 0) - 1.0
                    AS simple_return_1,

                LN(mid_price / NULLIF(previous_mid, 0))
                    AS log_return_1,

                mid_price / NULLIF(mid_10, 0) - 1.0
                    AS return_10,

                mid_price / NULLIF(mid_100, 0) - 1.0
                    AS return_100

            FROM prices
        ),

        features AS (
            SELECT
                *,

                STDDEV_SAMP(log_return_1) OVER (
                    ORDER BY timestamp
                    ROWS BETWEEN 99 PRECEDING AND CURRENT ROW
                ) AS volatility_100,

                STDDEV_SAMP(log_return_1) OVER (
                    ORDER BY timestamp
                    ROWS BETWEEN 999 PRECEDING AND CURRENT ROW
                ) AS volatility_1000,

                AVG(spread) OVER (
                    ORDER BY timestamp
                    ROWS BETWEEN 99 PRECEDING AND CURRENT ROW
                ) AS average_spread_100,

                AVG(spread_bps) OVER (
                    ORDER BY timestamp
                    ROWS BETWEEN 99 PRECEDING AND CURRENT ROW
                ) AS average_spread_bps_100,

                bidVolume + askVolume AS quoted_volume,

                askVolume - bidVolume AS volume_imbalance,

                (askVolume - bidVolume)
                    / NULLIF(askVolume + bidVolume, 0)
                    AS normalized_volume_imbalance

            FROM returns
        )

        SELECT
            timestamp,
            bidPrice,
            askPrice,
            bidVolume,
            askVolume,

            mid_price,
            spread,
            spread_bps,

            simple_return_1,
            log_return_1,
            return_10,
            return_100,

            volatility_100,
            volatility_1000,

            average_spread_100,
            average_spread_bps_100,

            quoted_volume,
            volume_imbalance,
            normalized_volume_imbalance,

            source_file,
            source_row_number,
            instrument,
            timeframe,
            price_side,
            year,
            month

        FROM features
        ORDER BY timestamp
    """


def build_features(
    parquet_root: Path = PARQUET_ROOT,
    feature_root: Path = FEATURE_ROOT,
    timeframes: list[str] | None = None,
    sides: list[str] | None = None,
    memory_limit: str = "4GB",
    threads: int = 4,
) -> dict[str, Any]:

    parquet_root = parquet_root.resolve()
    feature_root = feature_root.resolve()

    if not parquet_root.exists():
        raise FileNotFoundError(
            f"Input Parquet dataset not found: {parquet_root}"
        )

    selected_timeframes = set(timeframes or TIMEFRAMES)
    selected_sides = set(sides or SIDES)

    invalid_timeframes = selected_timeframes - TIMEFRAMES
    invalid_sides = selected_sides - SIDES

    if invalid_timeframes:
        raise ValueError(
            f"Unsupported timeframes: {sorted(invalid_timeframes)}"
        )

    if invalid_sides:
        raise ValueError(
            f"Unsupported sides: {sorted(invalid_sides)}"
        )

    groups = discover_files(parquet_root)

    groups = {
        key: paths
        for key, paths in groups.items()
        if key[0] in selected_timeframes
        and key[1] in selected_sides
    }

    if not groups:
        raise RuntimeError("No matching Parquet inputs found.")

    feature_root.mkdir(parents=True, exist_ok=True)

    final_output = feature_root / "v1"
    staging_output = feature_root / "v1_building"

    if final_output.exists():
        raise FileExistsError(
            f"Feature output already exists: {final_output}. "
            "Do not overwrite a completed feature version."
        )

    if staging_output.exists():
        raise FileExistsError(
            f"Previous staging directory exists: {staging_output}. "
            "Inspect it before removing or resuming."
        )

    staging_output.mkdir(parents=True)

    results = []
    total_groups = len(groups)

    with tempfile.TemporaryDirectory(
        prefix="quantresearch_duckdb_"
    ) as temporary_directory:

        connection = duckdb.connect(database=":memory:")

        try:
            connection.execute(
                f"SET memory_limit = '{memory_limit}'"
            )
            connection.execute(f"SET threads = {int(threads)}")
            connection.execute("SET TimeZone = 'UTC'")
            connection.execute(
                "SET preserve_insertion_order = false"
            )
            connection.execute(
                f"SET temp_directory = "
                f"{sql_string(temporary_directory)}"
            )

            for index, ((timeframe, side), paths) in enumerate(
                sorted(groups.items()), start=1
            ):
                print(
                    f"[{index}/{total_groups}] "
                    f"{timeframe.upper()} {side.upper()} "
                    f"({len(paths)} input files)",
                    flush=True,
                )

                if timeframe == "tick":
                    query = tick_feature_sql(paths)
                else:
                    query = candle_feature_sql(paths)

                output_directory = (
                    staging_output
                    / "instrument=xauusd"
                    / f"timeframe={timeframe}"
                    / f"price_side={side}"
                )

                output_directory.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                output_sql = sql_string(
                    str(output_directory).replace("\\", "/")
                )

                connection.execute(
                    f"""
                    COPY ({query})
                    TO {output_sql}
                    (
                        FORMAT PARQUET,
                        COMPRESSION ZSTD,
                        PARTITION_BY (year, month)
                    )
                    """
                )

                output_files = list(
                    output_directory.rglob("*.parquet")
                )

                row_count = connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM read_parquet(
                        {file_list_sql(output_files)},
                        hive_partitioning = true
                    )
                    """
                ).fetchone()[0]

                result = {
                    "timeframe": timeframe,
                    "price_side": side,
                    "input_files": len(paths),
                    "output_files": len(output_files),
                    "rows_written": row_count,
                }

                results.append(result)

                print(
                    f"    Output files: {len(output_files):,} | "
                    f"Rows: {row_count:,}",
                    flush=True,
                )

        finally:
            connection.close()

    total_rows = sum(
        result["rows_written"] for result in results
    )

    manifest = {
        "dataset": "xauusd",
        "feature_version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_parquet_root": str(parquet_root),
        "feature_definitions": {
            "simple_return_1": "close / previous_close - 1",
            "log_return_1": "ln(close / previous_close)",
            "simple_return_5": "close / close_5 - 1",
            "log_return_5": "ln(close / close_5)",
            "momentum_20": "close / close_20 - 1",
            "volatility_20": "rolling sample standard deviation of log_return_1",
            "volatility_60": "rolling sample standard deviation of log_return_1",
            "true_range": "max(high-low, abs(high-previous_close), abs(low-previous_close))",
            "atr_14": "14-row rolling mean of true_range",
            "atr_50": "50-row rolling mean of true_range",
            "normalized_atr_14": "atr_14 / close",
            "close_location_value": "(close-low) / (high-low)",
            "mid_price": "(bidPrice+askPrice) / 2",
            "spread": "askPrice-bidPrice",
            "spread_bps": "spread / mid_price * 10000",
            "tick_volatility_100": "100-row rolling sample standard deviation of log returns",
            "tick_volatility_1000": "1000-row rolling sample standard deviation of log returns",
            "normalized_volume_imbalance": "(askVolume-bidVolume) / (askVolume+bidVolume)",
        },
        "groups": results,
        "total_rows": total_rows,
        "total_output_files": sum(
            result["output_files"] for result in results
        ),
    }

    manifest_path = staging_output / "feature_manifest.json"

    manifest_path.write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    staging_output.rename(final_output)

    print("\nFEATURE ENGINEERING COMPLETE")
    print(f"Groups processed: {len(results):,}")
    print(f"Rows written: {total_rows:,}")
    print(f"Output files: {manifest['total_output_files']:,}")
    print(f"Dataset: {final_output}")

    return manifest


def main():
    parser = argparse.ArgumentParser(
        description="XAUUSD feature engineering pipeline"
    )

    parser.add_argument(
        "--timeframes",
        help="Comma-separated timeframes. Default: all available.",
    )

    parser.add_argument(
        "--sides",
        help="Comma-separated price sides. Default: bid and ask.",
    )

    parser.add_argument(
        "--memory-limit",
        default="4GB",
    )

    parser.add_argument(
        "--threads",
        type=int,
        default=4,
    )

    args = parser.parse_args()

    timeframes = (
        [item.strip() for item in args.timeframes.split(",")]
        if args.timeframes else None
    )

    sides = (
        [item.strip() for item in args.sides.split(",")]
        if args.sides else None
    )

    build_features(
        timeframes=timeframes,
        sides=sides,
        memory_limit=args.memory_limit,
        threads=args.threads,
    )


if __name__ == "__main__":
    main()