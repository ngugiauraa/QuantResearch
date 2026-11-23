import numpy as np
import pandas as pd

from quantresearch.strategies import REGISTRY, engines, get
from quantresearch.strategies.base import SIGNAL_COLUMNS

DAYS = 5  # Monday 2024-01-01 to Friday 2024-01-05
P = get("london_open_momentum").defaults


def at(day, hh, mm):
    return day * 288 + (hh * 60 + mm) // 5


def build():
    n = DAYS * 288
    ts = pd.date_range("2024-01-01 00:00", periods=n, freq="5min", tz="UTC")
    close = np.full(n, 100.0)
    close[at(0, 7, 15)] = 100.4  # Monday: first up-cross (0.4 ATR) ...
    close[at(0, 7, 20)] = 99.5  # ... followed by a down-cross that must NOT trade (not the first).
    close[at(1, 6, 50)] = 105.0  # Tuesday: an earlier Asian bar, which must not be the reference.
    close[at(1, 7, 30)] = 99.65  # Tuesday: first down-cross, -0.35 ATR from the 06:55 close of 100.
    close[at(2, 8, 30)] = 101.0  # Wednesday: crosses only after the window closes.
    close[at(3, 8, 25)] = 100.6  # Thursday: the last bar inside the window.
    close[at(4, 7, 15)] = 101.0  # Friday: excluded day.
    return pd.DataFrame({"ts": ts, "close": close, "atr_14": 1.0})


def test_only_the_first_cross_inside_the_window_on_monday_to_thursday_is_an_event():
    df = build()
    ev = engines.london_open_events(df, P)
    ups = df.loc[ev["first_up"], "ts"].tolist()
    downs = df.loc[ev["first_down"], "ts"].tolist()
    assert ups == [pd.Timestamp("2024-01-01 07:15", tz="UTC"), pd.Timestamp("2024-01-04 08:25", tz="UTC")]
    assert downs == [pd.Timestamp("2024-01-02 07:30", tz="UTC")]


def test_signals_columns_stop_and_hold():
    df = build()
    s = engines.prepare_london_open(df, P)
    assert set(SIGNAL_COLUMNS) <= set(s.columns)
    assert s.long_entry.sum() == 2 and s.short_entry.sum() == 1
    assert s.stop_dist.iloc[at(0, 7, 15)] == 1.0 and (s.max_hold == 2).all()


def test_a_half_atr_reversal_from_the_entry_extreme_exits():
    s = engines.prepare_london_open(build(), P)
    assert s.exit_long.iloc[at(0, 7, 20)]  # 99.5 < 100.4 - 0.5
    assert not s.exit_long.iloc[at(0, 7, 15)]  # The entry bar itself is the peak.


def test_signals_do_not_read_the_future():
    df = build()
    full = engines.prepare_london_open(df, P)
    for cut in (at(0, 7, 20), at(1, 7, 30) + 1, at(3, 8, 20), at(3, 8, 30), 900):
        part = engines.prepare_london_open(df.iloc[:cut].reset_index(drop=True), P)
        for col in SIGNAL_COLUMNS:
            assert np.array_equal(full[col].iloc[:cut].to_numpy(), part[col].to_numpy(), equal_nan=True), (col, cut)


def test_the_cross_is_scaled_by_atr_and_needs_a_known_asian_close():
    df = build()
    assert not engines.london_open_events(df.assign(atr_14=2.0), P)["first_up"].iloc[at(0, 7, 15)]  # 0.2 ATR < 0.3
    gap = df.iloc[at(0, 7, 0):].reset_index(drop=True)  # No bars before 07:00 on the first day: no reference, no trade.
    assert not engines.london_open_events(gap, P)["first_up"].iloc[at(0, 7, 15) - at(0, 7, 0)]


def test_hypotheses_cover_continuation_reversal_and_weekday_split():
    df = build()
    hyps = {h.id: h for h in engines.hypotheses_london_open(df, P)}
    assert {"LO1_up", "LO1_down", "LO2_up", "LO2_down", "LO3_mon_wed_up", "LO3_thu_up"} <= set(hyps)
    assert hyps["LO1_up"].mask.sum() == 2 and hyps["LO1_up"].horizon == 2 and hyps["LO1_up"].side == 1
    shifted = hyps["LO2_up"].mask
    assert shifted.iloc[at(0, 7, 15) + 4] and shifted.sum() == 2 and hyps["LO2_up"].side == -1
    assert hyps["LO3_mon_wed_up"].mask.sum() == 1 and hyps["LO3_thu_up"].mask.sum() == 1
    assert hyps["LO3_mon_wed_down"].mask.sum() == 1 and hyps["LO3_thu_down"].mask.sum() == 0


def test_it_is_registered_as_an_m5_engine_with_a_complete_record():
    d = REGISTRY["london_open_momentum"]
    assert d.signal_tf == "m5" and d.limitations and set(d.grid) <= set(d.defaults)
