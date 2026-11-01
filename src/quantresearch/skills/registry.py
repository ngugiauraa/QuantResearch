"""Every skill behind one interface, each with an honest record of what was kept and what changed."""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable

from quantresearch.core.config import paths
from quantresearch.skills import upstream, xauusd


@dataclass
class Skill:
    key: str
    name: str
    job: str
    kept: list[str]
    adapted: list[str]
    unavailable: list[str]
    upstream_version: str
    needs: list[str] = field(default_factory=list)
    actions: dict[str, Callable] = field(default_factory=dict, repr=False)

    def info(self) -> dict:
        return {"key": self.key, "name": self.name, "job": self.job, "kept": self.kept, "adapted": self.adapted,
                "unavailable": self.unavailable, "upstream_version": self.upstream_version, "needs": self.needs,
                "actions": sorted(self.actions), "ready": self.ready(), "missing": self.missing()}

    def missing(self) -> list[str]:
        return [n for n in self.needs if n == "SEC_USER_AGENT" and not sec_user_agent()]

    def ready(self) -> bool:
        return not self.missing()

    def instructions(self) -> str:
        return upstream.instructions(self.key)


def sec_user_agent() -> str | None:
    """SEC asks automated users for a real name and email. It is never invented here."""
    if os.environ.get("SEC_USER_AGENT"):
        return os.environ["SEC_USER_AGENT"]
    path = upstream.workspace() / "trader" / "config.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")).get("sec_user_agent")
    return None


def _tool(skill: str):
    def run(args: list[str], timeout: int = 180) -> dict:
        return upstream.run_tool(skill, args, timeout)
    return run


V2 = "day-trader-skill 3d0f4ef"
V1 = "trader-skill 5189e79"

SKILLS: dict[str, Skill] = {s.key: s for s in [
    Skill("news", "Trader News", "New headlines and filings, tagged, each shown once",
          ["Keyword tags", "Seen-once state", "Headlines are leads, not facts", "No predictions", "SEC 8-K reading for listed companies"],
          ["Gold relevance flag", "GDELT as a headline source", "Miners and gold ETFs as the default watchlist"],
          ["8-K item logic has no meaning for spot gold itself", "Yahoo RSS for gold tickers is unverified"],
          V2, actions={"tag": xauusd.tag_headlines, "tool": _tool("news")}),
    Skill("risk", "Trader Risk", "PASS or BLOCK per order, before any broker",
          ["Exits are never blocked by entry limits", "Batch-safe checks", "Fail-closed behaviour", "Every decision recorded", "Loss per trade, daily loss, trades per day, reward:risk, stop required"],
          ["Runs on the platform's independent RiskEngine", "Lots, not shares", "Drawdown, spread, slippage, volatility, news and emergency-stop limits added"],
          ["US-ticker asset check would block XAUUSD, so it applies only in the original tool", "PDT rule does not apply to spot gold"],
          V2, actions={"tool": _tool("risk")}),
    Skill("budget", "Trader Budget", "Sizes a position from the smallest of four limits",
          ["Smallest-of-limits sizing", "Says which limit decided", "Refuses rather than shrinks an impossible plan", "Proposals only"],
          ["Lots and contract size replace whole shares", "Leverage input for margin accounts"],
          ["Alpaca order format"], V2, actions={"size": xauusd.size_trade, "tool": _tool("budget")}),
    Skill("broker", "Trader Broker", "Tickets, a recorded yes for one exact order, audit log",
          ["One yes for one order", "'sure' and 'yes to all' do not count", "Risk passes first", "Failed orders are not retried without a new check", "Never from a background task in assisted mode"],
          ["The MT5 adapter replaces Alpaca and IBKR connectors", "The approval step is real code in OrderRouter, not an instruction", "Automated mode exists, as the project requires, behind separate activation: a recorded deviation from upstream"],
          ["Alpaca and IBKR MCP setup", "The AI client's own permission card"],
          V2, actions={"tool": _tool("broker")}),
    Skill("alerts", "Trader Alerts", "Level, percent, time and session alerts that ring once",
          ["Upstream's alert engine is called unchanged", "Ring-once state", "Rules from a plan", "Alerts are facts, never orders"],
          ["Gold market clock replaces NYSE hours", "Short-side plan rules", "Quotes from the broker or warehouse"],
          ["NYSE holiday tables", "Stooq was returning errors upstream"],
          V2, actions={"check": xauusd.check_alerts, "from_plan": xauusd.alerts_from_plan}),
    Skill("analyst", "Trader Analyst", "Reported results from SEC XBRL filings",
          ["The original tool, run unchanged", "Filed numbers first", "Derived quarters marked"],
          ["Default targets are gold miners, as context"],
          ["XAUUSD has no earnings or filings, so this never produces a gold signal"],
          V2, needs=["SEC_USER_AGENT"], actions={"tool": _tool("analyst")}),
    Skill("screener", "Trader Screener", "Applies your rules, explains every pass and fail, ranks",
          ["The original engine, called unchanged", "Missing means missing", "Never calls a pass a buy"],
          ["Works on any metrics table, such as miners or strategy reports"],
          ["Stock-picking is not this project's purpose"],
          V1, actions={"screen": lambda companies, spec: upstream.load("screener").screen(companies, spec), "tool": _tool("screener")}),
    Skill("copycat", "Trader Copycat", "Rebuilds a fund's holdings from its 13F-HR",
          ["The original parser and diff, run unchanged", "Data age stated every time", "Firm, not person", "No claim of current holdings"],
          ["A filter for gold ETFs and miners, to observe institutional positioning", "Filing date is the point-in-time availability date"],
          ["13Fs do not cover spot gold", "Portfolio copying does not map onto one XAUUSD position"],
          V1, needs=["SEC_USER_AGENT"], actions={"gold_exposure": xauusd.gold_exposure, "tool": _tool("copycat")}),
    Skill("scanner", "Day Trader Scanner", "Lists charts matching setup rules",
          ["Upstream's find_setups is called unchanged", "A match is not a recommendation", "Says what was scanned"],
          ["Timeframes replace the stock universe, since gold is one instrument"],
          ["Universe of about 5,900 US stocks", "Yahoo liquidity filters"],
          V2, actions={"setups": xauusd.scan_setups, "timeframes": xauusd.scan_timeframes, "tool": _tool("scanner")}),
    Skill("entry", "Entry Timer", "The first completed bar to close beyond a level",
          ["A wick is not a trigger", "Only closed bars count", "One trigger per setup", "Expiry", "Optional volume check", "Reproduces upstream's TSLA example"],
          ["Short side added", "Session choice, since gold has no single open"],
          ["NYSE clock"], V2, actions={"check": xauusd.entry_status, "tool": _tool("entry")}),
    Skill("exit", "Exit Planner", "Stop and target before the entry",
          ["Stop rules", "Rounding away from the entry", "R-based targets", "Too-tight refusal", "Reproduces upstream's example"],
          ["Short side added", "A spread-aware refusal"],
          ["NYSE flat-by defaults"], V2, actions={"plan": xauusd.plan_exit}),
]}


def outcome_info() -> dict:
    return {"key": "timemachine", "name": "Outcome Simulator (original Time Machine)", "upstream_version": V2,
            "kept": ["Bar draws", "Stop before target", "Seeding", "Verdict logic and wording"],
            "adapted": ["288 five-minute slots on the New York day", "Local warehouse data", "Spread-adjusted R", "Partial sessions"],
            "unavailable": ["Yahoo data path"]}


class SkillLog:
    """Records every skill call for the dashboard. Inputs are stored; secrets are not passed here."""

    def __init__(self, path=None):
        path = path or paths().app / "skills.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.execute("CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, skill TEXT, action TEXT, status TEXT, output TEXT)")
        self.con.commit()

    def record(self, skill: str, action: str, status: str, output) -> None:
        self.con.execute("INSERT INTO runs(at,skill,action,status,output) VALUES(?,?,?,?,?)",
                         (datetime.now(timezone.utc).isoformat(), skill, action, status, json.dumps(output, default=str)[:20000]))
        self.con.commit()

    def recent(self, skill: str | None = None, limit: int = 50) -> list[dict]:
        sql = "SELECT at,skill,action,status,output FROM runs" + (" WHERE skill=?" if skill else "") + " ORDER BY id DESC LIMIT ?"
        rows = self.con.execute(sql, ((skill, limit) if skill else (limit,))).fetchall()
        return [{"at": a, "skill": b, "action": c, "status": d, "output": json.loads(e)} for a, b, c, d, e in rows]


def run(key: str, action: str, *args, log: SkillLog | None = None, **kwargs):
    if key not in SKILLS:
        raise KeyError(f"Unknown skill {key!r}")
    skill = SKILLS[key]
    if action not in skill.actions:
        raise KeyError(f"Skill {key} has no action {action!r}. Actions: {sorted(skill.actions)}")
    if skill.missing():
        result = {"status": "needs_configuration", "missing": skill.missing()}
        if log:
            log.record(key, action, "needs_configuration", result)
        return result
    try:
        output = skill.actions[action](*args, **kwargs)
    except xauusd.PlanRefused as exc:
        if log:
            log.record(key, action, "refused", str(exc))
        raise
    if log:
        log.record(key, action, "ok", output)
    return output
