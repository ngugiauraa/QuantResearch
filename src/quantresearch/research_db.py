from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb


ROOT = Path(__file__).resolve().parents[2]
PARQUET_ROOT = ROOT / "data" / "processed" / "parquet"


def _sql_string(value: str) -> str:
    """Safely escape a string literal for DuckDB SQL."""
    return "'" + value.replace("'", "''") + "'"


class GoldResearchDB:
    """Read-only research interface over the validated XAUUSD Parquet archive."""

    def __init__(
        self,
        parquet_root: Path = PARQUET_ROOT,
        read_only: bool = True,
    ):
        self.parquet_root = Path(parquet_root).resolve()

        if not self.parquet_root.exists():
            raise FileNotFoundError(
                f"Parquet dataset not found: {self.parquet_root}"
            )

        self.files = sorted(self.parquet_root.rglob("*.parquet"))

        if not self.files:
            raise FileNotFoundError("No Parquet files found.")

        self.connection = duckdb.connect(
            database=":memory:",
            read_only=False,
        )

        try:
            self.connection.execute("SET TimeZone = 'UTC'")
            self.connection.execute(
                "SET preserve_insertion_order = false"
            )

            file_literals = ", ".join(
                _sql_string(str(path).replace("\\", "/"))
                for path in self.files
            )

            self.connection.execute(
                f"""
                CREATE VIEW gold AS
                SELECT *
                FROM read_parquet(
                    [{file_literals}],
                    hive_partitioning = true,
                    union_by_name = true
                )
                """
            )

        except Exception:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def execute(
        self,
        sql: str,
        parameters: list[Any] | tuple[Any, ...] | None = None,
    ):
        if parameters is None:
            return self.connection.execute(sql)

        return self.connection.execute(sql, parameters)

    def summary(self) -> dict[str, Any]:
        row = self.execute(
            """
            SELECT
                COUNT(*) AS total_rows,
                COUNT(DISTINCT timeframe) AS timeframes,
                COUNT(DISTINCT price_side) AS price_sides,
                MIN(timestamp) AS first_timestamp,
                MAX(timestamp) AS last_timestamp
            FROM gold
            """
        ).fetchone()

        return {
            "total_rows": row[0],
            "timeframes": row[1],
            "price_sides": row[2],
            "first_timestamp": str(row[3]),
            "last_timestamp": str(row[4]),
            "parquet_files": len(self.files),
        }

    def coverage(self):
        return self.execute(
            """
            SELECT
                timeframe,
                price_side,
                COUNT(*) AS rows,
                COUNT(DISTINCT year) AS years,
                MIN(timestamp) AS first_timestamp,
                MAX(timestamp) AS last_timestamp
            FROM gold
            GROUP BY timeframe, price_side
            ORDER BY timeframe, price_side
            """
        ).fetchdf()

    def query(
        self,
        timeframe: str,
        price_side: str = "bid",
        start: str | None = None,
        end: str | None = None,
        limit: int = 100,
        columns: list[str] | None = None,
        order: str = "ASC",
    ):
        allowed_timeframes = {
            "tick", "s1", "m1", "m5", "m15",
            "m30", "h1", "h4", "d1", "mn1",
        }

        if timeframe not in allowed_timeframes:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        if price_side not in {"bid", "ask"}:
            raise ValueError("price_side must be 'bid' or 'ask'.")

        if limit < 1:
            raise ValueError("limit must be positive.")

        order = order.upper()

        if order not in {"ASC", "DESC"}:
            raise ValueError("order must be ASC or DESC.")

        available_columns = {
            "timestamp",
            "open", "high", "low", "close", "volume",
            "askPrice", "bidPrice", "askVolume", "bidVolume",
            "source_file", "source_row_number",
            "instrument", "timeframe", "price_side", "year", "month",
        }

        if columns is None:
            selected_columns = (
                [
                    "timestamp",
                    "bidPrice", "askPrice",
                    "bidVolume", "askVolume",
                    "source_file",
                ]
                if timeframe == "tick"
                else [
                    "timestamp",
                    "open", "high", "low", "close", "volume",
                    "source_file",
                ]
            )
        else:
            selected_columns = columns

        if not selected_columns:
            raise ValueError("At least one column must be selected.")

        invalid_columns = set(selected_columns) - available_columns

        if invalid_columns:
            raise ValueError(
                f"Unsupported columns: {sorted(invalid_columns)}"
            )

        column_sql = ", ".join(
            f'"{column}"' for column in selected_columns
        )

        conditions = [
            "timeframe = ?",
            "price_side = ?",
        ]

        parameters: list[Any] = [timeframe, price_side]

        if start:
            conditions.append(
                "timestamp >= CAST(? AS TIMESTAMPTZ)"
            )
            parameters.append(start)

        if end:
            conditions.append(
                "timestamp < CAST(? AS TIMESTAMPTZ)"
            )
            parameters.append(end)

        parameters.append(limit)

        sql = f"""
            SELECT {column_sql}
            FROM gold
            WHERE {" AND ".join(conditions)}
            ORDER BY timestamp {order}
            LIMIT ?
        """

        return self.execute(sql, parameters).fetchdf()

    def sql(self, statement: str):
        """Execute custom research SQL against the gold view."""
        return self.connection.execute(statement).fetchdf()