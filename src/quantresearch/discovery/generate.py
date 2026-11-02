"""Hypothesis generators. All output is validated specs; nothing here executes generated code.

Grammar        random typed conditions over the catalogue, with thresholds taken from TRAINING-window quantiles
Mutation       threshold, operator, feature, window, atom and exit-gene changes, recorded as ancestry
Crossover      recombines conditions and exit blocks of two parents
Templates      market-behaviour starting points (opening ranges, session sweeps, squeezes, gaps, divergences ...)
               with parameter spaces for optimisation. They are seeds, not limits: evolution rewrites them.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

import numpy as np

from quantresearch.discovery import spec as S
from quantresearch.discovery.expr import Evaluator

QLEVELS = np.array([0.02, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.98])
HOLDS = {"m15": [4, 8, 16, 32, 64, 96, 192], "h1": [2, 4, 8, 16, 24, 48, 120], "h4": [2, 3, 6, 12, 30, 60],
         "d1ny": [1, 2, 3, 5, 10, 20, 40]}
INTRADAY = ("m15", "h1")


@dataclass
class FeatureInfo:
    name: str
    family: str
    kind: str          # numeric, binary, calendar
    quantiles: np.ndarray | None
    values: list | None


class Grammar:
    """Typed random construction over a training window. Constants come from that window's quantiles only."""

    def __init__(self, ev: Evaluator, rng: np.random.Generator, features: list[str] | None = None):
        self.ev, self.rng, self.tf = ev, rng, ev.store.tf
        names = features or ev.store.names
        self.info: dict[str, FeatureInfo] = {}
        win = ev.window
        for name in sorted(names):
            if name not in ev.store.index or name == "day_end":
                continue
            x = ev.feature(name)[win]
            x = x[np.isfinite(x)]
            if len(x) < 200:
                continue
            fam = ev.store.family(name)
            uniq = np.unique(x[: min(len(x), 50_000)])
            if name in S.CALENDAR_SETS:
                self.info[name] = FeatureInfo(name, fam, "calendar", None, sorted(int(v) for v in uniq))
            elif len(uniq) <= 2 and set(uniq.tolist()) <= {0.0, 1.0}:
                if 0.002 < x.mean() < 0.998:
                    self.info[name] = FeatureInfo(name, fam, "binary", None, None)
            else:
                q = np.quantile(x, QLEVELS)
                if np.nanmax(q) - np.nanmin(q) > 0:
                    self.info[name] = FeatureInfo(name, fam, "numeric", q, None)
        self.numeric = sorted(n for n, i in self.info.items() if i.kind == "numeric")
        self.binary = sorted(n for n, i in self.info.items() if i.kind == "binary")
        self.calendar = sorted(n for n, i in self.info.items() if i.kind == "calendar")
        self.by_family: dict[str, list[str]] = {}
        for n, i in self.info.items():
            self.by_family.setdefault(i.family, []).append(n)
        for v in self.by_family.values():
            v.sort()
        self.regime_pool = sorted(n for n in self.numeric if self.info[n].family in ("volatility", "trend", "higher_timeframe", "macro", "cross_market"))

    # ------------------------------------------------------------ helpers
    def choice(self, items):
        return items[int(self.rng.integers(len(items)))]

    def quantile_of(self, node: dict, level: float) -> float | None:
        x = self.ev.num(node)[self.ev.window]
        x = x[np.isfinite(x)]
        if len(x) < 200:
            return None
        return float(np.quantile(x, level))

    def level_of(self, node: dict, value: float) -> float:
        x = self.ev.num(node)[self.ev.window]
        x = x[np.isfinite(x)]
        return float((x < value).mean()) if len(x) else 0.5

    def numeric_leaf(self, name: str | None = None) -> dict:
        name = name or self.choice(self.numeric)
        leaf = {"f": name}
        u = self.rng.random()
        if u < 0.08:
            return {"op": "delta", "a": leaf, "n": int(self.choice([1, 3, 6, 12]))}
        if u < 0.13:
            return {"op": "zs", "a": leaf, "n": int(self.choice([20, 50, 100, 250]))}
        if u < 0.16:
            return {"op": "rmean", "a": leaf, "n": int(self.choice([3, 5, 10, 20]))}
        if u < 0.19:
            return {"op": "lag", "a": leaf, "n": int(self.choice([1, 2, 3, 6]))}
        return leaf

    # ------------------------------------------------------------ atoms
    def atom(self, pool: list[str] | None = None) -> dict | None:
        u = self.rng.random()
        if u < 0.10 and self.calendar and pool is None:
            name = self.choice(self.calendar)
            vals = self.info[name].values
            k = int(self.rng.integers(1, min(4, len(vals)) + 1))
            start = int(self.rng.integers(len(vals)))
            chosen = sorted({vals[(start + j) % len(vals)] for j in range(k)})
            return {"op": "in", "a": {"f": name}, "set": [int(v) for v in chosen]}
        if u < 0.22 and self.binary and pool is None:
            core = {"op": "is", "a": {"f": self.choice(self.binary)}}
            if self.rng.random() < 0.3:
                core = {"op": "recent", "a": core, "n": int(self.choice([2, 3, 6, 12]))}
            if self.rng.random() < 0.15:
                core = {"op": "not", "a": core}
            return core
        names = pool or self.numeric
        if not names:
            return None
        leaf = self.numeric_leaf(self.choice(names))
        r = self.rng.random()
        if r < 0.10:
            i, j = sorted(self.rng.choice(len(QLEVELS), 2, replace=False))
            lo, hi = self.quantile_of(leaf, QLEVELS[i]), self.quantile_of(leaf, QLEVELS[j])
            if lo is None or hi is None or not lo < hi:
                return None
            return {"op": "between", "a": leaf, "lo": lo, "hi": hi}
        level = float(self.choice(QLEVELS))
        c = self.quantile_of(leaf, level)
        if c is None:
            return None
        if r < 0.17:
            return {"op": self.choice(["cross_above", "cross_below"]), "a": leaf, "b": {"c": c}}
        op = "gt" if level >= 0.5 else "lt"
        if self.rng.random() < 0.2:
            op = "lt" if op == "gt" else "gt"
        return {"op": op, "a": leaf, "b": {"c": c}}

    def condition(self, k: int | None = None) -> dict | None:
        k = k or int(self.rng.choice([1, 2, 3, 4], p=[0.25, 0.4, 0.25, 0.1]))
        atoms = []
        for _ in range(k * 3):
            a = self.atom()
            if a is not None:
                atoms.append(a)
            if len(atoms) >= k:
                break
        if not atoms:
            return None
        if len(atoms) >= 2 and self.rng.random() < 0.08:
            atoms = [{"op": "or", "args": atoms[:2]}] + atoms[2:]
        return join(atoms)

    # ------------------------------------------------------------ exits
    def exit_genes(self) -> dict:
        rng, tf = self.rng, self.tf
        g = {}
        u = rng.random()
        g["entry"] = {"type": "market"} if u < 0.7 else {"type": "stop" if u < 0.85 else "limit",
                                                         "offset_atr": float(self.choice([0.1, 0.25, 0.5, 1.0])),
                                                         "expiry": int(self.choice([1, 2, 4, 8]))}
        g["stop"] = ({"type": "atr", "mult": float(self.choice([0.75, 1.0, 1.5, 2.0, 3.0, 4.0]))} if rng.random() < 0.8 else
                     {"type": "swing", "lookback": int(self.choice([5, 10, 20, 50])), "buffer_atr": float(self.choice([0.0, 0.25, 0.5]))})
        u = rng.random()
        g["target"] = ({"type": "none"} if u < 0.35 else {"type": "r", "r": float(self.choice([1.0, 1.5, 2.0, 3.0, 5.0]))} if u < 0.8
                       else {"type": "atr", "mult": float(self.choice([1.0, 2.0, 4.0]))})
        g["exit"] = {"max_hold": int(self.choice(HOLDS[tf])),
                     "trail_atr": float(self.choice([1.0, 2.0, 3.0])) if rng.random() < 0.25 else None,
                     "breakeven_r": float(self.choice([1.0, 1.5])) if rng.random() < 0.2 else None,
                     "day_end": bool(tf in INTRADAY and rng.random() < 0.2), "flip": bool(rng.random() < 0.5),
                     "long_exit": None, "short_exit": None}
        return g

    def random_spec(self, direction: str | None = None) -> dict | None:
        rng = self.rng
        direction = direction or str(rng.choice(["long", "short", "both"], p=[0.4, 0.3, 0.3]))
        spec = {"timeframe": self.tf, "direction": direction, **self.exit_genes()}
        if direction in ("long", "both"):
            spec["long"] = self.condition()
        if direction in ("short", "both"):
            spec["short"] = self.condition()
        if spec.get("long") is None and direction != "short" or spec.get("short") is None and direction != "long":
            return None
        if rng.random() < 0.35 and self.regime_pool:
            spec["regime"] = self.atom(self.regime_pool)
        if rng.random() < 0.12:
            spec["no_trade"] = self.atom()
        if rng.random() < 0.25 and self.tf in INTRADAY:
            spec["sessions"] = sorted(set(rng.choice(list(S.SESSIONS), int(rng.integers(1, 3)), replace=False).tolist()))
        if rng.random() < 0.12:
            side = "long_exit" if direction != "short" else "short_exit"
            spec["exit"][side] = self.atom()
        return spec

    # ------------------------------------------------------------ variation
    def mutate(self, parent: dict) -> tuple[dict, str]:
        s = copy.deepcopy(S.normalize(parent))
        s.pop("meta", None)
        rng = self.rng
        sides = [k for k in ("long", "short") if s[k] is not None]
        ops = ["threshold", "threshold", "flip_op", "replace_atom", "add_atom", "drop_atom", "swap_feature", "window",
               "exit", "exit", "regime", "sessions"]
        op = self.choice(ops)
        side = self.choice(sides)
        atoms_ = atoms(s[side])
        if op in ("threshold", "flip_op", "swap_feature", "window"):
            idx = [i for i, a in enumerate(atoms_) if a.get("op") in ("gt", "lt", "between", "cross_above", "cross_below")]
            if not idx:
                op = "replace_atom"
            else:
                i = self.choice(idx)
                a = copy.deepcopy(atoms_[i])
                leaf = a["a"]
                if op == "threshold":
                    if a["op"] == "between":
                        lo_l, hi_l = self.level_of(leaf, a["lo"]), self.level_of(leaf, a["hi"])
                        lo_l = float(np.clip(lo_l + rng.normal(0, 0.06), 0.01, 0.97))
                        hi_l = float(np.clip(hi_l + rng.normal(0, 0.06), lo_l + 0.02, 0.99))
                        lo, hi = self.quantile_of(leaf, lo_l), self.quantile_of(leaf, hi_l)
                        if lo is not None and hi is not None and lo < hi:
                            a["lo"], a["hi"] = lo, hi
                    else:
                        lev = float(np.clip(self.level_of(leaf, a["b"]["c"]) + rng.normal(0, 0.06), 0.01, 0.99))
                        c = self.quantile_of(leaf, lev)
                        if c is not None:
                            a["b"] = {"c": c}
                elif op == "flip_op" and a["op"] in ("gt", "lt"):
                    a["op"] = "lt" if a["op"] == "gt" else "gt"
                elif op == "swap_feature":
                    base = leaf if "f" in leaf else leaf.get("a", {})
                    fam = self.info.get(base.get("f", ""), None)
                    pool = self.by_family.get(fam.family, self.numeric) if fam else self.numeric
                    pool = [p for p in pool if self.info[p].kind == "numeric"] or self.numeric
                    new = self.atom(pool)
                    a = new or a
                elif op == "window" and "n" in leaf:
                    leaf["n"] = int(np.clip(round(leaf["n"] * float(self.choice([0.5, 0.75, 1.5, 2.0]))), 1 if leaf["op"] in ("lag", "delta") else 2,
                                            S.MAX_LAG if leaf["op"] in ("lag", "delta") else S.MAX_WINDOW))
                atoms_[i] = a
        if op == "replace_atom":
            new = self.atom()
            if new is not None:
                atoms_[int(rng.integers(len(atoms_)))] = new
        elif op == "add_atom" and len(atoms_) < 5:
            new = self.atom()
            if new is not None:
                atoms_.append(new)
        elif op == "drop_atom" and len(atoms_) > 1:
            atoms_.pop(int(rng.integers(len(atoms_))))
        elif op == "exit":
            genes = self.exit_genes()
            block = self.choice(["entry", "stop", "target", "max_hold", "trail_atr", "breakeven_r", "day_end", "flip"])
            if block in ("entry", "stop", "target"):
                s[block] = genes[block]
            else:
                s["exit"][block] = genes["exit"][block]
        elif op == "regime":
            s["regime"] = None if s["regime"] is not None and rng.random() < 0.5 else (self.atom(self.regime_pool) if self.regime_pool else None)
        elif op == "sessions" and self.tf in INTRADAY:
            s["sessions"] = None if s["sessions"] and rng.random() < 0.5 else sorted(set(rng.choice(list(S.SESSIONS), int(rng.integers(1, 3)), replace=False).tolist()))
        s[side] = join(atoms_)
        return s, f"mutate:{op}"

    def crossover(self, a: dict, b: dict) -> tuple[dict, str]:
        a, b = S.normalize(a), S.normalize(b)
        child = copy.deepcopy(a)
        child.pop("meta", None)
        for side in ("long", "short"):
            if a[side] is not None and b[side] is not None:
                pool = atoms(a[side]) + atoms(b[side])
                k = max(1, min(5, len(atoms(a[side]))))
                pick = sorted(self.rng.choice(len(pool), min(k, len(pool)), replace=False).tolist())
                child[side] = join([copy.deepcopy(pool[i]) for i in pick])
        for block in ("entry", "stop", "target", "exit", "regime", "sessions"):
            if self.rng.random() < 0.5:
                child[block] = copy.deepcopy(b[block])
                if block == "exit":
                    child["exit"]["long_exit"] = a["exit"]["long_exit"] if child["long"] is not None else None
                    child["exit"]["short_exit"] = a["exit"]["short_exit"] if child["short"] is not None else None
        return child, "crossover"


def atoms(cond: dict | None) -> list[dict]:
    if cond is None:
        return []
    if cond.get("op") == "and":
        return [copy.deepcopy(a) for a in cond["args"]]
    return [copy.deepcopy(cond)]


def join(items: list[dict]) -> dict | None:
    items = [i for i in items if i is not None]
    if not items:
        return None
    return items[0] if len(items) == 1 else {"op": "and", "args": items}


# ---------------------------------------------------------------- market-behaviour templates
def _cmp(f, op, c):
    return {"op": op, "a": {"f": f}, "b": {"c": float(c)}}


def _is(f):
    return {"op": "is", "a": {"f": f}}


TEMPLATES: dict[str, dict] = {}


def template(name: str, family: str, space: dict, tfs=("m15", "h1", "h4", "d1ny")):
    def wrap(fn):
        TEMPLATES[name] = {"family": family, "space": space, "build": fn, "tfs": tfs}
        return fn
    return wrap


def _exits(p, tf, default_hold=None):
    hold = p.get("hold", default_hold or HOLDS[tf][3])
    out = {"stop": {"type": "atr", "mult": float(p.get("stop", 2.0))},
           "target": {"type": "none"} if p.get("target", 0) == 0 else {"type": "r", "r": float(p["target"])},
           "exit": {"max_hold": int(hold), "day_end": bool(p.get("day_end", False)), "flip": False,
                    "trail_atr": None if p.get("trail", 0) == 0 else float(p["trail"])}}
    return out


COMMON = {"stop": [1.0, 1.5, 2.0, 3.0], "target": [0, 1.5, 2.0, 3.0], "trail": [0, 2.0]}


@template("opening_range_breakout", "session_range", {**COMMON, "range": ["ldn_or", "ny_or", "asia"], "buffer": [0.0, 0.1, 0.25, 0.5],
                                                       "max_width": [1.0, 2.0, 4.0], "hold": [4, 8, 16, 24], "day_end": [True, False]}, INTRADAY)
def _orb(p, tf):
    r = p["range"]
    base = [_is(f"{r}_complete"), _cmp(f"{r}_range_atr", "lt", p["max_width"])]
    long_ = [{"op": "cross_above", "a": {"f": f"dist_{r}_high"}, "b": {"c": float(p["buffer"])}}] + base
    short = [{"op": "cross_below", "a": {"f": f"dist_{r}_low"}, "b": {"c": -float(p["buffer"])}}] + base
    return {"timeframe": tf, "direction": "both", "long": join(long_), "short": join(short), **_exits(p, tf)}


@template("session_range_sweep_reversal", "liquidity", {**COMMON, "range": ["asia", "ldn_or"], "depth": [0.0, 0.2, 0.5],
                                                        "hold": [4, 8, 16], "day_end": [True, False]}, INTRADAY)
def _sweep(p, tf):
    r = p["range"]
    # price ran above the range high within the last bars, now closes back below it: fade the sweep
    short = [_is(f"{r}_complete"), {"op": "recent", "a": _cmp(f"dist_{r}_high", "gt", p["depth"]), "n": 3}, _cmp(f"dist_{r}_high", "lt", 0)]
    long_ = [_is(f"{r}_complete"), {"op": "recent", "a": _cmp(f"dist_{r}_low", "lt", -p["depth"]), "n": 3}, _cmp(f"dist_{r}_low", "gt", 0)]
    return {"timeframe": tf, "direction": "both", "long": join(long_), "short": join(short), **_exits(p, tf)}


@template("previous_day_level", "levels", {**COMMON, "mode": ["fade", "follow"], "depth": [0.0, 0.25, 0.5], "hold": [4, 8, 24]})
def _pdl(p, tf):
    up = {"op": "cross_above", "a": {"f": "dist_pdh"}, "b": {"c": float(p["depth"])}}
    dn = {"op": "cross_below", "a": {"f": "dist_pdl"}, "b": {"c": -float(p["depth"])}}
    long_, short = (dn, up) if p["mode"] == "fade" else (up, dn)
    return {"timeframe": tf, "direction": "both", "long": long_, "short": short, **_exits(p, tf)}


@template("volatility_squeeze_breakout", "volatility", {**COMMON, "squeeze": [0.05, 0.1, 0.2, 0.3], "channel": [20, 55], "hold": [8, 24, 48]})
def _squeeze(p, tf):
    sq = {"op": "recent", "a": _cmp("bb_width_rank", "lt", p["squeeze"]), "n": 5}
    n = p["channel"]
    return {"timeframe": tf, "direction": "both", "long": join([sq, _cmp(f"dist_dch{n}", "gt", 0)]),
            "short": join([sq, _cmp(f"dist_dcl{n}", "lt", 0)]), **_exits(p, tf)}


@template("trend_pullback", "trend", {**COMMON, "htf_trend": ["h4_trend", "d1ny_trend", "h1_trend"], "rsi": [30, 35, 40, 45], "hold": [8, 24, 48]},
          ("m15", "h1", "h4"))
def _pullback(p, tf):
    f = p["htf_trend"] if not p["htf_trend"].startswith(tf) else "structure_trend"
    return {"timeframe": tf, "direction": "both", "long": join([_cmp(f, "gt", 0), _cmp("rsi14", "lt", p["rsi"]), _cmp("ema_gap", "gt", 0)]),
            "short": join([_cmp(f, "lt", 0), _cmp("rsi14", "gt", 100 - p["rsi"]), _cmp("ema_gap", "lt", 0)]), **_exits(p, tf)}


@template("exhaustion_fade", "exhaustion", {**COMMON, "streak": [3, 4, 5, 6, 8], "range_z": [0.0, 1.0, 2.0], "hold": [2, 4, 8, 16]})
def _exhaust(p, tf):
    return {"timeframe": tf, "direction": "both",
            "long": join([_cmp("streak", "lt", -p["streak"] + 0.5), _cmp("range_z", "gt", p["range_z"])]),
            "short": join([_cmp("streak", "gt", p["streak"] - 0.5), _cmp("range_z", "gt", p["range_z"])]), **_exits(p, tf)}


@template("gap_reaction", "gap", {**COMMON, "mode": ["fade", "follow"], "gap": [0.25, 0.5, 1.0], "hold": [2, 4, 8, 24]})
def _gap(p, tf):
    up, dn = _cmp("gap_atr", "gt", p["gap"]), _cmp("gap_atr", "lt", -p["gap"])
    long_, short = (dn, up) if p["mode"] == "fade" else (up, dn)
    return {"timeframe": tf, "direction": "both", "long": long_, "short": short, **_exits(p, tf)}


@template("silver_divergence", "cross_market", {**COMMON, "z": [1.0, 1.5, 2.0, 2.5], "horizon": ["s", "l"], "mode": ["catch_up", "continue"], "hold": [4, 12, 24]})
def _silver(p, tf):
    f = f"xagusd_div{p['horizon']}"
    hi, lo = _cmp(f, "gt", p["z"]), _cmp(f, "lt", -p["z"])
    long_, short = (lo, hi) if p["mode"] == "catch_up" else (hi, lo)
    return {"timeframe": tf, "direction": "both", "long": long_, "short": short, **_exits(p, tf)}


@template("dollar_move", "cross_market", {**COMMON, "sym": ["eurusd", "dollaridxusd", "usdjpy"], "z": [1.0, 1.5, 2.0], "hold": [4, 12, 24]})
def _dollar(p, tf):
    f = f"{p['sym']}_rets"
    weak_usd = _cmp(f, "gt" if p["sym"] == "eurusd" else "lt", p["z"] if p["sym"] == "eurusd" else -p["z"])
    strong_usd = _cmp(f, "lt" if p["sym"] == "eurusd" else "gt", -p["z"] if p["sym"] == "eurusd" else p["z"])
    return {"timeframe": tf, "direction": "both", "long": weak_usd, "short": strong_usd, **_exits(p, tf)}


@template("real_yield_shift", "macro", {**COMMON, "chg": [0.05, 0.1, 0.2], "hold": [8, 24, 48, 120]}, ("h1", "h4", "d1ny"))
def _ry(p, tf):
    return {"timeframe": tf, "direction": "both", "long": _cmp("real_yield_chg5", "lt", -p["chg"]), "short": _cmp("real_yield_chg5", "gt", p["chg"]), **_exits(p, tf)}


@template("fomc_window", "event", {**COMMON, "before": [6, 24, 48], "side": ["long", "short"], "hold": [4, 12, 24]}, ("m15", "h1", "h4"))
def _fomc(p, tf):
    cond = join([{"op": "between", "a": {"f": "hours_to_fomc"}, "lo": 0.0, "hi": float(p["before"])}])
    return {"timeframe": tf, "direction": p["side"], p["side"]: cond, **_exits(p, tf)}


@template("payroll_reaction", "event", {**COMMON, "since": [1, 2, 4], "mode": ["fade", "follow"], "z": [1.0, 2.0], "hold": [2, 4, 8]}, INTRADAY)
def _nfp(p, tf):
    near = {"op": "between", "a": {"f": "hours_since_nfp"}, "lo": 0.0, "hi": float(p["since"])}
    up, dn = _cmp("ret_z3", "gt", p["z"]), _cmp("ret_z3", "lt", -p["z"])
    long_, short = (dn, up) if p["mode"] == "fade" else (up, dn)
    return {"timeframe": tf, "direction": "both", "long": join([near, long_]), "short": join([near, short]), **_exits(p, tf)}


@template("time_series_momentum", "momentum", {**COMMON, "lookback": [24, 60], "z": [0.5, 1.0, 1.5], "hold": [24, 48, 120]}, ("h1", "h4", "d1ny"))
def _tsm(p, tf):
    f = f"ret_z{p['lookback']}"
    return {"timeframe": tf, "direction": "both", "long": _cmp(f, "gt", p["z"]), "short": _cmp(f, "lt", -p["z"]), **_exits(p, tf)}


@template("session_transition", "session", {**COMMON, "hour": [2, 3, 7, 8, 9, 10, 13, 14], "move": [0.5, 1.0, 1.5], "mode": ["fade", "follow"], "hold": [2, 4, 8]}, INTRADAY)
def _transition(p, tf):
    at = {"op": "in", "a": {"f": "ny_hour"}, "set": [int(p["hour"])]}
    up, dn = _cmp("ret_day_atr", "gt", p["move"]), _cmp("ret_day_atr", "lt", -p["move"])
    long_, short = (dn, up) if p["mode"] == "fade" else (up, dn)
    return {"timeframe": tf, "direction": "both", "long": join([at, long_]), "short": join([at, short]), **_exits(p, tf)}


@template("structure_break", "structure", {**COMMON, "kind": ["bos", "choch"], "htf_align": [True, False], "hold": [8, 24, 48]})
def _structure(p, tf):
    k = p["kind"]
    lng, sht = [_is(f"{k}_up")], [_is(f"{k}_down")]
    if p["htf_align"] and tf != "d1ny":
        h = "d1ny_trend" if tf in ("h4",) else "h4_trend"
        lng.append(_cmp(h, "gt", 0))
        sht.append(_cmp(h, "lt", 0))
    return {"timeframe": tf, "direction": "both", "long": join(lng), "short": join(sht), **_exits(p, tf)}


@template("liquidity_sweep_reversal", "liquidity", {**COMMON, "range_z": [0.0, 1.0, 2.0], "close_pos": [0.5, 0.6, 0.7], "hold": [4, 8, 16]})
def _swing_sweep(p, tf):
    return {"timeframe": tf, "direction": "both",
            "long": join([_is("sweep_low"), _cmp("range_z", "gt", p["range_z"]), _cmp("close_pos", "gt", p["close_pos"])]),
            "short": join([_is("sweep_high"), _cmp("range_z", "gt", p["range_z"]), _cmp("close_pos", "lt", 1 - p["close_pos"])]), **_exits(p, tf)}


@template("calendar_cell", "calendar", {**COMMON, "dow": [0, 1, 2, 3, 4], "hours": [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9, 10, 11], [12, 13, 14], [18, 19, 20]],
                                        "side": ["long", "short"], "hold": [1, 2, 4]}, INTRADAY)
def _cal(p, tf):
    cond = join([{"op": "in", "a": {"f": "dow"}, "set": [int(p["dow"])]}, {"op": "in", "a": {"f": "ny_hour"}, "set": [int(h) for h in p["hours"]]}])
    return {"timeframe": tf, "direction": p["side"], p["side"]: cond, **_exits(p, tf)}


@template("turn_of_month", "calendar", {**COMMON, "before": [1, 2, 3], "after": [1, 2, 3], "side": ["long", "short"], "hold": [24, 48, 120]}, ("h1", "h4", "d1ny"))
def _tom(p, tf):
    cond = {"op": "or", "args": [_cmp("tdoe", "lt", p["before"] + 0.5), _cmp("tdom", "lt", p["after"] + 0.5)]}
    return {"timeframe": tf, "direction": p["side"], p["side"]: cond, **_exits(p, tf)}


def build_template(name: str, params: dict, tf: str) -> dict:
    spec = TEMPLATES[name]["build"](params, tf)
    spec.setdefault("timeframe", tf)
    return spec


def templates_for(tf: str) -> list[str]:
    return sorted(n for n, t in TEMPLATES.items() if tf in t["tfs"])
