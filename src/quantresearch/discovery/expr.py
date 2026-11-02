"""Interpret validated condition trees over a feature store window, and turn a spec into kernel inputs.

An evaluator is bound to rows [lo, hi) of a store, where lo includes a warm-up before the window. It never reads a
row at or after `hi`, so anything after the window end cannot influence a result. A walk-forward isolation test
changes every value after a cutoff and checks that nothing before it moves.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from quantresearch.discovery import spec as S
from quantresearch.discovery.kernel import simulate
from quantresearch.discovery.store import FeatureStore


class Evaluator:
    def __init__(self, store: FeatureStore, start: int, end: int, cache_limit: int = 160,
                 overrides: dict[str, np.ndarray] | None = None):
        self.store = store
        self.start, self.end = start, end
        self.lo = max(0, start - S.WARMUP_BARS)
        self.hi = end
        self.n = self.hi - self.lo
        self.cache: dict[str, np.ndarray] = {}
        self.cache_limit = cache_limit
        self.overrides = overrides or {}

    # rows of the evaluation window inside the evaluator's arrays
    @property
    def window(self) -> slice:
        return slice(self.start - self.lo, self.end - self.lo)

    def feature(self, name: str) -> np.ndarray:
        key = "f:" + name
        if key not in self.cache:
            src = self.overrides.get(name)
            arr = (src if src is not None else self.store.feature(name))[self.lo:self.hi]
            self._put(key, np.asarray(arr, dtype="float64"))
        return self.cache[key]

    def price(self, name: str) -> np.ndarray:
        return np.asarray(self.store.prices[name][self.lo:self.hi], dtype="float64")

    def _put(self, key: str, value: np.ndarray) -> None:
        if len(self.cache) >= self.cache_limit:
            for k in list(self.cache)[: self.cache_limit // 4]:
                del self.cache[k]
        self.cache[key] = value

    # ------------------------------------------------------------ numeric
    def num(self, node: dict) -> np.ndarray:
        if "f" in node:
            return self.feature(node["f"])
        if "c" in node:
            return np.full(self.n, float(node["c"]))
        key = S.canonical(node)
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        op = node["op"]
        a = self.num(node["a"])
        with np.errstate(all="ignore"):
            if op in S.NUM_BINARY:
                b = self.num(node["b"])
                if op == "add":
                    out = a + b
                elif op == "sub":
                    out = a - b
                elif op == "mul":
                    out = a * b
                else:
                    out = np.where(np.abs(b) > 1e-12, a / np.where(np.abs(b) > 1e-12, b, 1.0), np.nan)
            elif op == "abs":
                out = np.abs(a)
            elif op == "neg":
                out = -a
            else:
                n = int(node["n"])
                s = pd.Series(a)
                if op == "lag":
                    out = s.shift(n).to_numpy()
                elif op == "delta":
                    out = (s - s.shift(n)).to_numpy()
                elif op == "rmean":
                    out = s.rolling(n, min_periods=n).mean().to_numpy()
                elif op == "rstd":
                    out = s.rolling(n, min_periods=n).std().to_numpy()
                elif op == "rmax":
                    out = s.rolling(n, min_periods=n).max().to_numpy()
                elif op == "rmin":
                    out = s.rolling(n, min_periods=n).min().to_numpy()
                elif op == "rank":
                    out = s.rolling(n, min_periods=n).rank(pct=True).to_numpy()
                else:  # zs
                    m = s.rolling(n, min_periods=n).mean()
                    sd = s.rolling(n, min_periods=n).std()
                    out = ((s - m) / sd.where(sd > 0)).to_numpy()
        out = np.where(np.isfinite(out), out, np.nan)
        self._put(key, out)
        return out

    # ------------------------------------------------------------ boolean
    def boolean(self, node: dict | None, default: bool = True) -> np.ndarray:
        if node is None:
            return np.full(self.n, default)
        key = "b:" + S.canonical(node)
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        op = node["op"]
        with np.errstate(invalid="ignore"):
            if op in ("gt", "lt"):
                a, b = self.num(node["a"]), self.num(node["b"])
                out = (a > b) if op == "gt" else (a < b)
            elif op == "between":
                a = self.num(node["a"])
                out = (a > node["lo"]) & (a < node["hi"])
            elif op in ("cross_above", "cross_below"):
                a, b = self.num(node["a"]), self.num(node["b"])
                pa, pb = np.r_[np.nan, a[:-1]], np.r_[np.nan, b[:-1]]
                out = (a > b) & (pa <= pb) if op == "cross_above" else (a < b) & (pa >= pb)
            elif op == "and":
                out = np.logical_and.reduce([self.boolean(x) for x in node["args"]])
            elif op == "or":
                out = np.logical_or.reduce([self.boolean(x) for x in node["args"]])
            elif op == "not":
                inner = node["a"]
                out = ~self.boolean(inner)
                # NOT of an undefined comparison stays false: missing data never permits a trade.
                out &= self.defined(inner)
            elif op in ("persist", "recent"):
                x = pd.Series(self.boolean(node["a"]).astype(float))
                r = x.rolling(int(node["n"]), min_periods=int(node["n"]))
                out = (r.min() == 1).to_numpy() if op == "persist" else (r.max() == 1).to_numpy()
            elif op == "in":
                a = self.num(node["a"])
                out = np.isin(a, np.asarray(node["set"], dtype="float64"))
            else:  # is
                a = self.num(node["a"])
                out = np.isfinite(a) & (a != 0)
        out = np.asarray(out, dtype=bool)
        self._put(key, out)
        return out

    def defined(self, node: dict) -> np.ndarray:
        """Rows where every leaf of a tree has a value."""
        ok = np.ones(self.n, dtype=bool)
        for n in S.nodes(node):
            if "f" in n:
                ok &= np.isfinite(self.feature(n["f"]))
        return ok


@dataclass
class Costs:
    name: str = "observed"
    extra_spread: float = 0.0
    slippage: float = 0.0
    commission_per_lot: float = 0.0  # per side
    delay_bars: int = 0
    limit_through: float = 0.0
    swap: bool = True

    def as_dict(self) -> dict:
        return dict(self.__dict__)


SCENARIOS = {
    "observed": Costs("observed"),
    "spread_x2_equiv": Costs("spread_x2_equiv", extra_spread=0.30, slippage=0.05),
    "stress": Costs("stress", extra_spread=0.60, slippage=0.20, commission_per_lot=7.0),
    "delay_1bar": Costs("delay_1bar", delay_bars=1),
    "stress_delay": Costs("stress_delay", extra_spread=0.60, slippage=0.20, commission_per_lot=7.0, delay_bars=1),
    "limit_through": Costs("limit_through", limit_through=0.10),
    "no_swap": Costs("no_swap", swap=False),
}


@dataclass
class Compiled:
    """Kernel inputs for one spec over one evaluator."""
    want_long: np.ndarray
    want_short: np.ndarray
    arrays: dict
    params: dict


def compile_spec(spec: dict, ev: Evaluator) -> Compiled:
    s = S.normalize(spec)
    n = ev.n
    atr = ev.price("atr_14")
    close = ev.price("close")
    allowed = ev.boolean(s["regime"]) & ~ev.boolean(s["no_trade"], default=False)
    if s["sessions"]:
        sess = np.zeros(n, dtype=bool)
        for name in s["sessions"]:
            sess |= ev.feature(f"sess_{name}") == 1
        allowed &= sess
    g = s["guards"]
    if g.get("max_spread_z") is not None and "spread_z" in ev.store.index:
        sz = ev.feature("spread_z")
        allowed &= ~(sz > g["max_spread_z"])
    if g.get("fomc_hours") and "hours_to_fomc" in ev.store.index:
        h = ev.feature("hours_to_fomc")
        allowed &= ~((h >= 0) & (h <= g["fomc_hours"]))
    allowed &= np.isfinite(atr) & (atr > 0)
    want_long = (ev.boolean(s["long"], default=False) & allowed) if s["direction"] in ("long", "both") else np.zeros(n, bool)
    want_short = (ev.boolean(s["short"], default=False) & allowed) if s["direction"] in ("short", "both") else np.zeros(n, bool)

    st = s["stop"]
    if st["type"] == "atr":
        stop_l = stop_s = st["mult"] * atr
    else:
        k = int(st["lookback"])
        lowest = pd.Series(ev.price("low")).rolling(k, min_periods=k).min().to_numpy()
        highest = pd.Series(ev.price("high")).rolling(k, min_periods=k).max().to_numpy()
        stop_l = close - lowest + st["buffer_atr"] * atr
        stop_s = highest - close + st["buffer_atr"] * atr
    # A stop closer than a quarter ATR is not a realistic stop: no entry.
    floor = 0.25 * atr
    stop_l = np.where(stop_l >= floor, stop_l, np.nan)
    stop_s = np.where(stop_s >= floor, stop_s, np.nan)
    tg = s["target"]
    if tg["type"] == "r":
        tgt_l, tgt_s = tg["r"] * stop_l, tg["r"] * stop_s
    elif tg["type"] == "atr":
        tgt_l = tgt_s = tg["mult"] * atr
    else:
        tgt_l = tgt_s = np.full(n, np.nan)
    ex = s["exit"]
    exit_l = ev.boolean(ex["long_exit"], default=False)
    exit_s = ev.boolean(ex["short_exit"], default=False)
    day_end = ev.feature("day_end") == 1 if "day_end" in ev.store.index else np.zeros(n, bool)
    trail = ex["trail_atr"] * atr if ex["trail_atr"] is not None else np.full(n, np.nan)
    e = s["entry"]
    offset = e.get("offset_atr", 0.0) * atr
    params = {"entry_type": {"market": 0, "stop": 1, "limit": 2}[e["type"]], "expiry": int(e.get("expiry", 1)),
              "max_hold": int(ex["max_hold"]), "use_day_end": bool(ex["day_end"]), "flip": bool(ex["flip"]),
              "be_r": float(ex["breakeven_r"]) if ex["breakeven_r"] is not None else np.nan}
    arrays = {"stop_l": np.asarray(stop_l, float), "stop_s": np.asarray(stop_s, float), "tgt_l": np.asarray(tgt_l, float),
              "tgt_s": np.asarray(tgt_s, float), "exit_l": exit_l, "exit_s": exit_s, "day_end": day_end,
              "trail": np.asarray(trail, float), "offset": np.asarray(offset, float)}
    return Compiled(want_long, want_short, arrays, params)


@dataclass
class Trades:
    entry: np.ndarray
    exit: np.ndarray
    side: np.ndarray
    entry_px: np.ndarray
    exit_px: np.ndarray
    stop: np.ndarray
    pnl_oz: np.ndarray
    risk_oz: np.ndarray
    reason: np.ndarray
    lo: int  # store row of evaluator index 0

    @property
    def r(self) -> np.ndarray:
        return self.pnl_oz / np.maximum(self.risk_oz, 1e-9)

    def __len__(self) -> int:
        return len(self.entry)


def run_compiled(c: Compiled, ev: Evaluator, costs: Costs, want_long=None, want_short=None) -> Trades:
    p = ev.store.prices
    lo, hi = ev.lo, ev.hi
    get = lambda k: np.ascontiguousarray(p[k][lo:hi], dtype=np.float64)  # noqa: E731
    if costs.swap:
        swl = np.ascontiguousarray(ev.store.swap_long[lo:hi], dtype=np.float64)
        sws = np.ascontiguousarray(ev.store.swap_short[lo:hi], dtype=np.float64)
    else:
        swl = sws = np.zeros(hi - lo)
    wl = c.want_long if want_long is None else want_long
    ws = c.want_short if want_short is None else want_short
    a = c.arrays
    out = simulate(get("bid_open"), get("bid_high"), get("bid_low"), get("bid_close"),
                   get("ask_open"), get("ask_high"), get("ask_low"), get("ask_close"), get("close"),
                   np.ascontiguousarray(wl, dtype=np.bool_), np.ascontiguousarray(ws, dtype=np.bool_),
                   a["stop_l"], a["stop_s"], a["tgt_l"], a["tgt_s"],
                   np.ascontiguousarray(a["exit_l"], dtype=np.bool_), np.ascontiguousarray(a["exit_s"], dtype=np.bool_),
                   np.ascontiguousarray(a["day_end"], dtype=np.bool_), a["trail"], a["offset"],
                   c.params["entry_type"], c.params["expiry"], c.params["max_hold"], c.params["use_day_end"],
                   c.params["flip"], c.params["be_r"], costs.extra_spread, costs.slippage,
                   costs.commission_per_lot / 100.0, int(costs.delay_bars), costs.limit_through,
                   swl, sws, ev.start - lo, ev.end - lo)
    return Trades(*out, lo=lo)


def run_spec(spec: dict, ev: Evaluator, costs: Costs | None = None) -> tuple[Compiled, Trades]:
    c = compile_spec(spec, ev)
    return c, run_compiled(c, ev, costs or SCENARIOS["observed"])
