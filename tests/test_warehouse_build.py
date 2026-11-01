import lzma
import struct
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from quantresearch.core import timeframes as tfs
from quantresearch.core.config import Paths
from quantresearch.warehouse import audit, build, calendar as cal, store


def write_day(p: Paths, side: str, day: date, minutes=range(0, 3), base=2000000, spread=0):
    records = b""
    for i, m in enumerate(minutes):
        px = base + spread + i * 100
        records += struct.pack(">IIIIIf", m * 60, px, px + 50, px - 20, px + 70, 0.001)
    path = p.bi5 / "m1" / side / str(day.year) / f"{day}.bi5"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(lzma.compress(records, format=lzma.FORMAT_ALONE))


def make_month(tmp_path, days=(date(2024, 1, 30), date(2024, 1, 31))):
    p = Paths(tmp_path)
    for day in days:
        write_day(p, "bid", day)
        write_day(p, "ask", day, spread=300)
    return p


def test_build_month_merges_sides_and_keeps_the_31st(tmp_path):
    p = make_month(tmp_path)
    meta = build.build_month(2024, 1, p)
    assert meta["rows"] == 6 and meta["rejected"] == 0 and meta["input_files"] == 4
    bars = build.read_tf("m1", p)
    assert (bars["ts"].dt.day == 31).sum() == 3
    assert bars["ask_close"].iloc[0] - bars["bid_close"].iloc[0] == pytest.approx(0.3)
    assert bars["spread_mean"].iloc[0] == pytest.approx(0.3)


def test_invalid_rows_are_quarantined_not_changed(tmp_path):
    p = Paths(tmp_path)
    day = date(2024, 1, 31)
    bad = struct.pack(">IIIIIf", 0, 2000000, 1990000, 1995000, 2005000, 0.001)  # close below low
    good = struct.pack(">IIIIIf", 60, 2000000, 2001000, 1999000, 2002000, 0.001)
    path = p.bi5 / "m1" / "bid" / "2024" / f"{day}.bi5"
    path.parent.mkdir(parents=True)
    path.write_bytes(lzma.compress(bad + good, format=lzma.FORMAT_ALONE))
    meta = build.build_month(2024, 1, p)
    assert meta["rows"] == 1 and meta["rejected"] == 1
    q = pd.read_csv(p.quarantine / "2024-01.csv")
    assert "LOW_ABOVE_OHLC" in q["reason"].iloc[0]
    assert q["close"].iloc[0] == 1990.0


def test_missing_ask_stays_null_and_is_flagged(tmp_path):
    p = Paths(tmp_path)
    write_day(p, "bid", date(2024, 1, 31))
    build.build_month(2024, 1, p)
    bars = build.read_tf("m1", p)
    assert bars["ask_close"].isna().all()
    assert (bars["flags"] & build.FLAG_NO_ASK > 0).all()


def test_aggregate_ohlc_and_counts_are_exact():
    bars = store.synthetic_bars(10)
    m1 = bars[build.BAR_COLUMNS]
    h = build.aggregate(m1, "m5")
    first = m1.iloc[:5]
    assert len(h) == 2
    row = h.iloc[0]
    assert row.bid_open == first.bid_open.iloc[0] and row.bid_close == first.bid_close.iloc[-1]
    assert row.bid_high == first.bid_high.max() and row.bid_low == first.bid_low.min()
    assert row.bid_volume == first.bid_volume.sum() and row.m1_count == 5


def test_higher_timeframes_tile_m1_with_nothing_lost():
    m1 = store.synthetic_bars(3000)[build.BAR_COLUMNS]
    for tf in ("m5", "m15", "m30", "h1", "h4", "d1", "d1ny", "mn1"):
        assert build.aggregate(m1, tf)["m1_count"].sum() == 3000, tf


def test_new_york_day_rolls_at_1700_local_across_dst():
    winter = pd.Series(pd.to_datetime(["2024-01-10 21:59", "2024-01-10 22:00"], utc=True))
    assert tfs.bucket(winter, "d1ny").tolist()[0] != tfs.bucket(winter, "d1ny").tolist()[1]
    summer = pd.Series(pd.to_datetime(["2024-07-10 20:59", "2024-07-10 21:00"], utc=True))
    assert tfs.bucket(summer, "d1ny").tolist()[0] != tfs.bucket(summer, "d1ny").tolist()[1]
    assert str(tfs.bucket(summer, "d1ny").iloc[1]) == "2024-07-10 21:00:00+00:00"


def test_close_times_handle_month_length():
    ts = pd.Series(pd.to_datetime(["2024-02-01"], utc=True))
    assert str(tfs.close_times(ts, "mn1").iloc[0]) == "2024-03-01 00:00:00+00:00"
    assert str(tfs.close_times(ts, "h4").iloc[0]) == "2024-02-01 04:00:00+00:00"


def test_audit_fails_on_the_month_end_defect():
    # Same shape as the first-generation archive: every day present except month ends.
    expected = cal.trading_weekdays(date(2024, 1, 1), date(2024, 6, 30))
    month_ends = {cal.last_trading_weekday(2024, m) for m in range(1, 7)}
    damaged = [d for d in expected if d not in month_ends]
    ts = pd.Series(pd.to_datetime([f"{d} 12:00" for d in damaged], utc=True))
    report = audit.audit_days(ts, date(2024, 1, 1), date(2024, 6, 30))
    assert not report["passed"]
    assert len(report["missing_month_ends"]) == 6


def test_audit_passes_when_complete():
    expected = cal.trading_weekdays(date(2024, 1, 1), date(2024, 6, 30))
    ts = pd.Series(pd.to_datetime([f"{d} 12:00" for d in expected], utc=True))
    assert audit.audit_days(ts, date(2024, 1, 1), date(2024, 6, 30))["passed"]


def test_good_friday_and_fixed_holidays_are_closed():
    assert cal.easter(2024) == date(2024, 3, 31)
    assert cal.easter(2026) == date(2026, 4, 5)
    assert not cal.is_trading_weekday(date(2024, 3, 29))
    assert not cal.is_trading_weekday(date(2024, 12, 25))
    assert cal.last_trading_weekday(2024, 3) == date(2024, 3, 28)


def test_daily_break_only_applies_from_2013():
    assert not cal.market_open(datetime(2020, 3, 3, 22, 30, tzinfo=timezone.utc))
    assert cal.market_open(datetime(2010, 3, 3, 22, 30, tzinfo=timezone.utc))
    assert not cal.market_open(datetime(2020, 3, 7, 12, 0, tzinfo=timezone.utc))
    assert cal.market_open(datetime(2024, 3, 10, 22, 0, tzinfo=timezone.utc))


def test_session_labels_follow_dst():
    ts = pd.Series(pd.to_datetime(["2024-01-10 13:30", "2024-07-10 12:30", "2024-01-10 03:00", "2024-01-10 23:00"], utc=True))
    assert cal.session_of(ts).tolist() == ["overlap", "overlap", "asia", "off"]


def test_load_bars_as_of_hides_unclosed_bars(tmp_path):
    p = make_month(tmp_path, days=(date(2024, 1, 31),))
    build.build_month(2024, 1, p)
    build.build_higher(p, frames=("m5",))
    all_m1 = store.load_bars("m1", p=p)
    assert len(all_m1) == 3
    assert len(store.load_bars("m1", as_of="2024-01-31 00:01:59", p=p)) == 1
    # The M5 bar opened at 00:00 but only closes at 00:05.
    assert len(store.load_bars("m5", as_of="2024-01-31 00:04:59", p=p)) == 0
    assert len(store.load_bars("m5", as_of="2024-01-31 00:05:00", p=p)) == 1


def test_mid_price_falls_back_to_bid_only_when_ask_is_missing(tmp_path):
    p = Paths(tmp_path)
    write_day(p, "bid", date(2024, 1, 31))
    build.build_month(2024, 1, p)
    frame = store.load_bars("m1", p=p)
    assert (frame["close"] == frame["bid_close"]).all()
    assert frame["ask_close"].isna().all()


def test_ticks_to_bars_uses_observed_quotes():
    ticks = pd.DataFrame({
        "ts": pd.to_datetime(["2024-01-02 10:00:00.1", "2024-01-02 10:00:00.9", "2024-01-02 10:00:01.2"], utc=True),
        "ask": [2000.5, 2000.7, 2000.6], "bid": [2000.1, 2000.2, 2000.3],
        "ask_volume": [1.0, 1, 1], "bid_volume": [1.0, 1, 1],
    })
    bars = store.ticks_to_bars(ticks, "s1")
    assert len(bars) == 2
    assert bars.bid_high.iloc[0] == 2000.2 and bars.ask_close.iloc[0] == 2000.7
    assert bars.spread_mean.iloc[0] == pytest.approx(0.45)
    assert bars.m1_count.iloc[0] == 2


def test_interrupted_build_leaves_no_partial_month(tmp_path, monkeypatch):
    p = make_month(tmp_path)

    def boom(self, *a, **k):
        Path_tmp = a[0]
        open(Path_tmp, "wb").write(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", boom)
    with pytest.raises(OSError):
        build.build_month(2024, 1, p)
    assert not list((p.bars / "m1").glob("year=*/month=*/bars.parquet"))
