"""Timeframe definitions. Bar timestamps are the bar's OPEN time in UTC.

A bar's values are not knowable until it closes, so `close_times` gives the only
legal availability time for its high, low, close and volume.
"""
from __future__ import annotations

import pandas as pd

FIXED_SECONDS = {
    "s1": 1, "m1": 60, "m5": 300, "m15": 900, "m30": 1800,
    "h1": 3600, "h4": 14400, "d1": 86400, "d1ny": 86400,
}
CALENDAR = {"mn1", "w1"}
ALL = tuple(FIXED_SECONDS) + ("w1", "mn1")
# Weekly bars run Saturday 00:00 to Saturday 00:00 UTC, so each holds one Sunday-evening-to-Friday trading week and
# closes about two hours after Friday's close. They are built from D1 bars on read (warehouse.store.load_bars).
BUILT_FROM_M1 = ("m5", "m15", "m30", "h1", "h4", "d1", "d1ny", "mn1")


def validate(tf: str) -> str:
    if tf not in FIXED_SECONDS and tf not in CALENDAR:
        raise ValueError(f"Unsupported timeframe: {tf}")
    return tf


def close_times(ts: pd.Series, tf: str) -> pd.Series:
    """Exclusive end of each bar, which is when its values become available."""
    validate(tf)
    if tf == "mn1":
        naive = ts.dt.tz_convert("UTC").dt.tz_localize(None)
        return (naive + pd.offsets.MonthBegin(1)).dt.tz_localize("UTC")
    if tf == "w1":
        return ts + pd.Timedelta(days=7)
    return ts + pd.Timedelta(seconds=FIXED_SECONDS[tf])


def bucket(ts: pd.Series, tf: str) -> pd.Series:
    """Open time of the bar containing each timestamp."""
    validate(tf)
    if tf == "mn1":
        naive = ts.dt.tz_convert("UTC").dt.tz_localize(None)
        return naive.dt.to_period("M").dt.to_timestamp().dt.tz_localize("UTC")
    if tf == "w1":
        day = ts.dt.tz_convert("UTC").dt.floor("D")
        return day - pd.to_timedelta((day.dt.day_of_week - 5) % 7, unit="D")  # Back to the Saturday on or before.
    if tf == "d1ny":
        # The trading day rolls at 17:00 New York. Each bar is labelled with the
        # UTC instant of that roll, so the timezone database handles DST.
        ny = ts.dt.tz_convert("America/New_York").dt.tz_localize(None)
        day = (ny + pd.Timedelta(hours=7)).dt.floor("D") - pd.Timedelta(hours=7)
        roll = day.dt.tz_localize("America/New_York", ambiguous=True, nonexistent="shift_forward")
        return roll.dt.tz_convert("UTC")
    return ts.dt.floor(f"{FIXED_SECONDS[tf]}s")
