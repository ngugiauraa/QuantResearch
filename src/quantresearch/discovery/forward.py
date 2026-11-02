"""Forward paper-trading registry: the only route out of discovery, and it leads to paper trading only.

Registering freezes a qualified candidate's definition with a timestamp. Only bars that open after that moment
count as forward evidence. Execution happens in `execution/discovery_paper.py`, through the independent risk
engine and a separate paper account. Nothing here can enable live trading.
"""
from __future__ import annotations

from datetime import datetime, timezone

from quantresearch.core.config import Paths, paths
from quantresearch.discovery import lab
from quantresearch.discovery.config import run_dir


def register(run_id: str, cid: str, p: Paths | None = None, when: datetime | None = None) -> dict:
    p = p or paths()
    con = lab.connect(run_dir(run_id, p) / "lab.sqlite")
    row = con.execute("SELECT * FROM candidates WHERE run_id=? AND cid=?", (run_id, cid)).fetchone()
    if row is None or row["stage"] not in ("qualified", "paper_trading") or row["dataset"] != "real":
        con.close()
        raise ValueError("Only a qualified real-data candidate can be registered for paper trading")
    when = (when or datetime.now(timezone.utc)).isoformat()
    con.execute("INSERT OR IGNORE INTO forward_registry VALUES(?,?,?,?,?,?,?,?)",
                (cid, run_id, row["spec_hash"], row["spec_z"], when, when, "registered",
                 "paper only; forward evidence starts at registration; live trading remains disabled"))
    con.execute("UPDATE candidates SET stage='paper_trading' WHERE run_id=? AND cid=?", (run_id, cid))
    con.commit()
    out = dict(con.execute("SELECT cid, run_id, spec_hash, registered_at, forward_start, status, note FROM forward_registry WHERE cid=?", (cid,)).fetchone())
    con.close()
    return out


def registered(run_id: str, p: Paths | None = None) -> list[dict]:
    p = p or paths()
    con = lab.connect(run_dir(run_id, p) / "lab.sqlite")
    rows = [dict(r) for r in con.execute("SELECT * FROM forward_registry WHERE run_id=?", (run_id,))]
    con.close()
    return rows
