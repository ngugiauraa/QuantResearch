"""Discovery settings and the time structure that keeps validation honest.

Time is cut into three kinds of period:

  research data   2003-05-05 up to HOLDOUT_START. Everything discovery may ever read.
  holdout         HOLDOUT_START onward. Already examined during earlier development and defect fixes, so it is
                  CONTAMINATED and is not used for selection. The discovery feature store is physically built without
                  it. It can be opened once per frozen, already-qualified candidate, for a confirmatory note only.
  forward         bars that arrive after a candidate is registered for paper trading. The only truly unseen data.

Inside the research data, candidates are produced at a series of vintage cutoffs. A vintage's search and all of its
parameter selection see only [cutoff - discovery_years, cutoff). Its chosen candidates are then frozen and evaluated
only on [cutoff, HOLDOUT_START). Search allocation is rewarded by training-window results only, so out-of-sample
results never shape a candidate's construction.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from quantresearch.core.config import Paths, paths

HOLDOUT_START = pd.Timestamp("2024-10-30", tz="UTC")
RESEARCH_START = pd.Timestamp("2003-05-05", tz="UTC")
HOLDOUT_NOTE = ("The 2024-10-30 to 2026-10-30 holdout was run several times during earlier development and defect "
                "correction. It is contaminated: discovery never reads it, and any single confirmatory look at a frozen, "
                "already-qualified candidate is labelled as such and cannot upgrade it.")

TIMEFRAMES = ("m15", "h1", "h4", "d1ny")
VINTAGES = ("2009-01-01", "2011-01-01", "2013-01-01", "2015-01-01", "2017-01-01", "2019-01-01", "2021-01-01", "2023-01-01")


@dataclass(frozen=True)
class Window:
    start: pd.Timestamp
    end: pd.Timestamp

    def as_dict(self) -> dict:
        return {"start": str(self.start.date()), "end": str(self.end.date())}


@dataclass(frozen=True)
class Vintage:
    """The time structure for one cutoff."""
    cutoff: pd.Timestamp
    discovery: Window          # all search and selection
    inner_train: Window        # fitness during search
    inner_validation: Window   # selection inside training, before freezing
    oos: Window                # frozen-candidate evaluation; never seen by the search

    @property
    def key(self) -> str:
        return str(self.cutoff.date())

    def as_dict(self) -> dict:
        return {"cutoff": self.key, "discovery": self.discovery.as_dict(), "inner_train": self.inner_train.as_dict(),
                "inner_validation": self.inner_validation.as_dict(), "oos": self.oos.as_dict()}


def make_vintage(cutoff: str | pd.Timestamp, discovery_years: int = 6, validation_years: int = 2,
                 holdout_start: pd.Timestamp = HOLDOUT_START) -> Vintage:
    c = pd.Timestamp(cutoff)
    c = c.tz_localize("UTC") if c.tzinfo is None else c
    start = max(RESEARCH_START, c - pd.DateOffset(years=discovery_years))
    split = c - pd.DateOffset(years=validation_years)
    if not start < split < c < holdout_start:
        raise ValueError(f"Vintage {c} does not fit inside the research period")
    return Vintage(c, Window(start, c), Window(start, split), Window(split, c), Window(c, holdout_start))


@dataclass
class DiscoveryConfig:
    run_id: str = "main"
    seed: int = 20261102
    max_hours: float = 48.0
    max_experiments: int = 50_000_000
    max_rounds: int = 1_000_000
    workers: int | None = None              # None: chosen from cores and free memory
    min_free_memory_gb: float = 0.9         # pause scheduling below this
    critical_free_memory_gb: float = 0.45   # shed a worker below this
    worker_task_limit: int = 12             # recycle the worker pool every this many rounds, to cap memory growth
    timeframes: tuple = TIMEFRAMES
    vintages: tuple = VINTAGES
    discovery_years: int = 6
    validation_years: int = 2
    null_share: float = 0.15                # compute spent on sign-randomised null markets
    tasks_per_round: int | None = None
    selected_per_task: int = 3              # candidates frozen per search task, the cap on out-of-sample looks
    gp_population: int = 48
    gp_generations: int = 10
    max_complexity: int = 24
    futility_min_hours: float = 12.0
    futility_window_rounds: int = 40
    exhaustion_novelty: float = 0.02
    risk_pct: float = 0.5
    benchmarks: bool = True                 # correlate candidates with the existing engines
    store_dir: str | None = None            # feature store override, used by tests
    holdout_start: str = str(HOLDOUT_START.date())

    def fingerprint(self) -> str:
        data = {k: v for k, v in asdict(self).items() if k not in ("workers", "max_hours", "max_experiments", "max_rounds",
                                                                    "min_free_memory_gb", "critical_free_memory_gb")}
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]

    def vintage_objects(self) -> list[Vintage]:
        hs = pd.Timestamp(self.holdout_start, tz="UTC")
        return [make_vintage(v, self.discovery_years, self.validation_years, hs) for v in self.vintages]


def discovery_root(p: Paths | None = None) -> Path:
    p = p or paths()
    return p.research / "discovery"


def run_dir(run_id: str, p: Paths | None = None):
    return discovery_root(p) / "runs" / run_id


def store_dir(p: Paths | None = None, dataset: str = "real"):
    return discovery_root(p) / "store" / dataset

