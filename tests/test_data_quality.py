import unittest
import pandas as pd

from quantresearch.data_quality import canonicalize

class DataQualityTests(unittest.TestCase):
    def test_canonicalizes_common_ohlc_headers(self):
        frame = pd.DataFrame({"Date": ["2025-01-01"], "Open": [1], "High": [2], "Low": [0.5], "Close": [1.5]})
        result = canonicalize(frame)
        self.assertTrue({"timestamp", "open", "high", "low", "close"}.issubset(result.columns))

if __name__ == "__main__":
    unittest.main()
