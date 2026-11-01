import csv
import tempfile
import unittest
from pathlib import Path

from quantresearch.raw_validator import (
    parse_timestamp,
    validate_file,
)


class RawValidatorTests(unittest.TestCase):

    def create_csv(self, filename, headers, rows):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)

        path = Path(directory.name) / filename

        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(headers)
            writer.writerows(rows)

        return path

    def test_valid_ohlc_candle_passes(self):
        path = self.create_csv(
            "xauusd_bid_m1_2024_01.csv",
            ["timestamp", "open", "high", "low", "close", "volume"],
            [["2024-01-02T10:00:00.000Z", "2060", "2065", "2058", "2062", "100"]],
        )

        result, anomalies = validate_file(path)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["rows"], 1)
        self.assertEqual(result["invalid_ohlc"], 0)
        self.assertEqual(anomalies, [])

    def test_close_below_low_is_exported(self):
        path = self.create_csv(
            "xauusd_bid_d1_2000_02.csv",
            ["timestamp", "open", "high", "low", "close", "volume"],
            [["2000-02-07T00:00:00.000Z", "313", "319", "304", "301", "100"]],
        )

        result, anomalies = validate_file(path)

        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["invalid_ohlc"], 1)
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(
            anomalies[0]["anomaly_type"],
            "LOW_ABOVE_OHLC_MIN",
        )
        self.assertEqual(anomalies[0]["difference"], "3")

    def test_invalid_timestamp_is_detected(self):
        path = self.create_csv(
            "xauusd_bid_m1_2024_01.csv",
            ["timestamp", "open", "high", "low", "close", "volume"],
            [["not-a-timestamp", "2060", "2065", "2058", "2062", "100"]],
        )

        result, anomalies = validate_file(path)

        self.assertEqual(result["invalid_timestamps"], 1)
        self.assertTrue(
            any(a["anomaly_type"] == "INVALID_TIMESTAMP" for a in anomalies)
        )

    def test_malformed_row_is_detected(self):
        path = self.create_csv(
            "xauusd_bid_m1_2024_01.csv",
            ["timestamp", "open", "high", "low", "close", "volume"],
            [["2024-01-02T10:00:00Z", "2060", "2065"]],
        )

        result, anomalies = validate_file(path)

        self.assertEqual(result["malformed_rows"], 1)
        self.assertTrue(
            any(a["anomaly_type"] == "MALFORMED_ROW" for a in anomalies)
        )

    def test_invalid_numeric_value_is_detected(self):
        path = self.create_csv(
            "xauusd_bid_m1_2024_01.csv",
            ["timestamp", "open", "high", "low", "close", "volume"],
            [["2024-01-02T10:00:00Z", "invalid", "2065", "2058", "2062", "100"]],
        )

        result, anomalies = validate_file(path)

        self.assertEqual(result["invalid_numeric_values"], 1)
        self.assertTrue(
            any(a["anomaly_type"] == "INVALID_NUMERIC_VALUE" for a in anomalies)
        )

    def test_negative_tick_spread_is_detected(self):
        path = self.create_csv(
            "xauusd_bid_tick_2024_01.csv",
            ["timestamp", "askPrice", "bidPrice", "askVolume", "bidVolume"],
            [["2024-01-02T10:00:00Z", "2059", "2060", "1", "1"]],
        )

        result, anomalies = validate_file(path)

        self.assertEqual(result["invalid_spreads"], 1)
        self.assertTrue(
            any(a["anomaly_type"] == "NEGATIVE_SPREAD" for a in anomalies)
        )

    def test_timestamp_is_normalized_to_utc(self):
        timestamp = parse_timestamp("2024-01-02T13:00:00+03:00")

        self.assertEqual(
            timestamp.isoformat(),
            "2024-01-02T10:00:00+00:00",
        )


if __name__ == "__main__":
    unittest.main()
