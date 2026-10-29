import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quantresearch.session_audit import audit_sessions, market_is_open, parse_timestamp


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class TestMarketSession(unittest.TestCase):
    def test_daily_break_before_us_daylight_saving(self):
        # March 3: New York is UTC-5.
        self.assertTrue(market_is_open(utc("2025-03-03T21:59:00Z")))
        self.assertFalse(market_is_open(utc("2025-03-03T22:00:00Z")))
        self.assertFalse(market_is_open(utc("2025-03-03T22:59:00Z")))
        self.assertTrue(market_is_open(utc("2025-03-03T23:00:00Z")))

    def test_daily_break_after_us_daylight_saving(self):
        # March 10: New York is UTC-4.
        self.assertTrue(market_is_open(utc("2025-03-10T20:59:00Z")))
        self.assertFalse(market_is_open(utc("2025-03-10T21:00:00Z")))
        self.assertFalse(market_is_open(utc("2025-03-10T21:59:00Z")))
        self.assertTrue(market_is_open(utc("2025-03-10T22:00:00Z")))

    def test_weekend_and_sunday_open(self):
        self.assertFalse(market_is_open(utc("2025-03-08T15:00:00Z")))
        self.assertFalse(market_is_open(utc("2025-03-09T21:59:00Z")))
        self.assertTrue(market_is_open(utc("2025-03-09T22:00:00Z")))

    def test_timestamp_requires_timezone(self):
        with self.assertRaises(ValueError):
            parse_timestamp("2025-03-03T10:31:00")

    def test_timestamp_normalizes_to_utc(self):
        result = parse_timestamp("2025-03-03T05:31:00-05:00")
        self.assertEqual(result, utc("2025-03-03T10:31:00Z"))
        self.assertEqual(result.tzinfo, timezone.utc)


class TestSessionAudit(unittest.TestCase):
    def test_classifies_expected_and_unexpected_gaps(self):
        rows = [
            # Monday before daily closure.
            ("2025-03-03T21:59:00Z",),
            ("2025-03-03T23:00:00Z",),
            # An unexpected missing minute during open hours.
            ("2025-03-03T23:02:00Z",),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "candles.csv"
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp"])
                writer.writerows(rows)

            report = audit_sessions(path)

        self.assertEqual(report["rows"], 3)
        self.assertEqual(report["gap_counts"]["expected_closure"], 1)
        self.assertEqual(report["gap_counts"]["unexpected_trading_gap"], 1)
        self.assertEqual(report["missing_minute_totals"]["expected_closed"], 60)
        self.assertEqual(report["missing_minute_totals"]["unexpected_trading"], 1)

    def test_duplicate_timestamps_are_rejected(self):
        rows = [
            ("2025-03-03T10:00:00Z",),
            ("2025-03-03T10:00:00Z",),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "candles.csv"
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp"])
                writer.writerows(rows)

            with self.assertRaisesRegex(ValueError, "duplicate"):
                audit_sessions(path)


if __name__ == "__main__":
    unittest.main()
