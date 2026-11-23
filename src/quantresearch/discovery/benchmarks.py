"""Daily returns of every registered strategy engine over the research period, at their default parameters.

Used only to measure how correlated a discovered candidate is with what the platform already had. Seasonal
selects its cells on 2003-2008 data. Nothing here reads the holdout.
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from quantresearch.discovery.config import HOLDOUT_START


def compute(task: dict) -> dict:
    from quantresearch.research.experiment import load_research_frame
    from quantresearch.strategies import registry
    t0 = time.perf_counter()
    out = {}
    errors = {}
    for name, defn in sorted(registry.REGISTRY.items()):
        try:
            frame = load_research_frame(defn, None, HOLDOUT_START)
            frame = frame[frame["close_ts"] <= HOLDOUT_START].reset_index(drop=True)
            params = dict(defn.defaults)
            if defn.fit is not None:
                params.update(defn.fit(frame[frame["ts"] < pd.Timestamp("2009-01-01", tz="UTC")], params))
            engine = registry.run(defn, frame, params)
            curve = engine.equity_curve()
            daily = curve.groupby(curve.index.tz_convert("America/New_York").date).last().pct_change().dropna()
            out[name] = daily
            del frame, engine
        except Exception as exc:  # one engine failing must not stop the others; it is reported
            errors[name] = f"{type(exc).__name__}: {exc}"
    table = pd.DataFrame(out)
    table.index = pd.to_datetime(table.index)
    path = Path(task["out"])
    path.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(path)
    return {"task_id": task["task_id"], "engines": sorted(out), "errors": errors, "runtime_s": time.perf_counter() - t0}


def load(path: Path) -> pd.DataFrame | None:
    return pd.read_parquet(path) if Path(path).exists() else None


def registered_engines() -> list[str]:
    from quantresearch.strategies import registry
    return sorted(registry.REGISTRY)


def stale_cache_warning(table: pd.DataFrame | None, path: Path | str) -> str | None:
    """A message if the cached benchmark table lacks any engine registered now, otherwise None.

    The table is written once per run and reused, so it goes stale when an engine is added (or when one failed to
    compute, which the benchmarks log line reports). The file to delete is the benchmark table itself:
    experiments.sqlite is the research ledger and must be kept.
    """
    if table is None:
        return None
    engines = registered_engines()
    missing = sorted(set(engines) - set(table.columns))
    if not missing:
        return None
    return (f"Stale benchmark cache detected: {path} holds {len(table.columns)} engines but {len(engines)} are registered "
            f"(missing: {', '.join(missing)}). Delete that file (not experiments.sqlite, which is the research ledger) "
            f"and rerun to get accurate baselines for all {len(engines)} engines.")


def max_abs_correlation(days_ns, rets, table: pd.DataFrame | None) -> tuple[float | None, dict]:
    if table is None or table.empty or len(rets) < 30:
        return None, {}
    import numpy as np
    dates = (pd.to_datetime(np.asarray(days_ns), utc=True).tz_convert("America/New_York") + pd.Timedelta(hours=7)).normalize().tz_localize(None)
    s = pd.Series(np.asarray(rets), index=dates).groupby(level=0).sum()
    joined = table.join(s.rename("cand"), how="inner").fillna(0.0)
    if len(joined) < 30 or joined["cand"].std() == 0:
        return None, {}
    corr = {c: float(joined["cand"].corr(joined[c])) for c in table.columns if joined[c].std() > 0}
    return (max((abs(v) for v in corr.values()), default=None), corr)
