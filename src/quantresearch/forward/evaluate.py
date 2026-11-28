"""The single pass/fail of forward test 001, exactly as pre-registered. Interim values are shown on the dashboard;
the decision is taken once, when it falls due, and is then frozen."""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantresearch.forward import plan
from quantresearch.forward.journal import Journal
from quantresearch.research import experiment as ex


def max_drawdown_pct(curve: pd.Series) -> float:
    if curve.empty:
        return 0.0
    v = curve.to_numpy(dtype="float64")
    peak = np.maximum.accumulate(v)
    return float(np.max((peak - v) / peak) * 100)


def evaluate(journal: Journal, start: dict, gold_d1: pd.DataFrame, now: pd.Timestamp) -> dict:
    t0, now = pd.Timestamp(start["at"]), pd.Timestamp(now)
    eval_date, hard_end = t0 + pd.DateOffset(months=plan.EVAL_MONTHS), t0 + pd.DateOffset(months=plan.MAX_MONTHS)
    closed = journal.trades("closed")
    net = float(sum((t["profit"] or 0) + (t["commission"] or 0) + (t["swap"] or 0) for t in closed))
    curve = journal.equity_curve("ledger")
    daily = ex._daily_series(curve)
    gold = gold_d1[(gold_d1["ts"] >= t0.floor("D")) & (gold_d1["close_ts"] <= now)][["ts", "close"]] if len(gold_d1) else gold_d1
    bench = ex.risk_matched_buy_and_hold(gold, daily) if len(gold) else {"available": False, "beats": False, "reason": "no gold bars"}
    dd = max_drawdown_pct(curve)
    trades = journal.trades()
    slips = [t["slippage_entry"] for t in trades if t["slippage_entry"] is not None] + \
            [t["slippage_exit"] for t in trades if t["slippage_exit"] is not None]
    spreads = [t["spread_entry"] for t in trades if t["spread_entry"] is not None] + \
              [t["spread_exit"] for t in trades if t["spread_exit"] is not None]
    mean_slip = float(np.mean(slips)) if slips else None
    mean_spread = float(np.mean(spreads)) if spreads else None
    criteria = {
        "net_positive": {"pass": net > 0, "value": net, "rule": "net P&L after the demo account's spread, commission and swap > 0"},
        "beats_buy_and_hold": {"pass": bool(bench.get("beats")), "value": bench,
                               "rule": "daily Sharpe above long-only XAUUSD's over the same days (risk-matched)"},
        "drawdown_under_10": {"pass": dd < plan.MAX_DRAWDOWN_PCT, "value": dd, "rule": "max drawdown of the test's equity < 10%"},
        "costs_within_model": {"pass": (mean_slip is not None and mean_slip <= plan.MAX_MEAN_SLIPPAGE
                                        and mean_spread is not None and mean_spread <= plan.MAX_MEAN_SPREAD),
                               "value": {"mean_slippage": mean_slip, "mean_spread": mean_spread, "fills": len(slips)},
                               "rule": f"mean adverse slippage per fill <= ${plan.MAX_MEAN_SLIPPAGE}/oz and mean spread <= ${plan.MAX_MEAN_SPREAD}/oz"},
    }
    due = now >= eval_date and (len(closed) >= plan.MIN_TRADES or now >= hard_end)
    return {"as_of": now.isoformat(), "closed_trades": len(closed), "open_trades": len(trades) - len(closed), "net": net,
            "eval_date": eval_date.isoformat(), "hard_end": hard_end.isoformat(), "min_trades": plan.MIN_TRADES,
            "due": bool(due), "passed": bool(all(c["pass"] for c in criteria.values())) if due else None, "criteria": criteria,
            "note": "Interim: the decision is taken once, at the evaluation date." if not due else "Decision due."}
