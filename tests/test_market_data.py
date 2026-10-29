
import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quantresearch.market_data import (
    generate_candles,
    save_candles,
    validate_candle,
)


class MarketDataTests(unittest.TestCase):
    def test_generates_requested_number_of_candles(self):
        candles = generate_candles("XAUUSD", count=100)
        self.assertEqual(len(candles), 100)

    def test_both_instruments_are_supported(self):
        for symbol in ("XAUUSD", "US100"):
            with self.subTest(symbol=symbol):
                self.assertEqual(
                    generate_candles(symbol, count=3)[0]["symbol"],
                    symbol,
                )

    def test_generation_is_reproducible(self):
        self.assertEqual(
            generate_candles("US100", count=10, seed=7),
            generate_candles("US100", count=10, seed=7),
        )

    def test_candles_are_valid_and_ordered(self):
        candles = generate_candles("XAUUSD", count=20)

        for candle in candles:
            validate_candle(candle)

        timestamps = [c["timestamp"] for c in candles]
        self.assertEqual(len(timestamps), len(set(timestamps)))

        for previous, current in zip(candles, candles[1:]):
            first = datetime.fromisoformat(
                previous["timestamp"].replace("Z", "+00:00")
            )
            second = datetime.fromisoformat(
                current["timestamp"].replace("Z", "+00:00")
            )
            self.assertEqual(
                (second - first).total_seconds(), 60
            )

    def test_rejects_inconsistent_ohlc(self):
        candle = generate_candles("XAUUSD", count=1)[0]
        candle["high"] = candle["low"] - 1

        with self.assertRaises(ValueError):
            validate_candle(candle)

    def test_rejects_unknown_symbol(self):
        with self.assertRaises(ValueError):
            generate_candles("UNKNOWN", count=1)

    def test_exports_csv(self):
        candles = generate_candles("US100", count=5)

        with tempfile.TemporaryDirectory() as directory:
            path = save_candles(
                candles, Path(directory) / "us100.csv"
            )
            with path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["symbol"], "US100")


if __name__ == "__main__":
    unittest.main()
