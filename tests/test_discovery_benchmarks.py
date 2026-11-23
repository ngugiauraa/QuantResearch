import pandas as pd

from quantresearch.discovery import benchmarks
from quantresearch.strategies import REGISTRY


def table(names):
    idx = pd.date_range("2020-01-01", periods=5)
    return pd.DataFrame({n: 0.001 for n in names}, index=idx)


def test_a_cache_built_with_seven_engines_is_flagged_against_eight():
    older = sorted(REGISTRY)[:]
    older.remove("london_open_momentum")
    assert len(older) == 7 and len(REGISTRY) == 8
    message = benchmarks.stale_cache_warning(table(older), "runs/main/benchmarks.parquet")
    assert message and "Stale benchmark cache detected" in message
    assert "london_open_momentum" in message and "holds 7 engines but 8 are registered" in message
    assert "all 8 engines" in message and "benchmarks.parquet" in message
    # The ledger must not be named as the thing to delete.
    assert "not experiments.sqlite" in message


def test_the_expected_count_follows_the_registry_not_a_constant(monkeypatch):
    extra = REGISTRY["trend"]
    monkeypatch.setitem(REGISTRY, "ninth_engine", extra)
    message = benchmarks.stale_cache_warning(table([n for n in REGISTRY if n != "ninth_engine"]), "p")
    assert "9 are registered" in message and "ninth_engine" in message


def test_a_complete_or_missing_cache_gives_no_warning():
    assert benchmarks.stale_cache_warning(table(list(REGISTRY)), "p") is None
    assert benchmarks.stale_cache_warning(None, "p") is None
    assert benchmarks.stale_cache_warning(table(list(REGISTRY) + ["retired_engine"]), "p") is None


def test_the_warning_survives_a_round_trip_through_the_parquet_file(tmp_path):
    names = [n for n in sorted(REGISTRY) if n != "london_open_momentum"]
    path = tmp_path / "benchmarks.parquet"
    table(names).to_parquet(path)
    assert "london_open_momentum" in benchmarks.stale_cache_warning(benchmarks.load(path), path)
