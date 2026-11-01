
"""Resumable, month-chunked XAUUSD history downloader.

Each attempt downloads into an isolated temporary directory. A completed,
validated CSV is atomically moved into the raw archive. Timed-out attempts
terminate their entire process tree to prevent orphaned Node processes.
"""

import argparse
import calendar
import csv
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time

from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "data" / "raw" / "xauusd"
LOG_PATH = ROOT / "data" / "processed" / "download_log.jsonl"

SUPPORTED_TIMEFRAMES = {
    "tick", "s1", "m1", "m5", "m15",
    "m30", "h1", "h4", "d1", "mn1",
}


def log_event(event: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    with LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event) + "\n")


def month_chunks(start: date, end: date):
    if start > end:
        raise ValueError("Start date must not be after end date.")

    current = date(start.year, start.month, 1)

    while current <= end:
        last_day = calendar.monthrange(current.year, current.month)[1]

        yield (
            max(start, current),
            min(end, date(current.year, current.month, last_day)),
        )

        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)


def terminate_process_tree(process: subprocess.Popen) -> None:
    """Terminate the downloader and all child processes."""

    if process.poll() is not None:
        return

    if os.name == "nt":
        try:
            subprocess.run(
                [
                    "taskkill",
                    "/PID", str(process.pid),
                    "/T",
                    "/F",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_download_command(
    command: list[str],
    timeout: int,
    log_file: Path,
) -> None:
    """
    Run Dukascopy without a captured stdout pipe.

    Output is written to a log file, avoiding pipe-buffer deadlocks during
    long-running downloads.
    """

    creationflags = 0
    popen_kwargs = {}

    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    with log_file.open("w", encoding="utf-8", errors="replace") as output:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=output,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
            **popen_kwargs,
        )

        try:
            return_code = process.wait(timeout=timeout)

        except subprocess.TimeoutExpired:
            terminate_process_tree(process)

            raise TimeoutError(
                f"Download exceeded the {timeout}-second timeout. "
                "The downloader process tree was terminated."
            )

    if return_code != 0:
        raise subprocess.CalledProcessError(
            return_code,
            command,
        )


def print_log_tail(log_file: Path, lines: int = 60) -> None:
    """Print the final part of a failed attempt's log."""

    if not log_file.exists():
        return

    try:
        content = log_file.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()

        if not content:
            return

        print("\n--- Downloader output (last lines) ---")

        for line in content[-lines:]:
            print(line)

        print("--- End downloader output ---\n")

    except OSError as exc:
        print(f"Could not read downloader log: {exc}")


def validate_download(
    path: Path,
    start: date,
    end: date,
) -> tuple[int, datetime, datetime]:
    """
    Validate CSV structure and timestamps before accepting a download.

    Returns:
        (row_count, first_timestamp, last_timestamp)
    """

    if not path.exists():
        raise RuntimeError("Downloader did not produce a CSV file.")

    if path.stat().st_size == 0:
        raise RuntimeError("Downloader produced an empty CSV file.")

    row_count = 0
    first_timestamp = None
    last_timestamp = None

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        if not reader.fieldnames:
            raise RuntimeError("CSV has no header.")

        if "timestamp" not in reader.fieldnames:
            raise RuntimeError(
                f"CSV is missing timestamp column. "
                f"Found: {reader.fieldnames}"
            )

        for line_number, row in enumerate(reader, start=2):
            raw_timestamp = row.get("timestamp")

            if not raw_timestamp:
                raise RuntimeError(
                    f"Missing timestamp at CSV line {line_number}."
                )

            try:
                timestamp = datetime.fromisoformat(
                    raw_timestamp.strip().replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise RuntimeError(
                    f"Invalid timestamp at CSV line {line_number}: "
                    f"{raw_timestamp}"
                ) from exc

            if not start <= timestamp.date() <= end:
                raise RuntimeError(
                    f"Timestamp outside requested date range at line "
                    f"{line_number}: {raw_timestamp}"
                )

            if first_timestamp is None:
                first_timestamp = timestamp

            last_timestamp = timestamp
            row_count += 1

    if row_count == 0:
        raise RuntimeError("CSV contains a header but no data rows.")

    return row_count, first_timestamp, last_timestamp


def download_month(
    start: date,
    end: date,
    timeframe: str,
    price: str,
    timeout: int,
    retries: int,
    overwrite: bool = False,
) -> Path:

    year_dir = RAW_ROOT / timeframe / str(start.year)
    year_dir.mkdir(parents=True, exist_ok=True)

    filename = (
        f"xauusd_{price}_{timeframe}_"
        f"{start.year}_{start.month:02d}.csv"
    )

    output = year_dir / filename

    if output.exists() and output.stat().st_size > 0 and not overwrite:
        print(f"SKIP existing: {output.relative_to(ROOT)}")
        return output

    command_template = [
        "npx", "--yes", "dukascopy-node",
        "-i", "xauusd",
        "-from", start.isoformat(),
        "-to", end.isoformat(),
        "-t", timeframe,
        "-p", price,
        "-v",
        "-vu", "units",
        "-f", "csv",
        "-df", "iso",
        "-bs", "3",
        "-bp", "2000",
    ]

    for attempt in range(1, retries + 1):
        print(
            f"\n[{start:%Y-%m}] {timeframe} {price}: "
            f"attempt {attempt}/{retries}"
        )

        # Every attempt gets its own isolated output directory.
        with tempfile.TemporaryDirectory(
            prefix=f"dukascopy_{timeframe}_{start:%Y%m}_",
            dir=year_dir,
        ) as temp_directory:

            temp_dir = Path(temp_directory)
            temp_log = temp_dir / "download.log"

            command = [
                *command_template,
                "-dir", str(temp_dir),
                "-fn", filename[:-4],
            ]

            try:
                run_download_command(
                    command=command,
                    timeout=timeout,
                    log_file=temp_log,
                )

                candidates = [
                    temp_dir / filename,
                    temp_dir / f"{filename}.csv",
                    temp_dir / f"{filename[:-4]}.csv",
                ]

                actual = next(
                    (
                        candidate
                        for candidate in candidates
                        if candidate.exists()
                        and candidate.stat().st_size > 0
                    ),
                    None,
                )

                if actual is None:
                    raise RuntimeError(
                        "CLI finished but the expected CSV was not found."
                    )

                rows, first_ts, last_ts = validate_download(
                    actual,
                    start,
                    end,
                )

                # Move only a validated file into the permanent archive.
                # os.replace safely replaces an existing destination.
                os.replace(actual, output)

                log_event({
                    "status": "success",
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "timeframe": timeframe,
                    "price": price,
                    "path": output.relative_to(ROOT).as_posix(),
                    "bytes": output.stat().st_size,
                    "rows": rows,
                    "first_timestamp": first_ts.isoformat(),
                    "last_timestamp": last_ts.isoformat(),
                    "attempt": attempt,
                })

                print(f"SAVED: {output.relative_to(ROOT)}")
                print(f"Rows: {rows:,}")
                print(f"First: {first_ts}")
                print(f"Last:  {last_ts}")

                return output

            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
                TimeoutError,
                FileNotFoundError,
                RuntimeError,
                OSError,
            ) as exc:

                print_log_tail(temp_log)

                log_event({
                    "status": "retry_or_failure",
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "timeframe": timeframe,
                    "price": price,
                    "attempt": attempt,
                    "error": str(exc),
                })

                print(f"FAILED attempt {attempt}: {exc}")

        if attempt < retries:
            delay = min(120, 10 * (2 ** (attempt - 1)))
            print(f"Retrying in {delay} seconds...")
            time.sleep(delay)

    raise RuntimeError(
        f"All {retries} attempts failed for "
        f"{start} to {end} ({timeframe}, {price})."
    )


def main():
    parser = argparse.ArgumentParser(
        description="Resumable, month-chunked XAUUSD history downloader."
    )

    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)

    parser.add_argument(
        "--timeframe",
        default="m1",
        choices=sorted(SUPPORTED_TIMEFRAMES),
    )

    parser.add_argument(
        "--price",
        default="bid",
        choices=["bid", "ask"],
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Timeout per monthly attempt in seconds. Default: 1800.",
    )

    parser.add_argument(
        "--retries",
        type=int,
        default=4,
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Redownload and replace existing monthly files.",
    )

    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    if args.timeout < 1 or args.retries < 1:
        parser.error("--timeout and --retries must be positive.")

    chunks = list(month_chunks(start, end))
    failures = []

    print("Instrument: XAUUSD")
    print(f"Timeframe: {args.timeframe} | Price: {args.price}")
    print(f"Date range: {start} -> {end}")
    print(f"Chunks: {len(chunks)}")
    print(f"Timeout per attempt: {args.timeout}s")
    print(f"Retries per chunk: {args.retries}")
    print(f"Overwrite existing files: {args.overwrite}")

    for index, (chunk_start, chunk_end) in enumerate(chunks, 1):
        print(f"\n===== Chunk {index}/{len(chunks)} =====")

        try:
            download_month(
                start=chunk_start,
                end=chunk_end,
                timeframe=args.timeframe,
                price=args.price,
                timeout=args.timeout,
                retries=args.retries,
                overwrite=args.overwrite,
            )

        except RuntimeError as exc:
            failures.append({
                "start": chunk_start.isoformat(),
                "end": chunk_end.isoformat(),
                "error": str(exc),
            })

            print(f"CHUNK FAILED: {exc}")

    if failures:
        failure_path = (
            ROOT / "data" / "processed" / "download_failures.json"
        )

        failure_path.parent.mkdir(parents=True, exist_ok=True)

        failure_path.write_text(
            json.dumps(failures, indent=2),
            encoding="utf-8",
        )

        raise SystemExit(
            f"{len(failures)} chunk(s) failed. "
            f"Failures saved to {failure_path}. "
            "Rerun the same command to skip successful files and retry gaps."
        )

    print("\nAll requested chunks completed.")


if __name__ == "__main__":
    main()
