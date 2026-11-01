"""A controllable replay session on top of the event engine.

The engine is a single chronological stepper, so replay and backtest share one code path. Going
backwards is done by replaying from the start, which is deterministic, so there is no separate
"rewind" logic that could drift. A saved session records its configuration, the data's
fingerprint and the digest of its events, so anyone can re-run it and verify the same result.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from quantresearch.sim.costs import CostModel
from quantresearch.sim.engine import Engine, EngineConfig, Strategy


def data_fingerprint(frame: pd.DataFrame) -> str:
    """Stable hash of a frame's values, so a saved session can detect changed input data."""
    hashed = pd.util.hash_pandas_object(frame.reset_index(drop=True), index=False).to_numpy()
    return hashlib.sha256(hashed.tobytes()).hexdigest()


@dataclass
class ReplayConfig:
    name: str = "replay"
    timeframe: str = "m5"
    execution: str = "m1"
    start: str | None = None
    end: str | None = None
    strategy: str = "none"
    params: dict = field(default_factory=dict)
    initial_equity: float = 100_000.0
    costs: dict = field(default_factory=dict)
    seed: int = 19


def _nan_to_none(value):
    if isinstance(value, float) and np.isnan(value):
        return None
    return value


class ReplaySession:
    def __init__(self, execution: pd.DataFrame, signals: pd.DataFrame, strategy_factory: Callable[[], Strategy],
                 config: ReplayConfig | None = None, risk_gate=None):
        self.config = config or ReplayConfig()
        self._execution = execution
        self._signals = signals
        self._factory = strategy_factory
        self._risk_gate = risk_gate
        self.playing = False
        self.speed = 1
        self.reset()

    def _engine_config(self) -> EngineConfig:
        return EngineConfig(initial_equity=self.config.initial_equity, costs=CostModel(**self.config.costs))

    def reset(self) -> None:
        self.playing = False
        self.engine = Engine(self._execution, self._signals, self._factory(), self._engine_config(),
                             risk_gate=self._risk_gate)

    def play(self) -> None:
        self.playing = True

    def pause(self) -> None:
        self.playing = False

    def set_speed(self, events_per_tick: int) -> None:
        if events_per_tick < 1:
            raise ValueError("Speed must be at least 1 event per tick")
        self.speed = int(events_per_tick)

    def tick(self) -> int:
        """Advance by the current speed while playing. A driver calls this on its own clock."""
        if not self.playing:
            return 0
        moved = self.step(self.speed)
        if self.engine.finished:
            self.playing = False
        return moved

    def step(self, events: int = 1) -> int:
        moved = 0
        for _ in range(events):
            if not self.engine.step():
                break
            moved += 1
        return moved

    def seek(self, when) -> None:
        """Navigate to a time, forward or backward, by replaying deterministically."""
        stamp = pd.Timestamp(when)
        stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
        was_playing = self.playing
        if stamp < self.engine.now:
            self.reset()
        self.engine.run(until=stamp)
        self.playing = was_playing

    def to_end(self) -> None:
        self.engine.run()
        self.playing = False

    # ------------------------------------------------------------ views
    def visible_signals(self, limit: int = 300) -> pd.DataFrame:
        """Closed bars only. The chart can never show a bar that has not finished."""
        closes = self._signals["close_ts"]
        visible = self._signals[closes <= self.engine.now]
        return visible.tail(limit)

    def snapshot(self, candles: int = 300, events: int = 80) -> dict:
        e = self.engine
        i = min(e.cursor, e._n) - 1
        bid = float(e._bc[i]) if i >= 0 else None
        ask = float(e._ac[i]) if i >= 0 else None
        bars = self.visible_signals(candles)
        return {
            "name": self.config.name,
            "time": e.now.isoformat(),
            "cursor": e.cursor,
            "total": e._n,
            "playing": self.playing,
            "speed": self.speed,
            "finished": e.finished,
            "bid": _nan_to_none(bid),
            "ask": _nan_to_none(ask),
            "equity": e.account.equity,
            "balance": e.account.balance,
            "drawdown": e.account.drawdown,
            "candles": [
                {"t": int(t.timestamp()), "o": float(o), "h": float(h), "l": float(l), "c": float(c)}
                for t, o, h, l, c in zip(bars["ts"], bars["open"], bars["high"], bars["low"], bars["close"])
            ],
            "positions": [
                {"id": p.id, "side": p.side, "lots": p.lots, "entry": p.entry_price,
                 "entry_time": int(p.entry_time.timestamp()), "stop": p.stop, "target": p.target, "tag": p.tag}
                for p in e.positions
            ],
            "pending": [
                {"id": o.id, "side": o.intent.side, "lots": o.remaining, "kind": o.intent.kind, "price": o.intent.price}
                for o in e.pending
            ],
            "trades": [
                {"id": t.position_id, "side": t.side, "entry_time": int(t.entry_time.timestamp()), "entry": t.entry_price,
                 "exit_time": int(t.exit_time.timestamp()), "exit": t.exit_price, "reason": t.reason, "net": t.net,
                 "stop": t.stop, "target": t.target}
                for t in e.trades[-200:]
            ],
            "events": e.events[-events:],
        }

    def record(self) -> dict:
        """What is needed to verify this session later."""
        return {
            "config": asdict(self.config),
            "execution_fingerprint": data_fingerprint(self._execution[["ts"] + [c for c in self._execution.columns if c.startswith(("bid", "ask"))]]),
            "signals_rows": int(len(self._signals)),
            "cursor": self.engine.cursor,
            "time": self.engine.now.isoformat(),
            "events": len(self.engine.events),
            "trades": len(self.engine.trades),
            "digest": self.engine.digest(),
        }

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.record(), indent=1, default=str), encoding="utf-8")
        return target

    def verify(self, record: dict) -> bool:
        """Re-run from the start to the recorded position and compare fingerprints."""
        self.reset()
        self.step(record["cursor"])
        return self.record()["digest"] == record["digest"]
