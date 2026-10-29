
"""Synthetic OHLCV market-data generation and validation.

Synthetic prices are for software testing only, not trading signals.
"""

import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIELDS = [
    "timestamp", "symbol", "timeframe",
    "open", "high", "low", "close", "volume",
]

INSTRUMENTS = {
    "XAUUSD": {"start": 4000.0, "step": 2.5},
    "US100": {"start": 25000.0, "step": 12.0},
}


def validate_candle(candle: dict) -> None:
    """Raise ValueError when a candle violates the data contract."""
    required = set(FIELDS)
    missing = required - candle.keys()
    if missing:
        raise ValueError(f"Missing fields: {sorted(missing)}")

    if candle["symbol"] not in INSTRUMENTS:
        raise ValueError(f"Unsupported symbol: {candle['symbol']}")

    if candle["timeframe"] != "1m":
        raise ValueError("Only 1m candles are supported currently")

    try:
        timestamp = datetime.fromisoformat(
            candle["timestamp"].replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid timestamp") from exc

    if timestamp.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")

    try:
        open_, high, low, close, volume = (
            float(candle[key])
            for key in ("open", "high", "low", "close", "volume")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Prices and volume must be numeric") from exc

    if not all(map(
        lambda value: value == value
        and abs(value) != float("inf"),
        (open_, high, low, close, volume),
    )):
        raise ValueError("Prices and volume must be finite")

    if low <= 0 or volume < 0:
        raise ValueError("Prices must be positive and volume non-negative")

    if high < max(open_, close, low):
        raise ValueError("High is inconsistent with OHLC values")

    if low > min(open_, close, high):
        raise ValueError("Low is inconsistent with OHLC values")


def generate_candles(
    symbol: str,
    count: int = 500,
    start_time: datetime | None = None,
    seed: int = 42,
) -> list[dict]:
    """Generate reproducible, simulated one-minute candles."""
    if symbol not in INSTRUMENTS:
        raise ValueError(f"Unsupported symbol: {symbol}")
    if count < 1:
        raise ValueError("count must be at least 1")

    start_time = start_time or datetime(
        2026, 1, 5, 8, 0, tzinfo=timezone.utc
    )
    if start_time.tzinfo is None:
        raise ValueError("start_time must be timezone-aware")

    rng = random.Random(seed)
    instrument = INSTRUMENTS[symbol]
    previous_close = instrument["start"]
    candles = []

    for index in range(count):
        open_ = previous_close
        close = max(
            0.01,
            open_ + rng.gauss(0, instrument["step"]),
        )
        high = max(open_, close) + rng.random() * instrument["step"]
        low = max(
            0.01,
            min(open_, close) - rng.random() * instrument["step"],
        )
        volume = round(rng.uniform(1, 100), 2)

        candle = {
            "timestamp": (
                start_time + timedelta(minutes=index)
            ).isoformat().replace("+00:00", "Z"),
            "symbol": symbol,
            "timeframe": "1m",
            "open": round(open_, 5),
            "high": round(high, 5),
            "low": round(low, 5),
            "close": round(close, 5),
            "volume": volume,
        }

        validate_candle(candle)
        candles.append(candle)
        previous_close = close

    return candles


def save_candles(candles: list[dict], output_path: str | Path) -> Path:
    """Validate and write candles to a CSV file."""
    if not candles:
        raise ValueError("Cannot save an empty candle collection")

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    for candle in candles:
        validate_candle(candle)

    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(candles)

    return path
