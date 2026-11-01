from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "data" / "raw" / "xauusd"
PROCESSED_ROOT = ROOT / "data" / "processed"
PARQUET_ROOT = PROCESSED_ROOT / "parquet"
QUARANTINE_ROOT = PROCESSED_ROOT / "quarantine"
REGISTRY_PATH = PROCESSED_ROOT / "quality_registry.json"

CHUNK_SIZE = 100_000

FILENAME_PATTERN = re.compile(
    r"xauusd_(?P<price>bid|ask)_"
    r"(?P<timeframe>tick|s1|m1|m5|m15|m30|h1|h4|d1|mn1)_"
    r"(?P<year>\d{4})_(?P<month>\d{2})\.csv$",
    re.IGNORECASE,
)

CANDLE_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

TICK_COLUMNS = [
    "timestamp",
    "askPrice",
    "bidPrice",
    "askVolume",
    "bidVolume",
]


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")

    with temporary.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, default=str)

    temporary.replace(path)


def _metadata(path: Path) -> dict[str, Any]:
    match = FILENAME_PATTERN.fullmatch(path.name)

    if not match:
        raise ValueError(f"Unexpected source filename: {path.name}")

    result = match.groupdict()
    result["price"] = result["price"].lower()
    result["timeframe"] = result["timeframe"].lower()
    result["year"] = int(result["year"])
    result["month"] = int(result["month"])

    if not 1 <= result["month"] <= 12:
        raise ValueError(f"Invalid month in filename: {path.name}")

    return result


def _arrow_schema(is_tick: bool) -> pa.Schema:
    """Return a stable schema independent of CSV chunk inference."""

    fields = [
        pa.field("timestamp", pa.timestamp("us", tz="UTC")),
    ]

    if is_tick:
        fields.extend([
            pa.field("askPrice", pa.float64()),
            pa.field("bidPrice", pa.float64()),
            pa.field("askVolume", pa.float64()),
            pa.field("bidVolume", pa.float64()),
        ])
    else:
        fields.extend([
            pa.field("open", pa.float64()),
            pa.field("high", pa.float64()),
            pa.field("low", pa.float64()),
            pa.field("close", pa.float64()),
            pa.field("volume", pa.float64()),
        ])

    fields.extend([
        pa.field("source_row_number", pa.int64()),
        pa.field("source_file", pa.string()),
    ])

    return pa.schema(fields)


def _to_arrow_table(
    accepted: pd.DataFrame,
    is_tick: bool,
) -> pa.Table:
    """Normalize pandas dtypes and enforce the canonical Arrow schema."""

    numeric_columns = (
        ["askPrice", "bidPrice", "askVolume", "bidVolume"]
        if is_tick
        else ["open", "high", "low", "close", "volume"]
    )

    data = accepted.copy()

    data["timestamp"] = pd.to_datetime(
        data["timestamp"],
        utc=True,
        errors="raise",
    )

    for column in numeric_columns:
        data[column] = data[column].astype("float64")

    data["source_row_number"] = data["source_row_number"].astype("int64")
    data["source_file"] = data["source_file"].astype("string")

    schema = _arrow_schema(is_tick)

    return pa.Table.from_pandas(
        data,
        schema=schema,
        preserve_index=False,
        safe=True,
    )


def _normalise_chunk(
    chunk: pd.DataFrame,
    metadata: dict[str, Any],
    source_file: str,
    row_offset: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    is_tick = metadata["timeframe"] == "tick"
    required = TICK_COLUMNS if is_tick else CANDLE_COLUMNS

    missing = [column for column in required if column not in chunk.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")

    data = chunk[required].copy()

    data["source_row_number"] = range(
        row_offset + 2,
        row_offset + len(data) + 2,
    )
    data["source_file"] = source_file

    reasons = pd.Series("", index=data.index, dtype="object")

    def reject(mask: pd.Series, reason: str) -> None:
        nonlocal reasons
        mask = mask.fillna(True)
        reasons.loc[mask] = reasons.loc[mask].map(
            lambda current: (
                f"{current};{reason}" if current else reason
            )
        )

    timestamps = pd.to_datetime(
        data["timestamp"],
        utc=True,
        errors="coerce",
    )

    reject(timestamps.isna(), "INVALID_TIMESTAMP")
    data["timestamp"] = timestamps

    numeric_columns = (
        ["askPrice", "bidPrice", "askVolume", "bidVolume"]
        if is_tick
        else ["open", "high", "low", "close", "volume"]
    )

    for column in numeric_columns:
        data[column] = pd.to_numeric(data[column], errors="coerce")
        reject(data[column].isna(), f"INVALID_{column.upper()}")

    price_columns = (
        ["askPrice", "bidPrice"]
        if is_tick
        else ["open", "high", "low", "close"]
    )

    for column in price_columns:
        reject(data[column] <= 0, f"NONPOSITIVE_{column.upper()}")

    volume_columns = (
        ["askVolume", "bidVolume"]
        if is_tick
        else ["volume"]
    )

    for column in volume_columns:
        reject(data[column] < 0, f"NEGATIVE_{column.upper()}")

    if is_tick:
        reject(data["askPrice"] < data["bidPrice"], "NEGATIVE_SPREAD")
    else:
        reject(
            data["high"] < data[["open", "close", "low"]].max(axis=1),
            "HIGH_BELOW_OHLC_MAX",
        )
        reject(
            data["low"] > data[["open", "close", "high"]].min(axis=1),
            "LOW_ABOVE_OHLC_MIN",
        )
        reject(data["high"] < data["low"], "HIGH_BELOW_LOW")

    wrong_month = (
        data["timestamp"].notna()
        & (
            (data["timestamp"].dt.year != metadata["year"])
            | (data["timestamp"].dt.month != metadata["month"])
        )
    )
    reject(wrong_month, "TIMESTAMP_FILENAME_MONTH_MISMATCH")

    invalid = reasons.ne("")

    accepted = data.loc[~invalid].copy()
    rejected = data.loc[invalid].copy()

    if not rejected.empty:
        rejected["rejection_reason"] = reasons.loc[invalid]

    return accepted, rejected, {
        "rows_read": int(len(data)),
        "rows_accepted": int(len(accepted)),
        "rows_rejected": int(len(rejected)),
    }


def process_file(
    source_path: Path,
    chunk_size: int = CHUNK_SIZE,
) -> dict[str, Any]:
    metadata = _metadata(source_path)

    partition = (
        f"instrument=xauusd/"
        f"timeframe={metadata['timeframe']}/"
        f"price_side={metadata['price']}/"
        f"year={metadata['year']}/"
        f"month={metadata['month']:02d}"
    )

    output_dir = PARQUET_ROOT / partition
    quarantine_dir = QUARANTINE_ROOT / partition

    output_dir.mkdir(parents=True, exist_ok=True)
    quarantine_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{source_path.stem}.parquet"
    quarantine_path = quarantine_dir / f"{source_path.stem}_rejected.csv"

    temporary_output = output_path.with_suffix(".parquet.tmp")
    temporary_quarantine = quarantine_path.with_suffix(".csv.tmp")

    for path in (temporary_output, temporary_quarantine):
        if path.exists():
            path.unlink()

    stats: dict[str, Any] = {
        "source_file": str(source_path.relative_to(ROOT)),
        "timeframe": metadata["timeframe"],
        "price_side": metadata["price"],
        "year": metadata["year"],
        "month": metadata["month"],
        "rows_read": 0,
        "rows_validated": 0,
        "rows_accepted": 0,
        "rows_written": 0,
        "rows_rejected": 0,
        "duplicate_timestamps": 0,
        "out_of_order_timestamps": 0,
        "first_timestamp": None,
        "last_timestamp": None,
        "status": "PASS",
        "parquet_path": None,
        "quarantine_path": None,
    }

    writer = None
    previous_timestamp = None
    quarantine_header_written = False
    row_offset = 0

    try:
        is_tick = metadata["timeframe"] == "tick"
        required = TICK_COLUMNS if is_tick else CANDLE_COLUMNS

        for chunk in pd.read_csv(
            source_path,
            dtype=str,
            chunksize=chunk_size,
            on_bad_lines="error",
        ):
            accepted, rejected, checks = _normalise_chunk(
                chunk=chunk,
                metadata=metadata,
                source_file=source_path.name,
                row_offset=row_offset,
            )

            stats["rows_read"] += checks["rows_read"]
            stats["rows_validated"] += checks["rows_accepted"]
            stats["rows_rejected"] += checks["rows_rejected"]

            row_offset += checks["rows_read"]

            if not accepted.empty:
                timestamps = accepted["timestamp"]

                if stats["first_timestamp"] is None:
                    stats["first_timestamp"] = timestamps.iloc[0].isoformat()

                stats["last_timestamp"] = timestamps.iloc[-1].isoformat()

                for timestamp in timestamps:
                    if previous_timestamp is not None:
                        if timestamp == previous_timestamp:
                            stats["duplicate_timestamps"] += 1
                        elif timestamp < previous_timestamp:
                            stats["out_of_order_timestamps"] += 1

                    previous_timestamp = timestamp

                table = _to_arrow_table(accepted, is_tick)

                if writer is None:
                    writer = pq.ParquetWriter(
                        temporary_output,
                        _arrow_schema(is_tick),
                        compression="zstd",
                    )

                writer.write_table(table)

                stats["rows_written"] += table.num_rows

            if not rejected.empty:
                rejected.to_csv(
                    temporary_quarantine,
                    mode="a",
                    index=False,
                    header=not quarantine_header_written,
                )
                quarantine_header_written = True

        if writer is not None:
            writer.close()
            writer = None

            temporary_output.replace(output_path)
            stats["parquet_path"] = str(output_path.relative_to(ROOT))

        if quarantine_header_written:
            temporary_quarantine.replace(quarantine_path)
            stats["quarantine_path"] = str(
                quarantine_path.relative_to(ROOT)
            )

        elif temporary_quarantine.exists():
            temporary_quarantine.unlink()

        stats["rows_accepted"] = stats["rows_written"]

        if stats["rows_rejected"]:
            stats["status"] = "PARTIAL"

        if (
            stats["duplicate_timestamps"]
            or stats["out_of_order_timestamps"]
        ):
            if stats["status"] == "PASS":
                stats["status"] = "WARN"

    except Exception as error:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass

        for path in (temporary_output, temporary_quarantine):
            if path.exists():
                path.unlink()

        # No rows from a failed file count as successfully persisted.
        stats["rows_accepted"] = 0
        stats["rows_written"] = 0
        stats["status"] = "ERROR"
        stats["error"] = f"{type(error).__name__}: {error}"

        # Do not report an output from a failed attempt.
        stats["parquet_path"] = None
        stats["quarantine_path"] = None

    return stats


def _recalculate_registry(
    registry: dict[str, Any],
) -> dict[str, Any]:
    results = registry.get("file_results", [])

    registry["total_files"] = len(results)
    registry["files_processed"] = len(results)

    for status in ("pass", "partial", "warn", "error"):
        registry[f"files_{status}"] = sum(
            1 for item in results
            if item.get("status", "").upper() == status.upper()
        )

    for key in (
        "rows_read",
        "rows_validated",
        "rows_accepted",
        "rows_written",
        "rows_rejected",
    ):
        registry[key] = sum(
            int(item.get(key, 0) or 0)
            for item in results
        )

    registry["generated_at"] = datetime.now(timezone.utc).isoformat()

    return registry


def process_archive(
    raw_root: Path = RAW_ROOT,
    chunk_size: int = CHUNK_SIZE,
    retry_errors: bool = False,
) -> dict[str, Any]:
    previous_registry: dict[str, Any] = {}

    if retry_errors:
        if not REGISTRY_PATH.exists():
            raise FileNotFoundError(
                f"Cannot retry errors: registry not found: {REGISTRY_PATH}"
            )

        previous_registry = json.loads(
            REGISTRY_PATH.read_text(encoding="utf-8")
        )

        failed_entries = [
            item
            for item in previous_registry.get("file_results", [])
            if item.get("status") == "ERROR"
        ]

        files = [
            ROOT / item["source_file"]
            for item in failed_entries
            if item.get("source_file")
        ]

        if not files:
            print("No ERROR files found in the existing registry.")
            return previous_registry

        print(f"Retrying {len(files)} previously failed files.")

    else:
        files = sorted(raw_root.rglob("*.csv"))

    new_results = []

    for index, source_path in enumerate(files, start=1):
        try:
            result = process_file(
                source_path,
                chunk_size=chunk_size,
            )
        except Exception as error:
            result = {
                "source_file": str(source_path),
                "status": "ERROR",
                "error": f"{type(error).__name__}: {error}",
                "rows_read": 0,
                "rows_validated": 0,
                "rows_accepted": 0,
                "rows_written": 0,
                "rows_rejected": 0,
                "parquet_path": None,
                "quarantine_path": None,
            }

        new_results.append(result)

        print(
            f"[{index}/{len(files)}] "
            f"{result.get('status')} "
            f"{source_path.name} "
            f"written={result.get('rows_written', 0):,}"
        )

    if retry_errors:
        replacements = {
            item["source_file"]: item
            for item in new_results
        }

        merged_results = []

        for old_item in previous_registry.get("file_results", []):
            source = old_item.get("source_file")

            if source in replacements:
                merged_results.append(replacements.pop(source))
            else:
                merged_results.append(old_item)

        merged_results.extend(replacements.values())

        registry = previous_registry
        registry["file_results"] = merged_results

    else:
        registry = {
            "raw_root": str(raw_root),
            "chunk_size": chunk_size,
            "file_results": new_results,
        }

    registry = _recalculate_registry(registry)
    _atomic_json(REGISTRY_PATH, registry)

    print("\nPIPELINE COMPLETE")
    print(f"Files processed: {registry['files_processed']:,}")
    print(f"PASS:            {registry['files_pass']:,}")
    print(f"PARTIAL:         {registry['files_partial']:,}")
    print(f"WARN:            {registry['files_warn']:,}")
    print(f"ERROR:           {registry['files_error']:,}")
    print(f"Rows read:       {registry['rows_read']:,}")
    print(f"Rows validated:  {registry['rows_validated']:,}")
    print(f"Rows accepted:   {registry['rows_accepted']:,}")
    print(f"Rows written:    {registry['rows_written']:,}")
    print(f"Rows rejected:   {registry['rows_rejected']:,}")
    print(f"Registry:        {REGISTRY_PATH}")

    return registry


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Chunked XAUUSD quality validation and Parquet conversion."
    )

    parser.add_argument(
        "--retry-errors",
        action="store_true",
        help="Retry only files marked ERROR in the existing registry.",
    )

    parser.add_argument(
        "--chunk-size",
        type=int,
        default=CHUNK_SIZE,
    )

    args = parser.parse_args()

    if args.chunk_size < 1:
        parser.error("--chunk-size must be positive.")

    process_archive(
        chunk_size=args.chunk_size,
        retry_errors=args.retry_errors,
    )


if __name__ == "__main__":
    main()