"""Pick 15 example trades per setup, spread across years, for the trader to check against how they trade.

    python -m quantresearch.playbooks.examples

Reads the trades written by `playbooks.setups`. Years are chosen evenly across those that have trades, and within a
year the trade is drawn with a fixed seed: no picking of good-looking ones. Writes
research/playbooks/examples.json, which the dashboard's My Playbooks page shows with an M5 chart of each moment.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from quantresearch.core.config import paths
from quantresearch.playbooks.setups import SETUPS

PER_SETUP = 15
SEED = 20261128


def load_trades(folder) -> list[dict]:
    rows = []
    for f in sorted(folder.glob("trades_*.json")):
        rows += [r for r in json.loads(f.read_text(encoding="utf-8")) if r.get("trade") and "r" in r["trade"]]
    return rows


def pick(rows: list[dict], n: int = PER_SETUP, seed: int = SEED) -> list[dict]:
    rng = np.random.default_rng(seed)
    by_year: dict[str, list[dict]] = {}
    for r in rows:
        by_year.setdefault(r["day"][:4], []).append(r)
    years = sorted(by_year)
    if len(years) >= n:
        chosen_years = [years[int(round(i))] for i in np.linspace(0, len(years) - 1, n)]
    else:  # Fewer years than examples: cycle through them.
        chosen_years = [years[i % len(years)] for i in range(n)]
    out, used = [], set()
    for y in chosen_years:
        pool = [r for r in by_year[y] if r["day"] not in used]
        if not pool:
            continue
        r = pool[int(rng.integers(len(pool)))]
        used.add(r["day"])
        out.append(r)
    return sorted(out, key=lambda r: r["day"])


def describe(r: dict) -> dict:
    s, t = r["signal"], r["trade"]
    ny = lambda x: pd.Timestamp(x).tz_convert("America/New_York").strftime("%Y-%m-%d %H:%M")
    return {"setup": r["setup"], "date": r["day"], "side": "long" if s["side"] > 0 else "short",
            "entry_time_ny": ny(t["fill_ts"]), "entry": round(t["fill"], 2), "stop": round(s["stop"], 2), "target": round(t["target"], 2),
            "target_kind": t["target_kind"], "risk_usd": round(t["risk"], 2), "result_r": round(t["r"], 2), "result_r_stressed": round(t["r_stress"], 2),
            "exit_reason": t["exit_reason"], "exit_time_ny": ny(t["exit_ts"]), "exit": round(t["exit"], 2),
            "signal_ts": s["signal_ts"], "fill_ts": t["fill_ts"], "exit_ts": t["exit_ts"], "context": s["context"]}


def build() -> dict:
    p = paths()
    folder = p.research / "playbooks"
    rows = load_trades(folder)
    out = {"note": "Examples for confirmation only; no results are reported until the definitions are confirmed.",
           "definitions": "docs/my_playbooks.md", "setups": {}}
    for name in SETUPS:
        out["setups"][name] = [describe(r) for r in pick([r for r in rows if r["setup"] == name])]
    (folder / "examples.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return out


if __name__ == "__main__":
    o = build()
    for k, v in o["setups"].items():
        print(k, len(v), [e["date"] for e in v])
