"""Candidate stages and ranking.

Stage is decided by gates, never by score. A candidate is ranked first by the highest stage it has passed, then
by a score that only orders candidates within the same stage. So a large historical return cannot lift a
candidate past a failed statistical, robustness or adversarial test.
"""
from __future__ import annotations

import numpy as np

STAGES = ("experimental", "screened", "selected", "walk_forward", "statistically_validated", "robust", "qualified", "paper_trading")
STAGE_LABELS = {
    "experimental": "Experimental", "screened": "Screened", "selected": "Frozen for out-of-sample test",
    "walk_forward": "Passed walk-forward", "statistically_validated": "Statistically validated",
    "robust": "Passed robustness and adversarial tests", "qualified": "Qualified for paper trading only",
    "paper_trading": "Paper trading",
}
DASHBOARD_GROUPS = {
    "Experimental": ("experimental",), "Screened": ("screened", "selected", "walk_forward"),
    "Statistically validated": ("statistically_validated", "robust"), "Qualified": ("qualified",), "Paper-trading": ("paper_trading",),
}


def tier(stage: str) -> int:
    return STAGES.index(stage) if stage in STAGES else 0


def _clip(x, lo, hi):
    return float(min(max(x, lo), hi))


def score(c: dict) -> dict:
    """Within-stage score in roughly [-1, 1], with every component reported so the ordering can be audited."""
    oos = c.get("oos") or {}
    st3 = c.get("stage3") or {}
    rob = (c.get("robust") or {}).get("tests", {})
    parts = {}
    parts["expectancy"] = 0.30 * _clip((oos.get("exp_r") or 0) / 0.5, -1, 1)
    ci = st3.get("sharpe_ci95")
    parts["confidence"] = 0.20 * _clip((ci[0] if ci else (oos.get("sharpe") or 0) - 1.0) / 2, -1, 1)
    parts["deflated_sharpe"] = 0.15 * _clip(st3.get("dsr", 0) or 0, 0, 1)
    parts["drawdown"] = 0.10 * (1 - _clip((oos.get("max_dd") or 0) / 0.25, 0, 1))
    parts["sample"] = 0.05 * _clip(np.log10(max(oos.get("trades", 1), 1)) / 3, 0, 1)
    parts["fold_consistency"] = 0.05 * _clip(oos.get("positive_year_share") or 0, 0, 1)
    pert = (rob.get("parameter_perturbation") or {}).get("value") or {}
    parts["parameter_stability"] = 0.05 * _clip(pert.get("positive_share", 0), 0, 1)
    stress = ((rob.get("cost_stress") or {}).get("value") or {}).get("exp_r")
    parts["cost_sensitivity"] = 0.05 * (_clip(stress / oos["exp_r"], -1, 1) if stress is not None and oos.get("exp_r") else 0)
    conc = max([((rob.get(k) or {}).get("value") or {}).get("concentration", 0) or 0 for k in ("regime_volatility", "regime_trend")] or [0])
    parts["regime_concentration"] = -0.05 * _clip(conc, 0, 1)
    parts["complexity"] = -0.02 * _clip((c.get("complexity") or 0) / 10, 0, 3)
    parts["correlation_with_existing"] = -0.05 * _clip(abs(c.get("max_corr_existing") or 0), 0, 1)
    deps = ((rob.get("feature_dependency") or {}).get("value") or {}).get("features", {})
    lost = [d.get("share_of_edge_lost") or 0 for d in deps.values()]
    parts["single_feature_dependency"] = -0.03 * _clip(max(lost) if lost else 0, 0, 1)
    sides = oos.get("sides") or {}
    if c.get("direction") == "both":
        both = all(((sides.get(s) or {}).get("exp_r") or 0) > 0 for s in ("long", "short"))
        parts["side_balance"] = 0.05 if both else -0.05
    timing = (c.get("robust") or {}).get("timing_ratio")
    if timing is not None:
        parts["timing_specificity"] = -0.05 * _clip(timing, 0, 1)
    total = float(sum(parts.values()))
    return {"score": total, "components": parts, "tier": tier(c.get("stage", "experimental"))}


def order(cands: list[dict]) -> list[dict]:
    for c in cands:
        c["ranking"] = score(c)
    return sorted(cands, key=lambda c: (-c["ranking"]["tier"], -c["ranking"]["score"], c["cid"]))
