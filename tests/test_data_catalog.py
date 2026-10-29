
import json
import tempfile
import unittest
from pathlib import Path

from quantresearch.data_catalog import (
    build_inventory,
    inspect_csv,
    parse_timestamp,
)


class TestDataCatalog(unittest.TestCase):
    def test_parses_utc_z_timestamp(self):
        parsed = parse_timestamp("2025-03-02T23:00:00Z")
        self.assertIsNotNone(parsed)
        self.assertIsNotNone(parsed.tzinfo)

    def test_parses_naive_timestamp_without_assuming_timezone(self):
        parsed = parse_timestamp("2025-03-02T23:00:00")
        self.assertIsNotNone(parsed)
        self.assertIsNone(parsed.tzinfo)

    def test_invalid_timestamp_returns_none(self):
        self.assertIsNone(parse_timestamp("not-a-timestamp"))

    def test_inspects_rows_and_duplicate_timestamps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            path.write_text(
                "timestamp,close\n"
                "2025-03-02T23:00:00Z,2859.9\n"
                "2025-03-02T23:00:00Z,2859.9\n"
                "2025-03-02T23:02:00Z,2872.7\n",
                encoding="utf-8",
            )

            result = inspect_csv(path)
            self.assertEqual(result["rows"], 3)
            self.assertEqual(result["valid_timestamps"], 3)
            self.assertEqual(result["unique_timestamps"], 2)
            self.assertEqual(result["duplicate_timestamp_count"], 1)
            self.assertEqual(result["invalid_timestamps"], 0)
            self.assertTrue(result["timestamp_timezone_present"])

    def test_inventory_reads_catalog_and_scans_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog = root / "catalog.json"
            data_dir = root / "data"
            data_dir.mkdir()

            catalog.write_text(
                json.dumps({
                    "instrument": "XAUUSD",
                    "datasets": [{"id": "gold_ohlcv"}],
                }),
                encoding="utf-8",
            )
            (data_dir / "gold.csv").write_text(
                "timestamp,close\n2025-03-02T23:00:00Z,2859.9\n",
                encoding="utf-8",
            )

            result = build_inventory(catalog, [data_dir])
            self.assertEqual(result["instrument_scope"], "XAUUSD")
            self.assertEqual(len(result["local_csv_files"]), 1)
            self.assertEqual(result["local_csv_files"][0]["rows"], 1)


if __name__ == "__main__":
    unittest.main()
