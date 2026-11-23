import os
import subprocess
import sys
from pathlib import Path

import pytest

from quantresearch.core.config import Paths
from quantresearch.warehouse import store
from quantresearch.warehouse.store import WarehouseNotBuilt, friendly_warehouse_errors, load_bars, synthetic_bars

ROOT = Path(__file__).resolve().parents[1]


def test_an_empty_warehouse_raises_an_error_that_says_how_to_build_it(tmp_path):
    with pytest.raises(WarehouseNotBuilt) as caught:
        load_bars("h1", p=Paths(tmp_path))
    text = str(caught.value)
    for command in ("fetch_months m1", "warehouse.build --start 2003-05", "warehouse.audit", "external.store"):
        assert command in text, command
    assert "resumable" in text and "not needed for bars" in text
    assert isinstance(caught.value, FileNotFoundError)  # Existing `except FileNotFoundError` handlers still catch it.


def test_when_m1_exists_only_the_build_step_is_suggested(tmp_path):
    p = Paths(tmp_path)
    from quantresearch.warehouse import build
    m1 = synthetic_bars(500, "m1", start="2024-01-02")[build.BAR_COLUMNS]
    build._write_tf(m1, "m1", 2024, 1, p)
    with pytest.raises(WarehouseNotBuilt) as caught:
        load_bars("h1", p=p)
    text = str(caught.value)
    assert "warehouse.build --start 2003-05" in text and "fetch_months m1" not in text
    assert len(load_bars("m1", p=p)) == 500  # What exists still loads.


def test_the_cli_wrapper_prints_the_fix_and_exits_1_without_a_traceback(tmp_path, capsys):
    @friendly_warehouse_errors
    def main():
        load_bars("h1", p=Paths(tmp_path))

    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 1
    err = capsys.readouterr().err
    assert "fetch_months m1" in err and "Traceback" not in err


def test_the_wrapper_does_not_hide_other_errors_or_change_results():
    @friendly_warehouse_errors
    def other():
        raise FileNotFoundError("some other file")

    @friendly_warehouse_errors
    def fine(x):
        return x + 1

    with pytest.raises(FileNotFoundError, match="some other file"):
        other()
    assert fine(1) == 2


@pytest.mark.parametrize("args", [
    ["quantresearch.research.experiment"],
    ["quantresearch.ml.train_cli", "--tf", "h1", "--end", "2024-10-30"],
    ["quantresearch.timemachine.lookahead", "--tf", "m15", "--start", "2024-01-01", "--end", "2024-07-01", "2024-03-29 00:00"],
    ["quantresearch.timemachine.viewer", "--tf", "m15", "--start", "2024-03-01", "--end", "2024-05-01"],
])
def test_each_entry_point_fails_cleanly_with_no_data(tmp_path, args):
    env = {**os.environ, "QR_DATA_ROOT": str(tmp_path), "PYTHONPATH": str(ROOT / "src")}
    done = subprocess.run([sys.executable, "-m", *args], env=env, capture_output=True, text=True, timeout=300)
    assert done.returncode == 1, done.stderr[-800:]
    assert "fetch_months m1" in done.stderr and "Traceback" not in done.stderr
