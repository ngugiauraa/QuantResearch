
"""Inventory local research datasets without modifying source files."""

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = ROOT / "config" / "gold_data_catalog.json"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "gold_data_inventory.json"
DEFAULT_DIRECTORIES = [ROOT / "data" / "raw", ROOT / "data" / "processed"]

TIME_COLUMNS = ("timestamp", "datetime", "date", "time", "gmt time")


def display_path(path):
    """Use a project-relative path when possible, otherwise an absolute path."""
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def parse_timestamp(value):
    if not value:
        return None
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def inspect_csv(path):
    path = Path(path)
    timestamps = []
    rows_read = 0
    invalid_timestamps = 0
    timestamp_column = None

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        lookup = {column.strip().lower(): column for column in columns}

        for candidate in TIME_COLUMNS:
            if candidate in lookup:
                timestamp_column = lookup[candidate]
                break

        for row in reader:
            rows_read += 1
            if timestamp_column is None:
                continue
            parsed = parse_timestamp(row.get(timestamp_column, ""))
            if parsed is None:
                invalid_timestamps += 1
            else:
                timestamps.append(parsed)

    timestamps.sort()
    unique_timestamps = set(timestamps)

    return {
        "path": display_path(path),
        "file_size_bytes": path.stat().st_size,
        "rows": rows_read,
        "columns": columns,
        "timestamp_column": timestamp_column,
        "valid_timestamps": len(timestamps),
        "invalid_timestamps": invalid_timestamps,
        "first_timestamp": timestamps[0].isoformat() if timestamps else None,
        "last_timestamp": timestamps[-1].isoformat() if timestamps else None,
        "timestamp_timezone_present": (
            bool(timestamps)
            and all(ts.tzinfo is not None for ts in timestamps)
        ),
        "unique_timestamps": len(unique_timestamps),
        "duplicate_timestamp_count": len(timestamps) - len(unique_timestamps),
    }


def build_inventory(catalog_path=DEFAULT_CATALOG, directories=None):
    catalog_path = Path(catalog_path)
    directories = DEFAULT_DIRECTORIES if directories is None else directories

    with catalog_path.open("r", encoding="utf-8") as handle:
        catalog = json.load(handle)

    paths = set()
    for directory in directories:
        directory = Path(directory)
        if directory.exists():
            paths.update(p.resolve() for p in directory.rglob("*.csv"))

    datasets = []
    for path in sorted(paths):
        try:
            datasets.append(inspect_csv(path))
        except (OSError, UnicodeError, csv.Error) as exc:
            datasets.append({
                "path": display_path(path),
                "inspection_error": str(exc),
            })

    return {
        "instrument_scope": catalog["instrument"],
        "catalog_path": display_path(catalog_path),
        "catalog_datasets": catalog["datasets"],
        "local_csv_files": datasets,
        "notes": [
            "Coverage endpoints do not prove continuous coverage.",
            "Duplicate timestamps are checked within each file.",
            "Naive timestamps are not assumed to be UTC.",
            "This inventory does not establish data provenance or correctness.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--directories", nargs="*", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    directories = args.directories
    if directories is None:
        directories = DEFAULT_DIRECTORIES

    inventory = build_inventory(args.catalog, directories)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(inventory, indent=2),
        encoding="utf-8",
    )

    print(f"Instrument scope: {inventory['instrument_scope']}")
    print(f"CSV files inspected: {len(inventory['local_csv_files'])}")
    for item in inventory["local_csv_files"]:
        if "inspection_error" in item:
            print(f"ERROR {item['path']}: {item['inspection_error']}")
            continue
        print(
            f"{item['path']}: {item['rows']:,} rows | "
            f"{item['first_timestamp']} -> {item['last_timestamp']} | "
            f"{item['duplicate_timestamp_count']} duplicate timestamps"
        )
    print(f"Inventory written to: {args.output}")


if __name__ == "__main__":
    main()
