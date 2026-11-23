import numpy as np
import pandas as pd
import pytest

from quantresearch.strategies import engines, get

N = 150


def frame(**over):
    close = np.full(N, 100.0)
    ts = pd.date_range("2024-01-02", periods=N, freq="15min", tz="UTC")
    d = {"ts": ts, "close": close, "high": close + 1, "low": close - 1, "open": close, "atr_14": 2.0, "logret_1": 0.0,
         "vol_60": 0.01, "vol_20": 0.01, "volume_ratio": 2.0, "sweep_low": 0.0, "sweep_high": 0.0, "tr": 2.0,
         "structure_trend": 1.0, "spread": 0.3}
    d.update(over)
    return pd.DataFrame(d)


def shock(sign):
    return np.r_[np.zeros(N - 1), sign * 0.05]


P = get("behavioral").defaults


def test_panic_is_bought_only_in_an_uptrend():
    up = engines.prepare_behavioral(frame(logret_1=shock(-1), structure_trend=1.0), P)
    down = engines.prepare_behavioral(frame(logret_1=shock(-1), structure_trend=-1.0), P)
    flat = engines.prepare_behavioral(frame(logret_1=shock(-1), structure_trend=0.0), P)
    assert up.long_entry.iloc[-1] and not down.long_entry.iloc[-1] and not flat.long_entry.iloc[-1]


def test_fomo_is_sold_only_in_a_downtrend():
    down = engines.prepare_behavioral(frame(logret_1=shock(1), structure_trend=-1.0), P)
    up = engines.prepare_behavioral(frame(logret_1=shock(1), structure_trend=1.0), P)
    assert down.short_entry.iloc[-1] and not up.short_entry.iloc[-1]


def test_stop_runs_must_align_with_the_trend():
    # A low sweep on abnormal range, closing near the high, is a long setup; a high sweep is a short setup.
    base = dict(tr=np.r_[np.full(N - 1, 2.0), 30.0], low=np.r_[np.full(N - 1, 99.0), 90.0], high=np.r_[np.full(N - 1, 101.0), 101.0],
                close=np.r_[np.full(N - 1, 100.0), 100.5])
    low_sweep = frame(sweep_low=np.r_[np.zeros(N - 1), 1.0], **base)
    assert engines.prepare_behavioral(low_sweep.assign(structure_trend=1.0), P).long_entry.iloc[-1]
    assert not engines.prepare_behavioral(low_sweep.assign(structure_trend=-1.0), P).long_entry.iloc[-1]
    high_base = {**base, "high": np.r_[np.full(N - 1, 101.0), 112.0], "low": np.r_[np.full(N - 1, 99.0), 99.0],
                 "close": np.r_[np.full(N - 1, 100.0), 99.5]}
    high_sweep = frame(sweep_high=np.r_[np.zeros(N - 1), 1.0], **high_base)
    assert engines.prepare_behavioral(high_sweep.assign(structure_trend=-1.0), P).short_entry.iloc[-1]
    assert not engines.prepare_behavioral(high_sweep.assign(structure_trend=1.0), P).short_entry.iloc[-1]


@pytest.mark.parametrize("last_spread,expected", [(0.3, True), (0.44, True), (0.46, False), (3.0, False)])
def test_wide_spread_blocks_the_entry(last_spread, expected):
    # Median is 0.3, so the 1.5x limit is 0.45; the cases stay off the exact edge to avoid float noise.
    df = frame(logret_1=shock(-1), spread=np.r_[np.full(N - 1, 0.3), last_spread])
    assert bool(engines.prepare_behavioral(df, P).long_entry.iloc[-1]) is expected


def test_volatile_regime_is_avoided_and_missing_regime_is_ignored():
    df = frame(logret_1=shock(-1))
    assert engines.prepare_behavioral(df.assign(regime=1.0), P).long_entry.iloc[-1]
    assert not engines.prepare_behavioral(df.assign(regime=4.0), P).long_entry.iloc[-1]
    assert engines.prepare_behavioral(df, P).long_entry.iloc[-1]


def test_gates_can_be_switched_off():
    df = frame(logret_1=shock(-1), structure_trend=-1.0, spread=np.r_[np.full(N - 1, 0.3), 3.0])
    assert not engines.prepare_behavioral(df, P).long_entry.iloc[-1]
    off = {**P, "use_trend_align": False, "spread_gate": 0}
    assert engines.prepare_behavioral(df, off).long_entry.iloc[-1]
