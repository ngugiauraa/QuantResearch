"""Outcome Simulator: the original Time Machine's method, on XAUUSD.

Unchanged from upstream, and called directly: bar draws, stop-before-target rule, seeding, R, percentile
and histogram logic, verdict thresholds and wording.

Adapted, as gold is not a 78-bar stock session:
  - Sessions are New York trading days rolling at 17:00, divided into 288 five-minute slots.
  - Bars come from the local warehouse, not Yahoo, so pools can be far longer than 60 days.
  - A session need not have every slot, since gold often has short gaps. Ratios are only formed between
    consecutive slots. An empty slot borrows the nearest non-empty slot's pool, and the count is reported.
  - Spread is charged in `avg_R_after_spread`, which upstream does not model.
Limit carried from upstream: each bar is drawn independently, which removes volatility clustering.
Treat the odds as a reality check, not a forecast.
"""
from __future__ import annotations

import pandas as pd

from quantresearch.core import timeframes as tfs
from quantresearch.skills import upstream

SLOTS = 288
SLOT_MINUTES = 5


def _module():
    # A private copy, so changing the session length cannot affect anyone using the original.
    mod = upstream.load("timemachine", private=True)
    mod.SESSION_BARS = SLOTS
    return mod


def build_sessions(bars: pd.DataFrame) -> dict:
    """{session_label: {slot: bar}} from M5 bars. A slot's number is minutes since 17:00 New York / 5."""
    if bars.empty:
        return {}
    roll = tfs.bucket(bars["ts"], "d1ny")
    slot = ((bars["ts"] - roll).dt.total_seconds() // (SLOT_MINUTES * 60)).astype(int)
    out: dict = {}
    for label, s, o, h, l, c in zip(roll, slot, bars["open"], bars["high"], bars["low"], bars["close"]):
        out.setdefault(label, {})[int(s)] = {"o": float(o), "h": float(h), "l": float(l), "c": float(c)}
    return out


def build_pool(sessions: dict) -> tuple[list, int]:
    """Bar ratios per slot. Returns the pool and how many slots borrowed from a neighbour."""
    pool: list[list] = [[] for _ in range(SLOTS)]
    for bars in sessions.values():
        for slot in range(1, SLOTS):
            if slot in bars and slot - 1 in bars:
                prev = bars[slot - 1]["c"]
                b = bars[slot]
                pool[slot].append((b["o"] / prev, b["h"] / prev, b["l"] / prev, b["c"] / prev))
    filled = [i for i in range(SLOTS) if pool[i]]
    if not filled:
        raise ValueError("No usable bars for the replay pool")
    borrowed = 0
    for i in range(1, SLOTS):
        if not pool[i]:
            pool[i] = pool[min(filled, key=lambda f: abs(f - i))]
            borrowed += 1
    pool[0] = pool[1]
    return pool, borrowed


def plan_slot(when: pd.Timestamp) -> int:
    series = pd.Series([when])
    roll = tfs.bucket(series, "d1ny").iloc[0]
    return int((when - roll).total_seconds() // (SLOT_MINUTES * 60))


def history(sessions: dict, side: int, entry_slot: int, last: int, stop_frac: float, target_frac: float) -> dict:
    """What really happened: the same stop and target distances from this slot in each past session."""
    outcomes = {"target": 0, "stop": 0, "close": 0}
    total_r = 0.0
    for bars in sessions.values():
        if entry_slot not in bars or last not in bars:
            continue
        entry = bars[entry_slot]["c"]
        stop = entry * (1 - side * stop_frac)
        target = entry * (1 + side * target_frac)
        result = None
        for slot in range(entry_slot + 1, last + 1):
            if slot not in bars:
                continue
            b = bars[slot]
            if (b["l"] <= stop) if side > 0 else (b["h"] >= stop):
                result = "stop"
                break
            if (b["h"] >= target) if side > 0 else (b["l"] <= target):
                result = "target"
                break
        outcomes[result or "close"] += 1
        risk = abs(entry - stop)
        total_r += {"stop": -1.0, "target": target_frac / stop_frac}.get(result, side * (bars[last]["c"] - entry) / risk)
    count = sum(outcomes.values())
    return {"sessions": len(sessions), "triggered": count, **outcomes, "avg_R": round(total_r / count, 3) if count else None}


def simulate_plan(bars: pd.DataFrame, entry: float, stop: float, target: float, entry_time, until_slot: int | None = None,
                  replays: int = 10_000, seed: int = 19, window: int = 2, spread: float = 0.0, min_sessions: int = 20) -> dict:
    """Odds for one plan. Only sessions that ended before the entry's session form the pool."""
    side = 1 if target > entry else -1
    if side > 0 and not stop < entry < target:
        raise ValueError("A long needs stop < entry < target")
    if side < 0 and not target < entry < stop:
        raise ValueError("A short needs target < entry < stop")
    when = pd.Timestamp(entry_time)
    when = when.tz_localize("UTC") if when.tzinfo is None else when.tz_convert("UTC")
    current_session = tfs.bucket(pd.Series([when]), "d1ny").iloc[0]
    sessions = {k: v for k, v in build_sessions(bars).items() if k < current_session}
    if len(sessions) < min_sessions:
        raise ValueError(f"Only {len(sessions)} past sessions; at least {min_sessions} are needed")
    pool, borrowed = build_pool(sessions)
    k = plan_slot(when)
    last = SLOTS - 1 if until_slot is None else until_slot
    if last <= k:
        raise ValueError("Nothing is left of the session to replay")
    mod = _module()
    mc = mod.simulate(pool, entry, stop, target, k, last, replays, seed, window, "buy" if side > 0 else "sell")
    # Upstream's verdict text reads a clock time. The trading day ends at 17:00 New York.
    minutes = (17 * 60 + (last + 1) * SLOT_MINUTES) % (24 * 60)
    mc["until"] = f"{minutes // 60:02d}:{minutes % 60:02d}"
    risk = abs(entry - stop)
    return {
        "plan": {"side": side, "entry": entry, "stop": stop, "target": target, "entry_time": str(when), "entry_slot": k},
        "mc": mc, "pool_sessions": len(sessions), "pool_from": str(min(sessions)), "pool_to": str(max(sessions)),
        "slots_borrowed": borrowed, "avg_R_after_spread": round(mc["avg_R"] - spread / risk, 3),
        "history": history(sessions, side, k, last, risk / entry, abs(target - entry) / entry),
        "verdict": mod.verdict(mc), "verdict_notes": mod.verdict_notes(mc),
        "note": "A simulation built from past sessions, not a prediction. Bars are drawn independently, "
                "which removes volatility clustering, and news can do what those sessions never did.",
    }
