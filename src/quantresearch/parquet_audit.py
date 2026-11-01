from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]
PROCESSED_ROOT = ROOT / "data" / "processed"
PARQUET_ROOT = PROCESSED_ROOT / "parquet"
REGISTRY_PATH = PROCESSED_ROOT / "quality_registry.json"
REPORT_PATH = PROCESSED_ROOT / "parquet_audit_report.json"

BATCH_SIZE = 250_000

CANDLE_COLUMNS = {
    "timestamp", "open", "high", "low", "close", "volume",
    "source_row_number", "source_file",
}

TICK_COLUMNS = {
    "timestamp", "askPrice", "bidPrice", "askVolume", "bidVolume",
    "source_row_number", "source_file",
}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")

    with temporary.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, default=str)

    temporary.replace(path)


def inspect_parquet(
    path: Path,
    timeframe: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path.relative_to(ROOT)),
        "status": "PASS",
        "rows": 0,
        "first_timestamp": None,
        "last_timestamp": None,
        "duplicate_timestamps": 0,
        "out_of_order_timestamps": 0,
        "null_timestamps": 0,
        "null_source_rows": 0,
        "schema_errors": [],
        "data_errors": [],
    }

    try:
        parquet = pq.ParquetFile(path)
        schema = parquet.schema_arrow
        columns = set(schema.names)

        required = TICK_COLUMNS if timeframe == "tick" else CANDLE_COLUMNS
        missing = sorted(required - columns)

        if missing:
            result["schema_errors"].append(
                f"Missing required columns: {missing}"
            )

        if len(schema.names) != len(columns):
            result["schema_errors"].append("Duplicate column names")

        if result["schema_errors"]:
            result["status"] = "FAIL"
            return result

        previous_timestamp = None

        for batch in parquet.iter_batches(batch_size=BATCH_SIZE):
            frame = batch.to_pandas()
            result["rows"] += len(frame)

            timestamps = frame["timestamp"]

            null_timestamps = int(timestamps.isna().sum())
            result["null_timestamps"] += null_timestamps

            if "source_row_number" in frame:
                result["null_source_rows"] += int(
                    frame["source_row_number"].isna().sum()
                )

            valid_timestamps = timestamps.dropna()

            if not valid_timestamps.empty:
                if result["first_timestamp"] is None:
                    result["first_timestamp"] = (
                        valid_timestamps.iloc[0].isoformat()
                    )

                result["last_timestamp"] = (
                    valid_timestamps.iloc[-1].isoformat()
                )

            for timestamp in valid_timestamps:
                if previous_timestamp is not None:
                    if timestamp == previous_timestamp:
                        result["duplicate_timestamps"] += 1
                    elif timestamp < previous_timestamp:
                        result["out_of_order_timestamps"] += 1

                previous_timestamp = timestamp

            if timeframe == "tick":
                ask = frame["askPrice"]
                bid = frame["bidPrice"]

                invalid = (
                    ask.isna()
                    | bid.isna()
                    | (ask <= 0)
                    | (bid <= 0)
                    | (ask < bid)
                    | (frame["askVolume"] < 0)
                    | (frame["bidVolume"] < 0)
                )

            else:
                open_price = frame["open"]
                high = frame["high"]
                low = frame["low"]
                close = frame["close"]
                volume = frame["volume"]

                invalid = (
                    open_price.isna()
                    | high.isna()
                    | low.isna()
                    | close.isna()
                    | volume.isna()
                    | (open_price <= 0)
                    | (high <= 0)
                    | (low <= 0)
                    | (close <= 0)
                    | (volume < 0)
                    | (high < frame[["open", "close", "low"]].max(axis=1))
                    | (low > frame[["open", "close", "high"]].min(axis=1))
                    | (high < low)
                )

            result["data_errors"].append(
                int(invalid.fillna(True).sum())
            )

        result["invalid_data_rows"] = sum(result["data_errors"])
        result.pop("data_errors")

        if (
            result["null_timestamps"]
            or result["null_source_rows"]
            or result["invalid_data_rows"]
            or result["out_of_order_timestamps"]
        ):
            result["status"] = "FAIL"

        elif result["duplicate_timestamps"]:
            result["status"] = "WARN"

    except Exception as error:
        result["status"] = "ERROR"
        result["error"] = f"{type(error).__name__}: {error}"

    return result


def run_audit() -> dict[str, Any]:
    if not REGISTRY_PATH.exists():
        raise FileNotFoundError(
            f"Quality registry not found: {REGISTRY_PATH}"
        )

    with REGISTRY_PATH.open("r", encoding="utf-8") as file:
        registry = json.load(file)

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "registry_path": str(REGISTRY_PATH.relative_to(ROOT)),
        "registered_files": 0,
        "parquet_files_found": 0,
        "parquet_files_missing": 0,
        "orphan_parquet_files": 0,
        "rows_expected": 0,
        "rows_found": 0,
        "row_count_mismatches": 0,
        "files_passed": 0,
        "files_warned": 0,
        "files_failed": 0,
        "files_errored": 0,
        "results": [],
        "missing_files": [],
        "orphan_files": [],
    }

    registered_paths: set[Path] = set()

    for entry in registry.get("file_results", []):
        if entry.get("status") == "ERROR":
            continue

        relative_path = entry.get("parquet_path")
        if not relative_path:
            continue

        path = ROOT / relative_path
        registered_paths.add(path.resolve())

        report["registered_files"] += 1
        expected_rows = int(entry.get("rows_accepted", 0))
        report["rows_expected"] += expected_rows

        if not path.exists():
            report["parquet_files_missing"] += 1
            report["missing_files"].append(relative_path)
            report["files_failed"] += 1
            continue

        report["parquet_files_found"] += 1

        result = inspect_parquet(
            path,
            entry.get("timeframe", ""),
        )

        result["expected_rows"] = expected_rows
        result["row_count_matches"] = result["rows"] == expected_rows

        report["rows_found"] += result["rows"]

        if not result["row_count_matches"]:
            report["row_count_mismatches"] += 1
            result["status"] = "FAIL"

        status = result["status"]

        if status == "PASS":
            report["files_passed"] += 1
        elif status == "WARN":
            report["files_warned"] += 1
        elif status == "ERROR":
            report["files_errored"] += 1
        else:
            report["files_failed"] += 1

        report["results"].append(result)

    actual_paths = {
        path.resolve()
        for path in PARQUET_ROOT.rglob("*.parquet")
    } if PARQUET_ROOT.exists() else set()

    orphan_paths = actual_paths - registered_paths

    report["orphan_parquet_files"] = len(orphan_paths)
    report["orphan_files"] = [
        str(path.relative_to(ROOT))
        for path in sorted(orphan_paths)
    ]

    report["all_checks_passed"] = (
        report["parquet_files_missing"] == 0
        and report["row_count_mismatches"] == 0
        and report["files_failed"] == 0
        and report["files_errored"] == 0
        and report["orphan_parquet_files"] == 0
    )

    write_json(REPORT_PATH, report)

    print("\nPARQUET AUDIT COMPLETE")
    print(f"Registered outputs: {report['registered_files']:,}")
    print(f"Parquet files found: {report['parquet_files_found']:,}")
    print(f"Missing files:       {report['parquet_files_missing']:,}")
    print(f"Orphan files:        {report['orphan_parquet_files']:,}")
    print(f"Expected rows:       {report['rows_expected']:,}")
    print(f"Actual rows:         {report['rows_found']:,}")
    print(f"Row count mismatch:  {report['row_count_mismatches']:,}")
    print(f"Passed:              {report['files_passed']:,}")
    print(f"Warnings:            {report['files_warned']:,}")
    print(f"Failed:              {report['files_failed']:,}")
    print(f"Errors:              {report['files_errored']:,}")
    print(f"All checks passed:   {report['all_checks_passed']}")
    print(f"Report:              {REPORT_PATH}")

    return report


if __name__ == "__main__":
    run_audit()