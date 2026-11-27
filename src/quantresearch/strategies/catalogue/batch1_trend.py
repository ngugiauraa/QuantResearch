"""Catalogue batch 1: trend following and breakouts (20 strategies).

Rules are as documented in each entry; parameters are fixed. "Close above the prior N-bar high" means the bar's close
exceeds the highest high of the N bars before it. Where a classic rule uses an intraday stop order, this version
enters at the close that confirms the break (the engine acts at bar close), which is noted in the entry.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantresearch.strategies.base import trading_day_position
from quantresearch.strategies.catalogue.common import (NEW_YORK, cross_down, cross_up, ema, entry, local_clock, prior_high, prior_low,
                                                      signals, sma)

B = 1
FAMILY = "trend_breakout"


# ---------------------------------------------------------------- 1-3. Donchian / Turtle
def donchian(entry_n: int, exit_n: int, stop_atr: float):
    def prepare(df, p):
        return signals(df, long=df["close"] > prior_high(df, p["entry_n"]), short=df["close"] < prior_low(df, p["entry_n"]),
                       stop=p["stop_atr"] * df["atr_14"],
                       exit_long=df["close"] < prior_low(df, p["exit_n"]), exit_short=df["close"] > prior_high(df, p["exit_n"]))
    return prepare


# ---------------------------------------------------------------- 4-5. Moving-average crosses
def ma_cross(df, p):
    fast = sma(df["close"], p["fast"]) if p["kind"] == "sma" else ema(df["close"], p["fast"])
    slow = sma(df["close"], p["slow"]) if p["kind"] == "sma" else ema(df["close"], p["slow"])
    return signals(df, long=cross_up(fast, slow), short=cross_down(fast, slow), stop=p["stop_atr"] * df["atr_14"])


# ---------------------------------------------------------------- 6. Faber 10-month SMA (monthly bars)
def faber(df, p):
    avg = sma(df["close"], p["months"])
    return signals(df, long=df["close"] > avg, stop=p["stop_atr"] * df["atr_14"], exit_long=df["close"] < avg)


# ---------------------------------------------------------------- 7. Time-series momentum, monthly rebalance
def tsmom(df, p):
    rebalance = trading_day_position(df["ts"])["tdom"] == 1
    mom = df["close"] / df["close"].shift(p["lookback"]) - 1
    return signals(df, long=rebalance & (mom > 0), short=rebalance & (mom < 0), stop=p["stop_atr"] * df["atr_14"])


# ---------------------------------------------------------------- 8-9. Keltner and Bollinger breakouts
def keltner_breakout(df, p):
    mid = ema(df["close"], p["n"])
    upper, lower = mid + p["k"] * df["atr_14"], mid - p["k"] * df["atr_14"]
    return signals(df, long=cross_up(df["close"], upper), short=cross_down(df["close"], lower), stop=p["stop_atr"] * df["atr_14"],
                   exit_long=df["close"] < mid, exit_short=df["close"] > mid)


def bollinger_breakout(df, p):
    return signals(df, long=cross_up(df["close"], df["bb_upper"]), short=cross_down(df["close"], df["bb_lower"]),
                   stop=p["stop_atr"] * df["atr_14"], exit_long=df["close"] < df["bb_mid"], exit_short=df["close"] > df["bb_mid"])


# ---------------------------------------------------------------- 10. Wilder DI cross with ADX
def di_cross(df, p):
    strong = df["adx"] > p["adx_min"]
    up, down = cross_up(df["plus_di"], df["minus_di"]), cross_up(df["minus_di"], df["plus_di"])
    return signals(df, long=up & strong, short=down & strong, stop=p["stop_atr"] * df["atr_14"], exit_long=down, exit_short=up)


# ---------------------------------------------------------------- 11. Supertrend
def supertrend_line(df: pd.DataFrame, n: int, mult: float) -> tuple[pd.Series, pd.Series]:
    """Standard Supertrend on Wilder ATR(n). Returns (line, direction +1/-1). Each value uses the bar and earlier bars."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean().to_numpy()
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    close = df["close"].to_numpy()
    upper_basic, lower_basic = hl2 + mult * atr, hl2 - mult * atr
    upper, lower = np.full(len(df), np.nan), np.full(len(df), np.nan)
    direction = np.zeros(len(df))
    for i in range(len(df)):
        if np.isnan(atr[i]):
            continue
        if i == 0 or np.isnan(upper[i - 1]):
            upper[i], lower[i], direction[i] = upper_basic[i], lower_basic[i], 1
            continue
        upper[i] = upper_basic[i] if (upper_basic[i] < upper[i - 1] or close[i - 1] > upper[i - 1]) else upper[i - 1]
        lower[i] = lower_basic[i] if (lower_basic[i] > lower[i - 1] or close[i - 1] < lower[i - 1]) else lower[i - 1]
        if direction[i - 1] >= 0:
            direction[i] = -1 if close[i] < lower[i] else 1
        else:
            direction[i] = 1 if close[i] > upper[i] else -1
    line = np.where(direction > 0, lower, upper)
    return pd.Series(line, index=df.index), pd.Series(direction, index=df.index)


def supertrend(df, p):
    line, d = supertrend_line(df, p["n"], p["mult"])
    flip_up, flip_down = (d > 0) & (d.shift(1) < 0), (d < 0) & (d.shift(1) > 0)
    stop = np.maximum((df["close"] - line).abs(), 0.5 * df["atr_14"])
    return signals(df, long=flip_up, short=flip_down, stop=stop)


# ---------------------------------------------------------------- 12. MACD with zero-line filter
def macd(df, p):
    line = ema(df["close"], p["fast"]) - ema(df["close"], p["slow"])
    sig = ema(line, p["signal"])
    up, down = cross_up(line, sig), cross_down(line, sig)
    return signals(df, long=up & (line > 0), short=down & (line < 0), stop=p["stop_atr"] * df["atr_14"], exit_long=down, exit_short=up)


# ---------------------------------------------------------------- 13. Ichimoku cloud
def ichimoku(df, p):
    def mid(n):
        return (df["high"].rolling(n, min_periods=n).max() + df["low"].rolling(n, min_periods=n).min()) / 2
    tenkan, kijun = mid(9), mid(26)
    span_a, span_b = ((tenkan + kijun) / 2).shift(26), mid(52).shift(26)
    top, bottom = np.maximum(span_a, span_b), np.minimum(span_a, span_b)
    bull = (df["close"] > top) & (tenkan > kijun)
    bear = (df["close"] < bottom) & (tenkan < kijun)
    return signals(df, long=bull & ~bull.shift(1, fill_value=False), short=bear & ~bear.shift(1, fill_value=False),
                   stop=p["stop_atr"] * df["atr_14"], exit_long=df["close"] < kijun, exit_short=df["close"] > kijun)


# ---------------------------------------------------------------- 14. 52-week high
def high_52w(df, p):
    hi = df["close"].shift(1).rolling(p["lookback"], min_periods=p["lookback"]).max()
    return signals(df, long=cross_up(df["close"], hi), stop=p["stop_atr"] * df["atr_14"], exit_long=df["close"] < sma(df["close"], p["exit_sma"]))


# ---------------------------------------------------------------- 15. NR7
def nr7(df, p):
    rng = df["high"] - df["low"]
    narrow = rng == rng.rolling(p["n"], min_periods=p["n"]).min()
    was = narrow.shift(1, fill_value=False)
    hi, lo = df["high"].shift(1), df["low"].shift(1)
    long = was & (df["close"] > hi)
    short = was & (df["close"] < lo)
    stop = np.where(long, df["close"] - lo, hi - df["close"])
    return signals(df, long=long, short=short, stop=pd.Series(stop, index=df.index), hold=p["hold"])


# ---------------------------------------------------------------- 16. Inside bar
def inside_bar(df, p):
    inside = (df["high"] < df["high"].shift(1)) & (df["low"] > df["low"].shift(1))
    was = inside.shift(1, fill_value=False)
    mother_hi, mother_lo = df["high"].shift(2), df["low"].shift(2)
    long, short = was & (df["close"] > mother_hi), was & (df["close"] < mother_lo)
    rng = mother_hi - mother_lo
    stop = pd.Series(np.where(long, df["close"] - mother_lo, mother_hi - df["close"]), index=df.index)
    return signals(df, long=long, short=short, stop=stop, target=p["target_range"] * rng, hold=p["hold"])


# ---------------------------------------------------------------- 17. Dual Thrust
def ny_trading_day(ts: pd.Series) -> pd.Series:
    """The COMEX trading day: a session opening at 18:00 New York belongs to the next calendar date."""
    return (ts.dt.tz_convert(NEW_YORK) + pd.Timedelta(hours=6)).dt.date


def dual_thrust(df, p):
    day = ny_trading_day(df["ts"])
    daily = df.groupby(day).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
    n = p["days"]
    hh, ll = daily["high"].rolling(n).max().shift(1), daily["low"].rolling(n).min().shift(1)
    hc, lc = daily["close"].rolling(n).max().shift(1), daily["close"].rolling(n).min().shift(1)
    rng = np.maximum(hh - lc, hc - ll)
    day_open = df.groupby(day)["open"].transform("first")
    rng_bar = day.map(rng).astype("float64")
    clock = local_clock(df["ts"], NEW_YORK)
    entry_hours = ~((clock["minute"] >= p["last_entry_ny"] * 60) & (clock["minute"] < 18 * 60))
    flat_time = (clock["minute"] >= p["flat_ny"] * 60) & (clock["minute"] < 18 * 60)
    up = df["close"] > day_open + p["k"] * rng_bar
    down = df["close"] < day_open - p["k"] * rng_bar
    first_up = up & (up.astype(int).groupby(day).cumsum() == 1)
    first_down = down & (down.astype(int).groupby(day).cumsum() == 1)
    return signals(df, long=first_up & entry_hours, short=first_down & entry_hours, stop=p["k"] * rng_bar,
                   exit_long=flat_time, exit_short=flat_time)


# ---------------------------------------------------------------- 18. Chandelier exit
def chandelier(df, p):
    hh = df["high"].rolling(p["trail_n"], min_periods=p["trail_n"]).max()
    ll = df["low"].rolling(p["trail_n"], min_periods=p["trail_n"]).min()
    return signals(df, long=df["close"] > prior_high(df, p["entry_n"]), short=df["close"] < prior_low(df, p["entry_n"]),
                   stop=p["stop_atr"] * df["atr_14"],
                   exit_long=df["close"] < hh - p["trail_atr"] * df["atr_14"], exit_short=df["close"] > ll + p["trail_atr"] * df["atr_14"])


# ---------------------------------------------------------------- 19. EMA pullback in an aligned trend
def ema_pullback(df, p):
    up = (df["ema_20"] > df["ema_50"]) & (df["ema_50"] > df["sma_200"])
    down = (df["ema_20"] < df["ema_50"]) & (df["ema_50"] < df["sma_200"])
    long = up & (df["low"] <= df["ema_20"]) & (df["close"] > df["ema_20"]) & (df["close"] > df["open"])
    short = down & (df["high"] >= df["ema_20"]) & (df["close"] < df["ema_20"]) & (df["close"] < df["open"])
    return signals(df, long=long, short=short, stop=p["stop_atr"] * df["atr_14"], target=p["target_atr"] * df["atr_14"], hold=p["hold"])


# ---------------------------------------------------------------- 20. Prior-week range breakout
def weekly_breakout(df, p):
    week = df["ts"].dt.tz_convert("UTC").dt.to_period("W-SUN")
    stats = df.groupby(week).agg(high=("high", "max"), low=("low", "min"))
    prev_hi, prev_lo = week.map(stats["high"].shift(1)).astype("float64"), week.map(stats["low"].shift(1)).astype("float64")
    up, down = df["close"] > prev_hi, df["close"] < prev_lo
    first_up = up & (up.astype(int).groupby(week).cumsum() == 1)
    first_down = down & (down.astype(int).groupby(week).cumsum() == 1)
    return signals(df, long=first_up, short=first_down, stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])


ATR = "the project's Wilder ATR(14) on the signal timeframe"
OHLC = ["XAUUSD bars (mid prices)"]

ENTRIES = [
    entry("cat_turtle_s1_d1", "Turtle System 1 (20/10-day Donchian)", "d1", dict(entry_n=20, exit_n=10, stop_atr=2.0), donchian(20, 10, 2.0),
          "Daily Donchian breakout in the original Turtle System 1 form, without pyramiding or the skip-after-a-winner filter.",
          family=FAMILY, batch=B, source="Curtis Faith, 'Way of the Turtle' (2007)",
          logic="Buy when gold makes a new 20-day high and sell when it makes a new 20-day low; get out on a 10-day break the other way.",
          entry_rule="Long when the daily close is above the highest high of the prior 20 days; short when below the lowest low of the prior 20 days.",
          exit_rule="Exit long when the close is below the prior 10-day low; exit short when above the prior 10-day high; an opposite entry also reverses.",
          stop_rule=f"2 x {ATR} from the entry close.", data=OHLC,
          limitations=["Entries are at the confirming close, not at an intraday stop order as in the original."]),
    entry("cat_turtle_s2_d1", "Turtle System 2 (55/20-day Donchian)", "d1", dict(entry_n=55, exit_n=20, stop_atr=2.0), donchian(55, 20, 2.0),
          "Daily 55-day Donchian breakout with a 20-day exit, the Turtles' slower system.",
          family=FAMILY, batch=B, source="Curtis Faith, 'Way of the Turtle' (2007)",
          logic="Buy a new 55-day high and sell a new 55-day low; exit on a 20-day break the other way.",
          entry_rule="Long when the daily close is above the prior 55-day high; short when below the prior 55-day low.",
          exit_rule="Exit long below the prior 20-day low; exit short above the prior 20-day high; an opposite entry reverses.",
          stop_rule=f"2 x {ATR}.", data=OHLC),
    entry("cat_donchian20_h4", "Donchian 20/10 on H4", "h4", dict(entry_n=20, exit_n=10, stop_atr=2.0), donchian(20, 10, 2.0),
          "The Turtle System 1 rules applied to four-hour bars.",
          family=FAMILY, batch=B, source="Richard Donchian's channel rule, as popularised by the Turtles; H4 application is common in retail gold systems",
          logic="The 20/10 channel breakout, faster, on four-hour bars.",
          entry_rule="Long when the H4 close is above the prior 20-bar high; short when below the prior 20-bar low.",
          exit_rule="Exit long below the prior 10-bar low; exit short above the prior 10-bar high; opposite entry reverses.",
          stop_rule=f"2 x {ATR}.", data=OHLC),
    entry("cat_golden_cross_d1", "Golden/death cross (50/200-day SMA)", "d1", dict(kind="sma", fast=50, slow=200, stop_atr=4.0), ma_cross,
          "Trades the 50/200-day simple-average cross, holding until the opposite cross.",
          family=FAMILY, batch=B, source="Classic chart rule; e.g. Brock, Lakonishok and LeBaron, Journal of Finance 47(5), 1992",
          logic="Hold gold long after the 50-day average crosses above the 200-day, and short after it crosses below.",
          entry_rule="Long on the day the 50-day SMA crosses above the 200-day SMA; short on the day it crosses below.",
          exit_rule="The opposite cross reverses the position; no other exit.",
          stop_rule=f"4 x {ATR}, a catastrophic stop only (the classic rule has none); after a stop-out it waits for the next cross.", data=OHLC),
    entry("cat_ema_cross_h1", "EMA 20/50 cross on H1", "h1", dict(kind="ema", fast=20, slow=50, stop_atr=3.0), ma_cross,
          "Always-in-the-market style 20/50 EMA crossover on hourly bars.",
          family=FAMILY, batch=B, source="Common retail moving-average crossover system", logic="Follow the hourly 20/50 EMA cross.",
          entry_rule="Long when EMA(20) crosses above EMA(50); short when it crosses below.", exit_rule="Opposite cross reverses.",
          stop_rule=f"3 x {ATR}.", data=OHLC),
    entry("cat_faber_10m_mn1", "Faber 10-month SMA timing", "mn1", dict(months=10, stop_atr=4.0), faber,
          "Long gold when the month closes above its 10-month average, flat otherwise.",
          family=FAMILY, batch=B, source="Mebane Faber, 'A Quantitative Approach to Tactical Asset Allocation' (2007), https://papers.ssrn.com/sol3/papers.cfm?abstract_id=962461",
          logic="Own gold only while its monthly close is above the 10-month moving average.",
          entry_rule="Long at any monthly close above the 10-month SMA of monthly closes (so it re-enters the month after a stop-out if still above).",
          exit_rule="Exit when a monthly close is below the 10-month SMA. Long only.",
          stop_rule=f"4 x the monthly ATR(14), catastrophic only.", data=OHLC,
          limitations=["Two or three trades a year: walk-forward folds may have too few trades to evaluate, which reports as insufficient evidence."]),
    entry("cat_tsmom_12m_d1", "Time-series momentum (12-month, monthly rebalance)", "d1", dict(lookback=252, stop_atr=4.0), tsmom,
          "Sign of the trailing 12-month return, re-checked on the first trading day of each month.",
          family=FAMILY, batch=B, source="Moskowitz, Ooi and Pedersen, 'Time Series Momentum', Journal of Financial Economics 104(2), 2012, https://doi.org/10.1016/j.jfineco.2011.11.003",
          logic="Be long gold if it is up over the past year and short if it is down, checking once a month.",
          entry_rule="On the first trading day of each month: long if close / close 252 days ago - 1 > 0, short if < 0.",
          exit_rule="Reverses on a later monthly check with the opposite sign; otherwise holds.",
          stop_rule=f"4 x {ATR}, catastrophic; re-entry only at the next monthly check.", data=OHLC,
          limitations=["The paper volatility-scales positions; here sizing is the engine's fixed-risk rule."]),
    entry("cat_keltner_breakout_h1", "Keltner channel breakout (EMA20 +/- 2 ATR)", "h1", dict(n=20, k=2.0, stop_atr=2.0), keltner_breakout,
          "Follows closes outside a 2-ATR Keltner channel back to its midline.",
          family=FAMILY, batch=B, source="Chester Keltner (1960); ATR form popularised by Linda Bradford Raschke",
          logic="A close outside the ATR channel starts a trend trade that lasts until price returns to the average.",
          entry_rule="Long when the close crosses above EMA(20) + 2 x ATR(14); short when it crosses below EMA(20) - 2 x ATR(14).",
          exit_rule="Exit long when the close is below EMA(20); exit short when above EMA(20).", stop_rule=f"2 x {ATR}.", data=OHLC),
    entry("cat_bollinger_breakout_h4", "Bollinger band breakout on H4", "h4", dict(stop_atr=2.0), bollinger_breakout,
          "Follows a close outside the 20-bar, 2-standard-deviation band until the midline.",
          family=FAMILY, batch=B, source="John Bollinger, 'Bollinger on Bollinger Bands' (2001)",
          logic="A close outside the bands is treated as the start of a move.",
          entry_rule="Long when the H4 close crosses above the upper band (SMA20 + 2 SD); short when it crosses below the lower band.",
          exit_rule="Exit long when the close is below the 20-bar SMA; exit short when above it.", stop_rule=f"2 x {ATR}.", data=OHLC),
    entry("cat_di_cross_h1", "Wilder DI cross with ADX > 25", "h1", dict(adx_min=25.0, stop_atr=2.0), di_cross,
          "Directional-movement cross, taken only when ADX shows a trend.",
          family=FAMILY, batch=B, source="J. Welles Wilder, 'New Concepts in Technical Trading Systems' (1978)",
          logic="When +DI crosses above -DI in a trending market, buy; the reverse, sell.",
          entry_rule="Long when +DI(14) crosses above -DI(14) and ADX(14) > 25; short when -DI crosses above +DI and ADX > 25.",
          exit_rule="Exit on the opposite DI cross (regardless of ADX).", stop_rule=f"2 x {ATR}.", data=OHLC),
    entry("cat_supertrend_h1", "Supertrend (10, 3)", "h1", dict(n=10, mult=3.0), supertrend,
          "Trades the flips of a 10-bar, 3-ATR Supertrend line.",
          family=FAMILY, batch=B, source="Olivier Seban's Supertrend indicator; standard definition as in most charting packages",
          logic="Follow the Supertrend's direction, reversing when it flips.",
          entry_rule="Long on the bar the Supertrend (ATR 10, multiplier 3, on (high+low)/2) flips up; short when it flips down.",
          exit_rule="The opposite flip reverses.", stop_rule="Distance from the entry close to the Supertrend line, at least 0.5 x ATR(14).", data=OHLC),
    entry("cat_macd_zero_h4", "MACD signal cross with zero-line filter", "h4", dict(fast=12, slow=26, signal=9, stop_atr=2.5), macd,
          "MACD crossing its signal line on the trend's side of zero.",
          family=FAMILY, batch=B, source="Gerald Appel, 'Technical Analysis: Power Tools for Active Investors' (2005)",
          logic="Buy MACD bullish crosses only when MACD is above zero, and sell bearish crosses only below zero.",
          entry_rule="Long when MACD(12,26) crosses above its 9-EMA signal while MACD > 0; short when it crosses below while MACD < 0.",
          exit_rule="Exit long on any bearish signal cross; exit short on any bullish cross.", stop_rule=f"2.5 x {ATR}.", data=OHLC),
    entry("cat_ichimoku_h4", "Ichimoku cloud break with TK alignment", "h4", dict(stop_atr=2.5), ichimoku,
          "Enters when price clears the cloud with Tenkan above Kijun; exits on a close through Kijun.",
          family=FAMILY, batch=B, source="Goichi Hosoda's Ichimoku Kinko Hyo; standard 9/26/52 settings",
          logic="Price above the cloud with the fast line above the slow line is an uptrend; the reverse a downtrend.",
          entry_rule="Long on the first bar where the close is above both cloud spans (each shifted 26 bars) and Tenkan(9) > Kijun(26); short mirrored.",
          exit_rule="Exit long when the close is below Kijun; exit short when above.", stop_rule=f"2.5 x {ATR}.", data=OHLC),
    entry("cat_52w_high_d1", "52-week closing-high breakout", "d1", dict(lookback=252, exit_sma=50, stop_atr=3.0), high_52w,
          "Buys a new 52-week closing high and holds above the 50-day average.",
          family=FAMILY, batch=B, source="Adapted from George and Hwang, 'The 52-Week High and Momentum Investing', Journal of Finance 59(5), 2004 (stocks)",
          logic="New yearly highs tend to keep going, so buy them and hold until the trend weakens.",
          entry_rule="Long when the daily close crosses above the highest close of the prior 252 days. Long only.",
          exit_rule="Exit when the close is below the 50-day SMA.", stop_rule=f"3 x {ATR}.", data=OHLC),
    entry("cat_nr7_d1", "NR7 breakout", "d1", dict(n=7, hold=5), nr7,
          "After the narrowest-range day of seven, follows the next day's break of that day's range.",
          family=FAMILY, batch=B, source="Toby Crabel, 'Day Trading with Short Term Price Patterns and Opening Range Breakout' (1990)",
          logic="Very narrow days come before expansion; trade the direction of the break.",
          entry_rule="If yesterday's high-low range was the smallest of the last 7 days: long when today's close is above yesterday's high, short when below yesterday's low.",
          exit_rule="Exit after 5 days.", stop_rule="The far side of the NR7 day's range (its low for a long, its high for a short).", data=OHLC),
    entry("cat_inside_bar_h4", "Inside-bar breakout on H4", "h4", dict(target_range=1.0, hold=12), inside_bar,
          "Follows the break of the mother bar after an inside bar.",
          family=FAMILY, batch=B, source="Common price-action pattern, e.g. Al Brooks, 'Trading Price Action Trends' (2011)",
          logic="A bar inside the previous bar is a pause; the break of the bigger bar shows direction.",
          entry_rule="If the previous bar was inside the bar before it: long when the close is above the mother bar's high, short when below its low.",
          exit_rule="Target 1 x the mother bar's range from entry; otherwise exit after 12 bars.",
          stop_rule="The mother bar's opposite extreme.", data=OHLC),
    entry("cat_dual_thrust_h1", "Dual Thrust (4-day range, k = 0.5)", "h1", dict(days=4, k=0.5, last_entry_ny=15, flat_ny=16), dual_thrust,
          "Intraday breakout from the day's open by half of the 4-day range, flat by the New York afternoon.",
          family=FAMILY, batch=B, source="Michael Chalek's Dual Thrust system, widely published in futures-system literature",
          logic="Buy when price moves half a recent range above the day's open, sell the mirror, and close everything late in the day.",
          entry_rule="Range = max(HH - LC, HC - LL) over the prior 4 COMEX days. Long on the first H1 close above day open + 0.5 x Range; short on the first close below open - 0.5 x Range; no new entries from 15:00 New York.",
          exit_rule="Flat at the first bar from 16:00 New York; an opposite entry reverses.", stop_rule="0.5 x Range (back to the day's open).", data=OHLC),
    entry("cat_chandelier_h4", "20-bar breakout with chandelier exit", "h4", dict(entry_n=20, trail_n=22, trail_atr=3.0, stop_atr=3.0), chandelier,
          "Channel-breakout entries managed by a 3-ATR chandelier trail.",
          family=FAMILY, batch=B, source="Chuck LeBeau's chandelier exit, as described in Alexander Elder, 'Come into My Trading Room' (2002)",
          logic="Enter breakouts and let a trailing stop hung from the recent high decide the exit.",
          entry_rule="Long when the H4 close is above the prior 20-bar high; short when below the prior 20-bar low.",
          exit_rule="Exit long when the close is below the highest high of the last 22 bars - 3 x ATR(14); short mirrored.",
          stop_rule=f"3 x {ATR}.", data=OHLC),
    entry("cat_ema_pullback_h1", "Pullback to EMA20 in an aligned trend", "h1", dict(stop_atr=1.5, target_atr=3.0, hold=48), ema_pullback,
          "Buys dips to the 20-EMA when the 20/50/200 averages are stacked, sells rallies in the mirror.",
          family=FAMILY, batch=B, source="Common trend-pullback rule; own exact parameters",
          logic="In a clean uptrend, a touch of the 20-EMA that closes back above it is a chance to join the trend.",
          entry_rule="Long when EMA20 > EMA50 > SMA200, the bar's low touches EMA20, and the bar closes above EMA20 and above its open; short mirrored.",
          exit_rule="Target 3 x ATR(14); otherwise exit after 48 bars.", stop_rule=f"1.5 x {ATR}.", data=OHLC),
    entry("cat_weekly_breakout_h4", "Prior-week high/low breakout", "h4", dict(stop_atr=2.0, hold=30), weekly_breakout,
          "Follows the first H4 close beyond the previous week's high or low.",
          family=FAMILY, batch=B, source="Own idea, a weekly variant of the channel breakout",
          logic="Breaking last week's range shows a new weekly trend.",
          entry_rule="Long on the first H4 close of the week above the previous calendar week's (UTC) high; short on the first close below its low.",
          exit_rule="Exit after 30 H4 bars (about a week); an opposite entry reverses.", stop_rule=f"2 x {ATR}.", data=OHLC),
]
