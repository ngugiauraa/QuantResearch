"""The mandatory look-ahead integrity test.

1. Run the simulation on the complete dataset through time T and record everything at or before T.
2. Truncate the dataset at T.
3. Re-run.
4. Compare. Any difference means future data influenced the past.

Truncation keeps only bars that have closed by T. The one legitimate difference is a fill on the
bar that OPENS exactly at T, which the truncated run cannot have because that bar's data is gone.
Those events are the only ones excluded, and they are counted in the report so nothing is hidden.
There is no other allowed nondeterminism: the engine uses no random numbers or wall clock.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from quantresearch.features.registry import build_features
from quantresearch.sim.engine import Engine, EngineConfig, Strategy

# Events a fill or exit produces while handling the bar that opens at T.
EXECUTION_EVENTS = {"fill", "partial_fill", "order_expired"}


@dataclass
class IntegrityResult:
    cutoff: pd.Timestamp
    passed: bool
    feature_rows: int
    feature_differences: list[str]
    events_compared: int
    events_excluded_at_cutoff: int
    event_differences: list[dict]
    equity_points: int
    equity_differences: int
    trades_compared: int

    def as_dict(self) -> dict:
        return {k: (str(v) if isinstance(v, pd.Timestamp) else v) for k, v in self.__dict__.items()}


def _comparable(events: list[dict], cutoff: pd.Timestamp) -> tuple[list[dict], int]:
    limit = cutoff.isoformat()
    kept, excluded = [], 0
    for event in events:
        if event["time"] > limit:
            continue
        if event["time"] == limit and event["event"] in EXECUTION_EVENTS:
            excluded += 1
            continue
        kept.append({k: v for k, v in event.items() if k != "seq"})
    return kept, excluded


def compare_frames(full: pd.DataFrame, truncated: pd.DataFrame, columns: list[str]) -> list[str]:
    """Columns whose values differ, allowing only floating-point noise from rolling sums."""
    differences = []
    if len(full) != len(truncated):
        return [f"row count {len(full)} != {len(truncated)}"]
    for name in columns:
        a, b = full[name].to_numpy(dtype="float64"), truncated[name].to_numpy(dtype="float64")
        if not np.allclose(a, b, rtol=1e-11, atol=1e-11, equal_nan=True):
            first = int(np.flatnonzero(~np.isclose(a, b, rtol=1e-11, atol=1e-11, equal_nan=True))[0])
            differences.append(f"{name} first differs at row {first}: {a[first]} vs {b[first]}")
    return differences


def check(execution: pd.DataFrame, signal_bars: pd.DataFrame, strategy_factory: Callable[[], Strategy], cutoff,
          config: EngineConfig | None = None, featurize: Callable[[pd.DataFrame], pd.DataFrame] = build_features,
          risk_gate=None) -> IntegrityResult:
    cutoff = pd.Timestamp(cutoff)
    cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
    config = config or EngineConfig()

    # Full-history run: features see everything, and the engine runs through T.
    full_features = featurize(signal_bars)
    full_engine = Engine(execution, full_features, strategy_factory(), config, risk_gate=risk_gate)
    full_engine.run(until=cutoff)
    # Show the strategy any signal bar closing exactly at T, as the truncated run's ending does.
    full_engine._fire_signals(cutoff.value)

    # Truncated run: nothing after T exists.
    # A quote has no duration, so it is known at its own time. A bar is known when it closes.
    known_at = execution["close_ts"] if "close_ts" in execution else execution["ts"]
    exec_cut = execution[known_at <= cutoff].reset_index(drop=True)
    signal_cut = signal_bars[signal_bars["close_ts"] <= cutoff].reset_index(drop=True)
    cut_features = featurize(signal_cut)
    cut_engine = Engine(exec_cut, cut_features, strategy_factory(), config, risk_gate=risk_gate)
    cut_engine.run()

    numeric = [c for c in cut_features.columns if pd.api.types.is_numeric_dtype(cut_features[c])]
    visible = full_features[full_features["close_ts"] <= cutoff].reset_index(drop=True)
    feature_diffs = compare_frames(visible, cut_features, numeric)

    full_events, excluded = _comparable(full_engine.events, cutoff)
    cut_events, cut_excluded = _comparable(cut_engine.events, cutoff)
    differences = []
    for index, (a, b) in enumerate(zip(full_events, cut_events)):
        if a != b:
            differences.append({"index": index, "full": a, "truncated": b})
            break
    if len(full_events) != len(cut_events):
        differences.append({"full_events": len(full_events), "truncated_events": len(cut_events)})

    full_eq = full_engine.equity_curve()
    full_eq = full_eq[full_eq.index <= cutoff]
    cut_eq = cut_engine.equity_curve()
    if len(full_eq) == len(cut_eq):
        equity_diffs = int((~np.isclose(full_eq.to_numpy(), cut_eq.to_numpy(), rtol=1e-12)).sum())
    else:
        equity_diffs = abs(len(full_eq) - len(cut_eq))

    passed = not feature_diffs and not differences and equity_diffs == 0
    return IntegrityResult(cutoff, passed, len(cut_features), feature_diffs, len(full_events), excluded + cut_excluded,
                           differences, len(cut_eq), equity_diffs, len(cut_engine.trades))


def run_on_warehouse(tf: str, start: str, end: str, cutoffs: list[str], strategy_factory=None) -> list[dict]:
    from quantresearch.sim.reference import SmaCross
    from quantresearch.warehouse.store import load_bars

    factory = strategy_factory or (lambda: SmaCross())
    execution = load_bars("m1", start, end)
    signals = load_bars(tf, start, end)
    return [check(execution, signals, factory, cut).as_dict() for cut in cutoffs]


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the look-ahead integrity test on real data.")
    ap.add_argument("--tf", default="m15")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2024-04-15")
    ap.add_argument("cutoffs", nargs="+")
    args = ap.parse_args()
    results = run_on_warehouse(args.tf, args.start, args.end, args.cutoffs)
    print(json.dumps(results, indent=1, default=str))
    raise SystemExit(0 if all(r["passed"] for r in results) else 1)


if __name__ == "__main__":
    main()
