"""Combine strategy signals, skills context and an optional ML view into one proposal, with its reasons.

The output is a proposal for the risk engine to judge, never an order. If the ML layer fails or abstains, the
decision continues without it or stands down according to policy, and the risk engine is unaffected either way.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from quantresearch.sim.costs import OZ_PER_LOT
from quantresearch.sim.engine import Context, OrderIntent, Strategy
from quantresearch.strategies.base import StrategyDef


@dataclass
class Vote:
    source: str
    side: int
    stop_dist: float
    target_dist: float | None
    weight: float = 1.0


@dataclass
class DecisionPolicy:
    min_votes: int = 1
    conflict: str = "stand_down"  # stand_down or majority
    ml_mode: str = "off"  # off, veto (ML can block but never starts a trade) or required
    ml_min_confidence: float = 0.1
    risk_pct: float = 0.5
    max_lots: float = 10.0


@dataclass
class Decision:
    time: str
    action: str  # none, long, short
    votes: list[dict]
    ml: dict | None
    context: dict
    reasons: list[str]
    lots: float = 0.0
    stop_dist: float | None = None
    target_dist: float | None = None


def decide(votes: list[Vote], ml: dict | None, policy: DecisionPolicy, equity: float, when: str, context: dict | None = None) -> Decision:
    reasons: list[str] = []
    record = [asdict(v) for v in votes]
    def none(reason: str) -> Decision:
        return Decision(when, "none", record, ml, context or {}, reasons + [reason])

    if context and context.get("blocked"):
        return none(f"Blocked by context: {context['blocked']}")
    if not votes:
        return none("No strategy signal")
    long_weight = sum(v.weight for v in votes if v.side > 0)
    short_weight = sum(v.weight for v in votes if v.side < 0)
    if long_weight and short_weight and policy.conflict == "stand_down":
        return none(f"Strategies disagree: {long_weight} long against {short_weight} short")
    if long_weight == short_weight:
        return none("Strategies are evenly split")
    side = 1 if long_weight > short_weight else -1
    agreeing = [v for v in votes if v.side == side]
    if len(agreeing) < policy.min_votes:
        return none(f"Only {len(agreeing)} of the required {policy.min_votes} votes")
    reasons.append(f"{len(agreeing)} of {len(votes)} strategies favour {'long' if side > 0 else 'short'}")
    if policy.ml_mode != "off":
        if ml is None:
            if policy.ml_mode == "required":
                return none("ML view is required and unavailable")
            reasons.append("ML unavailable: continuing on strategies alone")
        elif ml.get("abstain"):
            if policy.ml_mode == "required":
                return none(f"ML abstained: {ml.get('abstain_reason')}")
            reasons.append(f"ML abstained: {ml.get('abstain_reason')}")
        else:
            agrees = (ml["prob_up"] > 0.5) == (side > 0)
            if not agrees and ml.get("confidence", 0) >= policy.ml_min_confidence:
                return none(f"ML disagrees with {ml['confidence']:.2f} confidence, probability up {ml['prob_up']:.3f}")
            reasons.append(f"ML probability up {ml['prob_up']:.3f}")
    stop = max(v.stop_dist for v in agreeing)  # The widest stop, so every voter's idea has room.
    targets = [v.target_dist for v in agreeing if v.target_dist is not None]
    target = min(targets) if targets else None
    lots = min(policy.max_lots, equity * policy.risk_pct / 100 / (stop * OZ_PER_LOT))
    return Decision(when, "long" if side > 0 else "short", record, ml, context or {}, reasons, lots, stop, target)


class DecisionStrategy(Strategy):
    """Runs the decision engine inside the event simulator, for backtests and Time Machine experiments."""
    name = "decision_engine"

    def __init__(self, frame: pd.DataFrame, voters: dict[str, pd.DataFrame], policy: DecisionPolicy | None = None,
                 ml: pd.DataFrame | None = None, context: pd.DataFrame | None = None, tag: str = "decision", max_hold: int | None = 48):
        self.policy = policy or DecisionPolicy()
        self.tag = tag
        self.max_hold = max_hold
        self.close = frame["close"].to_numpy(dtype="float64")
        self.voters = {n: {c: s[c].to_numpy() for c in ("long_entry", "short_entry", "stop_dist", "target_dist")} for n, s in voters.items()}
        self.ml = ml
        self.context = context
        self.decisions: list[Decision] = []
        self.entries: dict[int, int] = {}

    def _votes(self, i: int) -> list[Vote]:
        out = []
        for name, s in self.voters.items():
            side = 1 if s["long_entry"][i] else (-1 if s["short_entry"][i] else 0)
            distance = float(s["stop_dist"][i])
            if side and math.isfinite(distance) and distance > 0:
                target = float(s["target_dist"][i])
                out.append(Vote(name, side, distance, target if math.isfinite(target) else None))
        return out

    def on_bar(self, ctx: Context) -> None:
        i = ctx.index
        for pos in ctx.positions:
            started = self.entries.setdefault(pos.id, i)
            if self.max_hold is not None and i - started >= self.max_hold:
                ctx.close(pos, "time")
        if ctx.positions or ctx.orders:
            return
        votes = self._votes(i)
        if not votes:
            return
        ml_view = None
        if self.ml is not None:
            row = self.ml.iloc[i]
            ml_view = {"prob_up": float(row["prob_up"]), "confidence": float(row["confidence"]), "abstain": bool(row["abstain"]),
                       "abstain_reason": str(row["abstain_reason"])}
        context = None
        if self.context is not None:
            value = self.context.iloc[i].get("blocked")
            context = {"blocked": value} if isinstance(value, str) and value else None
        decision = decide(votes, ml_view, self.policy, ctx.account.equity, ctx.now.isoformat(), context)
        self.decisions.append(decision)
        ctx.log("decision", action=decision.action, reasons="; ".join(decision.reasons), votes=len(votes))
        if decision.action == "none":
            return
        side = 1 if decision.action == "long" else -1
        price = self.close[i]
        ctx.submit(OrderIntent(side, decision.lots, stop=price - side * decision.stop_dist,
                               target=None if decision.target_dist is None else price + side * decision.target_dist, tag=self.tag))

    def explain(self, when) -> Decision | None:
        """Reconstruct why a decision was made at a given time."""
        stamp = pd.Timestamp(when).isoformat()
        return next((d for d in self.decisions if d.time == stamp), None)
