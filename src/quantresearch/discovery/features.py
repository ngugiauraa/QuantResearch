"""Discovery features: the leaves a generated strategy may read.

Every leaf is scale-free (a ratio, z-score, rank, distance in ATR or a calendar value), so a threshold learned in one
decade still means something in the next. Raw price levels are never leaves.

Each feature carries:
  family        what kind of market behaviour it describes
  availability  when it is known: bar_close, published (external data by publication time), or calendar
                (a schedule knowable in advance)
  quality       observed, published_schedule, assumed_lag (FRED market series, unrevised in practice),
                or approximate_calendar

Excluded on purpose, and refused by the strategy validator:
  - CPI, PCE, payrolls, unemployment, GDP: FRED gives only today's revised values, so using them would be using
    revisions as if they had been known at the time. No vintage source is available without an API key.
  - The broad trade-weighted dollar index (DTWEXBGS), which is revised. The dollar index CFD, observed at bar close,
    is used instead.
Everything here is built from bars closed by each row's close_ts. Truncation tests prove it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantresearch.core import timeframes as tfs
from quantresearch.features import pit
from quantresearch.features.registry import build_features
from quantresearch.strategies.base import rolling_rank, trading_day_position
from quantresearch.strategies.engines import gap_measures, spread_model
from quantresearch.warehouse import calendar as cal

FORBIDDEN_SERIES = ("CPIAUCSL", "PCEPI", "PAYEMS", "UNRATE", "GDP", "DTWEXBGS")
FORBIDDEN_LEAVES = ("cpi", "pce", "payrolls", "unemployment", "gdp", "dollar_broad")
CROSS = ("xagusd", "eurusd", "usdjpy", "dollaridxusd", "ustbondtrusd")
HIGHER = {"m15": ("h1", "h4", "d1ny"), "h1": ("h4", "d1ny"), "h4": ("d1ny",), "d1ny": ()}
INTRADAY = ("m15", "h1")
PRICE_COLUMNS = ("bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close",
                 "open", "high", "low", "close", "atr_14")


class Catalog:
    def __init__(self):
        self.columns: dict[str, np.ndarray] = {}
        self.meta: dict[str, dict] = {}

    def add(self, name: str, values, family: str, definition: str, availability: str = "bar_close",
            quality: str = "observed") -> None:
        if any(name.startswith(f) or name == f for f in FORBIDDEN_LEAVES):
            raise ValueError(f"{name} is a forbidden leaf")
        arr = np.asarray(values, dtype="float64")
        arr = np.where(np.isfinite(arr), arr, np.nan).astype("float32")
        self.columns[name] = arr
        self.meta[name] = {"family": family, "definition": definition, "availability": availability, "quality": quality}


def _div(a, b):
    b = pd.Series(b)
    return pd.Series(a) / b.where(b.abs() > 0)


def ny_frame(ts: pd.Series) -> pd.DataFrame:
    ny = ts.dt.tz_convert("America/New_York")
    return pd.DataFrame({"minute": ny.dt.hour * 60 + ny.dt.minute, "dow": ny.dt.day_of_week}, index=ts.index)


def window_levels(df: pd.DataFrame, day: pd.Series, zone: str, begin: int, end: int, prefix: str) -> dict[str, pd.Series]:
    """Running high and low of bars that CLOSED inside a local-time window of the current trading day.

    A bar counts once it has closed at or before the window's end, so the level at any row uses closed bars only.
    After the window ends the level is fixed for the rest of the day; `complete` says when.
    """
    local_open = df["ts"].dt.tz_convert(zone)
    local_close = df["close_ts"].dt.tz_convert(zone)
    start_min = local_open.dt.hour * 60 + local_open.dt.minute
    close_min = local_close.dt.hour * 60 + local_close.dt.minute
    close_min = close_min.where(close_min > 0, 24 * 60)
    inside = (start_min >= begin) & (close_min <= end) & (start_min < end)
    hi = df["high"].where(inside).groupby(day).cummax().groupby(day).ffill()
    lo = df["low"].where(inside).groupby(day).cummin().groupby(day).ffill()
    complete = (close_min >= end) | (start_min >= end)
    started = inside.groupby(day).cummax().astype(bool)
    complete = complete & started
    return {f"{prefix}_high": hi, f"{prefix}_low": lo, f"{prefix}_complete": complete.astype(float)}


def first_friday_nfp(index_ts: pd.Series) -> pd.Series:
    """Approximate payroll release times: first Friday of the month, 08:30 New York.

    Rule-based and knowable in advance, but real dates sometimes differ (holidays, shutdowns). Labelled approximate.
    """
    years = range(index_ts.dt.year.min() - 1, index_ts.dt.year.max() + 2)
    stamps = []
    for y in years:
        for m in range(1, 13):
            d = pd.Timestamp(year=y, month=m, day=1)
            d = d + pd.Timedelta(days=(4 - d.weekday()) % 7)
            stamps.append(d.tz_localize("America/New_York") + pd.Timedelta(hours=8, minutes=30))
    return pd.Series(pd.DatetimeIndex(stamps).tz_convert("UTC"))


def hours_since(times: pd.Series, schedule: pd.Series) -> np.ndarray:
    grid = np.sort(pd.DatetimeIndex(schedule).as_unit("ns").asi8)
    now = pd.DatetimeIndex(times).as_unit("ns").asi8
    pos = np.searchsorted(grid, now, side="right") - 1
    out = np.full(len(now), np.nan)
    ok = pos >= 0
    out[ok] = (now[ok] - grid[pos[ok]]) / 3.6e12
    return out


def compact_htf(frame: pd.DataFrame) -> pd.DataFrame:
    """The higher-timeframe view attached to lower timeframes."""
    f = build_features(frame) if "rsi_14" not in frame else frame
    atr = f["atr_14"]
    return pd.DataFrame({
        "rsi": f["rsi_14"], "z20": f["zscore_20"], "adx": f["adx"],
        "slope": _div(f["slope_20"], f["vol_20"]),
        "ema_gap": _div(f["ema_20"] - f["ema_50"], atr),
        "dist_sma50": _div(f["close"] - f["sma_50"], atr),
        "dist_dch20": _div(f["close"] - f["donchian_high_20"], atr),
        "dist_dcl20": _div(f["close"] - f["donchian_low_20"], atr),
        "ret_z": _div(f["logret_1"], f["vol_60"]),
        "vol_rank": rolling_rank(f["vol_20"], 250),
        "trend": f["structure_trend"], "bb_pct": f["bb_pct"],
        "close_ts": f["close_ts"],
    }, index=f.index)


def build_catalog(tf: str, bars: pd.DataFrame, higher: dict[str, pd.DataFrame] | None = None,
                  cross: dict[str, pd.DataFrame] | None = None, events: pd.DataFrame | None = None) -> tuple[pd.DataFrame, Catalog]:
    """Return (prices frame, catalog of leaf features). `bars` must be sorted and end before the holdout."""
    f = build_features(bars.reset_index(drop=True))
    cat = Catalog()
    c, atr = f["close"], f["atr_14"]
    vol60 = f["vol_60"]

    # ---------------------------------------------------------------- trend and momentum
    for k in (1, 3, 6, 12, 24, 60):
        r = np.log(c / c.shift(k))
        cat.add(f"ret_z{k}", _div(r, vol60 * np.sqrt(k)), "momentum", f"log return over {k} bars in units of 60-bar volatility x sqrt({k})")
    cat.add("ema_gap", _div(f["ema_20"] - f["ema_50"], atr), "trend", "(EMA20 - EMA50) / ATR14")
    for n in (20, 50, 200):
        cat.add(f"dist_sma{n}", _div(c - f[f"sma_{n}"], atr), "trend", f"(close - SMA{n}) / ATR14")
    cat.add("slope20", _div(f["slope_20"], f["vol_20"]), "trend", "20-bar log-price slope / 20-bar volatility")
    cat.add("adx", f["adx"], "trend", "Wilder ADX 14")
    cat.add("di_diff", f["plus_di"] - f["minus_di"], "trend", "+DI - -DI")
    for n in (20, 55):
        cat.add(f"dist_dch{n}", _div(c - f[f"donchian_high_{n}"], atr), "breakout", f"(close - highest high of prior {n}) / ATR")
        cat.add(f"dist_dcl{n}", _div(c - f[f"donchian_low_{n}"], atr), "breakout", f"(close - lowest low of prior {n}) / ATR")
    sign = np.sign(c.diff()).fillna(0)
    run_id = (sign != sign.shift()).cumsum()
    cat.add("streak", sign * sign.groupby(run_id).cumcount().add(1), "momentum", "signed count of consecutive closes in one direction")

    # ---------------------------------------------------------------- mean reversion and oscillators
    cat.add("rsi14", f["rsi_14"], "mean_reversion", "Wilder RSI 14")
    cat.add("z20", f["zscore_20"], "mean_reversion", "z-score of close, 20 bars")
    cat.add("z60", f["zscore_60"], "mean_reversion", "z-score of close, 60 bars")
    cat.add("bb_pct", f["bb_pct"], "mean_reversion", "position in 20-bar 2-sd Bollinger band")

    # ---------------------------------------------------------------- volatility
    cat.add("vol_ratio", f["vol_ratio"], "volatility", "vol20 / vol60")
    cat.add("atr_ratio", f["atr_ratio"], "volatility", "ATR14 / ATR50")
    cat.add("vol_rank", rolling_rank(f["vol_20"], 500), "volatility", "percentile of vol20 in the last 500 bars")
    cat.add("atr_pct", _div(atr, c) * 100, "volatility", "ATR14 as % of price")
    cat.add("bb_width_rank", rolling_rank(f["bb_width"], 500), "volatility", "percentile of Bollinger width, 500 bars (squeeze)")
    tr_mean = f["tr"].rolling(100, min_periods=100).mean()
    tr_sd = f["tr"].rolling(100, min_periods=100).std()
    cat.add("range_z", _div(f["tr"] - tr_mean, tr_sd), "volatility", "z-score of true range, 100 bars")
    span = (f["high"] - f["low"])
    cat.add("close_pos", _div(c - f["low"], span), "price_action", "close location in the bar's range")
    cat.add("body_frac", _div(c - f["open"], span), "price_action", "(close - open) / range")
    cat.add("upper_wick", _div(f["high"] - np.maximum(f["open"], c), span), "price_action", "upper wick / range")
    cat.add("lower_wick", _div(np.minimum(f["open"], c) - f["low"], span), "price_action", "lower wick / range")
    cat.add("vol_cluster", f["vol_cluster"], "volatility", "autocorrelation of |return|, 60 bars")

    # ---------------------------------------------------------------- structure, liquidity sweeps, levels
    for name in ("bos_up", "bos_down", "choch_up", "choch_down", "sweep_high", "sweep_low", "fvg_up", "fvg_down",
                 "ifvg_up", "ifvg_down", "equal_highs", "equal_lows"):
        cat.add(name, f[name], "structure", f"project rule: {name} (see features/structure.py)")
    cat.add("structure_trend", f["structure_trend"], "structure", "+1/-1 from the latest structure break")
    cat.add("premium_discount", f["premium_discount"], "structure", "position of close in the last swing range")
    cat.add("dist_swing_high", _div(c - f["swing_high"], atr), "structure", "(close - last confirmed swing high) / ATR")
    cat.add("dist_swing_low", _div(c - f["swing_low"], atr), "structure", "(close - last confirmed swing low) / ATR")
    cat.add("dist_fvg_up", _div(c - f["fvg_up_high"], atr), "structure", "(close - nearest bullish FVG top) / ATR")
    cat.add("dist_fvg_down", _div(f["fvg_down_low"] - c, atr), "structure", "(nearest bearish FVG bottom - close) / ATR")
    for name, col in (("pdh", "pdh"), ("pdl", "pdl"), ("pdc", "pdc"), ("day_high", "day_high"), ("day_low", "day_low")):
        cat.add(f"dist_{name}", _div(c - f[col], atr), "levels", f"(close - {col}) / ATR; previous-day levels appear after the day ends")
    cat.add("day_range_atr", _div(f["day_high"] - f["day_low"], atr), "levels", "today's range so far / ATR")
    prev_range = f["pdh"] - f["pdl"]
    cat.add("pd_range_atr", _div(prev_range, atr), "levels", "previous day's range / ATR")

    day = tfs.bucket(f["ts"], "d1ny")
    day_open = f["open"].groupby(day).transform("first")
    cat.add("ret_day_atr", _div(c - day_open, atr), "session", "(close - trading-day open) / ATR")
    trading_date = (day.dt.tz_convert("America/New_York") + pd.Timedelta(hours=7)).dt.tz_localize(None).dt.normalize()
    week = trading_date - pd.to_timedelta(trading_date.dt.day_of_week, unit="D")
    weekly = pd.DataFrame({"h": f["high"].groupby(week).max(), "l": f["low"].groupby(week).min()}).shift(1)
    cat.add("dist_pwh", _div(c - week.map(weekly["h"]).astype(float), atr), "levels", "(close - previous week high) / ATR")
    cat.add("dist_pwl", _div(c - week.map(weekly["l"]).astype(float), atr), "levels", "(close - previous week low) / ATR")

    # ---------------------------------------------------------------- gaps and shocks
    g = gap_measures(f)
    cat.add("gap_atr", g["gap_atr"].fillna(0.0).where(f["atr_14"].shift(1).notna()), "gap", "open gap after a >6h closure / prior ATR; 0 otherwise")
    cat.add("shock_z", g["shock_z"], "overreaction", "1-bar log return / prior 60-bar volatility")

    # ---------------------------------------------------------------- microstructure (provider quotes)
    cat.add("spread_z", f["spread_z"], "microstructure", "z-score of close spread, 60 bars")
    cat.add("spread_rank", rolling_rank(f["spread_bps"], 500), "microstructure", "percentile of spread in bps, 500 bars")
    cat.add("volume_ratio", f["volume_ratio"], "microstructure", "provider activity / its 60-bar mean")
    cat.add("quote_imbalance", f["quote_imbalance"], "microstructure", "(ask volume - bid volume) / total")
    act = f["activity"]
    cat.add("activity_ratio", _div(act, act.rolling(100, min_periods=50).mean()), "microstructure", "quoted M1 bars / 100-bar mean")

    # ---------------------------------------------------------------- calendar and sessions (known in advance)
    ny = ny_frame(f["ts"])
    cat.add("hour_utc", f["ts"].dt.hour, "calendar", "bar open hour, UTC", "calendar")
    cat.add("ny_hour", ny["minute"] // 60, "calendar", "bar open hour, New York", "calendar")
    cat.add("dow", ny["dow"], "calendar", "New York weekday of bar open, 0=Mon", "calendar")
    cat.add("month", f["ts"].dt.month, "calendar", "month", "calendar")
    pos = trading_day_position(f["ts"])
    cat.add("tdom", pos["tdom"], "calendar", "trading day of month", "calendar")
    cat.add("tdoe", pos["tdoe"], "calendar", "trading days to month end", "calendar")
    flags = cal.session_flags(f["ts"])
    for name in ("asia", "london", "new_york"):
        cat.add(f"sess_{name}", flags[name].astype(float), "session", f"bar opens inside the {name} session", "calendar")
    cat.add("sess_overlap", (flags["london"] & flags["new_york"]).astype(float), "session", "London and New York overlap", "calendar")
    ends = tfs.bucket(f["close_ts"] - pd.Timedelta(nanoseconds=1), "d1ny") != tfs.bucket(f["close_ts"], "d1ny")
    cat.add("day_end", ends.astype(float), "session", "bar closes at the 17:00 New York roll", "calendar")

    if tf in INTRADAY:
        levels = {}
        levels.update(window_levels(f, day, "Asia/Tokyo", 9 * 60, 15 * 60, "asia"))
        levels.update(window_levels(f, day, "Europe/London", 8 * 60, 9 * 60, "ldn_or"))
        # First New York hour, 08:00-09:00, containing the 08:20 COMEX open; it aligns with M15 and H1 bar edges.
        levels.update(window_levels(f, day, "America/New_York", 8 * 60, 9 * 60, "ny_or"))
        for p in ("asia", "ldn_or", "ny_or"):
            hi, lo, done = levels[f"{p}_high"], levels[f"{p}_low"], levels[f"{p}_complete"]
            cat.add(f"dist_{p}_high", _div(c - hi, atr), "session_range", f"(close - {p} range high) / ATR, range from closed bars of today")
            cat.add(f"dist_{p}_low", _div(c - lo, atr), "session_range", f"(close - {p} range low) / ATR")
            cat.add(f"{p}_range_atr", _div(hi - lo, atr), "session_range", f"{p} range / ATR")
            cat.add(f"{p}_complete", done, "session_range", f"the {p} window has finished today", "calendar")

    # ---------------------------------------------------------------- higher timeframes, closed bars only
    for htf, frame in (higher or {}).items():
        view = compact_htf(frame)
        cols = [x for x in view.columns if x != "close_ts"]
        aligned = pit.align_higher_tf(f, view, cols, f"{htf}_")
        for col in cols:
            cat.add(f"{htf}_{col}", aligned[f"{htf}_{col}"].to_numpy(), "higher_timeframe", f"{col} of the last CLOSED {htf} bar")

    # ---------------------------------------------------------------- cross-market (provider H1 bars, at bar close)
    gold_r = np.log(c).diff()
    per_hour = max(1, int(3600 // tfs.FIXED_SECONDS[tf])) if tf in tfs.FIXED_SECONDS else 1
    short = max(1, 4 * per_hour) if tf in INTRADAY else 1
    long_ = max(2, 24 * per_hour) if tf in INTRADAY else 5
    for sym, frame in (cross or {}).items():
        aligned = pit.align_higher_tf(f, frame, ["close"], f"{sym}_")[f"{sym}_close"]
        x = np.log(aligned.astype(float))
        for k, label in ((short, "s"), (long_, "l")):
            r = x - x.shift(k)
            sd = r.rolling(500, min_periods=200).std()
            cat.add(f"{sym}_ret{label}", _div(r, sd), "cross_market", f"{sym} log return over {k} bars / its 500-bar sd")
            gr = np.log(c / c.shift(k))
            gsd = gr.rolling(500, min_periods=200).std()
            cat.add(f"{sym}_div{label}", _div(gr, gsd) - _div(r, sd), "cross_market", f"gold z-return minus {sym} z-return, {k} bars")
        cat.add(f"{sym}_corr", gold_r.rolling(100, min_periods=100).corr(x.diff()), "cross_market", f"100-bar return correlation with {sym}")
        if sym == "xagusd":
            ratio = np.log(c) - x
            m, s = ratio.rolling(500, min_periods=200).mean(), ratio.rolling(500, min_periods=200).std()
            cat.add("gsr_z", _div(ratio - m, s), "cross_market", "z-score of log gold/silver ratio, 500 bars")
            tmp = f[["close"]].assign(xagusd_close=aligned.to_numpy())
            cat.add("silver_resid_z", spread_model(tmp, "xagusd", 500)["spread_z"], "cross_market", "residual z of rolling ln(gold) on ln(silver), 500 bars")

    # ---------------------------------------------------------------- macro and events (published time)
    if events is not None and len(events):
        def level(series):
            return pit.align_events(f["close_ts"], events, series)

        def change(series, k=5):
            rows = events[events["series"] == series].sort_values("event_ts").copy()
            rows["value"] = rows["value"].diff(k)
            return pit.align_events(f["close_ts"], rows.dropna(subset=["value"]), series)

        q = "assumed_lag"
        cat.add("real_yield", level("DFII10"), "macro", "10y TIPS yield, by assumed publication time", "published", q)
        cat.add("real_yield_chg5", change("DFII10"), "macro", "5-observation change of 10y TIPS yield", "published", q)
        cat.add("yield10_chg5", change("DGS10"), "macro", "5-observation change of 10y yield", "published", q)
        curve = level("DGS10") - level("DGS2")
        cat.add("curve", curve, "macro", "10y - 2y Treasury yield", "published", q)
        cat.add("breakeven_chg5", change("T10YIE"), "macro", "5-observation change of 10y breakeven", "published", q)
        cat.add("vix", level("VIXCLS"), "macro", "VIX close", "published", q)
        cat.add("vix_chg5", change("VIXCLS"), "macro", "5-observation change of VIX", "published", q)
        cat.add("fed_funds", level("DFF"), "macro", "effective fed funds rate", "published", q)
        fomc = events[events["series"] == "FOMC_DECISION"]["event_ts"]
        if len(fomc):
            from quantresearch.external.store import event_proximity
            cat.add("hours_to_fomc", event_proximity(f["close_ts"], fomc).clip(upper=24 * 60), "event", "hours to the next scheduled FOMC decision", "calendar", "published_schedule")
            cat.add("hours_since_fomc", np.clip(hours_since(f["close_ts"], fomc), None, 24 * 60), "event", "hours since the last FOMC decision", "calendar", "published_schedule")
        nfp = first_friday_nfp(f["close_ts"])
        from quantresearch.external.store import event_proximity
        cat.add("hours_to_nfp", event_proximity(f["close_ts"], nfp).clip(upper=24 * 40), "event", "hours to the approximate payroll release (first Friday 08:30 NY)", "calendar", "approximate_calendar")
        cat.add("hours_since_nfp", np.clip(hours_since(f["close_ts"], nfp), None, 24 * 40), "event", "hours since the approximate payroll release", "calendar", "approximate_calendar")
        cot = events[events["series"] == "COT_MM_NET"].sort_values("event_ts").copy()
        if len(cot):
            v = cot["value"]
            cot["value"] = (v - v.rolling(104, min_periods=52).mean()) / v.rolling(104, min_periods=52).std()
            cat.add("cot_mm_z", pit.align_events(f["close_ts"], cot.dropna(subset=["value"]), "COT_MM_NET"), "positioning",
                    "managed-money net position z-score, 104 weeks, from Friday 15:30 NY publication", "published", "published_schedule")
    prices = f[["ts", "close_ts", *PRICE_COLUMNS]].copy()
    return prices, cat


def null_bars(m15: pd.DataFrame, seed: int) -> pd.DataFrame:
    """A sign-randomised market: every bar keeps its real size, wicks, spread, activity and timing, but the direction
    of each close-to-close move is a fair coin.

    No rule can predict direction in this market, so any candidate that passes validation on it is a false
    discovery. Running the identical pipeline on it measures the pipeline's own false-positive rate.
    """
    rng = np.random.default_rng(seed)
    mid_c = m15["close"].to_numpy(float)
    mid_o = m15["open"].to_numpy(float)
    prev = np.r_[mid_o[0], mid_c[:-1]]
    r_open = np.log(mid_o / prev)
    r_body = np.log(mid_c / mid_o)
    signs = rng.choice([-1.0, 1.0], size=len(m15))
    gap_sign = rng.choice([-1.0, 1.0], size=len(m15))
    new_o, new_c = np.empty(len(m15)), np.empty(len(m15))
    level = mid_o[0]
    for i in range(len(m15)):
        o = level * np.exp(gap_sign[i] * r_open[i]) if i else level
        cl = o * np.exp(signs[i] * r_body[i])
        new_o[i], new_c[i] = o, cl
        level = cl
    real_top = np.maximum(mid_o, mid_c)
    real_bot = np.minimum(mid_o, mid_c)
    up = np.log(m15["high"].to_numpy(float) / real_top)
    dn = np.log(real_bot / m15["low"].to_numpy(float))
    flip = signs < 0
    up2, dn2 = np.where(flip, dn, up), np.where(flip, up, dn)
    new_h = np.maximum(new_o, new_c) * np.exp(up2)
    new_l = np.minimum(new_o, new_c) / np.exp(dn2)
    out = m15[["ts", "bid_volume", "ask_volume", "m1_count", "spread_mean", "spread_max", "flags"]].copy()
    for name, mid in (("open", new_o), ("high", new_h), ("low", new_l), ("close", new_c)):
        s = (m15[f"ask_{name}"] - m15[f"bid_{name}"]).to_numpy(float)
        s = np.where(np.isfinite(s), s, np.nanmedian(s))
        out[f"bid_{name}"] = mid - s / 2
        out[f"ask_{name}"] = mid + s / 2
    out["bid_high"] = np.maximum.reduce([out["bid_high"], out["bid_open"], out["bid_close"]])
    out["bid_low"] = np.minimum.reduce([out["bid_low"], out["bid_open"], out["bid_close"]])
    out["ask_high"] = np.maximum.reduce([out["ask_high"], out["ask_open"], out["ask_close"]])
    out["ask_low"] = np.minimum.reduce([out["ask_low"], out["ask_open"], out["ask_close"]])
    return out


def aggregate(m15: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Higher bars from M15 bars, with the warehouse's aggregation rules."""
    key = tfs.bucket(m15["ts"], tf)
    g = m15.groupby(key, sort=True)
    out = pd.DataFrame({
        "bid_open": g["bid_open"].first(), "bid_high": g["bid_high"].max(), "bid_low": g["bid_low"].min(), "bid_close": g["bid_close"].last(),
        "ask_open": g["ask_open"].first(), "ask_high": g["ask_high"].max(), "ask_low": g["ask_low"].min(), "ask_close": g["ask_close"].last(),
        "bid_volume": g["bid_volume"].sum(), "ask_volume": g["ask_volume"].sum(), "m1_count": g["m1_count"].sum(),
        "spread_mean": g["spread_mean"].mean(), "spread_max": g["spread_max"].max(), "flags": g["flags"].max(),
    })
    out.index.name = "ts"
    return out.reset_index()


def finish_bars(frame: pd.DataFrame, tf: str) -> pd.DataFrame:
    from quantresearch.warehouse.store import _finish
    return _finish(frame.copy(), tf, None)
