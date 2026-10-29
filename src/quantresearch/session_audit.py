"""Classify missing XAUUSD minute bars against Dukascopy's weekly schedule."""

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
ONE_MINUTE = timedelta(minutes=1)


def parse_timestamp(value):
    """Parse an ISO timestamp and require an explicit timezone."""
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        raise ValueError(f"Timestamp has no timezone: {value}")
    return ts.astimezone(timezone.utc)


def market_is_open(timestamp):
    """Approximate the regular XAU/USD weekly schedule in New York time."""
    local = timestamp.astimezone(NEW_YORK)
    weekday = local.weekday()
    minute_of_day = local.hour * 60 + local.minute

    # Saturday: closed all day.
    if weekday == 5:
        return False

    # Sunday: opens at 18:00 New York time.
    if weekday == 6:
        return minute_of_day >= 18 * 60

    # Friday: closes at 17:00 New York time.
    if weekday == 4:
        return minute_of_day < 17 * 60

    # Monday–Thursday: daily break from 17:00 to 18:00 New York time.
    return not (17 * 60 <= minute_of_day < 18 * 60)


def audit_sessions(input_path):
    """Classify missing minute slots between consecutive observed candles."""
    with open(input_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "timestamp" not in (reader.fieldnames or []):
            raise ValueError("CSV must contain a timestamp column.")
        timestamps = sorted(
            parse_timestamp(row["timestamp"]) for row in reader
        )

    if not timestamps:
        raise ValueError("CSV contains no data rows.")

    if len(timestamps) != len(set(timestamps)):
        raise ValueError("CSV contains duplicate timestamps.")

    gaps = []
    totals = Counter()

    for previous, current in zip(timestamps, timestamps[1:]):
        delta = current - previous

        if delta <= ONE_MINUTE:
            continue

        missing = int(delta.total_seconds() // 60) - 1
        expected_closed = 0
        expected_open = 0
        slot = previous + ONE_MINUTE

        while slot < current:
            if market_is_open(slot):
                expected_open += 1
            else:
                expected_closed += 1
            slot += ONE_MINUTE

        if expected_open == 0:
            classification = "expected_closure"
        elif expected_closed == 0:
            classification = "unexpected_trading_gap"
        else:
            classification = "mixed_gap"

        gaps.append({
            "previous_candle": previous.isoformat().replace("+00:00", "Z"),
            "next_candle": current.isoformat().replace("+00:00", "Z"),
            "missing_minute_slots": missing,
            "expected_closed_minutes": expected_closed,
            "unexpected_trading_minutes": expected_open,
            "classification": classification,
        })

        totals[classification] += 1
        totals["missing_minute_slots"] += missing
        totals["expected_closed_minutes"] += expected_closed
        totals["unexpected_trading_minutes"] += expected_open

    return {
        "source_file": Path(input_path).name,
        "timezone_for_session_rules": "America/New_York",
        "schedule": (
            "Sunday 18:00 to Friday 17:00 New York time, "
            "with a daily 17:00-18:00 break Monday-Thursday"
        ),
        "rows": len(timestamps),
        "first_timestamp": timestamps[0].isoformat().replace("+00:00", "Z"),
        "last_timestamp": timestamps[-1].isoformat().replace("+00:00", "Z"),
        "gap_counts": {
            "expected_closure": totals["expected_closure"],
            "unexpected_trading_gap": totals["unexpected_trading_gap"],
            "mixed_gap": totals["mixed_gap"],
        },
        "missing_minute_totals": {
            "all": totals["missing_minute_slots"],
            "expected_closed": totals["expected_closed_minutes"],
            "unexpected_trading": totals["unexpected_trading_minutes"],
        },
        "gaps": gaps,
        "limitations": [
            "Uses regular weekly hours, not a complete holiday calendar.",
            "Special early closes and exceptional sessions are not modelled.",
            "Classifications are diagnostic, not proof of data corruption.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", help="Normalized OHLCV CSV")
    parser.add_argument("--output", required=True, help="JSON report path")
    args = parser.parse_args()

    report = audit_sessions(args.input_csv)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "rows": report["rows"],
        "gap_counts": report["gap_counts"],
        "missing_minute_totals": report["missing_minute_totals"],
        "report": str(output),
    }, indent=2))


if __name__ == "__main__":
    main()
