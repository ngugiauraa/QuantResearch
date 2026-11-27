"""Cross-market series fall back to MT5 when Dukascopy refuses, flagged, with Dukascopy taking priority."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from quantresearch.core.config import Paths
from quantresearch.external import sources as src
from quantresearch.external import store as ext


class FakeBroker:
    symbol_name = "XAUUSDm"

    def __init__(self, start="2026-11-20", hours=72, offered=None):
        self.start, self.hours = pd.Timestamp(start, tz="UTC"), hours
        self.offered = offered
        self.asked = []

    def rates(self, symbol, timeframe="h1", count=99_999):
        self.asked.append(symbol)
        if self.offered is not None and symbol not in self.offered:
            raise RuntimeError(f"no {symbol}")
        return [{"ts": (self.start + pd.Timedelta(hours=i)).to_pydatetime(), "open": 30.0 + i, "high": 31.0 + i, "low": 29.0 + i,
                 "close": 30.5 + i, "tick_volume": 100.0, "spread": 0.02} for i in range(self.hours)]


def provider_frame(start, hours):
    ts = pd.date_range(start, periods=hours, freq="h", tz="UTC")
    return pd.DataFrame({"ts": ts, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 5.0})


@pytest.fixture()
def p(tmp_path):
    return Paths(tmp_path)


def test_mt5_bars_use_the_accounts_suffix_and_are_flagged(p):
    broker = FakeBroker()
    src.fetch_cross_mt5("xagusd", broker, p=p)
    frame = ext.load_cross("xagusd", "h1", p)
    assert broker.asked == ["XAGUSDm"]
    assert len(frame) == 72 and (frame["flags"] == src.FLAG_BROKER).all() and (frame["source"] == "mt5:XAGUSDm").all()
    assert "close_ts" in frame


def test_dukascopy_takes_priority_and_mt5_fills_only_uncovered_days_and_the_tail(p):
    src._write(src.cross_path(p, "xagusd", "h1", "provider"), provider_frame("2026-11-20", 30))  # 20th, and the 21st until 05:00
    src.fetch_cross_mt5("xagusd", FakeBroker(start="2026-11-20", hours=72), p=p)
    frame = ext.load_cross("xagusd", "h1", p)
    duk, mt5 = frame[frame["flags"] == 0], frame[frame["flags"] == src.FLAG_BROKER]
    assert len(duk) == 30 and (duk["close"] == 1.0).all()
    # Broker hours after the provider's last bar fill in, nothing earlier on covered days is replaced.
    assert mt5["ts"].min() == pd.Timestamp("2026-11-21 06:00", tz="UTC") and len(mt5) == 72 - 30
    assert frame["ts"].is_unique and frame["ts"].is_monotonic_increasing


def test_a_later_full_dukascopy_download_replaces_the_mt5_rows(p):
    src.fetch_cross_mt5("eurusd", FakeBroker(), p=p)
    src._write(src.cross_path(p, "eurusd", "h1", "provider"), provider_frame("2026-11-20", 72))
    src.merge_cross("eurusd", "h1", p)
    assert (ext.load_cross("eurusd", "h1", p)["flags"] == 0).all()


def test_archived_mt5_history_is_kept_when_the_broker_serves_a_shorter_window(p):
    src.fetch_cross_mt5("eurusd", FakeBroker(start="2026-11-01", hours=48), p=p)
    src.fetch_cross_mt5("eurusd", FakeBroker(start="2026-11-02", hours=48), p=p)
    frame = ext.load_cross("eurusd", "h1", p)
    assert frame["ts"].min() == pd.Timestamp("2026-11-01", tz="UTC") and len(frame) == 72 and frame["ts"].is_unique


def test_a_dukascopy_file_from_before_the_split_is_kept(p):
    src._write(src.cross_path(p, "usdjpy", "h1"), provider_frame("2026-11-20", 24))  # Old layout: no flags column.
    src.fetch_cross_mt5("usdjpy", FakeBroker(start="2026-11-20", hours=48), p=p)
    frame = ext.load_cross("usdjpy", "h1", p)
    assert (frame["flags"] == 0).sum() == 24 and (frame["flags"] == src.FLAG_BROKER).sum() == 24


def test_refresh_falls_back_to_mt5_and_reports_both(p, monkeypatch):
    def refused(symbol, **kw):
        raise RuntimeError(f"{symbol}: plain 403")

    monkeypatch.setattr(src, "fetch_cross", refused)
    status = ext.refresh_cross(p, FakeBroker())
    assert status["xagusd"].startswith("ok (mt5 XAGUSDm, 72 bars") and "403" in status["xagusd"]
    assert status["usa500idxusd"].startswith("ok (mt5 US500m")
    assert status["ustbondtrusd"].startswith("failed") and "no equivalent" in status["ustbondtrusd"]


def test_the_broker_is_only_connected_when_a_fallback_is_needed(p, monkeypatch):
    monkeypatch.setattr(src, "fetch_cross", lambda symbol, **kw: src.cross_path(p, symbol))
    calls = []
    status = ext.refresh_cross(p, lambda: calls.append(1) or FakeBroker())
    assert not calls and all(v.startswith("ok (dukascopy)") for v in status.values())


def test_a_symbol_the_broker_lacks_is_reported_not_raised(p, monkeypatch):
    monkeypatch.setattr(src, "fetch_cross", lambda symbol, **kw: (_ for _ in ()).throw(RuntimeError("403")))
    status = ext.refresh_cross(p, FakeBroker(offered={"XAGUSDm"}))
    assert status["xagusd"].startswith("ok (mt5") and status["eurusd"].startswith("failed") and "MT5" in status["eurusd"]


def test_suffix_follows_the_gold_symbol():
    assert src.mt5_suffix("XAUUSDm") == "m" and src.mt5_suffix("XAUUSD") == "" and src.mt5_suffix("XAUUSD.r") == ".r"


def test_the_forming_bar_is_not_stored(p):
    src.fetch_cross_mt5("xagusd", FakeBroker(start="2026-11-20", hours=72), p=p, now=pd.Timestamp("2026-11-22 23:30", tz="UTC"))
    assert ext.load_cross("xagusd", "h1", p)["ts"].max() == pd.Timestamp("2026-11-22 22:00", tz="UTC")
