"""Controlled historical experiments combining strategies, skills, ML and macro context.

An experiment is declarative and reproducible. Everything fed to the replay is causal, and ML is refused for
any period before its training ended, because those predictions would be in-sample.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from quantresearch.decision.engine import DecisionPolicy, DecisionStrategy
from quantresearch.ml import pipeline as ml
from quantresearch.risk.engine import RiskEngine, RiskLimits, simulation_gate
from quantresearch.sim import metrics
from quantresearch.sim.costs import CostModel
from quantresearch.sim.engine import Engine, EngineConfig
from quantresearch.skills import xauusd
from quantresearch.strategies import get
from quantresearch.strategies.base import attach_signals
from quantresearch.timemachine import lookahead
from quantresearch.timemachine.replay import ReplayConfig, ReplaySession


@dataclass
class ExperimentSpec:
    name: str = "experiment"
    strategies: list[str] = field(default_factory=lambda: ["trend"])
    params: dict = field(default_factory=dict)
    skills: list[str] = field(default_factory=list)  # e.g. "scanner_filter", "fomc_block"
    use_ml: bool = False
    ml_mode: str = "veto"
    use_risk_engine: bool = True
    min_votes: int = 1
    conflict: str = "stand_down"
    initial_equity: float = 100_000.0
    costs: dict = field(default_factory=dict)
    max_hold: int | None = None  # Only used when no voting strategy sets its own limit.


def build_voters(spec: ExperimentSpec, frame: pd.DataFrame, fit_end=None) -> dict[str, pd.DataFrame]:
    voters = {}
    for name in spec.strategies:
        defn = get(name)
        params = defn.params(**spec.params.get(name, {}))
        # Cells supplied by the caller were fitted on earlier data, so they are not fitted again here.
        if defn.fit is not None and "long_cells" not in params:
            cutoff = pd.Timestamp(fit_end) if fit_end is not None else frame["ts"].iloc[len(frame) // 2]
            cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
            params = {**params, **defn.fit(frame[frame["ts"] < cutoff], params)}
        voters[name] = defn.prepare(frame, params)
    return voters


def skill_context(spec: ExperimentSpec, frame: pd.DataFrame) -> pd.DataFrame | None:
    """Skills that act as historical gates. Each uses bars closed by then, or a calendar known in advance."""
    blocked = pd.Series("", index=frame.index, dtype=object)
    if "fomc_block" in spec.skills and "hours_to_fomc" in frame:
        near = frame["hours_to_fomc"].between(0, 2)
        blocked[near] = "within 2 hours of an FOMC decision"
    if "scanner_filter" in spec.skills:
        # Upstream's oversold rule, as a filter on the closes available at each bar.
        rsi = frame["rsi_14"]
        blocked[(rsi < 15) | (rsi > 85)] = "scanner: extreme RSI"
    if "wide_spread_block" in spec.skills and "spread_z" in frame:
        blocked[frame["spread_z"] > 4] = "spread far above its recent level"
    if not (blocked != "").any() and not spec.skills:
        return None
    return pd.DataFrame({"blocked": blocked})


def ml_view(model: ml.TrainedModel, frame: pd.DataFrame) -> pd.DataFrame:
    train_end = pd.Timestamp(model.train_end)
    if frame["ts"].iloc[0] <= train_end:
        raise ValueError(f"The replay starts {frame['ts'].iloc[0]}, inside the model's training period, which ended {train_end}. "
                         "Those predictions would be in-sample.")
    return ml.predict(model, ml.add_calendar(frame))


def build_session(spec: ExperimentSpec, frame: pd.DataFrame, execution: pd.DataFrame | None = None, model: ml.TrainedModel | None = None,
                  fit_end=None, risk: RiskEngine | None = None, voters: dict | None = None) -> ReplaySession:
    voters = build_voters(spec, frame, fit_end) if voters is None else voters
    predictions = ml_view(model, frame) if spec.use_ml and model is not None else None
    context = skill_context(spec, frame)
    policy = DecisionPolicy(min_votes=spec.min_votes, conflict=spec.conflict, ml_mode=spec.ml_mode if spec.use_ml else "off")
    atr_lookup = frame["atr_14"].to_numpy(dtype="float64")

    def factory():
        return DecisionStrategy(frame, voters, policy, predictions, context, tag=spec.name, max_hold=spec.max_hold)

    gate = None
    if spec.use_risk_engine:
        engine_risk = risk or RiskEngine(RiskLimits(max_order_lots=50, max_total_lots=100, max_spread_usd=50, max_slippage_usd=1e6, max_atr_pct=100))
        gate = simulation_gate(engine_risk, atr_of=lambda eng: float(atr_lookup[max(0, eng.signal_index)]) if not np.isnan(atr_lookup[max(0, eng.signal_index)]) else None)
    config = ReplayConfig(name=spec.name, strategy="+".join(spec.strategies), params=spec.params, initial_equity=spec.initial_equity, costs=spec.costs)
    return ReplaySession(frame if execution is None else execution, frame, factory, config, risk_gate=gate)


def run_experiment(spec: ExperimentSpec, frame: pd.DataFrame, execution: pd.DataFrame | None = None, model: ml.TrainedModel | None = None,
                   fit_end=None, voters: dict | None = None) -> dict:
    session = build_session(spec, frame, execution, model, fit_end, voters=voters)
    session.to_end()
    engine = session.engine
    engine.close_all()
    return {
        "spec": asdict(spec), "digest": engine.digest(), "report": metrics.report(engine.trades_frame(), engine.equity_curve(), spec.initial_equity),
        "decisions": [asdict(d) for d in engine.strategy.decisions[-200:]],
        "decision_counts": {k: sum(1 for d in engine.strategy.decisions if d.action == k) for k in ("long", "short", "none")},
        "events": len(engine.events), "record": session.record(),
    }


def compare(specs: list[ExperimentSpec], frame: pd.DataFrame, execution=None, model=None, fit_end=None) -> list[dict]:
    """Same data, same costs, different components: a like-for-like table."""
    rows = []
    for spec in specs:
        result = run_experiment(spec, frame, execution, model, fit_end)
        r = result["report"]
        rows.append({"name": spec.name, "strategies": spec.strategies, "skills": spec.skills, "ml": spec.use_ml, "trades": r.get("trades", 0),
                     "net_profit": r.get("net_profit", 0.0), "sharpe": r.get("sharpe"), "max_drawdown": r.get("max_drawdown"),
                     "profit_factor": r.get("profit_factor"), "win_rate": r.get("win_rate"), "digest": result["digest"]})
    return rows


def integrity(spec: ExperimentSpec, frame_builder, bars: pd.DataFrame, cutoff, model: ml.TrainedModel | None = None) -> lookahead.IntegrityResult:
    """The look-ahead test applied to a whole orchestrated experiment.

    `frame_builder` must compute features from whatever bars it is given, so truncation really does remove the future.
    """
    def featurize(part):
        return frame_builder(part.reset_index(drop=True))

    full = featurize(bars)
    fit_end = full["ts"].iloc[len(full) // 2]

    def factory_for(frame):
        voters = build_voters(spec, frame, fit_end)
        predictions = ml_view(model, frame) if spec.use_ml and model is not None else None
        policy = DecisionPolicy(min_votes=spec.min_votes, conflict=spec.conflict, ml_mode=spec.ml_mode if spec.use_ml else "off")
        return DecisionStrategy(frame, voters, policy, predictions, skill_context(spec, frame), tag=spec.name, max_hold=spec.max_hold)

    # The strategy object is built per run, from that run's own frame.
    class Wrapped(DecisionStrategy):
        def __init__(self):
            pass

        def on_start(self, ctx):
            inner = factory_for(ctx._e.signals)
            self.__dict__.update(inner.__dict__)

    return lookahead.check(bars, bars, Wrapped, cutoff, config=EngineConfig(initial_equity=spec.initial_equity, costs=CostModel(**spec.costs)), featurize=featurize)


def holdout_comparison(start: str, end: str | None = None, fit_end: str | None = None, out: str | None = None) -> list[dict]:
    """Same period, same observed costs: each engine alone, then combinations, skill gates and an ML veto.

    Run once on the holdout. Any result here is one look at one period, not evidence of a lasting edge.
    """
    from quantresearch.core.config import paths
    from quantresearch.research.experiment import load_research_frame
    from quantresearch.strategies import REGISTRY

    defn = get("stat_arb")  # Its frame carries every cross-market column the others could need.
    frame = load_research_frame(defn, None, end)
    registry = ml.ModelRegistry()
    # The veto is measured with the newest model even if it was not promoted, and labelled so.
    rows_ = registry.rows()
    promoted = registry.promoted()
    model = promoted or (registry.load(rows_[0]["id"]) if rows_ else None)
    in_window = (frame["ts"] >= pd.Timestamp(start, tz="UTC")).to_numpy()
    window = frame[in_window].reset_index(drop=True)
    # Each engine uses the parameters its walk-forward chose before the holdout, so this ties to its own report.
    chosen: dict[str, dict] = {}
    for name in REGISTRY:
        path = paths().research / "reports" / f"{name}_latest.json"
        if path.exists():
            chosen[name] = json.loads(path.read_text(encoding="utf-8"))["holdout"]["params"]
    specs = [ExperimentSpec(name=name, strategies=[name], use_risk_engine=False, params={name: chosen.get(name, {})}) for name in REGISTRY]
    all_names = [n for n in REGISTRY if n != "behavioral"]  # Behavioral signals on M15, this comparison is on H1.
    specs = [s for s in specs if s.name in all_names]
    specs += [
        ExperimentSpec(name="all_majority", strategies=all_names, conflict="majority", use_risk_engine=False),
        ExperimentSpec(name="all_two_votes", strategies=all_names, conflict="majority", min_votes=2, use_risk_engine=False),
        ExperimentSpec(name="trend_with_gates", strategies=["trend"], skills=["fomc_block", "wide_spread_block"], use_risk_engine=False),
        ExperimentSpec(name="all_majority_risk_engine", strategies=all_names, conflict="majority", use_risk_engine=True),
    ]
    if model is not None:
        label = "all_majority_ml_veto" if promoted is not None else "all_majority_ml_veto_unpromoted_model"
        specs.append(ExperimentSpec(name=label, strategies=all_names, conflict="majority", use_ml=True, use_risk_engine=False))
    # Seasonal cells are chosen on data before the holdout only, by fitting on the full frame's earlier part.
    rows = []
    for spec in specs:
        for name in spec.strategies:
            spec.params.setdefault(name, dict(chosen.get(name, {})))
            defn_item = get(name)
            if defn_item.fit is not None:
                pre = frame[frame["ts"] < pd.Timestamp(fit_end or start, tz="UTC")]
                spec.params[name] = {**spec.params[name], **defn_item.fit(pre, defn_item.params(**spec.params[name]))}
        # Signals are computed on all history so rolling windows are warm, then cut to the holdout.
        full_voters = build_voters(spec, frame, fit_end or start)
        voters = {k: v[in_window].reset_index(drop=True) for k, v in full_voters.items()}
        result = run_experiment(spec, window, model=model if spec.use_ml else None, fit_end=fit_end or start, voters=voters)
        r = result["report"]
        rows.append({"name": spec.name, "trades": r.get("trades", 0), "net_profit": r.get("net_profit", 0.0), "sharpe": r.get("sharpe"), "max_drawdown": r.get("max_drawdown"),
                     "profit_factor": r.get("profit_factor"), "expectancy": r.get("expectancy"), "win_rate": r.get("win_rate"), "decisions": result["decision_counts"], "digest": result["digest"]})
        print(json.dumps(rows[-1], default=str), flush=True)
    target = paths().research / "reports" / "holdout_comparison.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"start": start, "end": end, "model": None if model is None else model.name, "rows": rows}, indent=1, default=str), encoding="utf-8")
    return rows


def pre_trade_check(m5: pd.DataFrame, plan: dict, when, spread: float = 0.0, replays: int = 2000) -> dict:
    """The Outcome Simulator as a pre-trade step, using only sessions before `when`."""
    from quantresearch.skills import outcome

    stamp = pd.Timestamp(when)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    return outcome.simulate_plan(m5[m5["close_ts"] <= stamp], plan["entry"], plan["stop"], plan["target"], when, replays=replays, spread=spread)
