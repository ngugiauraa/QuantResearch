"""Walk-forward evaluation and reproducible strategy reports.

Periods are strictly chronological:
  folds: train on [t, t + train), test on [t + train, t + train + test), then roll forward.
  holdout: the most recent period. It is excluded from all selection and evaluated once at the end.

A profitable report does not qualify a strategy for trading. The deflated Sharpe ratio is shown so
the number of variants tried is counted against the result.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from quantresearch.core.config import Paths, paths
from quantresearch.research import stats
from quantresearch.sim import metrics
from quantresearch.sim.costs import CostModel, scenarios
from quantresearch.sim.engine import Engine, EngineConfig
from quantresearch.strategies import registry
from quantresearch.strategies.base import SignalStrategy, StrategyDef, attach_signals, forward_return
from quantresearch.warehouse.store import friendly_warehouse_errors


@dataclass(frozen=True)
class Fold:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_end: pd.Timestamp


def make_folds(start: pd.Timestamp, end: pd.Timestamp, train_years: int, test_months: int) -> list[Fold]:
    folds = []
    cursor = start
    while True:
        train_end = cursor + pd.DateOffset(years=train_years)
        test_end = train_end + pd.DateOffset(months=test_months)
        if test_end > end:
            break
        folds.append(Fold(cursor, train_end, test_end))
        cursor = cursor + pd.DateOffset(months=test_months)
    return folds


def param_grid(defn: StrategyDef) -> list[dict]:
    keys = list(defn.grid)
    combos = itertools.product(*[defn.grid[k] for k in keys]) if keys else [()]
    return [defn.params(**dict(zip(keys, c))) for c in combos]


def _slice(frame: pd.DataFrame, start, end) -> pd.DataFrame:
    return frame[(frame["ts"] >= start) & (frame["ts"] < end)]


def _run_slice(signals: pd.DataFrame, name: str, start, end, costs: CostModel, execution: pd.DataFrame | None = None,
               equity: float = 100_000.0, risk_pct: float = 0.5) -> Engine:
    part = _slice(signals, start, end).reset_index(drop=True)
    exec_part = part if execution is None else _slice(execution, start, end).reset_index(drop=True)
    config = EngineConfig(initial_equity=equity, costs=costs, log_signal_bars=False, record_equity_every=1)
    engine = Engine(exec_part, part, SignalStrategy(part, name, risk_pct=risk_pct), config)
    engine.run()
    engine.close_all()
    return engine


def _daily_series(curve: pd.Series) -> pd.Series:
    """Daily returns of an equity curve, indexed by UTC date."""
    if len(curve) < 2:
        return pd.Series(dtype="float64")
    return curve.groupby(curve.index.floor("D")).last().pct_change().dropna()


def _utc_dates(index) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(index)
    return (idx.tz_convert("UTC").tz_localize(None) if idx.tz is not None else idx).normalize()


def risk_matched_buy_and_hold(features: pd.DataFrame, strategy_daily: pd.Series) -> dict:
    """Compare a strategy with a long-only gold position at the same risk over the same days.

    The gold position is scaled to the strategy's own daily volatility. Scaling does not change a Sharpe ratio, so
    the strategy beats it exactly when its Sharpe is higher than gold's over the same days; the vol-matched gold
    return is shown for scale. Flat days count for both (the strategy earned nothing on them).
    """
    if strategy_daily is None or len(strategy_daily) < 20:
        return {"available": False, "beats": False, "reason": "fewer than 20 days of strategy returns"}
    gold = features.groupby(_utc_dates(features["ts"]))["close"].last().pct_change().dropna()
    strat = pd.Series(strategy_daily.to_numpy(), index=_utc_dates(strategy_daily.index))
    strat = strat.groupby(level=0).last()
    days = strat.index.intersection(gold.index)
    if len(days) < 20:
        return {"available": False, "beats": False, "reason": "fewer than 20 common days"}
    s, g = strat.loc[days], gold.loc[days]
    sd_s, sd_g = s.std(ddof=1), g.std(ddof=1)
    matched = g * (sd_s / sd_g) if sd_g > 0 else g * 0
    sh_s, sh_g = stats.sharpe(s.to_numpy()), stats.sharpe(g.to_numpy())
    return {"available": True, "days": int(len(days)), "first": str(days[0].date()), "last": str(days[-1].date()),
            "strategy_sharpe": sh_s, "buy_hold_sharpe": sh_g, "strategy_return": float((1 + s).prod() - 1),
            "buy_hold_return_same_risk": float((1 + matched).prod() - 1), "beats": bool(sh_s > sh_g and sd_s > 0)}


def _daily_returns(engine: Engine) -> np.ndarray:
    curve = engine.equity_curve()
    if len(curve) < 2:
        return np.zeros(0)
    return curve.groupby(curve.index.floor("D")).last().pct_change().dropna().to_numpy()


def _score(engine: Engine, min_trades: int) -> float:
    if len(engine.trades) < min_trades:
        return -np.inf
    return stats.sharpe(_daily_returns(engine))


def regime_labels(features: pd.DataFrame) -> pd.Series:
    """Causal market-state label per bar, indexed by when it became known. Used for performance by regime."""
    from quantresearch.strategies.base import rolling_rank

    trend = np.where(features["adx"] > 25, "trending", "ranging")
    vol = np.where(rolling_rank(features["vol_20"]) > 2 / 3, "high_vol", "normal_vol")
    labels = pd.Series([f"{a}_{b}" for a, b in zip(trend, vol)], index=pd.DatetimeIndex(features["close_ts"]))
    return labels[features["adx"].notna().to_numpy() & features["vol_20"].notna().to_numpy()]


def walk_forward(defn: StrategyDef, features: pd.DataFrame, costs: CostModel, train_years: int = 4, test_months: int = 12,
                 holdout_years: int = 2, min_trades: int = 20, execution: pd.DataFrame | None = None,
                 equity: float = 100_000.0) -> dict:
    data_start, data_end = features["ts"].iloc[0], features["ts"].iloc[-1]
    holdout_start = (data_end - pd.DateOffset(years=holdout_years)).normalize()
    folds = make_folds(data_start, holdout_start, train_years, test_months)
    grid = param_grid(defn)
    prepared: dict[str, pd.DataFrame] = {}

    def signals_for(params: dict) -> pd.DataFrame:
        key = json.dumps(params, sort_keys=True, default=str)
        if key not in prepared:
            prepared[key] = attach_signals(features, defn.prepare(features, params))
        return prepared[key]

    records, oos_trades, oos_curves, trial_sharpes, sensitivity = [], [], [], [], {}
    balance = equity
    for fold in folds:
        best_params, best_score = None, -np.inf
        train = _slice(features, fold.train_start, fold.train_end)
        for params in grid:
            if defn.fit is not None:
                params = {**params, **defn.fit(train, params)}
            engine = _run_slice(signals_for(params) if defn.fit is None else
                                attach_signals(features, defn.prepare(features, params)),
                                defn.name, fold.train_start, fold.train_end, costs, equity=equity)
            score = _score(engine, min_trades)
            if np.isfinite(score):
                trial_sharpes.append(score)
                label = json.dumps({k: params[k] for k in defn.grid}, sort_keys=True, default=str)
                sensitivity.setdefault(label, []).append(score)
            if score > best_score:
                best_params, best_score = params, score
        if best_params is None:
            records.append({"train_start": str(fold.train_start), "test_end": str(fold.test_end), "skipped": "too few trades in training"})
            continue
        signals = attach_signals(features, defn.prepare(features, best_params))
        # Each fold compounds on the last one's ending balance.
        test = _run_slice(signals, defn.name, fold.train_end, fold.test_end, costs, execution, balance)
        balance = test.account.equity
        frame = test.trades_frame()
        oos_trades.append(frame)
        oos_curves.append(test.equity_curve())
        records.append({"train_start": str(fold.train_start.date()), "train_end": str(fold.train_end.date()),
                        "test_end": str(fold.test_end.date()), "train_sharpe": float(best_score),
                        "params": {k: best_params[k] for k in defn.grid},
                        "test_trades": int(len(frame)), "test_net": float(frame["net"].sum()) if len(frame) else 0.0})
    trades = pd.concat(oos_trades, ignore_index=True) if oos_trades else pd.DataFrame()
    curve = pd.concat(oos_curves) if oos_curves else pd.Series(dtype="float64")
    daily_series = _daily_series(curve)
    daily = daily_series.to_numpy()
    # Per-period Sharpe for the trial comparison: annual figures are scaled back to daily.
    trial_daily = [s / np.sqrt(252) for s in trial_sharpes]
    return {
        "folds": records, "fold_count": len(folds), "variants_per_fold": len(grid), "trial_count": len(trial_sharpes),
        "holdout_start": str(holdout_start.date()), "out_of_sample": metrics.report(trades, curve, equity, regime_labels(features)),
        "oos_sharpe": stats.sharpe(daily), "psr": stats.probabilistic_sharpe(daily),
        "deflated_sharpe": stats.deflated_sharpe(daily, trial_daily),
        "sharpe_ci95": list(stats.block_bootstrap_ci(daily, block=10, statistic=lambda r: stats.sharpe(r))) if len(daily) > 20 else None,
        "monte_carlo": stats.monte_carlo_trades(trades["net"].to_numpy(), equity) if len(trades) else {},
        "parameter_sensitivity": {k: {"mean_train_sharpe": float(np.mean(v)), "min": float(np.min(v)), "max": float(np.max(v))}
                                  for k, v in sensitivity.items()},
        "last_params": next((r["params"] for r in reversed(records) if "params" in r), None),
        "_daily_returns": daily,  # Private: for project-wide deflation (research/trials.py). Never saved.
        "_daily_series": daily_series,  # Private: the same, dated, for the buy-and-hold comparison.
    }


def run_hypotheses(defn: StrategyDef, features: pd.DataFrame, holdout_start: str, params: dict | None = None) -> list[dict]:
    """Conditional tests on pre-holdout data, corrected for multiple testing across the family."""
    params = params or defn.defaults
    cutoff = pd.Timestamp(holdout_start, tz="UTC")
    hypotheses = defn.hypotheses(features, params)
    before = features["ts"] < cutoff
    results = []
    for h in hypotheses:
        fwd = forward_return(features["close"], h.horizon)
        result = stats.conditional_test(fwd[before], h.mask[before], h.side, h.horizon)
        results.append({"id": h.id, "statement": h.statement, "side": h.side, "horizon_bars": h.horizon, **result})
    tested = [r for r in results if r.get("tested")]
    for record, correction in zip(tested, stats.benjamini_hochberg([r["p_one_sided"] for r in tested])):
        record["p_bh"] = correction["p_adjusted"]
        record["significant_fdr5"] = correction["significant"]
    return results


def dataset_version(p: Paths | None = None) -> str:
    p = p or paths()
    digest = hashlib.sha256()
    metas = sorted((p.bars / "m1").glob("year=*/month=*/meta.json")) if (p.bars / "m1").exists() else []
    for meta in metas:
        data = json.loads(meta.read_text(encoding="utf-8"))
        digest.update(f"{data['year']}-{data['month']}-{data['inputs_sha256']}".encode())
    return digest.hexdigest()[:16] if metas else "unbuilt"


def full_report(defn: StrategyDef, features: pd.DataFrame, costs: CostModel | None = None, train_years: int = 4,
                test_months: int = 12, holdout_years: int = 2, execution: pd.DataFrame | None = None,
                dataset: str | None = None) -> dict:
    costs = costs or CostModel()
    wf = walk_forward(defn, features, costs, train_years, test_months, holdout_years, execution=execution)
    oos_daily = wf.pop("_daily_returns")
    oos_series = wf.pop("_daily_series")
    holdout_start = pd.Timestamp(wf["holdout_start"], tz="UTC")
    params = wf["last_params"] or defn.defaults
    params = {**defn.defaults, **params}
    pre = _slice(features, features["ts"].iloc[0], holdout_start)
    if defn.fit is not None and len(pre):
        params = {**params, **defn.fit(pre, params)}
    signals = attach_signals(features, defn.prepare(features, params))
    end = features["ts"].iloc[-1] + pd.Timedelta(days=1)
    by_scenario = {}
    for label, model in scenarios(costs).items():
        engine = _run_slice(signals, defn.name, holdout_start, end, model, execution)
        rep = metrics.report(engine.trades_frame(), engine.equity_curve(), 100_000.0)
        rep.pop("breakdowns", None)
        by_scenario[label] = rep
    base = _run_slice(signals, defn.name, holdout_start, end, costs, execution)
    oos_bh, hold_bh = risk_matched_buy_and_hold(features, oos_series), risk_matched_buy_and_hold(features, _daily_series(base.equity_curve()))
    buy_hold = {"out_of_sample": oos_bh, "holdout": hold_bh, "beats_both": bool(oos_bh["beats"] and hold_bh["beats"]),
                "method": "Long-only gold scaled to the strategy's own daily volatility over the same days (out-of-sample folds, then the "
                          "holdout). Same risk means the comparison is Sharpe against Sharpe. Added 2026-11-28 as a required check: "
                          "a strategy that does not beat it in both periods is not promising."}
    hypotheses = run_hypotheses(defn, features, wf["holdout_start"], params)
    # Gold's own return over the same periods. A long-biased rule can look good just by holding a rising market.
    first_test = pd.Timestamp(wf["folds"][0]["train_end"], tz="UTC") if wf["folds"] and "train_end" in wf["folds"][0] else features["ts"].iloc[0]
    def gold_return(start, stop):
        part = _slice(features, start, stop)["close"]
        return float(part.iloc[-1] / part.iloc[0] - 1) if len(part) > 1 else None
    benchmark = {"out_of_sample_buy_hold_return": gold_return(first_test, holdout_start), "holdout_buy_hold_return": gold_return(holdout_start, end),
                 "note": "Unleveraged price return, before financing. Strategies risk 0.5 % per trade, so returns are not directly comparable; "
                         "it shows how much market drift was available."}
    return {
        "strategy": defn.name, "title": defn.title, "methodology": defn.methodology, "limitations": defn.limitations,
        "created_at": datetime.now(timezone.utc).isoformat(), "dataset_version": dataset or dataset_version(),
        "signal_timeframe": defn.signal_tf, "execution": "signal_bars" if execution is None else "finer_bars",
        "period": {"first": str(features["ts"].iloc[0]), "last": str(features["ts"].iloc[-1]), "holdout_start": wf["holdout_start"]},
        "cost_model": costs.as_dict(), "defaults": defn.defaults, "grid": defn.grid,
        "hypotheses": hypotheses, "walk_forward": wf, "benchmark": benchmark, "risk_matched_benchmark": buy_hold,
        "holdout": {"params": {k: params[k] for k in defn.grid}, "scenarios": by_scenario,
                    "report": metrics.report(base.trades_frame(), base.equity_curve(), 100_000.0, regime_labels(features))},
        "verdict": verdict(wf, by_scenario, buy_hold),
        "_oos_daily": oos_daily,
    }


def verdict(wf: dict, holdout: dict, buy_hold: dict | None = None) -> dict:
    """A plain summary. It can only say a result is promising or not; it never approves trading."""
    oos = wf["out_of_sample"]
    trades = oos.get("trades", 0)
    dsr = wf["deflated_sharpe"]["dsr"]
    stressed = holdout.get("stress", {}).get("net_profit") or 0.0
    observed = holdout.get("observed", {}).get("net_profit") or 0.0
    positive = (oos.get("net_profit") or 0) > 0 and wf["oos_sharpe"] > 0
    beats = bool(buy_hold and buy_hold.get("beats_both"))
    if trades < 30:
        label = "insufficient_evidence"
    elif positive and dsr > 0.95 and observed > 0 and stressed > 0 and beats:
        label = "survives_tests_needs_paper_trading"
    elif positive:
        label = "weak_or_not_significant"
    else:
        label = "no_edge_found"
    return {"label": label, "oos_trades": trades, "oos_sharpe": wf["oos_sharpe"], "deflated_sharpe": dsr,
            "holdout_net": observed, "holdout_net_stress": stressed, "beats_buy_and_hold": beats,
            "note": "Not a recommendation. Live use also needs paper trading and broker-specific cost checks."}


class Ledger:
    """A durable record of every experiment: hypothesis, data, parameters, method, results, limits."""

    def __init__(self, p: Paths | None = None):
        self.p = p or paths()
        self.p.research.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.p.research / "experiments.sqlite")
        self.con.execute(
            """CREATE TABLE IF NOT EXISTS experiments(
                 id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, strategy TEXT, dataset_version TEXT,
                 period TEXT, methodology TEXT, params TEXT, verdict TEXT, limitations TEXT, report_path TEXT)"""
        )

    def add(self, report: dict) -> Path:
        report = {k: v for k, v in report.items() if not k.startswith("_")}  # Private working values are never saved.
        folder = self.p.research / "reports"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = report["created_at"].replace(":", "").replace("-", "")[:15]
        path = folder / f"{report['strategy']}_{stamp}.json"
        path.write_text(json.dumps(report, indent=1, default=_json_default), encoding="utf-8")
        latest = folder / f"{report['strategy']}_latest.json"
        latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        self.con.execute(
            "INSERT INTO experiments(created_at,strategy,dataset_version,period,methodology,params,verdict,limitations,report_path) VALUES(?,?,?,?,?,?,?,?,?)",
            (report["created_at"], report["strategy"], report["dataset_version"], json.dumps(report["period"]),
             report["methodology"], json.dumps(report["holdout"]["params"], default=str), json.dumps(report["verdict"], default=str),
             json.dumps(report["limitations"]), path.relative_to(self.p.root).as_posix()),
        )
        self.con.commit()
        return path

    def all(self) -> pd.DataFrame:
        return pd.read_sql("SELECT * FROM experiments ORDER BY id DESC", self.con)

    def close(self) -> None:
        self.con.close()


def _json_default(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else float(value)
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    return str(value)


def load_research_frame(defn: StrategyDef, start=None, end=None, p: Paths | None = None) -> pd.DataFrame:
    from quantresearch.external import store as ext
    from quantresearch.features.registry import build_features
    from quantresearch.warehouse.store import load_bars

    bars = load_bars(defn.signal_tf, start, end, p=p)
    frame = build_features(bars)
    if defn.needs:
        frame = ext.attach_cross(frame, defn.needs, p)
    try:
        macro = ext.macro_features(frame, p=p)
        frame = pd.concat([frame, macro], axis=1)
        events = ext.read_events(p)
        cot = events[events["series"] == "COT_MM_NET"].sort_values("event_ts").copy()
        if len(cot):
            from quantresearch.features import pit
            cot["value"] = (cot["value"] - cot["value"].rolling(104, min_periods=52).mean()) / cot["value"].rolling(104, min_periods=52).std()
            frame["cot_mm_z"] = pit.align_events(frame["close_ts"], cot.dropna(subset=["value"]), "COT_MM_NET")
    except (KeyError, FileNotFoundError):
        pass
    return frame


@friendly_warehouse_errors
def main() -> None:
    ap = argparse.ArgumentParser(description="Run strategy research reports.")
    ap.add_argument("names", nargs="*", help="Strategy names; all seven if omitted.")
    ap.add_argument("--start")
    ap.add_argument("--end")
    args = ap.parse_args()
    ledger = Ledger()
    for name in args.names or list(registry.REGISTRY):
        defn = registry.get(name)
        features = load_research_frame(defn, args.start, args.end)
        report = full_report(defn, features)
        path = ledger.add(report)
        print(json.dumps({"strategy": name, "report": str(path), **report["verdict"]}, default=_json_default), flush=True)
    ledger.close()


if __name__ == "__main__":
    main()
