"""Create a provenance manifest for CSV files in data/raw and data/processed."""

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIRS = [ROOT / "data" / "raw", ROOT / "data" / "processed"]
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "data_manifest.json"

TIMESTAMP_NAMES = {
    "timestamp", "time", "datetime", "date", "gmt time", "timestamp_utc"
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def find_timestamp_column(headers: list[str]) -> str | None:
    for header in headers:
        if header.strip().lower() in TIMESTAMP_NAMES:
            return header
    return None


def inspect_csv(path: Path) -> dict:
    """Inspect a CSV without modifying it."""
    row_count = 0
    first_timestamp = None
    last_timestamp = None
    timestamp_column = None

    with path.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        headers = reader.fieldnames or []
        timestamp_column = find_timestamp_column(headers)

        for row in reader:
            row_count += 1
            if timestamp_column:
                value = (row.get(timestamp_column) or "").strip()
                if value:
                    if first_timestamp is None:
                        first_timestamp = value
                    last_timestamp = value

    try:
        relative_path = path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        relative_path = str(path.resolve())

    return {
        "path": relative_path,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "rows": row_count,
        "timestamp_column": timestamp_column,
        "first_timestamp_in_file": first_timestamp,
        "last_timestamp_in_file": last_timestamp,
    }


def build_manifest() -> dict:
    files = []

    for directory in DATA_DIRS:
        if not directory.exists():
            continue

        for path in sorted(directory.rglob("*.csv")):
            files.append(inspect_csv(path))

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "project": "QuantResearch",
        "instrument_scope": ["XAUUSD"],
        "file_count": len(files),
        "files": files,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Path for the JSON manifest.",
    )
    args = parser.parse_args()

    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output

    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest()
    output.write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    print(f"Files catalogued: {manifest['file_count']}")
    print(f"Manifest written: {output}")

    for item in manifest["files"]:
        print(
            f"{item['rows']:>8,} rows | "
            f"{item['sha256'][:12]} | {item['path']}"
        )


if __name__ == "__main__":
    main()