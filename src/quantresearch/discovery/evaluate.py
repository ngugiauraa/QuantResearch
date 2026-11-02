"""Turn kernel trades into research statistics.

Sizing convention: every trade risks a fixed `risk_pct` of a constant reference equity (no compounding), so a
trade's return is risk_pct x R. Daily returns sum the trades closed on each trading day, with zeros on days
without a close, over every trading day of the window. Dollar figures assume $100,000 and 0.5 % risk. The
authoritative event engine, with compounding and lot rounding, re-runs finalists (see `confirm.py`).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantresearch.discovery.expr import Costs, Evaluator, SCENARIOS, Trades, compile_spec, run_compiled
from quantresearch.discovery.kernel import REASONS

EQUITY = 100_000.0
NS_YEAR = 365.25 * 86400e9


def daily_series(tr: Trades, ev: Evaluator, risk_pct: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    store = ev.store
    days = np.unique(np.asarray(store.day_ns[ev.start:ev.end]))
    rets = np.zeros(len(days))
    if len(tr):
        exit_days = np.asarray(store.day_ns)[tr.lo + tr.exit]
        pos = np.searchsorted(days, exit_days)
        np.add.at(rets, np.clip(pos, 0, len(days) - 1), tr.r * risk_pct / 100.0)
    return days, rets


def sharpe(rets: np.ndarray) -> float:
    if len(rets) < 3:
        return 0.0
    sd = rets.std(ddof=1)
    return float(rets.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0


def summarize(tr: Trades, ev: Evaluator, risk_pct: float = 0.5, detail: bool = True) -> dict:
    days, rets = daily_series(tr, ev, risk_pct)
    years = max((ev.store.close_ns[ev.end - 1] - ev.store.open_ns[ev.start]) / NS_YEAR, 1e-9) if ev.end > ev.start else 1e-9
    n = len(tr)
    out = {"trades": int(n), "years": float(years), "trades_per_year": float(n / years), "sharpe": sharpe(rets)}
    if n == 0:
        out.update(exp_r=0.0, total_r=0.0, net_usd=0.0, win_rate=None, max_dd=0.0, exposure=0.0)
        return out
    r = tr.r
    wins, losses = r[r > 0], r[r < 0]
    eq = np.cumsum(rets)
    dd = float(np.max(np.maximum.accumulate(np.r_[0.0, eq])[1:] - eq)) if len(eq) else 0.0
    bars = max(ev.end - ev.start, 1)
    out.update({
        "exp_r": float(r.mean()), "median_r": float(np.median(r)), "sd_r": float(r.std(ddof=1)) if n > 1 else 0.0,
        "total_r": float(r.sum()), "net_usd": float(r.sum() * EQUITY * risk_pct / 100.0),
        "expectancy_usd": float(r.mean() * EQUITY * risk_pct / 100.0),
        "win_rate": float((r > 0).mean()), "avg_win_r": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_r": float(losses.mean()) if len(losses) else 0.0,
        "payoff": float(wins.mean() / -losses.mean()) if len(wins) and len(losses) else None,
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else None,
        "max_dd": dd, "max_dd_usd": dd * EQUITY,
        "exposure": float(np.sum(tr.exit - tr.entry + 1) / bars),
        "top_trade_share": float(r.max() / wins.sum()) if len(wins) else 1.0,
        "avg_hold_bars": float(np.mean(tr.exit - tr.entry + 1)),
    })
    if detail:
        sides = {}
        for label, sgn in (("long", 1), ("short", -1)):
            m = tr.side == sgn
            sides[label] = {"trades": int(m.sum()), "exp_r": float(r[m].mean()) if m.any() else None, "total_r": float(r[m].sum())}
        out["sides"] = sides
        entry_year = pd.DatetimeIndex(np.asarray(ev.store.open_ns)[tr.lo + tr.entry], tz="UTC").year
        by_year = pd.Series(r).groupby(np.asarray(entry_year)).agg(["count", "sum", "mean"])
        out["years_detail"] = {str(int(y)): {"trades": int(row["count"]), "total_r": float(row["sum"]), "exp_r": float(row["mean"])}
                               for y, row in by_year.iterrows()}
        active = by_year[by_year["count"] >= 5]
        out["positive_year_share"] = float((active["sum"] > 0).mean()) if len(active) else None
        total_pos = active["sum"].clip(lower=0).sum()
        out["top_year_share"] = float(active["sum"].max() / total_pos) if total_pos > 0 else None
        out["exit_reasons"] = {REASONS[int(k)]: int(v) for k, v in zip(*np.unique(tr.reason, return_counts=True))}
    return out


def evaluate(spec: dict, ev: Evaluator, costs: Costs | str = "observed", risk_pct: float = 0.5, detail: bool = True,
             compiled=None) -> tuple[dict, Trades]:
    cost = SCENARIOS[costs] if isinstance(costs, str) else costs
    c = compiled or compile_spec(spec, ev)
    tr = run_compiled(c, ev, cost)
    return summarize(tr, ev, risk_pct, detail), tr


def entry_signature(tr: Trades, ev: Evaluator) -> str:
    """Behaviour fingerprint: the bars and sides where trades opened. Equivalent rules share it."""
    import hashlib
    h = hashlib.sha256()
    h.update(np.asarray(tr.entry + tr.lo, dtype=np.int64).tobytes())
    h.update(np.asarray(tr.side, dtype=np.int8).tobytes())
    h.update(np.asarray(tr.exit + tr.lo, dtype=np.int64).tobytes())
    return h.hexdigest()[:16]


def regime_split(tr: Trades, ev: Evaluator) -> dict:
    """Performance by volatility and trend state at entry, using causal features."""
    if not len(tr):
        return {}
    idx = tr.lo + tr.entry - 1  # state at the signal bar's close
    idx = np.clip(idx, 0, None)
    store = ev.store
    out = {}
    r = tr.r
    tests = {}
    if "vol_rank" in store.index:
        vr = np.asarray(store.feature("vol_rank"))[idx]
        tests["volatility"] = np.where(vr > 2 / 3, "high_vol", np.where(vr < 1 / 3, "low_vol", "mid_vol"))
    if "adx" in store.index:
        adx = np.asarray(store.feature("adx"))[idx]
        tests["trend"] = np.where(adx > 25, "trending", "ranging")
    if "ny_hour" in store.index:
        h = np.asarray(store.feature("ny_hour"))[idx]
        tests["session"] = np.select([(h >= 3) & (h < 8), (h >= 8) & (h < 12), (h >= 12) & (h < 17)], ["london", "ny_morning", "ny_afternoon"], "asia_evening")
    for name, labels in tests.items():
        table = {}
        for lab in sorted(set(labels.tolist())):
            m = labels == lab
            table[lab] = {"trades": int(m.sum()), "exp_r": float(r[m].mean()), "total_r": float(r[m].sum())}
        out[name] = table
    return out
