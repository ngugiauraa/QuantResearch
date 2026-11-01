from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
OUTPUT = ROOT / "data" / "processed" / "coverage_audit.json"
PATTERN = re.compile(r"(?P<instrument>xauusd)_(?P<price>bid|ask)_(?P<tf>tick|s1|m1|m5|m15|m30|h1|h4|d1|mn1)_(?P<year>\d{4})_(?P<month>\d{2})\.csv$", re.I)

def audit(raw_dir: Path = RAW, end: date | None = None) -> dict:
    end = end or date.today()
    inventory = defaultdict(dict)
    unrecognized = []
    for path in raw_dir.rglob("*.csv"):
        match = PATTERN.search(path.name)
        if not match:
            unrecognized.append(str(path))
            continue
        meta = match.groupdict()
        key = f"{meta['instrument'].lower()}_{meta['price'].lower()}_{meta['tf'].lower()}"
        ym = f"{meta['year']}-{meta['month']}"
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        inventory[key][ym] = {"path": str(path), "bytes": size, "empty": size == 0}

    reports = {}
    for key, months in sorted(inventory.items()):
        tf = key.rsplit("_", 1)[-1]
        earliest = date(1999, 6, 1) if tf in {"d1", "mn1"} else date(2003, 5, 1)
        first = min((date.fromisoformat(m + "-01") for m in months), default=None)
        expected = []
        cursor = earliest.replace(day=1)
        last = end.replace(day=1)
        while cursor <= last:
            expected.append(cursor.strftime("%Y-%m"))
            cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
        missing = [m for m in expected if m not in months]
        reports[key] = {
            "timeframe": tf,
            "files_found": len(months),
            "first_month_found": first.strftime("%Y-%m") if first else None,
            "last_month_found": max(months) if months else None,
            "missing_months": missing,
            "empty_files": [m for m, v in months.items() if v["empty"]],
            "files": months,
        }
    return {"generated_on": end.isoformat(), "datasets": reports, "unrecognized_csv_files": unrecognized}

def main():
    parser = argparse.ArgumentParser(description="Audit monthly XAUUSD CSV coverage.")
    parser.add_argument("--raw-dir", type=Path, default=RAW)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    report = audit(args.raw_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for name, item in report["datasets"].items():
        print(f"{name:24} files={item['files_found']:4} missing_months={len(item['missing_months']):4} first={item['first_month_found']}")
    print(f"\nReport: {args.output}")

if __name__ == "__main__":
    main()
