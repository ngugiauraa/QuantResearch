"""The live bar layer: warehouse history followed by the broker's M1 bars, with timeframes rebuilt across the seam."""
from datetime import datetime

import pandas as pd
import pytest

from quantresearch.core.config import Paths
from quantresearch.warehouse import build, store
from quantresearch.warehouse.live import LiveBars, broker_rows_to_bars

START = pd.Timestamp("2024-01-02 00:00", tz="UTC")


def make_warehouse(tmp_path, minutes: int = 270) -> Paths:
    """Synthetic M1 from 00:00 for `minutes` minutes (ends 04:29 by default, inside the 04:00 H1 bar)."""
    p = Paths(tmp_path)
    m1 = store.synthetic_bars(minutes, "m1", start=str(START.tz_localize(None)))[build.BAR_COLUMNS]
    build._write_tf(m1, "m1", 2024, 1, p)
    build.build_higher(p, frames=("h1",))
    return p


class FakeBroker:
    """Stands in for Mt5Broker.bars_m1: 60 broker minutes right after the warehouse, spread 0.2."""

    def __init__(self, first=START + pd.Timedelta(minutes=270), n=60, fail=False):
        self.rows = [{"ts": (first + pd.Timedelta(minutes=i)).to_pydatetime(), "open": 3000.0 + i, "high": 3001.0 + i, "low": 2999.0 + i,
                      "close": 3000.5 + i, "tick_volume": 10.0, "spread": 0.2} for i in range(n)]
        self.fail = fail
        self.calls = 0

    def bars_m1(self, start: datetime, end: datetime) -> list[dict]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("terminal gone")
        return [r for r in self.rows if start <= r["ts"] < end]


def test_broker_rows_get_the_warehouse_layout_and_the_broker_flag():
    frame = broker_rows_to_bars(FakeBroker().rows[:2])
    assert list(frame.columns) == build.BAR_COLUMNS
    assert (frame["flags"] == build.FLAG_BROKER).all()
    assert frame["ask_close"].iloc[0] == pytest.approx(frame["bid_close"].iloc[0] + 0.2)
    assert frame["bid_volume"].iloc[0] + frame["ask_volume"].iloc[0] == 10.0


def test_m1_is_extended_after_the_warehouse_end(tmp_path):
    p = make_warehouse(tmp_path)
    live = LiveBars(FakeBroker(), p)
    frame = live.load("m1")
    assert len(frame) == 270 + 60
    assert frame["ts"].is_monotonic_increasing and not frame["ts"].duplicated().any()
    assert (frame["flags"].iloc[270:] == build.FLAG_BROKER).all() and (frame["flags"].iloc[:270] & build.FLAG_BROKER == 0).all()
    assert "close_ts" in frame and frame["close"].notna().all()


def test_the_partial_warehouse_bar_is_rebuilt_with_broker_minutes(tmp_path):
    p = make_warehouse(tmp_path)
    frame = LiveBars(FakeBroker(), p).load("h1")
    seam = frame[frame["ts"] == START + pd.Timedelta(hours=4)].iloc[0]
    assert seam["m1_count"] == 60  # 30 warehouse minutes and 30 broker minutes, once
    assert seam["flags"] & build.FLAG_BROKER
    assert seam["bid_close"] == pytest.approx(3000.5 + 29)
    assert frame["ts"].iloc[-1] == START + pd.Timedelta(hours=5)
    assert not frame["ts"].duplicated().any()


def test_as_of_and_end_before_the_warehouse_end_leave_history_untouched(tmp_path):
    p = make_warehouse(tmp_path)
    broker = FakeBroker()
    live = LiveBars(broker, p)
    early = START + pd.Timedelta(hours=2)
    pd.testing.assert_frame_equal(live.load("h1", end=early), store.load_bars("h1", end=early, p=p))
    pd.testing.assert_frame_equal(live.load("m1", as_of=early), store.load_bars("m1", as_of=early, p=p))
    assert broker.calls == 0


def test_as_of_drops_bars_still_forming(tmp_path):
    p = make_warehouse(tmp_path)
    frame = LiveBars(FakeBroker(), p).load("h1", as_of=START + pd.Timedelta(minutes=320))
    assert frame["close_ts"].max() <= START + pd.Timedelta(minutes=320)


def test_a_failing_broker_falls_back_to_the_warehouse_and_says_why(tmp_path):
    p = make_warehouse(tmp_path)
    live = LiveBars(FakeBroker(fail=True), p)
    pd.testing.assert_frame_equal(live.load("m1"), store.load_bars("m1", p=p))
    assert "terminal gone" in live.last_error


def test_without_a_bar_capable_broker_it_is_exactly_the_warehouse(tmp_path):
    p = make_warehouse(tmp_path)
    live = LiveBars(object(), p)
    assert not live.enabled
    pd.testing.assert_frame_equal(live.load("h1"), store.load_bars("h1", p=p))


def test_the_tail_is_cached_briefly(tmp_path):
    p = make_warehouse(tmp_path)
    broker = FakeBroker()
    live = LiveBars(broker, p, ttl=60)
    live.load("m1")
    live.load("h1")
    assert broker.calls == 1
