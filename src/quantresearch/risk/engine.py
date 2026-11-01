"""The risk engine approves, reduces or rejects every proposed order.

It takes plain numbers and depends on nothing but the standard library, so a failed ML service or
strategy cannot affect it. Getting out is always possible: an order that only reduces an open
position skips the entry limits, because blocking an exit increases risk. Limits fail closed: missing
data needed for a check rejects the order instead of passing it.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

OZ_PER_LOT = 100.0


@dataclass
class RiskLimits:
    risk_per_trade_pct: float = 0.5
    max_daily_loss_pct: float = 2.0
    max_drawdown_pct: float = 10.0
    max_total_lots: float = 5.0
    max_order_lots: float = 2.0
    max_concurrent_positions: int = 3
    max_trades_per_day: int = 10
    min_reward_risk: float = 1.0
    require_stop: bool = True
    max_stop_distance_pct: float = 5.0
    max_spread_usd: float = 1.5
    max_slippage_usd: float = 1.0
    max_atr_pct: float = 3.0
    news_block_before_min: int = 15
    news_block_after_min: int = 15
    lockout_hours_after_daily_loss: int = 24
    lot_step: float = 0.01
    min_lots: float = 0.01

    def validate(self) -> None:
        for name, value in asdict(self).items():
            if isinstance(value, (int, float)) and value < 0:
                raise ValueError(f"Limit {name} cannot be negative")
        if self.lot_step <= 0:
            raise ValueError("lot_step must be positive")


@dataclass
class Proposal:
    side: int
    lots: float
    entry: float
    stop: float | None = None
    target: float | None = None
    source: str = ""
    reduces_position: bool = False


@dataclass
class Market:
    bid: float | None = None
    ask: float | None = None
    atr: float | None = None
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Account:
    equity: float
    balance: float | None = None
    open_lots: float = 0.0
    open_positions: int = 0
    net_lots: float = 0.0


@dataclass
class RiskDecision:
    approved: bool
    lots: float
    reasons: list[str]
    checks: dict[str, str]
    modified: bool = False


class RiskState:
    """Durable counters and lockouts. Survives a restart, so a restart cannot clear a lockout."""

    def __init__(self, path: str | Path | None = None):
        self.path = str(path) if path else ":memory:"
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path, check_same_thread=False)
        self.con.execute("CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT)")
        self.con.execute(
            """CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, approved INTEGER,
               lots REAL, source TEXT, proposal TEXT, reasons TEXT, checks TEXT)"""
        )
        self.con.commit()

    def get(self, key: str, default=None):
        row = self.con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value) -> None:
        self.con.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (key, json.dumps(value)))
        self.con.commit()

    def log(self, at: datetime, proposal: Proposal, decision: RiskDecision) -> None:
        self.con.execute(
            "INSERT INTO decisions(at,approved,lots,source,proposal,reasons,checks) VALUES(?,?,?,?,?,?,?)",
            (at.isoformat(), int(decision.approved), decision.lots, proposal.source, json.dumps(asdict(proposal)),
             json.dumps(decision.reasons), json.dumps(decision.checks)),
        )
        self.con.commit()

    def recent(self, limit: int = 100) -> list[dict]:
        rows = self.con.execute(
            "SELECT at,approved,lots,source,proposal,reasons,checks FROM decisions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{"at": r[0], "approved": bool(r[1]), "lots": r[2], "source": r[3], "proposal": json.loads(r[4]),
                 "reasons": json.loads(r[5]), "checks": json.loads(r[6])} for r in rows]


class RiskEngine:
    def __init__(self, limits: RiskLimits | None = None, state: RiskState | None = None,
                 news_schedule: Callable[[], list[datetime]] | None = None):
        self.limits = limits or RiskLimits()
        self.limits.validate()
        self.state = state or RiskState()
        self.news_schedule = news_schedule

    # ------------------------------------------------------------ controls
    @property
    def emergency_stop(self) -> bool:
        return bool(self.state.get("emergency_stop", False))

    def set_emergency_stop(self, active: bool, reason: str = "") -> None:
        self.state.set("emergency_stop", bool(active))
        self.state.set("emergency_reason", reason)

    def lockout_until(self) -> datetime | None:
        value = self.state.get("lockout_until")
        return datetime.fromisoformat(value) if value else None

    def set_lockout(self, until: datetime, reason: str) -> None:
        self.state.set("lockout_until", until.isoformat())
        self.state.set("lockout_reason", reason)

    def clear_lockout(self) -> None:
        self.state.set("lockout_until", None)
        self.state.set("lockout_reason", None)

    # ------------------------------------------------------------ tracking
    def observe(self, equity: float, now: datetime) -> dict:
        """Feed account equity. Tracks the day's start and the peak, and applies breaker lockouts."""
        day = now.date().isoformat()
        if self.state.get("day") != day:
            self.state.set("day", day)
            self.state.set("day_start_equity", equity)
            self.state.set("trades_today", 0)
        peak = max(float(self.state.get("peak_equity", equity)), equity)
        self.state.set("peak_equity", peak)
        start = float(self.state.get("day_start_equity", equity))
        daily = (start - equity) / start * 100 if start > 0 else 0.0
        drawdown = (peak - equity) / peak * 100 if peak > 0 else 0.0
        if daily >= self.limits.max_daily_loss_pct:
            self.set_lockout(now + timedelta(hours=self.limits.lockout_hours_after_daily_loss),
                             f"Daily loss {daily:.2f}% reached the {self.limits.max_daily_loss_pct}% limit")
        if drawdown >= self.limits.max_drawdown_pct:
            # Drawdown needs a person to review it, so this lockout has no end time.
            self.set_lockout(datetime(9999, 1, 1, tzinfo=timezone.utc),
                             f"Drawdown {drawdown:.2f}% reached the {self.limits.max_drawdown_pct}% limit")
        return {"daily_loss_pct": daily, "drawdown_pct": drawdown, "peak_equity": peak, "day_start_equity": start,
                "trades_today": int(self.state.get("trades_today", 0))}

    def record_entry(self) -> None:
        self.state.set("trades_today", int(self.state.get("trades_today", 0)) + 1)

    # ------------------------------------------------------------ the decision
    def evaluate(self, proposal: Proposal, account: Account, market: Market) -> RiskDecision:
        decision = self._decide(proposal, account, market)
        self.state.log(market.now, proposal, decision)
        return decision

    def _round_down(self, lots: float) -> float:
        step = self.limits.lot_step
        return round(int(lots / step + 1e-9) * step, 8)

    def _decide(self, p: Proposal, a: Account, m: Market) -> RiskDecision:
        limits = self.limits
        checks: dict[str, str] = {}
        reasons: list[str] = []

        def fail(check: str, why: str) -> None:
            checks[check] = "fail"
            reasons.append(why)

        def ok(check: str) -> None:
            checks[check] = "pass"

        if p.side not in (1, -1):
            fail("format", "Side must be +1 or -1")
        if p.lots is None or p.lots <= 0:
            fail("format", "Lots must be positive")
        if reasons:
            return RiskDecision(False, 0.0, reasons, checks)
        ok("format")

        # An exit that only reduces a position is always allowed, even during an emergency stop.
        if p.reduces_position:
            allowed = self._round_down(min(p.lots, a.open_lots)) if a.open_lots > 0 else 0.0
            if allowed <= 0:
                fail("exit", "No open position for this exit to reduce")
                return RiskDecision(False, 0.0, reasons, checks)
            checks["exit"] = "pass: risk-reducing"
            return RiskDecision(True, allowed, [], checks, modified=allowed != p.lots)

        if self.emergency_stop:
            fail("emergency_stop", "Emergency stop is active")
        else:
            ok("emergency_stop")
        until = self.lockout_until()
        if until is not None and m.now < until:
            fail("lockout", f"Trading is locked out: {self.state.get('lockout_reason')}")
        else:
            ok("lockout")

        # Data needed for checks: missing data fails closed.
        if not isinstance(a.equity, (int, float)) or a.equity <= 0:
            fail("equity", "Account equity is unknown or not positive")
            return RiskDecision(False, 0.0, reasons, checks)
        ok("equity")

        if m.bid is None or m.ask is None:
            fail("spread", "No live bid and ask")
        else:
            spread = m.ask - m.bid
            if spread < 0:
                fail("spread", f"Crossed quote: bid {m.bid} above ask {m.ask}")
            elif spread > limits.max_spread_usd:
                fail("spread", f"Spread {spread:.2f} is over the {limits.max_spread_usd} limit")
            else:
                ok("spread")
            reference = m.ask if p.side > 0 else m.bid
            if abs(p.entry - reference) > limits.max_slippage_usd:
                fail("slippage", f"Market {reference:.2f} moved {abs(p.entry - reference):.2f} from the planned {p.entry:.2f}")
            else:
                ok("slippage")

        if m.atr is None:
            fail("volatility", "No volatility reading")
        elif p.entry > 0 and m.atr / p.entry * 100 > limits.max_atr_pct:
            fail("volatility", f"ATR is {m.atr / p.entry * 100:.2f}% of price, over the {limits.max_atr_pct}% limit")
        else:
            ok("volatility")

        if self.news_schedule is not None:
            try:
                events = list(self.news_schedule())
            except Exception as exc:
                events = None
                fail("news", f"Event calendar unavailable: {exc}")
            if events is not None:
                before = timedelta(minutes=limits.news_block_before_min)
                after = timedelta(minutes=limits.news_block_after_min)
                near = [e for e in events if e - before <= m.now <= e + after]
                if near:
                    fail("news", f"Inside the restriction window of an event at {near[0].isoformat()}")
                else:
                    ok("news")

        # Stop, then reward:risk.
        risk_per_oz = None
        if p.stop is None:
            if limits.require_stop:
                fail("stop", "A stop-loss is required")
        else:
            risk_per_oz = p.side * (p.entry - p.stop)
            if risk_per_oz <= 0:
                fail("stop", "The stop is on the wrong side of the entry")
                risk_per_oz = None
            elif risk_per_oz / p.entry * 100 > limits.max_stop_distance_pct:
                fail("stop", f"Stop distance {risk_per_oz / p.entry * 100:.2f}% is over the {limits.max_stop_distance_pct}% limit")
            else:
                ok("stop")
        if p.target is not None and risk_per_oz:
            reward = p.side * (p.target - p.entry)
            if reward <= 0:
                fail("reward_risk", "The target is on the wrong side of the entry")
            elif reward / risk_per_oz < limits.min_reward_risk:
                fail("reward_risk", f"Reward:risk {reward / risk_per_oz:.2f} is under the {limits.min_reward_risk} minimum")
            else:
                ok("reward_risk")
        else:
            checks["reward_risk"] = "skipped: no target"

        if a.open_positions >= limits.max_concurrent_positions:
            fail("positions", f"{a.open_positions} positions already open, limit {limits.max_concurrent_positions}")
        else:
            ok("positions")
        trades = int(self.state.get("trades_today", 0))
        if trades >= limits.max_trades_per_day:
            fail("trades_per_day", f"{trades} entries today, limit {limits.max_trades_per_day}")
        else:
            ok("trades_per_day")

        # Size: reduce to fit the limits. Never increase.
        lots = p.lots
        notes = []
        if lots > limits.max_order_lots:
            lots = limits.max_order_lots
            notes.append("max_order")
        room = limits.max_total_lots - a.open_lots
        if room <= 0:
            fail("exposure", f"Exposure {a.open_lots} lots is at the {limits.max_total_lots} limit")
        elif lots > room:
            lots = room
            notes.append("exposure")
        if risk_per_oz:
            allowed = a.equity * limits.risk_per_trade_pct / 100.0 / (risk_per_oz * OZ_PER_LOT)
            if lots > allowed:
                lots = allowed
                notes.append("risk_per_trade")
        rounded = self._round_down(lots)
        if rounded < limits.min_lots and "exposure" not in checks:
            fail("size", f"Size {lots:.4f} lots is under the {limits.min_lots} minimum after limits")
        elif "size" not in checks:
            checks["size"] = "reduced: " + ",".join(notes) if notes else "pass"
        if reasons:
            return RiskDecision(False, 0.0, reasons, checks)
        return RiskDecision(True, rounded, [], checks, modified=abs(rounded - p.lots) > 1e-9)


def simulation_gate(risk: RiskEngine, atr_of=lambda engine: None, use_atr: bool = True):
    """Adapter so backtests and replays pass through the same risk engine as live orders."""
    from quantresearch.sim.engine import Decision

    def gate(intent, engine) -> Decision:
        now = engine.now.to_pydatetime()
        i = max(0, min(engine.cursor, engine._n - 1))
        bid = float(engine._bo[i])
        ask_value = engine._ao[i]
        ask = None if ask_value != ask_value else float(ask_value)
        risk.observe(engine.account.equity, now)
        entry = intent.price if intent.price is not None else (ask if intent.side > 0 else bid)
        open_lots = sum(pos.lots for pos in engine.positions)
        atr = atr_of(engine) if use_atr else None
        decision = risk.evaluate(
            Proposal(intent.side, intent.lots, entry if entry is not None else bid, intent.stop, intent.target, intent.tag),
            Account(engine.account.equity, engine.account.balance, open_lots, len(engine.positions)),
            Market(bid, ask, atr, now),
        )
        if decision.approved:
            risk.record_entry()
        return Decision(decision.approved, decision.lots, "; ".join(decision.reasons) or "approved")

    return gate
