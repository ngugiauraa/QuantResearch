import unittest

import pandas as pd

from quantresearch.quality_pipeline import _metadata, _normalise_chunk


class QualityPipelineTests(unittest.TestCase):

    def setUp(self):
        self.metadata = {
            "price": "bid",
            "timeframe": "m1",
            "year": 2024,
            "month": 1,
        }

    def test_valid_candle_is_accepted(self):
        frame = pd.DataFrame([{
            "timestamp": "2024-01-15T10:00:00Z",
            "open": "2020",
            "high": "2025",
            "low": "2018",
            "close": "2023",
            "volume": "100",
        }])

        accepted, rejected, checks = _normalise_chunk(
            frame, self.metadata, "sample.csv", 0
        )

        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(rejected), 0)
        self.assertEqual(checks["rows_accepted"], 1)
        self.assertTrue(
            str(accepted.iloc[0]["timestamp"].tz) == "UTC"
        )

    def test_invalid_ohlc_is_quarantined(self):
        frame = pd.DataFrame([{
            "timestamp": "2024-01-15T10:00:00Z",
            "open": "313",
            "high": "319",
            "low": "304",
            "close": "301",
            "volume": "100",
        }])

        accepted, rejected, _ = _normalise_chunk(
            frame, self.metadata, "sample.csv", 0
        )

        self.assertEqual(len(accepted), 0)
        self.assertIn(
            "LOW_ABOVE_OHLC_MIN",
            rejected.iloc[0]["rejection_reason"],
        )

    def test_invalid_timestamp_is_quarantined(self):
        frame = pd.DataFrame([{
            "timestamp": "not-a-date",
            "open": "2020",
            "high": "2025",
            "low": "2018",
            "close": "2023",
            "volume": "100",
        }])

        accepted, rejected, _ = _normalise_chunk(
            frame, self.metadata, "sample.csv", 0
        )

        self.assertEqual(len(accepted), 0)
        self.assertIn(
            "INVALID_TIMESTAMP",
            rejected.iloc[0]["rejection_reason"],
        )

    def test_multiple_invalid_conditions_are_recorded(self):
        frame = pd.DataFrame([{
            "timestamp": "2024-01-15T10:00:00Z",
            "open": "-1",
            "high": "10",
            "low": "5",
            "close": "8",
            "volume": "-5",
        }])

        accepted, rejected, _ = _normalise_chunk(
            frame, self.metadata, "sample.csv", 0
        )

        reason = rejected.iloc[0]["rejection_reason"]

        self.assertEqual(len(accepted), 0)
        self.assertIn("NONPOSITIVE_OPEN", reason)
        self.assertIn("NEGATIVE_VOLUME", reason)

    def test_source_row_numbers_continue_across_chunks(self):
        frame = pd.DataFrame([{
            "timestamp": "2024-01-15T10:00:00Z",
            "open": "2020",
            "high": "2025",
            "low": "2018",
            "close": "2023",
            "volume": "100",
        }])

        accepted, _, _ = _normalise_chunk(
            frame, self.metadata, "sample.csv", 100
        )

        self.assertEqual(
            accepted.iloc[0]["source_row_number"],
            102,
        )

    def test_filename_metadata(self):
        from pathlib import Path

        result = _metadata(
            Path("xauusd_bid_m1_2024_01.csv")
        )

        self.assertEqual(result["timeframe"], "m1")
        self.assertEqual(result["price"], "bid")
        self.assertEqual(result["year"], 2024)
        self.assertEqual(result["month"], 1)


if __name__ == "__main__":
    unittest.main()