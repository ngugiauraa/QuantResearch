"""Registry of data sources for the XAUUSD research platform."""

from copy import deepcopy


DATA_SOURCES = {
    "dukascopy": {
        "category": "market_data",
        "status": "partially_acquired",
        "instruments": ["XAUUSD"],
        "fields": ["timestamp", "open", "high", "low", "close", "volume"],
        "resolution": ["1m"],
        "notes": (
            "Downloaded feed is bid-side data. Volume semantics must be "
            "verified with the provider; do not assume global traded volume."
        ),
    },
    "federal_reserve": {
        "category": "macroeconomic",
        "status": "not_acquired",
        "fields": ["policy_rate", "statements", "meeting_dates"],
        "notes": "Official Federal Reserve publications and policy data.",
    },
    "fred": {
        "category": "macroeconomic",
        "status": "not_acquired",
        "fields": ["treasury_yields", "real_yields", "dollar_related_series"],
        "notes": (
            "Check series definitions, update frequency, and release/vintage "
            "availability before using observations in historical research."
        ),
    },
    "bls": {
        "category": "macroeconomic",
        "status": "not_acquired",
        "fields": ["CPI", "employment", "unemployment"],
        "notes": "Economic observations and publication dates.",
    },
    "bea": {
        "category": "macroeconomic",
        "status": "not_acquired",
        "fields": ["PCE", "GDP", "income"],
        "notes": "Economic observations and publication dates.",
    },
    "news_events": {
        "category": "news",
        "status": "not_acquired",
        "fields": ["published_at", "source", "headline", "event_type"],
        "notes": "Store publication timestamps separately from event timestamps.",
    },
}


def get_sources(category: str | None = None) -> dict:
    """Return a copy of all sources, optionally filtered by category."""
    sources = deepcopy(DATA_SOURCES)

    if category is None:
        return sources

    return {
        name: details
        for name, details in sources.items()
        if details["category"] == category
    }


def print_sources() -> None:
    """Print a compact source inventory."""
    for name, details in DATA_SOURCES.items():
        print(
            f"{name:18} | {details['category']:14} | "
            f"{details['status']}"
        )


if __name__ == "__main__":
    print_sources()