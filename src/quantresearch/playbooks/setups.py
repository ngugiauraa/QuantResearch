"""The three personal XAUUSD playbooks, implemented exactly as specified (docs/my_playbooks.md), plus the trade
simulation on M1 bid/ask bars.

    python -m quantresearch.playbooks.setups --years 2005 2026     # every signal and trade, per setup and combined

Signals are found on M5 bars (mid prices). A signal acts at its M5 candle's close and fills at the next M1 bar's
open: the ask for a buy, the bid for a sell. Stops, targets and the breakeven move are checked on M1 bid (longs)
or ask (shorts), stop first when one bar touches both. Any trade still open at 17:00 New York on its entry day is
closed at the last minute's close before 17:00 (the trader's rule, confirmed 2026-11-28).
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from quantresearch.core.config import Paths, paths
from quantresearch.playbooks import primitives as P

NY, LDN = "America/New_York", "Europe/London"
BUFFER_ATR = 0.1
MIN_RR = 2.0
ADX_MIN = 20.0
NEWS_MIN = 30
SPREAD_X = 2.0
ZONE_DAYS = 5
AVG_BODY_N = 20
NORMAL_SPREAD_DAYS = 20
STRESS_EXTRA = 0.60 + 2 * 0.20 + 0.07  # The catalogue's stressed scenario per ounce round trip: spread, slippage both ways, commission.
SETUPS = ("ny_sweep_ifvg", "london_asian_sweep", "fvg_continuation")


def local(day: date, hh: int, mm: int, zone: str) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, time(hh, mm))).tz_localize(zone).tz_convert("UTC")


@dataclass
class Signal:
    setup: str
    day: str
    side: int
    signal_ts: str          # Close of the M5 entry candle (UTC)
    ref_close: float        # That candle's close (mid)
    stop: float
    target_rule: str        # "level_or_2r", "2r" or "level_only"
    target_level: float | None
    context: dict = field(default_factory=dict)


class Frames:
    """One year of bars and everything derived from them, with a warm-up before the year."""

    def __init__(self, year: int, p: Paths, loader=None, events=None):
        from quantresearch.warehouse.store import load_bars

        load = loader or (lambda tf, a, b: load_bars(tf, a, b, p=p))
        a = pd.Timestamp(f"{year}-01-01", tz="UTC") - pd.Timedelta(days=60)
        b = pd.Timestamp(f"{year + 1}-01-01", tz="UTC") + pd.Timedelta(days=2)
        self.year = year
        self.bars = {tf: load(tf, a, b).reset_index(drop=True) for tf in ("m5", "m15", "h1", "h4", "d1", "d1ny")}
        self.m1 = load("m1", pd.Timestamp(f"{year}-01-01", tz="UTC") - pd.Timedelta(days=1), b + pd.Timedelta(days=40)).reset_index(drop=True)
        self.arr = {tf: self._arrays(f) for tf, f in self.bars.items()}
        m5 = self.arr["m5"]
        m5["atr"] = P.atr(m5["h"], m5["l"], m5["c"], 14)
        body = np.abs(m5["c"] - m5["o"])
        m5["body"] = body
        m5["avg_body"] = pd.Series(body).shift(1).rolling(AVG_BODY_N, min_periods=AVG_BODY_N).mean().to_numpy()
        m5["spread"] = (self.bars["m5"]["ask_close"] - self.bars["m5"]["bid_close"]).to_numpy(dtype="float64")
        slot = self.bars["m5"]["ts"].dt.strftime("%H:%M")
        sp = pd.Series(m5["spread"])
        m5["normal_spread"] = sp.groupby(slot.to_numpy()).transform(
            lambda s: s.shift(1).rolling(NORMAL_SPREAD_DAYS, min_periods=5).median()).to_numpy()
        for tf in ("h1", "h4"):
            x = self.arr[tf]
            x["adx"] = P.adx(x["h"], x["l"], x["c"], 14)
            x["swings"] = P.swings(x["h"], x["l"])
            x["breaks"] = P.structure_breaks(x["o"], x["h"], x["l"], x["c"], x["swings"])
            x["break_known"] = np.array([x["close"][br.idx] for br in x["breaks"]], dtype="datetime64[ns]")
            x["swing_known"] = np.array([s.known for s in x["swings"]], dtype="int64")
        d1 = self.arr["d1"]
        d1["atr"] = P.atr(d1["h"], d1["l"], d1["c"], 14)
        self.zones = {}
        for tf in ("m15", "h1", "h4"):
            x = self.arr[tf]
            zs = P.fvgs(x["o"], x["h"], x["l"], x["c"], x["open"], x["close"], tf)
            if tf in ("h1", "h4"):
                zs += P.order_blocks(x["o"], x["h"], x["l"], x["c"], x["close"], x["breaks"], tf)
            P.mark_fills(zs, x["h"], x["l"], x["open"])
            self.zones[tf] = zs
        self.news = np.sort(np.asarray(events if events is not None else news_times(p), dtype="datetime64[ns]"))

    @staticmethod
    def _arrays(f: pd.DataFrame) -> dict:
        return {"open": f["ts"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]"),
                "close": f["close_ts"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]"),
                "o": f["open"].to_numpy("float64"), "h": f["high"].to_numpy("float64"),
                "l": f["low"].to_numpy("float64"), "c": f["close"].to_numpy("float64")}

    # ------------------------------------------------------------ lookups
    @staticmethod
    def t64(ts: pd.Timestamp) -> np.datetime64:
        return np.datetime64(ts.tz_convert("UTC").tz_localize(None), "ns")

    def m5_range(self, start: pd.Timestamp, end: pd.Timestamp) -> np.ndarray:
        m5 = self.arr["m5"]
        a = np.searchsorted(m5["open"], self.t64(start), "left")
        b = np.searchsorted(m5["close"], self.t64(end), "right")
        return np.arange(a, b)

    def last_closed(self, tf: str, t: pd.Timestamp) -> int:
        return int(np.searchsorted(self.arr[tf]["close"], self.t64(t), "right")) - 1

    def adx_ok(self, t: pd.Timestamp) -> tuple[bool, float]:
        i = self.last_closed("h1", t)
        v = float(self.arr["h1"]["adx"][i]) if i >= 0 else float("nan")
        return bool(np.isfinite(v) and v >= ADX_MIN), v

    def trend(self, tf: str, t64) -> int:
        x = self.arr[tf]
        n = int(np.searchsorted(x["break_known"], t64, "right"))
        if n < 2:
            return 0
        a, b = x["breaks"][n - 2].direction, x["breaks"][n - 1].direction
        return a if a == b else 0

    def news_blocked(self, t64) -> bool:
        w = np.timedelta64(NEWS_MIN, "m")
        i = np.searchsorted(self.news, t64 - w, "left")
        return i < len(self.news) and self.news[i] <= t64 + w

    def entry_blocked(self, m: int) -> str | None:
        m5 = self.arr["m5"]
        if self.news_blocked(m5["close"][m]):
            return "news"
        sp, nsp = m5["spread"][m], m5["normal_spread"][m]
        if np.isfinite(sp) and np.isfinite(nsp) and nsp > 0 and sp > SPREAD_X * nsp:
            return "spread"
        return None

    def buffer(self, m: int) -> float:
        a = self.arr["m5"]["atr"][m]
        return BUFFER_ATR * (a if np.isfinite(a) else 0.0)

    def m5_fvg_lo_hi(self, i: int) -> tuple[int, float, float] | None:
        x = self.arr["m5"]
        if i < 2:
            return None
        if x["h"][i - 2] < x["l"][i]:
            return 1, x["h"][i - 2], x["l"][i]
        if x["l"][i - 2] > x["h"][i]:
            return -1, x["h"][i], x["l"][i - 2]
        return None

    def spike_trigger(self, e: int, side: int, floor: int) -> tuple[float | None, str, int]:
        """The level a candle must close beyond after the extreme `e`: the inverted spike FVG if one formed in the run
        into the extreme (the most recent one), otherwise the CISD level. side is the trade side."""
        x = self.arr["m5"]
        rs = P.run_start(x["o"], x["c"], e, side, floor)
        for k in range(e, max(rs + 2, floor + 2) - 1, -1):
            g = self.m5_fvg_lo_hi(k)
            if g and g[0] == -side:
                return (g[1] if side < 0 else g[2]), "ifvg", k
        return P.cisd_level(x["o"], x["c"], e, side, floor), "cisd", e

    # ------------------------------------------------------------ session levels
    def prev_day(self, t: pd.Timestamp) -> tuple[float, float] | None:
        i = self.last_closed("d1ny", t)
        x = self.arr["d1ny"]
        return (float(x["h"][i]), float(x["l"][i])) if i >= 0 else None

    def prev_week(self, day: date) -> tuple[float, float] | None:
        f = self.bars["d1ny"]
        trade_day = (f["close_ts"].dt.tz_convert(NY) - pd.Timedelta(seconds=1)).dt.date
        monday = day - timedelta(days=day.weekday())
        mask = (trade_day >= monday - timedelta(days=7)) & (trade_day < monday)
        if not mask.any():
            return None
        return float(f.loc[mask, "high"].max()), float(f.loc[mask, "low"].min())

    def hi_lo(self, start: pd.Timestamp, end: pd.Timestamp) -> tuple[float, float] | None:
        idx = self.m5_range(start, end)
        if len(idx) == 0:
            return None
        x = self.arr["m5"]
        return float(x["h"][idx].max()), float(x["l"][idx].min())


def news_times(p: Paths) -> list:
    from quantresearch.external import store as ext

    ev = ext.read_events(p)
    out = list(ev.loc[ev["series"] == "FOMC_DECISION", "event_ts"])
    for s in ("ALFRED_PAYEMS_CHG", "ALFRED_CPI_MOM"):
        out += list(ev.loc[ev["series"] == s, "published_ts"])
    return [np.datetime64(pd.Timestamp(t).tz_convert("UTC").tz_localize(None), "ns") for t in out]


# ---------------------------------------------------------------- Setup 1: NY open sweep + iFVG
def setup1(F: Frames, day: date) -> tuple[Signal | None, str]:
    ws, we = local(day, 8, 30, NY), local(day, 10, 30, NY)
    ok, adx_v = F.adx_ok(ws)
    if not ok:
        return None, f"choppy (H1 ADX {adx_v:.1f})"
    pdr, pw = F.prev_day(ws), F.prev_week(day)
    asia = F.hi_lo(local(day, 0, 0, "UTC"), local(day, 6, 0, "UTC"))
    london = F.hi_lo(local(day, 8, 0, LDN), ws)
    if pdr is None:
        return None, "no previous day"
    pd_mid = (pdr[0] + pdr[1]) / 2
    highs = {"PDH": pdr[0], **({"PWH": pw[0]} if pw else {}), **({"Asian high": asia[0]} if asia else {}), **({"London high": london[0]} if london else {})}
    lows = {"PDL": pdr[1], **({"PWL": pw[1]} if pw else {}), **({"Asian low": asia[1]} if asia else {}), **({"London low": london[1]} if london else {})}
    x = F.arr["m5"]
    win = F.m5_range(ws, we)
    if len(win) == 0:
        return None, "no bars"
    day_start = F.m5_range(local(day - timedelta(days=1), 18, 0, NY), ws)
    first = int(day_start[0]) if len(day_start) else int(win[0])
    zones = [z for tf in ("h1", "h4") for z in F.zones[tf] if z.formed_ts >= F.t64(ws - pd.Timedelta(days=ZONE_DAYS))]
    cands = []
    for i in win:
        t_open = x["open"][i]
        for side in (-1, 1):
            levels = highs if side < 0 else lows
            swept = []
            for name, lvl in levels.items():
                before = x["h"][first:i].max() if i > first else -np.inf
                before_l = x["l"][first:i].min() if i > first else np.inf
                if side < 0 and x["h"][i] > lvl and before <= lvl:
                    swept.append((name, lvl))
                if side > 0 and x["l"][i] < lvl and before_l >= lvl:
                    swept.append((name, lvl))
            if not swept:
                continue
            zone = next((z for z in zones if z.direction == side and z.unmitigated_at(t_open)
                         and ((z.mid > pd_mid) if side < 0 else (z.mid < pd_mid)) and x["h"][i] >= z.lo and x["l"][i] <= z.hi), None)
            if zone is None:
                continue
            name, lvl = (max if side < 0 else min)(swept, key=lambda s: s[1])
            # V-shape: a close back past the level within 3 candles of the extreme.
            k = next((j for j in range(i, win[-1] + 1) if (x["c"][j] < lvl if side < 0 else x["c"][j] > lvl)), None)
            if k is None:
                continue
            seg = slice(i, k + 1)
            e = i + int(np.argmax(x["h"][seg]) if side < 0 else np.argmin(x["l"][seg]))
            if k - e > 3:
                continue
            level, how, fvg_k = F.spike_trigger(e, side, max(first, i - 36))
            if level is None:
                continue
            m = next((j for j in range(max(k, e), win[-1] + 1) if (x["c"][j] < level if side < 0 else x["c"][j] > level)), None)
            if m is None:
                continue
            blocked = F.entry_blocked(m)
            if blocked:
                cands.append((m, None, blocked))
                continue
            ext = (x["h"][e:m + 1].max() if side < 0 else x["l"][e:m + 1].min())
            stop = ext + F.buffer(m) if side < 0 else ext - F.buffer(m)
            pools = [v for v in (lows if side < 0 else highs).values() if (v < x["c"][m] if side < 0 else v > x["c"][m])]
            pool = (max(pools) if side < 0 else min(pools)) if pools else None
            sig = Signal(SETUPS[0], str(day), side, str(pd.Timestamp(x["close"][m], tz="UTC")), float(x["c"][m]), float(stop),
                         "level_or_2r", None if pool is None else float(pool),
                         {"swept": name, "swept_level": float(lvl), "sweep_ts": str(pd.Timestamp(x["open"][i], tz="UTC")),
                          "extreme": float(ext), "trigger": how, "trigger_level": float(level),
                          "zone": {"kind": zone.kind, "tf": zone.tf, "lo": zone.lo, "hi": zone.hi}, "pd_mid": pd_mid, "adx_h1": adx_v})
            cands.append((m, sig, None))
    return _first(cands)


def _first(cands):
    if not cands:
        return None, "no setup"
    cands.sort(key=lambda c: c[0])
    for m, sig, why in cands:
        if sig is not None:
            return sig, "ok"
    return None, f"filtered ({cands[0][2]})"


# ---------------------------------------------------------------- Setup 2: London open Asian-range sweep
def setup2(F: Frames, day: date) -> tuple[Signal | None, str]:
    asia = F.hi_lo(local(day, 0, 0, "UTC"), local(day, 6, 0, "UTC"))
    if asia is None:
        return None, "no Asian session"
    ah, al = asia
    d = F.last_closed("d1", local(day, 0, 0, "UTC"))
    atr_d1 = F.arr["d1"]["atr"][d] if d >= 0 else np.nan
    if not np.isfinite(atr_d1) or ah - al > atr_d1:
        return None, "Asian range wider than ATR(14, D1)"
    ws, we = local(day, 7, 0, LDN), local(day, 9, 30, LDN)
    ok, adx_v = F.adx_ok(ws)
    if not ok:
        return None, f"choppy (H1 ADX {adx_v:.1f})"
    x = F.arr["m5"]
    win = F.m5_range(ws, we)
    cands = []
    for side, cond in ((-1, lambda i: x["h"][i] > ah), (1, lambda i: x["l"][i] < al)):
        i = next((j for j in win if cond(j)), None)  # The first sweep of that side in the window.
        if i is None:
            continue
        for j in range(i, win[-1] + 1):
            inside = al < x["c"][j] < ah
            displaced = (x["c"][j] < x["o"][j]) if side < 0 else (x["c"][j] > x["o"][j])
            big = np.isfinite(x["avg_body"][j]) and x["body"][j] >= 1.5 * x["avg_body"][j]
            if not (inside and displaced and big):
                continue
            seg = slice(i, j + 1)
            e = i + int(np.argmax(x["h"][seg]) if side < 0 else np.argmin(x["l"][seg]))
            level, how, _ = F.spike_trigger(e, side, max(int(win[0]) - 36, i - 36))
            if level is None or not (x["c"][j] < level if side < 0 else x["c"][j] > level):
                continue
            blocked = F.entry_blocked(j)
            if blocked:
                cands.append((j, None, blocked))
                break
            ext = x["h"][seg].max() if side < 0 else x["l"][seg].min()
            stop = ext + F.buffer(j) if side < 0 else ext - F.buffer(j)
            target = al if side < 0 else ah
            cands.append((j, Signal(SETUPS[1], str(day), side, str(pd.Timestamp(x["close"][j], tz="UTC")), float(x["c"][j]), float(stop),
                                    "level_or_2r", float(target),
                                    {"asian_high": ah, "asian_low": al, "atr_d1": float(atr_d1), "sweep_ts": str(pd.Timestamp(x["open"][i], tz="UTC")),
                                     "extreme": float(ext), "trigger": how, "trigger_level": float(level),
                                     "body_x_avg": float(x["body"][j] / x["avg_body"][j]), "adx_h1": adx_v}), None))
            break
    return _first(cands)


# ---------------------------------------------------------------- Setup 3: 15m/1H FVG trend continuation
def setup3(F: Frames, day: date) -> tuple[Signal | None, str]:
    ws, we = local(day, 4, 0, NY), local(day, 12, 0, NY)
    ok, adx_v = F.adx_ok(ws)
    if not ok:
        return None, f"choppy (H1 ADX {adx_v:.1f})"
    x, h1 = F.arr["m5"], F.arr["h1"]
    win = F.m5_range(ws, we)
    pool = [z for tf in ("m15", "h1") for z in F.zones[tf] if z.kind == "fvg" and z.formed_ts >= F.t64(ws - pd.Timedelta(days=ZONE_DAYS))]
    touched: list[tuple] = []  # (zone, touch index)
    cands = []
    for m in win:
        t_close = x["close"][m]
        side = F.trend("h4", t_close)
        if side == 0 or F.trend("h1", t_close) != side:
            touched.clear()
            continue
        n_sw = int(np.searchsorted(h1["swing_known"], F.last_closed("h1", pd.Timestamp(t_close, tz="UTC")), "right"))
        sw = h1["swings"][max(0, n_sw - 60):n_sw]  # Swings known by now; the last 60 are plenty for the leg and target.
        last_sh = next((s for s in reversed(sw) if s.high), None)
        last_sl = next((s for s in reversed(sw) if not s.high), None)
        if last_sh is None or last_sl is None:
            continue
        leg_mid = (last_sh.price + last_sl.price) / 2
        # New touches on this candle.
        for z in pool:
            if z.direction != side or not z.unmitigated_at(x["open"][m]) or any(z is t[0] for t in touched):
                continue
            if (z.mid < leg_mid) if side > 0 else (z.mid > leg_mid):
                if (x["l"][m] <= z.hi) if side > 0 else (x["h"][m] >= z.lo):
                    touched.append((z, m))
        # A close through the far edge kills the zone.
        touched = [(z, t) for z, t in touched if not ((x["c"][m] < z.lo) if side > 0 else (x["c"][m] > z.hi))]
        for z, t in touched:
            if m <= t:
                continue
            seg = slice(t, m)
            p_ = t + int(np.argmin(x["l"][seg]) if side > 0 else np.argmax(x["h"][seg]))
            lvl = P.cisd_level(x["o"], x["c"], p_, side, max(t - 24, 0))
            cisd = lvl is not None and m > p_ and ((x["c"][m] > lvl) if side > 0 else (x["c"][m] < lvl))
            g = F.m5_fvg_lo_hi(m)
            fresh = g is not None and g[0] == side and m - 2 >= t
            if not (cisd or fresh):
                continue
            blocked = F.entry_blocked(m)
            if blocked:
                cands.append((m, None, blocked))
                break
            ext = x["l"][t:m + 1].min() if side > 0 else x["h"][t:m + 1].max()
            far = min(z.lo, ext) if side > 0 else max(z.hi, ext)
            stop = far - F.buffer(m) if side > 0 else far + F.buffer(m)
            target = next((s.price for s in reversed(sw) if s.high == (side > 0) and ((s.price > x["c"][m]) if side > 0 else (s.price < x["c"][m]))), None)
            if target is None:
                continue
            cands.append((m, Signal(SETUPS[2], str(day), side, str(pd.Timestamp(t_close, tz="UTC")), float(x["c"][m]), float(stop),
                                    "level_only", float(target),
                                    {"zone": {"kind": "fvg", "tf": z.tf, "lo": z.lo, "hi": z.hi}, "touch_ts": str(pd.Timestamp(x["open"][t], tz="UTC")),
                                     "trigger": "cisd" if cisd else "fresh_fvg", "leg_mid": leg_mid, "adx_h1": adx_v,
                                     "trend": {"h4": side, "h1": side}}), None))
            break
        if cands and cands[-1][1] is not None:
            break
    return _first(cands)


# ---------------------------------------------------------------- trade simulation on M1
def simulate(F: Frames, s: Signal) -> dict | None:
    t = np.datetime64(pd.Timestamp(s.signal_ts).tz_convert("UTC").tz_localize(None), "ns")
    ts = F.m1_ts if hasattr(F, "m1_ts") else None
    if ts is None:
        F.m1_ts = F.m1["ts"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
        F.m1_cols = {c: F.m1[c].to_numpy("float64") for c in ("bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close", "spread_mean")}
        ts = F.m1_ts
    a = int(np.searchsorted(ts, t, "left"))
    if a >= len(ts):
        return None
    cut = int(np.searchsorted(ts, np.datetime64(local(date.fromisoformat(s.day), 17, 0, NY).tz_localize(None), "ns"), "left"))
    if cut <= a:
        return {"skipped": "signal after the 17:00 New York close"}
    c = {k: v[:cut] for k, v in F.m1_cols.items()}
    side = s.side
    if side > 0:
        fill = c["ask_open"][a] if np.isfinite(c["ask_open"][a]) else c["bid_open"][a] + c["spread_mean"][a]
    else:
        fill = c["bid_open"][a]
    risk = side * (fill - s.stop)
    if not np.isfinite(fill) or risk <= 0:
        return {"skipped": "stop on the wrong side of the fill"}
    two_r = fill + side * MIN_RR * risk
    if s.target_rule == "level_only":
        if s.target_level is None or side * (s.target_level - fill) < MIN_RR * risk:
            return {"skipped": "previous swing is less than 2R away"}
        target = s.target_level
    elif s.target_level is not None and side * (s.target_level - fill) >= MIN_RR * risk:
        target = s.target_level
    else:
        target = two_r
    if side > 0:
        lo, hi, op, close_px = c["bid_low"][a:], c["bid_high"][a:], c["bid_open"][a:], c["bid_close"][a:]
        hit_stop, hit_tgt, hit_be = lo <= s.stop, hi >= target, hi >= fill + risk
    else:
        lo, hi, op, close_px = c["ask_high"][a:], c["ask_low"][a:], c["ask_open"][a:], c["ask_close"][a:]
        hit_stop, hit_tgt, hit_be = lo >= s.stop, hi <= target, hi <= fill - risk

    def first(mask, start=0):
        k = np.flatnonzero(mask[start:])
        return start + int(k[0]) if len(k) else None
    st, tg, be = first(hit_stop), first(hit_tgt), first(hit_be)
    if be is not None and (st is None or be < st) and (tg is None or be < tg):
        be_stop = (lo <= fill) if side > 0 else (lo >= fill)
        st2 = first(be_stop, be + 1)
        tg2 = tg
        if st2 is not None and (tg2 is None or st2 <= tg2):
            j, why, px = st2, "breakeven", fill
            if (op[j] < fill) if side > 0 else (op[j] > fill):
                px = op[j]
        elif tg2 is not None:
            j, why, px = tg2, "target", target
        else:
            j, why, px = len(op) - 1, "time_1700", close_px[-1]
    elif st is not None and (tg is None or st <= tg):
        j, why, px = st, "stop", s.stop
        if (op[j] < s.stop) if side > 0 else (op[j] > s.stop):
            px = op[j]
    elif tg is not None:
        j, why, px = tg, "target", target
        if (op[j] > target) if side > 0 else (op[j] < target):
            px = op[j]
    else:
        j, why, px = len(op) - 1, "time_1700", close_px[-1]
    if not np.isfinite(px):  # No ask recorded at 17:00: the bid plus that minute's spread.
        px = c["bid_close"][a + j] + (c["spread_mean"][a + j] if side < 0 else 0.0)
    r = side * (px - fill) / risk
    return {"fill": float(fill), "fill_ts": str(pd.Timestamp(ts[a], tz="UTC")), "risk": float(risk), "target": float(target),
            "target_kind": "level" if target != two_r else "2R", "exit": float(px), "exit_ts": str(pd.Timestamp(ts[a + j], tz="UTC")),
            "exit_reason": why, "r": float(r), "r_stress": float(r - STRESS_EXTRA / risk),
            "breakeven_moved": be is not None and (st is None or be < st) and (tg is None or be < tg)}


# ---------------------------------------------------------------- running
FUNCS = {SETUPS[0]: setup1, SETUPS[1]: setup2, SETUPS[2]: setup3}


def trading_days(year: int) -> list[date]:
    return [d.date() for d in pd.bdate_range(f"{year}-01-01", f"{year}-12-31")]


def run_year(year: int, p: Paths | None = None, F: Frames | None = None) -> list[dict]:
    p = p or paths()
    F = F or Frames(year, p)
    rows = []
    for day in trading_days(year):
        for name, fn in FUNCS.items():
            sig, why = fn(F, day)
            row = {"setup": name, "day": str(day), "status": why}
            if sig is not None:
                res = simulate(F, sig)
                row.update(signal=asdict(sig), trade=res)
                if res is None or "skipped" in res:
                    row["status"] = (res or {}).get("skipped", "no M1 data")
            rows.append(row)
    return rows


def combined(rows: list[dict]) -> list[dict]:
    """One trade per day across the three: the earliest valid signal (ties go to the lower setup number)."""
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("trade") and "r" in r["trade"]:
            by_day.setdefault(r["day"], []).append(r)
    order = {n: i for i, n in enumerate(SETUPS)}
    return [min(v, key=lambda r: (r["signal"]["signal_ts"], order[r["setup"]])) for _, v in sorted(by_day.items())]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Personal playbooks: signals and trades.")
    ap.add_argument("--years", nargs=2, type=int, default=[2004, 2026])
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    p = paths()
    out = p.research / "playbooks"
    out.mkdir(parents=True, exist_ok=True)
    for year in range(args.years[0], args.years[1] + 1):
        rows = run_year(year, p)
        (out / f"trades_{year}.json").write_text(json.dumps(rows, default=str), encoding="utf-8")
        n = {s: sum(1 for r in rows if r["setup"] == s and r.get("trade") and "r" in r["trade"]) for s in SETUPS}
        print(year, n, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
