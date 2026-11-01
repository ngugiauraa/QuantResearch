"""Performance metrics. Win rate alone is never the headline: expectancy and drawdown come with it."""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantresearch.warehouse import calendar as cal


def _safe(numerator: float, denominator: float) -> float | None:
    return None if denominator == 0 or np.isnan(denominator) else float(numerator / denominator)


def trade_stats(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"trades": 0}
    net = trades["net"]
    wins, losses = net[net > 0], net[net < 0]
    gross_profit, gross_loss = float(wins.sum()), float(-losses.sum())
    r = trades["r_multiple"].dropna() if "r_multiple" in trades else pd.Series(dtype=float)
    holding = (pd.to_datetime(trades["exit_time"], utc=True) - pd.to_datetime(trades["entry_time"], utc=True)).dt.total_seconds()
    return {
        "trades": int(len(trades)),
        "net_profit": float(net.sum()),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "win_rate": float((net > 0).mean()),
        "profit_factor": _safe(gross_profit, gross_loss),
        "expectancy": float(net.mean()),
        "average_win": float(wins.mean()) if len(wins) else 0.0,
        "average_loss": float(losses.mean()) if len(losses) else 0.0,
        "average_r": float(r.mean()) if len(r) else None,
        "commission_paid": float(trades["commission"].sum()),
        "swap_paid": float(trades["swap"].sum()),
        "average_holding_hours": float(holding.mean() / 3600),
        "cost_sources": trades["cost_source"].value_counts().to_dict() if "cost_source" in trades else {},
    }


def equity_stats(equity: pd.Series, initial: float | None = None) -> dict:
    if len(equity) < 2:
        return {}
    daily = equity.groupby(equity.index.floor("D")).last()
    start = initial if initial is not None else float(equity.iloc[0])
    returns = daily.pct_change().dropna()
    years = max((equity.index[-1] - equity.index[0]).total_seconds() / (365.25 * 86400), 1e-9)
    drawdown = 1 - equity / equity.cummax()
    max_dd = float(drawdown.max())
    final = float(equity.iloc[-1])
    cagr = (final / start) ** (1 / years) - 1 if final > 0 and start > 0 else -1.0
    # 252 sessions a year; gold trades roughly the same number of weekdays.
    sd = float(returns.std())
    downside = float(np.sqrt((returns.clip(upper=0) ** 2).mean())) if len(returns) else 0.0
    dd_dollars = float((equity.cummax() - equity).max())
    return {
        "final_equity": final,
        "total_return": final / start - 1,
        "cagr": float(cagr),
        "max_drawdown": max_dd,
        "max_drawdown_usd": dd_dollars,
        "sharpe": _safe(float(returns.mean()) * np.sqrt(252), sd),
        "sortino": _safe(float(returns.mean()) * np.sqrt(252), downside),
        "calmar": _safe(float(cagr), max_dd),
        "recovery_factor": _safe(final - start, dd_dollars),
        "years": float(years),
    }


def exposure(trades: pd.DataFrame, equity: pd.Series) -> float:
    if trades.empty or len(equity) < 2:
        return 0.0
    intervals = sorted(zip(pd.to_datetime(trades["entry_time"], utc=True), pd.to_datetime(trades["exit_time"], utc=True)))
    covered = pd.Timedelta(0)
    current_start, current_end = intervals[0]
    for begin, end in intervals[1:]:
        if begin > current_end:
            covered += current_end - current_start
            current_start, current_end = begin, end
        else:
            current_end = max(current_end, end)
    covered += current_end - current_start
    total = equity.index[-1] - equity.index[0]
    return float(covered / total) if total > pd.Timedelta(0) else 0.0


def breakdowns(trades: pd.DataFrame, regimes: pd.Series | None = None) -> dict:
    """Performance by strategy tag, session, weekday, month, year and optional regime."""
    if trades.empty:
        return {}
    entry = pd.to_datetime(trades["entry_time"], utc=True)
    keys = {
        "by_strategy": trades["tag"].fillna(""),
        "by_session": cal.session_of(entry),
        "by_weekday": entry.dt.day_name(),
        "by_month": entry.dt.month,
        "by_year": entry.dt.year,
        "by_side": trades["side"].map({1: "long", -1: "short"}),
    }
    if regimes is not None and len(regimes):
        positions = regimes.index.searchsorted(entry, side="right") - 1
        labels = [regimes.iloc[p] if p >= 0 else "unknown" for p in positions]
        keys["by_regime"] = pd.Series(labels, index=trades.index)
    out = {}
    for name, key in keys.items():
        table = {}
        for label, group in trades.groupby(key, sort=True):
            net = group["net"]
            losses = -net[net < 0].sum()
            table[str(label)] = {
                "trades": int(len(group)), "net": float(net.sum()), "win_rate": float((net > 0).mean()),
                "expectancy": float(net.mean()), "profit_factor": _safe(float(net[net > 0].sum()), float(losses)),
            }
        out[name] = table
    return out


def report(trades: pd.DataFrame, equity: pd.Series, initial: float | None = None,
           regimes: pd.Series | None = None) -> dict:
    stats = {**trade_stats(trades), **equity_stats(equity, initial), "exposure": exposure(trades, equity)}
    if not trades.empty and len(equity) >= 2:
        years = max((equity.index[-1] - equity.index[0]).total_seconds() / (365.25 * 86400), 1e-9)
        stats["trades_per_year"] = float(len(trades) / years)
    stats["breakdowns"] = breakdowns(trades, regimes)
    return stats
