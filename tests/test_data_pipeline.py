import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from quantresearch.dukascopy_download import month_chunks
from quantresearch.data_manifest import inspect_csv
from quantresearch.data_sources import get_sources
from quantresearch.resample import load_normalized_csv, resample_symbol


class TestDataPipeline(unittest.TestCase):

    def test_month_chunks_cross_year_boundary(self):
        chunks = list(
            month_chunks(date(2025, 12, 15), date(2026, 2, 10))
        )

        self.assertEqual(
            chunks,
            [
                (date(2025, 12, 15), date(2025, 12, 31)),
                (date(2026, 1, 1), date(2026, 1, 31)),
                (date(2026, 2, 1), date(2026, 2, 10)),
            ],
        )

    def test_month_chunks_reject_invalid_range(self):
        with self.assertRaises(ValueError):
            list(month_chunks(date(2025, 2, 1), date(2025, 1, 1)))

    def test_registry_contains_gold_data_source(self):
        sources = get_sources("market_data")
        self.assertIn("dukascopy", sources)
        self.assertIn("XAUUSD", sources["dukascopy"]["instruments"])

    def test_manifest_inspects_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.csv"
            path.write_text(
                "timestamp,open,high,low,close,volume\n"
                "2025-01-01T00:00:00Z,1,2,0.5,1.5,10\n",
                encoding="utf-8",
            )

            result = inspect_csv(path)

            self.assertEqual(result["rows"], 1)
            self.assertEqual(result["timestamp_column"], "timestamp")
            self.assertEqual(len(result["sha256"]), 64)

    def test_resampling_aggregates_ohlcv(self):
        timestamps = pd.date_range(
            "2025-01-01T00:00:00Z",
            periods=10,
            freq="min",
        )

        df = pd.DataFrame({
            "timestamp": timestamps,
            "symbol": ["XAUUSD"] * 10,
            "open": [100 + i for i in range(10)],
            "high": [102 + i for i in range(10)],
            "low": [99 + i for i in range(10)],
            "close": [101 + i for i in range(10)],
            "volume": [1] * 10,
        })

        result = resample_symbol(df, "5m", "5min", 5)

        self.assertEqual(len(result), 2)
        self.assertEqual(result.iloc[0]["open"], 100)
        self.assertEqual(result.iloc[0]["close"], 105)
        self.assertEqual(result.iloc[0]["high"], 106)
        self.assertEqual(result.iloc[0]["low"], 99)
        self.assertEqual(result.iloc[0]["volume"], 5)
        self.assertTrue(result.iloc[0]["source_rows_complete"])

    def test_resampling_marks_partial_bar(self):
        timestamps = pd.date_range(
            "2025-01-01T00:00:00Z",
            periods=3,
            freq="min",
        )

        df = pd.DataFrame({
            "timestamp": timestamps,
            "symbol": ["XAUUSD"] * 3,
            "open": [100] * 3,
            "high": [101] * 3,
            "low": [99] * 3,
            "close": [100] * 3,
            "volume": [1] * 3,
        })

        result = resample_symbol(df, "5m", "5min", 5)

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["source_rows"], 3)
        self.assertFalse(result.iloc[0]["source_rows_complete"])


if __name__ == "__main__":
    unittest.main()

class ConsolidateGuardTests(unittest.TestCase):
    def test_refuses_the_validated_archive(self):
        from quantresearch.consolidate import consolidate
        with self.assertRaises(ValueError):
            consolidate(Path("nope"), Path("data/processed/parquet"), "m1")
