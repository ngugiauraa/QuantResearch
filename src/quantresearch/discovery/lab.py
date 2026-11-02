"""The discovery laboratory database (SQLite, WAL). One writer, the controller; readers may open it read-only.

Nothing is deleted. Rows from a round that was interrupted before its checkpoint are kept and marked superseded,
and the round is re-run deterministically.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  run_id TEXT PRIMARY KEY, created_at TEXT, config TEXT, fingerprint TEXT, status TEXT, started_at TEXT,
  ended_at TEXT, stop_reason TEXT, code_version TEXT, datasets TEXT, sessions INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS rounds(
  run_id TEXT, round INTEGER, started_at TEXT, ended_at TEXT, tasks INTEGER, experiments INTEGER, status TEXT,
  PRIMARY KEY(run_id, round));
CREATE TABLE IF NOT EXISTS tasks(
  task_id TEXT PRIMARY KEY, run_id TEXT, round INTEGER, seq INTEGER, kind TEXT, dataset TEXT, tf TEXT, vintage TEXT,
  cell TEXT, seed INTEGER, params TEXT, status TEXT, runtime_s REAL, cpu_s REAL, peak_rss_mb REAL, pid INTEGER,
  evaluated INTEGER, selected INTEGER, novelty REAL, error TEXT, superseded INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS candidates(
  run_id TEXT, cid TEXT, spec_hash TEXT, dataset TEXT, tf TEXT, vintage TEXT, direction TEXT, origin TEXT, operator TEXT,
  parents TEXT, spec_z BLOB, complexity INTEGER, family TEXT, signature TEXT, created_round INTEGER, task_id TEXT,
  stage TEXT, rejected_at TEXT, reject_reason TEXT, fitness REAL, it TEXT, iv TEXT, oos TEXT, stage3 TEXT,
  robust TEXT, confirm TEXT, ranking TEXT, max_corr_existing REAL, updated_round INTEGER,
  PRIMARY KEY(run_id, cid));
CREATE TABLE IF NOT EXISTS experiments(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, round INTEGER, task_id TEXT, cid TEXT, dataset TEXT, tf TEXT,
  vintage TEXT, stage TEXT, passed INTEGER, reason TEXT, trades INTEGER, exp_r REAL, sharpe REAL, fitness REAL,
  complexity INTEGER, origin TEXT, operator TEXT, superseded INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS folds(
  run_id TEXT, cid TEXT, fold TEXT, trades INTEGER, total_r REAL, exp_r REAL, PRIMARY KEY(run_id, cid, fold));
CREATE TABLE IF NOT EXISTS oos_series(run_id TEXT, cid TEXT, days BLOB, rets BLOB, sharpe REAL, t_nw REAL, p_nw REAL, PRIMARY KEY(run_id, cid));
CREATE TABLE IF NOT EXISTS findings(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, round INTEGER, task_id TEXT,
  kind TEXT, dataset TEXT, tf TEXT, vintage TEXT, payload TEXT, superseded INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS signatures(run_id TEXT, scope TEXT, sig TEXT, PRIMARY KEY(run_id, scope, sig));
CREATE TABLE IF NOT EXISTS archives(run_id TEXT, cell TEXT, round INTEGER, payload BLOB, PRIMARY KEY(run_id, cell));
CREATE TABLE IF NOT EXISTS resources(run_id TEXT, ts TEXT, round INTEGER, cpu_pct REAL, rss_mb REAL, peak_rss_mb REAL,
  sys_avail_mb REAL, workers INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS checkpoints(run_id TEXT, round INTEGER, path TEXT, sha256 TEXT, created_at TEXT,
  PRIMARY KEY(run_id, round));
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts TEXT, level TEXT, message TEXT);
CREATE TABLE IF NOT EXISTS holdout_access(spec_hash TEXT PRIMARY KEY, run_id TEXT, cid TEXT, accessed_at TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS forward_registry(cid TEXT PRIMARY KEY, run_id TEXT, spec_hash TEXT, spec_z BLOB,
  registered_at TEXT, forward_start TEXT, status TEXT, note TEXT);
CREATE INDEX IF NOT EXISTS ix_exp_round ON experiments(run_id, round);
CREATE INDEX IF NOT EXISTS ix_exp_stage ON experiments(run_id, stage, passed);
CREATE INDEX IF NOT EXISTS ix_cand_stage ON candidates(run_id, dataset, stage);
CREATE INDEX IF NOT EXISTS ix_cand_stage_only ON candidates(run_id, stage);
CREATE INDEX IF NOT EXISTS ix_cand_family ON candidates(run_id, dataset, tf, vintage, family);
CREATE INDEX IF NOT EXISTS ix_cand_updated ON candidates(run_id, updated_round);
CREATE INDEX IF NOT EXISTS ix_tasks_round ON tasks(run_id, round);
"""


def connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=30, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(oos_series)")}
    for col in ("t_nw", "p_nw"):
        if col not in cols:
            con.execute(f"ALTER TABLE oos_series ADD COLUMN {col} REAL")
    con.commit()
    return con


def jsonable(value):
    """NaN and infinity become null; numpy and pandas scalars become plain Python. (Kept local: discovery must not
    import the API package, which reaches the broker.)"""
    import math

    import numpy as np
    import pandas as pd
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return [jsonable(v) for v in value.tolist()]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, bytes):
        return None
    return value


def dumps(value) -> str:
    return json.dumps(jsonable(value), separators=(",", ":"))
