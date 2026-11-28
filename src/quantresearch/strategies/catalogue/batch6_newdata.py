"""Catalogue batch 6: strategies that need the data added on 2026-11-28 (9 strategies).

New evidence rather than new rules on old data:
  - first-release US payrolls, unemployment and CPI from ALFRED vintages (external/alfred.py), with a *surprise
    proxy*: the first release minus the median of the previous 12 first releases. It is not a consensus surprise
    (no free consensus archive exists), and the rules below say so;
  - the full CFTC disaggregated COT report for gold, including producers, swap dealers, other reportables and
    open interest (external/sources.parse_cot).
Macro columns are `m_<SERIES>` as published by each bar's close, and `hrs_<SERIES>` is the hours since that series
was last published. Releases are at 08:30 New York. Parameters are fixed as documented and were set before any of
these strategies was run.
"""
from __future__ import annotations

import pandas as pd

from quantresearch.strategies.catalogue.common import cross_down, cross_up, entry, prior_high, prior_low, signals, sma

B = 6
EV, POS = "event", "positioning"
ATR = "the project's Wilder ATR(14) on the signal timeframe"
H1 = ["XAUUSD H1 bars (mid prices)"]
D1 = ["XAUUSD D1 bars (mid prices)"]
ALFRED = "ALFRED first-release vintages (St. Louis Fed), surprise proxy = first release minus median of the previous 12 first releases"
COT = "CFTC disaggregated Commitments of Traders, gold (COMEX), published Friday 15:30 New York for the Tuesday"
EVENT_SOURCE = ("Event studies of metals and macro news: Elder, Miao and Ramchander, 'Impact of Macroeconomic News on Metal Futures', "
                "J. Banking & Finance 36(1), 2012, which finds gold falls on stronger-than-expected US activity and inflation news")


def _has(df, *columns) -> bool:
    return all(c in df and df[c].notna().any() for c in columns)


def release_reaction(series: str, window_h: float, side_when_high: int):
    """At the first bar closing within `window_h` hours after a release: trade the surprise's sign (or against it)."""
    def prepare(df, p):
        value, hrs = f"m_{series}", f"hrs_{series}"
        if not _has(df, value, hrs):
            return signals(df)
        just = (df[hrs] > 0) & (df[hrs] <= window_h)
        first = just & ~just.shift(1, fill_value=False)
        high, low = first & (df[value] > p["threshold"]), first & (df[value] < -p["threshold"])
        long, short = (high, low) if side_when_high > 0 else (low, high)
        if p.get("long_only"):
            short = None
        return signals(df, long=long, short=short, stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])
    return prepare


def cot_extreme(series: str, side_when_high: int):
    """Long (or short) when a COT net position's two-year z-score crosses beyond +-z; hold a fixed time."""
    def prepare(df, p):
        col = f"m_{series}"
        if not _has(df, col):
            return signals(df)
        x = df[col]
        z = (x - x.rolling(p["window"], min_periods=p["window"] // 2).mean()) / x.rolling(p["window"], min_periods=p["window"] // 2).std()
        high, low = cross_up(z, p["z"]), cross_down(z, -p["z"])
        long, short = (high, low) if side_when_high > 0 else (low, high)
        return signals(df, long=long, short=short, stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])
    return prepare


def cot_flow_trend(df, p):
    col = "m_COT_OTHER_NET"
    if not _has(df, col):
        return signals(df)
    flow = df[col] - df[col].shift(p["days"])
    trend = sma(df["close"], p["sma"])
    up, down = (flow > 0) & (df["close"] > trend), (flow < 0) & (df["close"] < trend)
    return signals(df, long=up & ~up.shift(1, fill_value=False), short=down & ~down.shift(1, fill_value=False),
                   stop=p["stop_atr"] * df["atr_14"], exit_long=~up, exit_short=~down)


def oi_breakout(df, p):
    if not _has(df, "m_COT_OI"):
        return signals(df)
    rising = df["m_COT_OI"] > df["m_COT_OI"].shift(p["oi_days"])
    long = (df["close"] > prior_high(df, p["n"])) & rising
    short = (df["close"] < prior_low(df, p["n"])) & rising
    return signals(df, long=long, short=short, stop=p["stop_atr"] * df["atr_14"],
                   exit_long=df["close"] < prior_low(df, p["exit_n"]), exit_short=df["close"] > prior_high(df, p["exit_n"]))


ENTRIES = [
    entry("cat_nfp_surprise_proxy_h1", "Payrolls surprise (first release vs its recent median)", "h1",
          dict(threshold=75.0, stop_atr=2.0, hold=24), release_reaction("ALFRED_PAYEMS_SURP", 1.0, -1),
          "Sells gold after an unusually strong first-release payrolls number and buys after an unusually weak one.",
          family=EV, batch=B, source=EVENT_SOURCE,
          logic="Strong jobs data lifts expected rates and the dollar, which weighs on gold; the move is said to continue after the first minutes.",
          entry_rule="At the first H1 close after the 08:30 New York release: short if first-release payrolls change minus the median of the previous 12 "
                     "first releases is above +75k; long if below -75k.",
          exit_rule="Exit after 24 bars.", stop_rule=f"2 x {ATR}.", data=H1 + [ALFRED], macro=["ALFRED_PAYEMS_SURP"],
          limitations=["The surprise is a proxy against the series' own recent first releases, not against the economists' consensus."]),
    entry("cat_nfp_surprise_proxy_d1", "Payrolls surprise, daily hold", "d1",
          dict(threshold=75.0, stop_atr=3.0, hold=5), release_reaction("ALFRED_PAYEMS_SURP", 24.0, -1),
          "The daily-bar version: acts at the release day's close and holds a week.",
          family=EV, batch=B, source=EVENT_SOURCE,
          logic="As the hourly rule, but measured over days, where costs matter less.",
          entry_rule="At the close of the release day: short if the payrolls surprise proxy is above +75k; long if below -75k.",
          exit_rule="Exit after 5 days.", stop_rule=f"3 x {ATR}.", data=D1 + [ALFRED], macro=["ALFRED_PAYEMS_SURP"],
          limitations=["The surprise is a proxy against the series' own recent first releases, not against the economists' consensus."]),
    entry("cat_core_cpi_surprise_proxy_h1", "Core CPI surprise (first release vs its recent median)", "h1",
          dict(threshold=0.1, stop_atr=2.0, hold=24), release_reaction("ALFRED_CORE_SURP", 1.0, -1),
          "Sells gold after a hot first-release core CPI and buys after a soft one.",
          family=EV, batch=B, source=EVENT_SOURCE,
          logic="A hot core inflation print raises expected policy rates, which the cited study finds weighs on gold in the short run.",
          entry_rule="At the first H1 close after the 08:30 New York CPI release: short if first-release core CPI m/m minus the median of the previous "
                     "12 first releases is above +0.1 points; long if below -0.1.",
          exit_rule="Exit after 24 bars.", stop_rule=f"2 x {ATR}.", data=H1 + [ALFRED], macro=["ALFRED_CORE_SURP"],
          limitations=["The surprise is a proxy against the series' own recent first releases, not against the economists' consensus."]),
    entry("cat_payroll_revision_d1", "Payrolls revisions", "d1",
          dict(threshold=50.0, stop_atr=3.0, hold=10), release_reaction("ALFRED_PAYEMS_REV", 24.0, -1),
          "Buys gold after a large downward revision to the previous month's payrolls and sells after a large upward one.",
          family=EV, batch=B, source="Own idea, using first-release vintages: revisions are information the headline number hides",
          logic="Revisions show how the labour market really went; a large downward revision is a dovish surprise even when the headline is fine.",
          entry_rule="At the close of the release day: long if the previous month's change was revised down by more than 50k; short if revised up by more than 50k.",
          exit_rule="Exit after 10 days.", stop_rule=f"3 x {ATR}.", data=D1 + [ALFRED], macro=["ALFRED_PAYEMS_REV"]),
    entry("cat_unrate_jump_d1", "Unemployment-rate jump", "d1",
          dict(threshold=0.2, stop_atr=3.0, hold=20, long_only=True), release_reaction("ALFRED_UNRATE_SURP", 24.0, 1),
          "Buys gold when the first-release unemployment rate jumps above its recent level.",
          family=EV, batch=B, source="Claudia Sahm's recession rule (2019) as the idea; this is a simpler first-release form, own parameters",
          logic="A sudden rise in unemployment brings forward rate cuts, which supports gold.",
          entry_rule="At the close of the release day: long if the first-release unemployment rate is at least 0.2 points above the median of the "
                     "previous 12 first releases. Long only.",
          exit_rule="Exit after 20 days.", stop_rule=f"3 x {ATR}.", data=D1 + [ALFRED], macro=["ALFRED_UNRATE_SURP"]),
    entry("cat_cot_producer_z_d1", "Producer hedgers unusually light (COT)", "d1",
          dict(window=520, z=1.5, stop_atr=3.0, hold=20), cot_extreme("COT_PROD_NET", 1),
          "Buys gold when producers and merchants are much less net short than usual, and sells when they are much more.",
          family=POS, batch=B, source="The 'commercials are the smart money' COT reading, e.g. Larry Williams, 'Trade Stocks and Commodities with the Insiders' (2005)",
          logic="Producers hedge less when they expect higher prices; their positioning is said to lead.",
          entry_rule="Long when the z-score of producer/merchant net position over the last 520 daily bars (about two years) crosses above +1.5; "
                     "short when it crosses below -1.5.",
          exit_rule="Exit after 20 days.", stop_rule=f"3 x {ATR}.", data=D1 + [COT], macro=["COT_PROD_NET"]),
    entry("cat_cot_swap_z_d1", "Swap dealers unusually long (COT)", "d1",
          dict(window=520, z=1.5, stop_atr=3.0, hold=20), cot_extreme("COT_SWAP_NET", 1),
          "Follows swap dealers when their net position is far from its two-year norm.",
          family=POS, batch=B, source="Same COT 'commercials' reading applied to swap dealers (bank hedging desks); own parameters",
          logic="Swap dealers sit on the commercial side of the market; extreme moves in their book may reflect informed client flow.",
          entry_rule="Long when the swap-dealer net position z-score (520 daily bars) crosses above +1.5; short when it crosses below -1.5.",
          exit_rule="Exit after 20 days.", stop_rule=f"3 x {ATR}.", data=D1 + [COT], macro=["COT_SWAP_NET"]),
    entry("cat_cot_other_flow_d1", "Other-reportables flow with the trend (COT)", "d1",
          dict(days=20, sma=50, stop_atr=3.0), cot_flow_trend,
          "Holds gold in the trend's direction while large non-commercial traders outside managed money are adding in that direction.",
          family=POS, batch=B, source="Own idea using the 'other reportables' category of the disaggregated report",
          logic="Trend moves backed by fresh positioning are more likely to last.",
          entry_rule="Long on the first day the other-reportables net position is higher than 20 daily bars earlier and the close is above SMA(50); "
                     "short mirrored.",
          exit_rule="Exit when either condition no longer holds.", stop_rule=f"3 x {ATR}.", data=D1 + [COT], macro=["COT_OTHER_NET"]),
    entry("cat_oi_breakout_d1", "Breakout confirmed by rising open interest", "d1",
          dict(n=20, exit_n=10, oi_days=20, stop_atr=3.0), oi_breakout,
          "Takes 20-day breakouts only while futures open interest is rising.",
          family=POS, batch=B, source="Classic volume/open-interest confirmation, e.g. John Murphy, 'Technical Analysis of the Financial Markets' (1999), ch. 7",
          logic="A breakout with rising open interest has new money behind it; one with falling open interest is short covering.",
          entry_rule="Long when the close is above the prior 20-day high and COT open interest is above its value 20 daily bars earlier; short when "
                     "the close is below the prior 20-day low with open interest rising.",
          exit_rule="Exit long on a close below the prior 10-day low; exit short on a close above the prior 10-day high.", stop_rule=f"3 x {ATR}.",
          data=D1 + [COT + " (open interest is weekly; daily futures open interest is not free)"], macro=["COT_OI"]),
]
