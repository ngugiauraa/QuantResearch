"""XAUUSD versions of the day-trading skills, keeping the originals' rules wherever they apply.

Decision rules are kept: closes confirm entries, stops round away from the entry, size is the smallest
of the limits, an impossible plan is refused rather than shrunk, and alerts ring once. Added: the short
side, lots and contract specs, near-24-hour session handling, and the gold calendar.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from quantresearch.features.technical import atr as atr_series
from quantresearch.skills import upstream
from quantresearch.warehouse import calendar as cal

# Session opens for the opening-range idea. Gold has no single open, so this is an explicit choice.
SESSION_OPENS = {"new_york_equity": ("America/New_York", 9, 30), "new_york_comex": ("America/New_York", 8, 20),
                 "london": ("Europe/London", 8, 0), "asia": ("Asia/Tokyo", 9, 0)}
MIN_RISK_PCT = Decimal("0.05")  # Upstream's "too tight to survive the spread" floor.


class PlanRefused(ValueError):
    """The plan does not make sense. Upstream refuses rather than repairs."""


# ---------------------------------------------------------------- Scanner
def scan_setups(closes: list[float]) -> dict:
    """Upstream's daily setups, applied directly to one instrument's completed closes."""
    if len(closes) < 30:
        return {"status": "insufficient_data", "setups": [], "numbers": {}}
    tags, numbers = upstream.load("scanner").find_setups([float(c) for c in closes])
    return {"status": "ok", "setups": tags, "numbers": numbers}


def scan_timeframes(frames: dict[str, pd.DataFrame]) -> list[dict]:
    """Upstream scans thousands of stocks on one timeframe. Gold is one chart, so timeframes replace the universe."""
    hits = []
    for tf, bars in frames.items():
        closes = bars["close"].dropna().tolist()[-260:]
        result = scan_setups(closes)
        hits.append({"timeframe": tf, "as_of": str(bars["close_ts"].iloc[-1]) if len(bars) else None, **result})
    return hits


# ---------------------------------------------------------------- Entry Timer
@dataclass
class OpeningRange:
    high: float
    low: float
    start: pd.Timestamp
    end: pd.Timestamp
    bars: int


def session_open_utc(day, session: str) -> pd.Timestamp:
    zone, hour, minute = SESSION_OPENS[session]
    return pd.Timestamp(f"{day} {hour:02d}:{minute:02d}").tz_localize(zone).tz_convert("UTC")


def opening_range(bars: pd.DataFrame, day, session: str = "new_york_equity", minutes: int = 15) -> OpeningRange | None:
    start = session_open_utc(day, session)
    end = start + pd.Timedelta(minutes=minutes)
    # Only bars wholly inside the range, and fully closed by its end.
    part = bars[(bars["ts"] >= start) & (bars["close_ts"] <= end)]
    if part.empty:
        return None
    return OpeningRange(float(part["high"].max()), float(part["low"].min()), start, end, len(part))


def entry_status(bars: pd.DataFrame, day, now, session: str = "new_york_equity", minutes: int = 15, level: float | None = None,
                 side: int = 1, no_entries_after_minutes: int = 360, min_volume_x: float | None = None) -> dict:
    """Upstream's rule: the first COMPLETED bar after the range to CLOSE beyond the level.

    A wick is not a trigger, and a close exactly at the level is not beyond it. Only bars closed by `now`
    count. The short side mirrors the rule and is a project addition.
    """
    now = pd.Timestamp(now)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    closed = bars[bars["close_ts"] <= now]
    start = session_open_utc(day, session)
    forming_until = start + pd.Timedelta(minutes=minutes)
    if now < start:
        return {"status": "not_open_yet", "opens": str(start)}
    if now < forming_until and level is None:
        return {"status": "range_forming", "range_ends": str(forming_until)}
    rng = opening_range(closed, day, session, minutes)
    if rng is None and level is None:
        return {"status": "no_data"}
    trigger = level if level is not None else (rng.high if side > 0 else rng.low)
    after = closed[(closed["ts"] >= forming_until)]
    deadline = start + pd.Timedelta(minutes=no_entries_after_minutes)
    in_range_volume = None
    if rng is not None and min_volume_x:
        inside = closed[(closed["ts"] >= rng.start) & (closed["close_ts"] <= rng.end)]
        in_range_volume = inside["volume"].mean()
    for _, bar in after.iterrows():
        beyond = bar["close"] > trigger if side > 0 else bar["close"] < trigger
        volume_ok = in_range_volume is None or bar["volume"] >= min_volume_x * in_range_volume
        if beyond and volume_ok:
            if bar["close_ts"] > deadline:
                return {"status": "expired", "level": trigger, "cutoff": str(deadline)}
            return {"status": "triggered", "time": str(bar["close_ts"]), "price": float(bar["close"]), "bar_open": str(bar["ts"]),
                    "level": trigger, "side": side, "opening_range": None if rng is None else {"high": rng.high, "low": rng.low},
                    "note": "This is the moment to get in, if you take the trade. Nothing was placed."}
    if now > deadline:
        return {"status": "expired", "level": trigger, "cutoff": str(deadline)}
    last = float(after["close"].iloc[-1]) if len(after) else None
    return {"status": "waiting", "level": trigger, "last_close": last,
            "distance": None if last is None else round(side * (trigger - last), 3)}


# ---------------------------------------------------------------- Exit Planner
def _d(x) -> Decimal:
    return Decimal(str(x))


def _away(price: Decimal, side: int) -> Decimal:
    """Round a stop to the cent, away from the entry: down for a long, up for a short."""
    return price.quantize(Decimal("0.01"), rounding=ROUND_FLOOR if side > 0 else ROUND_CEILING)


def plan_exit(entry: float, side: int = 1, stop_rule: str = "or_mid", stop_param: float | None = None, target_r: float | None = 2.0,
              target_price: float | None = None, or_high: float | None = None, or_low: float | None = None, atr: float | None = None,
              spread: float | None = None) -> dict:
    """Stop and target before the entry. Long-side arithmetic matches upstream's `compute` exactly."""
    e = _d(entry).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if e <= 0:
        raise PlanRefused("The entry price must be above zero")
    if stop_rule in ("or_mid", "or_low", "or_high"):
        if or_high is None or or_low is None:
            raise PlanRefused(f"A {stop_rule} stop needs the opening range")
        high, low = _d(or_high), _d(or_low)
        if high <= low:
            raise PlanRefused("The opening-range high must be above its low")
        raw = (high + low) / 2 if stop_rule == "or_mid" else (low if side > 0 else high)
    elif stop_rule == "price":
        raw = _d(stop_param)
    elif stop_rule == "pct":
        raw = e * (1 - side * _d(stop_param) / 100)
    elif stop_rule == "atr":
        if atr is None:
            raise PlanRefused("An ATR stop needs an ATR reading")
        raw = e - side * _d(stop_param) * _d(round(atr, 6))
    else:
        raise PlanRefused(f"Unknown stop rule {stop_rule!r}")
    stop = _away(raw, side)
    if side * (e - stop) <= 0:
        raise PlanRefused(f"The stop {stop} is on the wrong side of the entry {e}")
    risk = side * (e - stop)
    floor = e * MIN_RISK_PCT / 100
    if risk < floor:
        raise PlanRefused(f"The stop is {risk} from the entry, under 0.05% of price: too tight to survive the spread")
    if spread is not None and risk <= _d(spread) * 2:
        raise PlanRefused(f"The stop is {risk} from the entry, no more than twice the {spread} spread")
    if target_price is not None:
        target = _d(target_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        target = (e + side * _d(target_r) * risk).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if side * (target - e) <= 0:
        raise PlanRefused(f"The target {target} is on the wrong side of the entry {e}")
    return {"side": side, "entry": float(e), "stop": float(stop), "target": float(target), "risk_per_oz": float(risk),
            "reward_risk": float((side * (target - e)) / risk), "stop_rule": stop_rule,
            "note": "A stop is a plan, not a guarantee: a gap or fast market can fill past it."}


def session_atr(bars: pd.DataFrame, n: int = 14) -> float | None:
    if len(bars) < 2:
        return None
    series = atr_series(bars["high"], bars["low"], bars["close"], min(n, len(bars) - 1))
    value = series.iloc[-1]
    return None if np.isnan(value) else float(value)


# ---------------------------------------------------------------- Budget Manager
def size_trade(plan: dict, equity: float, max_loss_usd: float, max_position_pct: float = 75.0, max_order_lots: float = 2.0,
               cash_buffer_pct: float = 10.0, contract_size: float = 100.0, lot_step: float = 0.01, lot_min: float = 0.01,
               leverage: float = 1.0) -> dict:
    """Smallest of the four limits, and which decided it. A plan too wide for the max loss is refused.

    Upstream's whole shares become lots. With contract_size 1, step 1 and leverage 1 this is the same as upstream's
    calculation, and a test holds it to upstream's published example.
    """
    entry, risk = plan["entry"], plan["risk_per_oz"]
    if risk <= 0 or entry <= 0:
        raise PlanRefused("The plan's risk must be positive")
    notional_per_lot = entry * contract_size
    limits = {
        "max_loss": max_loss_usd / (risk * contract_size),
        "position_cap": equity * max_position_pct / 100 * leverage / notional_per_lot,
        "max_order": max_order_lots,
        "cash": equity * (1 - cash_buffer_pct / 100) * leverage / notional_per_lot,
    }
    decided = min(limits, key=limits.get)
    steps = int(limits[decided] / lot_step + 1e-9)
    lots = round(steps * lot_step, 8)
    if lots < lot_min:
        raise PlanRefused(f"Zero size: the {decided} limit allows only {limits[decided]:.4f} lots")
    return {"lots": lots, "decided_by": decided, "limits": {k: round(v, 4) for k, v in limits.items()},
            "notional": round(lots * notional_per_lot, 2), "max_loss_at_stop": round(lots * contract_size * risk, 2),
            "note": "A proposal only. The risk engine checks it next. A stop can fill worse than its price."}


# ---------------------------------------------------------------- Price Alerts
def gold_status(now: datetime, session_open: str = "new_york_comex") -> dict:
    """The status shape upstream's alert engine expects, for the gold market."""
    ny = now.astimezone(cal.NEW_YORK)
    zone, hour, minute = SESSION_OPENS[session_open]
    return {"now_new_york": ny.strftime("%Y-%m-%d %H:%M"), "date": ny.date().isoformat(), "weekday": ny.strftime("%A"),
            "calendar_known": True, "open": cal.market_open(now.astimezone(timezone.utc)),
            "reason": "gold session" if cal.market_open(now.astimezone(timezone.utc)) else "gold market closed",
            "early_close": None, "opens": "18:00", "closes": "17:00"}


def check_alerts(rules: list[dict], price: float, prev_close: float | None, state: dict | None, now: datetime) -> tuple[list[dict], dict]:
    """Upstream's rule engine, unchanged. Only the market clock and quote source are gold's."""
    alerts = upstream.load("alerts")
    utc = now.astimezone(timezone.utc).replace(tzinfo=None)
    quotes = {"XAUUSD": {"price": price, "prev_close": prev_close}}
    rings, new_state = alerts.evaluate(rules, quotes, gold_status(now), state or {}, utc)
    for ring in rings:
        ring["message"] = ring["message"].replace("the NYSE is open", "the gold market is open")
    return rings, new_state


def alerts_from_plan(plan: dict, date: str, flat_by: str | None = None) -> list[dict]:
    side = plan["side"]
    rules = [
        {"id": "xau-plan-stop", "symbol": "XAUUSD", "type": "below" if side > 0 else "above", "price": plan["stop"], "date": date, "note": "stop"},
        {"id": "xau-plan-target", "symbol": "XAUUSD", "type": "above" if side > 0 else "below", "price": plan["target"], "date": date, "note": "target"},
    ]
    if flat_by:
        rules.append({"id": "xau-flat-by", "symbol": "XAUUSD", "type": "time", "at": flat_by, "date": date, "label": "flat by"})
    return rules


# ---------------------------------------------------------------- News Monitor
GOLD_WATCH = {"GLD": "SPDR Gold", "IAU": "iShares Gold", "NEM": "Newmont", "GOLD": "Barrick", "AEM": "Agnico Eagle"}
GOLD_TERMS = (r"\bgold\b", r"\bbullion\b", r"\bXAU\b", r"\bFOMC\b", r"\bFed\b", r"\bCPI\b", r"\bpayrolls\b",
              r"\binflation\b", r"\btreasury yields?\b", r"\bdollar\b", r"\bcentral bank")


def tag_headlines(items: list[dict]) -> list[dict]:
    """Upstream's keyword tags, plus a relevance flag for gold. Headlines are leads, not facts."""
    import re

    news = upstream.load("news")
    out = []
    for item in items:
        record = {"title": item.get("title", ""), "summary": item.get("summary", ""), "ticker": item.get("ticker", ""), "kind": "headline",
                  "id": item.get("url") or item.get("title", ""), "link": item.get("url", ""), "time": item.get("published_ts"), "source": item.get("source", "")}
        news.tag([record], GOLD_WATCH)
        hay = f"{record['title']} {record['summary']}"
        record["gold_relevant"] = any(re.search(t, hay, re.I) for t in GOLD_TERMS)
        out.append(record)
    return out


class NewsDesk:
    """Shows only what is new since the last check, as upstream does. State is a set of seen ids."""

    def __init__(self, seen: set[str] | None = None):
        self.seen = seen or set()

    def new_items(self, items: list[dict]) -> list[dict]:
        fresh = [i for i in tag_headlines(items) if i["id"] not in self.seen]
        self.seen |= {i["id"] for i in fresh}
        return fresh


# ---------------------------------------------------------------- Copycat, for gold
GOLD_EXPOSURE = {"GLD", "IAU", "IAUM", "GLDM", "SGOL", "PHYS", "BAR", "GDX", "GDXJ", "RING", "NEM", "GOLD", "AEM", "FNV", "WPM", "KGC", "AU", "GFI", "HMY"}


def gold_exposure(positions: list[dict]) -> dict:
    """Filter a filer's 13F to gold-linked holdings. 13Fs never contain spot gold itself.

    Point-in-time rule: this is usable only from the filing date, up to 45 days after quarter end.
    """
    picked = [p for p in positions if str(p.get("ticker", "")).upper() in GOLD_EXPOSURE]
    total = sum(float(p.get("pct") or 0) for p in picked)
    return {"positions": sorted(picked, key=lambda p: -float(p.get("pct") or 0)), "share_of_13f_pct": round(total, 3),
            "caveat": "Long US-listed positions only, up to 45 days old when filed. Not what the fund holds today."}
