"""The single, recorded, confirmatory look at the contaminated holdout.

Rules enforced in code:
  - Only a candidate already at stage 'qualified' (every research gate passed on pre-holdout data) may be looked at.
  - Each frozen definition (spec hash) may be looked at once, ever. A second request raises.
  - The result is stored beside the candidate as a note. It never changes the candidate's stage upward, and it is
    labelled contaminated wherever it is shown. A negative result is reported as a warning.
The discovery store has no holdout rows, so this module builds its own temporary arrays from the warehouse.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from quantresearch.core.config import Paths, paths
from quantresearch.discovery import features as feat
from quantresearch.discovery import lab
from quantresearch.discovery import spec as S
from quantresearch.discovery.config import HOLDOUT_NOTE, HOLDOUT_START, run_dir


class HoldoutAlreadyUsed(RuntimeError):
    pass


class NotQualified(RuntimeError):
    pass


def _holdout_store(tf: str, folder: Path, p: Paths):
    from quantresearch.discovery.store import FeatureStore, _cross, _events, _write_tf
    from quantresearch.warehouse.store import load_bars
    if not (folder / tf / "meta.json").exists():
        start = HOLDOUT_START - pd.DateOffset(years=3)
        bars = load_bars(tf, start, None, p=p)
        higher = {h: load_bars(h, start, None, p=p) for h in feat.HIGHER.get(tf, ())}
        far = pd.Timestamp("2100-01-01", tz="UTC")
        prices, cat = feat.build_catalog(tf, bars, higher, _cross(far, p), _events(far, p))
        _write_tf(folder / tf, tf, prices, cat, HOLDOUT_START, "holdout", {"kind": "holdout_confirmation"})
    return FeatureStore(folder / tf, allow_holdout=True)


def confirm_once(run_id: str, cid: str, p: Paths | None = None) -> dict:
    from quantresearch.discovery.evaluate import evaluate
    from quantresearch.discovery.expr import Evaluator
    p = p or paths()
    d = run_dir(run_id, p)
    con = lab.connect(d / "lab.sqlite")
    row = con.execute("SELECT * FROM candidates WHERE run_id=? AND cid=?", (run_id, cid)).fetchone()
    if row is None or row["stage"] not in ("qualified", "paper_trading") or row["dataset"] != "real":
        raise NotQualified("Only a qualified real-data candidate may be looked at on the holdout")
    if con.execute("SELECT 1 FROM holdout_access WHERE spec_hash=?", (row["spec_hash"],)).fetchone():
        raise HoldoutAlreadyUsed(f"{row['spec_hash']} has already had its one holdout look")
    spec = S.decompress(row["spec_z"])
    store = _holdout_store(row["tf"], d / "holdout_store", p)
    a, b = store.bounds(HOLDOUT_START, pd.Timestamp("2100-01-01", tz="UTC"))
    m, _ = evaluate(spec, Evaluator(store, a, b), "observed")
    m_stress, _ = evaluate(spec, Evaluator(store, a, b), "stress", detail=False)
    result = {"label": "contaminated_holdout_confirmatory_only", "note": HOLDOUT_NOTE, "observed": m,
              "stress": {k: m_stress.get(k) for k in ("trades", "exp_r", "sharpe", "total_r")},
              "warning": None if (m.get("exp_r") or 0) > 0 else "negative on the holdout: treat the candidate with extra suspicion"}
    con.execute("INSERT INTO holdout_access VALUES(?,?,?,?,?)", (row["spec_hash"], run_id, cid, datetime.now(timezone.utc).isoformat(), lab.dumps(result)))
    con.commit()
    con.close()
    return result
