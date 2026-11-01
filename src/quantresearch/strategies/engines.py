"""The seven strategy frameworks, as measurable rules.

Parameters and hypotheses are research starting points. Nothing here assumes the edge exists:
each engine's job is to give its idea a fair test and report what happens.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from quantresearch.core import timeframes as tfs
from quantresearch.strategies.base import (Hypothesis, StrategyDef, daily_trend, empty_signals, rolling_rank,
                                           trading_day_position)
from quantresearch.warehouse import calendar as cal


# ---------------------------------------------------------------- 1. Mean reversion
def prepare_mean_reversion(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    s = empty_signals(df)
    calm = df["adx"] < p["adx_max"]
    s["long_entry"] = (df["zscore_20"] < -p["z_entry"]) & (df["rsi_14"] < p["rsi_low"]) & calm
    s["short_entry"] = (df["zscore_20"] > p["z_entry"]) & (df["rsi_14"] > 100 - p["rsi_low"]) & calm
    s["stop_dist"] = p["stop_atr"] * df["atr_14"]
    # Target is the rolling mean, the statistical equilibrium the idea assumes.
    s["target_dist"] = (df["close"] - df["sma_20"]).abs().clip(lower=0.25 * df["atr_14"])
    s["max_hold"] = p["max_hold"]
    return s


def hypotheses_mean_reversion(df: pd.DataFrame, p: dict) -> list[Hypothesis]:
    calm = df["adx"] < p["adx_max"]
    return [
        Hypothesis("MR1", "After a z-score below -2, forward returns are positive", df["zscore_20"] < -2, 1, 12),
        Hypothesis("MR2", "After a z-score above +2, forward returns are negative", df["zscore_20"] > 2, -1, 12),
        Hypothesis("MR3", "Reversion after low z-scores holds in low-trend regimes", (df["zscore_20"] < -2) & calm, 1, 12),
        Hypothesis("MR4", "Reversion after high z-scores holds in low-trend regimes", (df["zscore_20"] > 2) & calm, -1, 12),
        Hypothesis("MR5", "RSI below 30 precedes positive returns", df["rsi_14"] < 30, 1, 12),
        Hypothesis("MR6", "Closes under the lower Bollinger band precede positive returns", df["bb_pct"] < 0, 1, 12),
    ]


# ---------------------------------------------------------------- 2. Trend and momentum
def prepare_trend(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    s = empty_signals(df)
    n = p["channel"]
    upper = df["high"].rolling(n, min_periods=n).max().shift(1)
    lower = df["low"].rolling(n, min_periods=n).min().shift(1)
    strong = df["adx"] > p["adx_min"]
    align = daily_trend(df) if p["use_daily"] else pd.Series(0.0, index=df.index)
    long_ok = (align >= 0) if not p["use_daily"] else (align > 0)
    short_ok = (align <= 0) if not p["use_daily"] else (align < 0)
    s["long_entry"] = (df["close"] > upper) & (df["ema_20"] > df["ema_50"]) & strong & long_ok
    s["short_entry"] = (df["close"] < lower) & (df["ema_20"] < df["ema_50"]) & strong & short_ok
    s["stop_dist"] = p["stop_atr"] * df["atr_14"]
    s["max_hold"] = p["max_hold"]
    s["exit_long"] = df["close"] < df["ema_50"]
    s["exit_short"] = df["close"] > df["ema_50"]
    return s


def hypotheses_trend(df: pd.DataFrame, p: dict) -> list[Hypothesis]:
    up = df["ema_20"] > df["ema_50"]
    return [
        Hypothesis("TF1", "Breakouts above the 20-bar channel continue up", df["breakout_up_20"] == 1, 1, 24),
        Hypothesis("TF2", "Breakdowns below the 20-bar channel continue down", df["breakout_down_20"] == 1, -1, 24),
        Hypothesis("TF3", "Positive 60-bar momentum persists", df["mom_60"] > 0, 1, 24),
        Hypothesis("TF4", "Breakouts with ADX above 25 continue further", (df["breakout_up_20"] == 1) & (df["adx"] > 25), 1, 24),
        Hypothesis("TF5", "Breakouts during volatility expansion continue", (df["breakout_up_20"] == 1) & (df["vol_ratio"] > 1.2), 1, 24),
        Hypothesis("TF6", "A rising fast average with a rising daily trend predicts gains", up & (daily_trend(df) > 0), 1, 24),
    ]


# ---------------------------------------------------------------- 3. Statistical arbitrage
def spread_model(df: pd.DataFrame, leg: str, window: int) -> pd.DataFrame:
    """Rolling regression of ln(gold) on ln(leg). All inputs are trailing, so the residual is causal."""
    if f"{leg}_close" not in df or df[f"{leg}_close"].notna().sum() < window:
        return pd.DataFrame({"beta": np.nan, "residual": np.nan, "spread_z": np.nan, "corr": np.nan}, index=df.index)
    y, x = np.log(df["close"]), np.log(df[f"{leg}_close"])
    cov = y.rolling(window, min_periods=window).cov(x)
    var = x.rolling(window, min_periods=window).var()
    beta = cov / var
    alpha = y.rolling(window, min_periods=window).mean() - beta * x.rolling(window, min_periods=window).mean()
    residual = y - (alpha + beta * x)
    sd = residual.rolling(window, min_periods=window).std()
    return pd.DataFrame({"beta": beta, "residual": residual, "spread_z": residual / sd.where(sd > 0),
                         "corr": y.rolling(window, min_periods=window).corr(x)}, index=df.index)


def prepare_stat_arb(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    s = empty_signals(df)
    m = spread_model(df, p["leg"], p["window"])
    stable = m["corr"].abs() > p["corr_min"]
    s["long_entry"] = (m["spread_z"] < -p["z_entry"]) & stable
    s["short_entry"] = (m["spread_z"] > p["z_entry"]) & stable
    s["stop_dist"] = p["stop_atr"] * df["atr_14"]
    s["max_hold"] = p["max_hold"]
    s["exit_long"] = m["spread_z"] > -p["z_exit"]
    s["exit_short"] = m["spread_z"] < p["z_exit"]
    return s


def hypotheses_stat_arb(df: pd.DataFrame, p: dict) -> list[Hypothesis]:
    out = []
    for leg in ("xagusd", "eurusd", "dollaridxusd"):
        m = spread_model(df, leg, p["window"])
        out.append(Hypothesis(f"SA_{leg}_low", f"Gold cheap to {leg} on the residual rises", m["spread_z"] < -2, 1, 24))
        out.append(Hypothesis(f"SA_{leg}_high", f"Gold rich to {leg} on the residual falls", m["spread_z"] > 2, -1, 24))
    return out


# ---------------------------------------------------------------- 4. Behavioral bias recognition
def behavioral_measures(df: pd.DataFrame) -> pd.DataFrame:
    """Observable stand-ins for panic, FOMO and stop runs. They measure price behaviour, not emotions."""
    ret = df["logret_1"]
    ret_z = ret / df["vol_60"].where(df["vol_60"] > 0)
    tr_mean = df["tr"].rolling(100, min_periods=100).mean()
    tr_sd = df["tr"].rolling(100, min_periods=100).std()
    span = (df["high"] - df["low"]).where(lambda x: x > 0)
    return pd.DataFrame({
        "ret_z": ret_z,
        "range_z": (df["tr"] - tr_mean) / tr_sd.where(tr_sd > 0),
        "close_pos": (df["close"] - df["low"]) / span,
        "volatility_cluster": rolling_rank(df["vol_20"]),
    }, index=df.index)


def prepare_behavioral(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    s = empty_signals(df)
    b = behavioral_measures(df)
    active = df["volume_ratio"] > p["volume_min"]
    panic = (b["ret_z"] < -p["shock_z"]) & active
    fomo = (b["ret_z"] > p["shock_z"]) & active
    stop_run_low = (df["sweep_low"] == 1) & (b["range_z"] > p["range_z"]) & (b["close_pos"] > 0.6)
    stop_run_high = (df["sweep_high"] == 1) & (b["range_z"] > p["range_z"]) & (b["close_pos"] < 0.4)
    s["long_entry"] = panic | stop_run_low
    s["short_entry"] = fomo | stop_run_high
    s["stop_dist"] = p["stop_atr"] * df["atr_14"]
    s["target_dist"] = p["target_r"] * s["stop_dist"]
    s["max_hold"] = p["max_hold"]
    return s


def hypotheses_behavioral(df: pd.DataFrame, p: dict) -> list[Hypothesis]:
    b = behavioral_measures(df)
    return [
        Hypothesis("BB1", "Panic: a return under -3 sd is followed by a rebound", b["ret_z"] < -3, 1, 8),
        Hypothesis("BB2", "FOMO: a return above +3 sd is followed by a pullback", b["ret_z"] > 3, -1, 8),
        Hypothesis("BB3", "A low sweep with the close back inside precedes gains", df["sweep_low"] == 1, 1, 8),
        Hypothesis("BB4", "A high sweep with the close back inside precedes losses", df["sweep_high"] == 1, -1, 8),
        Hypothesis("BB5", "Sweeps on abnormal range reverse more", (df["sweep_low"] == 1) & (b["range_z"] > 1.5), 1, 8),
        Hypothesis("BB6", "Extreme volatility regimes follow through down", b["volatility_cluster"] > 0.95, -1, 8),
    ]


# ---------------------------------------------------------------- 5. ETF and turnaround structure
def first_notice_windows(ts: pd.Series, days_before: int) -> pd.Series:
    """True in the roll window before COMEX gold first notice.

    Active months are Feb, Apr, Jun, Aug, Dec (Oct is thinner and left out). First notice is taken as
    the last trading weekday of the month before delivery. This is a rule-based approximation, not
    the exchange's published calendar. The calendar is knowable in advance.
    """
    dates = ts.dt.date
    unique = sorted(set(dates))
    if not unique:
        return pd.Series(False, index=ts.index)
    marks: set[date] = set()
    for year in range(unique[0].year - 1, unique[-1].year + 2):
        for delivery in (2, 4, 6, 8, 12):
            prev_year, prev_month = (year - 1, 12) if delivery == 1 else (year, delivery - 1)
            notice = cal.last_trading_weekday(prev_year, prev_month)
            day = notice
            count = 0
            while count < days_before:
                marks.add(day)
                day -= timedelta(days=1)
                while not cal.is_trading_weekday(day):
                    day -= timedelta(days=1)
                count += 1
    return dates.map(lambda d: d in marks).astype(bool)


def prepare_etf_turnaround(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    s = empty_signals(df)
    pos = trading_day_position(df["ts"])
    if p["mode"] == "turn_of_month":
        window = (pos["tdoe"] <= p["days_before"]) | (pos["tdom"] <= p["days_after"])
    else:
        window = first_notice_windows(df["ts"], p["days_before"])
    entering = window & ~window.shift(1, fill_value=False)
    leaving = ~window
    if p["direction"] > 0:
        s["long_entry"], s["exit_long"] = entering, leaving
    else:
        s["short_entry"], s["exit_short"] = entering, leaving
    s["stop_dist"] = p["stop_atr"] * df["atr_50"]
    return s


def hypotheses_etf_turnaround(df: pd.DataFrame, p: dict) -> list[Hypothesis]:
    pos = trading_day_position(df["ts"])
    out = [
        Hypothesis("ET1", "Returns are positive in the last 2 trading days of a month", pos["tdoe"] <= 2, 1, 24),
        Hypothesis("ET2", "Returns are positive in the first 3 trading days of a month", pos["tdom"] <= 3, 1, 24),
        Hypothesis("ET3", "Returns are negative in the 3 days before first notice", first_notice_windows(df["ts"], 3), -1, 24),
        Hypothesis("ET4", "Quarter-end days behave differently", (pos["tdoe"] <= 2) & df["ts"].dt.month.isin([3, 6, 9, 12]), -1, 24),
    ]
    if "cot_mm_z" in df:
        out += [Hypothesis("ET5", "Extreme speculative long positioning precedes declines", df["cot_mm_z"] > 1.5, -1, 120),
                Hypothesis("ET6", "Extreme speculative short positioning precedes gains", df["cot_mm_z"] < -1.5, 1, 120)]
    return out


# ---------------------------------------------------------------- 6. Seasonal and calendar
def fit_seasonal(train: pd.DataFrame, p: dict) -> dict:
    """Pick (weekday, hour) cells from TRAINING data only. Selection never sees the test period."""
    ret = np.log(train["close"].shift(-1) / train["close"])
    frame = pd.DataFrame({"ret": ret, "wd": train["ts"].dt.day_of_week, "hr": train["ts"].dt.hour}).dropna()
    table = frame.groupby(["wd", "hr"])["ret"].agg(["mean", "std", "count"])
    table = table[table["count"] >= p["min_obs"]]
    table["t"] = table["mean"] / (table["std"] / np.sqrt(table["count"]))
    longs = [list(map(int, i)) for i in table.index[table["t"] > p["t_min"]]]
    shorts = [list(map(int, i)) for i in table.index[table["t"] < -p["t_min"]]]
    return {"long_cells": longs, "short_cells": shorts}


def prepare_seasonal(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    s = empty_signals(df)
    code = df["ts"].dt.day_of_week * 100 + df["ts"].dt.hour
    longs = {a * 100 + b for a, b in p.get("long_cells", [])}
    shorts = {a * 100 + b for a, b in p.get("short_cells", [])}
    # A cell's return is the bar after it, so signal on the cell's close and hold one bar.
    s["long_entry"] = code.isin(longs)
    s["short_entry"] = code.isin(shorts)
    s["stop_dist"] = p["stop_atr"] * df["atr_14"]
    s["max_hold"] = 1
    return s


def hypotheses_seasonal(df: pd.DataFrame, p: dict) -> list[Hypothesis]:
    ts = df["ts"]
    flags = cal.session_flags(ts)
    out = [Hypothesis(f"SE_wd{d}", f"Returns differ from zero on weekday {d}", ts.dt.day_of_week == d, 1, 1) for d in range(5)]
    out += [Hypothesis(f"SE_{name}", f"Returns differ from zero in the {name} session", flags[name], 1, 1) for name in flags]
    out += [Hypothesis(f"SE_m{m}", f"Returns differ from zero in month {m}", ts.dt.month == m, 1, 1) for m in range(1, 13)]
    if "hours_to_fomc" in df:
        out.append(Hypothesis("SE_fomc", "Gold rises into FOMC decisions (24 hours before)", df["hours_to_fomc"].between(0, 24), 1, 1))
    return out


# ---------------------------------------------------------------- 7. Overreaction and gaps
def gap_measures(df: pd.DataFrame, closure_hours: float = 6.0) -> pd.DataFrame:
    """A gap is the jump from the last close to the first open after a closure.

    Measured on bid prices. Bars after a closure have long gaps between their timestamps, and that
    is all it takes to spot them; no future data is used.
    """
    previous = df["bid_close"].shift(1)
    idle = df["ts"].diff().dt.total_seconds() / 3600 > closure_hours
    gap = (df["bid_open"] - previous).where(idle)
    return pd.DataFrame({
        "gap": gap,
        "gap_atr": gap / df["atr_14"].shift(1),
        "shock_z": df["logret_1"] / df["vol_60"].shift(1).where(df["vol_60"].shift(1) > 0),
    }, index=df.index)


def prepare_overreaction(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    s = empty_signals(df)
    m = gap_measures(df)
    side = -1 if p["mode"] == "fade" else 1
    up_event = (m["gap_atr"] > p["gap_atr"]) | (m["shock_z"] > p["shock_z"])
    down_event = (m["gap_atr"] < -p["gap_atr"]) | (m["shock_z"] < -p["shock_z"])
    s["long_entry"] = down_event if side < 0 else up_event
    s["short_entry"] = up_event if side < 0 else down_event
    s["stop_dist"] = p["stop_atr"] * df["atr_14"]
    s["target_dist"] = p["target_r"] * s["stop_dist"]
    s["max_hold"] = p["max_hold"]
    return s


def hypotheses_overreaction(df: pd.DataFrame, p: dict) -> list[Hypothesis]:
    m = gap_measures(df)
    return [
        Hypothesis("OR1", "Gaps up over 0.5 ATR tend to fill", m["gap_atr"] > 0.5, -1, 8),
        Hypothesis("OR2", "Gaps down over 0.5 ATR tend to fill", m["gap_atr"] < -0.5, 1, 8),
        Hypothesis("OR3", "A shock under -3 sd reverses", m["shock_z"] < -3, 1, 8),
        Hypothesis("OR4", "A shock above +3 sd reverses", m["shock_z"] > 3, -1, 8),
        Hypothesis("OR5", "A shock above +3 sd continues (the opposing view)", m["shock_z"] > 3, 1, 24),
        Hypothesis("OR6", "A shock under -3 sd continues (the opposing view)", m["shock_z"] < -3, -1, 24),
    ]


DEFINITIONS = [
    StrategyDef("mean_reversion", "Mean Reversion", "h1",
                dict(z_entry=2.0, rsi_low=30.0, adx_max=25.0, stop_atr=1.5, max_hold=24),
                dict(z_entry=[1.5, 2.0, 2.5], adx_max=[20.0, 25.0, 35.0], stop_atr=[1.0, 1.5, 2.5]),
                prepare_mean_reversion, hypotheses_mean_reversion,
                "Fades z-score and RSI extremes back to the 20-bar mean in low-ADX regimes, with an ATR stop and time exit.",
                ["ADX is a lagging regime proxy.", "Trending years can erase many small wins."]),
    StrategyDef("trend", "Trend Following and Momentum", "h1",
                dict(channel=55, adx_min=20.0, use_daily=True, stop_atr=2.0, max_hold=480),
                dict(channel=[20, 55, 100], adx_min=[15.0, 20.0, 30.0], use_daily=[True, False]),
                prepare_trend, hypotheses_trend,
                "Enters channel breakouts confirmed by average alignment, ADX and the last completed day's trend. Exits on a slow-average cross or stop.",
                ["Breakout entries pay spread at the worst moments.", "Gold's long bull runs flatter long-side trend tests."]),
    StrategyDef("stat_arb", "Statistical Arbitrage", "h1",
                dict(leg="xagusd", window=720, z_entry=2.0, z_exit=0.5, corr_min=0.5, stop_atr=3.0, max_hold=240),
                dict(leg=["xagusd", "eurusd"], z_entry=[1.5, 2.0, 2.5], window=[480, 720]),
                prepare_stat_arb, hypotheses_stat_arb,
                "Trades gold's residual from a rolling log regression on a related market, with a correlation stability filter.",
                ["Only the gold leg is traded, in line with the project's scope, so this is relative-value timing, "
                 "not a hedged spread.", "Cointegration is tested separately and may not hold."],
                needs=["xagusd", "eurusd", "dollaridxusd"]),
    StrategyDef("behavioral", "Behavioral Bias Recognition", "m15",
                dict(shock_z=3.0, volume_min=1.2, range_z=1.5, stop_atr=1.5, target_r=1.5, max_hold=32),
                dict(shock_z=[2.5, 3.0, 4.0], range_z=[1.0, 1.5, 2.5], target_r=[1.0, 2.0]),
                prepare_behavioral, hypotheses_behavioral,
                "Fades abnormal return shocks and liquidity sweeps with abnormal range. Labels describe measurable price behaviour only.",
                ["'Panic' and 'FOMO' are names for measurements, not evidence of what traders felt.",
                 "Provider volume is an activity proxy."]),
    StrategyDef("etf_turnaround", "ETF and Turnaround Effects", "h1",
                dict(mode="turn_of_month", days_before=2, days_after=3, direction=1, stop_atr=4.0),
                dict(mode=["turn_of_month", "first_notice"], days_before=[1, 2, 3], direction=[1, -1]),
                prepare_etf_turnaround, hypotheses_etf_turnaround,
                "Tests turn-of-month and COMEX first-notice windows, and COT positioning extremes where available.",
                ["No machine-readable ETF holdings source was available, so ETF flows are NOT tested directly.",
                 "First-notice dates are approximated by rule, not taken from the exchange calendar.",
                 "No gold futures price data, so rollover price effects are not observed directly."]),
    StrategyDef("seasonal", "Seasonal and Calendar Effects", "h1",
                dict(t_min=3.0, min_obs=100, stop_atr=3.0),
                dict(t_min=[2.5, 3.0, 4.0]),
                prepare_seasonal, hypotheses_seasonal,
                "Selects weekday-hour cells on training data only and trades them out of sample.",
                ["120 cells are searched, so selection bias is high. Out-of-sample results are the ones that count.",
                 "CPI and payroll release times were not available: the BLS schedule refused automated access. "
                 "Only FOMC decisions are tested as events."],
                fit=fit_seasonal),
    StrategyDef("overreaction", "Overreaction and Gaps", "h1",
                dict(mode="fade", gap_atr=0.5, shock_z=3.0, stop_atr=2.0, target_r=1.0, max_hold=24),
                dict(mode=["fade", "follow"], gap_atr=[0.3, 0.5, 1.0], shock_z=[2.5, 3.0, 4.0]),
                prepare_overreaction, hypotheses_overreaction,
                "Compares fading against following large gaps and return shocks, across session boundaries.",
                ["A gap entry fills after the gap, at a possibly wide weekend-open spread."]),
]
