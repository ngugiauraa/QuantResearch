"""The forward test's record: every signal, order, fill, exit, missed signal, equity sample and alert.

One SQLite file per use: data/app/forward_001.sqlite for the demo test, an in-memory one for replays. Rows are only
ever added or completed (an open trade gains its exit), never deleted.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

TRADE_COLUMNS = ("ticket", "strategy", "side", "lots", "signal_close_ts", "signal_close", "stop", "target", "ref_price",
                 "fill_price", "fill_at", "spread_entry", "slippage_entry", "exit_reason", "exit_signal_ts", "exit_ref",
                 "exit_price", "exit_at", "spread_exit", "slippage_exit", "profit", "commission", "swap", "status", "late_exit")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Journal:
    def __init__(self, path: str | Path | None = None):
        self.path = str(path) if path else ":memory:"
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.con = sqlite3.connect(self.path, check_same_thread=False)
        self.con.execute("CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT)")
        self.con.execute("CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, sim_at TEXT, kind TEXT, strategy TEXT, data TEXT)")
        cols = ", ".join(f"{c} {'INTEGER PRIMARY KEY' if c == 'ticket' else ''}" for c in TRADE_COLUMNS)
        self.con.execute(f"CREATE TABLE IF NOT EXISTS trades({cols})")
        self.con.execute("CREATE TABLE IF NOT EXISTS equity(at TEXT PRIMARY KEY, equity REAL, ledger REAL, balance REAL)")
        self.con.commit()

    def close(self) -> None:
        self.con.close()

    # ------------------------------------------------------------ state
    def get(self, key: str, default=None):
        with self.lock:
            row = self.con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value) -> None:
        with self.lock:
            self.con.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (key, json.dumps(value, default=str)))
            self.con.commit()

    # ------------------------------------------------------------ events
    def event(self, kind: str, strategy: str | None = None, at: str | None = None, **data) -> None:
        with self.lock:
            self.con.execute("INSERT INTO events(at,sim_at,kind,strategy,data) VALUES(?,?,?,?,?)",
                             (_now(), at, kind, strategy, json.dumps(data, default=str)))
            self.con.commit()

    def events(self, limit: int = 200, kinds: tuple[str, ...] | None = None) -> list[dict]:
        sql = "SELECT id,at,sim_at,kind,strategy,data FROM events"
        args: list = []
        if kinds:
            sql += f" WHERE kind IN ({','.join('?' * len(kinds))})"
            args += list(kinds)
        sql += " ORDER BY id DESC LIMIT ?"
        with self.lock:
            rows = self.con.execute(sql, [*args, limit]).fetchall()
        return [{"id": a, "at": b, "bar_time": c, "kind": d, "strategy": e, **json.loads(f)} for a, b, c, d, e, f in rows]

    def counts(self) -> dict[str, int]:
        with self.lock:
            rows = self.con.execute("SELECT kind, COUNT(*) FROM events GROUP BY kind").fetchall()
        return {k: int(n) for k, n in rows}

    def missed_by_reason(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.events(100_000, ("missed",)):
            out[e.get("reason", "?")] = out.get(e.get("reason", "?"), 0) + 1
        return out

    # ------------------------------------------------------------ trades
    def add_trade(self, **row) -> None:
        row.setdefault("status", "open")
        keys = [k for k in TRADE_COLUMNS if k in row]
        with self.lock:
            self.con.execute(f"INSERT OR REPLACE INTO trades({','.join(keys)}) VALUES({','.join('?' * len(keys))})", [row[k] for k in keys])
            self.con.commit()

    def update_trade(self, ticket: int, **row) -> None:
        keys = [k for k in TRADE_COLUMNS if k in row and k != "ticket"]
        if not keys:
            return
        with self.lock:
            self.con.execute(f"UPDATE trades SET {', '.join(f'{k}=?' for k in keys)} WHERE ticket=?", [*(row[k] for k in keys), ticket])
            self.con.commit()

    def trades(self, status: str | None = None) -> list[dict]:
        sql = f"SELECT {','.join(TRADE_COLUMNS)} FROM trades" + (" WHERE status=?" if status else "") + " ORDER BY fill_at"
        with self.lock:
            rows = self.con.execute(sql, (status,) if status else ()).fetchall()
        return [dict(zip(TRADE_COLUMNS, r)) for r in rows]

    def open_trade(self, strategy: str) -> dict | None:
        return next((t for t in self.trades("open") if t["strategy"] == strategy), None)

    # ------------------------------------------------------------ equity
    def sample(self, at: str, equity: float, ledger: float, balance: float | None = None) -> None:
        with self.lock:
            self.con.execute("INSERT OR REPLACE INTO equity VALUES(?,?,?,?)", (at, equity, ledger, balance))
            self.con.commit()

    def equity_curve(self, column: str = "ledger") -> pd.Series:
        with self.lock:
            rows = self.con.execute(f"SELECT at, {column} FROM equity ORDER BY at").fetchall()
        if not rows:
            return pd.Series(dtype="float64")
        idx = pd.to_datetime([r[0] for r in rows], utc=True, format="ISO8601")
        return pd.Series([r[1] for r in rows], index=idx, dtype="float64")
