"""Registry of data sources for the XAUUSD research platform."""

from copy import deepcopy


DATA_SOURCES = {
    "dukascopy": {
        "category": "market_data",
        "status": "acquired",
        "instruments": ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "dollar index", "T-bond"],
        "fields": ["timestamp", "bid and ask open/high/low/close", "volume"],
        "resolution": ["tick", "1m", "1h"],
        "notes": (
            "M1 bid and ask, and ticks with both sides. Volume is provider "
            "activity, not global traded volume. See the README for coverage."
        ),
    },
    "federal_reserve": {
        "category": "macroeconomic",
        "status": "meeting_dates_only",
        "fields": ["meeting_dates"],
        "notes": "Official Federal Reserve publications and policy data.",
    },
    "fred": {
        "category": "macroeconomic",
        "status": "latest_values_only_no_vintages",
        "fields": ["treasury_yields", "real_yields", "dollar_related_series"],
        "notes": (
            "Check series definitions, update frequency, and release/vintage "
            "availability before using observations in historical research."
        ),
    },
    "bls": {
        "category": "macroeconomic",
        "status": "schedule_refused_automated_access_values_via_fred",
        "fields": ["CPI", "employment", "unemployment"],
        "notes": "Economic observations and publication dates.",
    },
    "bea": {
        "category": "macroeconomic",
        "status": "values_via_fred",
        "fields": ["PCE", "GDP", "income"],
        "notes": "Economic observations and publication dates.",
    },
    "news_events": {
        "category": "news",
        "status": "optional_recent_headlines_gdelt",
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