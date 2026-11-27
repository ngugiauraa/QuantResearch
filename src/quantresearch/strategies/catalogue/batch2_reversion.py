"""Catalogue batch 2: mean reversion and volatility (20 strategies). Parameters are fixed as documented."""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantresearch.strategies.catalogue.batch1_trend import ny_trading_day
from quantresearch.strategies.catalogue.common import cross_down, cross_up, ema, entry, prior_high, prior_low, rsi, signals, sma

B = 2
REV, VOL = "mean_reversion", "volatility"
ATR = "the project's Wilder ATR(14) on the signal timeframe"
OHLC = ["XAUUSD bars (mid prices)"]


# ---------------------------------------------------------------- Connors-style short-term reversion
def rsi2(df, p):
    r = rsi(df["close"], 2)
    trend = sma(df["close"], 200)
    fast = sma(df["close"], 5)
    return signals(df, long=(df["close"] > trend) & (r < p["low"]), short=(df["close"] < trend) & (r > p["high"]),
                   stop=p["stop_atr"] * df["atr_14"], hold=p["hold"], exit_long=df["close"] > fast, exit_short=df["close"] < fast)


def three_down(df, p):
    c = df["close"]
    down3 = (c < c.shift(1)) & (c.shift(1) < c.shift(2)) & (c.shift(2) < c.shift(3))
    up3 = (c > c.shift(1)) & (c.shift(1) > c.shift(2)) & (c.shift(2) > c.shift(3))
    trend, fast = sma(c, 200), sma(c, 5)
    return signals(df, long=down3 & (c > trend), short=up3 & (c < trend), stop=p["stop_atr"] * df["atr_14"], hold=p["hold"],
                   exit_long=c > fast, exit_short=c < fast)


def double_seven(df, p):
    c = df["close"]
    low7, high7 = c.rolling(7, min_periods=7).min(), c.rolling(7, min_periods=7).max()
    trend = sma(c, 200)
    return signals(df, long=(c <= low7) & (c > trend), short=(c >= high7) & (c < trend), stop=p["stop_atr"] * df["atr_14"], hold=p["hold"],
                   exit_long=c >= high7, exit_short=c <= low7)


def ibs(df, p):
    rng = (df["high"] - df["low"]).where(lambda r: r > 0)
    strength = (df["close"] - df["low"]) / rng
    return signals(df, long=strength < p["low"], short=strength > p["high"], stop=p["stop_atr"] * df["atr_14"], hold=1)


# ---------------------------------------------------------------- Band fades
def bb_fade(df, p):
    quiet = df["adx"] < p["adx_max"]
    return signals(df, long=quiet & (df["close"] < df["bb_lower"]), short=quiet & (df["close"] > df["bb_upper"]),
                   stop=p["stop_atr"] * df["atr_14"], hold=p["hold"], exit_long=df["close"] > df["bb_mid"], exit_short=df["close"] < df["bb_mid"])


def zscore_fade(df, p):
    z = df["zscore_60"]
    mid = sma(df["close"], 60)
    return signals(df, long=cross_down(z, -p["z"]), short=cross_up(z, p["z"]), stop=p["stop_atr"] * df["atr_14"], hold=p["hold"],
                   exit_long=df["close"] > mid, exit_short=df["close"] < mid)


def keltner_fade(df, p):
    mid = ema(df["close"], 20)
    upper, lower = mid + p["k"] * df["atr_14"], mid - p["k"] * df["atr_14"]
    quiet = df["adx"] < p["adx_max"]
    return signals(df, long=quiet & cross_down(df["close"], lower), short=quiet & cross_up(df["close"], upper), stop=p["stop_atr"] * df["atr_14"],
                   hold=p["hold"], exit_long=df["close"] > mid, exit_short=df["close"] < mid)


def rsi_pullback(df, p):
    r = df["rsi_14"]
    trend = df["sma_200"]
    return signals(df, long=(df["close"] > trend) & cross_down(r, p["low"]), short=(df["close"] < trend) & cross_up(r, p["high"]),
                   stop=p["stop_atr"] * df["atr_14"], hold=p["hold"], exit_long=r > p["exit_long"], exit_short=r < p["exit_short"])


def quiet_fade(df, p):
    quiet = df["atr_ratio"] < p["atr_ratio_max"]
    z = df["zscore_20"]
    return signals(df, long=quiet & (z < -p["z"]), short=quiet & (z > p["z"]), stop=p["stop_atr"] * df["atr_14"], hold=p["hold"],
                   exit_long=z > 0, exit_short=z < 0)


# ---------------------------------------------------------------- False breakouts (Raschke)
def turtle_soup(df, p):
    n = p["n"]
    low_n, high_n = prior_low(df, n), prior_high(df, n)
    # The previous n-bar low must be at least `age` bars old, as in the original rule.
    low_age = df["low"].shift(1).rolling(n, min_periods=n).apply(lambda w: n - 1 - int(np.argmin(w)), raw=True)
    high_age = df["high"].shift(1).rolling(n, min_periods=n).apply(lambda w: n - 1 - int(np.argmax(w)), raw=True)
    long = (df["low"] < low_n) & (df["close"] > low_n) & (low_age >= p["age"])
    short = (df["high"] > high_n) & (df["close"] < high_n) & (high_age >= p["age"])
    stop = pd.Series(np.where(long, df["close"] - df["low"], df["high"] - df["close"]), index=df.index)
    stop = np.maximum(stop, 0.25 * df["atr_14"])
    return signals(df, long=long, short=short, stop=stop, target=p["target_r"] * stop, hold=p["hold"])


def eighty_twenty(df, p):
    rng = (df["high"] - df["low"]).where(lambda r: r > 0)
    open_pos, close_pos = (df["open"] - df["low"]) / rng, (df["close"] - df["low"]) / rng
    bear_day = ((open_pos > 0.8) & (close_pos < 0.2)).shift(1, fill_value=False)
    bull_day = ((open_pos < 0.2) & (close_pos > 0.8)).shift(1, fill_value=False)
    prev_lo, prev_hi = df["low"].shift(1), df["high"].shift(1)
    long = bear_day & (df["low"] < prev_lo) & (df["close"] > prev_lo)
    short = bull_day & (df["high"] > prev_hi) & (df["close"] < prev_hi)
    stop = np.maximum(pd.Series(np.where(long, df["close"] - df["low"], df["high"] - df["close"]), index=df.index), 0.25 * df["atr_14"])
    return signals(df, long=long, short=short, stop=stop, hold=p["hold"])


def pdh_pdl_fade(df, p):
    long = (df["low"] < df["pdl"]) & (df["close"] > df["pdl"])
    short = (df["high"] > df["pdh"]) & (df["close"] < df["pdh"])
    day = ny_trading_day(df["ts"])
    first_long = long & (long.astype(int).groupby(day).cumsum() == 1)
    first_short = short & (short.astype(int).groupby(day).cumsum() == 1)
    mid = (df["pdh"] + df["pdl"]) / 2
    stop = np.maximum(pd.Series(np.where(first_long, df["close"] - df["low"], df["high"] - df["close"]), index=df.index), 0.3 * df["atr_14"])
    return signals(df, long=first_long, short=first_short, stop=stop, target=(df["close"] - mid).abs(), hold=p["hold"])


def weekend_gap_fade(df, p):
    gap_hours = df["ts"].diff().dt.total_seconds() / 3600
    after_weekend = (gap_hours > 24) & (df["ts"].dt.tz_convert("America/New_York").dt.weekday == 6)
    friday_close = df["close"].shift(1)
    gap = df["open"] - friday_close
    big = after_weekend & (gap.abs() > p["gap_atr"] * df["atr_14"].shift(1))
    long = big & (gap < 0) & (df["close"] < friday_close)
    short = big & (gap > 0) & (df["close"] > friday_close)
    return signals(df, long=long, short=short, stop=gap.abs(), target=(df["close"] - friday_close).abs(), hold=p["hold"])


def vwap_fade(df, p):
    day = ny_trading_day(df["ts"])
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"].clip(lower=0).replace(0, np.nan).fillna(1e-9)
    cum_pv, cum_v = (typical * vol).groupby(day).cumsum(), vol.groupby(day).cumsum()
    vwap = cum_pv / cum_v
    dev = (typical - vwap)
    sd = np.sqrt((dev.pow(2) * vol).groupby(day).cumsum() / cum_v)
    bars_in_day = day.groupby(day).cumcount()
    ready = bars_in_day >= p["min_bars"]
    long = ready & (df["close"] < vwap - p["k"] * sd)
    short = ready & (df["close"] > vwap + p["k"] * sd)
    return signals(df, long=long & ~long.shift(1, fill_value=False), short=short & ~short.shift(1, fill_value=False),
                   stop=p["stop_atr"] * df["atr_14"], hold=p["hold"], exit_long=df["close"] > vwap, exit_short=df["close"] < vwap)


# ---------------------------------------------------------------- Volatility
def bb_squeeze(df, p):
    width = df["bb_width"]
    squeezed = width <= width.rolling(p["lookback"], min_periods=p["lookback"]).min() * (1 + p["tolerance"])
    recent = squeezed.rolling(p["within"], min_periods=1).max().astype(bool)
    return signals(df, long=recent & cross_up(df["close"], df["bb_upper"]), short=recent & cross_down(df["close"], df["bb_lower"]),
                   stop=p["stop_atr"] * df["atr_14"], exit_long=df["close"] < df["bb_mid"], exit_short=df["close"] > df["bb_mid"])


def ttm_squeeze(df, p):
    mid = sma(df["close"], 20)
    sd = df["close"].rolling(20, min_periods=20).std()
    kc = ema(df["close"], 20)
    on = (mid + 2 * sd < kc + p["kc"] * df["atr_14"]) & (mid - 2 * sd > kc - p["kc"] * df["atr_14"])
    held = on.rolling(p["min_squeeze"], min_periods=p["min_squeeze"]).min().astype(bool)
    fired = held.shift(1, fill_value=False) & ~on
    donch_mid = (df["high"].rolling(20, min_periods=20).max() + df["low"].rolling(20, min_periods=20).min()) / 2
    mom = df["close"] - (donch_mid + mid) / 2
    return signals(df, long=fired & (mom > 0), short=fired & (mom < 0), stop=p["stop_atr"] * df["atr_14"], hold=p["hold"],
                   exit_long=(mom < mom.shift(1)) & (mom.shift(1) < mom.shift(2)), exit_short=(mom > mom.shift(1)) & (mom.shift(1) > mom.shift(2)))


def range_expansion(df, p):
    rng = df["high"] - df["low"]
    big = rng > p["k"] * df["atr_14"].shift(1)
    pos = (df["close"] - df["low"]) / rng.where(rng > 0)
    return signals(df, long=big & (pos > 0.75), short=big & (pos < 0.25), stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])


def nr4_ib(df, p):
    rng = df["high"] - df["low"]
    nr4 = rng == rng.rolling(4, min_periods=4).min()
    inside = (df["high"] < df["high"].shift(1)) & (df["low"] > df["low"].shift(1))
    setup = (nr4 & inside).shift(1, fill_value=False)
    hi, lo = df["high"].shift(1), df["low"].shift(1)
    long, short = setup & (df["close"] > hi), setup & (df["close"] < lo)
    stop = pd.Series(np.where(long, df["close"] - lo, hi - df["close"]), index=df.index)
    return signals(df, long=long, short=short, stop=stop, hold=p["hold"])


def atr_percentile_breakout(df, p):
    pct = df["atr_14"].rolling(p["lookback"], min_periods=p["lookback"] // 2).rank(pct=True)
    calm = pct.shift(1) < p["calm"]
    return signals(df, long=calm & (df["close"] > prior_high(df, p["n"])), short=calm & (df["close"] < prior_low(df, p["n"])),
                   stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])


def vix_spike(df, p):
    if "vix" not in df or df["vix"].notna().sum() == 0:
        return signals(df)
    rel = df["vix_chg5"] / (df["vix"] - df["vix_chg5"]).where(lambda v: v > 0)
    spike = cross_up(rel, p["rise"])
    return signals(df, long=spike, stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])


ENTRIES = [
    entry("cat_rsi2_d1", "Connors RSI(2) reversion, daily", "d1", dict(low=10.0, high=90.0, stop_atr=3.0, hold=10), rsi2,
          "Buys short-term oversold days above the 200-day average and sells overbought days below it.",
          family=REV, batch=B, source="Larry Connors and Cesar Alvarez, 'Short Term Trading Strategies That Work' (2008)",
          logic="In an uptrend, a very oversold two-day RSI tends to bounce; the mirror in a downtrend.",
          entry_rule="Long when close > SMA(200) and RSI(2) < 10; short when close < SMA(200) and RSI(2) > 90.",
          exit_rule="Exit long when the close is above SMA(5); exit short when below SMA(5); at most 10 days.",
          stop_rule=f"3 x {ATR} (the book uses no stop; this one is catastrophic).", data=OHLC),
    entry("cat_rsi2_h1", "Connors RSI(2) reversion, hourly", "h1", dict(low=10.0, high=90.0, stop_atr=2.0, hold=24), rsi2,
          "The RSI(2) rules applied to hourly bars.",
          family=REV, batch=B, source="Connors and Alvarez (2008), applied intraday; own timeframe choice",
          logic="The same oversold-bounce idea on hourly bars.",
          entry_rule="Long when close > SMA(200) and RSI(2) < 10; short when close < SMA(200) and RSI(2) > 90 (hourly bars).",
          exit_rule="Exit long above SMA(5), short below SMA(5); at most 24 bars.", stop_rule=f"2 x {ATR}.", data=OHLC),
    entry("cat_three_down_d1", "Three down days in an uptrend", "d1", dict(stop_atr=3.0, hold=10), three_down,
          "Buys three consecutive lower closes above the 200-day average.",
          family=REV, batch=B, source="Larry Connors, 'How Markets Really Work' (2004)",
          logic="Several losing days in a row inside an uptrend are a pullback to buy.",
          entry_rule="Long after three consecutive lower closes with close > SMA(200); short after three higher closes with close < SMA(200).",
          exit_rule="Exit long above SMA(5), short below SMA(5); at most 10 days.", stop_rule=f"3 x {ATR}.", data=OHLC),
    entry("cat_double_seven_d1", "Double 7s", "d1", dict(stop_atr=3.0, hold=15), double_seven,
          "Buys a 7-day closing low above the 200-day average and sells at a 7-day closing high.",
          family=REV, batch=B, source="Larry Connors and Cesar Alvarez, 'Short Term Trading Strategies That Work' (2008)",
          logic="A one-week low in an uptrend tends to revert to a one-week high.",
          entry_rule="Long when the close is the lowest of the last 7 closes and above SMA(200); short mirrored below SMA(200).",
          exit_rule="Exit long at a 7-day closing high; exit short at a 7-day closing low; at most 15 days.", stop_rule=f"3 x {ATR}.", data=OHLC),
    entry("cat_ibs_d1", "Internal bar strength", "d1", dict(low=0.2, high=0.8, stop_atr=2.0), ibs,
          "Buys days that close near their low and sells days that close near their high, for one day.",
          family=REV, batch=B, source="Pagonidis, 'The IBS Effect: Mean Reversion in Equity ETFs' (NAAIM, 2013)",
          logic="A close at the bottom of the day's range tends to be followed by a better day.",
          entry_rule="IBS = (close - low) / (high - low). Long when IBS < 0.2; short when IBS > 0.8.",
          exit_rule="Exit after one day.", stop_rule=f"2 x {ATR}.", data=OHLC,
          limitations=["Documented on equity ETFs; gold trades almost 24 hours, so the daily close is less of a 'last word'."]),
    entry("cat_bb_fade_h1", "Bollinger fade in low ADX", "h1", dict(adx_max=20.0, stop_atr=1.5, hold=24), bb_fade,
          "Fades closes outside the bands when ADX says the market is ranging.",
          family=REV, batch=B, source="John Bollinger (2001); ranging filter by ADX from Wilder (1978)",
          logic="In a range, a close outside the bands tends to come back to the middle.",
          entry_rule="When ADX(14) < 20: long when the close is below the lower band (SMA20 - 2 SD); short when above the upper band.",
          exit_rule="Exit at the 20-bar SMA; at most 24 bars.", stop_rule=f"1.5 x {ATR}.", data=OHLC),
    entry("cat_zscore60_h4", "60-bar z-score fade on H4", "h4", dict(z=2.0, stop_atr=2.0, hold=30), zscore_fade,
          "Fades two-standard-deviation departures from the 60-bar mean.",
          family=REV, batch=B, source="Generic statistical mean reversion; own parameters",
          logic="Stretched far from its average, gold tends to snap back.",
          entry_rule="Long when the 60-bar z-score crosses below -2; short when it crosses above +2.",
          exit_rule="Exit at the 60-bar mean; at most 30 bars.", stop_rule=f"2 x {ATR}.", data=OHLC),
    entry("cat_keltner_fade_h1", "Keltner fade (2.5 ATR) in low ADX", "h1", dict(k=2.5, adx_max=20.0, stop_atr=1.5, hold=24), keltner_fade,
          "Fades closes beyond a 2.5-ATR Keltner channel in a ranging market.",
          family=REV, batch=B, source="Linda Bradford Raschke's Keltner usage; own fade parameters",
          logic="Outside a wide ATR channel in a quiet market is too far; trade back to the average.",
          entry_rule="When ADX(14) < 20: long when the close crosses below EMA(20) - 2.5 x ATR; short when it crosses above EMA(20) + 2.5 x ATR.",
          exit_rule="Exit at EMA(20); at most 24 bars.", stop_rule=f"1.5 x {ATR}.", data=OHLC),
    entry("cat_rsi_pullback_h4", "RSI(14) pullback with the 200-bar trend", "h4", dict(low=35.0, high=65.0, exit_long=55.0, exit_short=45.0, stop_atr=2.0, hold=30), rsi_pullback,
          "Buys RSI dips below 35 above the 200-bar average, sells pops above 65 below it.",
          family=REV, batch=B, source="Common trend-pullback rule (e.g. Andrew Cardwell's RSI ranges); own parameters",
          logic="In an uptrend RSI rarely stays below 35, so a dip there is a pullback to buy.",
          entry_rule="Long when close > SMA(200) and RSI(14) crosses below 35; short when close < SMA(200) and RSI(14) crosses above 65.",
          exit_rule="Exit long when RSI > 55, short when RSI < 45; at most 30 bars.", stop_rule=f"2 x {ATR}.", data=OHLC),
    entry("cat_quiet_fade_h1", "Fade stretches when volatility is contracting", "h1", dict(atr_ratio_max=0.8, z=2.0, stop_atr=1.5, hold=24), quiet_fade,
          "Fades 20-bar z-score extremes only while short-term ATR is below long-term ATR.",
          family=REV, batch=B, source="Own idea", logic="When the market is calming down, extremes are more likely noise than a new trend.",
          entry_rule="When ATR(14)/ATR(50) < 0.8: long when the 20-bar z-score < -2; short when > +2.",
          exit_rule="Exit when the z-score crosses zero; at most 24 bars.", stop_rule=f"1.5 x {ATR}.", data=OHLC),
    entry("cat_turtle_soup_d1", "Turtle Soup (failed 20-day breakout)", "d1", dict(n=20, age=4, target_r=1.5, hold=5), turtle_soup,
          "Fades a new 20-day low or high that closes back inside the channel.",
          family=REV, batch=B, source="Linda Bradford Raschke and Laurence Connors, 'Street Smarts' (1995)",
          logic="Breakout traders get trapped when a new 20-day extreme fails the same day.",
          entry_rule="Long when the low breaks the prior 20-day low (made at least 4 days earlier) and the close is back above it; short mirrored.",
          exit_rule="Target 1.5 x the stop distance; otherwise exit after 5 days.",
          stop_rule="Just beyond the day's extreme: close - low for a long (at least 0.25 ATR).", data=OHLC),
    entry("cat_80_20_d1", "80-20 reversal", "d1", dict(hold=3), eighty_twenty,
          "After a day that opened at one extreme and closed at the other, fades the next day's poke beyond it.",
          family=REV, batch=B, source="Linda Bradford Raschke and Laurence Connors, 'Street Smarts' (1995)",
          logic="A day that runs from top to bottom often sees a final flush, then a reversal the next day.",
          entry_rule="If yesterday opened in its top 20% and closed in its bottom 20%: long today when the low breaks yesterday's low and the close is back above it. Short mirrored.",
          exit_rule="Exit after 3 days.", stop_rule="Today's extreme (at least 0.25 ATR).", data=OHLC),
    entry("cat_pdh_pdl_fade_h1", "Failed break of the previous day's high/low", "h1", dict(hold=12), pdh_pdl_fade,
          "Fades an hourly bar that pokes beyond yesterday's high or low and closes back inside.",
          family=REV, batch=B, source="Common intraday level rule; own parameters",
          logic="Stops sit beyond yesterday's extremes; once taken, price often returns into the range.",
          entry_rule="First time each COMEX day: long when an H1 bar's low is below yesterday's low and it closes above it; short mirrored at yesterday's high.",
          exit_rule="Target the middle of yesterday's range; otherwise exit after 12 bars.",
          stop_rule="The bar's extreme (at least 0.3 ATR).", data=OHLC),
    entry("cat_weekend_gap_fade_h1", "Weekend gap fade", "h1", dict(gap_atr=0.5, hold=24), weekend_gap_fade,
          "Fades a Sunday-open gap of more than half an ATR back to Friday's close.",
          family=REV, batch=B, source="Common gap-fill belief; see also the project's overreaction engine",
          logic="Weekend gaps in gold often fill as the week's trading starts.",
          entry_rule="On the first H1 bar after the weekend: if open - Friday close exceeds 0.5 x ATR and the bar has not closed back through Friday's close, fade the gap.",
          exit_rule="Target Friday's close; otherwise exit after 24 bars.", stop_rule="The size of the gap.", data=OHLC,
          limitations=["Overlaps the overreaction engine's gap fade; it is counted as a separate trial in the deflated Sharpe."]),
    entry("cat_vwap_fade_m15", "Session VWAP 2-SD fade", "m15", dict(k=2.0, min_bars=8, stop_atr=1.5, hold=16), vwap_fade,
          "Fades closes more than two volume-weighted deviations from the COMEX day's VWAP.",
          family=REV, batch=B, source="Common intraday VWAP-band reversion; own parameters",
          logic="Far from the day's volume-weighted average, price tends to come back to it.",
          entry_rule="From the 8th M15 bar of the COMEX day: long on the first close below VWAP - 2 x the volume-weighted SD; short above VWAP + 2 SD.",
          exit_rule="Exit at VWAP; at most 16 bars.", stop_rule=f"1.5 x {ATR}.", data=["XAUUSD bars", "provider tick volume as the volume weight"],
          limitations=["Volume is spot-FX tick activity, not exchange volume, so this VWAP is an approximation."]),
    entry("cat_bb_squeeze_h4", "Bollinger squeeze breakout", "h4", dict(lookback=125, tolerance=0.05, within=5, stop_atr=2.0), bb_squeeze,
          "After band width reaches a six-month low, follows the next close outside the bands.",
          family=VOL, batch=B, source="John Bollinger, 'Bollinger on Bollinger Bands' (2001), the Squeeze",
          logic="Very narrow bands come before big moves; trade the direction of the break.",
          entry_rule="If band width was within 5% of its 125-bar low at any of the last 5 bars: long when the close crosses above the upper band, short below the lower band.",
          exit_rule="Exit long below the 20-bar SMA, short above it.", stop_rule=f"2 x {ATR}.", data=OHLC),
    entry("cat_ttm_squeeze_h1", "TTM Squeeze release", "h1", dict(kc=1.5, min_squeeze=6, stop_atr=2.0, hold=24), ttm_squeeze,
          "When Bollinger bands leave the Keltner channel after a squeeze, trades the momentum direction.",
          family=VOL, batch=B, source="John Carter, 'Mastering the Trade' (2005)",
          logic="Bands inside the Keltner channel mean stored energy; the first bar out shows the release direction.",
          entry_rule="After at least 6 bars with BB(20, 2) inside KC(EMA20 +/- 1.5 ATR), on the first bar outside: long if close - mean(Donchian mid, SMA20) > 0, short if < 0.",
          exit_rule="Exit when that momentum falls two bars in a row (rises for shorts); at most 24 bars.", stop_rule=f"2 x {ATR}.", data=OHLC),
    entry("cat_range_expansion_h1", "Range-expansion bar follow-through", "h1", dict(k=2.0, stop_atr=1.5, hold=6), range_expansion,
          "Follows an hourly bar twice the normal range that closes near its extreme.",
          family=VOL, batch=B, source="Own idea, after Crabel's range-expansion observations",
          logic="A very wide bar closing at its high shows strong buying that often continues briefly.",
          entry_rule="If the bar's range > 2 x the previous bar's ATR(14): long if it closes in its top quarter, short if in its bottom quarter.",
          exit_rule="Exit after 6 bars.", stop_rule=f"1.5 x {ATR}.", data=OHLC),
    entry("cat_nr4_ib_h4", "NR4 inside-bar breakout", "h4", dict(hold=6), nr4_ib,
          "Follows the break of a bar that is both an inside bar and the narrowest of four.",
          family=VOL, batch=B, source="Toby Crabel (1990), the NR4/IB pattern",
          logic="An inside bar that is also the narrowest of four is maximum compression; the break shows direction.",
          entry_rule="If the previous bar was NR4 and inside: long when the close is above its high, short when below its low.",
          exit_rule="Exit after 6 bars.", stop_rule="The setup bar's opposite extreme.", data=OHLC),
    entry("cat_calm_breakout_h1", "Breakout from low ATR percentile", "h1", dict(lookback=500, calm=0.2, n=24, stop_atr=2.0, hold=24), atr_percentile_breakout,
          "Takes 24-bar breakouts only after ATR sat in its bottom fifth.",
          family=VOL, batch=B, source="Own idea (volatility-regime breakout filter)",
          logic="Breakouts out of quiet markets are more likely to start real moves than breakouts in noisy ones.",
          entry_rule="If the previous bar's ATR(14) percentile over 500 bars was below 20%: long when the close is above the prior 24-bar high, short below the prior 24-bar low.",
          exit_rule="Exit after 24 bars.", stop_rule=f"2 x {ATR}.", data=OHLC),
    entry("cat_vix_spike_d1", "Long gold after a VIX spike", "d1", dict(rise=0.25, stop_atr=3.0, hold=10), vix_spike,
          "Buys gold when the VIX rises 25% over five observations.",
          family=VOL, batch=B, source="Safe-haven literature, e.g. Baur and Lucey, 'Is Gold a Hedge or a Safe Haven?', Financial Review 45(2), 2010",
          logic="When fear jumps, money tends to flow into gold for a while.",
          entry_rule="Long on the day the VIX's 5-observation change exceeds 25% of its level 5 observations ago (VIX as published by that close). Long only.",
          exit_rule="Exit after 10 days.", stop_rule=f"3 x {ATR}.", data=["XAUUSD daily bars", "VIX close (FRED VIXCLS)"],
          limitations=["VIX values use an assumed one-day publication lag (FRED gives no release times)."]),
]
