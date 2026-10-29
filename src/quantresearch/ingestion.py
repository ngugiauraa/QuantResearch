
"""Import, normalize, and audit historical OHLCV CSV data.

The source file is never modified. Accepted rows are normalized into the
project's canonical candle schema; data-quality findings are written to a
separate audit report.
"""

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from quantresearch.market_data import FIELDS, validate_candle


# Maps common provider column names to the project's standard schema.
ALIASES = {
    "timestamp": {"timestamp", "time", "date", "datetime", "gmt time"},
    "open": {"open", "o"},
    "high": {"high", "h"},
    "low": {"low", "l"},
    "close": {"close", "c", "last"},
    "volume": {"volume", "vol", "tick_volume", "tickvol"},
    "symbol": {"symbol", "instrument", "ticker", "asset"},
    "timeframe": {"timeframe", "interval", "period"},
}


def normalize_timestamp(value: str, assumed_timezone: str = "UTC") -> str:
    """Convert a timestamp to ISO 8601 UTC format ending in Z."""
    value = value.strip()
    if not value:
        raise ValueError("Timestamp is empty")

    # Handle the common UTC suffix and ISO 8601 timestamps.
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(assumed_timezone))
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"Unknown assumed timezone: {assumed_timezone}"
            ) from exc

    return (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _column_map(headers: list[str]) -> dict[str, str]:
    """Map canonical field names to matching source CSV headers."""
    normalized_headers = {
        header.strip().lower(): header for header in headers
    }
    mapping = {}

    for canonical, aliases in ALIASES.items():
        for alias in aliases:
            if alias in normalized_headers:
                mapping[canonical] = normalized_headers[alias]
                break

    missing = {"timestamp", "open", "high", "low", "close", "volume"} - (
        mapping.keys()
    )
    if missing:
        raise ValueError(
            f"CSV is missing required columns: {sorted(missing)}. "
            f"Found: {headers}"
        )

    return mapping


def import_and_audit_csv(
    input_path: str | Path,
    symbol: str | None = None,
    timeframe: str = "1m",
    assumed_timezone: str = "UTC",
) -> tuple[list[dict], dict]:
    """Return accepted normalized candles and a detailed quality report."""
    source = Path(input_path)
    accepted = []
    row_errors = []
    counts = Counter()
    seen = set()
    previous_input_time = {}
    out_of_order_rows = []

    with source.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")

        mapping = _column_map(reader.fieldnames)

        for row_number, row in enumerate(reader, start=2):
            counts["rows_read"] += 1
            try:
                candle_symbol = (
                    (row.get(mapping.get("symbol", ""), "") or "").strip()
                    or symbol
                )
                candle_timeframe = (
                    (
                        row.get(mapping.get("timeframe", ""), "") or ""
                    ).strip()
                    or timeframe
                )

                if not candle_symbol:
                    raise ValueError(
                        "No symbol column/value found; provide --symbol"
                    )

                candle = {
                    "timestamp": normalize_timestamp(
                        row.get(mapping["timestamp"], ""),
                        assumed_timezone,
                    ),
                    "symbol": candle_symbol.upper(),
                    "timeframe": candle_timeframe,
                }

                for field in ("open", "high", "low", "close", "volume"):
                    candle[field] = float(row[mapping[field]])

                # Validate the candle using the existing project contract.
                validate_candle(candle)

                key = (candle["symbol"], candle["timeframe"])
                previous = previous_input_time.get(key)
                current_dt = datetime.fromisoformat(
                    candle["timestamp"].replace("Z", "+00:00")
                )
                if previous is not None and current_dt < previous:
                    counts["out_of_order_rows"] += 1
                    out_of_order_rows.append(row_number)
                previous_input_time[key] = current_dt

                duplicate_key = (
                    candle["symbol"],
                    candle["timeframe"],
                    candle["timestamp"],
                )
                if duplicate_key in seen:
                    counts["duplicate_rows"] += 1
                    row_errors.append({
                        "row": row_number,
                        "issue": "Duplicate timestamp for symbol/timeframe",
                        "timestamp": candle["timestamp"],
                    })
                    continue

                seen.add(duplicate_key)
                accepted.append(candle)
                counts["rows_accepted"] += 1

            except (ValueError, TypeError, KeyError) as exc:
                counts["rows_rejected"] += 1
                row_errors.append({
                    "row": row_number,
                    "issue": str(exc),
                })

    # Sort accepted candles into chronological order for downstream research.
    accepted.sort(
        key=lambda candle: (
            candle["symbol"],
            candle["timeframe"],
            candle["timestamp"],
        )
    )

    # A gap is a separation longer than the expected one-minute interval.
    # The count represents absent minute slots, not proof of bad data.
    gaps = []
    grouped = {}
    for candle in accepted:
        key = (candle["symbol"], candle["timeframe"])
        grouped.setdefault(key, []).append(candle)

    for (candle_symbol, candle_timeframe), candles in grouped.items():
        if candle_timeframe != "1m":
            continue

        for previous, current in zip(candles, candles[1:]):
            first = datetime.fromisoformat(
                previous["timestamp"].replace("Z", "+00:00")
            )
            second = datetime.fromisoformat(
                current["timestamp"].replace("Z", "+00:00")
            )
            minutes = int((second - first).total_seconds() // 60)

            if minutes > 1:
                gaps.append({
                    "symbol": candle_symbol,
                    "timeframe": candle_timeframe,
                    "from": previous["timestamp"],
                    "to": current["timestamp"],
                    "missing_minute_slots": minutes - 1,
                })

    report = {
        "source_file": source.name,
        "assumed_timezone_for_naive_timestamps": assumed_timezone,
        "counts": {
            "rows_read": counts["rows_read"],
            "rows_accepted": counts["rows_accepted"],
            "rows_rejected": counts["rows_rejected"],
            "duplicate_rows": counts["duplicate_rows"],
            "out_of_order_rows": counts["out_of_order_rows"],
        },
        "out_of_order_source_row_numbers": out_of_order_rows,
        "gap_count": len(gaps),
        "gaps": gaps,
        "row_errors": row_errors,
        "normalized_rows_are_sorted": True,
        "notes": [
            "The source CSV was read only and was not modified.",
            "Duplicate rows after the first occurrence were rejected.",
            "Gaps are observations, not automatic proof of missing data.",
            "Naive timestamps were interpreted using the configured timezone.",
        ],
    }

    return accepted, report


def save_normalized_csv(
    candles: list[dict], output_path: str | Path
) -> Path:
    """Write normalized candles using the canonical project schema."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(candles)

    return path


def save_audit_report(report: dict, output_path: str | Path) -> Path:
    """Write the audit report as readable JSON."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize and audit a historical OHLCV CSV."
    )
    parser.add_argument("input", help="Path to the provider's original CSV")
    parser.add_argument("--symbol", help="Symbol if the CSV has no symbol column")
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument(
        "--assumed-timezone",
        default="UTC",
        help="Timezone for timestamps without an offset (default: UTC)",
    )
    parser.add_argument(
        "--output",
        default="data/processed/normalized.csv",
        help="Destination for normalized candles",
    )
    parser.add_argument(
        "--report",
        default="data/processed/audit_report.json",
        help="Destination for the audit report",
    )
    args = parser.parse_args()

    candles, report = import_and_audit_csv(
        args.input,
        symbol=args.symbol,
        timeframe=args.timeframe,
        assumed_timezone=args.assumed_timezone,
    )
    save_normalized_csv(candles, args.output)
    save_audit_report(report, args.report)

    print("Import complete.")
    print(json.dumps(report["counts"], indent=2))
    print(f"Detected gaps: {report['gap_count']}")
    print(f"Normalized data: {args.output}")
    print(f"Audit report: {args.report}")


if __name__ == "__main__":
    main()
