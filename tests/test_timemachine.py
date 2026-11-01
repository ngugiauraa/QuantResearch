import json
import threading
import urllib.request

import numpy as np
import pandas as pd
import pytest

from quantresearch.features.registry import build_features
from quantresearch.sim.engine import Engine, Strategy
from quantresearch.sim.reference import SmaCross
from quantresearch.timemachine import lookahead
from quantresearch.timemachine.replay import ReplayConfig, ReplaySession
from quantresearch.timemachine.viewer import serve
from quantresearch.warehouse.build import BAR_COLUMNS, aggregate
from quantresearch.warehouse.store import _finish, synthetic_bars


def data(n=6000, seed=3):
    m1 = synthetic_bars(n, seed=seed, vol=0.0007)
    m5 = _finish(aggregate(m1[BAR_COLUMNS], "m5"), "m5", None)
    return m1, m5


def session(n=3000):
    m1, m5 = data(n)
    return ReplaySession(m1, build_features(m5), SmaCross, ReplayConfig(name="t"))


@pytest.mark.parametrize("cut", ["2024-01-02 20:00", "2024-01-03 04:05", "2024-01-03 21:00", "2024-01-04 22:00", "2024-01-05 03:35"])
def test_look_ahead_integrity_passes(cut):
    m1, m5 = data()
    result = lookahead.check(m1, m5, SmaCross, cut)
    assert result.passed, result.as_dict()
    assert result.events_compared > 10 and result.feature_rows > 100
    assert result.trades_compared > 0


def test_cutoff_off_the_bar_grid_also_passes():
    m1, m5 = data()
    assert lookahead.check(m1, m5, SmaCross, "2024-01-03 11:07:30").passed


class Clairvoyant(Strategy):
    """A deliberately cheating strategy, to prove the test can catch one."""

    def __init__(self):
        self.done = False

    def on_bar(self, ctx):
        future = ctx._e.signals["close"].iloc[ctx.index + 40:]
        if not self.done and len(future) and future.iloc[0] > ctx.bar["close"]:
            ctx.buy(0.1)
            self.done = True


def test_the_integrity_test_catches_a_strategy_reading_the_future():
    m1, m5 = data()
    bars_available = m5[m5["close_ts"] <= pd.Timestamp("2024-01-02 04:00", tz="UTC")]
    assert len(bars_available) < len(m5)
    results = [lookahead.check(m1, m5, Clairvoyant, c).passed for c in
               ("2024-01-02 00:30", "2024-01-02 01:30", "2024-01-02 03:00", "2024-01-03 00:00")]
    assert not all(results)


def leaky_features(bars):
    out = build_features(bars)
    out["leak"] = out["close"].shift(-3)
    return out


def test_the_integrity_test_catches_a_leaking_feature():
    m1, m5 = data()
    result = lookahead.check(m1, m5, SmaCross, "2024-01-03 04:00", featurize=leaky_features)
    assert not result.passed
    assert any("leak" in d for d in result.feature_differences)


def test_centered_rolling_window_is_caught():
    m1, m5 = data()

    def centered(bars):
        out = build_features(bars)
        out["centered"] = out["close"].rolling(11, center=True).mean()
        return out

    assert not lookahead.check(m1, m5, SmaCross, "2024-01-03 04:00", featurize=centered).passed


def test_step_play_pause_and_speed():
    s = session()
    assert s.step(5) == 5 and s.engine.cursor == 5
    assert s.tick() == 0  # paused
    s.set_speed(10)
    s.play()
    assert s.tick() == 10 and s.engine.cursor == 15
    s.pause()
    assert s.tick() == 0
    with pytest.raises(ValueError):
        s.set_speed(0)


def test_reset_returns_to_the_start_and_is_identical():
    s = session()
    s.step(1500)
    digest = s.engine.digest()
    s.reset()
    assert s.engine.cursor == 0 and not s.engine.events
    s.step(1500)
    assert s.engine.digest() == digest


def test_seek_back_and_forth_is_consistent():
    s = session()
    s.seek("2024-01-02 20:00")
    a = s.engine.digest()
    s.seek("2024-01-02 22:00")
    s.seek("2024-01-02 20:00")
    assert s.engine.digest() == a


def test_snapshot_never_shows_an_unclosed_bar():
    s = session()
    for _ in range(40):
        s.step(7)
        snap = s.snapshot()
        now = pd.Timestamp(snap["time"])
        if snap["candles"]:
            last_open = pd.Timestamp(snap["candles"][-1]["t"], unit="s", tz="UTC")
            assert last_open + pd.Timedelta(minutes=5) <= now


def test_session_record_verifies_and_detects_tampering(tmp_path):
    s = session()
    s.step(2000)
    path = s.save(tmp_path / "r.json")
    record = json.loads(path.read_text())
    assert s.verify(record)
    record["digest"] = "0" * 64
    assert not s.verify(record)


def test_replay_matches_a_plain_backtest():
    s = session()
    s.to_end()
    m1, m5 = data(3000)
    plain = Engine(m1, build_features(m5), SmaCross()).run()
    assert plain.digest() == s.engine.digest()


def test_viewer_serves_page_and_controls_replay():
    s = session()
    server = serve(s, port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(body):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/control", data=json.dumps(body).encode(), method="POST")
        return json.loads(urllib.request.urlopen(req).read())

    try:
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
        assert "<canvas" in page and "Event log" in page
        state = post({"action": "step", "value": 400})
        assert state["cursor"] == 400 and state["candles"] and state["bid"]
        assert post({"action": "speed", "value": 50})["speed"] == 50
        assert post({"action": "play"})["playing"] is True
        assert post({"action": "pause"})["playing"] is False
        assert post({"action": "reset"})["cursor"] == 0
        with pytest.raises(urllib.error.HTTPError):
            post({"action": "buy"})
    finally:
        server.shutdown()
