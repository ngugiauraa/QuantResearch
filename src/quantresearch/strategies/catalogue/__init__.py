"""The strategy catalogue: documented, fixed-parameter gold strategies run through the same research as the 8 engines.

They are kept apart from `strategies.REGISTRY`, which drives live signals on the Overview: catalogue strategies are
research candidates only. See docs/strategy_catalogue.md for the full list with sources and exact rules.

Strategies are never removed from this catalogue, even when they fail: tests/test_catalogue.py checks every name in
docs/strategy_catalogue.json is still here, so the record of what was tried stays complete.
"""
from __future__ import annotations

from quantresearch.strategies.catalogue import batch1_trend, batch2_reversion, batch3_session, batch4_macro, batch5_smc, batch6_newdata, batch7_slow
from quantresearch.strategies.catalogue.common import CatalogueEntry

ENTRIES: list[CatalogueEntry] = [*batch1_trend.ENTRIES, *batch2_reversion.ENTRIES, *batch3_session.ENTRIES, *batch4_macro.ENTRIES, *batch5_smc.ENTRIES,
                               *batch6_newdata.ENTRIES, *batch7_slow.ENTRIES]
CATALOGUE: dict[str, CatalogueEntry] = {e.name: e for e in ENTRIES}

if len(CATALOGUE) != len(ENTRIES):
    raise RuntimeError("Duplicate catalogue strategy names")


def get(name: str) -> CatalogueEntry:
    if name not in CATALOGUE:
        raise KeyError(f"Unknown catalogue strategy {name!r}")
    return CATALOGUE[name]
