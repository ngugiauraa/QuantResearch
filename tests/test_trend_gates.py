import numpy as np
import pandas as pd

from quantresearch.strategies import engines, get

OFF = dict(use_session=False, use_vol_band=False, no_friday_late=False, fomc_blackout_h=0.0, use_regime=False)


def hourly(n, start="2024-01-01 00:00", **cols):
    ts = pd.date_range(start, periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"ts": ts, **{k: v for k, v in cols.items()}})


def gate(df, **over):
    return engines.trend_entry_gate(df, {**OFF, **over})


def test_session_gate_allows_only_the_london_and_new_york_open_bars():
    df = hourly(24)
    allowed = gate(df, use_session=True)
    assert df.loc[allowed, "ts"].dt.hour.tolist() == [7, 8, 13, 14]


def test_no_entries_late_on_friday_but_monday_is_fine():
    df = hourly(24 * 7, start="2024-01-01 00:00")  # Monday 2024-01-01
    allowed = gate(df, no_friday_late=True)
    late_friday = (df["ts"].dt.day_of_week == 4) & (df["ts"].dt.hour >= 18)
    assert not allowed[late_friday].any() and allowed[~late_friday].all()
    assert allowed[(df["ts"].dt.day_of_week == 4) & (df["ts"].dt.hour == 17)].all()
    assert allowed[(df["ts"].dt.day_of_week == 0) & (df["ts"].dt.hour == 18)].all()


def test_fomc_blackout_covers_zero_to_four_hours_before_and_ignores_missing():
    df = hourly(6, hours_to_fomc=[5.0, 4.0, 2.0, 0.0, np.nan, 30.0])
    assert gate(df, fomc_blackout_h=4.0).tolist() == [True, False, False, False, True, True]
    assert gate(hourly(3), fomc_blackout_h=4.0).all()  # No hours_to_fomc column: the gate stays out of the way.


def test_regime_gate_blocks_ranging_and_volatile_only():
    df = hourly(6, regime=[3.0, 4.0, 1.0, 0.0, 2.0, np.nan])
    assert gate(df, use_regime=True).tolist() == [False, False, True, True, True, True]


def test_volatility_band_uses_the_trailing_percentile():
    rng = np.random.default_rng(0)
    base = rng.permutation(np.arange(1.0, 501.0))
    for latest, expected in ((250.0, True), (500.0, False), (10.0, False)):
        df = hourly(501, atr_14=np.append(base, latest))
        assert bool(gate(df, use_vol_band=True).iloc[-1]) is expected, latest
    assert not gate(hourly(501, atr_14=np.append(base, np.nan)), use_vol_band=True).iloc[-1]  # No history: no entry.


def scene(start, n=100):
    close = np.linspace(100, 110, n)
    close[-1] = 130  # A breakout through the 55-bar channel on the last bar.
    return hourly(n, start=start, close=close, high=close + 1, low=close - 1, atr_14=2.0, adx=30.0, ema_20=120.0, ema_50=110.0)


def test_entry_fires_in_the_london_open_and_not_at_3am():
    params = get("trend").params(use_daily=False, use_vol_band=False)
    london = scene("2024-01-01 04:00")  # Last bar opens 07:00 on Friday 2024-01-05.
    night = scene("2024-01-01 00:00")  # Last bar opens 03:00.
    assert london["ts"].iloc[-1].hour == 7 and night["ts"].iloc[-1].hour == 3
    assert engines.prepare_trend(london, params).long_entry.iloc[-1]
    assert not engines.prepare_trend(night, params).long_entry.iloc[-1]


def test_exit_is_a_two_atr_trail_below_the_recent_high_and_is_never_gated():
    close = np.concatenate([np.linspace(100, 120, 40), [114.0]])  # Falls 6 below the 121 peak high; 2 x ATR is 4.
    df = hourly(41, start="2024-01-01 03:00", close=close, high=close + 1, low=close - 1, atr_14=2.0, adx=30.0, ema_20=110.0, ema_50=105.0)
    s = engines.prepare_trend(df, get("trend").params(use_daily=False))
    assert s.exit_long.iloc[-1] and not s.exit_long.iloc[:-1].any()  # 03:00 start/hour is outside the entry session.
    mild = df.assign(close=np.concatenate([close[:-1], [118.0]]))  # Only 3 below the peak: still inside the trail.
    assert not engines.prepare_trend(mild, get("trend").params(use_daily=False)).exit_long.iloc[-1]
