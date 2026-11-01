"""Name lookup and uniform running of strategy engines."""
from __future__ import annotations

import pandas as pd

from quantresearch.sim.costs import CostModel
from quantresearch.sim.engine import Engine, EngineConfig
from quantresearch.strategies.base import SignalStrategy, StrategyDef, attach_signals
from quantresearch.strategies.engines import DEFINITIONS

REGISTRY: dict[str, StrategyDef] = {d.name: d for d in DEFINITIONS}


def get(name: str) -> StrategyDef:
    if name not in REGISTRY:
        raise KeyError(f"Unknown strategy {name!r}. Available: {sorted(REGISTRY)}")
    return REGISTRY[name]


def prepare(defn: StrategyDef, features: pd.DataFrame, params: dict) -> pd.DataFrame:
    return attach_signals(features, defn.prepare(features, params))


def run(defn: StrategyDef, features: pd.DataFrame, params: dict, execution: pd.DataFrame | None = None,
        costs: CostModel | None = None, initial_equity: float = 100_000.0, risk_pct: float = 0.5,
        risk_gate=None, log_signal_bars: bool = False) -> Engine:
    signals = prepare(defn, features, params)
    config = EngineConfig(initial_equity=initial_equity, costs=costs or CostModel(), log_signal_bars=log_signal_bars)
    engine = Engine(signals if execution is None else execution, signals,
                    SignalStrategy(signals, defn.name, risk_pct=risk_pct), config, risk_gate=risk_gate)
    engine.run()
    engine.close_all()
    return engine
