"""Do the best candidates of one discovery run reappear in a run with a different random seed?

    python -m quantresearch.discovery.compare --a main --b seed2

Robust findings should be found again by an independent search; search luck should not. A candidate of run A
"reappears" in run B at four levels, from strictest to loosest:
  identical rule       the same spec (spec_hash)
  identical trades     a different spec whose entries are the same trades (entry signature)
  same rule shape      the same structure with different constants (structural family)
  same ingredients     the same timeframe, direction and set of features
Each level is checked against everything B evaluated, against B's walk-forward passers, and against B's own best N.
Only real-data candidates count; null-market candidates are excluded. "Best" is the out-of-sample Sharpe among
candidates that passed walk-forward, as in the run reports.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from quantresearch.core.config import paths
from quantresearch.discovery import spec as S

LEVELS = ("identical_rule", "identical_trades", "same_rule_shape", "same_ingredients")


def _features(node, out: set) -> set:
    if isinstance(node, dict):
        if "f" in node and isinstance(node["f"], str):
            out.add(node["f"])
        for v in node.values():
            _features(v, out)
    elif isinstance(node, list):
        for v in node:
            _features(v, out)
    return out


def load(run_id: str, root: Path | None = None) -> dict:
    root = root or paths().research / "discovery" / "runs"
    con = sqlite3.connect(f"file:{(root / run_id / 'lab.sqlite').as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            """SELECT c.cid, c.spec_hash, c.family, c.signature, c.tf, c.direction, c.stage, c.spec_z, o.sharpe
               FROM candidates c LEFT JOIN oos_series o ON o.run_id = c.run_id AND o.cid = c.cid
               WHERE c.run_id = ? AND c.dataset = 'real'""", (run_id,)).fetchall()
    finally:
        con.close()
    cands = []
    for cid, h, fam, sig, tf, d, stage, z, sharpe in rows:
        cands.append({"cid": cid, "spec_hash": h, "family": fam, "signature": sig, "tf": tf, "direction": d, "stage": stage,
                      "sharpe": sharpe, "spec_z": z})
    return {"run_id": run_id, "candidates": cands}


def _ingredients(c: dict) -> str:
    if "ingredients" not in c:
        try:
            feats = sorted(_features(S.decompress(c["spec_z"]), set()))
        except Exception:
            feats = []
        c["ingredients"] = f"{c['tf']}|{c['direction']}|{','.join(feats)}"
    return c["ingredients"]


def _keys(c: dict) -> dict[str, str | None]:
    return {"identical_rule": c["spec_hash"], "identical_trades": c["signature"] or None, "same_rule_shape": c["family"],
            "same_ingredients": _ingredients(c)}


def best(run: dict, n: int) -> list[dict]:
    wf = [c for c in run["candidates"] if c["stage"] == "walk_forward" and c["sharpe"] is not None]
    return sorted(wf, key=lambda c: c["sharpe"], reverse=True)[:n]


def compare(a: dict, b: dict, tops=(10, 50)) -> dict:
    pools = {"evaluated": b["candidates"], "walk_forward": [c for c in b["candidates"] if c["stage"] == "walk_forward"]}
    for n in tops:
        pools[f"best_{n}"] = best(b, n)
    index = {pool: {lvl: {_keys(c)[lvl] for c in cs} - {None} for lvl in LEVELS} for pool, cs in pools.items()}
    out = {"a": a["run_id"], "b": b["run_id"], "computed_at": datetime.now(timezone.utc).isoformat(),
           "b_counts": {k: len(v) for k, v in pools.items()}, "tops": {}}
    for n in tops:
        top = best(a, n)
        res = {pool: {lvl: sum(1 for c in top if _keys(c)[lvl] in index[pool][lvl]) for lvl in LEVELS} for pool in pools}
        detail = [{"cid": c["cid"], "tf": c["tf"], "direction": c["direction"], "oos_sharpe": c["sharpe"],
                   **{f"in_b_{pool}": next((lvl for lvl in LEVELS if _keys(c)[lvl] in index[pool][lvl]), None) for pool in ("evaluated", "walk_forward", f"best_{n}")}}
                  for c in top]
        out["tops"][str(n)] = {"size": len(top), "reappear": res, "candidates": detail}
    return out


def write(result: dict, folder: Path | None = None) -> Path:
    folder = folder or paths().research / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"discovery_seed_check_{result['a']}_{result['b']}.json"
    path.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--a", default="main")
    ap.add_argument("--b", default="seed2")
    args = ap.parse_args(argv)
    result = compare(load(args.a), load(args.b))
    print(write(result))
    for n, t in result["tops"].items():
        print(f"best {n} of {args.a}:", json.dumps(t["reappear"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
