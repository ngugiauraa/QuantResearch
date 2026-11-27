"""Write docs/strategy_catalogue.md and docs/strategy_catalogue.json from the catalogue's own entries.

    python -m quantresearch.strategies.catalogue.docs

The JSON manifest is the record of every strategy ever catalogued. tests/test_catalogue.py fails if a name in it
disappears from the code (failing strategies stay on record) or if a strategy's parameters drift from it.
New names are appended; existing entries are never dropped.
"""
from __future__ import annotations

import json
from pathlib import Path

from quantresearch.strategies.catalogue import CATALOGUE, ENTRIES

DOCS = Path(__file__).resolve().parents[4] / "docs"
FAMILIES = {"trend_breakout": "Trend and breakout", "mean_reversion": "Mean reversion", "volatility": "Volatility", "session": "Session and time of day",
            "calendar": "Calendar", "event": "Scheduled events", "macro": "Macro", "cross_market": "Cross-market", "smc_ict": "SMC / ICT structure"}


def write(folder: Path = DOCS) -> tuple[Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    manifest_path = folder / "strategy_catalogue.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8"))["strategies"] if manifest_path.exists() else []
    current = {e.name: e.as_dict() for e in ENTRIES}
    kept = [current.get(s["name"], {**s, "removed_from_code": True}) for s in old]
    known = {s["name"] for s in old}
    kept += [d for name, d in current.items() if name not in known]
    manifest_path.write_text(json.dumps({"note": "Every catalogue strategy ever defined. Never remove entries.", "strategies": kept}, indent=1) + "\n", encoding="utf-8")

    lines = ["# Strategy catalogue", "",
             f"{len(ENTRIES)} gold (XAUUSD) strategies that are commonly claimed to be automatable, plus some of this project's own ideas. "
             "Each is implemented in `src/quantresearch/strategies/catalogue/` and runs through exactly the same research as the 8 engines: "
             "walk-forward out-of-sample folds, the untouched two-year holdout, observed and stressed costs, and the same report.", "",
             "**Hard rules.** Parameters are fixed as written here and are never tuned (each strategy has one variant; the first run locks it). "
             "Every run is recorded in the experiment ledger and the trial book, and nothing is deleted. The deflated Sharpe that decides "
             "'promising' counts every variant ever tested by the engines and the catalogue. A strategy is only promising if it has at least 30 "
             "out-of-sample trades, is positive out of sample, positive in the holdout, positive in the holdout under stressed costs, and has a "
             "project-wide deflated Sharpe above 0.95.", "",
             "**Conventions.** Signals act at a bar's close and fill on a later event, so 'breaks above X' means 'closes above X'. ATR is Wilder ATR(14) "
             "on the strategy's timeframe. Every strategy has a stop because positions are sized from it (0.5% risk). Times are local market time "
             "with daylight saving. 'Needs data we don't have' strategies are implemented but cannot trade until that data exists.", "",
             "Regenerate with `python -m quantresearch.strategies.catalogue.docs`. Run with `python -m quantresearch.research.catalogue --all` "
             "or from the dashboard's Strategy Catalogue table.", ""]
    missing = [e for e in ENTRIES if e.missing_data]
    if missing:
        lines += ["## Needs data we don't have", ""] + [f"- `{e.name}`: {'; '.join(e.missing_data)}" for e in missing] + [""]
    for batch in sorted({e.batch for e in ENTRIES}):
        lines += [f"## Batch {batch}", ""]
        for e in [x for x in ENTRIES if x.batch == batch]:
            d = e.defn
            lines += [f"### `{d.name}`: {d.title}", "",
                      f"- **Family:** {FAMILIES.get(e.family, e.family)}. **Timeframe:** {d.signal_tf.upper()}.",
                      f"- **Source:** {e.source}",
                      f"- **Logic:** {e.logic}",
                      f"- **Entry:** {e.entry}",
                      f"- **Exit:** {e.exit}",
                      f"- **Stop:** {e.stop}",
                      f"- **Data:** {'; '.join(e.data)}" + (f". Cross-market: {', '.join(d.needs)}" if d.needs else "") + (f". Macro: {', '.join(e.macro)}" if e.macro else ""),
                      f"- **Parameters (fixed):** `{json.dumps(d.defaults, default=str)}`"]
            if e.missing_data:
                lines.append(f"- **Needs data we don't have:** {'; '.join(e.missing_data)}")
            lines.append("")
    md = folder / "strategy_catalogue.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    return md, manifest_path


if __name__ == "__main__":
    for path in write():
        print(path)
    print(f"{len(CATALOGUE)} strategies")
