
import csv
import tempfile
import unittest
from pathlib import Path

from quantresearch.ingestion import import_and_audit_csv


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.directory = Path(self.temp_dir.name)

    def write_csv(self, name, headers, rows):
        path = self.directory / name
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(headers)
            writer.writerows(rows)
        return path

    def test_maps_aliases_and_normalizes_utc(self):
        path = self.write_csv(
            "provider.csv",
            ["time", "o", "h", "l", "c", "vol"],
            [
                ["2026-01-05T11:00:00+03:00", 4000, 4002, 3998, 4001, 12],
            ],
        )

        candles, report = import_and_audit_csv(path, symbol="XAUUSD")

        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0]["timestamp"], "2026-01-05T08:00:00Z")
        self.assertEqual(candles[0]["symbol"], "XAUUSD")
        self.assertEqual(report["counts"]["rows_accepted"], 1)

    def test_naive_timestamps_use_configured_timezone(self):
        path = self.write_csv(
            "naive.csv",
            ["timestamp", "open", "high", "low", "close", "volume"],
            [["2026-01-05T11:00:00", 4000, 4002, 3998, 4001, 12]],
        )

        candles, _ = import_and_audit_csv(
            path, symbol="XAUUSD", assumed_timezone="Africa/Nairobi"
        )

        self.assertEqual(candles[0]["timestamp"], "2026-01-05T08:00:00Z")

    def test_rejects_invalid_ohlc_row(self):
        path = self.write_csv(
            "invalid.csv",
            ["timestamp", "open", "high", "low", "close", "volume"],
            [["2026-01-05T08:00:00Z", 4000, 3990, 3995, 4001, 12]],
        )

        candles, report = import_and_audit_csv(path, symbol="XAUUSD")

        self.assertEqual(candles, [])
        self.assertEqual(report["counts"]["rows_rejected"], 1)

    def test_flags_duplicate_timestamps(self):
        path = self.write_csv(
            "duplicates.csv",
            ["timestamp", "open", "high", "low", "close", "volume"],
            [
                ["2026-01-05T08:00:00Z", 4000, 4002, 3998, 4001, 12],
                ["2026-01-05T08:00:00Z", 4001, 4003, 3999, 4002, 14],
            ],
        )

        candles, report = import_and_audit_csv(path, symbol="XAUUSD")

        self.assertEqual(len(candles), 1)
        self.assertEqual(report["counts"]["duplicate_rows"], 1)

    def test_detects_gaps_without_filling_them(self):
        path = self.write_csv(
            "gaps.csv",
            ["timestamp", "open", "high", "low", "close", "volume"],
            [
                ["2026-01-05T08:00:00Z", 4000, 4002, 3998, 4001, 12],
                ["2026-01-05T08:03:00Z", 4001, 4003, 3999, 4002, 14],
            ],
        )

        candles, report = import_and_audit_csv(path, symbol="XAUUSD")

        self.assertEqual(len(candles), 2)
        self.assertEqual(report["gap_count"], 1)
        self.assertEqual(report["gaps"][0]["missing_minute_slots"], 2)

    def test_sorts_output_and_reports_out_of_order_input(self):
        path = self.write_csv(
            "unordered.csv",
            ["timestamp", "open", "high", "low", "close", "volume"],
            [
                ["2026-01-05T08:01:00Z", 4001, 4003, 3999, 4002, 14],
                ["2026-01-05T08:00:00Z", 4000, 4002, 3998, 4001, 12],
            ],
        )

        candles, report = import_and_audit_csv(path, symbol="XAUUSD")

        self.assertEqual(report["counts"]["out_of_order_rows"], 1)
        self.assertLess(candles[0]["timestamp"], candles[1]["timestamp"])

    def test_rejects_csv_without_required_columns(self):
        path = self.write_csv("wrong.csv", ["timestamp", "close"], [
            ["2026-01-05T08:00:00Z", 4001],
        ])

        with self.assertRaises(ValueError):
            import_and_audit_csv(path, symbol="XAUUSD")


if __name__ == "__main__":
    unittest.main()
