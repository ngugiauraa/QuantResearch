"""Download long-run annual and monthly historical gold price datasets."""

import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "data" / "raw" / "gold_historical"

DATASETS = {
    "annual": "https://datahub.io/core/gold-prices/r/annual.csv",
    "monthly": "https://datahub.io/core/gold-prices/r/monthly.csv",
}


def download(name: str, url: str) -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / f"gold_price_{name}.csv"
    temporary = output.with_suffix(".csv.part")

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "QuantResearch historical-data project"},
    )

    digest = hashlib.sha256()
    total_bytes = 0

    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            with temporary.open("wb") as file:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    file.write(chunk)
                    digest.update(chunk)
                    total_bytes += len(chunk)

        if total_bytes == 0:
            raise RuntimeError(f"Downloaded an empty file for {name}.")

        temporary.replace(output)

    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise

    result = {
        "dataset": name,
        "source": url,
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "path": output.relative_to(ROOT).as_posix(),
        "bytes": total_bytes,
        "sha256": digest.hexdigest(),
        "interpretation": (
            "Annual average gold prices."
            if name == "annual"
            else (
                "Monthly historical gold prices. The source notes that "
                "1833-1959 monthly entries repeat annual averages, "
                "rather than representing true monthly observations."
            )
        ),
    }

    print(f"Downloaded {name}: {total_bytes:,} bytes")
    return result


def main():
    results = []

    for name, url in DATASETS.items():
        try:
            results.append(download(name, url))
        except Exception as exc:
            print(f"FAILED {name}: {exc}")

    manifest = OUTPUT_DIR / "source_manifest.json"
    manifest.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Source manifest: {manifest}")

    if len(results) != len(DATASETS):
        raise SystemExit("One or more historical gold datasets failed.")


if __name__ == "__main__":
    main()