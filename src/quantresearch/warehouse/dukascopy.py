"""Decode Dukascopy's public .bi5 files and name their URLs.

Formats, both big-endian and LZMA-alone compressed:
  candles: uint32 seconds from period start, open, close, low, high (points), float32 volume
  ticks:   uint32 ms from hour start, ask, bid (points), float32 ask volume, bid volume

XAUUSD uses 1,000 points per dollar. Volumes are multiplied by 1,000,000 to match the
"units" figures in the earlier CSV archive. They are provider activity figures, not
market-wide traded volume.
"""
from __future__ import annotations

import lzma
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

BASE = "https://datafeed.dukascopy.com/datafeed/XAUUSD"
POINT = 1000.0
VOLUME_UNITS = 1_000_000.0
CANDLE = np.dtype([("t", ">u4"), ("o", ">u4"), ("c", ">u4"), ("l", ">u4"), ("h", ">u4"), ("v", ">f4")])
TICK = np.dtype([("t", ">u4"), ("a", ">u4"), ("b", ">u4"), ("av", ">f4"), ("bv", ">f4")])


class CorruptFile(ValueError):
    """The bytes are not a valid provider file."""


def m1_url(day: date, side: str) -> str:
    # The provider's month is zero-based.
    return f"{BASE}/{day.year}/{day.month - 1:02d}/{day.day:02d}/{side.upper()}_candles_min_1.bi5"


def tick_url(day: date, hour: int) -> str:
    return f"{BASE}/{day.year}/{day.month - 1:02d}/{day.day:02d}/{hour:02d}h_ticks.bi5"


def _decode(raw: bytes, dtype: np.dtype) -> np.ndarray:
    if not raw:
        return np.empty(0, dtype=dtype)
    try:
        data = lzma.decompress(raw, format=lzma.FORMAT_ALONE)
    except lzma.LZMAError as exc:
        raise CorruptFile(f"Not LZMA data: {exc}") from exc
    if len(data) % dtype.itemsize:
        raise CorruptFile(f"Length {len(data)} is not a multiple of record size {dtype.itemsize}")
    return np.frombuffer(data, dtype=dtype)


def decode_candles(raw: bytes, day: date, keep_flat: bool = False) -> pd.DataFrame:
    """Decode one day of M1 candles.

    Zero-volume rows are minutes with no quote activity. The provider repeats the last
    price on them, so by default they are dropped rather than treated as trading.
    """
    rec = _decode(raw, CANDLE)
    if not keep_flat:
        rec = rec[rec["v"] > 0]
    start = pd.Timestamp(day, tz="UTC")
    return pd.DataFrame({
        "ts": start + pd.to_timedelta(rec["t"].astype("int64"), unit="s"),
        "open": rec["o"].astype("float64") / POINT,
        "high": rec["h"].astype("float64") / POINT,
        "low": rec["l"].astype("float64") / POINT,
        "close": rec["c"].astype("float64") / POINT,
        "volume": np.round(rec["v"].astype("float64") * VOLUME_UNITS),
    })


def decode_ticks(raw: bytes, day: date, hour: int) -> pd.DataFrame:
    rec = _decode(raw, TICK)
    start = pd.Timestamp(datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc))
    return pd.DataFrame({
        "ts": start + pd.to_timedelta(rec["t"].astype("int64"), unit="ms"),
        "ask": rec["a"].astype("float64") / POINT,
        "bid": rec["b"].astype("float64") / POINT,
        "ask_volume": np.round(rec["av"].astype("float64") * VOLUME_UNITS),
        "bid_volume": np.round(rec["bv"].astype("float64") * VOLUME_UNITS),
    })


def validate_payload(raw: bytes, kind: str) -> int:
    """Return the record count, or raise CorruptFile. Rejects HTML error bodies."""
    rec = _decode(raw, CANDLE if kind == "m1" else TICK)
    if len(rec) > 1 and not np.all(np.diff(rec["t"].astype("int64")) >= 0):
        raise CorruptFile("Record times go backwards")
    return int(len(rec))
