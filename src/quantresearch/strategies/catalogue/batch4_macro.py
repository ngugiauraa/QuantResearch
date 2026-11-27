"""Catalogue batch 4: macro and cross-market relationships (20 strategies).

Macro inputs are attached by the catalogue runner as `m_<SERIES>` columns: the latest value PUBLISHED by each bar's
close (FRED values carry the project's conservative assumed publication lags; COT is Friday 15:30 New York). Cross-
market closes are `<symbol>_close`, the latest finished H1 bar. Changes over "20 days" on daily bars are value minus
the value 20 daily bars earlier. All parameters are fixed as documented.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantresearch.strategies.catalogue.common import cross_down, cross_up, entry, prior_high, prior_low, signals, sma

B = 4
MAC, XM = "macro", "cross_market"
ATR = "the project's Wilder ATR(14) on the signal timeframe"


def _has(df, *columns) -> bool:
    return all(c in df and df[c].notna().any() for c in columns)


# ---------------------------------------------------------------- Rates and inflation
def change_rule(series: str, days: int, threshold: float, side_when_up: int, relative: bool = False, hold: str = "hold"):
    """Enter when a macro series has moved more than `threshold` over `days` daily bars."""
    def prepare(df, p):
        col = f"m_{series}"
        if not _has(df, col):
            return signals(df)
        x = df[col]
        chg = (x / x.shift(days) - 1) if relative else (x - x.shift(days))
        up, down = cross_up(chg, threshold), cross_down(chg, -threshold)
        long, short = (up, down) if side_when_up > 0 else (down, up)
        return signals(df, long=long, short=short if p.get("two_sided", True) else None, stop=p["stop_atr"] * df["atr_14"], hold=p[hold])
    return prepare


def real_yield_trend(df, p):
    if not _has(df, "m_DFII10"):
        return signals(df)
    ry = df["m_DFII10"]
    falling = ry < sma(ry, p["ry_sma"])
    up = df["close"] > df["sma_50"]
    state = falling & up
    return signals(df, long=state & ~state.shift(1, fill_value=False), stop=p["stop_atr"] * df["atr_14"], exit_long=~falling | ~up)


def fed_cut_cycle(df, p):
    if not _has(df, "m_DFF"):
        return signals(df)
    chg = df["m_DFF"] - df["m_DFF"].shift(p["days"])
    cutting = chg < -p["threshold"]
    return signals(df, long=cutting & ~cutting.shift(1, fill_value=False), stop=p["stop_atr"] * df["atr_14"], exit_long=chg >= 0)


def curve_steepener(df, p):
    if not _has(df, "m_DGS10", "m_DGS2"):
        return signals(df)
    curve = df["m_DGS10"] - df["m_DGS2"]
    chg = curve - curve.shift(p["days"])
    return signals(df, long=cross_up(chg, p["threshold"]), stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])


def dollar_yield_combo(df, p):
    if not _has(df, "m_DTWEXBGS", "m_DFII10"):
        return signals(df)
    d = df["m_DTWEXBGS"] - df["m_DTWEXBGS"].shift(p["days"])
    r = df["m_DFII10"] - df["m_DFII10"].shift(p["days"])
    both_down, both_up = (d < 0) & (r < 0), (d > 0) & (r > 0)
    return signals(df, long=both_down & ~both_down.shift(1, fill_value=False), short=both_up & ~both_up.shift(1, fill_value=False),
                   stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])


def vix_regime(df, p):
    if not _has(df, "m_VIXCLS"):
        return signals(df)
    v = df["m_VIXCLS"]
    return signals(df, long=cross_up(v, p["enter"]), stop=p["stop_atr"] * df["atr_14"], exit_long=v < p["exit"])


def gvz_panic(df, p):
    if not _has(df, "m_GVZCLS"):
        return signals(df)
    g = df["m_GVZCLS"]
    z = (g - g.rolling(p["window"], min_periods=p["window"] // 2).mean()) / g.rolling(p["window"], min_periods=p["window"] // 2).std()
    return signals(df, long=cross_up(z, p["z"]) & (df["close"] < df["sma_50"]), stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])


def cpi_surprise(df, p):
    if not _has(df, "m_CPI_SURPRISE"):
        return signals(df)
    s = df["m_CPI_SURPRISE"]
    new = s.ne(s.shift(1)) & s.notna()
    return signals(df, long=new & (s > p["threshold"]), short=new & (s < -p["threshold"]), stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])


# ---------------------------------------------------------------- Positioning
def cot_contrarian(df, p):
    if not _has(df, "cot_mm_z"):
        return signals(df)
    z = df["cot_mm_z"]
    return signals(df, long=cross_down(z, -p["z"]), short=cross_up(z, p["z"]), stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])


def cot_momentum(df, p):
    if not _has(df, "m_COT_MM_NET"):
        return signals(df)
    net = df["m_COT_MM_NET"]
    report = net.ne(net.shift(1)) & net.notna()
    values = net[report]
    rising = (values.diff() > 0).rolling(p["reports"]).sum() == p["reports"]
    falling = (values.diff() < 0).rolling(p["reports"]).sum() == p["reports"]
    long = pd.Series(False, index=df.index)
    short = pd.Series(False, index=df.index)
    long.loc[rising[rising].index] = True
    short.loc[falling[falling].index] = True
    return signals(df, long=long, short=short, stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])


# ---------------------------------------------------------------- Cross-market
def gold_silver_ratio(df, p):
    if not _has(df, "xagusd_close"):
        return signals(df)
    ratio = np.log(df["close"] / df["xagusd_close"])
    w = p["window"]
    z = (ratio - ratio.rolling(w, min_periods=w).mean()) / ratio.rolling(w, min_periods=w).std()
    return signals(df, long=cross_down(z, -p["z"]), short=cross_up(z, p["z"]), stop=p["stop_atr"] * df["atr_14"],
                   exit_long=z > -p["z_exit"], exit_short=z < p["z_exit"])


def silver_lead(df, p):
    if not _has(df, "xagusd_close"):
        return signals(df)
    s_ret, g_ret = np.log(df["xagusd_close"]).diff(), np.log(df["close"]).diff()
    s_sd, g_sd = s_ret.rolling(p["window"], min_periods=p["window"]).std(), g_ret.rolling(p["window"], min_periods=p["window"]).std()
    jump = s_ret.abs() > p["k"] * s_sd
    quiet = g_ret.abs() < p["gold_k"] * g_sd
    return signals(df, long=jump & quiet & (s_ret > 0), short=jump & quiet & (s_ret < 0), stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])


def dollar_catchup(df, p):
    if not _has(df, "dollaridxusd_close"):
        return signals(df)
    n = p["n"]
    d_ret = df["dollaridxusd_close"] / df["dollaridxusd_close"].shift(n) - 1
    g_ret = df["close"] / df["close"].shift(n) - 1
    long = (d_ret < -p["dollar_move"]) & (g_ret < p["gold_max"])
    short = (d_ret > p["dollar_move"]) & (g_ret > -p["gold_max"])
    return signals(df, long=long & ~long.shift(1, fill_value=False), short=short & ~short.shift(1, fill_value=False),
                   stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])


def dollar_breakout(df, p):
    if not _has(df, "dollaridxusd_close"):
        return signals(df)
    d = df["dollaridxusd_close"]
    hi, lo = d.shift(1).rolling(p["n"], min_periods=p["n"]).max(), d.shift(1).rolling(p["n"], min_periods=p["n"]).min()
    return signals(df, long=cross_down(d, lo), short=cross_up(d, hi), stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])


def eurusd_confirm(df, p):
    if not _has(df, "eurusd_close"):
        return signals(df)
    e = df["eurusd_close"]
    n = p["n"]
    e_up = e > e.shift(1).rolling(n, min_periods=n).max()
    e_down = e < e.shift(1).rolling(n, min_periods=n).min()
    g_up, g_down = df["close"] > prior_high(df, n), df["close"] < prior_low(df, n)
    return signals(df, long=g_up & e_up, short=g_down & e_down, stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])


def return_trigger(symbol: str, n: int, threshold: float, side: int):
    """Enter gold on `side` when `symbol` has returned less than -threshold over n bars (a sell-off)."""
    def prepare(df, p):
        col = f"{symbol}_close"
        if not _has(df, col):
            return signals(df)
        ret = df[col] / df[col].shift(n) - 1
        trig = cross_down(ret, -threshold)
        return signals(df, **({"long": trig} if side > 0 else {"short": trig}), stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])
    return prepare


def oil_inflation(df, p):
    if not _has(df, "m_DCOILWTICO"):
        return signals(df)
    oil = df["m_DCOILWTICO"].where(df["m_DCOILWTICO"] > 0)
    ret = oil / oil.shift(p["days"]) - 1
    return signals(df, long=cross_up(ret, p["threshold"]), stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])


D1 = ["XAUUSD daily bars"]
ENTRIES = [
    entry("cat_real_yield_momentum_d1", "Real-yield moves (10-year TIPS)", "d1", dict(stop_atr=3.0, hold=10), change_rule("DFII10", 20, 0.15, -1),
          "Long gold after the 10-year real yield falls 0.15 points in 20 days; short after it rises as much.",
          family=MAC, batch=B, source="Erb and Harvey, 'The Golden Dilemma' (NBER w18706, 2013), https://www.nber.org/papers/w18706",
          logic="Gold pays no yield, so falling real yields make it more attractive and rising ones less.",
          entry_rule="Long when DFII10 - DFII10 20 days earlier crosses below -0.15; short when it crosses above +0.15.",
          exit_rule="Exit after 10 days.", stop_rule=f"3 x {ATR}.", data=D1 + ["FRED DFII10 (10-year TIPS yield)"], macro=["DFII10"]),
    entry("cat_real_yield_trend_d1", "Falling real-yield regime with gold uptrend", "d1", dict(ry_sma=120, stop_atr=3.0), real_yield_trend,
          "Long gold while the real yield is below its 120-day average and gold is above its 50-day average.",
          family=MAC, batch=B, source="Erb and Harvey (2013); regime form is own idea",
          logic="Hold gold only when both the macro tailwind and the price trend agree.",
          entry_rule="Long when DFII10 < its 120-day SMA and the close > SMA(50), on the first day both hold. Long only.",
          exit_rule="Exit when either condition fails.", stop_rule=f"3 x {ATR}.", data=D1 + ["FRED DFII10"], macro=["DFII10"]),
    entry("cat_dollar_momentum_d1", "Broad-dollar moves", "d1", dict(stop_atr=3.0, hold=10), change_rule("DTWEXBGS", 20, 0.01, -1, relative=True),
          "Long gold after the broad trade-weighted dollar falls 1% in 20 days; short after it rises 1%.",
          family=MAC, batch=B, source="Gold-dollar inverse relationship, e.g. Capie, Mills and Wood, 'Gold as a Hedge against the Dollar', J. Int. Financial Markets 15(4), 2005",
          logic="Gold is priced in dollars, so a weaker dollar tends to lift it.",
          entry_rule="Long when the 20-day % change in DTWEXBGS crosses below -1%; short when it crosses above +1%.",
          exit_rule="Exit after 10 days.", stop_rule=f"3 x {ATR}.", data=D1 + ["FRED DTWEXBGS (weekly, assumed 8-day lag)"], macro=["DTWEXBGS"],
          limitations=["DTWEXBGS is weekly with an assumed 8-day publication lag, so the signal is slow."]),
    entry("cat_dollar_yield_combo_d1", "Dollar and real yield falling together", "d1", dict(days=5, stop_atr=3.0, hold=5), dollar_yield_combo,
          "Long gold when both the dollar and the real yield fell over five days; short when both rose.",
          family=MAC, batch=B, source="Own idea combining the two main gold macro drivers",
          logic="When both main headwinds ease at once, gold should do well; when both tighten, badly.",
          entry_rule="Long on the first day both DTWEXBGS and DFII10 are below their values 5 days earlier; short on the first day both are above.",
          exit_rule="Exit after 5 days.", stop_rule=f"3 x {ATR}.", data=D1 + ["FRED DTWEXBGS", "FRED DFII10"], macro=["DTWEXBGS", "DFII10"]),
    entry("cat_breakeven_trend_d1", "Breakeven inflation moves", "d1", dict(stop_atr=3.0, hold=10), change_rule("T10YIE", 20, 0.10, 1),
          "Long gold after 10-year breakeven inflation rises 0.10 points in 20 days; short after it falls as much.",
          family=MAC, batch=B, source="Gold as an inflation hedge, e.g. Bampinas and Panagiotidis, 'Are Gold and Silver a Hedge against Inflation?', Int. Review of Financial Analysis 41, 2015",
          logic="Rising inflation expectations are said to lift gold.",
          entry_rule="Long when T10YIE - its value 20 days earlier crosses above +0.10; short when it crosses below -0.10.",
          exit_rule="Exit after 10 days.", stop_rule=f"3 x {ATR}.", data=D1 + ["FRED T10YIE"], macro=["T10YIE"]),
    entry("cat_fed_cut_cycle_d1", "Fed cutting cycle", "d1", dict(days=60, threshold=0.25, stop_atr=4.0), fed_cut_cycle,
          "Long gold while the fed funds rate is more than 0.25 points below its level 60 days earlier.",
          family=MAC, batch=B, source="Commonly claimed 'gold rallies in easing cycles'; own exact rule",
          logic="Rate cuts lower the cost of holding gold.",
          entry_rule="Long on the first day DFF - DFF 60 days earlier < -0.25. Long only.", exit_rule="Exit when that change is back at or above zero.",
          stop_rule=f"4 x {ATR}, catastrophic.", data=D1 + ["FRED DFF"], macro=["DFF"],
          limitations=["Few cutting cycles since 2003: expect few trades."]),
    entry("cat_curve_steepener_d1", "Yield-curve steepening", "d1", dict(days=20, threshold=0.15, stop_atr=3.0, hold=20), curve_steepener,
          "Long gold after the 10-year minus 2-year spread steepens by 0.15 points in 20 days.",
          family=MAC, batch=B, source="Own idea (steepening as an easing/inflation signal)",
          logic="A steepening curve often signals expected easing or rising inflation, both said to help gold.",
          entry_rule="Long when (DGS10 - DGS2) minus its value 20 days earlier crosses above +0.15. Long only.",
          exit_rule="Exit after 20 days.", stop_rule=f"3 x {ATR}.", data=D1 + ["FRED DGS10", "FRED DGS2"], macro=["DGS10", "DGS2"]),
    entry("cat_vix_regime_d1", "High-VIX regime", "d1", dict(enter=30.0, exit=25.0, stop_atr=3.0), vix_regime,
          "Long gold when the VIX rises above 30 until it falls back below 25.",
          family=MAC, batch=B, source="Baur and Lucey, 'Is Gold a Hedge or a Safe Haven?', Financial Review 45(2), 2010",
          logic="In market stress gold is said to act as a safe haven.",
          entry_rule="Long when the VIX close crosses above 30. Long only.", exit_rule="Exit when the VIX is below 25.",
          stop_rule=f"3 x {ATR}.", data=D1 + ["FRED VIXCLS"], macro=["VIXCLS"]),
    entry("cat_gvz_panic_d1", "Gold-volatility spike reversal", "d1", dict(window=250, z=2.0, stop_atr=3.0, hold=10), gvz_panic,
          "Long gold when its own implied volatility spikes while price is below the 50-day average.",
          family=MAC, batch=B, source="Own idea (panic reversal using CBOE's gold volatility index)",
          logic="A spike in gold's implied volatility during a fall often marks capitulation.",
          entry_rule="Long when the 250-day z-score of GVZ crosses above 2 and the close is below SMA(50). Long only.",
          exit_rule="Exit after 10 days.", stop_rule=f"3 x {ATR}.", data=D1 + ["FRED GVZCLS (CBOE Gold ETF Volatility Index)"], macro=["GVZCLS"]),
    entry("cat_oil_inflation_d1", "Oil surge as an inflation signal", "d1", dict(days=20, threshold=0.10, stop_atr=3.0, hold=20), oil_inflation,
          "Long gold after WTI crude rises 10% in 20 days.",
          family=MAC, batch=B, source="Commonly claimed oil-gold inflation link; own exact rule",
          logic="A sharp oil rise feeds inflation fears, which are said to lift gold.",
          entry_rule="Long when WTI (FRED DCOILWTICO) / its value 20 days earlier - 1 crosses above 10%. Long only.",
          exit_rule="Exit after 20 days.", stop_rule=f"3 x {ATR}.", data=D1 + ["FRED DCOILWTICO (assumed 8-day lag)"], macro=["DCOILWTICO"]),
    entry("cat_cpi_surprise_d1", "CPI surprise", "d1", dict(threshold=0.1, stop_atr=3.0, hold=5), cpi_surprise,
          "Trades gold in the direction of a CPI surprise versus consensus.",
          family=MAC, batch=B, source="Event-study literature on gold and macro news, e.g. Elder, Miao and Ramchander, 'Impact of Macroeconomic News on Metal Futures', J. Banking & Finance 36(1), 2012",
          logic="Higher-than-expected inflation is said to lift gold.",
          entry_rule="On the release day: long if actual CPI m/m minus consensus > +0.1 points, short if < -0.1.",
          exit_rule="Exit after 5 days.", stop_rule=f"3 x {ATR}.", data=D1 + ["CPI first releases and consensus forecasts"],
          missing=["CPI consensus forecasts and first-release values (no free source; FRED has revised values only)"], macro=["CPI_SURPRISE"]),
    entry("cat_cot_contrarian_d1", "COT managed-money extremes (contrarian)", "d1", dict(z=2.0, stop_atr=3.0, hold=10), cot_contrarian,
          "Fades extreme managed-money positioning in gold futures.",
          family=MAC, batch=B, source="Common sentiment rule; see e.g. Sanders, Boris and Manfredo, 'Hedgers, Funds, and Small Speculators in the Energy Futures Markets', Energy Economics 26(3), 2004",
          logic="When funds are extremely long, few buyers remain, so the price is vulnerable.",
          entry_rule="Long when the 104-week z-score of CFTC managed-money net (published Friday 15:30 New York) crosses below -2; short when it crosses above +2.",
          exit_rule="Exit after 10 days.", stop_rule=f"3 x {ATR}.", data=D1 + ["CFTC disaggregated COT, gold"]),
    entry("cat_cot_momentum_d1", "COT managed-money trend", "d1", dict(reports=3, stop_atr=3.0, hold=5), cot_momentum,
          "Follows three consecutive weekly increases (or decreases) in managed-money net length.",
          family=MAC, batch=B, source="Own idea (flow persistence in positioning)",
          logic="Funds adding to positions week after week may keep buying.",
          entry_rule="On a new COT publication: long if managed-money net rose in each of the last 3 reports; short if it fell in each.",
          exit_rule="Exit after 5 days.", stop_rule=f"3 x {ATR}.", data=D1 + ["CFTC disaggregated COT, gold"], macro=["COT_MM_NET"]),
    entry("cat_gold_silver_ratio_d1", "Gold/silver ratio extremes", "d1", dict(window=250, z=2.0, z_exit=0.5, stop_atr=3.0), gold_silver_ratio,
          "Trades gold against extremes of the gold/silver ratio's 250-day z-score.",
          family=XM, batch=B, source="Commonly traded gold/silver ratio reversion; e.g. Baur and Tran, 'The Long-run Relationship of Gold and Silver', Applied Economics Letters 21(13), 2014",
          logic="When gold is unusually expensive relative to silver, the ratio tends to fall back, which can mean gold falls.",
          entry_rule="z = 250-day z-score of ln(gold / silver). Short gold when z crosses above 2; long gold when it crosses below -2.",
          exit_rule="Exit when |z| < 0.5.", stop_rule=f"3 x {ATR}.", data=D1 + ["silver (XAGUSD) H1 closes"], needs=["xagusd"],
          limitations=["Only gold is traded, so this is relative-value timing, not a hedged spread.",
                       "Silver comes from Dukascopy where available, otherwise MT5 (Exness) from mid-2018."]),
    entry("cat_silver_lead_h1", "Silver jump, gold lag", "h1", dict(window=100, k=2.0, gold_k=0.5, stop_atr=1.5, hold=3), silver_lead,
          "Follows a large hourly silver move that gold has not yet made.",
          family=XM, batch=B, source="Own idea (lead-lag between related metals)",
          logic="Silver sometimes moves first; gold may follow within a few hours.",
          entry_rule="If silver's 1-bar log return exceeds 2 x its 100-bar SD while gold's is under 0.5 x its own SD: enter gold in silver's direction.",
          exit_rule="Exit after 3 bars.", stop_rule=f"1.5 x {ATR}.", data=["XAUUSD H1 bars", "silver (XAGUSD) H1 closes"], needs=["xagusd"]),
    entry("cat_dollar_catchup_h1", "Gold catching up with a dollar move", "h1", dict(n=24, dollar_move=0.005, gold_max=0.002, stop_atr=2.0, hold=12), dollar_catchup,
          "Long gold when the dollar index fell 0.5% in a day but gold has not risen, and the mirror.",
          family=XM, batch=B, source="Own idea (gold-dollar lag)",
          logic="If the dollar has weakened and gold has not reacted yet, gold may catch up.",
          entry_rule="Over 24 H1 bars: long when the dollar index is down more than 0.5% and gold is up less than 0.2%; short mirrored. First bar of each condition.",
          exit_rule="Exit after 12 bars.", stop_rule=f"2 x {ATR}.", data=["XAUUSD H1 bars", "dollar index H1 closes"], needs=["dollaridxusd"]),
    entry("cat_dollar_breakout_h4", "Dollar-index channel break", "h4", dict(n=55, stop_atr=2.0, hold=30), dollar_breakout,
          "Long gold when the dollar index breaks its 55-bar low; short when it breaks its 55-bar high.",
          family=XM, batch=B, source="Own idea (trend in the dollar transmitted to gold)",
          logic="A new dollar trend is a headwind or tailwind for gold.",
          entry_rule="Long gold when the dollar index close crosses below its prior 55-bar low; short when it crosses above its prior 55-bar high.",
          exit_rule="Exit after 30 bars.", stop_rule=f"2 x {ATR}.", data=["XAUUSD H4 bars", "dollar index H1 closes"], needs=["dollaridxusd"]),
    entry("cat_eurusd_confirm_h1", "Gold breakout confirmed by EURUSD", "h1", dict(n=20, stop_atr=2.0, hold=24), eurusd_confirm,
          "Takes gold 20-bar breakouts only when EURUSD breaks the same way (dollar weakness).",
          family=XM, batch=B, source="Own idea (dollar confirmation of gold moves)",
          logic="A gold breakout backed by a weaker dollar is more likely to be real.",
          entry_rule="Long when gold closes above its prior 20-bar high and EURUSD above its prior 20-bar high; short when both break their lows.",
          exit_rule="Exit after 24 bars.", stop_rule=f"2 x {ATR}.", data=["XAUUSD H1 bars", "EURUSD H1 closes"], needs=["eurusd"]),
    entry("cat_usdjpy_riskoff_h1", "Yen-strength risk-off", "h1", dict(stop_atr=2.0, hold=24), return_trigger("usdjpy", 24, 0.01, 1),
          "Long gold when USDJPY falls 1% in 24 hours.",
          family=XM, batch=B, source="Own idea (yen and gold as twin havens)",
          logic="A sharp yen rally signals risk aversion, which may also lift gold.",
          entry_rule="Long gold when USDJPY's 24-bar return crosses below -1%. Long only.",
          exit_rule="Exit after 24 bars.", stop_rule=f"2 x {ATR}.", data=["XAUUSD H1 bars", "USDJPY H1 closes"], needs=["usdjpy"]),
    entry("cat_spx_selloff_d1", "Equity sell-off flight to gold", "d1", dict(stop_atr=3.0, hold=5), return_trigger("usa500idxusd", 5, 0.03, 1),
          "Long gold after the S&P 500 falls 3% in five days.",
          family=XM, batch=B, source="Baur and Lucey (2010), gold as a safe haven against equities",
          logic="When stocks fall hard, money may move into gold.",
          entry_rule="Long gold when the S&P 500's 5-day return crosses below -3%. Long only.",
          exit_rule="Exit after 5 days.", stop_rule=f"3 x {ATR}.", data=D1 + ["S&P 500 CFD H1 closes"], needs=["usa500idxusd"],
          limitations=["The S&P series is MT5's from mid-2023 only (Dukascopy refuses), so there is little data."]),
]
