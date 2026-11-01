from __future__ import annotations

import argparse

from quantresearch.research_db import GoldResearchDB


def main():
    parser = argparse.ArgumentParser(
        description="XAUUSD Quantitative Research Query Engine"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    subparsers.add_parser("summary")
    subparsers.add_parser("coverage")

    query_parser = subparsers.add_parser("query")

    query_parser.add_argument(
        "--timeframe",
        required=True,
        choices=[
            "tick", "s1", "m1", "m5", "m15",
            "m30", "h1", "h4", "d1", "mn1",
        ],
    )

    query_parser.add_argument(
        "--side",
        choices=["bid", "ask"],
        default="bid",
    )

    query_parser.add_argument("--start")
    query_parser.add_argument("--end")
    query_parser.add_argument("--limit", type=int, default=100)
    query_parser.add_argument(
        "--order",
        choices=["ASC", "DESC"],
        default="ASC",
    )

    sql_parser = subparsers.add_parser("sql")
    sql_parser.add_argument(
        "--statement",
        required=True,
    )

    args = parser.parse_args()

    with GoldResearchDB() as db:
        if args.command == "summary":
            for key, value in db.summary().items():
                print(f"{key}: {value}")

        elif args.command == "coverage":
            print(db.coverage().to_string(index=False))

        elif args.command == "query":
            result = db.query(
                timeframe=args.timeframe,
                price_side=args.side,
                start=args.start,
                end=args.end,
                limit=args.limit,
                order=args.order,
            )

            print(result.to_string(index=False))

        elif args.command == "sql":
            print(db.sql(args.statement).to_string(index=False))


if __name__ == "__main__":
    main()