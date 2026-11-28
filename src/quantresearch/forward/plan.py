"""Forward test 001 as pre-registered in docs/forward_test_001.md. Every number here is copied from that file.

Nothing in this module may change after the start: the runner checks the strategies' parameters against LOCKED
and the catalogue's own parameter lock, and refuses to run if they differ.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from quantresearch.core.config import Paths
from quantresearch.risk.engine import RiskLimits

TEST_ID = "forward_test_001"
REPO = Path(__file__).resolve().parents[3]
DOC = REPO / "docs" / "forward_test_001.md"

# Table order is the priority order when signals arrive at the same bar close.
STRATEGIES: tuple[str, ...] = (
    "cat_real_yield_trend_d1", "cat_macd_zero_h4", "cat_chandelier_h4", "cat_bollinger_breakout_h4",
    "cat_ichimoku_h4", "cat_ema_cross_h1", "cat_calm_breakout_h1",
)
LOCKED: dict[str, dict] = {
    "cat_real_yield_trend_d1": {"ry_sma": 120, "stop_atr": 3.0},
    "cat_macd_zero_h4": {"fast": 12, "slow": 26, "signal": 9, "stop_atr": 2.5},
    "cat_chandelier_h4": {"entry_n": 20, "trail_n": 22, "trail_atr": 3.0, "stop_atr": 3.0},
    "cat_bollinger_breakout_h4": {"stop_atr": 2.0},
    "cat_ichimoku_h4": {"stop_atr": 2.5},
    "cat_ema_cross_h1": {"kind": "ema", "fast": 20, "slow": 50, "stop_atr": 3.0},
    "cat_calm_breakout_h1": {"lookback": 500, "calm": 0.2, "n": 24, "stop_atr": 2.0, "hold": 24},
}

RISK_PCT = 0.25
DIRECTION_CAP_PCT = 1.0
LATE_FRACTION = 0.10
MIN_START_EQUITY = 120_000.0
MAGIC = 880101
COMMENT_PREFIX = "fwd1:"
HISTORY_YEARS = 3  # Indicator history loaded before the start; fixed at the start so live and replay agree.

EVAL_MONTHS, MAX_MONTHS, MIN_TRADES = 6, 12, 30
MAX_DRAWDOWN_PCT = 10.0
MAX_MEAN_SLIPPAGE = 0.20
MAX_MEAN_SPREAD = 1.35

BAR_LENGTH_MIN = {"h1": 60, "h4": 240, "d1": 1440}


def limits() -> RiskLimits:
    """The test's own risk limits. The dashboard's limits file is never read or written."""
    return RiskLimits(risk_per_trade_pct=RISK_PCT, max_daily_loss_pct=2.0, max_drawdown_pct=10.0, max_total_lots=5.0,
                      max_order_lots=2.0, max_concurrent_positions=7, max_trades_per_day=20, min_reward_risk=1.0,
                      require_stop=True, max_stop_distance_pct=10.0, max_spread_usd=1.5, max_slippage_usd=1.0,
                      max_atr_pct=3.0, news_block_before_min=15, news_block_after_min=15)


def comment(strategy: str) -> str:
    return (COMMENT_PREFIX + strategy.removeprefix("cat_"))[:31]


class PlanViolation(RuntimeError):
    """The code or the pre-registration no longer matches what was registered."""


def check_locked(p: Paths) -> None:
    """Every strategy's parameters must equal the registered table and the catalogue's first-run lock."""
    from quantresearch.research.catalogue import ParamsChanged, lock_params
    from quantresearch.strategies.catalogue import CATALOGUE

    for name in STRATEGIES:
        current = CATALOGUE[name].defn.defaults
        if current != LOCKED[name]:
            raise PlanViolation(f"{name} parameters {current} differ from the registered {LOCKED[name]}")
        try:
            lock_params(name, current, p)
        except ParamsChanged as exc:
            raise PlanViolation(str(exc)) from exc


def doc_commit() -> str:
    """The commit that last changed the pre-registration. Raises if the file is untracked or has local edits."""
    rel = DOC.relative_to(REPO).as_posix()
    def git(*args):
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=False)
    if git("ls-files", "--error-unmatch", rel).returncode != 0:
        raise PlanViolation(f"{rel} is not committed")
    if git("status", "--porcelain", "--", rel).stdout.strip():
        raise PlanViolation(f"{rel} has uncommitted changes")
    out = git("log", "-1", "--format=%H", "--", rel).stdout.strip()
    if not out:
        raise PlanViolation(f"{rel} has no commit")
    return out
