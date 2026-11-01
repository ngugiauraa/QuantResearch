"""XAUUSD trading calendar for completeness audits and session labels.

Rules come from what the archive actually shows, not an official exchange calendar:
  - The week runs from Sunday 18:00 to Friday 17:00 New York time.
  - From 2013 a daily 17:00-18:00 New York break appears. Before 2013 it does not.
  - Christmas Day, New Year's Day and Good Friday are treated as closed.
Other partial holidays are reported by the audit as "thin", not as failures, because
no verified historical holiday schedule is available.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

NEW_YORK = ZoneInfo("America/New_York")
DAILY_BREAK_FROM = date(2013, 1, 1)

# London and New York sessions are given in local time so DST moves them correctly.
SESSIONS = {
    "asia": ("Asia/Tokyo", 9 * 60, 15 * 60),
    "london": ("Europe/London", 8 * 60, 16 * 60 + 30),
    "new_york": ("America/New_York", 8 * 60 + 20, 17 * 60),
}


def easter(year: int) -> date:
    """Western Easter by the anonymous Gregorian algorithm."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def closed_holidays(year: int) -> set[date]:
    return {date(year, 1, 1), date(year, 12, 25), easter(year) - timedelta(days=2)}


def is_trading_weekday(day: date) -> bool:
    return day.weekday() < 5 and day not in closed_holidays(day.year)


def trading_weekdays(start: date, end: date) -> list[date]:
    out, day = [], start
    while day <= end:
        if is_trading_weekday(day):
            out.append(day)
        day += timedelta(days=1)
    return out


def last_trading_weekday(year: int, month: int) -> date:
    day = date(year + (month == 12), 1 if month == 12 else month + 1, 1) - timedelta(days=1)
    while not is_trading_weekday(day):
        day -= timedelta(days=1)
    return day


def market_open(ts: datetime) -> bool:
    """Regular weekly schedule, with the daily break only where the archive shows it."""
    local = ts.astimezone(NEW_YORK)
    minute = local.hour * 60 + local.minute
    if local.weekday() == 5:
        return False
    if local.weekday() == 6:
        return minute >= 18 * 60
    if local.weekday() == 4 and minute >= 17 * 60:
        return False
    if local.date() >= DAILY_BREAK_FROM and 17 * 60 <= minute < 18 * 60:
        return False
    return True


def session_flags(ts: pd.Series) -> pd.DataFrame:
    """Boolean membership of each session. Sessions overlap by design."""
    out = {}
    for name, (zone, begin, end) in SESSIONS.items():
        local = ts.dt.tz_convert(zone)
        minute = local.dt.hour * 60 + local.dt.minute
        out[name] = ((minute >= begin) & (minute < end)).to_numpy()
    return pd.DataFrame(out, index=ts.index)


def session_of(ts: pd.Series) -> pd.Series:
    """One label per bar: overlap, london, new_york, asia or off."""
    f = session_flags(ts)
    label = np.select(
        [f["london"] & f["new_york"], f["new_york"], f["london"], f["asia"]],
        ["overlap", "new_york", "london", "asia"], default="off",
    )
    return pd.Series(label, index=ts.index)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
