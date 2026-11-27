"""Catalogue batch 5: SMC / ICT-style rules on the project's own structure detectors (20 strategies).

"Smart money" terms here are names for this project's measurable definitions (features/structure.py), not claims
about who is trading: a swing is a confirmed 3-left/3-right fractal (known 3 bars after it), a BOS is a close beyond
the last swing in the trend's direction, a CHoCH is one against it, a sweep is a wick beyond a swing with the close
back inside, an FVG is a three-bar gap. ICT teaches these by video, not in papers, so sources are given as such.
Killzones use New York time. Parameters are fixed as documented.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantresearch.strategies.base import daily_trend
from quantresearch.strategies.catalogue.common import NEW_YORK, entry, first_per_day, local_clock, signals, window_range

B = 5
SMC = "smc_ict"
ICT = "ICT (Michael J. Huddleston) public video teaching; no formal publication"
OHLC = ["XAUUSD bars (mid prices)", "project structure detectors: swings, BOS/CHoCH, sweeps, FVGs"]


def _floor_stop(dist: pd.Series, atr: pd.Series, lo: float = 0.5, hi: float = 4.0) -> pd.Series:
    return dist.clip(lower=lo * atr, upper=hi * atr)


def _flag(df, col) -> pd.Series:
    return df[col].fillna(0).astype(float) > 0


def _kz(df, start_h, end_h):
    clock = local_clock(df["ts"], NEW_YORK)
    return (clock["minute"] >= start_h * 60) & (clock["minute"] < end_h * 60), clock


def structure_break(kind: str):
    """kind 'bos' or 'choch': trade the break; stop beyond the opposite swing; target R x stop."""
    def prepare(df, p):
        up, down = _flag(df, f"{kind}_up"), _flag(df, f"{kind}_down")
        if p.get("displacement"):
            big = (df["high"] - df["low"]) > p["displacement"] * df["atr_14"]
            up, down = up & big, down & big
        if p.get("htf_trend"):
            trend = daily_trend(df, p["htf_trend"])
            up, down = up & (trend > 0), down & (trend < 0)
        if p.get("kz"):
            in_kz, clock = _kz(df, *p["kz"])
            up, down = up & in_kz, down & in_kz
            flat = clock["minute"] >= p["flat_ny"] * 60
        else:
            flat = None
        dist = pd.Series(np.where(up, df["close"] - df["swing_low"], df["swing_high"] - df["close"]), index=df.index)
        stop = _floor_stop(dist, df["atr_14"])
        return signals(df, long=up, short=down, stop=stop, target=p["target_r"] * stop, hold=p["hold"], exit_long=flat, exit_short=flat)
    return prepare


def fvg_retest(df, p):
    trend = df["structure_trend"]
    long = (trend > 0) & df["fvg_up_high"].notna() & (df["low"] <= df["fvg_up_high"]) & (df["close"] > df["fvg_up_low"]) & (df["close"] > df["open"])
    short = (trend < 0) & df["fvg_down_low"].notna() & (df["high"] >= df["fvg_down_low"]) & (df["close"] < df["fvg_down_high"]) & (df["close"] < df["open"])
    dist = pd.Series(np.where(long, df["close"] - df["fvg_up_low"], df["fvg_down_high"] - df["close"]), index=df.index) + 0.1 * df["atr_14"]
    stop = _floor_stop(dist, df["atr_14"], 0.3)
    long, short = long & ~long.shift(1, fill_value=False), short & ~short.shift(1, fill_value=False)
    return signals(df, long=long, short=short, stop=stop, target=p["target_r"] * stop, hold=p["hold"])


def consequent_encroachment(df, p):
    """Price reaches the midpoint of the nearest active FVG and closes back on the trend side of it."""
    trend = df["structure_trend"]
    mid_up = (df["fvg_up_low"] + df["fvg_up_high"]) / 2
    mid_dn = (df["fvg_down_low"] + df["fvg_down_high"]) / 2
    long = (trend > 0) & (df["low"] <= mid_up) & (df["close"] > mid_up)
    short = (trend < 0) & (df["high"] >= mid_dn) & (df["close"] < mid_dn)
    dist = pd.Series(np.where(long, df["close"] - df["fvg_up_low"], df["fvg_down_high"] - df["close"]), index=df.index) + 0.1 * df["atr_14"]
    stop = _floor_stop(dist, df["atr_14"], 0.3)
    long, short = long & ~long.shift(1, fill_value=False), short & ~short.shift(1, fill_value=False)
    return signals(df, long=long, short=short, stop=stop, target=p["target_r"] * stop, hold=p["hold"])


def ifvg_entry(df, p):
    return signals(df, long=_flag(df, "ifvg_up"), short=_flag(df, "ifvg_down"), stop=p["stop_atr"] * df["atr_14"],
                   target=p["target_atr"] * df["atr_14"], hold=p["hold"])


def sweep_reversal(df, p):
    up, down = _flag(df, "sweep_low"), _flag(df, "sweep_high")
    if p.get("kz"):
        in_kz, clock = _kz(df, *p["kz"])
        up, down = up & in_kz, down & in_kz
        flat = clock["minute"] >= p["flat_ny"] * 60
    else:
        flat = None
    if p.get("need_equal"):
        eq_lo = df["equal_lows"].fillna(0).astype(float).rolling(p["need_equal"], min_periods=1).max() > 0
        eq_hi = df["equal_highs"].fillna(0).astype(float).rolling(p["need_equal"], min_periods=1).max() > 0
        up, down = up & eq_lo, down & eq_hi
    dist = pd.Series(np.where(up, df["close"] - df["low"], df["high"] - df["close"]), index=df.index) + 0.1 * df["atr_14"]
    stop = _floor_stop(dist, df["atr_14"], 0.3)
    return signals(df, long=up, short=down, stop=stop, target=p["target_r"] * stop, hold=p["hold"], exit_long=flat, exit_short=flat)


def sweep_then_choch(df, p):
    n = p["within"]
    swept_low = _flag(df, "sweep_low").astype(float).rolling(n, min_periods=1).max() > 0
    swept_high = _flag(df, "sweep_high").astype(float).rolling(n, min_periods=1).max() > 0
    long, short = swept_low & _flag(df, "choch_up"), swept_high & _flag(df, "choch_down")
    low_n, high_n = df["low"].rolling(n, min_periods=1).min(), df["high"].rolling(n, min_periods=1).max()
    dist = pd.Series(np.where(long, df["close"] - low_n, high_n - df["close"]), index=df.index) + 0.1 * df["atr_14"]
    stop = _floor_stop(dist, df["atr_14"])
    return signals(df, long=long, short=short, stop=stop, target=p["target_r"] * stop, hold=p["hold"])


def judas_swing(df, p):
    clock = local_clock(df["ts"], NEW_YORK)
    # The Asian range runs 20:00-00:00 New York; it belongs to the next calendar day's London session.
    session_day = (df["ts"].dt.tz_convert(NEW_YORK) + pd.Timedelta(hours=4)).dt.date
    asia = (clock["minute"] >= 20 * 60)
    rng = window_range(df, asia, session_day)
    london = (clock["minute"] >= 2 * 60) & (clock["minute"] < 5 * 60)
    short = london & (df["high"] > rng["high"]) & (df["close"] < rng["high"])
    long = london & (df["low"] < rng["low"]) & (df["close"] > rng["low"])
    first = first_per_day(short | long, session_day)
    long, short = long & first, short & first
    width = rng["high"] - rng["low"]
    dist = pd.Series(np.where(long, df["close"] - df["low"], df["high"] - df["close"]), index=df.index) + 0.1 * df["atr_14"]
    stop = _floor_stop(dist, df["atr_14"], 0.3)
    flat = (clock["minute"] >= p["flat_ny"] * 60) & (clock["minute"] < 20 * 60)
    return signals(df, long=long, short=short, stop=stop, target=width, exit_long=flat, exit_short=flat)


def silver_bullet(df, p):
    in_kz, clock = _kz(df, 10, 11)
    fvg_time = df["ts"].where(_flag(df, "fvg_up")).ffill()
    fvg_dn_time = df["ts"].where(_flag(df, "fvg_down")).ffill()
    hour_start = df["ts"].dt.tz_convert(NEW_YORK).dt.floor("h").dt.tz_convert("UTC")
    fresh_up, fresh_dn = in_kz & (fvg_time >= hour_start), in_kz & (fvg_dn_time >= hour_start)
    long = fresh_up & (df["low"] <= df["fvg_up_high"]) & (df["close"] > df["fvg_up_low"]) & ~_flag(df, "fvg_up")
    short = fresh_dn & (df["high"] >= df["fvg_down_low"]) & (df["close"] < df["fvg_down_high"]) & ~_flag(df, "fvg_down")
    day = clock["date"]
    first = first_per_day(long | short, day)
    long, short = long & first, short & first
    dist = pd.Series(np.where(long, df["close"] - df["fvg_up_low"], df["fvg_down_high"] - df["close"]), index=df.index) + 0.1 * df["atr_14"]
    stop = _floor_stop(dist, df["atr_14"], 0.3)
    flat = (clock["minute"] >= p["flat_ny"] * 60) & (clock["minute"] < 18 * 60)
    return signals(df, long=long, short=short, stop=stop, target=p["target_r"] * stop, exit_long=flat, exit_short=flat)


def premium_discount(df, p):
    trend, pd_ = df["structure_trend"], df["premium_discount"]
    long = (trend > 0) & (pd_ < p["discount"]) & (df["close"] > df["open"])
    short = (trend < 0) & (pd_ > 1 - p["discount"]) & (df["close"] < df["open"])
    long, short = long & ~long.shift(1, fill_value=False), short & ~short.shift(1, fill_value=False)
    dist = pd.Series(np.where(long, df["close"] - df["swing_low"], df["swing_high"] - df["close"]), index=df.index) + 0.1 * df["atr_14"]
    stop = _floor_stop(dist, df["atr_14"], 0.3)
    target = pd.Series(np.where(long, df["swing_high"] - df["close"], df["close"] - df["swing_low"]), index=df.index)
    return signals(df, long=long, short=short, stop=stop, target=target.where(target > 0), hold=p["hold"])


def pd_level_sweep(df, p):
    in_kz, clock = _kz(df, *p["kz"])
    short = in_kz & (df["high"] > df["pdh"]) & (df["close"] < df["pdh"])
    long = in_kz & (df["low"] < df["pdl"]) & (df["close"] > df["pdl"])
    first = first_per_day(long | short, clock["date"])
    long, short = long & first, short & first
    dist = pd.Series(np.where(long, df["close"] - df["low"], df["high"] - df["close"]), index=df.index) + 0.1 * df["atr_14"]
    stop = _floor_stop(dist, df["atr_14"], 0.3)
    flat = (clock["minute"] >= p["flat_ny"] * 60) & (clock["minute"] < 18 * 60)
    return signals(df, long=long, short=short, stop=stop, target=p["target_r"] * stop, exit_long=flat, exit_short=flat)


def order_block(df, p):
    """At each BOS, the last opposite-colour candle in the prior `lookback` bars is the order block. A retest of it
    within `valid` bars, closing back out of the zone in the break's direction, is the entry. One entry per block."""
    n = len(df)
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    bos_up, bos_dn = _flag(df, "bos_up").to_numpy(), _flag(df, "bos_down").to_numpy()
    long, short = np.zeros(n, bool), np.zeros(n, bool)
    stop = np.full(n, np.nan)
    atr = df["atr_14"].to_numpy()
    zone = None  # (side, low, high, expires)
    for i in range(n):
        if zone is not None:
            side, zl, zh, expires = zone
            if i > expires:
                zone = None
            elif side > 0 and l[i] <= zh and c[i] > zh:
                long[i], stop[i], zone = True, c[i] - zl + 0.1 * atr[i], None
            elif side < 0 and h[i] >= zl and c[i] < zl:
                short[i], stop[i], zone = True, zh - c[i] + 0.1 * atr[i], None
            elif (side > 0 and c[i] < zl) or (side < 0 and c[i] > zh):
                zone = None  # Closed through the block: it failed.
        if bos_up[i] or bos_dn[i]:
            want_bear = bool(bos_up[i])
            for j in range(i - 1, max(-1, i - 1 - p["lookback"]), -1):
                if (c[j] < o[j]) if want_bear else (c[j] > o[j]):
                    zone = (1 if want_bear else -1, l[j], h[j], i + p["valid"])
                    break
    stop_s = _floor_stop(pd.Series(stop, index=df.index), df["atr_14"], 0.3)
    return signals(df, long=pd.Series(long, index=df.index), short=pd.Series(short, index=df.index), stop=stop_s, target=p["target_r"] * stop_s, hold=p["hold"])


def ote(df, p):
    """Optimal trade entry: after a BOS, a retracement to 62-79% of the swing range, closing in the trend direction."""
    rng = df["swing_high"] - df["swing_low"]
    trend = df["structure_trend"]
    retrace_up = (df["swing_high"] - df["close"]) / rng.where(rng > 0)
    retrace_dn = (df["close"] - df["swing_low"]) / rng.where(rng > 0)
    recent_up = _flag(df, "bos_up").astype(float).rolling(p["within"], min_periods=1).max() > 0
    recent_dn = _flag(df, "bos_down").astype(float).rolling(p["within"], min_periods=1).max() > 0
    long = (trend > 0) & recent_up & retrace_up.between(p["lo"], p["hi"]) & (df["close"] > df["open"])
    short = (trend < 0) & recent_dn & retrace_dn.between(p["lo"], p["hi"]) & (df["close"] < df["open"])
    long, short = long & ~long.shift(1, fill_value=False), short & ~short.shift(1, fill_value=False)
    dist = pd.Series(np.where(long, df["close"] - df["swing_low"], df["swing_high"] - df["close"]), index=df.index) + 0.1 * df["atr_14"]
    stop = _floor_stop(dist, df["atr_14"], 0.3)
    target = pd.Series(np.where(long, df["swing_high"] - df["close"], df["close"] - df["swing_low"]), index=df.index)
    return signals(df, long=long, short=short, stop=stop, target=target.where(target > 0), hold=p["hold"])


def power_of_three(df, p):
    """Accumulation (Asia), manipulation (a London move against the day), distribution (New York): at 08:30 New York,
    if the day first dipped below the midnight open and is now back above it, go long; the mirror short."""
    clock = local_clock(df["ts"], NEW_YORK)
    day = clock["date"]
    # The open of the day's first bar at or after 00:00 New York (bars group by New York date, so it is that bar).
    midnight = df.groupby(day)["open"].transform("first")
    low_so_far = df["low"].groupby(day).cummin()
    high_so_far = df["high"].groupby(day).cummax()
    at = clock["minute"] == 8 * 60 + 15  # The M15 bar closing 08:30.
    dipped = low_so_far < midnight - p["manip_atr"] * df["atr_14"]
    popped = high_so_far > midnight + p["manip_atr"] * df["atr_14"]
    long = at & dipped & (df["close"] > midnight) & ~popped
    short = at & popped & (df["close"] < midnight) & ~dipped
    dist = pd.Series(np.where(long, df["close"] - low_so_far, high_so_far - df["close"]), index=df.index) + 0.1 * df["atr_14"]
    stop = _floor_stop(dist, df["atr_14"], 0.5, 8.0)
    flat = (clock["minute"] >= p["flat_ny"] * 60) & (clock["minute"] < 18 * 60)
    return signals(df, long=long, short=short, stop=stop, exit_long=flat, exit_short=flat)


R2 = dict(target_r=2.0)
STOP_SWING = "Beyond the opposite confirmed swing (swing low for longs), at least 0.5 and at most 4 x ATR(14)."
ENTRIES = [
    entry("cat_bos_follow_h1", "Break of structure continuation", "h1", dict(**R2, hold=48), structure_break("bos"),
          "Follows each break of structure in the trend's direction with a 2R target.",
          family=SMC, batch=B, source=ICT, logic="A close beyond the last swing in the trend's direction confirms the trend; join it.",
          entry_rule="Long on a bullish BOS (close above the last confirmed swing high, trend not down); short on a bearish BOS.",
          exit_rule="Target 2 x the stop distance; otherwise exit after 48 bars; an opposite signal reverses.", stop_rule=STOP_SWING, data=OHLC),
    entry("cat_choch_reversal_h1", "Change of character reversal", "h1", dict(**R2, hold=48), structure_break("choch"),
          "Trades the first break of structure against the prevailing trend.",
          family=SMC, batch=B, source=ICT, logic="The first close beyond a swing against the trend marks a possible reversal.",
          entry_rule="Long on a bullish CHoCH (close above the last swing high while the trend is down); short on a bearish CHoCH.",
          exit_rule="Target 2R; otherwise exit after 48 bars.", stop_rule=STOP_SWING, data=OHLC),
    entry("cat_mss_displacement_h1", "Market-structure shift with displacement", "h1", dict(displacement=1.5, **R2, hold=48), structure_break("choch"),
          "A CHoCH made by a wide-range bar (more than 1.5 ATR).",
          family=SMC, batch=B, source=ICT, logic="A structure shift made with a big, fast candle shows real intent behind the reversal.",
          entry_rule="As the CHoCH rule, but only when the breaking bar's high-low range exceeds 1.5 x ATR(14).",
          exit_rule="Target 2R; otherwise exit after 48 bars.", stop_rule=STOP_SWING, data=OHLC),
    entry("cat_htf_bias_bos_h1", "BOS with the daily bias", "h1", dict(htf_trend=20, **R2, hold=48), structure_break("bos"),
          "Takes hourly BOS signals only in the direction of the daily trend.",
          family=SMC, batch=B, source=ICT + " (higher-timeframe bias)", logic="Trade lower-timeframe structure only with the higher-timeframe trend.",
          entry_rule="Long on a bullish H1 BOS when the last completed New York day closed above its 20-day average; short mirrored.",
          exit_rule="Target 2R; otherwise exit after 48 bars.", stop_rule=STOP_SWING, data=OHLC),
    entry("cat_ny_kz_bos_m15", "BOS in the New York killzone", "m15", dict(kz=(7, 10), flat_ny=12, htf_trend=20, **R2, hold=20), structure_break("bos"),
          "M15 BOS between 07:00 and 10:00 New York in the daily trend's direction, flat by noon.",
          family=SMC, batch=B, source=ICT + " (killzones)", logic="Structure breaks in the most active US hours, with the daily trend, carry follow-through.",
          entry_rule="Long on a bullish M15 BOS with a bar opening 07:00-10:00 New York and an up daily trend (20-day); short mirrored.",
          exit_rule="Target 2R; flat from 12:00 New York; at most 20 bars.", stop_rule=STOP_SWING, data=OHLC),
    entry("cat_choch_ny_kz_m15", "CHoCH in the New York killzone", "m15", dict(kz=(7, 10), flat_ny=12, **R2, hold=20), structure_break("choch"),
          "M15 change of character between 07:00 and 10:00 New York.",
          family=SMC, batch=B, source=ICT + " (killzones)", logic="Reversals that start in the New York killzone often set the day's direction.",
          entry_rule="Long on a bullish M15 CHoCH with a bar opening 07:00-10:00 New York; short on a bearish one.",
          exit_rule="Target 2R; flat from 12:00 New York; at most 20 bars.", stop_rule=STOP_SWING, data=OHLC),
    entry("cat_fvg_retest_h1", "Fair value gap retest with trend", "h1", dict(**R2, hold=24), fvg_retest,
          "Buys a dip into the nearest bullish FVG in an up structure, and the mirror.",
          family=SMC, batch=B, source=ICT, logic="Price often returns to an imbalance and continues; enter on the return.",
          entry_rule="Up structure: long when the bar's low reaches the nearest active bullish FVG's upper edge and it closes above the gap's lower edge and above its open. Short mirrored.",
          exit_rule="Target 2R; otherwise exit after 24 bars.", stop_rule="Beyond the gap's far edge + 0.1 ATR (0.3-4 ATR).", data=OHLC),
    entry("cat_fvg_ce_h1", "FVG consequent encroachment", "h1", dict(**R2, hold=24), consequent_encroachment,
          "Enters when price tags the middle of the nearest FVG and closes back on the trend side.",
          family=SMC, batch=B, source=ICT + " ('consequent encroachment')", logic="The midpoint of a gap is where it is often defended.",
          entry_rule="Up structure: long when the low reaches the midpoint of the nearest bullish FVG and the close is above it. Short mirrored.",
          exit_rule="Target 2R; otherwise exit after 24 bars.", stop_rule="Beyond the gap's far edge + 0.1 ATR (0.3-4 ATR).", data=OHLC),
    entry("cat_ifvg_h1", "Inverse FVG", "h1", dict(stop_atr=1.5, target_atr=3.0, hold=24), ifvg_entry,
          "Follows a close through an FVG, which turns it into an inverse FVG.",
          family=SMC, batch=B, source=ICT, logic="When a gap fails and price closes through it, the move tends to continue.",
          entry_rule="Long when a bearish FVG is closed through (inverse bullish FVG); short when a bullish FVG is closed through.",
          exit_rule="Target 3 x ATR(14); otherwise exit after 24 bars.", stop_rule="1.5 x ATR(14).", data=OHLC),
    entry("cat_sweep_reversal_h1", "Liquidity sweep reversal", "h1", dict(**R2, hold=24), sweep_reversal,
          "Fades a wick beyond the last swing that closes back inside.",
          family=SMC, batch=B, source=ICT + " ('stop hunt' / 'liquidity grab')", logic="Stops cluster beyond swings; once taken, price often reverses.",
          entry_rule="Long when the low goes below the last swing low and the close is back above it; short mirrored at the swing high.",
          exit_rule="Target 2R; otherwise exit after 24 bars.", stop_rule="Beyond the sweep bar's extreme + 0.1 ATR (0.3-4 ATR).", data=OHLC),
    entry("cat_sweep_choch_h1", "Sweep followed by CHoCH", "h1", dict(within=6, **R2, hold=36), sweep_then_choch,
          "A change of character within six bars of a liquidity sweep.",
          family=SMC, batch=B, source=ICT + " (sweep plus market-structure shift)", logic="A stop hunt followed by a structure break against it is a classic reversal set-up.",
          entry_rule="Long on a bullish CHoCH if a low sweep happened in the last 6 bars; short on a bearish CHoCH after a high sweep.",
          exit_rule="Target 2R; otherwise exit after 36 bars.", stop_rule="Beyond the lowest low (highest high) of the last 6 bars + 0.1 ATR.", data=OHLC),
    entry("cat_equal_lows_raid_h1", "Raid on equal highs or lows", "h1", dict(need_equal=10, **R2, hold=24), sweep_reversal,
          "Fades a sweep of a level made by two near-equal swings.",
          family=SMC, batch=B, source=ICT + " ('equal highs/lows are liquidity')", logic="Equal highs or lows attract stops; the raid on them often reverses.",
          entry_rule="Long on a low sweep when equal lows (two swing lows within 0.1 ATR) were flagged in the last 10 bars; short on a high sweep after equal highs.",
          exit_rule="Target 2R; otherwise exit after 24 bars.", stop_rule="Beyond the sweep bar's extreme + 0.1 ATR (0.3-4 ATR).", data=OHLC),
    entry("cat_london_kz_sweep_m15", "Sweep reversal in the London killzone", "m15", dict(kz=(2, 5), flat_ny=12, **R2, hold=16), sweep_reversal,
          "M15 liquidity sweeps between 02:00 and 05:00 New York, faded.",
          family=SMC, batch=B, source=ICT + " (London killzone)", logic="London often raids stops left in Asia before the real move.",
          entry_rule="Long on an M15 low sweep with a bar opening 02:00-05:00 New York; short on a high sweep.",
          exit_rule="Target 2R; flat from 12:00 New York; at most 16 bars.", stop_rule="Beyond the sweep bar's extreme + 0.1 ATR.", data=OHLC),
    entry("cat_ny_kz_sweep_m15", "Sweep reversal in the New York killzone", "m15", dict(kz=(7, 10), flat_ny=12, **R2, hold=16), sweep_reversal,
          "M15 liquidity sweeps between 07:00 and 10:00 New York, faded.",
          family=SMC, batch=B, source=ICT + " (New York killzone)", logic="The New York open often raids London's extremes before reversing.",
          entry_rule="Long on an M15 low sweep with a bar opening 07:00-10:00 New York; short on a high sweep.",
          exit_rule="Target 2R; flat from 12:00 New York; at most 16 bars.", stop_rule="Beyond the sweep bar's extreme + 0.1 ATR.", data=OHLC),
    entry("cat_pdh_pdl_kz_m15", "Previous-day high/low raid in the NY killzone", "m15", dict(kz=(7, 10), flat_ny=12, **R2), pd_level_sweep,
          "Fades a raid on yesterday's high or low during the New York killzone.",
          family=SMC, batch=B, source=ICT, logic="Yesterday's extremes hold resting orders; a raid on them in the killzone tends to fail.",
          entry_rule="First per day: short when an M15 bar opening 07:00-10:00 New York trades above yesterday's high and closes below it; long mirrored at yesterday's low.",
          exit_rule="Target 2R; flat from 12:00 New York.", stop_rule="Beyond the bar's extreme + 0.1 ATR.", data=OHLC),
    entry("cat_judas_swing_m15", "Judas swing at the London open", "m15", dict(flat_ny=12), judas_swing,
          "Fades a London-killzone break of the Asian range that closes back inside, targeting the other side.",
          family=SMC, batch=B, source=ICT + " ('Judas swing')", logic="The London open often fakes one way out of the Asian range before the real move the other way.",
          entry_rule="Asian range = 20:00-00:00 New York. Between 02:00 and 05:00 New York, first time per day: short when the high breaks the range high and the close is back below it; long mirrored.",
          exit_rule="Target = the Asian range width; flat from 12:00 New York.", stop_rule="Beyond the bar's extreme + 0.1 ATR (0.3-4 ATR).", data=OHLC),
    entry("cat_silver_bullet_m5", "ICT Silver Bullet (10-11 New York)", "m5", dict(**R2, flat_ny=11.5), silver_bullet,
          "Enters a retest of an FVG formed inside the 10:00-11:00 New York hour.",
          family=SMC, batch=B, source=ICT + " ('Silver Bullet')", logic="An imbalance created in the 10-11 New York hour is often retested and respected.",
          entry_rule="M5, bars opening 10:00-11:00 New York, first per day: long when a bullish FVG formed this hour is retested (low into the gap, close above its lower edge); short mirrored.",
          exit_rule="Target 2R; flat from 11:30 New York.", stop_rule="Beyond the gap's far edge + 0.1 ATR (0.3-4 ATR).", data=OHLC),
    entry("cat_premium_discount_h1", "Buy discount, sell premium", "h1", dict(discount=0.25, hold=48), premium_discount,
          "In an up structure buys in the bottom quarter of the swing range, targeting the swing high; the mirror.",
          family=SMC, batch=B, source=ICT + " (premium and discount arrays)", logic="Buy cheap within the current range in an uptrend, sell dear in a downtrend.",
          entry_rule="Up structure: long on the first bullish bar with premium_discount < 0.25 (bottom quarter of the last swing range); short mirrored above 0.75.",
          exit_rule="Target the opposite swing; otherwise exit after 48 bars.", stop_rule="Beyond the swing low (high) + 0.1 ATR (0.3-4 ATR).", data=OHLC),
    entry("cat_order_block_h1", "Order-block retest", "h1", dict(lookback=10, valid=24, **R2, hold=36), order_block,
          "After a BOS, buys the first retest of the last opposite candle before the break.",
          family=SMC, batch=B, source=ICT + " ('order blocks')", logic="The last down candle before a strong up break marks where buyers stepped in; it often holds on a retest.",
          entry_rule="At a bullish BOS, the order block is the last bearish candle in the prior 10 bars. Within 24 bars, long when a bar's low enters the block and it closes above the block's high. Short mirrored. A close through the block cancels it.",
          exit_rule="Target 2R; otherwise exit after 36 bars.", stop_rule="Beyond the block's far edge + 0.1 ATR (0.3-4 ATR).", data=OHLC),
    entry("cat_ote_h1", "Optimal trade entry (62-79% retracement)", "h1", dict(within=24, lo=0.62, hi=0.79, hold=48), ote,
          "After a BOS, buys a retracement into the 62-79% zone of the swing range.",
          family=SMC, batch=B, source=ICT + " ('optimal trade entry', Fibonacci 62-79%)", logic="Deep but not full retracements after a structure break offer good risk-reward entries.",
          entry_rule="Within 24 bars of a bullish BOS, in an up structure: long on the first bullish bar whose close is 62-79% of the way down from the swing high to the swing low. Short mirrored.",
          exit_rule="Target the swing high (low); otherwise exit after 48 bars.", stop_rule="Beyond the swing low (high) + 0.1 ATR (0.3-4 ATR).", data=OHLC),
    entry("cat_power_of_three_m15", "Power of three (AMD) at 08:30 New York", "m15", dict(manip_atr=2.0, flat_ny=16), power_of_three,
          "If the day dipped well below the midnight open and is back above it by 08:30 New York, buys for the New York session.",
          family=SMC, batch=B, source=ICT + " ('power of three': accumulation, manipulation, distribution)",
          logic="A false early move against the day's open, then a recovery through it, often precedes the day's real move.",
          entry_rule="At the bar closing 08:30 New York: long if today's low was more than 2 x ATR(14, M15) below the 00:00 New York open and the close is back above that open (and no equal push above); short mirrored.",
          exit_rule="Flat from 16:00 New York.", stop_rule="Beyond the day's extreme so far + 0.1 ATR (0.5-8 ATR).", data=OHLC),
]
