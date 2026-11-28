"""Full test of the personal playbooks under the project's hard rules.

    python -m quantresearch.playbooks.report

Reads the trades written by `playbooks.setups` (fixed rules, confirmed by the trader before this test). Four
variants, each recorded once in the trial book: the three setups alone and the combined one-trade-a-day system.

The same rules as every catalogue strategy:
  - out of sample: the catalogue's walk-forward test windows (`experiment.make_folds`, 4-year train, 12-month test,
    up to the holdout); a window counts only if the 4 years before it had at least 20 trades, as in the walk-forward;
  - holdout: from the catalogue's holdout start (two years before the data ends) to the end, never used before;
  - costs: observed (real bid and ask) and stressed (+$1.07/oz per round trip);
  - sizing: 0.5% of equity at risk per trade, as the catalogue, so a trade's return is 0.5% x its R;
  - `catalogue_verdict`: 30+ OOS trades, positive OOS, positive holdout observed and stressed, project-wide
    deflated Sharpe above 0.95, and beating risk-matched long-only gold out of sample and in the holdout.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from quantresearch.core.config import Paths, paths
from quantresearch.playbooks.examples import load_trades
from quantresearch.playbooks.setups import SETUPS, combined
from quantresearch.research import experiment as ex
from quantresearch.research import stats

RISK = 0.005
EQUITY = 100_000.0
MIN_TRAIN_TRADES = 20
VARIANTS = (*SETUPS, "combined_one_a_day")
TITLES = {"ny_sweep_ifvg": "Setup 1: NY Open Sweep + iFVG", "london_asian_sweep": "Setup 2: London Open Asian-Range Sweep",
          "fvg_continuation": "Setup 3: 15m/1H FVG Trend Continuation", "combined_one_a_day": "Combined, one trade a day"}


def frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame([{"setup": r["setup"], "day": pd.Timestamp(r["day"]), "side": r["signal"]["side"],
                        "fill_ts": pd.Timestamp(r["trade"]["fill_ts"]), "r": r["trade"]["r"], "r_stress": r["trade"]["r_stress"],
                        "exit_reason": r["trade"]["exit_reason"]} for r in rows])
    return df.sort_values("fill_ts").reset_index(drop=True) if len(df) else df


def daily(df: pd.DataFrame, days: pd.DatetimeIndex, col: str = "r") -> pd.Series:
    """Daily return at 0.5% risk per trade; zero on days without a trade (all trades close the same day)."""
    s = (df.groupby(df["day"].dt.normalize())[col].sum() * RISK) if len(df) else pd.Series(dtype=float)
    return s.reindex(days, fill_value=0.0)


def max_streak(r: np.ndarray) -> int:
    best = cur = 0
    for x in r:
        cur = cur + 1 if x < 0 else 0
        best = max(best, cur)
    return best


def drawdown(r: np.ndarray) -> tuple[float, float]:
    """Max drawdown of the cumulative R curve, in R and as a % of equity at 0.5% risk (compounded)."""
    if len(r) == 0:
        return 0.0, 0.0
    cum = np.cumsum(r)
    dd_r = float(np.max(np.maximum.accumulate(np.r_[0, cum])[1:] - cum))
    eq = np.cumprod(1 + RISK * r)
    peak = np.maximum.accumulate(np.r_[1.0, eq])[1:]
    return dd_r, float(np.max((peak - eq) / peak) * 100)


def stats_block(df: pd.DataFrame) -> dict:
    r, rs = df["r"].to_numpy(), df["r_stress"].to_numpy()
    years = max(1e-9, (df["day"].max() - df["day"].min()).days / 365.25) if len(df) else 0
    dd_r, dd_pct = drawdown(r)
    return {"trades": int(len(df)), "trades_per_year": float(len(df) / years) if years else 0.0,
            "win_rate": float((r > 0).mean()) if len(r) else None, "breakeven_share": float((np.abs(r) < 0.05).mean()) if len(r) else None,
            "average_r": float(r.mean()) if len(r) else None, "expectancy_r_stressed": float(rs.mean()) if len(r) else None,
            "total_r": float(r.sum()), "total_r_stressed": float(rs.sum()), "max_losing_streak": max_streak(r),
            "max_drawdown_r": dd_r, "max_drawdown_pct": dd_pct,
            "exit_reasons": df["exit_reason"].value_counts().to_dict() if len(df) else {}}


def evaluate(name: str, df: pd.DataFrame, gold: pd.DataFrame, holdout_start: pd.Timestamp, end: pd.Timestamp, first_bar: pd.Timestamp) -> tuple[dict, np.ndarray]:
    folds = ex.make_folds(first_bar, holdout_start, 4, 12)
    kept, skipped = [], []
    for f in folds:
        train = df[(df["day"] >= f.train_start.tz_localize(None)) & (df["day"] < f.train_end.tz_localize(None))]
        test = df[(df["day"] >= f.train_end.tz_localize(None)) & (df["day"] < f.test_end.tz_localize(None))]
        row = {"test_start": str(f.train_end.date()), "test_end": str(f.test_end.date()), "train_trades": int(len(train)), "test_trades": int(len(test)),
               "test_r": float(test["r"].sum())}
        (kept if len(train) >= MIN_TRAIN_TRADES else skipped).append(row)
    oos_mask = np.zeros(len(df), bool)
    for k in kept:
        oos_mask |= ((df["day"] >= pd.Timestamp(k["test_start"])) & (df["day"] < pd.Timestamp(k["test_end"]))).to_numpy()
    oos, hold = df[oos_mask], df[df["day"] >= holdout_start.tz_localize(None)]
    bdays = pd.bdate_range(first_bar.tz_localize(None).normalize(), end.tz_localize(None).normalize())
    in_oos = np.zeros(len(bdays), bool)
    for k in kept:
        in_oos |= (bdays >= pd.Timestamp(k["test_start"])) & (bdays < pd.Timestamp(k["test_end"]))
    oos_days = bdays[in_oos]
    hold_days = bdays[bdays >= holdout_start.tz_localize(None)]
    d_oos, d_hold = daily(oos, oos_days), daily(hold, hold_days)
    d_hold_s = daily(hold, hold_days, "r_stress")
    to_utc = lambda s: pd.Series(s.to_numpy(), index=s.index.tz_localize("UTC"))
    bench_oos = ex.risk_matched_buy_and_hold(gold, to_utc(d_oos)) if len(d_oos) else {"available": False, "beats": False}
    bench_hold = ex.risk_matched_buy_and_hold(gold, to_utc(d_hold)) if len(d_hold) else {"available": False, "beats": False}
    by_year = {str(y): stats_block(g) for y, g in df.groupby(df["day"].dt.year)}
    report = {
        "strategy": f"pb_{name}", "title": TITLES[name], "grid": {}, "defaults": {"rules": "docs/my_playbooks.md", "variant": name}, "catalogue": True,
        "family": "personal_playbook", "created_at": datetime.now(timezone.utc).isoformat(), "holdout_start": str(holdout_start.date()),
        "all": stats_block(df), "out_of_sample_stats": stats_block(oos), "holdout_stats": stats_block(hold), "by_year": by_year,
        "folds": kept, "folds_skipped_too_few_training_trades": skipped,
        "walk_forward": {"oos_sharpe": float(stats.sharpe(d_oos.to_numpy())) if len(d_oos) > 2 else 0.0,
                         "deflated_sharpe": {"dsr": stats.probabilistic_sharpe(d_oos.to_numpy(), 0.0) if len(d_oos) > 2 else 0.0},
                         "out_of_sample": {"trades": int(len(oos)), "net_profit": float(EQUITY * d_oos.sum()),
                                           "net_profit_stress": float(EQUITY * RISK * oos["r_stress"].sum())}},
        "holdout": {"scenarios": {"observed": {"net_profit": float(EQUITY * d_hold.sum()), "trades": int(len(hold))},
                                  "stress": {"net_profit": float(EQUITY * d_hold_s.sum())}}},
        "risk_matched_benchmark": {"out_of_sample": bench_oos, "holdout": bench_hold,
                                   "beats_both": bool(bench_oos.get("beats") and bench_hold.get("beats")),
                                   "method": "Sharpe of the daily returns (0.5% risk per trade) vs long-only gold over the same days"},
    }
    return report, d_oos.to_numpy()


def run(p: Paths | None = None) -> dict:
    from quantresearch.research.catalogue import catalogue_verdict
    from quantresearch.research.trials import TrialBook, project_deflated_sharpe
    from quantresearch.warehouse.store import load_bars

    p = p or paths()
    rows = load_trades(p.research / "playbooks")
    sets = {s: frame([r for r in rows if r["setup"] == s]) for s in SETUPS}
    sets["combined_one_a_day"] = frame(combined(rows))
    gold = load_bars("d1", None, None, p=p)[["ts", "close_ts", "close"]]
    first_bar, end = gold["ts"].iloc[0], gold["close_ts"].iloc[-1]
    holdout_start = (end - pd.DateOffset(years=2)).normalize()
    reports, returns = {}, {}
    book = TrialBook(p)
    try:
        for name, df in sets.items():
            reports[name], returns[name] = evaluate(name, df, gold, holdout_start, end, first_bar)
            book.record_report(reports[name], f"research/reports/pb_{name}_latest.json")
        out = p.research / "reports"
        out.mkdir(parents=True, exist_ok=True)
        for name, rep in reports.items():
            project = project_deflated_sharpe(returns[name], book)
            rep["project_multiple_testing"] = {**project, "book": book.summary()}
            rep["catalogue_verdict"] = catalogue_verdict(rep, project)
            (out / f"pb_{name}_latest.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
            (p.research / "catalogue_returns").mkdir(parents=True, exist_ok=True)
            np.save(p.research / "catalogue_returns" / f"pb_{name}.npy", returns[name])
    finally:
        book.close()
    return reports


if __name__ == "__main__":
    for name, rep in run().items():
        v, a = rep["catalogue_verdict"], rep["all"]
        print(name, v["label"], {k: round(a[k], 3) if isinstance(a[k], float) else a[k] for k in ("trades", "trades_per_year", "win_rate", "average_r", "expectancy_r_stressed", "max_losing_streak", "max_drawdown_r", "max_drawdown_pct")},
              "fails", [k for k, ok in v["checks"].items() if not ok])
