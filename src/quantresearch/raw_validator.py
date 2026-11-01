from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


ROOT = Path("data/raw/xauusd")
OUTPUT_DIR = Path("data/processed")

REPORT_PATH = OUTPUT_DIR / "raw_validation_report.json"
ANOMALIES_PATH = OUTPUT_DIR / "ohlc_anomalies.csv"

FILENAME_PATTERN = re.compile(
    r"^xauusd_(bid|ask)_"
    r"(tick|s1|m1|m5|m15|m30|h1|h4|d1|mn1)_"
    r"(\d{4})_(\d{2})\.csv$",
    re.IGNORECASE,
)

OHLC_COLUMNS = {
    "timestamp", "open", "high", "low", "close", "volume"
}

TICK_COLUMNS = {
    "timestamp", "askPrice", "bidPrice", "askVolume", "bidVolume"
}

VALID_TIMEFRAMES = {
    "tick", "s1", "m1", "m5", "m15",
    "m30", "h1", "h4", "d1", "mn1"
}

ANOMALY_COLUMNS = [
    "file",
    "timeframe",
    "price_type",
    "timestamp",
    "row_number",
    "anomaly_type",
    "open",
    "high",
    "low",
    "close",
    "difference",
    "details",
]


def parse_decimal(value: str | None) -> Decimal:
    if value is None or not value.strip():
        raise InvalidOperation("Missing numeric value")

    result = Decimal(value.strip())

    if not result.is_finite():
        raise InvalidOperation("Non-finite numeric value")

    return result


def parse_timestamp(value: str | None) -> datetime:
    if value is None or not value.strip():
        raise ValueError("Missing timestamp")

    text = value.strip()

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    timestamp = datetime.fromisoformat(text)

    if timestamp.tzinfo is None:
        raise ValueError("Timestamp is missing timezone information")

    return timestamp.astimezone(timezone.utc)


def relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT.parent.parent))
    except ValueError:
        return str(path)


def new_file_result(path: Path) -> dict[str, Any]:
    match = FILENAME_PATTERN.match(path.name)

    if match:
        price_type, timeframe, year, month = match.groups()
    else:
        price_type = None
        timeframe = None
        year = None
        month = None

    return {
        "file": relative_path(path),
        "price_type": price_type,
        "timeframe": timeframe,
        "year": int(year) if year else None,
        "month": int(month) if month else None,
        "status": "PASS",
        "rows": 0,
        "first_timestamp": None,
        "last_timestamp": None,
        "invalid_timestamps": 0,
        "invalid_numeric_values": 0,
        "invalid_ohlc": 0,
        "invalid_prices": 0,
        "invalid_volumes": 0,
        "invalid_spreads": 0,
        "duplicate_adjacent_timestamps": 0,
        "out_of_order_timestamps": 0,
        "timestamps_outside_filename_month": 0,
        "malformed_rows": 0,
        "missing_columns": [],
        "extra_columns": [],
        "errors": [],
    }


def add_error(result: dict[str, Any], message: str) -> None:
    if message not in result["errors"]:
        result["errors"].append(message)


def validate_file(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = new_file_result(path)
    anomalies: list[dict[str, Any]] = []

    match = FILENAME_PATTERN.match(path.name)

    if not match:
        result["status"] = "FAIL"
        add_error(result, "Filename does not match expected naming convention")
        return result, anomalies

    price_type, timeframe, year_text, month_text = match.groups()
    timeframe = timeframe.lower()
    expected_year = int(year_text)
    expected_month = int(month_text)

    if timeframe not in VALID_TIMEFRAMES:
        result["status"] = "FAIL"
        add_error(result, "Unsupported timeframe")
        return result, anomalies

    try:
        initial_stat = path.stat()
        initial_mtime = initial_stat.st_mtime_ns
        initial_size = initial_stat.st_size

        with path.open("r", newline="", encoding="utf-8-sig") as file:
            reader = csv.reader(file)

            header = next(reader, None)

            if not header:
                result["status"] = "FAIL"
                add_error(result, "Empty file or missing header")
                return result, anomalies

            if len(header) != len(set(header)):
                result["status"] = "FAIL"
                add_error(result, "Duplicate column names")

            expected_columns = (
                TICK_COLUMNS if timeframe == "tick" else OHLC_COLUMNS
            )

            missing = sorted(expected_columns - set(header))
            extra = sorted(set(header) - expected_columns)

            result["missing_columns"] = missing
            result["extra_columns"] = extra

            if missing:
                result["status"] = "FAIL"
                add_error(result, "Required columns missing")

            if extra:
                result["status"] = "FAIL"
                add_error(result, "Unexpected columns present")

            if missing:
                return result, anomalies

            column_indexes = {
                name: index for index, name in enumerate(header)
            }

            previous_timestamp = None

            for row_number, row in enumerate(reader, start=2):
                result["rows"] += 1

                if len(row) != len(header):
                    result["malformed_rows"] += 1
                    result["status"] = "FAIL"

                    add_error(result, "Malformed CSV row width")

                    anomalies.append({
                        "file": result["file"],
                        "timeframe": timeframe,
                        "price_type": price_type,
                        "timestamp": None,
                        "row_number": row_number,
                        "anomaly_type": "MALFORMED_ROW",
                        "open": None,
                        "high": None,
                        "low": None,
                        "close": None,
                        "difference": None,
                        "details": (
                            f"Expected {len(header)} fields; "
                            f"received {len(row)}"
                        ),
                    })
                    continue

                values = {
                    name: row[index].strip()
                    for name, index in column_indexes.items()
                }

                timestamp = None

                try:
                    timestamp = parse_timestamp(values.get("timestamp"))

                    timestamp_text = timestamp.isoformat()

                    if result["first_timestamp"] is None:
                        result["first_timestamp"] = timestamp_text

                    result["last_timestamp"] = timestamp_text

                    if (
                        timestamp.year != expected_year
                        or timestamp.month != expected_month
                    ):
                        result["timestamps_outside_filename_month"] += 1

                    if previous_timestamp is not None:
                        if timestamp == previous_timestamp:
                            result["duplicate_adjacent_timestamps"] += 1
                        elif timestamp < previous_timestamp:
                            result["out_of_order_timestamps"] += 1

                    previous_timestamp = timestamp

                except (ValueError, TypeError, OverflowError) as exc:
                    result["invalid_timestamps"] += 1
                    result["status"] = "FAIL"

                    add_error(result, "Invalid timestamp")

                    anomalies.append({
                        "file": result["file"],
                        "timeframe": timeframe,
                        "price_type": price_type,
                        "timestamp": values.get("timestamp"),
                        "row_number": row_number,
                        "anomaly_type": "INVALID_TIMESTAMP",
                        "open": None,
                        "high": None,
                        "low": None,
                        "close": None,
                        "difference": None,
                        "details": str(exc),
                    })

                if timeframe == "tick":
                    numeric_fields = [
                        "askPrice", "bidPrice",
                        "askVolume", "bidVolume"
                    ]
                else:
                    numeric_fields = [
                        "open", "high", "low", "close", "volume"
                    ]

                numbers = {}
                numeric_failed = False

                for field in numeric_fields:
                    try:
                        numbers[field] = parse_decimal(values.get(field))
                    except (InvalidOperation, ValueError, TypeError):
                        numeric_failed = True
                        result["invalid_numeric_values"] += 1
                        result["status"] = "FAIL"

                        anomalies.append({
                            "file": result["file"],
                            "timeframe": timeframe,
                            "price_type": price_type,
                            "timestamp": values.get("timestamp"),
                            "row_number": row_number,
                            "anomaly_type": "INVALID_NUMERIC_VALUE",
                            "open": values.get("open"),
                            "high": values.get("high"),
                            "low": values.get("low"),
                            "close": values.get("close"),
                            "difference": None,
                            "details": f"Invalid or missing value in {field}",
                        })

                if numeric_failed:
                    add_error(result, "Invalid numeric values")
                    continue

                if timeframe == "tick":
                    ask = numbers["askPrice"]
                    bid = numbers["bidPrice"]
                    ask_volume = numbers["askVolume"]
                    bid_volume = numbers["bidVolume"]

                    if ask <= 0 or bid <= 0:
                        result["invalid_prices"] += 1
                        result["status"] = "FAIL"

                        anomalies.append({
                            "file": result["file"],
                            "timeframe": timeframe,
                            "price_type": price_type,
                            "timestamp": values.get("timestamp"),
                            "row_number": row_number,
                            "anomaly_type": "NON_POSITIVE_PRICE",
                            "open": None,
                            "high": None,
                            "low": None,
                            "close": None,
                            "difference": None,
                            "details": f"Bid={bid}, Ask={ask}",
                        })

                    if ask < bid:
                        result["invalid_spreads"] += 1
                        result["status"] = "FAIL"

                        anomalies.append({
                            "file": result["file"],
                            "timeframe": timeframe,
                            "price_type": price_type,
                            "timestamp": values.get("timestamp"),
                            "row_number": row_number,
                            "anomaly_type": "NEGATIVE_SPREAD",
                            "open": None,
                            "high": None,
                            "low": None,
                            "close": None,
                            "difference": bid - ask,
                            "details": f"Ask={ask} is below Bid={bid}",
                        })

                    if ask_volume < 0 or bid_volume < 0:
                        result["invalid_volumes"] += 1
                        result["status"] = "FAIL"

                        anomalies.append({
                            "file": result["file"],
                            "timeframe": timeframe,
                            "price_type": price_type,
                            "timestamp": values.get("timestamp"),
                            "row_number": row_number,
                            "anomaly_type": "NEGATIVE_VOLUME",
                            "open": None,
                            "high": None,
                            "low": None,
                            "close": None,
                            "difference": None,
                            "details": (
                                f"Ask volume={ask_volume}, "
                                f"Bid volume={bid_volume}"
                            ),
                        })

                    continue

                o = numbers["open"]
                h = numbers["high"]
                l = numbers["low"]
                c = numbers["close"]
                volume = numbers["volume"]

                if min(o, h, l, c) <= 0:
                    result["invalid_prices"] += 1
                    result["status"] = "FAIL"

                    anomalies.append({
                        "file": result["file"],
                        "timeframe": timeframe,
                        "price_type": price_type,
                        "timestamp": values.get("timestamp"),
                        "row_number": row_number,
                        "anomaly_type": "NON_POSITIVE_PRICE",
                        "open": str(o),
                        "high": str(h),
                        "low": str(l),
                        "close": str(c),
                        "difference": None,
                        "details": "One or more OHLC prices are non-positive",
                    })

                if volume < 0:
                    result["invalid_volumes"] += 1
                    result["status"] = "FAIL"

                    anomalies.append({
                        "file": result["file"],
                        "timeframe": timeframe,
                        "price_type": price_type,
                        "timestamp": values.get("timestamp"),
                        "row_number": row_number,
                        "anomaly_type": "NEGATIVE_VOLUME",
                        "open": str(o),
                        "high": str(h),
                        "low": str(l),
                        "close": str(c),
                        "difference": None,
                        "details": f"Volume={volume}",
                    })

                # Standard OHLC invariants:
                # High must be >= open, close and low.
                # Low must be <= open, close and high.
                violations = []

                if h < max(o, c, l):
                    violations.append("HIGH_BELOW_OHLC_MAX")

                if l > min(o, c, h):
                    violations.append("LOW_ABOVE_OHLC_MIN")

                if violations:
                    result["invalid_ohlc"] += 1
                    result["status"] = "FAIL"

                    difference = None

                    if c < l:
                        difference = l - c
                    elif c > h:
                        difference = c - h

                    anomalies.append({
                        "file": result["file"],
                        "timeframe": timeframe,
                        "price_type": price_type,
                        "timestamp": values.get("timestamp"),
                        "row_number": row_number,
                        "anomaly_type": ";".join(violations),
                        "open": str(o),
                        "high": str(h),
                        "low": str(l),
                        "close": str(c),
                        "difference": (
                            str(difference)
                            if difference is not None else None
                        ),
                        "details": (
                            "OHLC invariant violated: "
                            "high must be >= open, low and close; "
                            "low must be <= open, high and close"
                        ),
                    })

            if result["timestamps_outside_filename_month"]:
                result["status"] = "FAIL"
                add_error(result, "Timestamps outside filename month")

            if result["duplicate_adjacent_timestamps"]:
                result["status"] = "FAIL"
                add_error(result, "Adjacent duplicate timestamps")

            if result["out_of_order_timestamps"]:
                result["status"] = "FAIL"
                add_error(result, "Out-of-order timestamps")

        final_stat = path.stat()

        if (
            initial_mtime != final_stat.st_mtime_ns
            or initial_size != final_stat.st_size
        ):
            result["status"] = "FAIL"
            add_error(result, "File changed during validation")

    except (OSError, csv.Error, UnicodeError, StopIteration) as exc:
        result["status"] = "FAIL"
        add_error(result, f"File processing error: {exc}")

    return result, anomalies


def write_json(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")

    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    temporary.replace(path)


def write_anomalies(path: Path, anomalies: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")

    with temporary.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=ANOMALY_COLUMNS)
        writer.writeheader()
        writer.writerows(anomalies)

    temporary.replace(path)


def build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "total_files": len(results),
        "passed_files": sum(r["status"] == "PASS" for r in results),
        "failed_files": sum(r["status"] == "FAIL" for r in results),
        "total_rows": sum(r["rows"] for r in results),
        "by_timeframe": {},
        "by_year": {},
        "validation_checks": {},
    }

    timeframe_stats = defaultdict(Counter)
    year_stats = defaultdict(Counter)
    check_totals = Counter()

    check_fields = [
        "invalid_timestamps",
        "invalid_numeric_values",
        "invalid_ohlc",
        "invalid_prices",
        "invalid_volumes",
        "invalid_spreads",
        "duplicate_adjacent_timestamps",
        "out_of_order_timestamps",
        "timestamps_outside_filename_month",
        "malformed_rows",
    ]

    for result in results:
        timeframe = result["timeframe"] or "unknown"
        year = str(result["year"]) if result["year"] else "unknown"

        for bucket in (timeframe_stats[timeframe], year_stats[year]):
            bucket["files"] += 1
            bucket["passed"] += result["status"] == "PASS"
            bucket["failed"] += result["status"] == "FAIL"
            bucket["rows"] += result["rows"]

        for field in check_fields:
            value = result[field]
            timeframe_stats[timeframe][field] += value
            year_stats[year][field] += value
            check_totals[field] += value

    summary["by_timeframe"] = {
        key: dict(value) for key, value in sorted(timeframe_stats.items())
    }

    summary["by_year"] = {
        key: dict(value) for key, value in sorted(year_stats.items())
    }

    summary["validation_checks"] = dict(check_totals)

    return summary


def main() -> None:
    if not ROOT.exists():
        raise SystemExit(f"Data directory not found: {ROOT}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    files = sorted(ROOT.rglob("*.csv"))

    print(f"Scanning {len(files):,} CSV files...\n")

    results = []
    all_anomalies = []

    for index, path in enumerate(files, start=1):
        result, anomalies = validate_file(path)

        results.append(result)
        all_anomalies.extend(anomalies)

        if result["status"] == "FAIL":
            print(
                f"[FAIL] {result['file']} | "
                f"Rows={result['rows']:,} | "
                f"OHLC={result['invalid_ohlc']} | "
                f"Other anomalies={len(anomalies) - result['invalid_ohlc']}"
            )

        if index % 100 == 0 or index == len(files):
            print(f"Progress: {index:,}/{len(files):,}")

    summary = build_summary(results)

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "data_directory": str(ROOT),
        "summary": summary,
        "files": results,
    }

    write_json(REPORT_PATH, report)
    write_anomalies(ANOMALIES_PATH, all_anomalies)

    print("\n" + "=" * 72)
    print("VALIDATION SUMMARY")
    print("=" * 72)

    print(f"Total files:  {summary['total_files']:,}")
    print(f"Passed:       {summary['passed_files']:,}")
    print(f"Failed:       {summary['failed_files']:,}")
    print(f"Total rows:   {summary['total_rows']:,}")
    print(f"Anomalies:    {len(all_anomalies):,}")

    print("\nBY TIMEFRAME")

    print(
        f"{'TIMEFRAME':<12}"
        f"{'PASS':>8}"
        f"{'FAIL':>8}"
        f"{'ROWS':>16}"
        f"{'OHLC':>10}"
    )

    for timeframe, stats in summary["by_timeframe"].items():
        print(
            f"{timeframe:<12}"
            f"{stats['passed']:>8,}"
            f"{stats['failed']:>8,}"
            f"{stats['rows']:>16,}"
            f"{stats['invalid_ohlc']:>10,}"
        )

    print("\nANOMALY COUNTS")

    for name, value in summary["validation_checks"].items():
        if value:
            print(f"{name:<40} {value:>10,}")

    print("\nOUTPUT FILES")
    print(f"JSON report:    {REPORT_PATH}")
    print(f"Anomalies CSV:  {ANOMALIES_PATH}")


if __name__ == "__main__":
    main()
